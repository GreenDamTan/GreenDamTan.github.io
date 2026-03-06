# user.hdl 逆向分析报告

> **分析时间**: 2026-03-04 17:30
> **二进制文件**: user.hdl
> **架构**: x86_64 (ELF shared object)
> **源文件**: `/linux-dev/trim/handler_user/user.cpp`
> **分析工具**: IDA Pro + MCP

---

## 1. 服务概述

`user.hdl` 是 fnOS (trimOS) 的**用户管理服务处理模块**（handler），以共享库（.so/hdl）形式由主框架服务 **`com.trim.main`** 加载（UDS: `/run/trim_srv.socket`）。

> **注意**: user.hdl **不是**独立注册的 RPC 服务，它作为 handler 插件被 `com.trim.main` (TRIM Service) 加载。RPC Broker 中不存在 `com.trim.user` 服务，所有请求都通过 `com.trim.main` 路由。

它负责：

- 用户认证（PAM）、登录/登出、Token 管理
- 用户 CRUD（增删改查）
- 用户组管理（组增删改、成员管理）
- 密码管理（修改、检查、重置）
- 2FA 双因素认证集成
- 会话管理与登录设备追踪
- D-Bus 信号通知
- Samba 账户同步

**依赖库**:
| 库 | 用途 |
|---|---|
| libpq.so.5 | PostgreSQL 数据库 |
| libpam.so.0 | PAM 认证 |
| libcrypto.so.3 (OpenSSL 3.0) | AES-256-CBC 加密、RSA、SHA256、MD5、HMAC |
| libdbus-1.so.3 | D-Bus 信号 |
| libsimplerpc.so | 简单 RPC 调用（2FA 服务） |
| libppjson.so | JSON 解析/序列化 |
| libndev.so | 设备相关工具 |
| libcrypt.so.1 | crypt() 密码哈希 |
| libevent_logger.so | 事件日志记录 |

---

## 2. 入口点与回调函数

### 2.1 `before_init` @ 0x99e0

初始化函数，调用 `init_db()` 初始化数据库，然后注册 **26 个内部 API 函数**供其他 handler 调用：

```c
// before_init @ 0x99e0 — 反编译节选
void before_init(void (*register_fn)(const char*, func_ptr)) {
    init_db();
    register_fn("get_user_list",              get_user_list);
    register_fn("session_auth",               session_auth);
    register_fn("get_admin_uids",             get_admin_uids);
    register_fn("get_uids",                   get_uids);
    register_fn("get_group_list",             get_group_list);
    register_fn("get_session_uid",            get_session_uid);
    register_fn("get_uid_gid_by_username",    get_uid_gid_by_username);
    register_fn("is_admin",                   is_admin_grouper);
    register_fn("is_session_admin",           is_session_admin);
    register_fn("is_session_login",           is_session_login);
    register_fn("get_username_by_uid",        get_username_by_uid);
    register_fn("get_groupname_by_gid",       get_groupname_by_gid);
    register_fn("get_uver",                   get_uver);
    register_fn("send_string_to_user",        send_string_to_user);
    register_fn("send_string_to_admin",       send_string_to_admin);
    register_fn("send_string_to_all_users",   send_string_to_all_users);
    register_fn("auth_token_and_add_session", auth_token_and_add_session);
    register_fn("get_user_gid_by_uid",        get_user_gid_by_uid);
    register_fn("get_session_uid_gid_username", get_session_uid_gid_username);
    register_fn("query_token_by_session",     query_token_by_session);
    register_fn("query_uid_by_token",         query_uid_by_token);
    register_fn("get_user_type",              get_user_type);
    register_fn("get_group_type",             get_group_type);
    register_fn("get_main_session_count",     get_main_session_count);
    register_fn("remove_user_all_token",      remove_user_all_token);
    register_fn("refresh_token",              refresh_token);
}
```

### 2.2 `onSessionEnd` @ 0xa160

会话结束回调，清理会话和主会话记录：

```c
void onSessionEnd(uint64_t session_id) {
    unsigned int uid = get_session_uid(session_id);
    if (uid != -1)
        del_user_main_session(uid, session_id);
    del_session(session_id);
}
```

### 2.3 `onTimerLoop` @ 0xa190

定时器回调（每次 tick 调用），执行：
1. `print_token_counter()` — 输出 Token 统计
2. `clear_timeout_longtoken()` — 清理过期长期 Token
3. `clear_timeout_token()` — 清理过期短期 Token
4. 每 5 次 tick 清理过期的登录失败锁定记录 (`login_guard`)

---

## 3. RPC 方法分发表

主处理函数 `handler()` @ 0xa7b0 从 JSON 的 `req` 字段中提取方法名进行分发。

**`req` 字段解析逻辑** (关键！):
```c
// handler @ 0xa7b0 — req 解析
const char* req_str = input["req"];     // 如 "user.info"
int dot_pos = string::find(req_str, "."); // 找第一个 '.'
if (dot_pos < 0) → "Not found sub req." 错误;
string::erase(req_str, 0, dot_pos + 1);  // 删除第一个 '.' 及其前面的所有字符
// 现在 req_str = "info"，开始匹配方法名
```

因此 `req` 字段格式必须为 `"user.<method>"`（只包含一个 `.` 分隔符）。如果传入 `"com.trim.main.user.info"`，handler 只会剥离第一个 `"com."`，剩余 `"trim.main.user.info"` 不匹配任何方法。

> **框架行为**: `com.trim.main` 框架进程接收 RPC 请求后，将 JSON `data` 对象中的 `req` 字段**原样传递**给 handler，不做前缀剥离。因此客户端发送的 `req` 字段必须直接是 `"user.<method>"` 格式。

### 3.1 完整方法列表

| # | 方法名 | 处理函数 | 地址 | 大小 | 需要登录 | 需要管理员 |
|---|--------|----------|------|------|----------|-----------|
| 1 | `active` | `req_active` | 0xf940 | 0x4f | - | - |
| 2 | `authToken` | `req_authtoken` | 0xd400 | 0x373 | - | - |
| 3 | `login` | `req_login` | 0x20f60 | 0xc09 | - | - |
| 4 | `tokenLogin` | `req_token_login` | 0x1e440 | 0x10aa | - | - |
| 5 | `2fa.loginVerify` | `req_2fa_login_verify` | 0x21b70 | 0x84f | - | - |
| 6 | `2fa.resetPassword` | `req_2fa_reset_passwd` | 0x223c0 | 0x8ab | - | - |
| 7 | `add1000` | `req_add1000` | 0x16100 | 0xd7d | - | - |
| 8 | `isAdmin` | `req_isadmin` | 0xd780 | 0xd7 | ✓ | - |
| 9 | `info` | `req_info` | 0x22c70 | 0x683 | ✓ | - |
| 10 | `logout` | `req_logout` | 0x11880 | 0x55d | ✓ | - |
| 11 | `changePassword` | `req_change_passwd` | 0x14dc0 | 0x579 | ✓ | - |
| 12 | `listLoginDevice` | `req_list_login_device` | 0x20370 | 0xbed | ✓ | - |
| 13 | `checkNewUser` | `req_check_new_user_name` | 0x10950 | 0xe3 | ✓ | - |
| 14 | `checkNewGroup` | `req_check_new_group_name` | 0x10ad0 | 0xe3 | ✓ | - |
| 15 | `checkPassword` | `req_check_password` | 0x11420 | 0x2b2 | ✓ | - |
| 16 | `kickLoginDevice` | `req_kick_login_device` | 0xff40 | 0x70a | ✓ | - |
| 17 | `unfreeze` | `req_unfreeze` | 0x11f20 | 0x19a | ✓ | ✓ |
| 18 | `listUG` | `req_listug` | 0x1ff60 | 0x402 | ✓ | - |
| 19 | `groupUsers` | `req_gusers` | 0x1fa70 | 0x255 | ✓ | - |
| 20 | `listAllLoginDevice` | `req_list_all_user_login_device` | 0xf990 | 0x7c | ✓ | ✓ |
| 21 | `list` | `req_list` | 0x1f4f0 | 0x384 | ✓ | ✓ |
| 22 | `del` | `req_del` | 0x15700 | 0x363 | ✓ | ✓ |
| 23 | `add` | `req_add` | 0x16e80 | 0x1227 | ✓ | ✓ |
| 24 | `mod` | `req_mod` | 0x18a30 | 0x16fa | ✓ | ✓ |
| 25 | `setAdmin` | `req_user_set_admin` | 0x14850 | 0x56d | ✓ | ✓ |
| 26 | `groupList` | `req_glist` | 0x1f880 | 0x1e9 | ✓ | ✓ |
| 27 | `groupDel` | `req_gdel` | 0x1a550 | 0x2c9 | ✓ | ✓ |
| 28 | `groupAdd` | `req_gadd` | 0x1abf0 | 0x302 | ✓ | ✓ |
| 29 | `groupMod` | `req_gmod` | 0x1b440 | 0x466 | ✓ | ✓ |
| 30 | `groupInfo` | `req_ginfo` | 0x1fcd0 | 0x28b | ✓ | ✓ |
| 31 | `groupSetUsers` | `req_gsetusers` | 0x1c6f0 | 0xbf1 | ✓ | ✓ |
| 32 | `groupAddUsers` | `req_gaddusers` | 0x1d2f0 | 0xeb4 | ✓ | ✓ |
| 33 | `groupDelUsers` | `req_gdelusers` | 0x1b8b0 | 0xe34 | ✓ | ✓ |

### 3.2 错误码

| 错误码 | 含义 |
|--------|------|
| 0x1080 (4224) | 未登录 (not logged in) |
| 0x1100 (4352) | 无权限 (permission denied / not admin) |
| 0x1000 (4096) | 内部错误 / RPC 调用失败 |
| 0x1002 (4098) | 内存/资源错误 |
| 0x989680 (10000002) | 未知方法 |
| 0x21000 (135168) | Token 无效 / 认证失败 |
| 0x20000 (131072) | 用户不存在 |
| 0x20001 | 用户未找到 |
| 0x20002 (131074) | 用户已存在 |
| 0x22000 | 不能删除自己 |
| 0x22001 (139265) | 不能删除 uid=1000（初始管理员） |
| 0xFFFF (65535) | 通用系统错误 |

---

## 4. 关键方法详细分析

### 4.1 `handler()` — 主分发函数 @ 0xa7b0

```c
// handler @ 0xa7b0 — 反编译节选（简化）
int handler(uint64_t session, json_t* input, json_t* output, void* extra) {
    const char* req = input["req"];       // 获取请求字段
    const char* sub = strchr(req, '.') + 1; // 取 '.' 后的子命令

    if (!strcmp(sub, "active"))         return req_active(...);
    if (!strcmp(sub, "authToken"))      return req_authtoken(...);
    if (!strcmp(sub, "login"))          return req_login(...);
    if (!strcmp(sub, "tokenLogin"))     return req_token_login(...);

    // 以下需要 is_session_login() 检查
    if (!strcmp(sub, "listUG"))         { CHECK_LOGIN; return req_listug(...); }
    if (!strcmp(sub, "isAdmin"))        { CHECK_LOGIN; return req_isadmin(...); }
    if (!strcmp(sub, "logout"))         { CHECK_LOGIN; return req_logout(...); }
    if (!strcmp(sub, "info"))           { CHECK_LOGIN; return req_info(...); }
    if (!strcmp(sub, "changePassword")) { CHECK_LOGIN; return req_change_passwd(...); }
    // ...

    // 以下需要 is_session_login() + is_session_admin() 检查
    if (!strcmp(sub, "list"))           { CHECK_ADMIN; return req_list(...); }
    if (!strcmp(sub, "add"))            { CHECK_ADMIN; return req_add(...); }
    if (!strcmp(sub, "del"))            { CHECK_ADMIN; return req_del(...); }
    // ...

    // 2FA 方法不需要登录
    if (!strcmp(sub, "2fa.resetPassword"))  return req_2fa_reset_passwd(...);
    if (!strcmp(sub, "2fa.loginVerify"))    return req_2fa_login_verify(...);

    output["errno"] = 10000002; // 未知方法
}
```

**权限模型**:
- **无需认证**: `active`, `authToken`, `login`, `tokenLogin`, `add1000`, `2fa.*`
- **需要登录**: `isAdmin`, `info`, `logout`, `changePassword`, `listLoginDevice`, `checkNewUser`, `checkNewGroup`, `checkPassword`, `kickLoginDevice`, `listUG`, `groupUsers`
- **需要管理员**: `list`, `add`, `del`, `mod`, `setAdmin`, `unfreeze`, `listAllLoginDevice`, `group*`

### 4.2 `req_login()` — 用户登录 @ 0x20f60

**参数**:
| 字段 | 类型 | 必需 | 描述 |
|------|------|------|------|
| user | string | ✓ | 用户名 |
| password | string | ✓ | 密码 |
| stay | int/bool | - | 保持登录 (0/1/2) |
| did | string | - | 设备ID |
| deviceName | string | - | 设备名称 |
| deviceType | string | - | 设备类型 |
| isTrustedDevice | bool | - | 是否信任设备 |

**流程**:
1. 验证 `si` 字段（若通过 WebSocket 连接需要匹配 session_id）
2. `get_uid_gid_by_username()` 获取 uid
3. **登录防爆锁检查**（`login_guard`）：检查该 uid 是否被锁定
4. 调用 `check_avail()` 检查用户是否被禁用
5. 调用 `check_password()` 验证密码（PAM 认证）
6. **2FA 检查**：通过 `simplerpc` 调用 `/tfa/security/v1/getUserSecuritySetting` (UDS: `/var/run/tfa_uds.socket`)
   - 若启用 2FA 且绑定了密钥但设备不受信任，返回 `LoginSucc2FA1` 需二次验证
   - 若设备受信任或未绑定 2FA，调用 `rpc_2fa_update_device()` 更新设备
7. 成功则调用 `do_login()` 生成 token 并记录
8. 失败则更新 `login_guard` 计数器（达到阈值后锁定）
9. 发送事件日志 `LoginSucc` 或 `LoginFail`

```c
// req_login @ 0x20f60 — 登录核心逻辑节选
uid = get_uid_gid_by_username(username, 0);
if (uid == -1) { errno = 0x20000; return -1; }

// 登录防爆保护
pthread_mutex_lock(&mutex);
// 在 login_guard 树中查找 uid
// 如果 error_count >= g_login_guard 且 banned_time > now → 拒绝
pthread_mutex_unlock(&mutex);

// 密码验证
check_result = check_avail(username);
if (check_result != 0 || !check_password(username, password)) {
    // 更新 login_guard：error_count++
    send_event_log(3, "LoginFail", ...);
    errno = 0x20000; return -1;
}

// 2FA 检查
simplerpc_open("/var/run/tfa_uds.socket");
// → POST /tfa/security/v1/getUserSecuritySetting { uid, username, did }
```

### 4.3 `do_login()` — Token 生成核心 @ 0xd860

**Token 结构** (32 字节):
- 字节 0-3: `rand() ^ tv_nsec ^ secret_key[0:4]` — 随机种子
- 字节 4-7: `time(0)` — 时间戳
- 字节 8-11: `mask_rand ^ token_inc` — 混合掩码
- 字节 12-15: `rand() ^ tv_nsec ^ secret_key[8:12]` — 随机种子
- 字节 16-31: `AES-256-CBC(secret[0:15], key=secret_aeskey)` — 加密校验

**Secret key** 来源: `/usr/trim/etc/rsa_private_key.pem` 文件偏移 100 处的 32 字节。

**长期 Token** (40 字节，`stay=1` 时生成):
- 字节 0-3: `rand() ^ secret_key ^ tv_nsec`
- 字节 4-7: uid
- 字节 8-15: `time(0) + 2592000` (30天过期)
- 字节 16-31: `AES-256-CBC(secret[0:15], key=secret_aeskey)`
- 字节 32-39: checksum xor

Token 以 Base64 编码返回（32 字节 → 44 字符，40 字节 → 56 字符）。

**响应字段**:
| 字段 | 描述 |
|------|------|
| uid | 用户 uid |
| admin | 是否管理员 (0/1) |
| token | Base64 编码的 32 字节 session token |
| longToken | Base64 编码的 40 字节长期 token（stay 模式） |
| secret | Base64 编码的加密密钥（非 token 登录时） |
| backId | 后台 ID (`%08x%08x` 格式) |
| machineId | 设备机器 ID |

### 4.4 `req_authtoken()` — Token 认证 @ 0xd400

**参数**:
| 字段 | 类型 | 必需 | 描述 |
|------|------|------|------|
| si | string | ✓ | session_id (必须匹配) |
| token | string | ✓ | 44 字符 Base64 token |
| active | bool | - | 是否刷新 token |
| main | bool | - | 是否建立 main session |

**流程**:
1. 验证 `si` 与 session_id 匹配
2. Base64 解码 token（必须 44 字符 → 32 字节）
3. `query_token()` 验证 token 有效性
4. 若 `active=true`，调用 `refresh_token()` 刷新
5. `add_session()` 建立会话
6. 若 `main=true`，调用 `add_user_main_session()` 建立主会话

### 4.5 `req_add()` — 添加用户 @ 0x16e80

**参数**:
| 字段 | 类型 | 必需 | 描述 |
|------|------|------|------|
| user | string | ✓ | 用户名 |
| password | string | ✓ | 密码 |
| setAdmin | bool | - | 设置为管理员 |
| groups | array[string] | - | 所属组列表 |
| comment | string | - | 备注 |
| email | string | - | 邮箱 |
| mobile | string | - | 手机号 |
| disableChangePassword | bool | - | 禁止修改密码 |

**流程**:
1. 验证 `user` 和 `password` 字段存在
2. `is_os_user_exists()` 检查用户是否已存在 → 已存在返回 errno=131074
3. 解析 `groups` 数组，过滤掉 `Users`、`Administrators` 等系统组
4. 若 `setAdmin=true`，自动添加到 `Administrators` 组
5. 调用 `user_add()` 执行系统级用户创建
6. 成功后发送 D-Bus 信号和事件日志 `AddUser`

**底层操作** (`user_add` @ 0x15a70):
- 调用 `/usr/trim/bin/useradd` 创建系统用户
- 默认 shell: `-s/bin/bash`（管理员）或 `-s/usr/sbin/nologin`（普通用户）
- 调用 `gpasswd` 添加到组
- SQL: `INSERT INTO users(uid,email,mobile,disablechangepasswd) VALUES(...)`
- 调用 `pdbedit` 和 `smbpasswd` 创建 Samba 账户

### 4.6 `req_del()` — 删除用户 @ 0x15700

**参数**:
| 字段 | 类型 | 必需 | 描述 |
|------|------|------|------|
| user | string | ✓ | 要删除的用户名 |

**限制**:
- uid=1000（初始管理员）不可删除 → errno=139265
- 不能删除自己 → errno=0x22000

**流程**:
1. `get_uid_gid_by_username()` 获取 uid
2. 调用 `user_del()` 执行删除
3. `del_user_all_token()` 清除该用户所有 token
4. D-Bus 发送 `user_removed` 信号
5. 事件日志 `DelUser`

**底层操作** (`user_del` @ 0x15340):
- SQL: `DELETE FROM users WHERE uid=$1`
- 调用 `userdel` 删除系统账户
- 调用 `smbpasswd -x` 和 `pdbedit -x` 删除 Samba 账户

### 4.7 `req_change_passwd()` — 修改密码 @ 0x14dc0

**参数**:
| 字段 | 类型 | 必需 | 描述 |
|------|------|------|------|
| user | string | ✓ | 用户名 |
| password | string | 条件 | 旧密码（非管理员必需） |
| newPassword | string | ✓ | 新密码 |
| removeToken | bool | - | 是否清除其他 token |

**权限逻辑**:
- **管理员**: 可以修改任意用户密码，旧密码可选
- **普通用户**: 只能修改自己的密码，必须提供正确的旧密码
- 若用户设置了 `disableChangePassword`，普通用户无法修改

**流程**:
1. 调用 `user_change_passwd()` 修改系统密码 (PAM `chpasswd`)
2. `cache_passwd_group()` 刷新缓存
3. 若 `removeToken=true`，清除该用户其他 token
4. 同步更新 Samba 密码 (`smbpasswd -a`)

### 4.8 `req_info()` — 获取用户信息 @ 0x22c70

**参数**:
| 字段 | 类型 | 必需 | 描述 |
|------|------|------|------|
| user | string | - | 查询指定用户（管理员可用） |

**响应**:
```json
{
    "userInfo": {
        "user": "admin",
        "uid": 1000,
        "comment": "",
        "email": "",
        "mobile": "",
        "disableChangePassword": 0,
        "lastChange": 12345,
        "disableUser": 0,
        "admin": 1,
        "allowSSH": 1,
        "bannedTime": 0,
        "groups": ["Administrators", "Users"]
    }
}
```

**特殊字段**:
- `disableUser`: 0=正常, 1=密码无效（非$开头的hash）, >0=过期天数
- `bannedTime`: 登录失败锁定剩余秒数
- `admin`: 来自 passwd 结构中的 admin 标记
- `allowSSH`: 来自用户额外属性

### 4.9 `req_logout()` — 登出 @ 0x11880

**流程**:
1. `query_session()` 获取当前 session 的 token
2. `get_long_token_by_token()` 获取长期 token
3. SQL: `DELETE FROM longtoken WHERE token=$1` — 删除数据库中的长期 token
4. `del_token()` 删除内存中的短期 token
5. `del_user_main_session()` 删除主会话
6. `del_session()` 删除会话
7. 事件日志 `Logout`

### 4.10 `req_2fa_reset_passwd()` — 2FA 密码重置 @ 0x223c0

**参数**:
| 字段 | 类型 | 必需 | 描述 |
|------|------|------|------|
| code | string | ✓ | 2FA 验证码 |
| newPassword | string | ✓ | 新密码 |

**流程**:
1. 通过 `simplerpc` 调用 `/tfa/security/v1/resetPasswordVerify` 验证码
2. 从响应中获取 uid
3. 验证用户存在且为 NAS 用户
4. 调用 `user_change_passwd()` 重置密码
5. `del_user_all_token()` 清除所有 token
6. 更新 Samba 密码
7. 事件日志 `ResetPassword`

---

## 5. 数据库层

### 5.1 PostgreSQL 连接

```
host=/var/run/postgresql user=postgres dbname=trim
```

### 5.2 数据表结构

**groups 表**:
```sql
CREATE TABLE IF NOT EXISTS groups (
    gid       INT NOT NULL UNIQUE,
    comment   VARCHAR(256),
    permset   BIGINT DEFAULT 0,
    PRIMARY KEY(gid)
);
-- 默认数据
INSERT INTO groups (gid, comment) VALUES (1000, 'default administrator group');
INSERT INTO groups (gid, comment) VALUES (1001, 'default user group');
```

**users 表**:
```sql
CREATE TABLE IF NOT EXISTS users (
    uid                  INT NOT NULL UNIQUE,
    email                VARCHAR(128),
    mobile               VARCHAR(32),
    disablechangepasswd  SMALLINT DEFAULT 0,
    permset              BIGINT DEFAULT 0,
    PRIMARY KEY(uid)
);
```

**longtoken 表** (持久化登录 Token):
```sql
CREATE TABLE IF NOT EXISTS longtoken (
    token       VARCHAR(64) NOT NULL UNIQUE,
    did         VARCHAR(64),
    uid         INT NOT NULL,
    overtime    BIGINT,
    logintime   BIGINT,
    device_name VARCHAR(64),
    device_type VARCHAR(64),
    UNIQUE(did, uid)
);
```

### 5.3 关键 SQL 操作

| 操作 | SQL |
|------|-----|
| 新增长期Token | `INSERT INTO longtoken(token,uid,overtime,logintime,did,device_name,device_type) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(did,uid) DO UPDATE SET token=$1, overtime=$3, logintime=$4, device_name=$6, device_type=$7` |
| 新增无设备Token | `INSERT INTO longtoken(token,uid,overtime,logintime,device_name,device_type) VALUES($1,$2,$3,$4,$5,$6)` |
| 删除Token | `DELETE FROM longtoken WHERE token=$1` |
| 删除设备Token | `DELETE FROM longtoken WHERE did=$1 AND uid=$2` |
| 删除用户Token | `DELETE FROM longtoken WHERE uid=$1` |
| Token查询uid | `SELECT uid FROM longtoken WHERE token=$1 AND uid=$2 AND overtime>$3 LIMIT 1` |
| 刷新Token时间 | `UPDATE longtoken SET logintime=$1 WHERE token=$2` |
| 清理过期Token | `DELETE FROM longtoken WHERE logintime<$1 OR overtime<$2` |
| 删除用户数据 | `DELETE FROM users WHERE uid=$1` |
| 删除组数据 | `DELETE FROM groups WHERE gid=$1` |
| 新增组 | `INSERT INTO groups(gid,comment) VALUES($1, $2)` |
| 新增用户 | `INSERT INTO users(uid,email,mobile,disablechangepasswd) VALUES($1,$2,$3,$4) ON CONFLICT(uid) DO UPDATE SET permset=0,email=$2,mobile=$3,disablechangepasswd=$4` |
| 查组信息 | `SELECT gid,comment FROM groups` |
| 查用户信息 | `SELECT uid,email,mobile,disablechangepasswd FROM users` |

---

## 6. Token 与会话管理

### 6.1 Token 体系

系统维护三层 Token 机制：

1. **Session Token** (32 字节): 内存中的 `g_token_map`（`std::map<char*,void*>`），与 session_id 关联
2. **Long Token** (40 字节): 持久化到 PostgreSQL `longtoken` 表，30 天有效期 (`time(0) + 2592000`)
3. **Session 映射**: `g_session_map` 将 session_id 映射到 token

### 6.2 Token 生命周期

```
login → do_login() → [生成 session_token + long_token]
                    → add_token(token, uid, long_token, did)
                    → add_session(session_id, token)
                    → add_user_main_session(uid, session_id, token)
                    → INSERT INTO longtoken(...)

active → refresh_token(token)

authToken → query_token(token) → add_session()

logout → del_token() + DELETE FROM longtoken + del_session()

onTimerLoop → clear_timeout_longtoken() (SQL DELETE)
            → clear_timeout_token() (内存清理)
```

### 6.3 Token 统计

`print_token_counter()` @ 0x277d0 定期输出:
```
g_longtoken_token_map:%ld, g_token_map:%ld, g_uid_token_map:%ld/%ld, g_session_map:%ld, g_uid_mainsession_map:%ld/%ld
```

并写入 `/run/trim_token_counter` 文件（格式: `%u-%s`）。

---

## 7. 安全机制

### 7.1 密码验证 — PAM

```c
// check_password @ 0x10ef0
bool check_password(const char* username, const char* password) {
    pam_start("common-auth", username, &conv, &pamh);
    int ret = pam_authenticate(pamh, 0);
    pam_acct_mgmt(pamh, 0);  // 账户管理检查
    pam_end(pamh, ret);
    return (ret == PAM_SUCCESS);
}
```

### 7.2 账户可用性检查

```c
// check_avail @ 0x10e20
int check_avail(const char* username) {
    // 查找 passwd 结构
    // 检查密码过期 (shadow->sp_expire)
    // sp_expire > 0 且 86400 * sp_expire < time(0) → 返回 -2 (过期)
    // 否则返回 0 (可用)
}
```

### 7.3 登录防爆保护 (Login Guard)

全局变量 `g_login_guard` @ 0x39b20 控制：
- 高32位: 锁定时长（秒）
- 低32位: 最大失败次数

内存中维护 `std::_Rb_tree<uid, login_error_t>` 记录每个 uid 的失败次数和锁定截止时间。

**login_error_t 结构** (推断):
```c
struct login_error_t {
    unsigned int uid;          // +32: 用户 uid
    int date;                  // +40: 记录日期 (day|mon<<8|year<<16)
    unsigned int error_count;  // +56: 失败次数
    time_t banned_until;       // +48: 锁定截止时间
};
```

`onTimerLoop` 每 5 次 tick 清理过期的锁定记录。

### 7.4 AES 加密

使用 **AES-256-CBC** 对 Token 进行加密保护。密钥材料来自：
- `/usr/trim/etc/rsa_private_key.pem` 偏移 100 处的 32 字节
- 使用 TLS 线程局部存储缓存密钥，按文件 mtime 检测变更

### 7.5 Samba 同步

所有用户增/删/改密码操作同步到 Samba：
- 新增: `smbpasswd -a <user>` + `pdbedit`
- 删除: `smbpasswd -x <user>` + `pdbedit -x`
- 改密: `smbpasswd -a <user>` (stdin: `newpass\nnewpass\n`)

---

## 8. D-Bus 通知

通过 D-Bus 系统总线发送信号通知其他组件：

```c
// user_dbus_send_message @ 0xcf10
void user_dbus_send_message(const char* signal_name, uid_t uid, const char* username) {
    conn = dbus_bus_get_private(DBUS_BUS_SYSTEM, NULL);
    msg = dbus_message_new_signal("/trim/user", "trim.user.signal", signal_name);
    dbus_message_append_args(msg, DBUS_TYPE_UINT32, &uid, DBUS_TYPE_STRING, &username, 0);
    dbus_connection_send(conn, msg, NULL);
    dbus_message_unref(msg);
    dbus_connection_close(conn);
    dbus_connection_unref(conn);
}
```

| 信号名 | 触发场景 |
|--------|---------|
| `user_removed` | 用户删除后 |
| `user_added` | 用户创建后 |
| `user_enabled` | 用户启用后 |
| `user_disabled` | 用户禁用后 |

### WebSocket 推送

通过 `send_string_to_user/admin/all_users` 向客户端推送消息：
| 消息 | 触发场景 |
|------|---------|
| `{"sysNotify":"kicked"}` | 设备被踢出 |
| `{"sysNotify":"tokenExpired"}` | Token 过期 |
| `{"sysNotify":"privilegeChanged","privilege":"1"}` | 提升为管理员 |
| `{"sysNotify":"privilegeChanged","privilege":"0"}` | 取消管理员 |

---

## 9. 2FA 集成

通过 `simplerpc` + `/var/run/tfa_uds.socket` 与 2FA 服务通信：

| 端点 | 用途 |
|------|------|
| `/tfa/security/v1/getUserSecuritySetting` | 查询用户 2FA 设置 |
| `/tfa/security/v1/loginVerify` | 2FA 登录验证 |
| `/tfa/security/v1/bindVerifyEmailCode` | 邮件验证码绑定 |
| `/tfa/security/v1/resetPasswordVerify` | 密码重置验证 |
| `/tfa/security/v1/updateDevice` | 更新受信任设备 |

**2FA 响应字段**:
- `isTwofaEnforced`: 是否强制 2FA
- `isBindTwofaSecret`: 是否已绑定 2FA 密钥
- `isTrustedDevice`: 当前设备是否受信任
- `accessToken`: 2FA 验证通过后的 access token

---

## 10. 系统文件操作

| 文件 | 用途 |
|------|------|
| `/etc/passwd` | 用户账户信息（`fgetpwent_r` 读取） |
| `/etc/shadow` | 密码哈希（`fgetspent` 读取） |
| `/etc/group` | 用户组（`fgetgrent_r` 读取） |
| `/usr/trim/etc/rsa_private_key.pem` | RSA 私钥 + AES 密钥材料 |
| `/run/trim_token_counter` | Token 计数器输出 |
| `/proc/sys/kernel/random/uuid` | UUID 生成 |
| `/dev/null` | 子进程重定向 |

**系统命令调用**:
| 命令 | 用途 |
|------|------|
| `/usr/trim/bin/useradd` | 创建用户（首用户 `-u1000 -s/bin/bash`） |
| `userdel` | 删除用户 |
| `usermod` | 修改用户（启用/禁用） |
| `chpasswd` | 修改密码 |
| `gpasswd` | 组成员管理 |
| `smbpasswd` | Samba 密码管理 |
| `pdbedit` | Samba 用户数据库管理 |
| `groupadd` → `/usr/trim/bin/groupadd` | 创建组 |
| `groupdel` | 删除组 |
| `groupmod` | 修改组 |

---

## 11. 内存数据结构

### 11.1 全局变量

| 地址 | 名称 | 类型 | 描述 |
|------|------|------|------|
| 0x399d0 | `locker_passwd_group` | pthread_rwlock_t | passwd/group 缓存读写锁 |
| 0x39908 | `dword_39908` / map_name_passwd | std::_Rb_tree | 用户名→passwd* 映射 |
| 0x39b20 | `g_login_guard` | int64 | 登录防爆配置 (low32=次数, hi32=秒) |
| 0x39b28 | `unk_39B28` | std::_Rb_tree | uid→login_error_t 映射 |
| 0x39b30 | `qword_39B30` | node | 登录防爆树根节点 |
| 0x39b58 | `mutex` | pthread_mutex_t | login_guard 保护锁 |
| - | `g_groups` | array | 组数据缓存数组 |
| - | `g_groups_count` | int | 组数量 |

### 11.2 TLS 变量（线程局部存储）

| TLS 偏移 | 用途 |
|----------|------|
| qword_38F80 | AES 密钥缓存 (32字节) |
| qword_38FA0 | RSA 密钥文件 mtime 缓存 |
| qword_38FC8 | `ezpq` PostgreSQL 连接对象 |

---

## 12. 完整函数地址索引

### 12.1 入口/回调

| 地址 | 函数名 | 大小 |
|------|--------|------|
| 0x99e0 | `before_init` | 0x1b1 |
| 0xa160 | `onSessionEnd` | 0x25 |
| 0xa190 | `onTimerLoop` | 0x165 |
| 0xa7b0 | `handler` | 0xd61 |

### 12.2 RPC 处理函数

| 地址 | 函数名 | 大小 |
|------|--------|------|
| 0xd400 | `req_authtoken` | 0x373 |
| 0xd780 | `req_isadmin` | 0xd7 |
| 0xd860 | `do_login` | 0x20dd |
| 0xf940 | `req_active` | 0x4f |
| 0xf990 | `req_list_all_user_login_device` | 0x7c |
| 0xff40 | `req_kick_login_device` | 0x70a |
| 0x10950 | `req_check_new_user_name` | 0xe3 |
| 0x10ad0 | `req_check_new_group_name` | 0xe3 |
| 0x11420 | `req_check_password` | 0x2b2 |
| 0x11880 | `req_logout` | 0x55d |
| 0x11f20 | `req_unfreeze` | 0x19a |
| 0x14850 | `req_user_set_admin` | 0x56d |
| 0x14dc0 | `req_change_passwd` | 0x579 |
| 0x15700 | `req_del` | 0x363 |
| 0x16100 | `req_add1000` | 0xd7d |
| 0x16e80 | `req_add` | 0x1227 |
| 0x18a30 | `req_mod` | 0x16fa |
| 0x1a550 | `req_gdel` | 0x2c9 |
| 0x1abf0 | `req_gadd` | 0x302 |
| 0x1b440 | `req_gmod` | 0x466 |
| 0x1b8b0 | `req_gdelusers` | 0xe34 |
| 0x1c6f0 | `req_gsetusers` | 0xbf1 |
| 0x1d2f0 | `req_gaddusers` | 0xeb4 |
| 0x1e440 | `req_token_login` | 0x10aa |
| 0x1f4f0 | `req_list` | 0x384 |
| 0x1f880 | `req_glist` | 0x1e9 |
| 0x1fa70 | `req_gusers` | 0x255 |
| 0x1fcd0 | `req_ginfo` | 0x28b |
| 0x1ff60 | `req_listug` | 0x402 |
| 0x20370 | `req_list_login_device` | 0xbed |
| 0x20f60 | `req_login` | 0xc09 |
| 0x21b70 | `req_2fa_login_verify` | 0x84f |
| 0x223c0 | `req_2fa_reset_passwd` | 0x8ab |
| 0x22c70 | `req_info` | 0x683 |

### 12.3 底层业务函数

| 地址 | 函数名 | 大小 | 描述 |
|------|--------|------|------|
| 0x9280 | `init_db` | 0x75c | 初始化 PostgreSQL 表 |
| 0x120c0 | `cache_passwd_group` | 0x25eb | 刷新 /etc/passwd, shadow, group 缓存 |
| 0x10e20 | `check_avail` | 0xc5 | 检查用户账户可用性 |
| 0x10ef0 | `check_password` (part.0) | 0x132 | PAM 密码验证 |
| 0x11050 | `user_change_passwd` | 0x29e | 修改系统密码 (chpasswd) |
| 0x15340 | `user_del` | 0x3b3 | 删除系统用户 |
| 0x15a70 | `user_add` | 0x683 | 创建系统用户 |
| 0x180b0 | `user_mod` | 0x980 | 修改系统用户 |
| 0xcf10 | `user_dbus_send_message` | 0x94 | 发送 D-Bus 信号 |
| 0x1e230 | `rpc_2fa_update_device` | 0x201 | 更新 2FA 受信任设备 |

### 12.4 Token 管理函数

| 地址 | 函数名 | 大小 | 描述 |
|------|--------|------|------|
| 0x9c90 | `clear_timeout_longtoken` | 0x4c1 | 清理过期长期 Token |
| 0x10820 | `auth_token_and_add_session` | 0x94 | Token 认证并建立会话 |
| 0x262f0 | `_Rb_tree::_M_erase` (token) | 0x1c6 | Token 树删除 |
| 0x264c0 | `_Rb_tree::_M_copy` (token) | 0xd3 | Token 树拷贝 |
| 0x26db0 | `unsafe_uid_token_map_del_token` | 0x136 | 按 uid 删除 token |
| 0x27060 | `del_user_main_session` | 0x110 | 删除主会话 |
| 0x27170 | `del_all_main_session_by_uid_token` | 0x163 | 按 uid+token 删除所有主会话 |
| 0x272e0 | `send_string_to_all_main_session_by_uid_token` | 0xf1 | 向 uid 的所有主会话发消息 |
| 0x273e0 | `is_this_token_have_main_session` | 0xe2 | 检查 token 是否有主会话 |
| 0x274d0 | `get_main_session_count` | 0x34 | 获取主会话数量 |
| 0x27510 | `query_token_by_session` | 0xad | 按 session 查询 token |
| 0x277d0 | `print_token_counter` | 0x292 | 打印 token 统计 |
| 0x27a70 | `get_token_by_long_token` | 0x75 | 按长期 token 查找短期 token |
| 0x29350 | `add_user_main_session` | 0x304 | 添加主会话 |

### 12.5 事件日志函数

| 地址 | 函数名 | 大小 |
|------|--------|------|
| 0x23d60 | `send_event_log<...>` (LoginSucc/LoginFail) | 0x4b1 |
| 0x24220 | `send_event_log<...>` (各类事件) | 0x4bc |
| 0x246e0 | `send_event_log<...>` (DelUser 等) | 0x463 |

---

## 13. 调用示例（trimrpc 协议）

### 13.1 服务路由说明

`user.hdl` 由 `com.trim.main` 加载，**不是独立的 RPC 服务**：

| 项目 | 值 |
|------|-----|
| Broker 注册的服务名 | `com.trim.main` |
| UDS 路径 | `/run/trim_srv.socket` |
| `req` 字段格式 | `"user.<method>"` （**不含** `com.trim.main.` 前缀） |

> **重要**: RPC Broker 中不存在 `com.trim.user` 服务。必须通过 `com.trim.main` 或直连 UDS 调用。

### 13.2 使用 trimrpc 脚本调用

由于 `report-trimrpc-202603041034.py` 的 `build_rpc_request()` 会将 `req` 构造为 `"{service}.{method}"`，
通过标准模式传入 `service=com.trim.main, method=user.info` 会生成 `req="com.trim.main.user.info"`（**错误！handler 会解析失败**）。

**正确做法 — 使用 `--raw --uds` 直连模式**:

```bash
# 无需认证的方法

# active (心跳/保活)
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.active","pid":1,"reqid":"0000000000000001"}}'

# 登录
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.login","pid":1,"reqid":"0000000000000002",
    "user":"admin","password":"xxx","stay":1,
    "deviceName":"trimrpc-py","deviceType":"cli"}}'

# tokenLogin (长期Token登录)
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.tokenLogin","pid":1,"reqid":"0000000000000003",
    "token":"<base64_long_token>","did":"<device_id>"}}'

# authToken (Session Token 认证)
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.authToken","pid":1,"reqid":"0000000000000004",
    "si":"<session_id>","token":"<base64_token>","active":true,"main":true}}'

# add1000 (初始用户创建，无需认证)
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.add1000","pid":1,"reqid":"0000000000000005",
    "user":"admin","password":"admin123"}}'

# 2FA 登录验证
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.2fa.loginVerify","pid":1,"reqid":"0000000000000006",
    "accessToken":"<token>","emailCode":"<code>"}}'

# 2FA 密码重置
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.2fa.resetPassword","pid":1,"reqid":"0000000000000007",
    "code":"<2fa_code>","newPassword":"newpass123"}}'

# 需要登录的方法（需先通过 login 获取 token，再通过 authToken 建立会话后调用）

# 获取用户信息
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.info","pid":1,"reqid":"0000000000000010"}}'

# 用户列表（管理员）
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.list","pid":1,"reqid":"0000000000000011"}}'

# 添加用户（管理员）
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.add","pid":1,"reqid":"0000000000000012",
    "user":"newuser","password":"pass123","setAdmin":false,
    "groups":["mygroup"],"email":"user@example.com"}}'

# 删除用户（管理员）
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.del","pid":1,"reqid":"0000000000000013",
    "user":"olduser"}}'

# 修改密码
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.changePassword","pid":1,"reqid":"0000000000000014",
    "user":"admin","password":"oldpass","newPassword":"newpass"}}'

# 组列表（管理员）
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.groupList","pid":1,"reqid":"0000000000000015"}}'

# 登出
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.logout","pid":1,"reqid":"0000000000000016"}}'

# 查看登录设备
python3 report-trimrpc-202603041034.py --raw --uds /run/trim_srv.socket \
  '{"data":{"req":"user.listLoginDevice","pid":1,"reqid":"0000000000000017"}}'
```

### 13.3 `req` 格式对照表

| 正确 `req` 值 | 错误 `req` 值 | 说明 |
|---------------|---------------|------|
| `"user.active"` | `"com.trim.main.user.active"` | handler 只剥离第一个 `.` 前的内容 |
| `"user.login"` | `"com.trim.user.login"` | Broker 中不存在 com.trim.user |
| `"user.info"` | `"com.trim.main.user.info"` | 剥离后变成 `"trim.main.user.info"` 不匹配 |
| `"user.2fa.loginVerify"` | — | 2FA 方法用 `"2fa."` 作为子前缀 |
