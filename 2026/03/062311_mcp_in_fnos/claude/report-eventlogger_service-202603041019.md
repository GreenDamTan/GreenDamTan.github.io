# eventlogger_service 逆向分析报告

**分析日期**: 2026-03-04
**二进制文件**: `eventlogger_service`
**来源**: fnOS 1.1.15
**架构**: x86_64 (ELF)
**函数总数**: 1462+

---

## 1. 服务概述

`eventlogger_service` 是 fnOS 的事件日志服务，负责系统事件日志的记录、查询、导出、归档，以及告警蜂鸣器管理和调试日志收集。

- **服务名称**: `eventlogger`
- **DBus/RPC 名称**: `com.trim.eventlogger`
- **RPC UUID**: `EECC6649-92CB-4246-A76A-F14358F13C07`
- **RPC 服务名**: `logger service`
- **Unix Socket 路径**: `/run/trim_eventlogger`
- **事件日志 Socket**: `busi_eventlogger` / `sys_eventlogger`

---

## 2. 通信架构总览

### 2.1 提供的接口（API Handlers）

服务通过 `framework::Application::RegisterHandler` 注册了 **13 个 API Handler**，分为三类：

#### EventLogSvr（事件日志，6个）

| Handler 名称 | 处理函数 | 地址 | 功能说明 |
|---|---|---|---|
| `common.list` | `EventLogSvr::OnList` | `0xb63d0` | 查询事件日志列表 |
| `common.clear` | `EventLogSvr::OnClear` | `0xb3c80` | 清除事件日志 |
| `common.export` | `EventLogSvr::OnExport` | `0xb3eb0` | 导出事件日志 |
| `common.archive` | `EventLogSvr::OnArchiveSet` | `0xb5b90` | 设置日志归档策略 |
| `common.archive.get` | `EventLogSvr::OnArchiveGetOpt` | `0xb6050` | 获取归档配置 |
| `common.moduleList` | `EventLogSvr::OnListModules` | `0xb6b90` | 列出日志模块 |

#### DebuglogManager（调试日志，2个）

| Handler 名称 | 处理函数 | 地址 | 功能说明 |
|---|---|---|---|
| `debuglog.copyStart` | `DebuglogManager::OnDebuglogCopyStart` | `0x8a4f0` | 开始复制调试日志 |
| `debuglog.copyStop` | `DebuglogManager::OnDebuglogCopyStop` | `0x8bc50` | 停止复制调试日志 |

#### AlertMgr（告警管理，5个）

| Handler 名称 | 处理函数 | 地址 | 功能说明 |
|---|---|---|---|
| `alert.setBeepEvents` | `AlertMgr::OnSetBeepEvents` | - | 设置蜂鸣器告警事件 |
| `alert.getBeepEvents` | `AlertMgr::OnGetBeepEvents` | - | 获取蜂鸣器告警事件 |
| `alert.getBeepReasons` | `AlertMgr::OnGetBeepResons` | - | 获取蜂鸣器告警原因 |
| `alert.muteBeeper` | `AlertMgr::OnMuteBeeper` | - | 静音蜂鸣器 |
| `alert.getSupportedBeepEvents` | `AlertMgr::OnGetSupportedBeepEvents` | - | 获取支持的蜂鸣器事件类型 |

### 2.2 调用的外部服务（出站通信）

#### RPC 调用

| 目标服务 | 调用方 | 方法 | 说明 |
|---|---|---|---|
| `com.trim.resmon` | `AlertMgr::proxy` (0x49a50) | 转发 alert.* 请求 | 代理转发告警相关请求到资源监控服务 |
| `com.trim.main` | `NotificationSender::SendNotification` (0xd64e0) | `sendNotify` | 发送系统通知到主服务 |

#### DBus 订阅

| 目标服务 | 信号名称 | 回调函数 | 说明 |
|---|---|---|---|
| `com.trim.i18n` | `*eChanged` (localeChanged) | `on_dbus_lang_change_callback` (0xcbe20) | 监听系统语言变更事件 |

#### HTTP 调用

| URL | 方法 | Socket | 说明 |
|---|---|---|---|
| `http://localhost/sac/i18n/v1/config` | GET | `/var/run/trim_sac.socket` | 通过 SAC 获取 i18n 语言配置 |

### 2.3 通信架构图

```
                          ┌──────────────────────┐
                          │  eventlogger_service  │
                          │  com.trim.eventlogger │
                          │  UUID: EECC6649-...   │
                          └──────────┬───────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼─────────┐  ┌────────▼────────┐  ┌─────────▼─────────┐
    │   EventLogSvr     │  │  DebuglogManager│  │    AlertMgr       │
    │  6 API Handlers   │  │  2 API Handlers │  │  5 API Handlers   │
    │  SQLite 数据库     │  │  文件复制管理    │  │  蜂鸣器告警管理   │
    └─────────┬─────────┘  └─────────────────┘  └────────┬──────────┘
              │                                           │
    ┌─────────▼─────────┐                      ┌─────────▼──────────┐
    │  NotificationSender│                     │  AlertMgr::proxy   │
    │  → com.trim.main   │                     │  → com.trim.resmon │
    │  method: sendNotify │                     │  转发 alert.* 请求 │
    └────────────────────┘                      └────────────────────┘

         ┌──────────────────────────┐
         │  DBus: com.trim.i18n    │
         │  信号: localeChanged     │ ──→ SysLang::subscriber_dbus_msg
         └──────────────────────────┘

         ┌──────────────────────────┐
         │  HTTP via Unix Socket    │
         │  /var/run/trim_sac.socket│ ──→ GET /sac/i18n/v1/config
         └──────────────────────────┘
```

---

## 3. 关键函数分析

### 3.1 trimlogger::LoggerApp::Start (0xcf480)

**功能**: 服务主入口函数，负责注册所有 RPC 服务和 API Handler，初始化子系统。

**关键步骤**:
1. 注册 RPC 服务（UUID + 名称）
2. 注册 13 个 API Handler（common.*, debuglog.*, alert.*）
3. 设置服务参数：PatchRevision=0, AllowedConnections=100, NetworkWorker=2
4. 根据 `hardware_concurrency()` 设置线程数
5. 调用 `EventLogSvr::Init()` 初始化事件日志系统
6. 调用 `Application::Start()` 启动服务

**反汇编节选**:
```asm
; 注册 RPC 服务
cf49f  lea     rsi, aEecc664992cb42_0  ; "EECC6649-92CB-4246-A76A-F14358F13C07"
cf4b3  lea     rsi, aLoggerService    ; "logger service"
cf4cb  call    framework::Application::RegisterRpcService

; 注册 common.list Handler
cf569  lea     rsi, aCommonList       ; "common.list"
cf551  lea     rdx, trimlogger::EventLogSvr::OnList
cf581  call    sub_CC420              ; RegisterHandler

; 注册 common.clear Handler
       lea     rsi, aCommonClear      ; "common.clear"
       lea     rdx, trimlogger::EventLogSvr::OnClear
```

### 3.2 trimlogger::LoggerApp::LoggerApp (0xcc4c0)

**功能**: 构造函数，创建 Application 实例和子组件。

```
framework::Application::Application(this, "eventlogger", 0, 1);
trimlogger::EventLogSvr::EventLogSvr(this + 552);      // offset +0x228
trimlogger::DebuglogManager::DebuglogManager(this + 704); // offset +0x2C0
```

### 3.3 trimlogger::EventLogSvr::Init (0xb6eb0)

**功能**: 初始化事件日志服务器。

**关键步骤**:
1. 初始化 `i18n::SysLang` 单例并更新当前语言
2. 创建 `SignalEvent` 对象用于事件通知
3. 启动工作线程处理事件循环
4. 调用 `GetLastRebootEvent()` 获取上次重启事件
5. 通过 `LoggerDb::NewSysbootLog()` 记录系统启动日志

**伪代码**:
```c
void EventLogSvr::Init() {
    SysLang::get();  // 初始化 SysLang 单例 (pthread_once)
    SysLang::update_current_lang(instance);  // 更新当前语言

    SignalEvent *sig = new SignalEvent();  // 创建事件通知
    this->signal_event = sig;

    std::thread worker(event_loop_func, this);  // 启动事件循环线程
    this->worker_thread = worker;

    LogDao reboot_event = GetLastRebootEvent();
    if (reboot_event.valid) {
        LoggerDb::get()->NewSysbootLog(reboot_event);  // 记录重启日志
    }
}
```

### 3.4 trimlogger::i18n::SysLang::subscriber_dbus_msg (0xcb0c0)

**功能**: 订阅 DBus 语言变更信号。

**关键步骤**:
1. 构建 DBus 信号名称（包含 `eChanged` 后缀，完整名可能是 `localeChanged`）
2. 创建信号回调函数 `on_dbus_lang_change_callback`
3. 调用 `framework::dbus::SubscribeSignal(path, "com.trim.i18n", callbacks)`
4. 失败时记录错误日志

**回调函数 on_dbus_lang_change_callback (0xcbe20)**:
```c
void on_dbus_lang_change_callback(const char* json_msg) {
    spdlog::info("subscriber lang change event: {}", json_msg);
    PPJson::Document doc;
    if (doc.parse(json_msg)) {
        const char* newLang = doc["newLanguage"].getString();
        if (newLang) {
            lingual::SetLocale(newLang);  // 更新本地语言设置
        }
    }
}
```

### 3.5 trimlogger::i18n::SysLang::update_current_lang (0xca9a0)

**功能**: 通过 HTTP 请求获取当前系统语言配置。

**关键步骤**:
1. 默认初始化语言为 `zh-CN`
2. 通过 libcurl 发起 HTTP GET 请求:
   - URL: `http://localhost/sac/i18n/v1/config`
   - Unix Socket: `/var/run/trim_sac.socket`
3. 失败时记录: `"failed to get system lang conf: {}"`

### 3.6 trimlogger::AlertMgr::AlertMgr (0x48ea0)

**功能**: AlertMgr 构造函数，初始化 RPC 客户端。

**关键步骤**:
1. 创建 `trimrpc::RpcClient`（UUID: `EECC6649-92CB-4246-A76A-F14358F13C07`，服务名: `com.trim.eventlogger`）
2. 调用 `RpcClient::ApplyPermission("com.trim.resmon")` 申请权限
3. 成功: 设置 `this->initialized = 1`
4. 失败: 记录 `"new rpc client error"` 或 `"Failed to apply permissions: {}"`

**伪代码**:
```c
AlertMgr::AlertMgr() {
    this->initialized = 0;
    this->rpc_client = new trimrpc::RpcClient(
        "EECC6649-92CB-4246-A76A-F14358F13C07",
        "com.trim.eventlogger"
    );
    if (this->rpc_client) {
        int ret = rpc_client->ApplyPermission("com.trim.resmon");
        if (ret == 0) {
            this->initialized = 1;
        } else {
            spdlog::error("Failed to apply permissions: {}", ret);
        }
    } else {
        spdlog::error("new rpc client error");
    }
}
```

### 3.7 trimlogger::AlertMgr::proxy (0x49a50)

**功能**: RPC 代理函数，将 alert.* 请求转发到 `com.trim.resmon` 服务。

**关键步骤**:
1. 加锁（pthread_mutex_lock）
2. 检查 RPC 客户端是否已初始化，未初始化则尝试申请权限
3. 从请求 JSON 中获取 `data` 字段
4. 创建 `trimrpc::Request` 目标为 `com.trim.resmon`，action 为传入参数
5. 遍历请求参数，排除 `req` 和 `reqid`，复制其他参数到新请求
6. 调用 `RpcClient::Call()` 发送 RPC 请求
7. 成功: 获取响应并通过 `Response::Send()` 转发回原调用者
8. 失败: 返回错误码 `100010013`，记录 `"call {} ret {} errno: {}"`

### 3.8 trimlogger::NotificationSender::SendNotification (0xd64e0)

**功能**: 向 `com.trim.main` 服务发送系统通知。

**关键步骤**:
1. 创建 `trimrpc::RpcClient`（UUID 同上，服务名 `com.trim.eventlogger`）
2. 申请 `com.trim.main` 服务权限
3. 创建 `trimrpc::Request` 到 `com.trim.main`，方法名 `sendNotify`
4. 设置请求参数：
   - `uid` = 0
   - `category` = 0
   - `level` = 0
   - `title` = "" (空)
   - `from` = "log-center"
   - `data.eventId` = 第一个参数（事件ID）
   - `data.PATH` = 第二个参数（事件路径）
5. 调用 `RpcClient::Call()` 发送请求
6. 成功: 记录 `"Succeed to send notification!"`
7. 失败: 记录 `"Failed to send notification, ret:{}!"`

---

## 4. 关键类结构

### 4.1 trimlogger::LoggerApp
- 继承 `framework::Application`
- 构造时名称为 `"eventlogger"`
- 包含 `EventLogSvr` (offset +0x228) 和 `DebuglogManager` (offset +0x2C0)

### 4.2 trimlogger::EventLogSvr
- 6 个 API Handler（list/clear/export/archive/archive.get/moduleList）
- 使用 `SignalEvent` 进行事件通知
- 工作线程处理事件循环 (`EventLoop`)
- 通过 `LoggerDb` 操作 SQLite 数据库
- 通过 `NotificationSender` 发送系统通知

### 4.3 trimlogger::DebuglogManager
- 2 个 API Handler（debuglog.copyStart/copyStop）
- 管理调试日志的复制任务
- `MonitorTasks` 方法监控任务进度
- 支持 FTP/NFS/SMB/WebDAV 等协议

### 4.4 trimlogger::AlertMgr（单例）
- 5 个 API Handler（alert.*）
- 内部通过 `proxy()` 方法将请求转发到 `com.trim.resmon`
- 自身不处理告警逻辑，只做代理转发

### 4.5 trimlogger::LoggerDb
- SQLite 数据库封装
- 主要方法: `NewLog`, `NewSysbootLog`, `QueryLog`, `ClsLog`, `Export`, `FlushLog`, `Vacuum`
- 支持归档设置 (`ArchiveSetting`)
- 通过 `get()` 获取单例实例

### 4.6 trimlogger::i18n::SysLang
- i18n 语言支持
- 通过 HTTP 从 SAC 获取语言配置
- 订阅 DBus 信号监听语言变更
- 默认语言: `zh-CN`

### 4.7 trimlogger::NotificationSender
- 每次调用创建新的 `RpcClient`（非长连接）
- 向 `com.trim.main` 发送 `sendNotify` 请求
- 参数中 `from` 固定为 `"log-center"`

---

## 5. 关键字符串

| 地址 | 字符串 | 用途 |
|---|---|---|
| `0xe13f0` | `com.trim.eventlogger` | 本服务的 RPC/DBus 名称 |
| `0xe13a0` | `com.trim.resmon` | 资源监控服务（RPC 调用目标） |
| `0xe8cd7` | `com.trim.i18n` | i18n 服务（DBus 订阅目标） |
| `0xe98a8` | `com.trim.main` | 主服务（RPC 通知目标） |
| `0xe7b7a` | `/run/trim_eventlogger` | Unix Socket 路径 |
| `0xe8cbe` | `/var/run/trim_sac.socket` | SAC 通信 Socket |
| `0xe8d38` | `http://localhost/sac/i18n/v1/config` | i18n 配置 HTTP 端点 |
| - | `EECC6649-92CB-4246-A76A-F14358F13C07` | RPC 服务 UUID |
| - | `sendNotify` | 通知 RPC 方法名 |
| - | `log-center` | 通知来源标识 |
| - | `newLanguage` | DBus 语言变更信号中的字段名 |

---

## 6. 依赖库

- **trimrpc**: fnOS 自研 RPC 框架 (RpcClient, Request, Response)
- **framework**: fnOS 应用框架 (Application, Request, Response, SQLite, dbus::SubscribeSignal)
- **PPJson**: JSON 解析库 (Value, MutValue, Document, MutDocument)
- **spdlog/SPDLOG**: 日志记录框架
- **libcurl**: HTTP 客户端 (curl_easy_*)
- **lingual**: i18n/本地化库 (InitWithLocale, SetLocale)
- **fmt**: 格式化库 (v9)
- **SQLite3**: 数据库

---

## 7. IDA 操作记录

### 7.1 添加的注释

| 函数 | 地址 | 注释数量 |
|---|---|---|
| `LoggerApp::Start` | `0xcf480` | 17 条 |
| `EventLogSvr::Init` | `0xb6eb0` | 7 条 |
| `SysLang::subscriber_dbus_msg` | `0xcb0c0` | 4 条 |
| `SysLang::update_current_lang` | `0xca9a0` | 9 条 |
| `AlertMgr::AlertMgr` | `0x48ea0` | 9 条 |
| `AlertMgr::proxy` | `0x49a50` | 12 条 |
| `NotificationSender::SendNotification` | `0xd64e0` | 17 条 |
| 辅助函数 | 多个 | 4 条 |

**总计**: 79 条注释

### 7.2 重命名的函数

| 原名 | 新名 | 地址 | 说明 |
|---|---|---|---|
| `sub_46460` | `std_string_construct_from_cstr` | `0x46460` | std::string 构造函数 |
| `sub_D3AA0` | `std_string_construct_from_cstr_2` | `0xD3AA0` | std::string 构造函数（内联副本）|
| `sub_C7C00` | `dbus_signal_callback_manager` | `0xC7C00` | DBus 信号回调生命周期管理 |
| `sub_CBE20` | `on_dbus_lang_change_callback` | `0xCBE20` | DBus 语言变更回调处理函数 |

---

## 8. 总结

`eventlogger_service` 是一个中等复杂度的 fnOS 服务，主要功能是事件日志管理。其通信架构如下：

1. **对外提供** 13 个 API 接口，覆盖日志查询/清除/导出/归档、调试日志、告警蜂鸣器管理
2. **出站 RPC 调用** 到 `com.trim.resmon`（告警代理）和 `com.trim.main`（通知发送）
3. **DBus 订阅** `com.trim.i18n` 的语言变更信号
4. **HTTP 调用** 通过 SAC Unix Socket 获取 i18n 配置
5. **本地存储** 使用 SQLite 数据库持久化事件日志

值得注意的是，AlertMgr 的 5 个 API Handler 实质上是代理，全部通过 `proxy()` 方法转发到 `com.trim.resmon` 服务处理。NotificationSender 每次发送通知都会新建 RpcClient 连接，这是一个非长连接的设计模式。
