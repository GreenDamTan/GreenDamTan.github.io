# eventlogger_service trimrpc 分析报告

> 分析目标: eventlogger_service (fnOS 事件日志服务)
> 分析时间: 2026-03-04 17:19
> 工具: IDA Pro + MCP

---

## 1. 服务概览

`eventlogger_service` 是 fnOS 的事件日志管理服务，基于 `libframework.so` 框架构建。
通过 trimrpc 协议对外提供事件日志的查询、清除、导出、归档、调试日志管理和蜂鸣器告警等功能。

### 1.1 服务注册信息

来自 `LoggerApp::Start()` @ `0xcf4ae`:

```cpp
// 0xcf4ae
sub_CC420(v35, "EECC6649-92CB-4246-A76A-F14358F13C07");  // UUID
sub_CC420(v33, "logger service");                          // 服务显示名
framework::Application::RegisterRpcService(this, v33, v35);
```

| 属性 | 值 |
|---|---|
| **服务 ID** | `com.trim.eventlogger` |
| **UUID** | `EECC6649-92CB-4246-A76A-F14358F13C07` |
| **显示名称** | `logger service` |
| **UDS 路径** | `/run/trim_app_cgi/eventlogger` |
| **Broker 注册类型** | `0` (AppCgi / RpcPacket 协议) |
| **最大连接数** | 100 (`SetAllowedConnections(0x64)` @ `0xcfd6d`) |
| **网络工作线程** | 2 (`SetNetworkWorker(2)` @ `0xcfd7a`) |
| **业务线程数** | `hardware_concurrency() > 1 ? 2 : 1` @ `0xcfd95` |

### 1.2 主要模块

| 类名 | 职责 | 关键地址 |
|---|---|---|
| `trimlogger::LoggerApp` | 应用主类，注册所有 Handler | `Start()` @ `0xcf58f` |
| `trimlogger::EventLogSvr` | 事件日志核心处理 | `Init()` @ `0xb6eb0` |
| `trimlogger::LoggerDb` | SQLite 数据库操作层 | `get()` @ `0x7b450` |
| `trimlogger::AlertMgr` | 蜂鸣器/告警管理 (代理到 resmon) | `proxy()` @ `0x49a50` |
| `trimlogger::DebuglogManager` | 调试日志文件管理 | `OnDebuglogCopyStart()` @ `0x8a4f0` |
| `trimlogger::Archiver` | 日志归档处理器 | `HandleDbRecord()` @ `0x725b0` |
| `trimlogger::BusiEventLog` | 业务事件日志处理 | `HandleEventLog()` @ `0xabfa0` |
| `trimlogger::Configuration` | 事件分类配置管理 | singleton @ `instance_` |

---

## 2. RPC 方法注册表

所有方法在 `LoggerApp::Start()` @ `0xcf58f` 中通过 `framework::Application::RegisterHandler()` 注册。

### 2.1 完整方法列表

| # | 方法名 | Handler 函数 | 地址 | 注册地址 | 说明 |
|---|---|---|---|---|---|
| 1 | `common.list` | `EventLogSvr::OnList` | `0xb63d0` | `0xcf58f` | 分页查询事件日志 |
| 2 | `common.clear` | `EventLogSvr::OnClear` | `0xb3c80` | `0xcf625` | 清除事件日志 |
| 3 | `common.export` | `EventLogSvr::OnExport` | `0xb3eb0` | `0xcf6bb` | 导出事件日志到文件 |
| 4 | `common.archive` | `EventLogSvr::OnArchiveSet` | `0xb5b90` | `0xcf751` | 设置日志归档策略 |
| 5 | `common.archive.get` | `EventLogSvr::OnArchiveGetOpt` | `0xb6050` | `0xcf7e7` | 获取当前归档配置 |
| 6 | `common.moduleList` | `EventLogSvr::OnListModules` | `0xb6b90` | `0xcf87d` | 列出日志模块分类 |
| 7 | `debuglog.copyStart` | `DebuglogManager::OnDebuglogCopyStart` | `0x8a4f0` | `0xcf936` | 开始收集调试日志 |
| 8 | `debuglog.copyStop` | `DebuglogManager::OnDebuglogCopyStop` | `0x8bc50` | `0xcf9cc` | 停止收集调试日志 |
| 9 | `alert.setBeepEvents` | `AlertMgr::OnSetBeepEvents` | `0xd0440` | `0xcfa93` | 设置蜂鸣器告警事件 |
| 10 | `alert.getBeepEvents` | `AlertMgr::OnGetBeepEvents` | `0xd03c0` | `0xcfb38` | 获取蜂鸣器告警事件 |
| 11 | `alert.getBeepReasons` | `AlertMgr::OnGetBeepResons` | `0xd0340` | `0xcfbdd` | 获取蜂鸣器响铃原因 |
| 12 | `alert.muteBeeper` | `AlertMgr::OnMuteBeeper` | `0xd02c0` | `0xcfc82` | 静音蜂鸣器 |
| 13 | `alert.getSupportedBeepEvents` | `AlertMgr::OnGetSupportedBeepEvents` | `0xd0240` | `0xcfd27` | 获取支持的蜂鸣器事件类型 |

### 2.2 注册代码节选

```
; LoggerApp::Start() @ 0xcf58f — 以 common.list 为例

0xcf532  call    operator new(0x18u)           ; 分配 std::_Bind 对象
0xcf545  mov     [rax+10h], r14                ; this->eventlog_svr_ (offset+552)
0xcf558  mov     qword ptr [rax+8], 0
0xcf570  mov     [rbp-58h], rax
0xcf575  mov     qword ptr [rax], offset EventLogSvr::OnList  ; 绑定回调
0xcf581  call    sub_CC420(v33, "common.list") ; 构造方法名字符串
0xcf58f  call    framework::Application::RegisterHandler
```

---

## 3. 方法详细分析

### 3.1 `common.list` — 分页查询事件日志

**Handler:** `EventLogSvr::OnList()` @ `0xb63d0` (size: 0x7b1)

#### 请求参数

| 参数名 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `pageSize` | int | ✅ | 每页记录数 |
| `page` | int | ✅ | 页码 (从1开始) |
| `module` | int | ✅ | 模块分类 ID (-1=全部, 0=login, 1=transfer, 2=storage, 3=system) |
| `level` | int | ❌ | 日志级别过滤 (默认 -1=全部) |
| `locale` | string | ❌ | 国际化语言 |

#### 反编译节选

```cpp
// EventLogSvr::OnList @ 0xb63d0 — 参数解析

v4 = PPJson::Value::operator[](request + 152, "data");    // 0xb6452

// 必需参数检查: pageSize
sub_AE2C0(v75, "pageSize");                                // 0xb6489
v7 = v4["pageSize"];                                        // 0xb6497
v9 = framework::Request::CheckParams(v6, v8, v75);         // 0xb64c1
// → 缺少则返回 errno=100000002, errmsg="pageSize"

// 必需参数检查: page
sub_AE2C0(v75, "page");                                    // 0xb6526
v11 = framework::Request::CheckParams(v54, v8, v75);       // 0xb654b

// 读取参数值
pageSize = v4["pageSize"].asInt(0);                         // 0xb6588→0xb6599
page     = v4["page"].asInt(0);                             // 0xb65a6→0xb65b7

// 可选参数: level (默认 -1)
if (v4["level"].isNumber()) {                               // 0xb65c7→0xb65d0
    level = v4["level"].asInt(0);                           // 0xb65e8→0xb65f9
} else {
    level = -1;                                             // 0xb6ad8
}

// 必需参数检查: module
sub_AE2C0(v75, "module");                                   // 0xb660e
v19 = framework::Request::CheckParams(v17, v8, v75);       // 0xb663e

// 执行查询
trimlogger::LoggerDb::QueryLog(&v66, module, pageSize, page, level);  // 0xb669f
```

#### 响应格式

```json
{
  "data": {
    "result": "succ",
    "reqid": "...",
    "data": {
      "total": 1234,
      "rows": [
        {
          "id": 12345678,
          "level": 2,
          "module": 3,
          "eventtm": 1709571234,
          "username": "admin",
          "content": "User admin logged in from 192.168.1.100"
        }
      ]
    }
  }
}
```

响应构建 (@ `0xb66c5`):
```cpp
// 0xb66c5  构建响应 JSON
result_obj = response["data"][s_result_key_];   // 获取 data.result 对象
result_obj["total"] = v66;                       // 0xb66ee  总记录数

// 遍历查询结果 (@ 0xb69eb - 0xb6aa1)
for each row:
    doc = new PPJson::MutDocument();
    row_obj["level"]    = row.level;             // 0xb6a09
    row_obj["module"]   = row.module;            // 0xb6a23
    row_obj["id"]       = row.id;                // 0xb6a44
    row_obj["eventtm"]  = row.eventtm;           // 0xb6a6b
    row_obj["username"] = row.username ?: "system";  // 0xb6a96, 默认 "system" @ 0xb6a7f
    row_obj["content"]  = row.content;           // 0xb6831

    // 内容国际化处理 (@ 0xb6863 - 0xb6991)
    if (content has template) {
        lingual::GetMessage(locale, template, params) → localized_content;
        row_obj["content"] = localized_content;
    }

    result_obj["rows"].append(row_obj);          // 0xb69ae
```

#### 调用示例

```bash
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"common.list","params":{"pageSize":10,"page":1,"module":-1}}'
```

---

### 3.2 `common.clear` — 清除事件日志

**Handler:** `EventLogSvr::OnClear()` @ `0xb3c80` (size: 0x230)

#### 请求参数

| 参数名 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `module` | int | ✅ | 要清除的模块分类 ID |
| `level` | int | ✅ | 要清除的日志级别 |

#### 反编译节选

```cpp
// EventLogSvr::OnClear @ 0xb3c80

// 参数解析
module = data["module"].asInt(0);               // 0xb3df1→0xb3e02
level  = data["level"].asInt(0);                // 0xb3e0e→0xb3e22

// 执行清除
trimlogger::LoggerDb::get(&db);                 // 0xb3e24
result = trimlogger::LoggerDb::ClsLog(db, level, module);  // 0xb3e3d

if (result) {
    response.status = 2;  // 成功                // 0xb3e70
    framework::Response::Send(response);         // 0xb3e7e
} else {
    framework::Response::SetFailed(response, 100000102);  // 0xb3e55
    framework::Response::Send(response);         // 0xb3e5d
}
```

#### 调用示例

```bash
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"common.clear","params":{"module":-1,"level":-1}}'
```

---

### 3.3 `common.export` — 导出事件日志

**Handler:** `EventLogSvr::OnExport()` @ `0xb3eb0` (size: 0x1cd7)

这是最大的 Handler 函数 (7383 字节)，负责将日志导出为 CSV 文件。

#### 请求参数

| 参数名 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `module` | int | ✅ | 模块分类 |
| `level` | int | ✅ | 日志级别 |
| `output` | string | ✅ | 输出目录路径 |
| `locale` | string | ❌ | 国际化语言 |

#### 调用示例

```bash
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"common.export","params":{"module":-1,"level":-1,"output":"/tmp"}}'
```

---

### 3.4 `common.archive` — 设置归档策略

**Handler:** `EventLogSvr::OnArchiveSet()` @ `0xb5b90` (size: 0x4b1)

#### 请求参数

| 参数名 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `filePath` | string | ✅ | 归档文件存储路径 |
| `switch` | int | ✅ | 开关 (0=关闭, 1=开启) |
| `sizeGt` | int | ❌ | 日志大小阈值 (超过此大小触发归档) |
| `dateUnit` | int | ❌ | 时间单位 |
| `dateBefore` | int | ❌ | 日期阈值 (归档此日期之前的日志) |

#### 反编译节选

```cpp
// EventLogSvr::OnArchiveSet @ 0xb5b90

// 参数解析
switch_val = data["switch"].asInt(0);           // 0xb5d6e→0xb5d7f
filePath   = data["filePath"].asString(0);      // 0xb5d8c→0xb5da0

// 路径验证 (仅 switch=1 时)
if (switch_val) {
    if (access(filePath, F_OK)) {               // 0xb5f3f
        SetFailed(100000103);  // 路径不存在    // 0xb5fe0
        return;
    }
    if (!is_vol_path_writeable(filePath)) {      // 0xb5f64
        SetFailed(100000104);  // 路径不可写    // 0xb5ff8
        return;
    }
    if (!utils::PathAccessable(filePath)) {      // 0xb5f80
        SetFailed(100000106);  // 路径不可访问  // 0xb5f9f
        return;
    }
}

// 可选参数
if (data["sizeGt"].isNumber())
    setting.sizeGt = data["sizeGt"].asInt(0);       // 0xb5dfa→0xb5e0f
if (data["dateUnit"].isNumber())
    setting.dateUnit = data["dateUnit"].asInt(0);    // 0xb5e42→0xb5e57
if (data["dateBefore"].isNumber())
    setting.dateBefore = data["dateBefore"].asInt(0); // 0xb5e84→0xb5e99

// 写入数据库
trimlogger::LoggerDb::NewArchiveSetting(db, &setting);  // 0xb5eba

// 成功后通知归档线程
pthread_mutex_lock(this + 8);                   // 0xb5ee9
std::condition_variable::notify_one(this + 48); // 0xb5eff
pthread_mutex_unlock(this + 8);                 // 0xb5f07
```

#### 错误码

| 错误码 | 含义 |
|---|---|
| `100000102` | 数据库操作失败 |
| `100000103` | 归档路径不存在 |
| `100000104` | 归档路径不可写 |
| `100000106` | 归档路径不可访问 |

---

### 3.5 `common.archive.get` — 获取归档配置

**Handler:** `EventLogSvr::OnArchiveGetOpt()` @ `0xb6050` (size: 0x379)

无需请求参数。

#### 反编译节选

```cpp
// EventLogSvr::OnArchiveGetOpt @ 0xb6050

trimlogger::LoggerDb::get(&db);                             // 0xb60d9
trimlogger::LoggerDb::QueryArchiveSetting(&setting);        // 0xb60e5

// 构建响应
result["switch"]   = setting.switch_val;                     // 0xb6119
result["filePath"] = setting.filePath;                       // 0xb6139

// 路径可用性检查
if (setting.switch_val) {
    accessible = utils::PathAccessable(filePath);            // 0xb6279
    writable   = is_vol_path_writeable(filePath);            // 0xb637b
    validPath  = accessible && writable;
} else {
    validPath  = 1;
}
result["validPath"] = validPath;                             // 0xb6160

// 可选字段 (仅在设置存在时返回)
if (setting.has_sizeGt)    result["sizeGt"]     = setting.sizeGt;     // 0xb6194
if (setting.has_dateUnit)  result["dateUnit"]   = setting.dateUnit;   // 0xb6242
if (setting.has_dateBefore) result["dateBefore"] = setting.dateBefore; // 0xb61c6
```

#### 响应格式

```json
{
  "data": {
    "result": "succ",
    "data": {
      "switch": 0,
      "filePath": "/vol1/archive",
      "validPath": 1,
      "sizeGt": 500,
      "dateUnit": 1,
      "dateBefore": 30
    }
  }
}
```

---

### 3.6 `common.moduleList` — 列出日志模块分类

**Handler:** `EventLogSvr::OnListModules()` @ `0xb6b90` (size: 0x314)

无需请求参数。

#### 反编译节选

```cpp
// EventLogSvr::OnListModules @ 0xb6b90

// 遍历 MESSAGE_CATEGORY_ARRAY (共5个分类: -1, 0, 1, 2, 3)
for (i = -1; i <= 4; i++) {                     // 0xb6c3f
    doc = new PPJson::MutDocument();
    item["id"] = i;                              // 0xb6c4e→0xb6c58

    if (i == -1) {
        item["label"] = "(全部)";                // 0xb6e1a
    } else {
        // 取分类名称
        category_name = MESSAGE_CATEGORY_ARRAY[i]; // 0xb6c72
        // 国际化翻译
        lingual::GetLocaleText(&label, "log-center", category_name); // 0xb6cf8
        item["label"] = label;                    // 0xb6d0c
    }
    result["rows"].append(item);
}
```

#### 模块分类 (MESSAGE_CATEGORY_ARRAY)

从二进制数据确认 (@ `0xe4b7d`):

| ID | 名称 | 说明 |
|---|---|---|
| -1 | (全部) | 所有分类 |
| 0 | `login` | 登录相关 |
| 1 | `transfer` | 文件传输 |
| 2 | `storage` | 存储相关 |
| 3 | `system` | 系统事件 |

```
; 内存布局 @ 0xe4b7d
0xe4b7d: 6c 6f 67 69 6e 00                   ; "login\0"
0xe4b83: 74 72 61 6e 73 66 65 72 00          ; "transfer\0"
0xe4b8c: 73 74 6f 72 61 67 65 00             ; "storage\0"
0xe4b94: 73 79 73 74 65 6d 00                ; "system\0"
```

#### 响应格式

```json
{
  "data": {
    "result": "succ",
    "data": [
      {"id": -1, "label": "全部"},
      {"id": 0,  "label": "登录"},
      {"id": 1,  "label": "文件传输"},
      {"id": 2,  "label": "存储"},
      {"id": 3,  "label": "系统"}
    ]
  }
}
```

---

### 3.7 `debuglog.copyStart` — 开始收集调试日志

**Handler:** `DebuglogManager::OnDebuglogCopyStart()` @ `0x8a4f0` (size: 0x1751)

这是另一个大型 Handler (5969 字节)，负责收集系统各组件的调试日志并打包。

#### 请求参数

| 参数名 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `input` | string | ✅ | 输入来源路径 |
| `output` | string | ✅ | 输出目标路径 |
| `outputDir` | string | ❌ | 输出目录 |
| `srvType` | string | ❌ | 服务类型过滤 |

### 3.8 `debuglog.copyStop` — 停止收集调试日志

**Handler:** `DebuglogManager::OnDebuglogCopyStop()` @ `0x8bc50` (size: 0x325)

#### 请求参数

| 参数名 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `taskId` | string | ✅ | 任务 ID (从 copyStart 返回) |

---

### 3.9 `alert.*` — 蜂鸣器告警管理 (代理模式)

所有 5 个 `alert.*` 方法均为**代理模式**——它们不在 eventlogger 内部处理，
而是通过 `AlertMgr::proxy()` 转发到 `com.trim.resmon` 服务。

#### 代理架构

```
Client → eventlogger (alert.xxx) → AlertMgr::proxy() → com.trim.resmon (alert.xxx)
                                         ↑
                                    复制请求参数
                                    (排除 req, reqid)
```

#### AlertMgr::proxy() 反编译节选

```cpp
// AlertMgr::proxy @ 0x49a50

// 首次调用时申请 resmon 权限
if (!this->initialized) {                               // 0x49aea
    std_string_construct_from_cstr(v111, "com.trim.resmon");  // 0x49afb
    ret = trimrpc::RpcClient::ApplyPermission(rpc_client, v111); // 0x49b13
    if (ret) {
        // 权限申请失败 → 日志: "Failed to apply permissions: {}"
        SetFailed(100010013);                            // 0x4a126
        return;
    }
    this->initialized = 1;                               // 0x49b42
}

// 构建转发请求
data = request->json["data"];                            // 0x49b47
trimrpc::Request req("com.trim.resmon", action, "");     // 0x49ba0→0x49bb6

// 复制参数 (跳过 req 和 reqid)
for (iter = data.begin(); iter != data.end(); iter++) {  // 0x49cbd
    key = iter.key();
    if (strcmp(key, "req") && strcmp(key, "reqid")) {     // 0x49c71
        req.params()[key] = data[key];                   // 0x49c89→0x49ca8
    }
}

// 发送 RPC 调用
ret = trimrpc::RpcClient::Call(rpc_client, &req);        // 0x49ce2
if (ret == 0) {
    // 成功: 转发响应
    response = trimrpc::RpcClient::response();           // 0x49cf4
    resp["data"][result_key] = response;                 // 0x49d37
    Send(resp);                                          // 0x49d4c
} else {
    // 失败: 返回错误
    // 日志: "call {} ret {} errno: {}" @ 0x49f78
    SetFailed(100010013);                                // 0x49e71
    Send(resp);
}
```

#### 代理方法列表

| 方法名 | 本地 Handler 地址 | 转发到 resmon |
|---|---|---|
| `alert.setBeepEvents` | `0xd0440` | `alert.setBeepEvents` |
| `alert.getBeepEvents` | `0xd03c0` | `alert.getBeepEvents` |
| `alert.getBeepReasons` | `0xd0340` | `alert.getBeepReasons` |
| `alert.muteBeeper` | `0xd02c0` | `alert.muteBeeper` |
| `alert.getSupportedBeepEvents` | `0xd0240` | `alert.getSupportedBeepEvents` |

每个 Handler 结构相同 (以 OnGetSupportedBeepEvents 为例):
```cpp
// AlertMgr::OnGetSupportedBeepEvents @ 0xd0240
void OnGetSupportedBeepEvents(shared_ptr<Request> req) {
    proxy(req, "alert.getSupportedBeepEvents");  // 0xd026e — 直接代理
}
```

---

## 4. 数据库层 (LoggerDb)

### 4.1 LoggerDb 函数索引

| 函数名 | 地址 | Size | 说明 |
|---|---|---|---|
| `LoggerDb()` 构造 | `0x79620` | 0x741 | 初始化数据库连接 |
| `~LoggerDb()` 析构 | `0x7b1e0` | 0x26a | 关闭数据库 |
| `get()` | `0x7b450` | 0x10b | 获取单例 (shared_ptr) |
| `InitDatabase()` | `0x76a80` | 0x1f5 | 创建表和索引 |
| `QueryLog()` | `0x7be20` | 0x150c | 分页查询日志 (5388 字节) |
| `ClsLog()` | `0x7e7f0` | 0x982 | 清除日志 |
| `NewLog()` | `0x81410` | 0x4c4 | 插入新日志 |
| `NewLogBatch()` | `0x76a70` | 0x3 | 批量插入 (stub) |
| `NewSysbootLog()` | `0x7b560` | 0x8bc | 插入系统启动日志 |
| `Export()` | `0x7d570` | 0x1276 | 导出日志到文件 |
| `GetDBSize()` | `0x7d330` | 0x236 | 获取数据库大小 |
| `QueryArchiveSetting()` | `0x79d70` | 0x32a | 查询归档设置 |
| `NewArchiveSetting()` | `0x7ac60` | 0x57a | 保存归档设置 |
| `UpdateLastArchiveDate()` | `0x7a0a0` | 0xf8 | 更新最后归档日期 |
| `IterateRecords()` | `0x7a1a0` | 0x9a5 | 遍历记录 (用于归档) |
| `ClearRecordsBefore()` | `0x7ab50` | 0x56 | 清除指定时间之前的记录 |
| `QryRecordsBefore()` | `0x7abb0` | 0xa6 | 查询指定时间之前的记录数 |
| `FlushLog()` | `0x801f0` | 0xb0b | 刷新日志到磁盘 |
| `CheckLogscnt()` | `0x80d00` | 0x703 | 检查日志计数 |
| `Vacuum()` | `0x7f180` | 0xe64 | VACUUM 压缩数据库 |
| `Reset()` | `0x7fff0` | 0x1fc | 重置数据库 |

### 4.2 LogDao 数据结构

从 `OnList` 响应构建 (@ `0xb69eb`) 和 `HandleEventLog` (@ `0xabfa0`) 的字段访问模式推导:

```
struct LogDao {
    /* +0x00 */  std::string  name;         // 日志名称/标识
    /* +0x20 */  int          module;       // 模块分类 (login=0, transfer=1, storage=2, system=3)
    /* +0x50 */  std::string  username;     // 用户名
    /* +0x70 */  int64_t      eventtm;      // 事件时间戳 (unix timestamp)
    /* +0x80 */  std::string  template_str; // 消息模板 (用于 i18n)
    /* +0xA0 */  std::string  content;      // 原始内容 JSON
    /* +0xB8 */  int64_t      id;           // 数据库记录 ID
    /* +0xC0 */  int          level;        // 日志级别
};
// 每条记录大小: 200 字节 (0xC8), 从 OnList 遍历步长 v25 += 200 确认
```

### 4.3 ArchiveSetting 数据结构

从 `OnArchiveSet` (@ `0xb5b90`) 和 `OnArchiveGetOpt` (@ `0xb6050`) 推导:

```
struct ArchiveSetting {
    /* +0x00 */  int          switch_val;   // 归档开关 (0/1)
    /* +0x04 */  char         has_switch;   // 是否设置了 switch
    /* +0x08 */  std::string  filePath;     // 归档路径
    /* +0x28 */  int          sizeGt;       // 大小阈值
    /* +0x2C */  char         has_sizeGt;
    /* +0x30 */  int          dateUnit;     // 时间单位
    /* +0x34 */  char         has_dateUnit;
    /* +0x38 */  int          dateBefore;   // 日期阈值
    /* +0x3C */  char         has_dateBefore;
    /* +0x40 */  char         valid;        // 数据有效标志
};
```

---

## 5. 服务初始化流程

### 5.1 EventLogSvr::Init() @ `0xb6eb0`

```
Init()
  │
  ├─ [1] 初始化 i18n
  │   └─ SysLang::get()::instance → 获取语言单例
  │   └─ SysLang::update_current_lang()        // 0xb6f2f — 从 com.trim.i18n 获取语言
  │
  ├─ [2] 创建 SignalEvent (0x18 字节)
  │   └─ SignalEvent::SignalEvent()              // 0xb6f44 — 事件通知机制
  │
  ├─ [3] 启动工作线程
  │   └─ std::thread → EventLogSvr::EventLoop() // 0xb6fbb — 事件循环
  │
  └─ [4] 记录系统启动日志
      └─ GetLastRebootEvent()                    // 0xb6ff4
      └─ LoggerDb::NewSysbootLog()               // 0xb706f
```

### 5.2 EventLoop @ `0xb1e30` (size: 0x1bee)

事件循环是最大的函数之一 (7150 字节)，负责:
- 监听 SignalEvent 事件通知
- 处理 BusiEventLog 业务事件
- 执行归档检查 `CheckArchiveStatus()`
- 管理日志刷新 `FlushLog()`

---

## 6. BusiEventLog 事件处理

### 6.1 HandleEventLog() @ `0xabfa0` (size: 0xfaf)

此函数处理来自其他服务的事件日志 (非 RPC 直接调用)。支持两种事件格式:

#### 格式 1: 系统/内部事件 (a4=0)

从 JSON 中解析字段:
```cpp
eventId  = json["eventId"].asString();   // 0xac125 — 事件标识
from     = json["from"].asString();      // 0xac193 — 来源模块
datetime = json["datetime"].asInt64();   // 0xac1f1 — 时间戳
level    = json["level"].asInt();        // 0xac239 — 级别
uid      = json["uid"].asInt();          // 0xaca16 — 用户 ID
```

#### 格式 2: 业务事件 (a4=1)

```cpp
cat      = json["cat"].asInt();          // 0xac058 — 分类 ID
template = json["template"].asString();  // 0xac369 — 消息模板
uid      = json["uid"].asInt();          // 0xac673 — 用户 ID
user     = json["user"].asString();      // 0xac405 — 用户名 (可选)
level    = GetMessageLevel(from, template); // 0xac3bd — 从模板推导级别
```

最终调用:
```cpp
trimlogger::LoggerDb::get(&db);           // 0xac4e1
trimlogger::LoggerDb::NewLog(db, &logdao); // 0xac4fc
```

---

## 7. 错误码定义

| 错误码 | 含义 | 出现位置 |
|---|---|---|
| `100000002` | 必需参数缺失 | `framework::Request::CheckParams` 返回 |
| `100000004` | 方法不存在 / 无效请求 | framework 层通用错误 |
| `100000102` | 数据库操作失败 | `OnClear`, `OnArchiveSet`, `OnArchiveGetOpt` |
| `100000103` | 归档路径不存在 | `OnArchiveSet` @ `0xb5fe0` |
| `100000104` | 归档路径不可写 | `OnArchiveSet` @ `0xb5ff8` |
| `100000106` | 归档路径不可访问 | `OnArchiveSet` @ `0xb5f9f` |
| `100010013` | RPC 代理调用失败 | `AlertMgr::proxy` @ `0x49e71` |

---

## 8. 依赖服务

| 目标服务 | 用途 | 调用位置 |
|---|---|---|
| `com.trim.rpcbroker` | 服务注册与发现 | framework 自动处理 |
| `com.trim.resmon` | 蜂鸣器告警功能代理 | `AlertMgr::proxy()` @ `0x49a50` |
| `com.trim.i18n` | 国际化语言设置 | `SysLang::update_current_lang()` |

---

## 9. 完整函数地址索引

### EventLogSvr

| 地址 | 函数名 | 大小 |
|---|---|---|
| `0xae7c0` | `EventLogSvr::InitLang` | 0xcd |
| `0xae890` | `EventLogSvr::CreateWorkerThread` | 0x20f |
| `0xaeaa0` | `EventLogSvr::CheckArchiveStatus` | 0x1ba |
| `0xb1a10` | `EventLogSvr::~EventLogSvr` | 0xed |
| `0xb1b00` | `EventLogSvr::EventLogSvr` (构造) | 0x321 |
| `0xb1e30` | `EventLogSvr::EventLoop` | 0x1bee |
| `0xb3c80` | `EventLogSvr::OnClear` | 0x230 |
| `0xb3eb0` | `EventLogSvr::OnExport` | 0x1cd7 |
| `0xb5b90` | `EventLogSvr::OnArchiveSet` | 0x4b1 |
| `0xb6050` | `EventLogSvr::OnArchiveGetOpt` | 0x379 |
| `0xb63d0` | `EventLogSvr::OnList` | 0x7b1 |
| `0xb6b90` | `EventLogSvr::OnListModules` | 0x314 |
| `0xb6eb0` | `EventLogSvr::Init` | 0x2f7 |

### AlertMgr

| 地址 | 函数名 | 大小 |
|---|---|---|
| `0x48ea0` | `AlertMgr::AlertMgr` (构造) | 0xbac |
| `0x49a50` | `AlertMgr::proxy` | 0xf63 |
| `0xd0240` | `AlertMgr::OnGetSupportedBeepEvents` | 0x71 |
| `0xd02c0` | `AlertMgr::OnMuteBeeper` | 0x71 |
| `0xd0340` | `AlertMgr::OnGetBeepResons` | 0x71 |
| `0xd03c0` | `AlertMgr::OnGetBeepEvents` | 0x71 |
| `0xd0440` | `AlertMgr::OnSetBeepEvents` | 0x71 |

### DebuglogManager

| 地址 | 函数名 | 大小 |
|---|---|---|
| `0x87e90` | `DebuglogManager::RemoveDirectory` | 0x3d3 |
| `0x88270` | `DebuglogManager::DebuglogManager` (构造) | 0x3f2 |
| `0x88670` | `DebuglogManager::~DebuglogManager` | 0x94 |
| `0x88710` | `DebuglogManager::ParseDebuglogDir` | 0x1dd5 |
| `0x8a4f0` | `DebuglogManager::OnDebuglogCopyStart` | 0x1751 |
| `0x8bc50` | `DebuglogManager::OnDebuglogCopyStop` | 0x325 |
| `0x8bf80` | `DebuglogManager::MonitorTasks` | 0x1e48 |

### LoggerDb

| 地址 | 函数名 | 大小 |
|---|---|---|
| `0x76a80` | `LoggerDb::InitDatabase` | 0x1f5 |
| `0x79620` | `LoggerDb::LoggerDb` (构造) | 0x741 |
| `0x79d70` | `LoggerDb::QueryArchiveSetting` | 0x32a |
| `0x7a0a0` | `LoggerDb::UpdateLastArchiveDate` | 0xf8 |
| `0x7a1a0` | `LoggerDb::IterateRecords` | 0x9a5 |
| `0x7ab50` | `LoggerDb::ClearRecordsBefore` | 0x56 |
| `0x7abb0` | `LoggerDb::QryRecordsBefore` | 0xa6 |
| `0x7ac60` | `LoggerDb::NewArchiveSetting` | 0x57a |
| `0x7b450` | `LoggerDb::get` (单例) | 0x10b |
| `0x7b560` | `LoggerDb::NewSysbootLog` | 0x8bc |
| `0x7be20` | `LoggerDb::QueryLog` | 0x150c |
| `0x7d330` | `LoggerDb::GetDBSize` | 0x236 |
| `0x7d570` | `LoggerDb::Export` | 0x1276 |
| `0x7e7f0` | `LoggerDb::ClsLog` | 0x982 |
| `0x7f180` | `LoggerDb::Vacuum` | 0xe64 |
| `0x7fff0` | `LoggerDb::Reset` | 0x1fc |
| `0x801f0` | `LoggerDb::FlushLog` | 0xb0b |
| `0x80d00` | `LoggerDb::CheckLogscnt` | 0x703 |
| `0x81410` | `LoggerDb::NewLog` | 0x4c4 |

### 其他关键函数

| 地址 | 函数名 | 大小 |
|---|---|---|
| `0xabfa0` | `BusiEventLog::HandleEventLog` | 0xfaf |
| `0x725b0` | `Archiver::HandleDbRecord` | 0xa66 |
| `0x70d68` | `Archiver::GenerateFileName` | 0x50a |
| `0x950f0` | `FileTaskManager::QueryTaskStatus` | 0x26c |
| `0x9c070` | `FileTaskManager::QueryTaskStatus` (批量) | 0xca5 |
| `0xcf58f` | `LoggerApp::Start` (方法注册) | 0xbcb |

---

## 10. 实际调用示例

```bash
# 查询事件日志 (第1页, 每页10条, 全部模块)
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"common.list","params":{"pageSize":10,"page":1,"module":-1}}'

# 列出模块分类
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"common.moduleList"}'

# 获取归档配置
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"common.archive.get"}'

# 获取蜂鸣器事件 (代理到 resmon)
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"alert.getBeepEvents"}'

# 获取支持的蜂鸣器事件
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"alert.getSupportedBeepEvents"}'

# 获取蜂鸣器响铃原因
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"alert.getBeepReasons"}'

# 静音蜂鸣器
python3 report-trimrpc-202603041034.py \
  '{"service":"com.trim.eventlogger","method":"alert.muteBeeper"}'
```
