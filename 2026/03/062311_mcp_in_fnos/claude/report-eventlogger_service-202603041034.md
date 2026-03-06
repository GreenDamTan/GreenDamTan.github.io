# trimrpc 框架逆向分析报告

**分析日期**: 2026-03-04
**二进制文件**: `eventlogger_service`
**共享库**: `libtrimrpc.so.0.1.3`
**来源**: fnOS 1.1.15
**架构**: x86_64 (ELF)

---

## 1. 概述

`trimrpc` 是 fnOS 自研的 RPC（远程过程调用）框架，以 `libtrimrpc.so.0.1.3` 共享库形式提供。它与 `framework` 库配合使用，为 fnOS 各服务之间提供基于 UUID 和服务名的通信机制。

在 `eventlogger_service` 中，trimrpc 作为 **客户端** 使用，用于：
1. 代理转发 alert 请求到 `com.trim.resmon` 服务
2. 发送通知到 `com.trim.main` 服务

服务端（接收入站 RPC 请求）的能力由 `framework::Application` 提供。

---

## 2. trimrpc 核心 API

### 2.1 导出函数清单

trimrpc 通过 PLT/GOT 引入以下 6 个函数：

| PLT 地址 | GOT 地址 | 函数签名 | 说明 |
|---|---|---|---|
| `0x38080` | `0x10E828` | `RpcClient::RpcClient(const std::string& uuid, const std::string& service_name)` | 构造 RPC 客户端 |
| `0x38160` | `0x10E898` | `RpcClient::ApplyPermission(const std::string& target_service) → int` | 申请访问目标服务的权限 |
| `0x375d0` | `0x10E2D0` | `RpcClient::Call(const Request& request) → int` | 发送 RPC 请求 |
| `0x37810` | `0x10E3F0` | `RpcClient::response() → PPJson accessible object` | 获取 RPC 响应数据 |
| `0x38010` | `0x10E7F0` | `Request::Request(const std::string& target, const std::string& method, const std::string& extra)` | 构造 RPC 请求 |
| `0x37660` | `0x10E318` | `Request::params() → PPJson::MutValue*` | 获取请求参数的可变 JSON 对象 |

### 2.2 RpcClient 类

#### 构造函数

```c
// 签名推断
trimrpc::RpcClient::RpcClient(
    const std::string& uuid,          // 服务 UUID，如 "EECC6649-92CB-4246-A76A-F14358F13C07"
    const std::string& service_name   // 调用方服务名，如 "com.trim.eventlogger"
);
```

- 分配 `0x88` (136) 字节的 RpcClient 对象
- 内部创建 `0xD0` (208) 字节的连接对象（含 3 个 std::string 和 std::condition_variable）
- UUID 用于 RPC 服务识别和权限校验
- service_name 是调用方自身的服务标识

#### ApplyPermission

```c
// 返回 0 表示成功，非 0 表示失败
int trimrpc::RpcClient::ApplyPermission(
    const std::string& target_service  // 目标服务名，如 "com.trim.resmon"
);
```

- 在 Call() 之前必须先调用，获取访问目标服务的权限
- 权限是基于服务名的，不是基于方法名
- 可以对同一个 RpcClient 多次申请不同服务的权限

#### Call

```c
// 返回 0 表示成功，非 0 表示失败（错误码）
int trimrpc::RpcClient::Call(
    const trimrpc::Request& request
);
```

- 同步调用，阻塞直到响应返回
- 返回值用于错误判断，0 = 成功
- 失败时可通过 `errno` 获取系统错误码

#### response

```c
// 返回可通过 PPJson 接口访问的响应数据
auto trimrpc::RpcClient::response();
```

- 必须在 `Call()` 返回 0 后调用
- 返回值是带虚表的多态对象，可通过 PPJson 虚函数接口读取数据

### 2.3 Request 类

#### 构造函数

```c
trimrpc::Request::Request(
    const std::string& target_service,  // 目标服务名，如 "com.trim.resmon"
    const std::string& method,          // 方法名，如 "sendNotify"
    const std::string& extra            // 附加参数，通常为空字符串 ""
);
```

- 内部创建带虚表的多态对象（通过 `vtable + 8` 调用虚析构函数）
- Request 本身是一个 8 字节的指针包装器
- 创建后通过 `params()` 设置调用参数

#### params

```c
// 返回 PPJson::MutValue* 可变 JSON 对象
PPJson::MutValue* trimrpc::Request::params();
```

- 返回的对象通过 PPJson 虚函数接口操作
- 使用 `operator[](key)` 获取/创建子字段
- 使用 `PPJson::MutValue::operator=(value)` 设置值
- 支持嵌套访问，如 `params()["data"]["eventId"] = value`

---

## 3. RpcClient 内存布局

### 3.1 RpcClient 对象（0x88 = 136 字节）

通过 `unique_ptr<RpcClient>::~unique_ptr` (0xd7950) 逆向推断：

```
┌──────────────────────────────────────────────┐
│ RpcClient (0x88 = 136 bytes)                 │
├──────────────────────────────────────────────┤
│ offset  0: void* internal_conn  (8 bytes)    │ → 指向 0xD0 字节的内部对象
│ offset  8: std::string str1     (32 bytes)   │ SSO buffer at offset 24
│ offset 40: std::string str2     (32 bytes)   │ SSO buffer at offset 56
│ offset 72: [8 bytes padding/field]           │
│ offset 80: std::string str3     (32 bytes)   │ SSO buffer at offset 96
│ offset 112: [8 bytes padding/field]          │
│ offset 120: shared_ptr ctrl_block (8 bytes)  │
│ offset 128: [8 bytes]                        │
└──────────────────────────────────────────────┘
```

### 3.2 内部连接对象（0xD0 = 208 字节）

```
┌──────────────────────────────────────────────┐
│ Internal Connection Object (0xD0 = 208 bytes)│
├──────────────────────────────────────────────┤
│ offset   0: std::string str1    (32 bytes)   │ SSO buffer at offset 16
│ offset  32: std::string str2    (32 bytes)   │ SSO buffer at offset 48
│ offset  64: [8 bytes padding]                │
│ offset  72: std::string str3    (32 bytes)   │ SSO buffer at offset 88
│ offset 104: [56 bytes other fields]          │
│ offset 160: std::condition_variable (48 bytes)│ 用于同步等待 RPC 响应
└──────────────────────────────────────────────┘
```

**反汇编证据** (unique_ptr destructor at 0xd7950):

```asm
; 释放 RpcClient 3 个 std::string
d79a5  mov     rdi, [rbx+50h]    ; v2[10] = str3._M_data (offset 80)
d79b0  lea     rax, [rbx+60h]    ; v2+12  = str3 SSO buffer (offset 96)
d79bf  mov     rdi, [rbx+28h]    ; v2[5]  = str2._M_data (offset 40)
d79ca  lea     rax, [rbx+38h]    ; v2+7   = str2 SSO buffer (offset 56)
d79d9  mov     rdi, [rbx+8]      ; v2[1]  = str1._M_data (offset 8)
d79e4  lea     rax, [rbx+18h]    ; v2+3   = str1 SSO buffer (offset 24)

; 释放内部连接对象的 3 个 std::string
d7a07  mov     rdi, [rbp+48h]    ; internal.str3._M_data (offset 72)
d7a12  lea     rax, [rbp+58h]    ; internal.str3 SSO (offset 88)
d7a21  mov     rdi, [rbp+20h]    ; internal.str2._M_data (offset 32)
d7a2c  lea     rax, [rbp+30h]    ; internal.str2 SSO (offset 48)
d7a46  cmp     [rbp+0], rbp+10h  ; internal.str1._M_data (offset 0) vs SSO (offset 16)

; 析构顺序
d7a02  call    condition_variable::~condition_variable  ; offset 160
d7a5d  call    operator delete(internal, 0xD0)          ; 释放内部对象
d7a70  call    operator delete(client, 0x88)            ; 释放 RpcClient
```

### 3.3 Request 对象

Request 是一个轻量级指针包装器：

```
┌──────────────────────────────────┐
│ Request (8 bytes on stack)       │
├──────────────────────────────────┤
│ offset 0: void* internal (8 bytes)│ → 带虚表的多态内部对象
└──────────────────────────────────┘

Internal object vtable:
  +0: typeinfo pointer
  +8: virtual destructor
  ...
```

**反汇编证据** (AlertMgr::proxy cleanup at 0x49d6b):

```asm
; Request 析构 - 通过虚函数表调用虚析构
49d6b  jz      short loc_49D73
49d6d  mov     rax, [rdi]        ; load vtable pointer
49d70  call    qword ptr [rax+8] ; call virtual destructor
```

---

## 4. framework 层的 RPC 支持

### 4.1 framework::Application（服务端）

framework 提供服务端 RPC 能力，通过以下 API：

| 函数 | PLT 地址 | 说明 |
|---|---|---|
| `Application(name, arg2, arg3)` | `0x37980` | 构造应用，name 如 "eventlogger" |
| `RegisterRpcService(uuid, name)` | `0x37af0` | 注册 RPC 服务端点 |
| `RegisterHandler(action, callback)` | `0x37a30` | 注册 API Handler |
| `SetPatchRevision(rev)` | `0x371a0` | 设置补丁版本号 |
| `SetThreadNumber(min, max)` | `0x371b0` | 设置工作线程数范围 |
| `SetNetworkWorker(count)` | `0x37b40` | 设置网络工作线程数 |
| `SetAllowedConnections(count)` | `0x38190` | 设置最大连接数 |
| `Start(argc, argv)` | `0x38150` | 启动服务主循环 |
| `Shutdown()` | `0x373a0` | 关闭服务 |

### 4.2 framework::Request（入站请求）

Handler 回调接收 `shared_ptr<framework::Request>`:

```c
void Handler(shared_ptr<framework::Request> request) {
    // request 内部包含 PPJson 数据
    // offset +152 (0x98): PPJson::Value (请求 JSON 数据)

    PPJson::Value& data = request->data["data"];  // 获取 data 字段
    int pageSize = data["pageSize"].getInt();
    int page = data["page"].getInt();

    // 参数校验
    framework::Request::CheckParams(request, isPresent, "paramName");
}
```

**请求 JSON 结构**:
```json
{
    "req": "handler.name",        // API 名称，如 "common.list"
    "reqid": "request-id",        // 请求 ID
    "data": {                     // 业务参数
        "pageSize": 20,
        "page": 1,
        "level": 0,
        "module": "storage",
        "locale": "zh-CN"
    }
}
```

### 4.3 framework::Response（响应）

```c
// 构造响应
framework::Response response(request);  // 从 Request 构造

// 成功响应
response.data["result"]["total"] = count;
response.data["result"]["rows"].append(item);
response.status = 2;  // SUCCESS
response.Send();

// 失败响应
response.SetFailed(100010013);  // 错误码
response.Send();
```

**Response 内存布局** (从析构函数 0x68a80 推断):

```
┌──────────────────────────────────────────────┐
│ framework::Response                          │
├──────────────────────────────────────────────┤
│ offset   0: [8 bytes, 首字段]                │
│ offset   8: std::string (32 bytes)           │ SSO at offset 24
│ offset  40: std::string (32 bytes)           │ SSO at offset 56
│ offset  72: [16 bytes]                       │
│ offset  88: shared_ptr<Request> (8 bytes)    │ 关联的请求对象
│ offset  96: PPJson::MutDocument (8 bytes)    │
│ offset 104: PPJson::MutValue (80 bytes)      │ 响应 JSON 数据
│ offset 184: int status_code (4 bytes)        │ 状态码：2=成功
└──────────────────────────────────────────────┘
```

### 4.4 framework::Response 静态成员

- `Response::s_result_key_[abi:cxx11]` (0x10ea00) — 静态 std::string，作为 JSON 响应中结果数据的键名

---

## 5. RPC 通信流程

### 5.1 客户端发起 RPC 调用

```
┌─────────────────────────────────────────────────────────────┐
│                    RPC 客户端调用流程                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. 创建 RpcClient                                          │
│     client = new RpcClient(uuid, my_service_name)           │
│                                                             │
│  2. 申请权限                                                │
│     ret = client->ApplyPermission(target_service)           │
│     if (ret != 0) { error("Failed to apply permissions"); } │
│                                                             │
│  3. 构造请求                                                │
│     Request req(target_service, method, "");                 │
│     MutValue* params = req.params();                        │
│     params["key1"] = value1;                                │
│     params["data"]["key2"] = value2;                        │
│                                                             │
│  4. 发送调用                                                │
│     ret = client->Call(req);                                │
│                                                             │
│  5. 处理响应                                                │
│     if (ret == 0) {                                         │
│         auto resp = client->response();                     │
│         // 读取 resp 数据                                   │
│     } else {                                                │
│         // 错误处理                                         │
│     }                                                       │
│                                                             │
│  6. 清理                                                    │
│     // Request 通过虚析构自动清理                            │
│     // RpcClient 通过 unique_ptr 或手动 delete 清理          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 服务端接收 RPC 调用

```
┌─────────────────────────────────────────────────────────────┐
│                    RPC 服务端处理流程                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. 注册服务                                                │
│     app = new Application("eventlogger", 0, 1);             │
│     app->RegisterRpcService(uuid, "logger service");        │
│                                                             │
│  2. 注册 Handler                                            │
│     app->RegisterHandler("common.list", OnList);            │
│     app->RegisterHandler("common.clear", OnClear);          │
│     ...                                                     │
│                                                             │
│  3. 启动服务                                                │
│     app->SetAllowedConnections(100);                        │
│     app->SetNetworkWorker(2);                               │
│     app->SetThreadNumber(hw_concurrency, hw_concurrency);   │
│     app->Start(argc, argv);                                 │
│                                                             │
│  4. Handler 处理请求                                        │
│     void OnList(shared_ptr<Request> req) {                  │
│         Value& data = req->json[0x98]["data"];              │
│         // 读取参数...                                      │
│         Response resp(req);                                 │
│         resp.data["result"]["total"] = count;               │
│         resp.status = 2;                                    │
│         resp.Send();                                        │
│     }                                                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 权限模型

```
┌──────────────────────────────────────────────────────────┐
│                    trimrpc 权限模型                       │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  UUID: 全局服务注册标识符                                 │
│  ├── 服务端: RegisterRpcService(uuid, display_name)      │
│  └── 客户端: new RpcClient(uuid, caller_service_name)    │
│                                                          │
│  服务名: 权限控制粒度                                     │
│  ├── ApplyPermission("com.trim.resmon")                  │
│  │   → 获取调用 resmon 服务所有方法的权限                 │
│  └── ApplyPermission("com.trim.main")                    │
│      → 获取调用 main 服务所有方法的权限                   │
│                                                          │
│  注意:                                                   │
│  - 权限是基于服务名的，不是基于方法名                     │
│  - 一个 RpcClient 可申请多个目标服务的权限                │
│  - 同一 UUID 被多个服务共用（服务端和客户端使用相同 UUID） │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 6. 使用模式分析

### 6.1 AlertMgr 代理模式

AlertMgr 使用单例 RpcClient（在构造函数中创建），5 个 Handler 都通过同一个 `proxy()` 方法转发：

```c
// AlertMgr 构造函数 (0x48ea0)
AlertMgr::AlertMgr() {
    rpc_client = new RpcClient(
        "EECC6649-92CB-4246-A76A-F14358F13C07",
        "com.trim.eventlogger"
    );
    if (rpc_client) {
        if (rpc_client->ApplyPermission("com.trim.resmon") == 0) {
            initialized = true;
        }
    }
}

// Handler 注册 (在 LoggerApp::Start 中)
RegisterHandler("alert.setBeepEvents", AlertMgr::OnSetBeepEvents);
// → 内部调用 proxy(request, "alert.setBeepEvents")

// 代理函数 (0x49a50) 伪代码
void AlertMgr::proxy(shared_ptr<Request> req, const char* action) {
    mutex_lock(&lock);

    if (!initialized) {
        // 延迟初始化：尝试再次申请权限
        if (rpc_client->ApplyPermission("com.trim.resmon") == 0) {
            initialized = true;
        } else {
            Response(req).SetFailed(100010013).Send();
            return;
        }
    }

    // 获取请求数据
    Value& data = req->json["data"];

    // 创建 RPC 请求
    Request rpc_req("com.trim.resmon", action, "");

    // 复制参数（排除 "req" 和 "reqid"）
    for (auto& [key, value] : data) {
        if (key != "req" && key != "reqid") {
            rpc_req.params()[key] = value;
        }
    }

    // 发送 RPC 调用
    int ret = rpc_client->Call(rpc_req);
    if (ret == 0) {
        // 成功：转发响应
        auto resp_data = rpc_client->response();
        Response resp(req);
        resp["data"]["result"] = resp_data;
        resp.status = 2;
        resp.Send();
    } else {
        // 失败
        spdlog::warn("call {} ret {} errno: {}", action, ret, errno);
        Response(req).SetFailed(100010013).Send();
    }

    mutex_unlock(&lock);
}
```

**特点**:
- 单例 RpcClient，长连接复用
- mutex 保护并发访问
- 延迟权限申请（支持 resmon 服务后启动的场景）
- 透明代理：参数过滤后原样转发

### 6.2 NotificationSender 一次性模式

NotificationSender 每次调用都创建新的 RpcClient：

```c
// SendNotification (0xd64e0) 伪代码
void NotificationSender::SendNotification(
    const std::string& eventId,
    const std::string& path
) {
    // 每次创建新的 RpcClient
    auto client = new RpcClient(
        "EECC6649-92CB-4246-A76A-F14358F13C07",
        "com.trim.eventlogger"
    );

    // 申请权限
    if (client->ApplyPermission("com.trim.main") != 0) {
        spdlog::error("Failed to apply permissions: {}", ret);
        delete client;
        return;
    }

    // 构造请求
    Request req("com.trim.main", "sendNotify", "");
    auto params = req.params();
    params["uid"] = 0;
    params["category"] = 0;
    params["level"] = 0;
    params["title"] = "";
    params["from"] = "log-center";
    params["data"]["eventId"] = eventId;
    params["data"]["PATH"] = path;

    // 发送
    int ret = client->Call(req);
    if (ret == 0) {
        spdlog::info("Succeed to send notification!");
    } else {
        spdlog::error("Failed to send notification, ret:{}!", ret);
    }

    // 清理
    delete client;  // 通过 unique_ptr 自动清理
}
```

**特点**:
- 每次创建新连接，非长连接
- 无并发保护（假设调用方已处理并发）
- 参数固定格式：`from` 固定为 `"log-center"`

---

## 7. 关键数据格式

### 7.1 sendNotify 请求格式

```json
{
    "uid": 0,
    "category": 0,
    "level": 0,
    "title": "",
    "from": "log-center",
    "data": {
        "eventId": "<event_id_string>",
        "PATH": "<event_path_string>"
    }
}
```

### 7.2 AlertMgr 代理请求格式

代理会将原始请求中除 `req` 和 `reqid` 以外的所有字段复制到 RPC 请求中：

```json
// 原始请求
{
    "req": "alert.setBeepEvents",
    "reqid": "xxx-xxx",
    "data": { ... },
    "otherField": "value"
}

// 转发给 com.trim.resmon 的 RPC 请求
{
    "data": { ... },
    "otherField": "value"
}
```

---

## 8. framework::dbus 集成

除了 trimrpc，framework 还提供 DBus 信号订阅能力：

```c
// 函数签名
int framework::dbus::SubscribeSignal(
    const char* path,           // DBus 对象路径
    const char* service,        // DBus 服务名，如 "com.trim.i18n"
    std::vector<MemberHandler>& handlers  // 回调处理器列表
);
```

**MemberHandler 结构** (每个 64 字节):
```
┌──────────────────────────────────┐
│ MemberHandler (64 bytes)         │
├──────────────────────────────────┤
│ offset  0: std::string signal_name (32 bytes)│ 信号名称
│ offset 32: std::function callback  (32 bytes)│ 回调函数
│   +32: captured_ptr (this 指针)              │
│   +40: invoke_ptr  (调用函数指针)            │
│   +48: destroy_ptr (销毁函数指针)            │
└──────────────────────────────────┘
```

---

## 9. IDA 操作记录

### 9.1 添加的注释

| 目标 | 注释数量 |
|---|---|
| trimrpc PLT 函数（6个） | 6 条 |
| unique_ptr<RpcClient> 析构函数 | 12 条 |
| framework::Application PLT 函数 | 8 条 |
| framework::Response 函数 | 3 条 |
| framework::Response 析构函数 | 6 条 |
| framework::dbus::SubscribeSignal | 1 条 |
| AlertMgr Handler 函数（5个） | 5 条 |

**总计**: 41 条注释

### 9.2 创建的类型定义

| 类型名 | 大小 | 说明 |
|---|---|---|
| `trimrpc_RpcClient` | 136 bytes | RpcClient 外层对象布局 |
| `trimrpc_RpcClient_Internal` | 208 bytes | RpcClient 内部连接对象布局 |

### 9.3 交叉引用分析

| trimrpc 函数 | 调用位置 | 说明 |
|---|---|---|
| `RpcClient::RpcClient` | `AlertMgr::AlertMgr` (0x48f17), `SendNotification` (0xd653d) | 2处构造 |
| `RpcClient::ApplyPermission` | `AlertMgr::AlertMgr` (0x48f8a), `AlertMgr::proxy` (0x49b06), `SendNotification` (0xd65a8) | 3处权限申请 |
| `RpcClient::Call` | `AlertMgr::proxy` (0x49cdd), `SendNotification` (0xd6799) | 2处 RPC 调用 |
| `RpcClient::response` | `AlertMgr::proxy` (0x49cf4) | 1处获取响应 |
| `Request::Request` | `AlertMgr::proxy` (0x49bb6), `SendNotification` (0xd662c) | 2处请求构造 |
| `Request::params` | `AlertMgr::proxy` (0x49c8c), `SendNotification` (0xd668f) | 2处参数设置 |

---

## 10. 总结

### trimrpc 框架特征

1. **基于 UUID 的服务识别**: 每个 RPC 服务有唯一的 UUID，用于服务注册和客户端连接
2. **基于服务名的权限控制**: ApplyPermission 以目标服务名为粒度控制访问权限
3. **JSON 数据格式**: 使用 PPJson 库进行参数序列化和反序列化
4. **同步调用模型**: Call() 是阻塞调用，内部使用 condition_variable 等待响应
5. **轻量级 Request**: Request 对象是带虚表的多态指针包装器，仅占 8 字节栈空间
6. **重量级 Client**: RpcClient 占 136 字节 + 208 字节内部对象，适合长连接复用

### 与 framework 的集成

- **服务端**: framework::Application 提供 RegisterRpcService + RegisterHandler 实现服务端
- **客户端**: trimrpc::RpcClient + Request 实现客户端调用
- **数据桥接**: 两者共用 PPJson 作为 JSON 操作层
- **错误码**: framework::Response::SetFailed 使用数字错误码（如 100010013）

### 已知错误码

| 错误码 | 使用位置 | 含义推测 |
|---|---|---|
| `100010013` | AlertMgr::proxy | RPC 调用失败或权限申请失败的通用错误码 |
