# libtrimrpc.so.0.1.3 逆向分析报告

> 分析目标: fnOS 1.1.15 trimrpc 框架核心共享库
> 二进制文件: `libtrimrpc.so.0.1.3` (x86_64 ELF)
> 函数总数: 590 (其中 trimrpc 命名空间 266 个)
> 分析日期: 2026-03-04

---

## 1. 概述

`libtrimrpc.so.0.1.3` 是 fnOS 的进程间通信(IPC)框架核心库。它实现了一个基于 Unix Domain Socket (UDS) 的 RPC 系统，支持两种协议：**RpcPacket 协议**（面向 AppCgi 服务）和 **TrimSrv 协议**（面向 TrimSrv 服务）。

### 核心架构

```
                        ┌─────────────────────┐
                        │    RpcClient (API)   │
                        │  ApplyPermission()   │
                        │  Call(Request)        │
                        │  response()           │
                        └──────────┬────────────┘
                                   │
                        ┌──────────▼────────────┐
                        │     Caller (内部)      │
                        │  BuildRpcPacket()      │
                        │  BuildTrimSrvPacket()  │
                        │  CallOutInSync()       │
                        │  CallOutWithFuture()   │
                        └──────────┬────────────┘
                                   │
               ┌───────────────────▼───────────────────┐
               │     RpcConnections (单例连接管理器)     │
               │  Init() / Connect() / Send()           │
               │  OnReceived() / OnSendError()          │
               │  AddService() / GetServiceType()       │
               │  GetServiceUDS()                       │
               └───────────┬───────────┬───────────────┘
                           │           │
              ┌────────────▼──┐  ┌─────▼────────────┐
              │  ne* 网络引擎  │  │  PacketProcessor  │
              │ neCreateSocket │  │ RpcPacketProcessor│
              │ neConnect      │  │ TrimSrvPacket...  │
              │ neSend         │  └──────────────────┘
              └───────────────┘
```

---

## 2. 类详细分析

### 2.1 RpcClient (公共 API 层)

**地址: 0x259b0 (构造函数)**

RpcClient 是面向用户的 API 类，封装了所有 RPC 调用细节。

#### 对象布局

| 偏移 | 大小 | 类型 | 说明 |
|------|------|------|------|
| 0 | 8 | Caller* | 内部 Caller 对象指针 |
| 8 | 32 | std::string | UUID (调用者标识) |
| 40 | 32 | std::string | service_name (服务名) |
| 72 | 4 | int32 | error_code (最后错误码) |
| 80 | 32 | std::string | response_string (原始响应) |
| 112 | 16 | shared_ptr | response (Response 对象) |
| 128 | 4 | int32 | last_call_error |
| 132 | 4 | int32 | pid (进程 ID) |

#### 关键方法

**RpcClient::Call(Request) @ 0x261e0**
```
高层调用入口:
1. request.Build()  → 获取序列化数据
2. request.method() → 获取方法名
3. request.service() → 获取服务名
4. 委托给 Call(service, method, data)
```

**RpcClient::Call(service, method, data) @ 0x25bf0**
```
核心调用流程:
1. VerifyServiceId(service)       → 验证服务名合法性
2. caller->Call(service, method, data) → 执行内部调用
3. 存储 error_code 到 offset 128
4. 复制响应字符串到 offset 80
5. 获取/创建 RpcConnections 单例
6. GetServiceType(service)        → 获取服务类型
7. Response::Create(type, data)   → 创建响应对象
8. 存储 response shared_ptr 到 offset 112
9. 如果 error == 0, 返回 response.error_no()
```

**RpcClient::VerifyServiceId @ 0x256c0**
```
验证规则:
- 服务名长度 <= 128 字节，否则返回错误 5
- 服务名必须以 "com." (0x2e6d6f63) 开头，否则返回错误 6
- 通过 syslog 记录验证失败
```

**RpcClient::ApplyPermission(vector<string>) @ 0x27740**
```
权限申请流程:
1. 锁定 permission_mutex_ (全局静态互斥锁)
2. 复制 Caller 内部的 service_name
3. 构建 JSON 请求:
   {
     "data": {
       "req": "com.trim.rpcbroker.apply",
       "pid": <进程ID>,
       "reqid": "<十六进制时间戳>",
       "services": ["service1", "service2", ...]
     }
   }
4. 调用 Call("com.trim.rpcbroker", "apply", json_data)
5. 成功后调用 HandleServiceInfo 解析响应并注册服务
```

**RpcClient::HandleServiceInfo @ 0x26330**
```
解析服务信息响应:
1. PPJson 解析 JSON 响应
2. 检查 data.result == "succ" (0x63637573)
3. 验证 data.data 数组长度匹配请求的服务数量
4. 遍历每个服务条目，读取:
   - id (string)      服务标识
   - name (string)    服务名称
   - uds (string)     UDS 套接字路径
   - ip (string)      IP 地址
   - type (int16)     服务类型 (0=AppCgi, 1=TrimSrv)
   - token (string)   认证令牌
5. 获取/创建 RpcConnections 单例
6. 调用 RpcConnections::AddService 注册每个服务
返回: 0=成功, 1=数量不匹配, 200=解析失败
```

---

### 2.2 Caller (内部调用核心)

**地址: 0x12b60 (构造函数)**

Caller 是 RPC 调用的内部实现层，负责数据包构建、发送和同步等待。

#### 对象布局 (0xD0 = 208 字节)

| 偏移 | 大小 | 类型 | 说明 |
|------|------|------|------|
| 0 | 32 | std::string | UUID |
| 32 | 32 | std::string | 临时字符串 |
| 64 | 4 | int32 | timeout (默认 10 秒) |
| 68 | 4 | int32 | pid (getpid()) |
| 72 | 32 | std::string | response_data (响应数据) |
| 104 | 4 | int32 | error_code (默认 -1) |
| 112 | 8 | int64 | session_id |
| 120 | 40 | pthread_mutex_t | mutex |
| 160 | 48 | condition_variable | cond_var |

**静态成员: `Caller::session_id_`** — 全局原子计数器，用于生成唯一会话 ID。

#### Caller::Call @ 0x15480
```
内部调用总流程:
1. 获取/创建 RpcConnections 单例 (双重检查锁定)
2. RpcConnections::Connect(service_name)
3. 获取 ServiceType
4. 根据类型构建数据包:
   - TrimSrv (type=1): BuildTrimSrvPacket(service, method)
   - RpcPacket (其他):  BuildRpcPacket(service, method, data)
5. CallOutInSync(connection, packet) — 同步发送并等待
6. CancelByPacket — 清理会话回调
注意: TrimSrv 类型下 error 8 被映射为 202
```

#### Caller::BuildRpcPacket @ 0x14840
```
RpcPacket 构建:
1. operator new(0xB8) — 分配 shared_ptr 控制块(16B) + RpcPacket(0xA8B)
2. RpcPacket::RpcPacket() — 初始化 magic="CPRT", version=1
3. 如果有 token (a5), 设置到 RpcPacket.token 字段
4. 构建 JSON (PPJson):
   {
     "data": {
       "req": "<service>.<method>",
       "pid": <pid>,
       "reqid": "<十六进制时间戳>"
     }
   }
5. PPJson::MutDocument::write() → 序列化 JSON 字符串
6. 设置为 RpcPacket.data 字段
7. 复制 UUID 到 RpcPacket.token 字段
8. 原子递增 session_id_, 设置到 packet 和 caller
9. 返回 shared_ptr<RpcPacket>
```

#### Caller::BuildTrimSrvPacket @ 0x12d20
```
TrimSrv 数据包构建:
1. TrimSrvCommand::GetCommandId(method) — 查命令哈希表
2. operator new(0x48) — 分配控制块 + TrimSrvRequestPacket
3. 设置数据字段 = method 字符串
4. 设置 CommandId
5. 原子递增 session_id_
6. TrimSrvRequestPacket::SetSession(session_id)
7. 返回 shared_ptr<TrimSrvRequestPacket>
```

#### Caller::CallOutInSync @ 0x13cb0
```
同步等待机制:
1. 锁定 mutex (offset 120)
2. RpcConnections::Get(service) → 获取连接句柄
3. 绑定回调 = std::bind(Caller::NotifyResult, this, ...)
4. RpcConnections::Send(handle, packet, callback)
5. pthread_cond_clockwait(CLOCK_MONOTONIC, timeout)
   - 超时 → error_code = 100
6. 解锁 mutex
```

#### Caller::NotifyResult @ 0x12860
```
异步回调函数:
1. 锁定 mutex (offset 120)
2. 复制响应数据到 response_data (offset 72)
3. 设置 error_code (offset 104)
4. 信号 condition_variable (offset 160) — 唤醒 CallOutInSync
5. 解锁 mutex
```

#### Caller::CallOutWithFuture @ 0x14600
```
异步调用版本:
1. 创建 std::future + promise
2. 启动新线程执行 RPC 调用
3. 使用 futex 等待结果
4. 超时设置错误 100/101
5. 通过 condition_variable 通知调用方
```

---

### 2.3 RpcConnections (单例连接管理器)

**单例大小: 0x168 = 360 字节**

管理所有 UDS 连接的全局单例，使用双重检查锁定模式 (DCL) 创建。

#### 对象布局 (关键偏移)

| 偏移 | 说明 |
|------|------|
| 0 | ne_instance (网络引擎实例) |
| 8 | 标志位 |
| 16-55 | RB-tree: 连接映射 (socket_handle → ConnectionState) |
| 56-103 | RB-tree: 连接状态映射 |
| 104-151 | RB-tree: 会话回调映射 (session_id → callback) |
| 152-199 | RB-tree: service_map (service_name → ServiceInfo) |
| 200-231 | RB-tree: 额外映射 |
| 232-271 | 更多状态 |
| 272-319 | mutex 组 |
| 320-359 | 更多状态 |

#### RpcConnections::Init @ 0x196b0
```
初始化流程:
1. neCreateInstance(10, thread_count) — 创建网络引擎实例
2. 注册 5 个回调:
   - neSetCallback(instance, 1, OnConnected)
   - neSetCallback(instance, 2, OnReceived)
   - neSetCallback(instance, 3, OnDisconnected)
   - neSetCallback(instance, 4, OnConnectError)
   - neSetCallback(instance, 5, OnSendError)
3. InitServiceMap() — 注册默认服务
```

#### RpcConnections::InitServiceMap @ 0x19520
```
预注册 "com.trim.rpcbroker" 服务:
- 名称: "Broker"
- UDS 路径: (从二进制中的常量字符串获取)
- 类型: 默认
```

#### RpcConnections::Connect @ 0x1a920
```
建立连接:
1. 锁定 mutex
2. 检查现有连接状态
3. neCreateSocket(ne_instance, 1, 0) — 创建套接字
4. 创建 ConnectionState (0x68 字节)
5. 添加到连接映射
6. GetServiceUDS(service) — 获取 UDS 路径
7. 确定 ServiceType:
   - type == 1: 创建 TrimSrvPacketProcessor (0x1B0 字节)
   - 其他:      创建 RpcPacketProcessor (0x1B0 字节)
8. neConnect(instance, socket, uds_path, packet_processor)
9. nanosleep 轮询等待连接完成:
   - 状态 CONNECTING(1) → CONNECTED(2)
返回: 0=成功, 3=socket 错误, 4=连接错误
```

#### RpcConnections::Send @ 0x192d0
```
发送数据包:
1. 锁定 send_mutex + callback_mutex
2. 获取 session_id (通过 BasePacket 虚函数 session())
3. 将 callback 插入 RB-tree (key=session_id)
4. 解锁 callback_mutex
5. 调用 BasePacket::Serialize() 虚函数序列化
6. SendPacket(handle, data, length) — 实际发送
7. 如果第一次 SendPacket 失败，重试一次
8. 完全失败时:
   - syslog(3, "send error:session:0x%0lx", session)
   - 调用 callback(session, 102, "")
9. 解锁 send_mutex
```

#### RpcConnections::OnReceived @ 0x17490
```
数据接收处理:
1. 将收到的数据送入 PacketProcessor::AppendAndParse
2. PacketProcessor 解析出完整数据包
3. 通过 session_id 在 RB-tree 中查找回调
4. 调用 callback(session_id, error_code, data_string)
```

#### RpcConnections::AddService @ 0x1a0e0
```
注册服务:
1. 锁定 service_mutex (offset 240)
2. 在 service_map RB-tree (offset 152) 中查找
3. 如果不存在，插入新的 ServiceInfo 节点
```

#### RpcConnections::GetServiceType @ 0x1a730
```
查询服务类型:
1. 在 service_map RB-tree 中查找服务名
2. 返回 ServiceInfo.type (uint16, node+192)
3. 未找到返回 0 (默认 AppCgi 类型)
```

---

### 2.4 RpcPacket 协议

**地址: 0x1ddf0 (构造函数)**

RpcPacket 是面向 AppCgi 服务的二进制协议，使用 30 字节固定头部。

#### 包头格式 (30 字节)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|         Magic "CPRT"          |   Version     |             |
|       (0x54525043, 4B)        |    (2B)       |             |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+             |
|                    Session ID (8 bytes)                     |
|                                               +-+-+-+-+-+-+|
|                                               | data_len   |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| data_len(2B) | token_len(2B) | extra_data_len (4B)         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| reserved/flags (4B)           | extra_data2_len (4B)        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0 | 4 | magic | 固定 `0x54525043` = "CPRT" |
| 4 | 2 | version | 协议版本 = 1 |
| 6 | 8 | session_id | 会话标识 (原子递增) |
| 14 | 2 | data_len | 数据区长度 |
| 16 | 2 | token_len | 令牌区长度 |
| 18 | 4 | extra_data_len | 扩展数据1长度 |
| 22 | 4 | reserved | 保留/标志 |
| 26 | 4 | extra_data2_len | 扩展数据2长度 |

#### RpcPacket 对象布局 (0xA8 字节)

| 偏移 | 大小 | 类型 | 说明 |
|------|------|------|------|
| 0 | 8 | vtable* | 虚表指针 |
| 8 | 4 | uint32 | magic (0x54525043) |
| 12 | 2 | uint16 | version (1) |
| 14 | 2 | uint16 | data_len |
| 16 | 2 | uint16 | token_len |
| 18 | 4 | uint32 | extra_data_len |
| 22 | 4 | uint32 | reserved |
| 26 | 4 | uint32 | extra_data2_len |
| 30 | 2 | padding | |
| 32 | 8 | uint64 | session_id |
| 40 | 32 | std::string | data (JSON 请求数据) |
| 72 | 32 | std::string | token (认证令牌/UUID) |
| 104 | 32 | std::string | extra_data |
| 136 | 32 | std::string | extra_data2 |

#### RpcPacket data 字段 JSON 格式
```json
{
  "data": {
    "req": "<service_name>.<method_name>",
    "pid": 12345,
    "reqid": "0123456789abcdef"
  }
}
```

其中 `reqid` 是当前时间戳转换为 16 字节十六进制字符串。

---

### 2.5 TrimSrv 协议

TrimSrv 是面向 TrimSrv 服务的更简单的二进制协议，只有 12 字节头部。

#### TrimSrvRequestPacket

**构造: Caller::BuildTrimSrvPacket @ 0x12d20**

对象布局 (含控制块 0x48 字节):

| 偏移 | 大小 | 类型 | 说明 |
|------|------|------|------|
| 0 | 8 | vtable* | 虚表指针 |
| 8 | 32 | std::string | data (方法名字符串) |
| 40 | 8 | uint64 | session_id |
| 48 | 4 | int32 | data_length |
| 52 | 4 | padding | |
| 56 | 8 | reserved | 0 |
| 64 | 4 | int32 | command_id |

序列化: 直接返回 data 字符串内容。

#### TrimSrvResponsePacket

**反序列化头部: 0x21320**

头部格式 (12 字节):
```
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|         packet_size (uint32, 4B)              |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|         session_id (uint64, 8B)               |
|                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- `data_length = packet_size - 12`
- 数据部分紧跟在头部之后

#### TrimSrvCommand::GetCommandId @ 0x1fef0
```
从全局 CommandMap 哈希表中查找方法名对应的命令 ID:
- 小哈希表 (<=20 元素): 线性遍历链表
- 大哈希表 (>20 元素): 布谷哈希查找 (hash_seed = 0xC70F6907)
- 返回 int32 CommandId，未找到返回 0
```

已知的 TrimSrvCommand 子类:
- `TokenQueryCommand` — 令牌查询
- `NotificationCommand` — 通知
- `GetStorageStateCommand` — 获取存储状态
- `GetStorageAndCacheStateCommand` — 获取存储和缓存状态

---

### 2.6 Request/Response 类层次

#### Request 工厂模式

**Request::Request @ 0x21680**
```
根据 ServiceType 创建不同的请求子类:
- type == 1: TrimSrvRequest (0xD8 = 216 字节)
- 其他:      AppCgiRequest (0xE8 = 232 字节)
```

**TrimSrvRequest::Build @ 0x23510**
```
1. 检查 error_code (offset 112)
2. 调用 TrimSrvCommand 虚函数:
   - CheckParams(vtable+16): 验证参数
   - HandleResponse(vtable+24): 构建请求数据
3. 失败设置错误码 201
```

**AppCgiRequest::Build @ 0x21c70**
```
构建 AppCgi 格式的请求数据 (JSON 序列化)
```

#### Response 工厂模式

**Response::Create @ 0x23b80**
```
根据 ServiceType 创建不同的响应子类:
- type == 0: AppCgiResponse  (0xE0 = 224 字节, vtable off_309B0)
- type == 1: TrimSrvResponse (0xB0 = 176 字节, vtable off_309E8)
- 其他:      RawResponse     (0x90 = 144 字节, vtable off_30A20)
所有返回 shared_ptr<Response>
```

#### AppCgiResponse

**error_no @ 0x24c20**
```
1. 检查 valid 标志 (offset 200)
2. 从 PPJson::MutValue["errno"] 读取错误码
3. 如果 JSON 中不存在 errno 字段，返回 0
```

关键虚方法: `valid()`, `error_no()`, `request_id()`, `result()`, `data()`, `values()`, `value(key)`, `SetResultDataKey()`

#### TrimSrvResponse

**error_no @ 0x252f0**
```
1. 尝试从 PPJson::MutValue["errno"] 读取
2. 如果 JSON 中无此字段，返回 offset 44 处的默认值
```

#### RawResponse
最简单的响应类型，存储原始字符串 + PPJson::MutDocument + MutValue。

---

### 2.7 PacketProcessor (数据流处理器)

两种 PacketProcessor 分别处理两种协议的数据流解析:

#### RpcPacketProcessor::AppendAndParse @ 0x1cec0
```
处理 RpcPacket 协议:
1. 将接收数据追加到内部缓冲区
2. 检查是否有完整的 30 字节头部
3. 解析头部获取各段长度
4. 当所有数据段完整时:
   - DeserializeHeader → DeserializeData
   - 返回完整的解析结果
```

#### TrimSrvPacketProcessor::AppendAndParse @ 0x1d4b0
```
处理 TrimSrv 协议:
1. 使用 stringstream 缓冲接收数据
2. 当可用数据 > 12 字节:
   - 读取 12 字节头部
   - DeserializeHeader: 获取 packet_size 和 session_id
   - data_length = packet_size - 12
3. 读取 data_length 字节的数据
4. DeserializeData
5. 验证完整性: 实际读取 == 预期大小
6. 成功后重置 stringstream 状态
```

---

## 3. 网络引擎接口 (ne*)

trimrpc 通过外部 `ne*` 函数族与底层网络引擎交互:

| 函数 | 说明 |
|------|------|
| `neCreateInstance(max_conn, thread_count)` | 创建网络引擎实例 |
| `neCreateSocket(instance, type=1, flags=0)` | 创建套接字 |
| `neConnect(instance, socket, uds_path, processor)` | 发起 UDS 连接 |
| `neSend(instance, socket, data, length, processor)` | 发送数据 |
| `neClose(instance, socket)` | 关闭套接字 |
| `neSetCallback(instance, event_type, callback)` | 注册事件回调 |

回调事件类型:
- 1: OnConnected — 连接成功
- 2: OnReceived — 接收数据
- 3: OnDisconnected — 连接断开
- 4: OnConnectError — 连接失败
- 5: OnSendError — 发送失败

---

## 4. 错误码表

| 错误码 | 含义 | 产生位置 |
|--------|------|----------|
| 0 | 成功 | 各处 |
| 1 | HandleServiceInfo 服务数量不匹配 | HandleServiceInfo |
| 3 | Socket 创建失败 | RpcConnections::Connect |
| 4 | 连接失败 | RpcConnections::Connect |
| 5 | 服务名超过 128 字节 | VerifyServiceId |
| 6 | 服务名不以 "com." 开头 | VerifyServiceId |
| 8 | TrimSrv 通信错误 (映射为 202) | Caller::Call |
| 100 | 同步等待超时 | CallOutInSync / CallOutWithFuture |
| 101 | Future 等待超时 | CallOutWithFuture |
| 102 | 发送完全失败 | RpcConnections::Send |
| 200 | HandleServiceInfo JSON 解析失败 | HandleServiceInfo |
| 201 | TrimSrvRequest::Build 失败 | TrimSrvRequest::Build |
| 202 | TrimSrv 通信错误 (error 8 映射) | Caller::Call |

---

## 5. 完整调用流程

### 5.1 标准 RPC 调用流程 (AppCgi)

```
应用程序
  │
  ├─ RpcClient::ApplyPermission(["com.trim.service1"])
  │   ├─ 构建 JSON: {data:{req:"com.trim.rpcbroker.apply", services:[...]}}
  │   ├─ RpcClient::Call("com.trim.rpcbroker", "apply", json)
  │   │   └─ [完整 Call 流程，见下方]
  │   └─ HandleServiceInfo: 解析响应，注册服务 UDS 路径
  │
  ├─ Request req("com.trim.service1", "doSomething")
  │   └─ 工厂模式: GetServiceType → 创建 AppCgiRequest/TrimSrvRequest
  │
  ├─ RpcClient::Call(req)
  │   ├─ req.Build() → 序列化请求数据
  │   ├─ req.method() / req.service()
  │   │
  │   └─ RpcClient::Call("com.trim.service1", "doSomething", data)
  │       ├─ VerifyServiceId("com.trim.service1") ✓
  │       │
  │       ├─ Caller::Call("com.trim.service1", "doSomething", data)
  │       │   ├─ RpcConnections::Connect("com.trim.service1")
  │       │   │   ├─ neCreateSocket(inst, 1, 0)
  │       │   │   ├─ GetServiceUDS → 获取 UDS 路径
  │       │   │   ├─ GetServiceType → type=0 → RpcPacketProcessor
  │       │   │   ├─ neConnect(inst, sock, uds, processor)
  │       │   │   └─ nanosleep 轮询等待 CONNECTED
  │       │   │
  │       │   ├─ BuildRpcPacket:
  │       │   │   ├─ 构建 JSON {data:{req, pid, reqid}}
  │       │   │   ├─ 设置 RpcPacket magic="CPRT", version=1
  │       │   │   └─ 原子递增 session_id
  │       │   │
  │       │   ├─ CallOutInSync:
  │       │   │   ├─ 绑定 NotifyResult 回调
  │       │   │   ├─ RpcConnections::Send(handle, packet, callback)
  │       │   │   │   ├─ 回调存入 RB-tree
  │       │   │   │   ├─ RpcPacket::Serialize (30B头+数据)
  │       │   │   │   └─ neSend → 失败重试一次
  │       │   │   └─ pthread_cond_clockwait(10秒超时)
  │       │   │
  │       │   │   ... 等待远端响应 ...
  │       │   │
  │       │   │   [ne* 接收线程]:
  │       │   │   ├─ OnReceived 回调触发
  │       │   │   ├─ RpcPacketProcessor::AppendAndParse
  │       │   │   ├─ 查找 session 回调
  │       │   │   └─ Caller::NotifyResult:
  │       │   │       ├─ 复制响应数据
  │       │   │       ├─ 设置 error_code
  │       │   │       └─ 信号 condition_variable
  │       │   │
  │       │   └─ CancelByPacket — 清理回调
  │       │
  │       ├─ GetServiceType → Response::Create(type, response_data)
  │       └─ 返回 error_code / response.error_no()
  │
  └─ RpcClient::response() → 获取 shared_ptr<Response>
```

### 5.2 TrimSrv 调用差异

TrimSrv 协议与 RpcPacket 协议的主要差异:

| 特性 | RpcPacket (AppCgi) | TrimSrv |
|------|-------------------|---------|
| 头部大小 | 30 字节 | 12 字节 |
| 头部魔数 | "CPRT" (0x54525043) | 无 |
| 头部字段 | magic+version+session+4个长度 | packet_size+session_id |
| 数据格式 | 4 段(data/token/extra_data/extra_data2) | 单一数据段 |
| 请求构建 | JSON {data:{req, pid, reqid}} | 通过 TrimSrvCommand 虚函数 |
| 命令路由 | 按 "service.method" 字符串 | 按 CommandId (哈希表查找) |
| 序列化 | 完整二进制协议 | 简单字符串传输 |
| 响应类型 | AppCgiResponse (0xE0B) | TrimSrvResponse (0xB0B) |
| 错误映射 | 直接使用 | error 8 → 202 |

---

## 6. 依赖库

| 依赖 | 用途 |
|------|------|
| **PPJson** | JSON 序列化/反序列化 (MutDocument, MutValue, Document, Value) |
| **ne* 网络引擎** | UDS 连接管理 (neCreateInstance, neCreateSocket, neConnect, neSend) |
| **base64** | base64::DecodeInto 数据解码 |
| **libpthread** | 线程同步 (mutex, condition_variable, pthread_cond_clockwait) |
| **libstdc++** | STL 容器、字符串、智能指针 |

---

## 7. 安全观察

1. **服务名验证**: 仅检查前缀 "com." 和长度 <= 128，无进一步的权限控制
2. **单次重试**: Send 失败后只重试一次，无指数退避
3. **明文 UDS**: 通信通过 Unix Domain Socket 明文传输
4. **超时固定**: 默认 10 秒超时，通过 SetTimeOut 可调
5. **会话 ID 可预测**: 简单原子递增计数器
6. **token 字段**: RpcPacket 支持 token 字段，用于认证令牌传递

---

## 8. 已添加 IDA 注释的函数列表

| 地址 | 函数名 | 说明 |
|------|--------|------|
| 0x259b0 | RpcClient::RpcClient | 构造函数 |
| 0x261e0 | RpcClient::Call(Request) | 高层调用入口 |
| 0x25bf0 | RpcClient::Call(s,m,d) | 核心调用流程 |
| 0x25980 | RpcClient::response | 获取响应 |
| 0x28040 | RpcClient::ApplyPermission(str) | 单服务权限 |
| 0x27740 | RpcClient::ApplyPermission(vec) | 多服务权限 |
| 0x256c0 | RpcClient::VerifyServiceId | 服务名验证 |
| 0x26330 | RpcClient::HandleServiceInfo | 服务信息解析 |
| 0x12b60 | Caller::Caller | 构造函数 |
| 0x15480 | Caller::Call | 内部调用总流程 |
| 0x14840 | Caller::BuildRpcPacket | RpcPacket 构建 |
| 0x12d20 | Caller::BuildTrimSrvPacket | TrimSrv 包构建 |
| 0x13cb0 | Caller::CallOutInSync | 同步等待 |
| 0x12860 | Caller::NotifyResult | 异步回调 |
| 0x14600 | Caller::CallOutWithFuture | Future 异步调用 |
| 0x196b0 | RpcConnections::Init | 网络初始化 |
| 0x19520 | RpcConnections::InitServiceMap | 服务注册 |
| 0x1a920 | RpcConnections::Connect | 建立连接 |
| 0x192d0 | RpcConnections::Send | 发送数据包 |
| 0x17490 | RpcConnections::OnReceived | 接收处理 |
| 0x1a0e0 | RpcConnections::AddService | 添加服务 |
| 0x1a730 | RpcConnections::GetServiceType | 查询服务类型 |
| 0x17440 | RpcConnections::SendPacket | 底层发送 |
| 0x1ddf0 | RpcPacket::RpcPacket | 包构造 |
| 0x1dc30 | RpcPacket::Serialize | 序列化 |
| 0x1dea0 | RpcPacket::DeserializeHeader | 头部反序列化 |
| 0x1dee0 | RpcPacket::DeserializeData | 数据反序列化 |
| 0x23b80 | Response::Create | 响应工厂 |
| 0x21680 | Request::Request | 请求工厂 |
| 0x1fef0 | TrimSrvCommand::GetCommandId | 命令 ID 查找 |
| 0x23510 | TrimSrvRequest::Build | TrimSrv 请求构建 |
| 0x21320 | TrimSrvResponsePacket::DeserializeHeader | TrimSrv 头部解析 |
| 0x163e0 | TrimSrvRequestPacket::Serialize | TrimSrv 序列化 |
| 0x1d4b0 | TrimSrvPacketProcessor::AppendAndParse | TrimSrv 流解析 |
| 0x1cec0 | RpcPacketProcessor::AppendAndParse | RpcPacket 流解析 |
| 0x24c20 | AppCgiResponse::error_no | AppCgi 错误码 |
| 0x252f0 | TrimSrvResponse::error_no | TrimSrv 错误码 |
| 0x21300 | TrimSrvRequestPacket::SetSession | 设置会话 ID |
| 0x15df0 | TrimSrvRequestPacket::session | 获取会话 ID |
