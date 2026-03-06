# fnOS trimrpc 框架完整逆向分析报告

> **分析日期**: 2026-03-04
> **目标系统**: fnOS 1.1.15
> **架构**: x86_64 (ELF)
> **分析二进制文件**:
> - `libtrimrpc.so.0.1.3` — trimrpc 核心库
> - `libframework.so.2.9` — 应用框架库（trimrpc 服务端集成）
> - `eventlogger_service` — 应用层使用示例
> **分析工具**: IDA Pro 9.0 + ida-pro-mcp

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [RPC 二进制协议](#2-rpc-二进制协议)
3. [libtrimrpc.so — 核心库分析](#3-libtrimrpcso--核心库分析)
4. [libframework.so — 服务端框架分析](#4-libframeworkso--服务端框架分析)
5. [RPC Broker 机制](#5-rpc-broker-机制)
6. [完整通信流程](#6-完整通信流程)
7. [应用层使用模式](#7-应用层使用模式)
8. [错误码体系](#8-错误码体系)
9. [安全分析](#9-安全分析)
10. [函数地址索引](#10-函数地址索引)

---

## 1. 系统架构总览

### 1.1 trimrpc 是什么

`trimrpc` 是 fnOS（铁威马 NAS 操作系统）自研的**进程间通信 (IPC) 框架**，基于 Unix Domain Socket (UDS) 实现。它为系统中所有服务提供统一的 RPC 调用接口，支持两种传输协议：**RpcPacket**（面向 AppCgi 服务）和 **TrimSrv**（面向原生 TrimSrv 服务）。

### 1.2 整体架构

```
┌────────────────────────────────────────────────────────────────────────┐
│                         应用层 (Application Layer)                      │
│                                                                        │
│  eventlogger_service    storage_service    main_service    ...         │
│  (com.trim.eventlogger) (com.trim.storage) (com.trim.main)            │
│         │                      │                  │                    │
│         │  trimrpc::RpcClient  │                  │                    │
│         │  (客户端 API)         │                  │                    │
└─────────┼──────────────────────┼──────────────────┼────────────────────┘
          │                      │                  │
┌─────────┼──────────────────────┼──────────────────┼────────────────────┐
│         ▼                      ▼                  ▼                    │
│  ┌─────────────────────────────────────────────────────────────┐      │
│  │              libtrimrpc.so.0.1.3 (核心库)                    │      │
│  │                                                              │      │
│  │  RpcClient ──► Caller ──► RpcConnections ──► ne* 网络引擎    │      │
│  │     │              │            │                │            │      │
│  │     │         BuildRpcPacket    │           UDS 连接管理       │      │
│  │     │         BuildTrimSrv      │                │            │      │
│  │     │              │            │                │            │      │
│  │     │         RpcPacket     PacketProcessor      │            │      │
│  │     │         TrimSrvPacket RpcPacketProcessor   │            │      │
│  │     │                       TrimSrvProcessor     │            │      │
│  └─────┼────────────────────────────────────────────┼────────────┘      │
│        │                                            │                   │
│        │            Unix Domain Socket              │                   │
│        │         /var/run/rpc/<service>              │                   │
│        │                                            │                   │
│  ┌─────┼────────────────────────────────────────────┼────────────┐      │
│  │     ▼         libframework.so.2.9 (服务端)        ▼            │      │
│  │                                                              │      │
│  │  Application ──► ServiceHelper ──► CallBroker               │      │
│  │     │                │                  │                    │      │
│  │  RegisterHandler     │           framework::RpcPacket        │      │
│  │  ProcessRequest      │           (独立协议实现)               │      │
│  │     │                │                  │                    │      │
│  │  Request ──► Response ──► SendingController                  │      │
│  └──────────────────────────────────────────────────────────────┘      │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────┐      │
│  │              RPC Broker (com.trim.rpcbroker)                  │      │
│  │         UDS: /var/run/rpc/rpcbroker                           │      │
│  │                                                              │      │
│  │  服务注册表 ──► 路由转发 ──► 权限管理                           │      │
│  └──────────────────────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.3 库职责划分

| 库 | 角色 | 职责 |
|---|---|---|
| `libtrimrpc.so` | 客户端核心 | RPC 调用发起、连接管理、数据包编解码、双协议支持 |
| `libframework.so` | 服务端框架 | 服务注册/注销、请求接收与分发、响应构建与发送 |
| RPC Broker | 中央路由 | 服务发现、路由转发、权限验证 |

### 1.4 关键设计特征

- **中心化路由**: 所有服务通过 RPC Broker 注册和发现，客户端先向 Broker 申请权限再直连服务
- **双协议支持**: AppCgi (RpcPacket) 和 TrimSrv 两种协议，通过服务类型自动选择
- **异步网络引擎**: 客户端使用 `ne*` 网络引擎管理连接，服务端使用 `SendingController` 异步发送
- **UDS 通信**: 全部通过 Unix Domain Socket 本地通信，路径格式 `/var/run/rpc/<service_name>`
- **PPJson**: 自研 JSON 库用于请求/响应序列化

---

## 2. RPC 二进制协议

### 2.1 RpcPacket 协议 (AppCgi)

RpcPacket 是主要的 RPC 传输协议，使用 **30 字节固定头部** + 可变长度数据段。

#### 2.1.1 头部格式

**libtrimrpc.so 中的 RpcPacket (完整版)**:

```
偏移  大小  字段                说明
────  ────  ──────────────────  ─────────────────────────────
0x00  4B    magic               固定 0x54525043 ("CPRT" LE)
0x04  2B    version             协议版本 = 1
0x06  8B    session_id          会话标识 (原子递增)
0x0E  2B    data_len            JSON 数据区长度
0x10  2B    token_len           认证令牌长度
0x12  4B    extra_data_len      扩展数据1长度
0x16  4B    reserved            保留/标志位
0x1A  4B    extra_data2_len     扩展数据2长度
────  ────  ──────────────────  ─────────────────────────────
总计: 30 字节 (0x1E)
```

数据区紧随头部:
```
+──────────────────────────────────────────────+
| Header (30 bytes)                            |
+──────────────────────────────────────────────+
| data (data_len bytes) — JSON 请求/响应数据    |
+──────────────────────────────────────────────+
| token (token_len bytes) — UUID/认证令牌       |
+──────────────────────────────────────────────+
| extra_data (extra_data_len bytes)            |
+──────────────────────────────────────────────+
| extra_data2 (extra_data2_len bytes)          |
+──────────────────────────────────────────────+
```

**libframework.so 中的 RpcPacket (简化版)**:

```
偏移  大小  字段                说明
────  ────  ──────────────────  ─────────────────────────────
0x00  4B    magic               固定 0x54525043 ("CPRT" LE)
0x04  4B    version             协议版本 = 1
0x08  4B    section_len[0]      数据段1长度
0x0C  4B    section_len[1]      数据段2长度
0x10  4B    section_len[2]      数据段3长度
0x14  4B    section_len[3]      数据段4长度
0x18  6B    reserved            保留字节
────  ────  ──────────────────  ─────────────────────────────
总计: 30 字节 (0x1E)
```

两个实现共享相同的魔数 `0x54525043` 和 30 字节头部长度，但字段布局略有不同。libtrimrpc 版本将 session_id 嵌入头部；libframework 版本使用 4 个等价的 section_length 字段。

#### 2.1.2 请求 JSON 格式

```json
{
    "data": {
        "req": "<service_name>.<method_name>",
        "pid": 12345,
        "reqid": "0123456789abcdef"
    }
}
```

- `req`: 目标服务和方法名，如 `"com.trim.resmon.send_alert"`
- `pid`: 调用方进程 ID
- `reqid`: 当前时间戳转 16 字节十六进制字符串

#### 2.1.3 响应 JSON 格式

```json
{
    "data": {
        "reqid": 123456789,
        "result": "succ",
        "result_rev": "1.0",
        "session": "session_token",
        "errno": 0
    }
}
```

- `result`: `"succ"` 表示成功
- `errno`: 错误码，0 = 成功

### 2.2 TrimSrv 协议

TrimSrv 是面向原生 TrimSrv 服务的简化协议。

#### 2.2.1 头部格式 (12 字节)

```
偏移  大小  字段          说明
────  ────  ──────────  ─────────────────
0x00  4B    packet_size  整包大小 (含头)
0x04  8B    session_id   会话标识
────  ────  ──────────  ─────────────────
总计: 12 字节
```

- 数据长度 = `packet_size - 12`
- 无魔数校验，无版本字段
- 数据紧跟头部

#### 2.2.2 协议对比

| 特性 | RpcPacket (AppCgi) | TrimSrv |
|---|---|---|
| 头部大小 | 30 字节 | 12 字节 |
| 魔数 | "CPRT" (0x54525043) | 无 |
| 会话管理 | session_id 在头部 | session_id 在头部 |
| 数据格式 | 4 段 (data/token/extra/extra2) | 单一数据段 |
| 请求构建 | JSON `{data:{req, pid, reqid}}` | TrimSrvCommand 虚函数 |
| 命令路由 | `"service.method"` 字符串匹配 | CommandId 哈希查找 |
| 服务类型 | type = 0 | type = 1 |

---

## 3. libtrimrpc.so — 核心库分析

### 3.1 函数统计

- **总函数数**: 590
- **trimrpc 命名空间**: 266 个函数
- **主要类**: RpcClient, Caller, RpcConnections, RpcPacket, Request, Response, PacketProcessor

### 3.2 RpcClient (公共 API 层)

**构造函数 @ 0x259b0**

```cpp
trimrpc::RpcClient::RpcClient(
    const std::string& uuid,           // 服务 UUID
    const std::string& service_name    // 调用方服务名，如 "com.trim.eventlogger"
);
```

对象大小: 0x88 (136) 字节

| 偏移 | 大小 | 类型 | 说明 |
|---|---|---|---|
| 0 | 8 | Caller* | 内部 Caller 对象指针 |
| 8 | 32 | std::string | UUID (调用者标识) |
| 40 | 32 | std::string | service_name (服务名) |
| 72 | 4 | int32 | error_code (最后错误码) |
| 80 | 32 | std::string | response_string (原始响应) |
| 112 | 16 | shared_ptr | response (Response 对象) |
| 128 | 4 | int32 | last_call_error |
| 132 | 4 | int32 | pid (进程 ID) |

#### RpcClient::ApplyPermission() @ 0x27740

申请访问目标服务的权限，这是调用任何服务前的必要步骤。

```cpp
int RpcClient::ApplyPermission(const std::vector<std::string>& services) {
    // 1. 加锁 permission_mutex_
    // 2. 构建 JSON 请求:
    //    {"data": {"req": "com.trim.rpcbroker.apply",
    //              "pid": <pid>, "reqid": "<hex_timestamp>",
    //              "services": ["com.trim.service1", ...]}}
    // 3. Call("com.trim.rpcbroker", "apply", json_data)
    // 4. HandleServiceInfo() 解析响应，注册服务 UDS 路径和类型
}
```

ApplyPermission 从 Broker 获取的服务信息:
```json
{
    "data": {
        "result": "succ",
        "data": [
            {
                "id": "service_uuid",
                "name": "com.trim.service1",
                "uds": "/var/run/rpc/service1",
                "ip": "127.0.0.1",
                "type": 0,
                "token": "auth_token"
            }
        ]
    }
}
```

`type` 字段决定后续使用哪种协议:
- `type = 0`: AppCgi → 使用 RpcPacket 协议
- `type = 1`: TrimSrv → 使用 TrimSrv 协议

#### RpcClient::Call() @ 0x25bf0

```cpp
int RpcClient::Call(const std::string& service, const std::string& method,
                    const std::string& data) {
    // 1. VerifyServiceId(service) — 验证前缀 "com." 且长度 <= 128
    // 2. caller->Call(service, method, data) — 执行内部调用
    // 3. GetServiceType → Response::Create(type, response_data)
    // 4. 返回 error_code 或 response.error_no()
}
```

#### RpcClient::VerifyServiceId() @ 0x256c0

```
验证规则:
- 服务名长度 <= 128 字节，否则返回错误 5
- 服务名必须以 "com." 开头，否则返回错误 6
```

### 3.3 Caller (内部调用核心)

**构造函数 @ 0x12b60**

对象大小: 0xD0 (208) 字节

| 偏移 | 大小 | 类型 | 说明 |
|---|---|---|---|
| 0 | 32 | std::string | UUID |
| 32 | 32 | std::string | 临时字符串 |
| 64 | 4 | int32 | timeout (默认 10 秒) |
| 68 | 4 | int32 | pid (getpid()) |
| 72 | 32 | std::string | response_data |
| 104 | 4 | int32 | error_code (默认 -1) |
| 112 | 8 | int64 | session_id |
| 120 | 40 | pthread_mutex_t | mutex |
| 160 | 48 | condition_variable | cond_var |

**静态成员**: `Caller::session_id_` — 全局原子计数器，生成唯一会话 ID。

#### Caller::Call() @ 0x15480

```
调用总流程:
1. 获取/创建 RpcConnections 单例 (DCL 双重检查锁定)
2. RpcConnections::Connect(service_name)
3. 获取 ServiceType
4. 按类型构建数据包:
   - type=1 (TrimSrv): BuildTrimSrvPacket(service, method)
   - 其他 (AppCgi):    BuildRpcPacket(service, method, data)
5. CallOutInSync(connection, packet) — 同步发送并等待
6. CancelByPacket — 清理会话回调
注: TrimSrv 类型下 error 8 映射为 202
```

#### Caller::BuildRpcPacket() @ 0x14840

```
构建 RpcPacket:
1. new RpcPacket() — magic="CPRT", version=1
2. 如有 token, 设置到 RpcPacket.token
3. 构建 JSON: {"data":{"req":"<service>.<method>","pid":<pid>,"reqid":"<hex_ts>"}}
4. PPJson::MutDocument::write() → 序列化 JSON
5. 设置为 RpcPacket.data
6. 复制 UUID 到 RpcPacket.token
7. 原子递增 session_id_, 设置到 packet
8. 返回 shared_ptr<RpcPacket>
```

#### Caller::BuildTrimSrvPacket() @ 0x12d20

```
构建 TrimSrv 数据包:
1. TrimSrvCommand::GetCommandId(method) — 查命令哈希表
2. new TrimSrvRequestPacket()
3. 设置 data = method, CommandId, session_id
4. 返回 shared_ptr<TrimSrvRequestPacket>
```

#### Caller::CallOutInSync() @ 0x13cb0

```
同步等待机制:
1. 锁定 mutex
2. RpcConnections::Get(service) → 获取连接句柄
3. 绑定回调 = std::bind(Caller::NotifyResult, this, ...)
4. RpcConnections::Send(handle, packet, callback)
5. pthread_cond_clockwait(CLOCK_MONOTONIC, timeout)
   - 超时 → error_code = 100
6. 解锁 mutex
```

#### Caller::NotifyResult() @ 0x12860

```
异步回调 (由 ne* 接收线程触发):
1. 锁定 mutex
2. 复制响应数据到 response_data
3. 设置 error_code
4. signal condition_variable — 唤醒 CallOutInSync
5. 解锁 mutex
```

### 3.4 RpcConnections (单例连接管理器)

单例对象大小: 0x168 (360) 字节，使用 DCL 模式创建。

| 偏移 | 说明 |
|---|---|
| 0 | ne_instance (网络引擎实例) |
| 16-55 | RB-tree: socket_handle → ConnectionState |
| 104-151 | RB-tree: session_id → callback |
| 152-199 | RB-tree: service_name → ServiceInfo |
| 272-319 | mutex 组 |

#### RpcConnections::Init() @ 0x196b0

```
初始化:
1. neCreateInstance(10, thread_count) — 创建网络引擎
2. 注册 5 个回调:
   - neSetCallback(inst, 1, OnConnected)
   - neSetCallback(inst, 2, OnReceived)
   - neSetCallback(inst, 3, OnDisconnected)
   - neSetCallback(inst, 4, OnConnectError)
   - neSetCallback(inst, 5, OnSendError)
3. InitServiceMap() — 注册默认服务 "com.trim.rpcbroker"
```

#### RpcConnections::Connect() @ 0x1a920

```
建立连接:
1. neCreateSocket(ne_instance, 1, 0)
2. 创建 ConnectionState (0x68 字节)
3. GetServiceUDS(service) → 获取 UDS 路径
4. 按 ServiceType 创建对应的 PacketProcessor:
   - type=1: TrimSrvPacketProcessor (0x1B0 字节)
   - 其他:   RpcPacketProcessor (0x1B0 字节)
5. neConnect(inst, sock, uds_path, processor)
6. nanosleep 轮询等待 CONNECTING→CONNECTED
返回: 0=成功, 3=socket 错误, 4=连接错误
```

#### RpcConnections::Send() @ 0x192d0

```
发送数据包:
1. 锁定 send_mutex + callback_mutex
2. 获取 session_id (BasePacket 虚函数)
3. callback 存入 RB-tree (key=session_id)
4. BasePacket::Serialize() 序列化
5. SendPacket(handle, data, length)
6. 首次失败则重试一次
7. 完全失败: syslog + callback(session, 102, "")
```

#### RpcConnections::OnReceived() @ 0x17490

```
接收处理:
1. 数据送入 PacketProcessor::AppendAndParse
2. PacketProcessor 解析出完整包
3. 通过 session_id 查找 RB-tree 中的回调
4. 调用 callback(session_id, error_code, data)
```

#### RpcConnections::AddService() @ 0x1a0e0

```
注册服务:
1. 锁定 service_mutex
2. 在 service_map RB-tree 中查找/插入 ServiceInfo
3. ServiceInfo 包含: name, uds_path, ip, type, token
```

### 3.5 ne* 网络引擎接口

trimrpc 通过外部 `ne*` 函数族与底层网络引擎交互:

| 函数 | 说明 |
|---|---|
| `neCreateInstance(max_conn, thread_count)` | 创建网络引擎实例 |
| `neCreateSocket(instance, type=1, flags=0)` | 创建套接字 |
| `neConnect(instance, socket, uds_path, processor)` | 发起 UDS 连接 |
| `neSend(instance, socket, data, length, processor)` | 发送数据 |
| `neClose(instance, socket)` | 关闭套接字 |
| `neSetCallback(instance, event_type, callback)` | 注册事件回调 |

回调事件类型: 1=OnConnected, 2=OnReceived, 3=OnDisconnected, 4=OnConnectError, 5=OnSendError

### 3.6 PacketProcessor (数据流处理器)

#### RpcPacketProcessor::AppendAndParse @ 0x1cec0

```
处理 RpcPacket 协议数据流:
1. 追加接收数据到内部缓冲区
2. 检查 30 字节头部是否完整
3. 解析头部获取各段长度
4. 当所有数据段完整:
   DeserializeHeader → DeserializeData → 返回结果
```

#### TrimSrvPacketProcessor::AppendAndParse @ 0x1d4b0

```
处理 TrimSrv 协议数据流:
1. stringstream 缓冲接收数据
2. 当可用数据 > 12 字节:
   读 12 字节头部, data_length = packet_size - 12
3. 读取 data_length 字节
4. 验证完整性: 实际读取 == 预期大小
```

### 3.7 Request/Response 工厂

#### Request 工厂 @ 0x21680

```
根据 ServiceType 创建不同子类:
- type=1: TrimSrvRequest (0xD8 字节)
- 其他:   AppCgiRequest (0xE8 字节)
```

#### Response 工厂 @ 0x23b80

```
根据 ServiceType 创建不同子类:
- type=0: AppCgiResponse  (0xE0 字节)
- type=1: TrimSrvResponse (0xB0 字节)
- 其他:   RawResponse     (0x90 字节)
所有返回 shared_ptr<Response>
```

### 3.8 TrimSrvCommand 系统

#### TrimSrvCommand::GetCommandId() @ 0x1fef0

```
从全局 CommandMap 查找方法名 → CommandId:
- 小表 (<=20): 线性遍历链表
- 大表 (>20):  布谷哈希 (seed = 0xC70F6907)
- 返回 int32 CommandId，未找到返回 0
```

已知的 TrimSrvCommand 子类:
- `TokenQueryCommand` — 令牌查询
- `NotificationCommand` — 通知
- `GetStorageStateCommand` — 获取存储状态
- `GetStorageAndCacheStateCommand` — 获取存储和缓存状态

---

## 4. libframework.so — 服务端框架分析

### 4.1 概述

`libframework.so.2.9` 是 fnOS 应用框架库，提供 RPC 服务端能力。它包含独立的 `framework::RpcPacket` 实现（与 `trimrpc::RpcPacket` 代码分离但协议兼容），以及服务注册、请求处理和响应发送的完整流程。

### 4.2 framework::RpcPacket

```cpp
class framework::RpcPacket {
    uint32_t magic;        // 0x54525043 ("CPRT")
    uint32_t version;      // 1
    std::string data[4];   // 4 个数据段 (SSO 优化)
};
```

#### 关键函数

| 函数 | 地址 | 说明 |
|---|---|---|
| RpcPacket::RpcPacket() | 0x4f390 | 初始化 magic=CPRT, version=1, 4 个空 string |
| RpcPacket::Serialize() | 0x4e020 | 30 字节头 + 4 个数据段 → binary string |
| RpcPacket::DeserializeHeader() | 0x4f310 | 解析 30 字节头部，验证 magic |
| RpcPacket::DeserializeData() | 0x4e1e0 | 按头部长度字段解析 4 个数据段 |

**与 libtrimrpc 版本的差异**:
- framework 版本更简单：无虚表、无 session_id 字段、无 extra_data
- framework 版本仅用于 ServiceHelper 与 Broker 通信
- framework 使用直接 socket API；libtrimrpc 使用 ne* 网络引擎

### 4.3 framework::ServiceHelper

ServiceHelper 负责通过 UDS 与 RPC Broker 通信，完成服务注册和注销。

#### ServiceHelper::Register() @ 0x5e2a0

```cpp
bool ServiceHelper::Register(
    const string& service_id,
    const string& service_name,    // "com.trim.<app_name>"
    const string& uds_path,       // "/var/run/rpc/<app_name>"
    const string& ip_address,     // "127.0.0.1"
    int service_type,             // 服务类型
    const string& revision        // 版本号
) {
    // 构建 JSON
    PPJson doc;
    doc["data"]["req"] = "com.trim.rpcbroker.register";
    doc["data"]["id"] = service_id;
    doc["data"]["name"] = service_name;
    doc["data"]["uds"] = uds_path;
    doc["data"]["ip"] = ip_address;
    doc["data"]["type"] = service_type;
    doc["data"]["revision"] = revision;

    // 封装到 RpcPacket 并发送
    RpcPacket packet;
    packet.data[0] = doc.write();
    return CallBroker(packet);
}
```

#### ServiceHelper::CallBroker() @ 0x5e1b0

```cpp
bool ServiceHelper::CallBroker(const RpcPacket& packet) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);

    sockaddr_un addr;
    addr.sun_family = AF_UNIX;
    strcpy(addr.sun_path, "/var/run/rpc/rpcbroker");
    connect(fd, &addr, sizeof(addr));

    string serialized = packet.Serialize();
    send(fd, serialized.data(), serialized.size(), 0);

    char buf[4096];
    recv(fd, buf, sizeof(buf), 0);
    close(fd);

    // 解析响应 RpcPacket
    RpcPacket response;
    response.DeserializeHeader(buf, received);
    response.DeserializeData(buf, received);
    return true;
}
```

**关键特征**: 与 libtrimrpc 不同，ServiceHelper 直接使用 POSIX socket API (`socket()` + `connect()` + `send()` + `recv()`)，不依赖 ne* 网络引擎。

#### ServiceHelper::Unregister() @ 0x5e8e0

```cpp
bool ServiceHelper::Unregister(const string& service_id) {
    PPJson doc;
    doc["data"]["req"] = "com.trim.rpcbroker.unregister";
    doc["data"]["id"] = service_id;

    RpcPacket packet;
    packet.data[0] = doc.write();
    return CallBroker(packet);
}
```

### 4.4 framework::Application (RPC 生命周期管理)

#### Application::RegisterRpcService() @ 0x26480

```cpp
void Application::RegisterRpcService() {
    // 在 detached 线程中执行注册
    std::thread([this]() {
        ServiceHelper helper;
        helper.Register(
            GenerateServiceId(),
            "com.trim." + app_name_,
            "/var/run/rpc/" + app_name_,
            "127.0.0.1",
            service_type_,
            app_version_
        );
    }).detach();
}
```

#### Application::UnregisterRpcService() @ 0x26670

```cpp
void Application::UnregisterRpcService() {
    ServiceHelper helper;
    helper.Unregister(service_id_);
}
```

### 4.5 framework::Request

`framework::Request` 封装入站 RPC 请求:

| 字段 | 类型 | 说明 |
|---|---|---|
| reqid | uint64_t | 请求 ID |
| method | std::string | 请求方法名 |
| session | std::string | 会话标识 |
| packet | shared_ptr\<BasePacket\> | 底层数据包指针 |
| start_time | uint64_t | 请求开始时间戳 |

### 4.6 framework::Response

#### Response::Response(Request*) @ 0x59bd0

从 Request 构造 Response，复制 reqid、method、session、packet 指针，初始化 PPJson 文档。

#### Response::SetFailed() @ 0x5a5d0

```cpp
void Response::SetFailed(int errno_code) {
    status_ = STATUS_FAILED;  // 3
    json_root_["data"]["errno"] = errno_code;
}
```

#### Response::Send() @ 0x59e20

```cpp
void Response::Send() {
    // 1. 填充响应 JSON
    json_root_["data"]["reqid"] = reqid_;
    json_root_["data"]["result"] = GetStatusString(status_);
    json_root_["data"]["session"] = session_;

    // 2. 序列化 JSON → 写入 packet
    string json_str = json_doc_.write(json_root_, 0x22, false);
    packet_->WriteData(json_str.c_str(), json_str.size());

    // 3. 添加到 SendingController 发送队列
    SendingController::instance()->AddTask(packet_);

    // 4. 性能监控：检测慢请求
    uint64_t elapsed_ms = (now() - start_time_) / 1000000;
    if (elapsed_ms > s_time_consuming_limit_ms_) {
        SPDLOG_WARN("[PERF]Slow request, req id:{}, method:{}, cost:{}",
                    reqid_, method_, elapsed_ms);
    }
}
```

---

## 5. RPC Broker 机制

### 5.1 Broker 概述

RPC Broker (`com.trim.rpcbroker`) 是 trimrpc 体系的中央路由服务，监听在 `/var/run/rpc/rpcbroker`。所有 RPC 服务的注册、发现和权限管理都通过 Broker 进行。

### 5.2 Broker API

| 请求 | 方向 | 说明 |
|---|---|---|
| `com.trim.rpcbroker.register` | 服务端 → Broker | 注册新服务 |
| `com.trim.rpcbroker.unregister` | 服务端 → Broker | 注销服务 |
| `com.trim.rpcbroker.apply` | 客户端 → Broker | 申请访问目标服务权限 |

#### 注册请求

```json
{
    "data": {
        "req": "com.trim.rpcbroker.register",
        "id": "unique_service_id",
        "name": "com.trim.service_name",
        "uds": "/var/run/rpc/service_name",
        "ip": "127.0.0.1",
        "type": 1,
        "revision": "1.0.0"
    }
}
```

#### 注销请求

```json
{
    "data": {
        "req": "com.trim.rpcbroker.unregister",
        "id": "unique_service_id"
    }
}
```

#### 权限申请请求

```json
{
    "data": {
        "req": "com.trim.rpcbroker.apply",
        "pid": 12345,
        "reqid": "0123456789abcdef",
        "services": ["com.trim.service1", "com.trim.service2"]
    }
}
```

#### 权限申请响应

```json
{
    "data": {
        "result": "succ",
        "data": [
            {
                "id": "service_uuid",
                "name": "com.trim.service1",
                "uds": "/var/run/rpc/service1",
                "ip": "127.0.0.1",
                "type": 0,
                "token": "auth_token_string"
            }
        ]
    }
}
```

### 5.3 服务发现流程

```
                客户端                      Broker                    服务端
                  │                           │                         │
                  │                           │    ①Register            │
                  │                           │◄─────────────────────── │
                  │                           │  (name,uds,ip,type)     │
                  │                           │ ──────────────────────► │
                  │                           │    OK                   │
                  │                           │                         │
                  │  ②ApplyPermission         │                         │
                  │ ─────────────────────────►│                         │
                  │  (services:[...])         │                         │
                  │ ◄─────────────────────────│                         │
                  │  (uds,ip,type,token)      │                         │
                  │                           │                         │
                  │  ③Direct Connect via UDS  │                         │
                  │ ─────────────────────────────────────────────────► │
                  │  RpcPacket / TrimSrv      │                         │
                  │ ◄───────────────────────────────────────────────── │
                  │  Response                 │                         │
```

**关键发现**: 客户端获取权限后直接与目标服务建立 UDS 连接通信，Broker 不参与后续数据转发。这是一种**服务发现模式**，而非代理模式。

---

## 6. 完整通信流程

### 6.1 客户端发起调用 (RpcPacket/AppCgi 协议)

```
应用程序
  │
  ├─ 1. RpcClient::ApplyPermission(["com.trim.target_service"])
  │     ├─ 构建 JSON: {data:{req:"com.trim.rpcbroker.apply", services:[...]}}
  │     ├─ Call("com.trim.rpcbroker", "apply", json)
  │     └─ HandleServiceInfo: 解析响应 → AddService(name, uds, type, token)
  │
  ├─ 2. Request req("com.trim.target_service", "method_name")
  │     └─ 工厂: GetServiceType → AppCgiRequest / TrimSrvRequest
  │
  └─ 3. RpcClient::Call(req)
        ├─ req.Build() → 序列化请求
        ├─ VerifyServiceId("com.trim.target_service") ✓
        │
        └─ Caller::Call("com.trim.target_service", "method_name", data)
              ├─ RpcConnections::Connect("com.trim.target_service")
              │     ├─ neCreateSocket → neConnect(uds_path)
              │     ├─ 创建 RpcPacketProcessor / TrimSrvPacketProcessor
              │     └─ nanosleep 等待 CONNECTED
              │
              ├─ BuildRpcPacket:
              │     ├─ RpcPacket(magic="CPRT", version=1)
              │     ├─ JSON: {data:{req:"<service>.<method>", pid, reqid}}
              │     └─ session_id = atomic_increment
              │
              ├─ CallOutInSync:
              │     ├─ callback 存入 RB-tree[session_id]
              │     ├─ Serialize → neSend
              │     └─ pthread_cond_clockwait(10秒超时)
              │
              │   ... 等待 ...
              │
              │   [ne* 接收线程]:
              │     ├─ OnReceived → PacketProcessor::AppendAndParse
              │     ├─ 查找 callback[session_id]
              │     └─ NotifyResult → signal condition_variable
              │
              └─ 返回 error_code + response_data
```

### 6.2 服务端处理请求

```
libframework.so 服务端
  │
  ├─ 1. Application 启动
  │     ├─ RegisterRpcService() (detached 线程)
  │     │     └─ ServiceHelper::Register → CallBroker
  │     │           └─ socket → connect(/var/run/rpc/rpcbroker) → send RpcPacket
  │     │
  │     └─ RegisterHandler("method_name", handler_func)
  │
  ├─ 2. 接收请求
  │     ├─ OnReceivedRequest() — 网络层回调
  │     ├─ Request::Parse(packet_data)
  │     │     ├─ 解析 JSON → 提取 req, reqid, session
  │     │     └─ CheckUserToken() — 验证令牌
  │     └─ ProcessRequest(request)
  │           └─ 查找已注册的 handler → handler(request, response)
  │
  ├─ 3. 构建响应
  │     ├─ Response response(request)
  │     ├─ response["data"]["result_data"] = ...
  │     └─ response.Send()
  │           ├─ 填充 JSON: reqid, result, session
  │           ├─ Serialize → WriteData to packet
  │           ├─ SendingController::AddTask(packet)
  │           └─ 性能监控: 检测慢请求
  │
  └─ 4. Application 关闭
        └─ UnregisterRpcService()
              └─ ServiceHelper::Unregister → CallBroker
```

---

## 7. 应用层使用模式

### 7.1 eventlogger_service 中的 RPC 客户端使用

`eventlogger_service` 展示了典型的 trimrpc 客户端使用模式:

```cpp
// 1. 构造 RPC 客户端
trimrpc::RpcClient client(
    "EECC6649-92CB-4246-A76A-F14358F13C07",  // UUID
    "com.trim.eventlogger"                     // 调用方服务名
);

// 2. 申请目标服务权限
client.ApplyPermission("com.trim.resmon");

// 3. 构造请求
trimrpc::Request req("com.trim.resmon", "send_alert", "");
PPJson::MutValue* params = req.params();
(*params)["alert_type"] = "disk_error";
(*params)["message"] = "Disk S.M.A.R.T. warning";

// 4. 发送请求并获取响应
int ret = client.Call(req);
if (ret == 0) {
    auto resp = client.response();
    // 处理响应...
}
```

### 7.2 PLT/GOT 导入函数

应用程序通过以下 6 个 PLT 入口使用 trimrpc:

| PLT 地址 | 函数签名 |
|---|---|
| 0x38080 | `RpcClient::RpcClient(string& uuid, string& service)` |
| 0x38160 | `RpcClient::ApplyPermission(string& target)` |
| 0x375d0 | `RpcClient::Call(Request& req)` |
| 0x37810 | `RpcClient::response()` |
| 0x38010 | `Request::Request(string& target, string& method, string& extra)` |
| 0x37660 | `Request::params()` |

*注: 地址来自 eventlogger_service 二进制文件*

### 7.3 服务命名规范

所有服务名遵循 `com.trim.<service_name>` 格式:

| 服务名 | 说明 |
|---|---|
| `com.trim.rpcbroker` | RPC Broker 中央路由 |
| `com.trim.eventlogger` | 事件日志服务 |
| `com.trim.resmon` | 资源监控服务 |
| `com.trim.main` | 主服务 |
| `com.trim.storage` | 存储管理服务 |

UDS 路径: `/var/run/rpc/<service_name>` (如 `/var/run/rpc/rpcbroker`)

---

## 8. 错误码体系

### 8.1 RpcClient/Caller 层错误码

| 错误码 | 含义 | 产生位置 |
|---|---|---|
| 0 | 成功 | 各处 |
| 1 | HandleServiceInfo 服务数量不匹配 | HandleServiceInfo |
| 3 | Socket 创建失败 | RpcConnections::Connect |
| 4 | 连接失败 | RpcConnections::Connect |
| 5 | 服务名超过 128 字节 | VerifyServiceId |
| 6 | 服务名不以 "com." 开头 | VerifyServiceId |
| 8 | TrimSrv 通信错误 (映射为 202) | Caller::Call |
| 100 | 同步等待超时 | CallOutInSync |
| 101 | Future 等待超时 | CallOutWithFuture |
| 102 | 发送完全失败 | RpcConnections::Send |
| 200 | HandleServiceInfo JSON 解析失败 | HandleServiceInfo |
| 201 | TrimSrvRequest::Build 失败 | TrimSrvRequest::Build |
| 202 | TrimSrv 通信错误 (error 8 映射) | Caller::Call |

### 8.2 Response 状态码

| 状态值 | 含义 | 对应字符串 |
|---|---|---|
| 0 | STATUS_UNKNOWN | — |
| 1 | STATUS_SUCCESS | "succ" |
| 3 | STATUS_FAILED | "fail" |

---

## 9. 安全分析

### 9.1 认证与授权

- **服务名验证**: 仅检查前缀 `"com."` 和长度 <= 128，无 ACL 或细粒度权限控制
- **Token 机制**: RpcPacket 支持 token 字段，ApplyPermission 响应中包含 token，但验证程度未知
- **CheckUserToken**: framework::Request 中有 CheckUserToken 方法，具体实现需进一步分析

### 9.2 通信安全

- **明文 UDS**: 所有通信通过 Unix Domain Socket 明文传输，无加密
- **本地通信**: UDS 限制了通信范围为本机，提供了一定的隔离
- **文件权限**: UDS 文件权限由 `/var/run/rpc/` 目录权限控制

### 9.3 可靠性

- **单次重试**: Send 失败后仅重试一次，无指数退避
- **固定超时**: 默认 10 秒，可通过 SetTimeOut 调整
- **会话 ID 可预测**: 简单原子递增计数器，非加密随机数
- **无心跳**: 未发现连接保活或心跳机制

### 9.4 潜在攻击面

1. **UDS 劫持**: 若攻击者可创建 `/var/run/rpc/<service>` socket，可劫持服务通信
2. **JSON 注入**: 请求/响应使用 PPJson 序列化，需确保 JSON 解析器不存在漏洞
3. **服务名欺骗**: 仅验证 `"com."` 前缀，任何本地进程可注册任意 `com.*` 服务名
4. **DoS**: 无速率限制，恶意客户端可发送大量请求

---

## 10. 函数地址索引

### 10.1 libtrimrpc.so.0.1.3

#### RpcClient
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x259b0 | RpcClient::RpcClient | 构造函数 |
| 0x261e0 | RpcClient::Call(Request) | 高层调用入口 |
| 0x25bf0 | RpcClient::Call(s,m,d) | 核心调用流程 |
| 0x25980 | RpcClient::response | 获取响应 |
| 0x27740 | RpcClient::ApplyPermission(vec) | 多服务权限 |
| 0x28040 | RpcClient::ApplyPermission(str) | 单服务权限 |
| 0x256c0 | RpcClient::VerifyServiceId | 服务名验证 |
| 0x26330 | RpcClient::HandleServiceInfo | 服务信息解析 |

#### Caller
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x12b60 | Caller::Caller | 构造函数 |
| 0x15480 | Caller::Call | 内部调用总流程 |
| 0x14840 | Caller::BuildRpcPacket | RpcPacket 构建 |
| 0x12d20 | Caller::BuildTrimSrvPacket | TrimSrv 包构建 |
| 0x13cb0 | Caller::CallOutInSync | 同步等待 |
| 0x12860 | Caller::NotifyResult | 异步回调 |
| 0x14600 | Caller::CallOutWithFuture | Future 异步调用 |

#### RpcConnections
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x196b0 | RpcConnections::Init | 网络初始化 |
| 0x19520 | RpcConnections::InitServiceMap | 默认服务注册 |
| 0x1a920 | RpcConnections::Connect | 建立连接 |
| 0x192d0 | RpcConnections::Send | 发送数据包 |
| 0x17490 | RpcConnections::OnReceived | 接收处理 |
| 0x1a0e0 | RpcConnections::AddService | 添加服务 |
| 0x1a730 | RpcConnections::GetServiceType | 查询服务类型 |
| 0x17440 | RpcConnections::SendPacket | 底层发送 |

#### 协议处理
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x1ddf0 | RpcPacket::RpcPacket | 包构造 |
| 0x1dc30 | RpcPacket::Serialize | 序列化 |
| 0x1dea0 | RpcPacket::DeserializeHeader | 头部反序列化 |
| 0x1dee0 | RpcPacket::DeserializeData | 数据反序列化 |
| 0x1cec0 | RpcPacketProcessor::AppendAndParse | RpcPacket 流解析 |
| 0x1d4b0 | TrimSrvPacketProcessor::AppendAndParse | TrimSrv 流解析 |
| 0x21320 | TrimSrvResponsePacket::DeserializeHeader | TrimSrv 头部解析 |
| 0x163e0 | TrimSrvRequestPacket::Serialize | TrimSrv 序列化 |

#### Request/Response
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x21680 | Request::Request | 请求工厂 |
| 0x23b80 | Response::Create | 响应工厂 |
| 0x23510 | TrimSrvRequest::Build | TrimSrv 请求构建 |
| 0x21c70 | AppCgiRequest::Build | AppCgi 请求构建 |
| 0x24c20 | AppCgiResponse::error_no | AppCgi 错误码 |
| 0x252f0 | TrimSrvResponse::error_no | TrimSrv 错误码 |
| 0x1fef0 | TrimSrvCommand::GetCommandId | 命令 ID 查找 |

### 10.2 libframework.so.2.9

#### RpcPacket
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x4f390 | RpcPacket::RpcPacket() | 构造函数 |
| 0x4e020 | RpcPacket::Serialize() | 序列化为二进制 |
| 0x4f310 | RpcPacket::DeserializeHeader() | 解析头部 |
| 0x4e1e0 | RpcPacket::DeserializeData() | 解析数据段 |

#### ServiceHelper
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x5e2a0 | ServiceHelper::Register() | 注册服务 |
| 0x5e1b0 | ServiceHelper::CallBroker() | 调用 Broker |
| 0x5e8e0 | ServiceHelper::Unregister() | 注销服务 |
| 0x5da80 | ServiceHelper::WaitResponse() | 等待响应 |

#### Application
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x26480 | Application::RegisterRpcService() | 注册 RPC 服务 |
| 0x26670 | Application::UnregisterRpcService() | 注销 RPC 服务 |
| 0x2b5d0 | Application::RegisterHandler() | 注册请求处理器 |
| 0x2f060 | Application::ProcessRequest() | 处理请求 |
| 0x30240 | Application::OnReceivedRequest() | 接收请求回调 |

#### Request/Response
| 地址 | 函数 | 说明 |
|---|---|---|
| 0x59bd0 | Response::Response(Request*) | 构造响应 |
| 0x59e20 | Response::Send() | 发送响应 |
| 0x5a5d0 | Response::SetFailed() | 设置失败状态 |
| 0x5a620 | Response::SetTotal() | 设置总数 |
| 0x56660 | Response::NeedStableDelivery() | 检查稳定传输 |
| 0x56c80 | Response::Fallback() | 降级处理 |
| 0x47370 | Response::~Response() | 析构函数 |

---

## 附录 A: 依赖库

| 依赖 | 用途 |
|---|---|
| **PPJson** | JSON 序列化/反序列化 (MutDocument, MutValue, Document, Value) |
| **ne* 网络引擎** | UDS 连接管理 (libtrimrpc 专用) |
| **SPDLOG** | 结构化日志 (libframework 使用) |
| **base64** | 数据编解码 |
| **libpthread** | 线程同步 (mutex, condition_variable) |
| **libstdc++** | STL 容器、字符串、智能指针 |

## 附录 B: 关键常量

| 常量 | 值 | 说明 |
|---|---|---|
| CPRT Magic | 0x54525043 | RpcPacket 协议魔数 |
| Protocol Version | 1 | RpcPacket 协议版本 |
| Header Size (RpcPacket) | 30 字节 | RpcPacket 头部大小 |
| Header Size (TrimSrv) | 12 字节 | TrimSrv 头部大小 |
| Default Timeout | 10 秒 | RPC 调用默认超时 |
| Max Service Name | 128 字节 | 服务名最大长度 |
| Cuckoo Hash Seed | 0xC70F6907 | TrimSrvCommand 哈希种子 |
| Broker UDS Path | /var/run/rpc/rpcbroker | RPC Broker 监听路径 |

---

**分析完成时间**: 2026-03-04
**分析来源**: libtrimrpc.so.0.1.3 + libframework.so.2.9 + eventlogger_service
**IDA 注释**: 已添加到所有关键函数 (共 50+ 个)
