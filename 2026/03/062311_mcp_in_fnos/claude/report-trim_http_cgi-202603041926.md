# trim_http_cgi (HTTP CGI Gateway) 逆向分析报告

> **分析日期**: 2026-03-04
> **二进制**: `trim_http_cgi` (ELF x86_64, Go binary)
> **编程语言**: Go (Golang)
> **UDS 路径**: `/var/run/trim_http_cgi.socket`
> **分析工具**: IDA Pro + Hex-Rays Decompiler

---

## 1. 概述

`trim_http_cgi` 是 fnOS (trimOS) 的 **HTTP CGI 网关服务**，用 **Go 语言**编写。它的主要职责是：

- 监听 Unix Domain Socket (`/var/run/trim_http_cgi.socket`)
- 接收 HTTP 请求并进行路由分发
- 通过 **trimrpc** 框架验证 token 认证
- 将请求转发给不同类型的 CGI handler
- 支持官方 CGI 和第三方 CGI 应用

### 架构定位

```
Web 客户端 (浏览器/App)
    │
    │ HTTP/HTTPS
    ▼
[Nginx / 前端代理]
    │
    │ Unix Domain Socket
    ▼
┌─────────────────────────────────────┐
│     trim_http_cgi (本服务)           │
│  - Token 认证 (via trimrpc)          │
│  - 路径解析与路由                     │
│  - CGI 类型识别                      │
└──────────┬──────────────────────────┘
           │
           ├─→ Official CGI (官方 CGI)
           │   └─ main._ptr_Handler.ServeHTTP
           │
           ├─→ ThirdParty CGI (第三方 CGI)
           │   └─ main.ThirdPartyCgiHandler
           │
           └─→ ThirdParty with AppUi
               └─ main.ThirdPartyCgiHandlerWithAppUi
```

---

## 2. main() — 服务启动

**地址**: `0x6732e0`

### 启动流程

```go
func main() {
    // 1. 创建日志器
    logger := log.New(os.Stderr, "logger: ", log.LstdFlags)

    // 2. 删除旧的 socket 文件（如果存在）
    os.Remove("/var/run/trim_http_cgi.socket")

    // 3. 创建 Unix Domain Socket 监听
    listener, err := net.Listen("unix", "/var/run/trim_http_cgi.socket")
    if err != nil {
        logger.Fatal(err)
    }

    // 4. 修改 socket 权限为 0666 (rw-rw-rw-)
    os.Chmod("/var/run/trim_http_cgi.socket", 0666)

    // 5. 创建 HTTP Server
    server := &http.Server{
        Handler: &TrimCgiRoute{},  // 主路由 handler
    }

    // 6. 启动服务
    err = server.Serve(listener)
    if err != nil {
        logger.Fatal(err)
    }
}
```

### 关键配置

| 配置项 | 值 | 说明 |
|--------|---|------|
| **UDS 路径** | `/var/run/trim_http_cgi.socket` | Unix Domain Socket 监听地址 |
| **Socket 权限** | `0666` (rw-rw-rw-) | 任何进程可读写 |
| **日志前缀** | `"logger: "` | 日志输出标识 |
| **Handler** | `TrimCgiRoute` | 主路由处理器 |

---

## 3. TrimCgiRoute.ServeHTTP() — 主路由处理

**地址**: `0x673440` (大小: 0x719)

这是整个 CGI 网关的**核心路由函数**，负责请求的认证、解析和分发。

### 处理流程

```
HTTP Request
    │
    ▼
┌─────────────────────────────────────┐
│ 1. getToken(request)                │  ← 从 Cookie/Header/Query 提取 token
│    - Cookie: "fnos-token"           │
│    - Header: "Authorization"        │
│    - Query: "token"                 │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 2. checkToken(token)                │  ← 通过 trimrpc 验证 token
│    - ApplyPermission("com.trim.main")│
│    - Call("tokenQuery", token)      │
│    - 检查响应 result == "succ"       │
└──────────┬──────────────────────────┘
           │
           │ 认证失败 → 返回 "invalid token"
           │
           ▼ 认证成功
┌─────────────────────────────────────┐
│ 3. parsePath(request.URL.Path)      │  ← 解析 URL 路径
│    返回: (cgiType, cgiPath, error)   │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 4. 根据 cgiType 分发:                │
│                                     │
│  - "Official" (8 字节)               │
│    → 返回 "function not yet implemented"
│                                     │
│  - "official" (8 字节)               │
│    → 返回 "function not yet implemented"
│                                     │
│  - "ThirdParty" (10 字节)            │
│    → ThirdPartyCgiHandler()         │
│                                     │
│  - "third-party" (11 字节)           │
│    → ThirdPartyCgiHandlerWithAppUi()│
│                                     │
│  - 其他                              │
│    → 返回 "invalid cgi type"         │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 5. os.Stat(cgiPath)                 │  ← 检查 CGI 文件是否存在
│    - 存在 → Handler.ServeHTTP()      │
│    - 不存在 → 返回错误信息            │
└─────────────────────────────────────┘
```

### 错误响应

| 错误 | 返回内容 |
|------|---------|
| Token 验证失败 | `"invalid token"` (13 字节) |
| 路径解析失败 | 错误详情 (从 parsePath 返回) |
| CGI 类型无效 | `"invalid cgi type"` (16 字节) |
| CGI 文件不存在 | 文件系统错误信息 |
| 官方 CGI 未实现 | `"function not yet implemented"` (28 字节) |

---

## 4. getToken() — Token 提取

**地址**: `0x6744a0` (大小: 0x1df)

从 HTTP 请求中提取认证 token，支持**三种来源**（按优先级）：

### Token 提取优先级

```
1. Cookie: "fnos-token"
   ↓ 如果不存在
2. Header: "Authorization"
   - 支持格式:
     * "Bearer <token>"  → 提取 token (去掉前 7 字节)
     * "Token <token>"   → 提取 token (去掉前 6 字节)
     * "Basic <token>"   → 提取 token (去掉前 5 字节)
     * 其他              → 直接使用整个值
   ↓ 如果不存在或长度 <= 20
3. Query Parameter: "token"
   ↓ 如果不存在
4. Cookie: "token"
   ↓ 如果都不存在
5. 返回空字符串
```

### 伪代码

```go
func getToken(request *http.Request) string {
    // 1. 尝试从 Cookie "fnos-token" 获取
    cookies := request.Cookies("fnos-token")
    if len(cookies) > 0 {
        return cookies[0].Value
    }

    // 2. 尝试从 Authorization Header 获取
    auth := request.Header.Get("authorization")
    if len(auth) > 20 {
        lower := strings.ToLower(auth[:7])
        if lower == "bearer " {
            return auth[7:]  // 去掉 "Bearer "
        }
        lower = strings.ToLower(auth[:6])
        if lower == "token " {
            return auth[6:]  // 去掉 "Token "
        }
        lower = strings.ToLower(auth[:5])
        if lower == "basic " {
            return auth[5:]  // 去掉 "Basic "
        }
        return auth  // 直接返回整个值
    }

    // 3. 尝试从 Query Parameter "token" 获取
    query := request.URL.Query()
    token := query.Get("token")
    if token != "" {
        return token
    }

    // 4. 尝试从 Cookie "token" 获取
    cookies = request.Cookies("token")
    if len(cookies) > 0 {
        return cookies[0].Value
    }

    // 5. 返回空
    return ""
}
```

---

## 5. checkToken() — Token 验证

**地址**: `0x6742a0` (大小: 0x1f9)

通过 **trimrpc 框架**调用 `com.trim.main` 服务的 `tokenQuery` 方法验证 token。

### 验证流程

```
┌─────────────────────────────────────┐
│ 1. 检查 token 是否为空               │
│    - 空 → 返回 "invalid token" 错误  │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 2. 初始化 trimrpc Client             │
│    client := trimrpc.NewClient()     │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 3. 申请服务权限                      │
│    client.ApplyPermission(           │
│        []string{"com.trim.main"}     │
│    )                                 │
│    - 失败 → 返回错误                 │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 4. 调用 tokenQuery RPC               │
│    response := client.Call(          │
│        service: "com.trim.main",     │
│        method:  "tokenQuery",        │
│        request: TokenQueryCommand{   │
│            Token: token              │
│        }                             │
│    )                                 │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 5. 检查响应                          │
│    result := response.Result()       │
│    if result == "succ" {             │
│        return nil  // 验证成功        │
│    } else {                          │
│        return "invalid response"     │
│    }                                 │
└─────────────────────────────────────┘
```

### 关键 RPC 调用

| 参数 | 值 |
|------|---|
| **Service** | `"com.trim.main"` |
| **Method** | `"tokenQuery"` |
| **Request Type** | `TokenQueryCommand` |
| **Request Data** | `{ Token: <token_string> }` |
| **成功响应** | `{ result: "succ" }` |

### 错误类型

| 错误 | 说明 |
|------|------|
| `"invalid token"` | token 为空 |
| `ApplyPermission 错误` | 无法获取 com.trim.main 服务权限 |
| `RPC Call 错误` | tokenQuery 调用失败 |
| `"invalid response"` | 响应中 result != "succ" |

---

## 6. parsePath() — 路径解析

**地址**: `0x673c20` (大小: 0x136)

解析 HTTP 请求的 URL 路径，提取 CGI 类型和 CGI 路径。

### 路径格式

```
URL Path 格式:
/<cgiType>/<cgiPath>

示例:
/ThirdParty/app1/index.cgi
    ↓
cgiType = "ThirdParty"
cgiPath = "app1/index.cgi"

/official/system/info.cgi
    ↓
cgiType = "official"
cgiPath = "system/info.cgi"
```

### 解析逻辑

```go
func parsePath(urlPath string) (cgiType, cgiPath string, err error) {
    // 去掉开头的 '/'
    path := strings.TrimPrefix(urlPath, "/")

    // 找到第一个 '/' 的位置
    idx := strings.Index(path, "/")
    if idx == -1 {
        return "", "", errors.New("invalid path format")
    }

    // 分割 cgiType 和 cgiPath
    cgiType = path[:idx]
    cgiPath = path[idx+1:]

    return cgiType, cgiPath, nil
}
```

---

## 7. CGI Handler 类型

### 7.1 Official CGI (官方 CGI)

**触发条件**: `cgiType == "Official"` 或 `cgiType == "official"`

**状态**: **未实现** (返回 `"function not yet implemented"`)

**预期用途**: 处理系统内置的官方 CGI 应用

---

### 7.2 ThirdParty CGI (第三方 CGI)

**函数**: `main.ThirdPartyCgiHandler` @ `0x673d60` (大小: 0x299)

**触发条件**: `cgiType == "ThirdParty"`

**功能**: 执行第三方应用的 CGI 脚本

**实现**: 标准 CGI 执行器，设置环境变量并调用 CGI 程序

---

### 7.3 ThirdParty with AppUi (第三方 + 应用 UI)

**函数**: `main.ThirdPartyCgiHandlerWithAppUi` @ `0x674000` (大小: 0x293)

**触发条件**: `cgiType == "third-party"`

**功能**: 执行第三方应用的 CGI 脚本，并支持应用 UI 集成

**与 ThirdParty 的区别**: 可能包含额外的 UI 路由或资源处理

---

### 7.4 Handler.ServeHTTP (标准 CGI Handler)

**函数**: `main._ptr_Handler.ServeHTTP` @ `0x670700` (大小: 0x258e)

**功能**: 标准 CGI 执行器，负责：
- 设置 CGI 环境变量
- 执行 CGI 程序
- 捕获输出并返回给客户端
- 处理 CGI 错误

---

## 8. CGI 环境变量

根据字符串分析，CGI Handler 会设置以下标准环境变量：

| 环境变量 | 说明 |
|---------|------|
| `QUERY_STRING` | URL 查询字符串 |
| `CONTENT_TYPE` | 请求 Content-Type |
| `CONTENT_LENGTH` | 请求 Content-Length |
| `REQUEST_METHOD` | HTTP 方法 (GET/POST/etc) |
| `SCRIPT_FILENAME` | CGI 脚本文件路径 |
| `SERVER_SOFTWARE` | 服务器软件标识 |
| `HTTP_PATH` | HTTP 路径 |

---

## 9. 辅助函数

### 9.1 extractCGISegment()

**地址**: `0x674680` (大小: 0x5b)

提取 CGI 路径段

---

### 9.2 getCGIPath()

**地址**: `0x6746e0` (大小: 0x12c)

获取完整的 CGI 文件系统路径

---

### 9.3 rewriteCGIPath()

**地址**: `0x674820` (大小: 0x114)

重写 CGI 路径（可能用于路径规范化或别名处理）

---

### 9.4 removeLeadingDuplicates()

**地址**: `0x670460` (大小: 0x293)

移除路径中的重复前导字符（如多个 `/`）

---

## 10. 与 trimrpc 框架的集成

`trim_http_cgi` 依赖 **trimrpc 框架**进行 token 验证：

### RPC 服务依赖

| 服务 | 方法 | 用途 |
|------|------|------|
| `com.trim.main` | `tokenQuery` | 验证用户 token |

### RPC 客户端初始化

```go
// 在 checkToken() 中
client := trimrpc.NewClient()
client.ApplyPermission([]string{"com.trim.main"})
```

### Token 验证请求

```go
type TokenQueryCommand struct {
    Token string
}

response := client.Call(
    "com.trim.main",
    "tokenQuery",
    TokenQueryCommand{Token: token}
)

// 检查响应
if response.Result() == "succ" {
    // 验证成功
}
```

---

## 11. 安全机制

### 11.1 认证流程

```
1. 提取 token (Cookie/Header/Query)
   ↓
2. 通过 trimrpc 调用 com.trim.main.tokenQuery
   ↓
3. 检查响应 result == "succ"
   ↓
4. 验证成功 → 允许访问 CGI
   验证失败 → 返回 "invalid token"
```

### 11.2 Socket 权限

- UDS 权限: `0666` (rw-rw-rw-)
- **任何本地进程可连接**
- 依赖 token 认证保护

### 11.3 CGI 文件检查

- 在执行前通过 `os.Stat()` 检查文件是否存在
- 防止路径遍历攻击（需要进一步验证）

---

## 12. 请求处理完整流程图

```
HTTP Client
    │
    │ HTTP Request
    ▼
┌─────────────────────────────────────┐
│  Nginx / 前端代理                    │
└──────────┬──────────────────────────┘
           │ Unix Socket
           ▼
┌─────────────────────────────────────┐
│  trim_http_cgi                      │
│  main.main()                        │
│  - Listen on /var/run/trim_http_cgi.socket
│  - http.Server.Serve()              │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│  TrimCgiRoute.ServeHTTP()           │
│                                     │
│  1. getToken(request)               │
│     - Cookie: fnos-token            │
│     - Header: Authorization         │
│     - Query: token                  │
│                                     │
│  2. checkToken(token)               │
│     ┌─────────────────────────┐    │
│     │ trimrpc Client          │    │
│     │ ├─ ApplyPermission      │    │
│     │ └─ Call(tokenQuery)     │    │
│     └─────────────────────────┘    │
│     ↓                               │
│     com.trim.main.tokenQuery        │
│     ↓                               │
│     result == "succ" ?              │
│                                     │
│  3. parsePath(URL.Path)             │
│     → (cgiType, cgiPath)            │
│                                     │
│  4. 路由分发:                        │
│     ├─ "Official"                   │
│     │  → 未实现                      │
│     ├─ "ThirdParty"                 │
│     │  → ThirdPartyCgiHandler       │
│     └─ "third-party"                │
│        → ThirdPartyCgiHandlerWithAppUi
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│  CGI Handler                        │
│  - 设置环境变量                      │
│  - 执行 CGI 程序                     │
│  - 捕获输出                          │
└──────────┬──────────────────────────┘
           │
           ▼
HTTP Response
```

---

## 13. 关键函数地址索引

| 函数名 | 地址 | 大小 | 说明 |
|--------|------|------|------|
| `main.main` | `0x6732e0` | 0x155 | 程序入口 |
| `main.TrimCgiRoute.ServeHTTP` | `0x673440` | 0x719 | 主路由处理 |
| `main.getToken` | `0x6744a0` | 0x1df | Token 提取 |
| `main.checkToken` | `0x6742a0` | 0x1f9 | Token 验证 |
| `main.parsePath` | `0x673c20` | 0x136 | 路径解析 |
| `main.ThirdPartyCgiHandler` | `0x673d60` | 0x299 | 第三方 CGI |
| `main.ThirdPartyCgiHandlerWithAppUi` | `0x674000` | 0x293 | 第三方 CGI + UI |
| `main._ptr_Handler.ServeHTTP` | `0x670700` | 0x258e | 标准 CGI 执行器 |
| `main.extractCGISegment` | `0x674680` | 0x5b | 提取 CGI 段 |
| `main.getCGIPath` | `0x6746e0` | 0x12c | 获取 CGI 路径 |
| `main.rewriteCGIPath` | `0x674820` | 0x114 | 重写 CGI 路径 |
| `main.removeLeadingDuplicates` | `0x670460` | 0x293 | 移除重复前导字符 |

---

## 14. 与其他服务的关系

### 14.1 与 trim 框架的关系

- **无直接关系**
- `trim` 框架处理 WebSocket 请求 (UDS: `/run/trim_cgi.socket`)
- `trim_http_cgi` 处理 HTTP CGI 请求 (UDS: `/var/run/trim_http_cgi.socket`)
- 两者是**独立的服务**

### 14.2 与 trimrpc 框架的关系

- **强依赖**
- 使用 `trimrpc.Client` 进行 RPC 调用
- 依赖 `com.trim.main` 服务进行 token 验证
- 通过 RPC Broker 进行服务发现

### 14.3 服务架构对比

| 服务 | 协议 | UDS 路径 | 用途 |
|------|------|---------|------|
| **trim** | WebSocket | `/run/trim_cgi.socket` | .hdl handler 插件框架 |
| **trim_http_cgi** | HTTP | `/var/run/trim_http_cgi.socket` | HTTP CGI 网关 |
| **com.trim.main** | trimrpc | `/run/trim_srv.socket` | 主服务 (token 验证等) |

---

## 15. 配置与部署

### 15.1 Socket 文件

```bash
# Socket 路径
/var/run/trim_http_cgi.socket

# 权限
-rw-rw-rw- (0666)

# 所有者
通常为运行 trim_http_cgi 的用户
```

### 15.2 前端代理配置 (推测)

```nginx
# Nginx 配置示例
upstream trim_http_cgi {
    server unix:/var/run/trim_http_cgi.socket;
}

location /ThirdParty/ {
    proxy_pass http://trim_http_cgi;
}

location /official/ {
    proxy_pass http://trim_http_cgi;
}
```

---

## 16. 安全建议

### 16.1 当前安全状况

✅ **已实现**:
- Token 认证 (通过 trimrpc)
- CGI 文件存在性检查

⚠️ **潜在风险**:
- Socket 权限 0666 (任何进程可连接)
- 依赖前端代理进行访问控制
- 官方 CGI 未实现（可能存在占位符漏洞）

### 16.2 建议

1. **收紧 Socket 权限**: 改为 0660，限制访问组
2. **路径遍历防护**: 验证 `parsePath()` 是否充分防御路径遍历
3. **CGI 执行权限**: 确保 CGI 程序以受限用户身份运行
4. **Token 过期**: 实现 token 过期机制
5. **速率限制**: 添加请求速率限制防止暴力破解

---

## 17. 总结

`trim_http_cgi` 是 fnOS 的 **HTTP CGI 网关**，负责：

1. **认证**: 通过 trimrpc 调用 `com.trim.main.tokenQuery` 验证 token
2. **路由**: 根据 URL 路径分发到不同的 CGI handler
3. **执行**: 调用 CGI 程序并返回结果

### 关键特性

- **Go 语言实现**: 使用 Go 标准库 `net/http` 和 `net` 包
- **Unix Socket**: 监听 `/var/run/trim_http_cgi.socket`
- **trimrpc 集成**: 依赖 trimrpc 框架进行服务间通信
- **多 CGI 类型**: 支持官方和第三方 CGI 应用

### 架构定位

```
Web UI → Nginx → trim_http_cgi → CGI 程序
                      ↓
                  trimrpc
                      ↓
              com.trim.main (token 验证)
```

`trim_http_cgi` 是连接 Web 前端和后端 CGI 应用的**桥梁**，通过 trimrpc 框架实现统一的认证和服务调用。
