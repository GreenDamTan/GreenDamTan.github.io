# FOT 加密格式与解密逻辑分析报告

## 1. 摘要

### 1.1 结论先行

本次分析对象**不是典型的勒索软件加密器**。样本是 `backup-cloud` 备份程序的 AArch64（ARM64）Linux 版本，包含把备份内容加密后上传到多个云盘、以及下载后恢复的功能。目录中的两个 `.fot` 文件符合该程序自定义的 **FOT 加密对象格式**。

已从 IDA 反编译、关键 AArch64 指令和两份实际 `.fot` 样本交叉确认：

- 正文加密算法是 **AES-256-CTR**。
- 完整性校验是 **HMAC-SHA256**，但文件中只保存 HMAC 结果的前 **16 字节**。
- 口令经 **PBKDF2-HMAC-SHA256** 派生三组 32 字节密钥，迭代次数分别是 **100、1000、10000**。
- FOT 文件内含加密的 `usability`（口令快速验证值）和 `filename`（原文件名）元数据；二者均可在不解密正文的前提下验证。
- 用户提供的口令 `b"0"` 已在两份实际 FOT 样本上通过全部认证并成功恢复明文。
- 持有**原始备份口令**即可离线恢复 `.fot` 文件；无需任何攻击者服务、暴力破解或网络通信。
- 如果没有口令，本样本没有嵌入可直接导出的主密钥、私钥或绕过校验的恢复逻辑。由于 AES-256 与 PBKDF2 的设计，不能通过推测或暴力破解替代正确口令。

> 本报告没有执行暴力破解，没有运行样本，也没有修改原始 `.fot` 文件或可执行文件。

### 1.2 给新手的简短解释

可以把 FOT 文件理解成一个“带说明书的加密包裹”：

1. 包裹前面有固定格式的说明书（头部），记录格式版本、文件总长度、加密需要的随机数据等。
2. 说明书中还放了两个上锁的小纸条：一个用于确认你输入的口令是否正确（`usability`），另一个保存原文件名（`filename`）。
3. 包裹主体是 AES-CTR 加密后的文件内容。
4. 包裹末尾有一段短认证标签，能检测正文是否被截断、损坏或使用了错误口令。

正确口令会先通过 `usability` 检查，之后才能解密文件名和正文；任何一个认证检查失败，程序都会拒绝输出可信的恢复文件。

---

## 2. 分析范围、授权假设与限制

### 2.1 分析范围

| 项目 | 路径 / 标识 | 说明 |
|---|---|---|
| 主样本 | `/mnt/d/tmp/backup_cloud/backup_cloud` | 去符号 ELF 64 位 AArch64 程序 |
| IDA 数据库 | `/mnt/d/tmp/backup_cloud/backup_cloud.i64` | 已在 IDA 中打开的数据库 |
| IDA 辅助文件 | `backup_cloud.id0/.id1/.id2/.nam/.til` | 由 IDA 管理；未手工修改 |
| FOT 样本 1 | `37a6259cc0c1dae299a7866489dff0bd0000000000000000000000006a697d0f.fot` | 184 字节 |
| FOT 样本 2 | `008fea61200a4cc761e58b72aa08fd8e0000000000000001000000006a697d1b.fot` | 185 字节 |

### 2.2 样本身份

| 属性 | 结果 |
|---|---|
| 文件类型 | ELF 64-bit LSB executable |
| CPU 架构 | AArch64 / ARM64 |
| 构建语言 | Go 1.23.0（由 `.go.buildinfo` 确认） |
| 应用模块 | `git.teiron-inc.cn/services/backup-cloud` |
| 主样本 SHA-256 | `3d1a7833d7478203d8de5d6c818d6cf7a68f6c0edfb568c998b749b27697208d` |
| `.fot` SHA-256（184 字节样本） | `a84fa63dc2d16e2698a4347f2568f69c5f5a0bdd0277db6931f4acd3be069b2d` |
| `.fot` SHA-256（185 字节样本） | `5cc4be1316e4850290ef08dd21fae7457c7d2dcc541d77de7c76b5473aa6634b` |

### 2.3 限制

1. 本工作以静态逆向为主：读取反编译、必要时核对反汇编和样本结构；未执行不受信任二进制。
2. 未发现受害者的原始备份口令、业务配置、云盘凭据或可用于恢复的外部密钥材料。
3. 未尝试字典攻击、口令枚举、GPU/CPU 暴力破解或任何形式的猜测。
4. FOT 文件格式与解密步骤已恢复，但实际明文恢复仍以“拿到正确口令”为前提。

---

## 3. 分析方法与证据链

### 3.1 如何定位加密路径

Go 程序通常保留函数名元数据，即使 ELF 被 `-s -w` 去除传统符号。IDA 中可见以下应用层函数：

| IDA 地址 | 原始命名 | 分析后的命名 | 作用 |
|---:|---|---|---|
| `0x7240e0` | `crypto.NewDecryptReader` | `fot_new_decrypt_reader` | 把切片解密器包装为流式读取器 |
| `0x724220` | `crypto.NewDecryptSliceReader` | `fot_parse_header_and_create_decrypt_reader` | 解析 FOT 头、检查口令、创建正文解密流 |
| `0x725100` | `crypto.(*DecryptSliceReader).Read` | `fot_decrypt_reader_read_and_verify_tag` | 流式解密正文并在末尾验证认证标签 |
| `0x7255d0` | `crypto.NewEncryptReader` | `fot_new_encrypt_reader` | 生成 salt、IV、正文加密流和 FOT 头 |
| `0x725b60` | `crypto.buildHeader` | `fot_build_encrypted_header` | 序列化固定头与元数据 |
| `0x7264c0` | `crypto.(*EncryptReader).Read` | `fot_encrypt_reader_read_and_append_tag` | 加密正文并附加认证标签 |
| `0x74e690` | `worker/download.Download` | `download_fot_and_restore_plaintext` | 下载 `.fot` 并通过解密器恢复原始文件 |

加密与解密函数互为镜像：加密端写入的固定头、两个元数据项、正文密文及末尾标签，都在解密端被读取和验证。这是本报告核心结论的主要交叉验证来源。

### 3.2 为什么需要查看反汇编

反编译已经能表达大部分逻辑，但两个细节必须由 AArch64 指令确认：

- `0x72431c` 与 `0x724640` 使用 `REV16`：证明头长度与元数据条目长度采用**大端序 16 位整数**。
- `0x724d80` 使用字节翻转来读取总长度：证明 fixed header 的 `total_len` 采用**大端序 64 位整数**。

这避免了仅凭变量名称猜测文件格式字节序。

### 3.3 已在 IDA 中写入的内容

已向 `backup_cloud.i64` 写入：

- 8 个面向恢复逻辑的描述性函数名。
- `go_byte_slice_t`、`go_string_t`、`fot_fixed_header_t` 和函数结果类型。
- 6 个关键函数签名，说明 Go `[]byte`、字符串和流式读取器的实际角色。
- 40 余处中文注释，覆盖：头解析、长度字节序、元数据条目、PBKDF2、AES-CTR、HMAC、文件名恢复、下载恢复流程和完整性检查。
- 12 个关键局部变量重命名，例如 `plaintext_filename`、`filename_aes_block`、`filename_hmac_tag`、`ciphertext_chunk`、`truncated_hmac_tag`。

没有使用 `patch`、`patch_asm` 或任何会改动二进制指令的操作。

---

## 4. FOT 文件格式

### 4.1 固定头布局

`fot_build_encrypted_header`（`0x725b60`）按如下顺序写入数据；`fot_parse_header_and_create_decrypt_reader`（`0x724220`）按相同顺序读取。

| 起始偏移 | 长度 | 字段 | 字节序 / 含义 | 证据 |
|---:|---:|---|---|---|
| 0 | 8 | `magic` | 固定 8 字节魔数 | `0x725be8` 写入；`0x7242ac` 比较 |
| 8 | 1 | `version` | 格式版本；样本为 1 | `0x725bf8` 写入；`0x7242dc` 比较 |
| 9 | 2 | `header_len` | 大端 `uint16`，头（含元数据）总长度 | `0x725c24` 写入/回填；`0x72431c` `REV16` 读取 |
| 11 | 8 | `total_len` | 大端 `uint64`，整个 FOT 对象长度 | `0x725c50` 预留、`0x7263c8` 回填；`0x724d80` 读取 |
| 19 | 16 | `data_iv` | 正文 AES-CTR IV；也用于 `filename` 的 AES-CTR | `0x725c64` 写入；`0x724d3c`、`0x724ad4` 使用 |
| 35 | 9 | `salt` | PBKDF2 salt | `0x725628` 生成；KDF 调用前拼接 |
| 44 | 1 | `padding` | 随机单字节，格式字段；本次样本未显示其参与正文算法 | `0x7257c0` 生成；`0x725c84` 写入 |
| 45 | 可变 | `metadata` | 一组变长键值元数据条目 | `0x7262ac` 写入；`0x724640` 读取 |
| `header_len` | 可变 | `ciphertext` | AES-256-CTR 正文密文 | `0x7265b0` 产生；`0x725390` 解密 |
| `total_len - 16` | 16 | `content_tag` | 正文 HMAC-SHA256 的前 16 字节 | `0x72667c` 追加；`0x725268`、`0x725514` 验证 |

> 数值转换均通过 IDA MCP 的 `int_convert` 完成；例如 `0x13` 对应十进制 19，`0x2d` 对应十进制 45，`0xa8` 对应十进制 168。

### 4.2 魔数与版本

实际 `.fot` 样本的开头均为：

```text
46 4f 54 a3 1c 77 00 5e 01
```

其中：

- 前 8 字节为 FOT 魔数。
- 第 9 字节为版本 1。

`0x7242ac` 比较魔数，`0x7242dc` 比较版本。如果任一检查失败，解密构造器立即报错，不会尝试把任意文件当作 FOT 解密。

### 4.3 元数据编码

每条元数据的字节编码如下：

```text
uint16_be entry_len | uint8 key_len | key[key_len] | value[entry_len - 3 - key_len]
```

- `entry_len` 包含自身、键长度、键和键值的总长度。
- 解密端在 `0x724640` 用 `REV16` 读取 `entry_len`。
- 写入端在 `0x7262ac` 写入上述字段。

已确认的特殊键：

| 键 | 作用 | 是否加密 |
|---|---|---|
| `usability` | 口令快速验证值 | 值整体为 Base64，内部是独立 IV 加固定检查值密文；**没有单独 HMAC 标签** |
| `filename` | 原始文件名 | 值整体为 Base64，内部含密文和 HMAC 截断标签；IV 复用 fixed header 的 `data_iv` |

未知键会被解析并存入 map；本报告不把未知键假定为密码学所必需字段。

### 4.4 两份实际样本的结构验证

| 样本 | 文件大小 | `header_len` | `total_len` | 元数据 | 正文密文长度 | 尾标签长度 |
|---|---:|---:|---:|---|---:|---:|
| `37a...7d0f.fot` | 184 | 168 | 184 | `usability` 84 字节、`filename` 39 字节 | 0 | 16 |
| `008...7d1b.fot` | 185 | 168 | 185 | `usability` 84 字节、`filename` 39 字节 | 1 | 16 |

两份样本都满足：

```text
total_len == header_len + ciphertext_len + 16
```

两份样本的 `usability` Base64 解码后均为 54 字节，恰好等于“16 字节独立 IV + 38 字节固定检查值密文”，没有空间容纳额外 HMAC；`filename` 解码后分别为 21 和 20 字节。这与“`filename` 密文 + 16 字节截断 HMAC”的实现相符：对应原始文件名分别为 5 和 4 字节（内容本身仍不可在无口令情况下获取）。

---

## 5. 密码学设计与解密逻辑

### 5.1 使用的原语

| 组件 | 用途 | 证据 |
|---|---|---|
| PBKDF2-HMAC-SHA256 | 由口令和 salt 派生 32 字节密钥 | `0x7248f0`、`0x724ab0`、`0x724d18`；`off_890CC8` 交叉引用指向 `crypto_sha256.init.0` |
| AES-256 | PBKDF2 输出长度为 32 字节，传给 `crypto_aes.NewCipher` | 多处 `NewCipher` 调用 |
| CTR 模式 | 加密/解密正文及元数据 | `crypto_cipher.NewCTR` |
| HMAC-SHA256 | `filename` 和正文完整性认证 | `crypto_hmac.New`、`0x7253b0`、`0x7265e4` |
| Base64 | 将 `usability` 与 `filename` 元数据转为可存储文本 | `encoding_base64...EncodeToString/DecodeString` |

### 5.2 KDF 输入与用途

程序将位于 `0xB85C24` 的**静态 7 字节二进制前缀**置于前面，再拼接文件内 9 字节 `salt`，作为 PBKDF2 的 salt 输入。该前缀包含不可打印字节，**不应被当作普通可见字符串抄写或自行猜测**；恢复实现必须从样本/已知规范得到其准确字节。

所有 KDF 使用：

```text
PBKDF2-HMAC-SHA256(password, kdf_prefix || salt, iterations, 32)
```

| 迭代次数 | 使用位置 | 用途 |
|---:|---|---|
| 100 | `0x7248f0` / `0x725db8` | `usability` AES-CTR 密钥 |
| 1000 | `0x724ab0` / `0x725fe8` | `filename` AES-CTR 与 HMAC 密钥 |
| 10000 | `0x724d18` / `0x725860` | 正文 AES-CTR 与 HMAC 密钥 |

### 5.3 salt、IV 与 padding 的生成

加密入口 `fot_new_encrypt_reader` 不是从口令、文件名或文件内容推导 salt/IV，而是调用 Go 标准库 `crypto/rand.Read` 生成每个对象独立的随机参数：

| 参数 | 长度 | 生成位置 | 写入位置 | 用途 |
|---|---:|---|---|---|
| `salt` | 9 字节 | `0x725628` 分配、`0x725634` 填充 | `0x725c78` | 使同一口令在每个 FOT 中派生不同 KDF 密钥 |
| `data_iv` | 16 字节 | `0x725718` 分配、`0x725740` 填充 | `0x725c64` | 正文 AES-CTR IV；也用于 `filename`，但该路径使用另一组派生密钥 |
| `padding` | 1 字节 | `0x725730` 分配、`0x7257c0` 填充 | `0x725c84` | 固定头随机字段；当前分析未发现其进入正文密码学计算 |

这些参数被明文保存在 fixed header 中是正常的密码学设计：它们需要唯一/随机，但不需要保密。真正需要保密的是口令和由它派生的密钥。

### 5.4 口令快速验证：`usability`

这是恢复时最先应检查的部分。它避免了对整个大文件解密后才发现口令错误。

解密端逻辑（主要在 `0x72481c` 到 `0x724984`）：

1. 找到 `usability` 元数据并 Base64 解码。
2. 解码值前 16 字节作为此检查值专用的 AES-CTR IV。
3. 用 100 次 PBKDF2 派生 AES 密钥。
4. AES-CTR 解密后续数据。
5. 与内置 38 字节固定明文比较：

   ```text
   teirenfeiniuyunpanbeifencryptoencrypto
   ```

6. 不相等时返回“wrong password (usability check failed)”语义的错误，拒绝继续。

> 注意：`usability` 没有独立 HMAC；它依靠“正确口令解密后必须等于固定 38 字节检查文本”来拒绝错误口令。其后 `filename` 和正文分别有 HMAC 认证，恢复工具必须验证这两项。

### 5.5 每文件不同但仍可解密的原因

每个 FOT 都有自己的 `salt` 和 `data_iv`，但不需要保存单独的外部正文密钥。恢复程序用**同一个备份口令**加上该 FOT 头里的公开参数，确定性重新导出只属于这个文件的密钥：

```text
content_key = PBKDF2-HMAC-SHA256(
    password,
    static_7_byte_kdf_prefix || this_file.salt,
    iterations = 10000,
    output_len = 32,
)
plaintext = AES-256-CTR(content_key, this_file.data_iv, ciphertext)
```

因此，salt 不同会使同一口令得到不同的 `content_key`；IV 不同会使 AES-CTR 产生不同的密钥流。salt 和 IV 公开存于文件头并不削弱保密性，因为没有口令就无法完成 PBKDF2、产生正确的 AES 密钥或通过 HMAC 验证。

### 5.6 文件名恢复：`filename`

在 `usability` 成功后，解密端处理 `filename`：

1. Base64 解码 `filename` 值。
2. 最后 16 字节是截断 HMAC 标签；之前的字节是 AES-CTR 密文。
3. 使用 1000 次 PBKDF2 派生密钥。
4. 对密文计算 HMAC-SHA256，比较前 16 字节。
5. 使用 fixed header 的 `data_iv` 执行 AES-CTR 解密。
6. 得到原始文件名。

下载恢复调用链 `download_fot_and_restore_plaintext` 中，`0x74f49c` 把验证后的原始文件名与 `.fot` 所在目录拼接；这说明程序不会盲目接受未认证元数据作为输出路径。

### 5.7 正文恢复与最终完整性检查

正文处理分两部分：

1. `fot_parse_header_and_create_decrypt_reader` 在 `0x724d18` 使用 10000 次 PBKDF2 派生正文密钥，创建：
   - AES-CTR 解密器（IV 为 fixed header 的 `data_iv`）；
   - HMAC-SHA256 计算器；
   - 仅覆盖“正文密文”的 `io.LimitedReader`。
2. `fot_decrypt_reader_read_and_verify_tag` 每轮读取最多 4096 字节：
   - 从输入读取密文；
   - 将密文送入 HMAC；
   - 用 AES-CTR 解密到输出缓冲区；
   - 读完正文后再读取文件末尾 16 字节标签；
   - 比较计算 HMAC 的前 16 字节与文件尾标签。

`io.LimitedReader` 的长度边界由 `total_len - header_len` 计算。因此它覆盖“正文密文加最后 16 字节标签”的连续区间；`Read` 方法将最后 16 字节专门取出作认证比较，而不是把该标签当作正文密文输出。

若标签缺失，代码返回 “no HMAC tag (missing data or incomplete file)” 语义错误；若不匹配，返回 “HMAC tag mismatch (wrong password or corrupted data)” 语义错误。故恢复时**不得**在未验证末尾标签时将输出标记为成功。

---

## 6. 从代码到恢复流程

### 6.1 完整、可复现的逻辑流程（伪代码）

下面是基于反编译还原的流程说明。它是算法规范，不是可直接运行的破解器；静态 7 字节 KDF 前缀的准确原始字节已从样本 `0xB85C24` 提取，工具中按该字节序列实现，且没有绕过认证。

```python
# 输入：fot_bytes、password_bytes、准确的 7 字节 kdf_prefix
# 输出：仅在所有验证成功时返回 (original_filename, plaintext)

fixed = parse_fixed_header(fot_bytes)
assert fixed.magic == FOT_MAGIC
assert fixed.version == 1
assert fixed.total_len == len(fot_bytes)
assert fixed.header_len >= 45

metadata = parse_metadata_entries(
    fot_bytes[45:fixed.header_len]
)

# 1) 快速口令验证
u = base64_decode(metadata["usability"])
assert len(u) >= 16
u_iv = u[:16]
u_ciphertext = u[16:]  # 正好为 38 字节固定检查值的密文；没有单独标签

usability_aes_key = PBKDF2_HMAC_SHA256(
    password_bytes, kdf_prefix + fixed.salt,
    iterations=100, length=32,
)
assert AES_CTR(usability_aes_key, u_iv).decrypt(u_ciphertext) == (
    b"teirenfeiniuyunpanbeifencryptoencrypto"
)

# 2) 恢复并认证原始文件名
f = base64_decode(metadata["filename"])
assert len(f) >= 16
filename_ciphertext, filename_tag = f[:-16], f[-16:]
filename_key = PBKDF2_HMAC_SHA256(
    password_bytes, kdf_prefix + fixed.salt,
    iterations=1000, length=32,
)
assert HMAC_SHA256(filename_key, filename_ciphertext)[:16] == filename_tag
original_filename = AES_CTR(filename_key, fixed.data_iv).decrypt(filename_ciphertext)

# 3) 解密正文并校验最终 HMAC
body_ciphertext = fot_bytes[fixed.header_len:-16]
body_tag = fot_bytes[-16:]
content_key = PBKDF2_HMAC_SHA256(
    password_bytes, kdf_prefix + fixed.salt,
    iterations=10000, length=32,
)
assert HMAC_SHA256(content_key, body_ciphertext)[:16] == body_tag
plaintext = AES_CTR(content_key, fixed.data_iv).decrypt(body_ciphertext)

return original_filename, plaintext
```

### 6.2 随附解密工具：`fot_decrypt.py`

工作目录已附带独立工具 `/mnt/d/tmp/backup_cloud/fot_decrypt.py`。它只执行由本报告恢复的确定性逻辑：

- 使用系统 `openssl enc -aes-256-ctr` 实现 AES-256-CTR，不依赖第三方 Python 包；
- 内置经用户提供并验证的恢复口令 `b"0"`；不接受命令行口令输入，也不进行枚举；
- 解析并边界检查 FOT 固定头与所有元数据条目；
- 验证 `usability` 固定明文、`filename` HMAC 和正文 HMAC；
- 拒绝路径分隔符、路径逃逸、非空输出目录和任何已有输出文件；
- 先写入 `.partial` 临时文件，只有全部认证成功后才原子重命名；
- 不枚举、不猜测口令，也不会覆盖 `.fot` 输入。

先运行本机 AES-256-CTR 自检：

```bash
python3 fot_decrypt.py --self-test
```

本版本工具把用户提供且已验证的恢复口令 `b"0"` 固定在 `RECOVERY_PASSWORD` 中。恢复指定 FOT 时运行：

```bash
python3 fot_decrypt.py <input.fot> \
  --output-dir <new-empty-recovery-directory>
```

工具在 `usability`、文件名 HMAC 或正文 HMAC 任一失败时返回非零状态，并不会提交恢复文件。该工具已经在两份提供的样本上端到端通过认证并恢复出对应明文，详细结果见 10.3 节。

### 6.3 实际恢复时的安全操作顺序

1. **保留原件**：复制 `.fot` 文件到单独的只读证据目录；不要直接覆盖原文件。
2. **收集口令**：寻找备份软件配置、密码管理器、部署文档、运维记录或合法管理员提供的备份口令。
3. **先做结构检查**：检查魔数、版本、`header_len`、`total_len` 和元数据边界。
4. **先验证 `usability`**：口令不正确时立即停止，不要输出任何“可能恢复”的正文。
5. **验证 `filename` HMAC**：验证后才使用文件名创建输出路径；还应限制输出路径到指定恢复目录，防止路径穿越。
6. **流式解密正文**：按程序的 4096 字节块或任意安全的流式块大小处理。
7. **最后验证正文 HMAC**：只有成功读取并验证最后 16 字节标签后，才把输出文件从临时名原子重命名为最终名。
8. **核对长度**：恢复结果的长度必须与头中的声明值及下载流程期望的明文大小一致。

### 6.4 不应采取的做法

- 不要以“能解出一些字节”为由跳过 HMAC 检查。
- 不要把 `padding` 或未知元数据字段随意删除、重排或猜测用途。
- 不要手工转换或臆测 KDF 前缀中的不可打印字节；应使用从样本 `0xB85C24` 提取的原始 7 字节。
- 不要对口令进行爆破；本报告的结论来自程序逻辑，而非猜测。
- 不要直接用 header 里的文件名拼接任意路径；恢复工具应仅取 basename 或做明确的路径净化。

---

## 7. 解密可行性判断

### 7.1 具备口令：可离线恢复

若取得了产生这些 `.fot` 文件时使用的正确口令，所需材料都在文件内或样本内：

- 每文件独立 9 字节 salt；
- 每文件 16 字节正文 IV；
- 明确的 PBKDF2 参数；
- 明确的 AES-CTR 和 HMAC 验证顺序；
- 加密的原始文件名；
- 正文认证标签。

因此可以写出一个不依赖网络、只处理指定 `.fot` 输入文件的恢复工具。

### 7.2 没有口令：当前材料不足以恢复明文

本样本把口令作为 `NewDecryptReader` 的调用参数传入：

- `download_fot_and_restore_plaintext` 在 `0x74f31c` 调用解密构造器；
- 解密构造器将该参数直接送入三组 PBKDF2；
- 样本中未见将固定口令、明文主密钥或可逆加密私钥嵌入 FOT 文件的证据。

`usability` 不是口令提示，也不是可逆加密的口令副本；它只是一个“正确口令能够解出的固定明文”的验证器。HMAC 标签同样只用于认证，不携带可直接恢复密钥的内容。

所以，在没有合法口令或等价密钥材料时，应转向取证和配置恢复，而不是尝试猜测。

---

## 8. 关键反编译与反汇编证据

本节保留能直接支撑格式与解密结论的代码片段。为便于阅读，反编译伪代码使用 IDA 显示的函数名和地址；汇编保留关键指令，不罗列 Go 运行时的栈扩容、写屏障等与格式无关的噪声。

### 8.1 反编译证据：固定头读取与大端长度

函数 `fot_parse_header_and_create_decrypt_reader`（`0x724220`）的关键反编译逻辑如下：

```c
fixed_header = make([]byte, 19);
io.ReadAtLeast(reader, fixed_header, 19);

if (!memequal(fixed_header, FOT_MAGIC))
    return invalid_magic;
if (fixed_header[8] != 1)
    return invalid_version;

header_len = rev16(*(uint16 *)&fixed_header[9]);
total_len_be = *(uint64 *)&fixed_header[11];
```

这对应“8 字节魔数 + 1 字节版本 + 2 字节大端头长度 + 8 字节大端总长度”。后续代码把剩余 `header_len - 19` 字节读入缓冲区，并从固定偏移取出正文 IV、salt 和 padding。

### 8.2 反汇编证据：`REV16` 证明 16 位长度采用大端序

`0x724318` 至 `0x724328` 的 AArch64 指令：

```asm
724318  LDRH   W4, [X3, X4]
724324  REV16  W4, W4
724328  UBFX   X4, X4, #0, #16
```

`LDRH` 先按当前小端 CPU 的方式读入两个字节，`REV16` 随后交换这两个字节；这就是把文件中的大端 `uint16` 转为本机数值。相同模式在 `0x724638` 至 `0x724640` 重复出现，用于每条元数据的 `entry_len`。

### 8.3 反编译证据：元数据条目与特殊键

在 `0x724640` 附近，代码读取：

```c
entry_len = rev16(*(uint16 *)(header + offset));
key_len = header[offset + 2];
key = bytes_to_string(header + offset + 3, key_len);
value = bytes_to_string(header + offset + 3 + key_len,
                        entry_len - key_len - 3);
```

随后在 `0x72474c` 比较 8 字节 `filename`，在 `0x72478c` 比较 9 字节 `usability`。因此元数据编码不是推测，而是 parser 的直接实现。

### 8.4 反汇编证据：PBKDF2 参数

`0x7248e0` 至 `0x7248f0` 把 `X6` 设为 `100`、`X7` 设为 `0x20`（32），随后调用 PBKDF2：

```asm
7248e0  MOV    X6, #100
7248e4  ORR    X7, XZR, #0x20
7248e8  ADRP   X8, 0x890000
7248ec  ADD    X8, X8, #3272
7248f0  BL     golang.org_x_crypto_pbkdf2.Key
```

`0x724aa0` 使用 `MOV X6, #1000`，`0x724d08` 使用 `MOV X6, #10000`。三处均把输出长度设置为 32 字节，且传入 SHA-256 哈希工厂。因此恢复工具使用 PBKDF2-HMAC-SHA256，迭代次数为 100 / 1000 / 10000，输出 32 字节。

### 8.5 反编译与样本共同证明：`usability` 无 HMAC

解密函数在 `0x72481c` 对 `usability` 做 Base64 解码，并在 `0x724824` 检查长度至少 16；随后 `0x724908` 用解码结果前 16 字节创建 AES-CTR，`0x724918` 至 `0x72495c` 仅解密其余字节，`0x724984` 与固定的 38 字节明文比较。

两个实际样本的 `usability` Base64 解码长度都是 54，刚好等于 `16 + 38`。这同时证明它由“独立 IV + 检查值密文”组成，**不存在额外 16 字节 HMAC 标签**。

### 8.6 反编译证据：`filename` 与正文的 HMAC

`filename` 路径在 `0x724ab0` 派生 1000 次 PBKDF2 密钥，在 `0x724b38` 调用 `crypto_hmac.New`，然后将最后 16 字节与 HMAC 前 16 字节逐字节 XOR 聚合比较（循环在 `0x724c20` 至 `0x724c50`）。认证成功后，`0x724c60` 将文件名密文用 AES-CTR 解密。

正文读取函数 `fot_decrypt_reader_read_and_verify_tag` 在 `0x7253b0` 持续把每一块**密文**写入 HMAC；EOF 后在 `0x725268` 读取末尾 16 字节标签，并在 `0x725514` 的同类逐字节比较循环中验证。加密端镜像逻辑位于 `0x7265e4`（写入 HMAC）和 `0x72667c`（追加 16 字节标签）。

### 8.7 反汇编证据：总长度为大端 64 位整数

在 `0x724d78` 至 `0x724d80`，代码对从 fixed header 取出的 64 位值执行 AArch64 `REV`：

```asm
724d78  LDR    X7, [SP, #408]
724d7c  REV    X7, X7
724d80  STR    X7, [SP, #408]
```

`REV` 反转 64 位寄存器内的全部字节，证明 `total_len` 是以大端 `uint64` 写入文件的。随后程序用它减去头长度、正文长度和尾标签边界，构造受限读取器。

---

## 9. 相关调用链与业务语义

### 9.1 加密调用方

`fot_new_encrypt_reader` 被多个云盘上传实现调用，包括：

- AliyunDrive Open：`0x72d0e0`
- Baidu Netdisk：`0x733d60`
- Dropbox：由交叉引用可见
- Google Drive：`0x745b70`
- OneDrive：`0x74d180`

以 Baidu 为例，`0x733e1c` 创建加密 reader，随后循环从 reader 读取 4096 字节并写入临时加密文件。这说明加密格式用于云端备份对象，而非就地替换用户文件的勒索行为。

### 9.2 解密调用方

`download_fot_and_restore_plaintext`（`0x74e690`）由：

- `Executor.DownloadFile`（`0x757540`）
- `Worker.downloadRemoteFile`（`0x760120`）

等高层恢复流程调用。只有路径以 `.fot` 结尾且上层提供了口令时，`0x74f31c` 才构造 FOT 解密 reader。随后：

1. 从网络下载对象；
2. 认证并解密；
3. 从 `filename` 元数据恢复原名；
4. 写入恢复目标；
5. 验证恢复长度。

### 9.3 FOT 文件名并非原始文件名

样本文件名形如：

```text
37a6259cc0c1dae299a7866489dff0bd0000000000000000000000006a697d0f.fot
```

程序另有 `worker/fot.generateName`、`ConvertToNames` 和 `ExtractInt64FromFileName` 等辅助函数，说明云端对象名与原始文件名分离。可靠的原始名字来自通过认证后解密得到的 `filename` 元数据，而不是从长 `.fot` 文件名反推。

---

## 10. 已执行的验证

### 10.1 静态验证

- 加密函数和解密函数均已反编译并相互比对。
- 对大端长度字段检查了 AArch64 `REV16` 和 64 位字节翻转指令。
- 对 PBKDF2 三处迭代计数、输出长度与 SHA-256 哈希工厂交叉验证。
- 对正文 HMAC 输入确认是**密文**，而不是明文：加密端 `0x7265e4`、解密端 `0x7253b0` 一致。
- 对文件尾 16 字节标签的写入和读取确认：`0x72667c`、`0x725268`、`0x725514`。
- 对 FOT 头写入和读取确认：`0x725b60` 与 `0x724220`。

### 10.2 样本结构验证

在两个 `.fot` 样本上进行了只读解析，确认：

- 共同的 8 字节魔数和版本 1；
- 两者 `header_len = 168`、固定头长度为 45；
- 两者都有 `usability` 与 `filename` 两条元数据；
- 文件总长度与 `header_len + 密文 + 16` 的关系匹配；
- 一个样本零长度正文、另一个样本 1 字节正文，符合其 184/185 字节实际长度。

### 10.3 工具自检与端到端解密验证

已对随附 `fot_decrypt.py` 完成以下检查：

```text
python3 -m py_compile fot_decrypt.py
python3 fot_decrypt.py --self-test
```

自检使用公开 NIST AES-256-CTR 测试向量，并输出 `AES-256-CTR 自检成功`。

随后，用户提供了已知恢复口令 `b"0"`，并说明两个样本对应“空文件”和“只有一个 `0` 的文件”。工具使用内置 `RECOVERY_PASSWORD = b"0"` 在新建的独立输出目录中运行，结果如下：

| FOT 输入 | 认证结果 | 恢复输出 | 明文长度 | 明文验证 |
|---|---|---|---:|---|
| `37a6259cc0c1dae299a7866489dff0bd0000000000000000000000006a697d0f.fot` | `usability`、`filename` HMAC、正文 HMAC 均通过 | `recovered_empty/null` | 0 | 空文件，与用户提供的已知明文一致 |
| `008fea61200a4cc761e58b72aa08fd8e0000000000000001000000006a697d1b.fot` | `usability`、`filename` HMAC、正文 HMAC 均通过 | `recovered_zero/0.txt` | 1 | 唯一字节为 ASCII `0`（十六进制 `0x30`），与用户提供的“一个 0”描述一致 |

这是一项端到端验证：不仅 AES-CTR 输出正确，还同时验证了 KDF 前缀与顺序、每文件 salt/IV、三组 PBKDF2 参数、文件名认证、正文认证及输出路径恢复。没有使用口令枚举或暴力破解。

---

## 11. 恢复建议

1. 从合法来源查找备份口令：服务部署变量、配置管理、密码保险库、管理员记录、自动化作业参数、备份系统文档。
2. 将 `.fot` 文件和原始样本只读保存，记录 SHA-256 与获取时间。
3. 在隔离环境中实现/使用恢复程序，并严格执行：结构检查 → `usability` 校验 → `filename` HMAC 校验 → 正文解密 → 正文 HMAC 校验 → 长度校验。
4. 使用临时恢复目录。只在最终 HMAC 和长度均成功后再提交输出文件。
5. 若有“明文原文件 + 对应 `.fot`”配对，可用它们验证文件对应关系、正文边界和恢复工具输出；但已知明文只能得到局部 AES-CTR 密钥流，不能反推 AES-256 密钥或 PBKDF2 口令。每个 FOT 均使用独立 salt 和 IV，因此该局部密钥流也不能迁移到其他 FOT 文件。
6. 若确认口令不可获取，应将工作重点转向恢复配置、历史快照、对象存储版本、终端备份、合法密钥托管系统或管理员保存的凭据；不要将计算资源投入暴力破解。

---

## 12. 地址速查表

| 地址 | 关键操作 |
|---:|---|
| `0x724268` | 读取 fixed header 的前 19 字节 |
| `0x7242ac` | 比较 FOT 魔数 |
| `0x72431c` | `REV16` 读取 `header_len` |
| `0x724640` | `REV16` 读取元数据条目长度 |
| `0x72474c` | 识别 `filename` |
| `0x72478c` | 识别 `usability` |
| `0x7248f0` | PBKDF2 100 次，`usability` AES 密钥 |
| `0x724984` | 固定 `usability` 明文比较 |
| `0x724ab0` | PBKDF2 1000 次，`filename` 密钥 |
| `0x724b38` | `filename` HMAC |
| `0x724d18` | PBKDF2 10000 次，正文密钥 |
| `0x724d3c` | 正文 AES-CTR 解密器 |
| `0x724d5c` | 正文 HMAC 初始化 |
| `0x725268` | 读取文件尾 16 字节标签 |
| `0x725514` | 比较正文 HMAC 标签 |
| `0x725628` / `0x725634` | 分配并以 `crypto/rand.Read` 生成 9 字节 salt |
| `0x725740` | `crypto/rand.Read` 生成 16 字节正文 IV |
| `0x7257c0` | `crypto/rand.Read` 生成 1 字节 padding |
| `0x725c64` | 将正文 IV 写入 fixed header |
| `0x725b60` | 构造 FOT 头 |
| `0x7262ac` | 写元数据条目 |
| `0x7263c8` | 回填总长度 |
| `0x7265b0` | 正文 AES-CTR 加密 |
| `0x7265e4` | 对正文密文计算 HMAC |
| `0x72667c` | 追加 16 字节正文标签 |
| `0x7268b0` | `fot_calculate_encrypted_size`：根据明文长度和元数据长度计算 FOT 加密对象总长度 |
| `0x74f31c` | 下载流程调用 FOT 解密构造器 |
| `0x74f49c` | 使用认证后的 `filename` 建立恢复输出路径 |
| `0x74f5d0` | 将解密流复制到恢复文件 |
| `0x74f5e8` | 校验恢复长度 |

---

## 13. 端到端数据流：文件从哪里进入、经过哪里、怎样写出

本节从“一个远程 FOT 对象如何变成一个本地原始文件”的角度重新组织证据。它不只罗列加密算法，而是追踪每一份数据在函数间的身份变化：**远程下载 URL → HTTP `Response.Body` 字节流 → FOT 头/元数据 → 已认证文件名与正文解密 Reader → 本地输出文件**。

### 13.1 总览：两条业务入口汇聚到同一个下载器

样本有两条上层下载路径；它们最终都调用同一个 `worker/download.Download`，即 IDA 重命名后的 `download_fot_and_restore_plaintext`（`0x74e690`）。两条路径的差别只在于谁提供远程对象链接、目标路径、认证 token 和重试策略；进入 `0x74e690` 后，FOT 处理逻辑相同。

```text
路径 A：单文件执行入口
─────────────────────────────────────────────────────────────────────
Executor.DownloadFile (0x757540)
  │  规范化请求路径，构造 Object，向 cloud driver 请求下载链接
  │  选择 driver 所需的访问 token / 域名参数
  └─► download_fot_and_restore_plaintext (0x74e690)

路径 B：Worker 的远程文件恢复入口
─────────────────────────────────────────────────────────────────────
Worker.downloadRemoteFile (0x760120)
  │  获取 driver；规范化远程与本地路径；构造 Object
  │  向 driver 请求目标对象下载链接
  │  如本地同名目标存在则尝试删除；最多重试 5 次
  ├─► 0x760774：含 Baidu token 的调用形式
  └─► 0x7607b4：其他 driver 的调用形式
       └─► download_fot_and_restore_plaintext (0x74e690)

共同的数据恢复路径
─────────────────────────────────────────────────────────────────────
remote URL / HTTP request
  └─► HTTP Response.Body（加密 FOT 的原始 Reader）
       └─► fot_new_decrypt_reader (0x7240e0)
            └─► fot_parse_header_and_create_decrypt_reader (0x724220)
                 └─► fot_decrypt_reader_read_and_verify_tag (0x725100)
                      └─► CopyBuffer (0x74e2a0) / io.CopyBuffer
                           └─► 本地恢复文件
```

> **阅读提示。** Go 的接口在反编译中通常表示为一对 `tab`（方法表）和 `data`（实例指针）。例如 `Response.Body.tab/data` 并不是两份文件内容，而是同一个 `io.Reader` 接口的运行时表示。本节把它们统一称为“Reader”或“字节流”，避免被 Go ABI 细节干扰。

### 13.2 调用边证据表

下表列出数据流不是根据函数名猜测，而是由完整 IDA 反编译中实际调用点确认的。

| 上游函数 / 地址 | 调用点 | 下游函数 / 地址 | 交接的数据 | 说明 |
|---|---:|---|---|---|
| `Executor.DownloadFile` `0x757540` | `0x7577c4` / `0x7577fc` | `download_fot_and_restore_plaintext` `0x74e690` | driver 提供的下载链接、目标路径、对象大小、认证参数 | 单文件业务入口直接进入下载器。 |
| `Worker.downloadRemoteFile` `0x760120` | `0x760774` / `0x7607b4` | `download_fot_and_restore_plaintext` `0x74e690` | 远程链接、以指针传递的本地目标路径、预计大小、口令 | Worker 路径会对非特定成功/认证失败状态重试，最多 5 次。 |
| `download_fot_and_restore_plaintext` `0x74e690` | `0x74f31c` | `fot_new_decrypt_reader` `0x7240e0` | `Response.Body` Reader、口令的 `[]byte` 表示 | 只有目标名以 `.fot` 结束且上层提供非空口令时才走此分支。 |
| `fot_new_decrypt_reader` `0x7240e0` | `0x72410c` | `fot_parse_header_and_create_decrypt_reader` `0x724220` | 底层 Reader、口令 | 构造阶段就解析第一个 FOT 头并认证头部元数据；并非等到正文已写出才检查口令。 |
| `DecryptReader.Read` `0x724fa0` | `0x724fd0` / `0x7250a8` | `fot_decrypt_reader_read_and_verify_tag` `0x725100` | 输出缓冲区、当前子 Reader | 正常情况下转发正文读取。 |
| `DecryptReader.Read` `0x724fa0` | `0x725050` | `fot_parse_header_and_create_decrypt_reader` `0x724220` | 原始底层 Reader、口令 | 当前子对象返回 `io.EOF` 时，再尝试解析下一个 FOT 对象；这是串接对象的续读路径，不是首次头解析。 |
| `download_fot_and_restore_plaintext` `0x74e690` | `0x74f5d0` | `CopyBuffer` `0x74e2a0` | 已打开的本地 `os.File`、预期大小、解密 Reader | `CopyBuffer` 内部调用 `io.CopyBuffer`，它不断驱动 Reader 读取。 |

### 13.3 阶段 0：上层如何拿到远程对象并进入下载器

#### A. `Executor.DownloadFile`（`0x757540`）

这是一个单文件执行入口。完整反编译显示它先调用 `FixAndCleanPath`（`0x757578`）规范化路径，使用 `path.Base`（`0x757584`）取得名称，并构造一个 `internal/model.Object`。随后通过 driver 的接口方法请求下载信息；成功后分别在 `0x7577c4` 或 `0x7577fc` 调用 `worker/download.Download`。

因此，这条路径的数据身份依次是：**用户/任务指定的路径与对象 ID → `Object` 描述 → driver 返回的下载 URL/大小 → 下载器参数**。它没有直接读取 `.fot` 文件；真正的 FOT 字节只在下载器获得 HTTP 响应后才出现。

#### B. `Worker.downloadRemoteFile`（`0x760120`）

这是 Worker 批量或远程恢复场景使用的入口。反编译中的 `0x7601f8` 先取得 driver，`0x760588` 通过 driver 接口获取目标对象下载链接；若链接获取失败，会返回专门的下载链接错误。成功后，代码在 `0x7605c0` 尝试移除已有本地目标，在 `0x760774`（Baidu 参数形式）或 `0x7607b4`（其他 driver）进入共同下载器。

它还包含重试循环：`0x7607e4` 增加尝试次数，超过 5 次后停止；成功、部分特定状态码以及认证相关状态会直接跳出。重要的是，`download_fot_and_restore_plaintext` 可通过传入的目标路径指针更新最终恢复路径；Worker 的错误路径会在 `0x7608e0` 尝试删除该路径。因此 Worker 是调用者中负责失败后清理的一层，而不是 FOT 解密器本身。

### 13.4 阶段 1：下载器建立 HTTP 输入流

`download_fot_and_restore_plaintext`（原始名称 `worker/download.Download`，`0x74e690`）是网络和解密逻辑的汇合点。

1. **识别恢复模式。** `0x74e710` 和 `0x74f19c` 都比较目标名末尾的 `.fot`。前者用于决定是否跳过普通文件续传/预先打开目标文件的逻辑；后者在 HTTP 成功后重新决定是否将 `Response.Body` 送入 FOT 解密器。FOT 分支还要求口令长度非零。
2. **准备 HTTP 请求。** `0x74ee44` 调用 `net_http_NewRequestWithContext` 创建 `GET` 请求。普通非 FOT 或续传逻辑可能在 `0x74ec20` 到 `0x74ecf8` 添加 `Range`；FOT 恢复分支避免把已有目标当作可直接续写的密文下载结果。
3. **执行请求并确认状态。** `0x74ef18` 通过 HTTP client 发起请求。`0x74f088` 只接受 HTTP `200` 或 `206`；其他状态会读取错误响应体并返回失败。
4. **获得真正的加密输入。** `0x74f0cc` 和 `0x74f0d4` 取出 `Response.Body.tab/data`，即随后所有 FOT 解析函数读取的底层 `io.Reader`。它被 defer 关闭，避免网络连接泄漏。

此时仍没有把网络正文看作明文；它只是“可能为 FOT 的原始字节流”。

### 13.5 阶段 2：FOT 分支把 HTTP Reader 和口令交给解密器

HTTP 成功后，控制流按下面的分支运行：

```text
目标不以 .fot 结尾，或口令为空
    Response.Body ──► CopyBuffer ──► 目标路径（普通下载）

目标以 .fot 结尾，且口令非空
    Response.Body + password string
      │
      ├─ 0x74f1bc：将 Go string 转为 []byte
      └─ 0x74f31c：fot_new_decrypt_reader(Response.Body, password_bytes)
```

`0x74f31c` 失败时，下载器在 `0x74f3e4` 区分认证/格式相关错误和普通错误；这里不会创建 FOT 恢复输出。也就是说，至少 `usability`、`filename` 和创建解密 Reader 所需的头部检查必须通过，程序才会继续到输出路径选择。

### 13.6 阶段 3：构造解密 Reader 时完成头部验证与文件名恢复

#### 13.6.1 构造器不是“无检查的包装”

`fot_new_decrypt_reader`（原始 `crypto.NewDecryptReader`，`0x7240e0`）在 `0x72410c` 调用 `fot_parse_header_and_create_decrypt_reader`。这意味着第一个 FOT 对象的头解析发生在构造阶段；构造成功后，下载器已经可以从返回对象取得**经认证并解密的原始文件名**、FOT 声明总长度和明文长度。

外层 `DecryptReader.Read`（`0x724fa0`）主要是一个组合 Reader：它先在 `0x724fd0` 读取当前 `DecryptSliceReader`；只有当前对象返回 `io.EOF` 时，才在 `0x725050` 再次调用头解析器以衔接下一个 FOT 对象。对于本次“一个 HTTP 对象对应一个 FOT 文件”的下载流程，通常不会触发这个续接分支。

#### 13.6.2 头解析器的输入、输出和内部阶段

`fot_parse_header_and_create_decrypt_reader`（原始 `crypto.NewDecryptSliceReader`，`0x724220`）的输入为：

- 底层 `io.Reader`：这里是 HTTP `Response.Body`；
- `[]byte password`：由下载器在 `0x74f1bc` 从上层传入；
- FOT 字节流中随后读取到的固定头、变长元数据和正文/标签。

其输出不是全文明文，而是一个保存了以下状态的流式 Reader：底层受限 Reader、AES-CTR 状态、正文 HMAC 状态、剩余正文长度、已认证文件名、FOT 总长度和明文长度。

具体过程如下。

1. **读取并验证固定头。** `0x724268` 通过 `io.ReadAtLeast` 读取前 19 字节；`0x7242ac` 比较 FOT magic，`0x7242dc` 验证版本。`0x72431c` 的 `REV16` 证明 `header_len` 是大端 16 位；`0x724d80` 的 64 位字节翻转证明 `total_len` 是大端 64 位。
2. **读取余下头并提取固定字段。** 由 `header_len - 19` 计算剩余头长度后在 `0x72436c` 读取。完整固定头 45 字节，因此 parser 从中取得 `data_iv[16]`、`salt[9]` 和 `padding`；元数据从偏移 45 开始。
3. **解析元数据。** 循环从 `0x724600` 开始，`0x724640` 使用 `REV16` 读每个 `entry_len`；按 `uint16_be entry_len | uint8 key_len | key | value` 切分。`0x72474c` 识别 `filename`，`0x72478c` 识别 `usability`；未知键只作为一般 map 项保存。
4. **验证口令。** `usability` 被 Base64 解码（`0x72481c`），前 16 字节用作它自己的 CTR IV。程序以 `PBKDF2-HMAC-SHA256(password, KDF_PREFIX || salt, 100, 32)`（`0x7248f0`）派生密钥，AES-CTR 解密后与固定检查字符串比较（`0x724984`）。失败立即返回，不会创建输出文件。
5. **认证并解密文件名。** `filename` 被 Base64 解码；最后 16 字节为 HMAC 截断值。程序以 1000 次 PBKDF2 在 `0x724ab0` 派生密钥，对文件名密文计算 HMAC（`0x724b38`），用逐字节 XOR 聚合比较标签（`0x724c20` 到 `0x724c50`），随后在 `0x724c60` 用 fixed header 的 `data_iv` AES-CTR 解密出原始文件名。
6. **为正文建立流式状态。** `0x724d18` 使用 10000 次 PBKDF2 派生正文密钥；`0x724d3c` 创建正文 AES-CTR，`0x724d5c` 创建正文 HMAC。`io.LimitedReader` 的底层范围覆盖“正文密文 + 文件末尾 16 字节标签”；独立的剩余正文计数排除了最后的标签，因此标签不会被解密成正文。
7. **返回 Reader 和元信息。** 返回对象携带已认证的文件名及长度。下载器以这些字段决定输出路径和长度检查，而不是从云端 `.fot` 对象名猜原文件名。

### 13.7 阶段 4：认证后的文件名如何成为本地目标路径

头解析成功后，`download_fot_and_restore_plaintext` 在 `0x74f460` 取得输入 `.fot` 目标路径的目录；随后读取解密 Reader 中保存的文件名，在 `0x74f49c` 调用 `path/filepath.Join` 组装最终输出路径。紧接着 `0x74f4d0` 用 `os.OpenFile` 创建/打开该恢复文件。

因此路径选择的精确数据流是：

```text
云端对象名（例如长的 *.fot 名称）
  │
  ├─ 只用于定位/下载 FOT 对象和取得其所在恢复目录
  │
  └─ 不作为原文件名

header.metadata["filename"]
  └─ Base64 解码 → HMAC 认证 → AES-CTR 解密
       └─ verified_filename
            └─ filepath.Join(Dir(fot_target_path), verified_filename) @ 0x74f49c
                 └─ os.OpenFile(...) @ 0x74f4d0
```

这里的关键边界是：**原程序在使用 `filename` 前已经完成其 HMAC 验证**。不过它直接将经认证的名称传给 `filepath.Join`；随附恢复工具额外拒绝路径分隔符并限制在显式输出目录内，这是工具针对离线恢复场景额外采取的路径安全措施，并不是对原程序行为的描述。

### 13.8 阶段 5：正文如何从密文流变成明文流

一旦目标文件已由 `0x74f4d0` 打开，下载器在 `0x74f5d0` 调用 `CopyBuffer`。`CopyBuffer`（`0x74e2a0`）调用 Go 标准库 `io.CopyBuffer`，由后者反复请求解密 Reader 填充缓冲区。真正处理每块正文的是 `fot_decrypt_reader_read_and_verify_tag`（原始 `crypto.(*DecryptSliceReader).Read`，`0x725100`）。

```text
io.CopyBuffer
  │  调用 Read(output_buffer)
  ▼
fot_decrypt_reader_read_and_verify_tag
  │
  ├─ 从 LimitedReader 读取正文密文块
  ├─ 将“原始密文块”写入 HMAC-SHA256 状态 @ 0x7253b0
  ├─ 使用已有 AES-256-CTR 状态将密文块转换为明文 @ 0x725390
  ├─ 将明文放入内部 buffer
  └─ 由 io.CopyBuffer 写入已经打开的本地 os.File
```

从反编译可见，常规 `io.CopyBuffer` 调用提供足够大的缓冲区时，Reader 在 `0x7252d8` 将单次密文读取限制为 4096 字节。该分块只改变处理方式，不改变 CTR 流或 HMAC 覆盖范围：HMAC 输入始终是密文，AES-CTR 状态跨块连续推进。

正文耗尽后，Reader 不立即报告最终成功，而是进行最后认证：

1. `0x725268` 从仍保留 16 字节的受限底层 Reader 读取存储的尾标签；若不足 16 字节，返回“缺少 HMAC 标签/文件不完整”错误。
2. `0x72528c` 取已累积的 HMAC-SHA256 值。
3. `0x725514` 到 `0x72553c` 将其前 16 字节与文件尾标签逐字节 XOR 聚合比较。
4. 标签正确才设置 `verified` 并返回 `io.EOF`；不匹配则返回 HMAC 错误而不是成功 EOF。

这说明“读取到 EOF”在这里不仅是网络流结束，更是**正文完整性认证成功的信号**。

### 13.9 阶段 6：写盘、长度检查与失败语义

`CopyBuffer` 在 `0x74e308` 调用 `io.CopyBuffer`。若 Reader 在最终标签检查时返回“缺标签”或“HMAC 不匹配”，`CopyBuffer` 在 `0x74e39c` 将其映射为结果码 17；其他复制错误映射为结果码 11。复制成功后，下载器再做两层长度检查：

- `0x74f5e8`：上层预计 FOT/对象大小与解密 Reader 记录的 FOT 总长度必须一致；
- `0x74f5fc`：检查写出的恢复文件大小是否等于解密 Reader 记录的明文长度。

#### 原程序与随附恢复脚本的写入时机不同

这一差异必须明确区分：

| 项目 | 原始 Go 下载逻辑 | 随附 `fot_decrypt.py` |
|---|---|---|
| 文件名认证 | 创建输出路径前完成 `filename` HMAC 验证。 | 写盘前完成 `filename` HMAC 验证，并拒绝路径分隔符。 |
| 正文 HMAC | 通过 `io.CopyBuffer` 边读、边解密、边写出；只有到 EOF 才读取并验证尾标签。 | 先在内存验证完整正文 HMAC，再解密。 |
| 输出文件打开 | `0x74f4d0`，在正文最终 HMAC 前。 | 认证成功后才写 `.partial`。 |
| HMAC 失败时的物理文件 | `0x74e690` 本身已经可能写入前面的明文字节；它不会在该函数内把此次写入变成原子提交。`Worker.downloadRemoteFile` 调用路径随后会在 `0x7608e0` 尝试删除失败目标；`Executor.DownloadFile` 的已导出包装代码中未见同等清理调用。 | 不会提交最终文件；只在全部验证通过后将 `.partial` 重命名为最终名。 |
| 适用原因 | 原程序优先采用流式低内存下载。 | 两份样本很小，离线恢复工具优先选择“先认证、后提交”的保守写盘语义。 |

因此，不能把原程序描述为“在写入前已经验证全部 HMAC”。准确表述是：它在写入前认证了头和文件名；它在流式写入过程中累计正文 HMAC，并在最后一个标签到达时决定整个恢复是否成功。实际取证恢复应以随附工具的“先认证、后原子提交”语义为准。

### 13.10 一次成功恢复的逐步检查表

下表把所有环节按实际时间顺序串起来。它也是复核实现是否遗漏认证步骤的清单。

| 序号 | 数据此时的身份 | 所在函数 / 地址 | 操作和必须满足的条件 | 失败结果 |
|---:|---|---|---|---|
| 1 | 远程对象描述 | `Executor.DownloadFile` `0x757540` 或 `Worker.downloadRemoteFile` `0x760120` | driver 获取下载链接。 | 不进入下载器。 |
| 2 | 下载 URL、目标 `.fot` 路径、口令 | `0x74e690` | 检测 `.fot` 与非空口令。 | 作为普通下载或直接报错，取决于上层参数。 |
| 3 | HTTP 请求 | `0x74ee44` | 建立 GET 请求。 | 返回请求错误。 |
| 4 | HTTP 响应 | `0x74f088` | 仅接受 200/206。 | 不把错误页面当 FOT。 |
| 5 | `Response.Body` 加密 Reader | `0x74f0cc` / `0x74f0d4` | 取得网络正文 Reader。 | 连接/读取失败。 |
| 6 | 底层 Reader + `[]byte` password | `0x74f1bc` / `0x74f31c` | 转换口令并构造 FOT 解密 Reader。 | 头部错误映射为下载失败。 |
| 7 | fixed header | `0x724268` 至 `0x72436c` | 读取 magic、version、`header_len`、`total_len` 和余下头。 | 格式/截断错误。 |
| 8 | metadata | `0x724600` 至 `0x7247fc` | 逐项边界检查，识别 `usability`、`filename`。 | 元数据格式错误。 |
| 9 | usability 密文 | `0x72481c` 至 `0x724984` | Base64、PBKDF2(100)、AES-CTR、固定明文比较。 | 口令错误或元数据损坏；尚未创建输出文件。 |
| 10 | filename 密文与标签 | `0x7249c4` 至 `0x724c60` | Base64、PBKDF2(1000)、HMAC、AES-CTR。 | 未认证名称不能用于输出路径。 |
| 11 | 正文加密状态 | `0x724d18` 至 `0x724e84` | PBKDF2(10000)，创建 CTR/HMAC/LimitedReader。 | 无可用正文 Reader。 |
| 12 | 已认证原始文件名 | `0x74f49c` | `Dir(fot path)` 与 `verified_filename` 拼接。 | 路径创建失败。 |
| 13 | 打开的输出文件 | `0x74f4d0` | `os.OpenFile`。 | 不会开始复制。 |
| 14 | 正文密文块 | `0x725314` / `0x7253b0` | 读块并把密文加入 HMAC。 | 读取错误。 |
| 15 | 明文块 | `0x725390` / `0x74f5d0` | AES-CTR 解密，`io.CopyBuffer` 写入文件。 | 复制错误；可能已有部分输出。 |
| 16 | 文件尾 16-byte tag | `0x725268` / `0x725514` | 读取并比较 HMAC 截断值。 | 码 17；正文不可信。 |
| 17 | 完整恢复文件 | `0x74f5e8` / `0x74f5fc` | 检查 FOT 对象长度与明文文件长度。 | 返回长度不一致错误。 |

### 13.11 附录索引：完整 IDA 输出而非节选

为使主报告保持可读，同时满足审计时需要逐行复查反编译与反汇编的要求，完整原始 IDA listing 单独保存在以下附录。附录中的 Go 运行时栈扩容、GC write barrier 和接口分派样板也会保留；它们看似冗长，但能证明 listing 没有为叙述方便而被截断或重写。

- [附录 A：完整 IDA 反编译与逐函数数据流说明](report_decompilation_appendix.md)
- [附录 B：`0x724220` 完整 AArch64 反汇编与区段注释](report_disassembly_appendix.md)

附录 B 还明确标注了证据边界：当前归档中保存了 `0x724220` 的完整反汇编；下载、写盘和正文 Reader 的其他关键点均有完整 IDA 反编译与调用地址证据，但不会被错误声称为“已逐条导出完整汇编”。

---

## 14. 结论

FOT 的解密逻辑已经从二进制中恢复到足以实现可靠恢复器的程度。用户提供的恢复口令 `b"0"` 已在两份样本上完成端到端验证：分别恢复出空的 `null` 文件与唯一内容为 ASCII `0`（`0x30`）的 `0.txt` 文件。后续恢复时应使用同一认证顺序离线处理；在任一验证失败时停止并保留原始证据，不能把未认证输出当作恢复成功。
