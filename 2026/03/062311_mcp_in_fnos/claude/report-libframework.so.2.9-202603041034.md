# libframework.so.2.9 trimrpc 分析报告

**分析日期**: 2026-03-04
**二进制文件**: libframework.so.2.9
**架构**: x86_64
**分析工具**: IDA Pro 9.0 + ida-pro-mcp

---

## 1. 概述

`libframework.so.2.9` 是 fnOS 应用框架库，提供了与 trimrpc 系统集成的功能。该库实现了自己的 `framework::RpcPacket` 类（独立于 `libtrimrpc.so` 中的 `trimrpc::RpcPacket`），并通过 `ServiceHelper` 类与 RPC Broker 进行通信。

### 1.1 核心组件

- **framework::RpcPacket**: RPC 二进制协议实现（与 trimrpc 相同的 30 字节头格式）
- **framework::ServiceHelper**: RPC Broker 通信接口（服务注册/注销）
- **framework::Request**: RPC 请求封装
- **framework::Response**: RPC 响应封装
- **framework::Application**: 应用程序基类，管理 RPC 服务生命周期

---

## 2. framework::RpcPacket 实现

### 2.1 类结构

```cpp
class framework::RpcPacket {
    uint32_t magic;        // 0x54525043 ("CPRT")
    uint32_t version;      // 1
    std::string data[4];   // 4 个数据段（SSO 优化）
};
```

### 2.2 二进制协议格式

与 `libtrimrpc.so` 中的 RpcPacket 完全相同：

```
+----------------+----------------+----------------+----------------+
| Magic (4 bytes)| Version (4 B) | Len1 (4 bytes) | Len2 (4 bytes) |
| 0x54525043     | 0x00000001    |                |                |
+----------------+----------------+----------------+----------------+
| Len3 (4 bytes) | Len4 (4 bytes) | Reserved (6 bytes)              |
+----------------+----------------+---------------------------------+
| Data Section 1 (variable length, Len1 bytes)                     |
+------------------------------------------------------------------+
| Data Section 2 (variable length, Len2 bytes)                     |
+------------------------------------------------------------------+
| Data Section 3 (variable length, Len3 bytes)                     |
+------------------------------------------------------------------+
| Data Section 4 (variable length, Len4 bytes)                     |
+------------------------------------------------------------------+
```

**头部结构** (30 字节):
- `magic`: 0x54525043 ("CPRT" 小端序)
- `version`: 0x00000001
- `section_length[4]`: 4 个数据段的长度
- `reserved[6]`: 保留字节

### 2.3 关键函数

#### RpcPacket::RpcPacket() @ 0x4f390
```cpp
// 构造函数：初始化魔数、版本号和 4 个 SSO 字符串
RpcPacket::RpcPacket() {
    this->magic = 0x54525043;    // "CPRT"
    this->version = 1;
    // 初始化 4 个 std::string (SSO 优化)
    for (int i = 0; i < 4; i++) {
        this->data[i] = "";
    }
}
```

#### RpcPacket::Serialize() @ 0x4e020
```cpp
// 序列化为二进制格式
std::string RpcPacket::Serialize() {
    std::string result;

    // 写入 30 字节头部
    result.append((char*)&magic, 4);
    result.append((char*)&version, 4);

    uint32_t lengths[4];
    for (int i = 0; i < 4; i++) {
        lengths[i] = data[i].size();
    }
    result.append((char*)lengths, 16);

    char reserved[6] = {0};
    result.append(reserved, 6);

    // 写入 4 个数据段
    for (int i = 0; i < 4; i++) {
        result.append(data[i]);
    }

    return result;
}
```

#### RpcPacket::DeserializeHeader() @ 0x4f310
```cpp
// 解析 30 字节头部
bool RpcPacket::DeserializeHeader(const char* buffer, size_t size) {
    if (size < 30) return false;

    memcpy(&magic, buffer, 4);
    memcpy(&version, buffer + 4, 4);

    if (magic != 0x54525043) return false;

    uint32_t lengths[4];
    memcpy(lengths, buffer + 8, 16);

    // 验证长度合法性
    for (int i = 0; i < 4; i++) {
        if (lengths[i] > MAX_SECTION_SIZE) return false;
    }

    return true;
}
```

#### RpcPacket::DeserializeData() @ 0x4e1e0
```cpp
// 解析 4 个数据段
bool RpcPacket::DeserializeData(const char* buffer, size_t size) {
    size_t offset = 30;  // 跳过头部

    for (int i = 0; i < 4; i++) {
        uint32_t len = section_lengths[i];
        if (offset + len > size) return false;

        data[i].assign(buffer + offset, len);
        offset += len;
    }

    return true;
}
```

---

## 3. framework::ServiceHelper 实现

`ServiceHelper` 负责与 RPC Broker (`com.trim.rpcbroker`) 通信，处理服务注册和注销。

### 3.1 通信架构

```
Application
    |
    v
ServiceHelper::Register/Unregister
    |
    v
ServiceHelper::CallBroker
    |
    v
Unix Domain Socket: /var/run/rpc/rpcbroker
    |
    v
RPC Broker Service
```

### 3.2 关键函数

#### ServiceHelper::Register() @ 0x5e2a0
```cpp
// 向 RPC Broker 注册服务
bool ServiceHelper::Register(
    const std::string& service_id,
    const std::string& service_name,
    const std::string& uds_path,
    const std::string& ip_address,
    int service_type,
    const std::string& revision
) {
    // 构建 JSON 请求
    PPJson::MutDocument doc;
    PPJson::MutValue& data = doc["data"];

    data["req"] = "com.trim.rpcbroker.register";
    data["id"] = service_id;
    data["name"] = service_name;
    data["uds"] = uds_path;
    data["ip"] = ip_address;
    data["type"] = service_type;
    data["revision"] = revision;

    // 序列化为 JSON 字符串
    std::string json_str = doc.write();

    // 创建 RpcPacket
    framework::RpcPacket packet;
    packet.data[0] = json_str;  // 将 JSON 放入第一个数据段

    // 调用 CallBroker 发送请求
    return CallBroker(packet);
}
```

**JSON 请求格式**:
```json
{
    "data": {
        "req": "com.trim.rpcbroker.register",
        "id": "service_unique_id",
        "name": "com.trim.service_name",
        "uds": "/var/run/rpc/service_name",
        "ip": "127.0.0.1",
        "type": 1,
        "revision": "1.0.0"
    }
}
```

#### ServiceHelper::CallBroker() @ 0x5e1b0
```cpp
// 通过 Unix Domain Socket 与 RPC Broker 通信
bool ServiceHelper::CallBroker(const RpcPacket& packet) {
    // 1. 创建 socket
    int sockfd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sockfd < 0) return false;

    // 2. 连接到 RPC Broker
    struct sockaddr_un addr;
    addr.sun_family = AF_UNIX;
    strcpy(addr.sun_path, "/var/run/rpc/rpcbroker");

    if (connect(sockfd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        close(sockfd);
        return false;
    }

    // 3. 序列化并发送 RpcPacket
    std::string serialized = packet.Serialize();
    ssize_t sent = send(sockfd, serialized.data(), serialized.size(), 0);

    if (sent != serialized.size()) {
        close(sockfd);
        return false;
    }

    // 4. 接收响应
    char response_buffer[4096];
    ssize_t received = recv(sockfd, response_buffer, sizeof(response_buffer), 0);

    close(sockfd);

    if (received <= 0) return false;

    // 5. 解析响应 RpcPacket
    RpcPacket response;
    if (!response.DeserializeHeader(response_buffer, received)) {
        return false;
    }
    if (!response.DeserializeData(response_buffer, received)) {
        return false;
    }

    // 6. 检查响应状态
    // (通常第一个数据段包含 JSON 响应)
    return true;
}
```

**通信流程**:
1. 创建 Unix Domain Socket
2. 连接到 `/var/run/rpc/rpcbroker`
3. 发送序列化的 RpcPacket
4. 等待并接收响应 RpcPacket
5. 解析响应并返回结果

#### ServiceHelper::Unregister() @ 0x5e8e0
```cpp
// 从 RPC Broker 注销服务
bool ServiceHelper::Unregister(const std::string& service_id) {
    // 构建 JSON 请求
    PPJson::MutDocument doc;
    PPJson::MutValue& data = doc["data"];

    data["req"] = "com.trim.rpcbroker.unregister";
    data["id"] = service_id;

    std::string json_str = doc.write();

    // 创建 RpcPacket
    framework::RpcPacket packet;
    packet.data[0] = json_str;

    // 调用 CallBroker 发送请求
    return CallBroker(packet);
}
```

**JSON 请求格式**:
```json
{
    "data": {
        "req": "com.trim.rpcbroker.unregister",
        "id": "service_unique_id"
    }
}
```

---

## 4. framework::Application RPC 集成

### 4.1 服务注册流程

#### Application::RegisterRpcService() @ 0x26480
```cpp
// 在独立线程中注册 RPC 服务
void Application::RegisterRpcService() {
    // 创建 detached 线程执行注册
    std::thread([this]() {
        ServiceHelper helper;

        std::string service_id = GenerateServiceId();
        std::string service_name = "com.trim." + this->app_name_;
        std::string uds_path = "/var/run/rpc/" + this->app_name_;
        std::string ip = "127.0.0.1";
        int type = 1;  // 服务类型
        std::string revision = this->app_version_;

        bool success = helper.Register(
            service_id,
            service_name,
            uds_path,
            ip,
            type,
            revision
        );

        if (success) {
            SPDLOG_INFO("RPC service registered: {}", service_name);
        } else {
            SPDLOG_ERROR("Failed to register RPC service: {}", service_name);
        }
    }).detach();
}
```

#### Application::UnregisterRpcService() @ 0x26670
```cpp
// 注销 RPC 服务
void Application::UnregisterRpcService() {
    ServiceHelper helper;

    std::string service_name = "com.trim." + this->app_name_;
    std::string service_id = this->service_id_;

    bool success = helper.Unregister(service_id);

    if (success) {
        SPDLOG_INFO("RPC service unregistered: {}", service_name);
    } else {
        SPDLOG_ERROR("Failed to unregister RPC service: {}", service_name);
    }
}
```

---

## 5. framework::Request 和 Response

### 5.1 Request 类

`framework::Request` 封装了 RPC 请求信息。

**关键字段**:
- `reqid`: 请求 ID (uint64_t)
- `method`: 请求方法名 (std::string)
- `session`: 会话标识 (std::string)
- `packet`: 底层数据包指针 (shared_ptr<BasePacket>)
- `start_time`: 请求开始时间戳

### 5.2 Response 类

#### Response::Response(Request*) @ 0x59bd0
```cpp
// 从 Request 构造 Response
Response::Response(const Request* req) {
    // 复制请求信息
    this->reqid_ = req->reqid_;
    this->method_ = req->method_;
    this->session_ = req->session_;
    this->packet_ = req->packet_;
    this->start_time_ = req->start_time_;

    // 初始化 JSON 文档
    this->json_doc_ = PPJson::MutDocument();
    this->json_root_ = PPJson::MutValue(json_doc_);

    // 初始化状态
    this->status_ = STATUS_UNKNOWN;
    this->need_stable_delivery_ = false;
}
```

#### Response::SetFailed() @ 0x5a5d0
```cpp
// 设置响应失败状态
void Response::SetFailed(int errno_code) {
    this->status_ = STATUS_FAILED;  // 3

    // 设置 JSON 字段
    json_root_["data"]["errno"] = errno_code;
}
```

#### Response::Send() @ 0x59e20
```cpp
// 发送响应
void Response::Send() {
    if (!this->packet_) {
        SPDLOG_ERROR("Empty reqid");
        return;
    }

    // 1. 确保状态已设置
    if (this->status_ == STATUS_UNKNOWN) {
        this->status_ = STATUS_SUCCESS;  // 默认成功
    }

    // 2. 填充响应 JSON
    json_root_["data"]["reqid"] = this->reqid_;
    json_root_["data"]["result"] = GetStatusString(this->status_);
    json_root_["data"]["result_rev"] = GetResultRevision();
    json_root_["data"]["session"] = this->session_;

    // 3. 序列化 JSON
    std::string json_str = json_doc_.write(json_root_, 0x22, false);

    // 4. 写入底层 packet
    if (json_str.size() > 0) {
        this->packet_->WriteData(json_str.c_str(), json_str.size());
    }

    // 5. 设置时间戳
    this->packet_->send_time_ = std::chrono::system_clock::now();
    this->packet_->recv_time_ = std::chrono::system_clock::now() / 1000000;

    // 6. 添加到发送队列
    SendingController::instance()->AddTask(this->packet_);

    // 7. 清理 JSON 数据
    json_root_.clear();

    // 8. 性能监控：记录慢请求
    if (this->start_time_ > 0) {
        uint64_t elapsed_ms = (std::chrono::steady_clock::now() - this->start_time_) / 1000000;

        if (elapsed_ms > s_time_consuming_limit_ms_) {
            SPDLOG_WARN(
                "[PERF]Slow request, req id:{}, method:{}, cost:{}",
                this->reqid_,
                this->method_,
                elapsed_ms
            );
        }
    }
}
```

**响应 JSON 格式**:
```json
{
    "data": {
        "reqid": 123456789,
        "result": "success",
        "result_rev": "1.0",
        "session": "session_token_here",
        "errno": 0
    }
}
```

---

## 6. 关键发现

### 6.1 双重 RpcPacket 实现

fnOS 系统中存在两个独立的 RpcPacket 实现：

1. **trimrpc::RpcPacket** (libtrimrpc.so)
   - 完整的 RPC 框架实现
   - 支持 AppCgi 和 TrimSrv 双协议
   - 包含网络引擎 (ne*) 集成

2. **framework::RpcPacket** (libframework.so)
   - 轻量级实现
   - 仅用于与 RPC Broker 通信
   - 直接使用 socket API，不依赖网络引擎

**两者共同点**:
- 相同的二进制协议格式（30 字节头 + 4 个数据段）
- 相同的魔数 0x54525043 ("CPRT")
- 相同的版本号 1

### 6.2 服务注册机制

应用程序通过以下流程注册到 RPC 系统：

```
Application 启动
    |
    v
RegisterRpcService() (独立线程)
    |
    v
ServiceHelper::Register()
    |
    v
构建 JSON 请求 {"req":"com.trim.rpcbroker.register", ...}
    |
    v
封装到 RpcPacket
    |
    v
CallBroker() 通过 UDS 发送到 /var/run/rpc/rpcbroker
    |
    v
RPC Broker 处理注册请求
    |
    v
返回响应 RpcPacket
```

### 6.3 通信路径

- **服务注册/注销**: Application → ServiceHelper → UDS → RPC Broker
- **RPC 请求处理**: Client → RPC Broker → UDS → Application → Request Handler → Response → SendingController

### 6.4 性能监控

框架内置性能监控功能：
- 记录每个请求的开始时间 (`start_time_`)
- 在 `Response::Send()` 中计算耗时
- 超过阈值 (`s_time_consuming_limit_ms_`) 的请求会被记录为慢请求
- 使用 SPDLOG 输出 `[PERF]Slow request` 日志

---

## 7. 函数地址索引

### 7.1 RpcPacket 相关
| 函数名 | 地址 | 说明 |
|--------|------|------|
| RpcPacket::RpcPacket() | 0x4f390 | 构造函数 |
| RpcPacket::Serialize() | 0x4e020 | 序列化为二进制 |
| RpcPacket::DeserializeHeader() | 0x4f310 | 解析头部 |
| RpcPacket::DeserializeData() | 0x4e1e0 | 解析数据段 |

### 7.2 ServiceHelper 相关
| 函数名 | 地址 | 说明 |
|--------|------|------|
| ServiceHelper::Register() | 0x5e2a0 | 注册服务 |
| ServiceHelper::CallBroker() | 0x5e1b0 | 调用 Broker |
| ServiceHelper::Unregister() | 0x5e8e0 | 注销服务 |
| ServiceHelper::WaitResponse() | 0x5da80 | 等待响应 |

### 7.3 Application 相关
| 函数名 | 地址 | 说明 |
|--------|------|------|
| Application::RegisterRpcService() | 0x26480 | 注册 RPC 服务 |
| Application::UnregisterRpcService() | 0x26670 | 注销 RPC 服务 |
| Application::RegisterHandler() | 0x2b5d0 | 注册请求处理器 |
| Application::ProcessRequest() | 0x2f060 | 处理请求 |
| Application::OnReceivedRequest() | 0x30240 | 接收请求回调 |

### 7.4 Request/Response 相关
| 函数名 | 地址 | 说明 |
|--------|------|------|
| Request::Parse() | 0x1e130 | 解析请求 |
| Request::CheckUserToken() | 0x1ea70 | 检查用户令牌 |
| Response::Response(Request*) | 0x59bd0 | 构造响应 |
| Response::Send() | 0x59e20 | 发送响应 |
| Response::SetFailed() | 0x5a5d0 | 设置失败状态 |
| Response::NeedStableDelivery() | 0x56660 | 检查是否需要稳定传输 |

---

## 8. 总结

`libframework.so.2.9` 提供了应用程序与 fnOS RPC 系统集成的完整框架：

1. **独立的 RpcPacket 实现**: 与 libtrimrpc.so 使用相同协议，但实现独立
2. **ServiceHelper 桥接**: 通过 Unix Domain Socket 与 RPC Broker 通信
3. **服务生命周期管理**: 自动注册/注销 RPC 服务
4. **请求/响应封装**: 简化 RPC 请求处理流程
5. **性能监控**: 内置慢请求检测和日志记录

该库是 fnOS 应用程序开发的核心基础设施，所有需要提供 RPC 服务的应用都依赖此库。

---

**分析完成时间**: 2026-03-04 10:34
**IDA 注释**: 已添加到所有关键函数
**变量重命名**: 已完成关键变量的语义化命名
