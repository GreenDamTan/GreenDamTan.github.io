# Token 验证机制研究报告

> **研究日期**: 2026-03-04
> **研究对象**: trim_http_cgi 的 token 验证机制
> **相关服务**: trim_http_cgi, com.trim.main, user.hdl

---

## 1. trim_http_cgi 的 Token 验证流程

### 1.1 完整流程

```
HTTP 请求
    │
    ▼
┌─────────────────────────────────────┐
│ getToken(request)                   │
│ ├─ Cookie: "fnos-token"             │
│ ├─ Header: "Authorization"          │
│ │   - "Bearer <token>"              │
│ │   - "Token <token>"               │
│ │   - "Basic <token>"               │
│ └─ Query: "token"                   │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ checkToken(token)                   │
│                                     │
│ 1. trimrpc.NewClient()              │
│ 2. ApplyPermission("com.trim.main") │
│ 3. Call("tokenQuery", token)       │
│                                     │
│    请求格式:                         │
│    {                                │
│      "service": "com.trim.main",    │
│      "method": "tokenQuery",        │
│      "params": {                    │
│        "token": "<token>"           │
│      }                              │
│    }                                │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ com.trim.main.tokenQuery            │
│                                     │
│ 响应格式:                            │
│ {                                   │
│   "result": "succ"  // 成功         │
│ }                                   │
│ 或                                  │
│ {                                   │
│   "errno": <错误码>                 │
│ }                                   │
└─────────────────────────────────────┘
```

### 1.2 关键代码位置

| 函数 | 地址 | 功能 |
|------|------|------|
| `main.getToken` | `0x6744a0` | 从 HTTP 请求提取 token |
| `main.checkToken` | `0x6742a0` | 通过 trimrpc 验证 token |
| `main.TrimCgiRoute.ServeHTTP` | `0x673440` | 主路由处理（调用上述函数）|

---

## 2. Token 验证的实现方式

### 方式1: 完全复现 trim_http_cgi (trimrpc 协议)

**优点**: 与 trim_http_cgi 完全一致
**缺点**: 需要实现完整的 trimrpc 协议

```python
# 使用 trimrpc 协议
import socket
import struct
import json

def validate_token_via_trimrpc(token):
    # 1. 构建 RpcPacket (30 字节头部 + JSON data)
    packet = build_rpc_packet(
        service="com.trim.main",
        method="tokenQuery",
        data={"token": token}
    )

    # 2. 连接 UDS
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect("/run/trim_srv.socket")  # 或其他路径

    # 3. 发送并接收
    sock.sendall(packet)
    response = sock.recv(8192)

    # 4. 解析响应
    result = parse_rpc_response(response)

    # 5. 检查结果
    return result.get("data", {}).get("result") == "succ"
```

**实现**: 见 `validate-token.py` 的 `validate_via_trimrpc()` 方法

---

### 方式2: 直接调用 user.hdl 方法 (原始 JSON)

**优点**: 简单直接，不需要 trimrpc 协议
**缺点**: 绕过了 com.trim.main 的封装

```python
# 使用原始 JSON (通过 /run/trim_srv.socket)
import socket
import json

def validate_token_via_user_hdl(token):
    # 1. 构建 JSON 请求
    request = {
        "req": "user.query_uid_by_token",
        "token": token
    }

    # 2. 连接 UDS
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect("/run/trim_srv.socket")

    # 3. 发送 JSON
    sock.sendall(json.dumps(request).encode('utf-8'))

    # 4. 接收响应
    response = sock.recv(8192)
    result = json.loads(response.decode('utf-8'))

    # 5. 检查结果
    return result.get("errno") == 0 and "uid" in result
```

**实现**: 见 `validate-token.py` 的 `validate_via_user_query()` 方法

---

### 方式3: 使用 user.authToken (验证并创建 session)

**优点**: 不仅验证 token，还创建 session
**缺点**: 会创建新的 session，可能影响系统状态

```python
def validate_and_create_session(token):
    request = {
        "req": "user.authToken",
        "token": token
    }

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect("/run/trim_srv.socket")
    sock.sendall(json.dumps(request).encode('utf-8'))

    response = sock.recv(8192)
    result = json.loads(response.decode('utf-8'))

    # 返回 session_id 和 uid
    if result.get("errno") == 0 and result.get("result") == "succ":
        return True, result.get("session_id"), result.get("uid")
    return False, None, None
```

**实现**: 见 `validate-token.py` 的 `validate_via_auth_token()` 方法

---

### 方式4: 使用 trimrpc Python 客户端

**优点**: 使用现成的 trimrpc 实现
**缺点**: 需要先发现服务

```bash
# 1. 发现 com.trim.main 服务
python3 report-trimrpc-202603041034.py --discover com.trim.main

# 输出示例:
# com.trim.main:
#   name: TRIM Service
#   uds: /run/trim_srv.socket
#   type: 1 (TrimSrv)

# 2. 调用 tokenQuery
python3 report-trimrpc-202603041034.py \
    --uds /run/trim_srv.socket \
    '{"service":"com.trim.main","method":"tokenQuery","params":{"token":"<token>"}}'
```

---

## 3. user.hdl 提供的 Token 相关方法

根据 `report-user.hdl-202603041730.md` 的分析，user.hdl 提供以下 token 相关方法：

| 方法 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `query_uid_by_token` | 通过 token 查询 uid | `{"token": "<token>"}` | `{"errno": 0, "uid": <uid>}` |
| `query_token_by_session` | 通过 session 查询 token | `{"session": <session_id>}` | `{"errno": 0, "token": "<token>"}` |
| `auth_token_and_add_session` | 验证 token 并创建 session | `{"token": "<token>"}` | `{"errno": 0, "session_id": <id>, "uid": <uid>}` |
| `authToken` | 验证 token (别名) | `{"token": "<token>"}` | `{"errno": 0, "result": "succ"}` |

### 调用方式

```bash
# 通过 /run/trim_srv.socket (com.trim.main 加载的 handler)
python3 report-trimrpc-202603041034.py \
    --raw --uds /run/trim_srv.socket \
    '{"req":"user.query_uid_by_token","token":"<token>"}'
```

---

## 4. com.trim.main 服务分析

### 4.1 服务信息

| 属性 | 值 |
|------|---|
| **服务名** | `com.trim.main` |
| **UDS 路径** | `/run/trim_srv.socket` |
| **服务类型** | TrimSrv (type=1) |
| **显示名称** | "TRIM Service" |

### 4.2 tokenQuery 方法

`com.trim.main.tokenQuery` 是 trim_http_cgi 用于验证 token 的方法。

**可能的实现**:
1. `com.trim.main` 加载了 user.hdl 或类似的 handler
2. `tokenQuery` 方法内部调用 `user.query_uid_by_token`
3. 如果查询成功（返回 uid），则返回 `{"result": "succ"}`

**验证方式**:
```bash
# 方法1: 使用 trimrpc 协议
python3 validate-token.py --method trimrpc <token>

# 方法2: 直接调用 user.hdl
python3 validate-token.py --method query <token>

# 方法3: 使用 authToken
python3 validate-token.py --method auth <token>

# 测试所有方法
python3 validate-token.py <token>
```

---

## 5. 实际使用示例

### 5.1 获取 Token

Token 通常在用户登录后生成。可以通过以下方式获取：

```bash
# 方法1: 调用 user.login
python3 report-trimrpc-202603041034.py \
    --raw --uds /run/trim_srv.socket \
    '{"req":"user.login","username":"admin","password":"<password>"}'

# 响应:
# {
#   "errno": 0,
#   "result": "succ",
#   "token": "<token>",
#   "session_id": <session_id>
# }

# 方法2: 从浏览器 Cookie 中提取
# Cookie: fnos-token=<token>
```

### 5.2 验证 Token

```bash
# 使用 validate-token.py
python3 validate-token.py <token>

# 输出示例:
# ============================================================
# Token 验证工具
# ============================================================
# Token: abc123...
# UDS: /run/trim_srv.socket
# 方法: all
#
# [方法1] trimrpc → com.trim.main.tokenQuery
# UDS: /run/trim_srv.socket
# 响应: {
#   "data": {
#     "result": "succ"
#   }
# }
# ✓ Token 验证成功
#
# [方法2] 原始 JSON → user.query_uid_by_token
# UDS: /run/trim_srv.socket
# 响应: {
#   "errno": 0,
#   "uid": 1000
# }
# ✓ Token 验证成功, UID: 1000
#
# [方法3] 原始 JSON → user.authToken
# UDS: /run/trim_srv.socket
# 响应: {
#   "errno": 0,
#   "result": "succ",
#   "session_id": 12345,
#   "uid": 1000
# }
# ✓ Token 验证成功
#   Session ID: 12345
#   UID: 1000
#
# ============================================================
# 验证结果总结
# ============================================================
# trimrpc   : ✓ 成功
# query     : ✓ 成功
# auth      : ✓ 成功
```

---

## 6. 与 trim 框架的关系

### 6.1 服务对比

| 服务 | 协议 | UDS 路径 | 用途 |
|------|------|---------|------|
| **trim** | WebSocket | `/run/trim_cgi.socket` | .hdl handler 框架 |
| **com.trim.main** | trimrpc | `/run/trim_srv.socket` | 主服务 (加载 user.hdl) |
| **trim_http_cgi** | HTTP | `/var/run/trim_http_cgi.socket` | HTTP CGI 网关 |

### 6.2 Token 验证路径

```
trim_http_cgi (HTTP 网关)
    │
    │ trimrpc 协议
    ▼
com.trim.main (/run/trim_srv.socket)
    │
    │ 加载 user.hdl
    ▼
user.hdl (handler)
    │
    │ tokenQuery → query_uid_by_token
    ▼
Token 数据库 / 内存缓存
```

---

## 7. 安全考虑

### 7.1 Token 存储

Token 可能存储在：
1. **内存缓存** - user.hdl 维护的 token → uid 映射
2. **数据库** - 持久化存储
3. **Redis/Memcached** - 分布式缓存

### 7.2 Token 过期

根据 user.hdl 的分析：
- `clear_timeout_token()` - 清理过期短期 token
- `clear_timeout_longtoken()` - 清理过期长期 token
- 定时器每秒执行一次

### 7.3 Token 刷新

```bash
# 调用 user.refresh_token
python3 report-trimrpc-202603041034.py \
    --raw --uds /run/trim_srv.socket \
    '{"req":"user.refresh_token","token":"<old_token>"}'

# 响应:
# {
#   "errno": 0,
#   "token": "<new_token>"
# }
```

---

## 8. 总结

### 8.1 推荐的验证方式

**生产环境**: 使用方式2 (直接调用 user.query_uid_by_token)
- 简单高效
- 不需要实现完整的 trimrpc 协议
- 直接返回 uid

**完全兼容**: 使用方式1 (trimrpc 协议)
- 与 trim_http_cgi 完全一致
- 适合需要完整兼容的场景

**需要 session**: 使用方式3 (user.authToken)
- 验证 token 并创建 session
- 适合需要维护会话的场景

### 8.2 工具使用

```bash
# 1. 验证 token
python3 validate-token.py <token>

# 2. 只使用特定方法
python3 validate-token.py --method query <token>

# 3. 指定 UDS 路径
python3 validate-token.py --uds /custom/path.socket <token>

# 4. 启用调试
python3 validate-token.py --debug <token>
```

### 8.3 关键发现

1. **trim_http_cgi 使用 trimrpc** 调用 `com.trim.main.tokenQuery`
2. **com.trim.main 加载 user.hdl**，提供 token 验证功能
3. **可以绕过 trimrpc** 直接调用 user.hdl 的方法
4. **三种验证方式** 都可以实现 token 验证
5. **推荐使用方式2** (user.query_uid_by_token) 最简单高效
