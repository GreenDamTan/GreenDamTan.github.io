# 一次 FOT 加密容器解密逆向记录

# 前言

前段时间拿到一批需要恢复的备份对象。下载下来的文件不是普通的数据文件，而是一串扩展名为 `.fot` 的长文件名对象。直接看内容，开头像是有格式头，后面则基本不可读。

这种东西最怕只看见 AES 就开始套工具。真要恢复，关键不在于“把正文跑一遍 CTR”，而在于先把容器边界、口令验证、文件名认证、正文认证和落盘时机全部对上。漏掉其中任何一步，都可能得到一份看上去能打开、实际上没有经过完整性验证的文件。

这次把对应的 ARM64 Go 程序从下载入口一路跟到输出文件。最后确认它不是那种把用户磁盘上的文件批量替换掉的典型逻辑，而是给云端备份对象套了一层自定义的 FOT 容器。

本文记录的是当前样本的完整恢复路径。文中会保留关键反编译和反汇编，主要是方便后面遇到同类版本时，不用从头猜它是不是同一套格式。这里没有运行未知原始程序；所有验证都只对授权对象做只读解析，并使用独立恢复器完成。

【待配图】

# 这次处理的是什么

程序是一个 ARM64 Linux 上的 Go 备份程序。它的下载路径会判断目标对象是不是 `.fot`，如果是，并且上层带了口令，就不把网络响应直接写到磁盘，而是先包进 FOT 解密 Reader。

换句话说，输入和输出大致是这样的：

```text
云端对象 URL
    │
    ▼
HTTP Response.Body（FOT 原始字节流）
    │
    ▼
FOT 头 / metadata / 正文密文 / 尾部 tag
    │
    ▼
解密 Reader
    │
    ▼
原始文件名 + 原始文件正文
```

真正值得注意的是，文件名并不取自云端对象名。`.fot` 对象名只是下载定位符；原始文件名放在 FOT metadata 里，验证成功以后才会解出来，再用它建立最终输出路径。

<div style="border:1px solid #888;border-radius:8px;padding:14px;margin:16px 0;background:#fafafa">
  <strong>这次恢复链路的四种数据身份</strong><br>
  <code>.fot 对象名</code>：用于定位和下载<br>
  <code>FOT 字节流</code>：格式头、metadata、正文密文、尾 tag<br>
  <code>verified_filename</code>：通过 HMAC 后得到的原文件名<br>
  <code>plaintext</code>：通过正文 tag 后可以提交的原文件内容
</div>

# 先把 FOT 文件拆开

先看固定头。当前格式的固定头长度是 45 bytes，后面跟变长 metadata、正文密文和最后的 16 bytes 认证标签。

```text
0x00                                                        total_len
┌────────┬───────┬────────────┬───────────┬──────┬─────┬───────────┬────────────┐
│ magic  │ ver   │ header_len │ total_len │  IV  │salt │ metadata  │ ciphertext │ tag │
│ 8 byte │1 byte │ 2 byte BE  │ 8 byte BE │16 B  │ 9 B │ variable  │  variable  │16 B │
└────────┴───────┴────────────┴───────────┴──────┴─────┴───────────┴────────────┘
   0       8        9            11          19    35      45      header_len  total_len-16
```

固定 magic 是：

```text
46 4f 54 a3 1c 77 00 5e
```

两个实际对象的前 64 bytes 如下。两份对象都是版本 1，`header_len` 都是 `0x00a8`，也就是 168 bytes；对象大小分别是 184 和 185 bytes，所以一个正文长度为 0，另一个正文长度为 1。

```text
00000000: 46 4f 54 a3 1c 77 00 5e 01 00 a8 00 00 00 00 00  FOT..w.^........
00000010: 00 00 b8 be 34 35 08 0a 0b 18 83 9c 74 4f 0d 6d  ....45......tO.m
00000020: 6e 80 b7 0b 60 17 4d 0a 02 68 a6 93 ca 00 54 09  n...`.M..h....T.
00000030: 75 73 61 62 69 6c 69 74 79 65 38 4f 49 38 39 47  usabilitye8OI89G

00000000: 46 4f 54 a3 1c 77 00 5e 01 00 a8 00 00 00 00 00  FOT..w.^........
00000010: 00 00 b9 2a 12 95 a5 82 e6 2e 7e ec 55 08 08 7a  ...*......~.U..z
00000020: 8d f3 b8 4b 7d ea 39 60 33 e5 2b d8 6d 00 54 09  ...K}.9`3.+.m.T.
00000030: 75 73 61 62 69 6c 69 74 79 2f 6a 48 54 61 53 71  usability/jHTaSq
```

【待配图】

## metadata 不是 JSON

`header_len` 之后才是正文。固定头中从偏移 45 开始的部分是一串变长条目，编码方式如下：

```text
uint16_be entry_len | uint8 key_len | key[key_len] | value[entry_len - 3 - key_len]
```

也就是读条目时不能只看 key。必须先确认 `entry_len` 没有越过 `header_len`，再确认 `key_len` 没有越过当前 entry。否则一个截断的 FOT 文件很容易把正文密文误当 metadata。

当前真正参与恢复的特殊 key 有两个：

| key | value 的外层形式 | 解码后的用途 |
|---|---|---|
| `usability` | Base64 | 独立 16-byte IV + 固定检查值密文；没有独立 HMAC。 |
| `filename` | Base64 | 文件名密文 + 16-byte 截断 HMAC。 |

正文则不在 metadata 里面。它从 `header_len` 开始，一直到 `total_len - 16`；最后 16 bytes 是正文 HMAC-SHA256 的前 16 bytes。

```text
ciphertext = blob[header_len : total_len - 16]
body_tag   = blob[total_len - 16 : total_len]
```

# 正文认证范围：只看解密端就能确定

恢复时真正关心的是：尾部 tag 到底认证了什么。`DecryptSliceReader.Read` 本身已经很明确：每次从底层 Reader 取到的是 ciphertext chunk，`0x7253b0` 把这块原始密文写入 HMAC 状态，`0x725390` 再用 CTR 状态变换为 plaintext。

```text
body_tag = HMAC-SHA256(content_key, ciphertext)[:16]
```

因此，正文 HMAC 覆盖的是 **ciphertext**，不是解密后的明文。CTR 模式本身只是可逆的流变换，不能告诉你数据有没有被截断、替换或用错口令；最后这 16 bytes tag 才决定全文是否可信。

# 三组 PBKDF2，不是一把 key 用到底

程序没有直接把口令拿去做 AES。它会把同一口令和文件头里的 salt 组合，分别做三次 PBKDF2-HMAC-SHA256。

```text
有效 PBKDF2 salt = 3c a1 5e f9 d2 07 6b || file_salt[9]
输出长度          = 32 bytes
哈希             = SHA-256
```

三组迭代次数和用途如下：

| 轮数 | 用途 | 结果 |
|---:|---|---|
| 100 | `usability` 快速口令检查 | AES-256-CTR key |
| 1000 | `filename` | AES-256-CTR key + HMAC key |
| 10000 | 正文 | AES-256-CTR key + HMAC key |

```text
password + KDF_PREFIX + file_salt
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
 PBKDF2(100) PBKDF2(1000) PBKDF2(10000)
     │           │           │
 usability    filename       body
  CTR check   CTR + HMAC   CTR + HMAC
```

`KDF_PREFIX` 不是字符串拼接时随手加的分隔符，而是二进制常量。顺序也不能写反：必须是 **固定前缀在前，文件 salt 在后**。

当前已验证恢复口令是：

```python
RECOVERY_PASSWORD = b"0"
```

这不是口令枚举结果。程序会通过 `usability` 字段快速拒绝错误口令：先 Base64 解码，取前 16 bytes 作为单独的 CTR IV，再用 100 次 PBKDF2 派生的 key 解密剩余部分。明文必须严格等于下面这个内置检查值：

```text
teirenfeiniuyunpanbeifencryptoencrypto
```

`usability` 没有额外 HMAC。两个样本中 Base64 解码后的长度都是 54 bytes，正好是：

```text
16-byte IV + 38-byte fixed-check ciphertext
```

这一步过不了，后面的文件名和正文就没有必要继续处理。

【待配图】

# 名称和地址速查表

后面会在两种命名之间切换：一类是二进制里保留的 Go 原始符号，另一类是我为了阅读数据流而起的分析名。分析名只是在 IDA 里把职责写清楚，不代表程序源码本来就这么命名。先放一张表，后面遇到名字不一样时可以回来看。

| 地址 | 原始 Go 符号 / 反编译中的名称 | 文中分析名 | 在解密流程里的职责 |
|---:|---|---|---|
| `0x757540` | `worker.(*Executor).DownloadFile` | `Executor.DownloadFile` | 单文件恢复入口；向 driver 取得下载信息后进入共同下载器。 |
| `0x760120` | `worker.(*Worker).downloadRemoteFile` | `Worker.downloadRemoteFile` | Worker 恢复入口；处理 driver、重试与失败后的目标清理。 |
| `0x74e690` | `worker/download.Download` | `download_fot_and_restore_plaintext` | HTTP 下载和 FOT 恢复的汇合点；决定 `Response.Body` 是否进入解密 Reader。 |
| `0x7240e0` | `crypto.NewDecryptReader` | `fot_new_decrypt_reader` | 外层 Reader 构造器；解析第一个对象并保存底层 Reader、口令与元信息。 |
| `0x724220` | `crypto.NewDecryptSliceReader` | `fot_parse_header_and_create_decrypt_reader` | 读取 FOT 头、解析 metadata、验证口令/文件名、建立正文 CTR/HMAC Reader。 |
| `0x724fa0` | `crypto.(*DecryptReader).Read` | `DecryptReader.Read` | 调度当前子 Reader；仅在当前对象 EOF 后尝试衔接下一个 FOT 对象。 |
| `0x725100` | `crypto.(*DecryptSliceReader).Read` | `fot_decrypt_reader_read_and_verify_tag` | 读取正文密文，更新 HMAC、CTR 解密，并在流末验证 16-byte tag。 |
| `0x74e2a0` | `worker/download.CopyBuffer` | `CopyBuffer` | 调 `io.CopyBuffer` 驱动解密 Reader，将它返回的 plaintext 写进已打开的文件。 |

表里的地址适用于这一个版本。变体里地址可能会整体移动，但函数之间的关系、关键字符串、PBKDF2 参数和 Reader 数据流通常更值得拿来对照。

# 用 `4781595.jpg` 示例对象走一遍

下面不再只抽象地说“有 header、有 metadata”。全文只使用用户指定的这份对象做示例：

```text
03aceacb3e8c37ff8fad811ab48874620000000000000772000000006a6a099f.fot
```

它的 SHA-256 是：

```text
8b36db3766ba752c45e350f777494ed8c14275db8d6c7a197bb5718eeb4114e1
```

这份 FOT 长 2,098 bytes；恢复后得到 `4781595.jpg`，大小 1,906 bytes。它不是人为拼出来的演示文件，下面的字段、key、HMAC、JPEG 头和图片都直接来自这份对象。

```text
FOT 文件总长度：2,098 bytes
├── [0, 45)       固定头
├── [45, 176)     metadata：usability + filename
├── [176, 2082)   正文 ciphertext：1,906 bytes
└── [2082, 2098)  body tag：16 bytes
```

## 阅读用的状态视图

反编译里保存 Reader 状态的是一串偏移和 Go 接口字段，不适合直接当结构体读。下面这个定义只是把 `NewDecryptSliceReader` 建好的、解密时真正会用到的字段按逻辑归拢，**不是**从程序里导出的原始 Go 源码或精确内存布局。

```go
type FOTHeaderView struct {
    Magic        [8]byte
    Version      byte
    HeaderLength uint16 // 文件里是 big-endian
    TotalLength  uint64 // 文件里是 big-endian
    DataIV       [16]byte
    Salt         [9]byte
    Padding      byte
    Metadata     map[string][]byte
}

type DecryptSliceReaderView struct {
    Reader          io.Reader      // HTTP Response.Body 或本地 FOT reader
    BodyReader      io.LimitedReader
    CTR             cipher.Stream
    BodyHMAC        hash.Hash
    Remain          int64          // 只计算 ciphertext，不包含尾 tag
    Verified        bool
    Filename        string         // filename HMAC 成功后才填入
    CipherLength    int64
    PlainLength     int64
}
```

其中最容易看错的是 `BodyReader` 和 `Remain`：底层 reader 仍然能接触到最后的 tag，但 `Remain` 只代表正文密文还有多少 bytes。正文读完后，`Read` 根据 `Remain == 0` 走到尾标签读取和比较分支，所以 tag 不会被 CTR 当作正文处理。

## 阶段 1：固定头 `[0, 45)`

前 45 bytes 解析为：

| 文件范围 | 原始值 | 解析结果 | 后续用途 |
|---|---|---|---|
| `[0, 8)` | `46 4f 54 a3 1c 77 00 5e` | FOT magic | 先确认不是别的格式。 |
| `[8, 9)` | `01` | version = 1 | 当前 Reader 只接受这个版本。 |
| `[9, 11)` | `00 b0` | header_len = 176 | metadata 到 offset 176 截止；正文从这里开始。 |
| `[11, 19)` | `00 00 00 00 00 00 08 32` | total_len = 2,098 | 最后 16 bytes 是 tag，因此正文末尾是 2,082。 |
| `[19, 35)` | `b5 61 b6 17 78 ed 6d 5c 61 7e de 08 d4 7d 02 f2` | `data_iv` | 文件名和正文的 CTR IV。 |
| `[35, 44)` | `6f 0e 31 5e c8 54 1e d9 7b` | file salt | 与固定前缀拼接后进入三组 PBKDF2。 |
| `[44, 45)` | `1b` | padding | 当前恢复路径不把它作为 key/IV/tag 使用。 |

下面是这 176 bytes header 的原始 `xxd -g 1` 视图。左边是文件偏移，右边是同一行可打印的 ASCII；表中的字段范围可以直接在这里对应，而不用在一长串 hex 中数位置。

```text
00000000: 46 4f 54 a3 1c 77 00 5e 01 00 b0 00 00 00 00 00  FOT..w.^........
00000010: 00 08 32 b5 61 b6 17 78 ed 6d 5c 61 7e de 08 d4  ..2.a..x.m\a~...
00000020: 7d 02 f2 6f 0e 31 5e c8 54 1e d9 7b 1b 00 54 09  }..o.1^.T..{..T.
00000030: 75 73 61 62 69 6c 69 74 79 78 2f 4e 2b 56 6a 33  usabilityx/N+Vj3
00000040: 41 67 37 70 4f 47 2b 61 37 37 6a 5a 57 44 33 32  Ag7pOG+a77jZWD32
00000050: 62 6b 33 77 44 37 52 65 4f 31 63 65 4a 58 4a 71  bk3wD7ReO1ceJXJq
00000060: 68 57 39 41 46 4a 56 66 57 6e 49 51 5a 36 49 45  hW9AFJVfWnIQZ6IE
00000070: 6e 39 79 74 4b 44 4e 38 31 35 70 2f 62 2b 67 48  n9ytKDN815p/b+gH
00000080: 43 00 2f 08 66 69 6c 65 6e 61 6d 65 49 31 62 36  C./.filenameI1b6
00000090: 35 66 6d 59 36 66 36 75 44 6a 63 72 79 74 2b 65  5fmY6f6uDjcryt+e
000000a0: 50 5a 2f 79 55 7a 45 6f 44 32 41 54 6a 45 62 69  PZ/yUzEoD2ATjEbi
```

这个阶段对应 `fot_parse_header_and_create_decrypt_reader`。Reader 首先只读 `[0, 19)`，因为此时必须先拿到 `header_len` 才知道还要读多少头；然后再读 `[19, 176)`，形成完整 header。此时还没有正文解密，也还没有创建输出文件。

```text
input reader offset: 0
  ├─ ReadAtLeast(19)  → magic/version/header_len/total_len
  ├─ ReadAtLeast(157) → 补齐 [19, 176) 的 header 剩余部分
  └─ reader offset: 176，下一次读才会拿到正文 ciphertext
```

## 阶段 2：metadata `[45, 176)`

这份样本有两个条目，共 131 bytes metadata：

| entry 范围 | `entry_len` | key | value 长度 | value 的含义 |
|---|---:|---|---:|---|
| `[45, 129)` | 84 | `usability` | 72 | Base64 文本，解码后是 54-byte 的“独立 IV + 检查值密文”。 |
| `[129, 176)` | 47 | `filename` | 36 | Base64 文本，解码后是 11-byte 文件名密文 + 16-byte HMAC tag。 |

`usability` 的 value 是：

```text
x/N+Vj3Ag7pOG+a77jZWD32bk3wD7ReO1ceJXJqhW9AFJVfWnIQZ6IEn9ytKDN815p/b+gHC
```

Base64 解码后为 54 bytes。下面按字段切开，而不是把 IV 和密文混在同一行：

```text
usability_iv [16]
c7 f3 7e 56 3d c0 83 ba 4e 1b e6 bb ee 36 56 0f

usability_ciphertext [38]
7d 9b 93 7c 03 ed 17 8e d5 c7 89 5c 9a a1 5b d0
05 25 57 d6 9c 84 19 e8 81 27 f7 2b 4a 0c df 35
e6 9f db fa 01 c2
```

前 16 bytes 是 `usability_iv`，剩余 38 bytes 才是固定检查值的密文。这里没有额外 tag；长度已经把这个结构钉死了。

`filename` 的 value 是：

```text
I1b65fmY6f6uDjcryt+ePZ/yUzEoD2ATjEbi
```

Base64 解码后为：

```text
23 56 fa e5 f9 98 e9 fe ae 0e 37 | 2b ca df 9e 3d 9f f2 53 31 28 0f 60 13 8c 46 e2
└────────── 文件名密文[11] ──────────┘   └──────────── 16-byte filename tag ────────────┘
```

此时 parser 得到的还是两个 `[]byte` value；它们先保存在 metadata map 里。只有口令检查和 filename tag 都通过，`Filename` 字段才会变成可用于输出路径的 `4781595.jpg`。

## 阶段 3：三组 key 从 password/salt 变成解密状态

当前验证口令是 `b"0"`。对于这份样本，三次派生的有效 salt 都是：

```text
3c a1 5e f9 d2 07 6b || 6f 0e 31 5e c8 54 1e d9 7b
```

也就是 16 bytes：

```text
3c a1 5e f9 d2 07 6b 6f 0e 31 5e c8 54 1e d9 7b
```

派生结果如下。列出 key 是为了让遇到同一版本的受害者可以核对自己的实现是不是把前缀顺序、轮数或 salt 长度写错了；它们都来自这份示例对象，不是额外猜出来的口令材料。

| 用途 | PBKDF2 rounds | key[0:16] | key[16:32] |
|---|---:|---|---|
| usability | 100 | `20 db 99 27 bc 00 3f 76 b0 a9 fe ab b7 34 b7 4d` | `12 42 32 ca 1b e3 c4 75 87 46 17 c6 70 23 fc c5` |
| filename | 1000 | `13 06 2c 4f 4a af 26 57 51 df 36 ce b2 36 b2 87` | `98 53 23 fd f8 4d 83 04 d7 36 f2 18 f9 ec 89 f5` |
| body | 10000 | `ac 0e 82 2e c2 d0 54 f4 ae 93 cb 91 00 d8 29 bf` | `7d f9 e8 86 d0 87 b3 c5 9d 41 7d 5c 5d a5 df b7` |

`usability` 路径用第一把 key 和自己的 IV 解密，得到：

```text
teirenfeiniuyunpanbeifencryptoencrypto
```

严格相等以后，口令才被接受。

## 阶段 4：文件名从 metadata 变成输出路径

这条 `filename` entry 位于 `[129, 176)`：

```text
[129, 131)  00 2f       entry_len = 47
[131, 132)  08          key_len = 8
[132, 140)  66 69 6c 65 6e 61 6d 65
                        ASCII "filename"
[140, 176)  I1b65fmY6f6uDjcryt+ePZ/yUzEoD2ATjEbi
                        Base64 value，长度 36 bytes
```

Base64 解码后，恢复器按最后 16 bytes 切开：

```text
packed filename bytes (27 bytes)
23 56 fa e5 f9 98 e9 fe ae 0e 37 | 2b ca df 9e 3d 9f f2 53 31 28 0f 60 13 8c 46 e2
└────────────── ct[11] ──────────────┘   └──────────── tag[16] ────────────┘
```

第一步不是把 `4781595.jpg` 当作可信名字直接拿来用，而是用 filename 的 1000 次 PBKDF2 key 对左侧 11 bytes 做 HMAC：

```text
HMAC-SHA256(
    13062c4f4aaf265751df36ceb236b287985323fdf84d8304d736f218f9ec89f5,
    23 56 fa e5 f9 98 e9 fe ae 0e 37,
)[:16]
= 2b ca df 9e 3d 9f f2 53 31 28 0f 60 13 8c 46 e2
```

计算结果和右侧 tag 完全一致，才接受这个 metadata value。当前实现里 CTR 变换和 tag 比较都发生在同一段构造逻辑中；关键的安全边界是：**只有 tag 成功，随后得到的明文才会被转成 Go string，并写入 Reader 的 filename 字段**。所以即使损坏 value 恰好 CTR 出一段可读字符，也不会被拿去拼路径。

本例文件名的 CTR 变换可以把每一个 byte 对上：

```text
filename_key  = 13 06 2c 4f 4a af 26 57 51 df 36 ce b2 36 b2 87 ...
data_iv       = b5 61 b6 17 78 ed 6d 5c 61 7e de 08 d4 7d 02 f2
CTR keystream = 17 61 c2 d4 cc a1 dc d0 c4 7e 50
ciphertext    = 23 56 fa e5 f9 98 e9 fe ae 0e 37
plaintext     = 34 37 38 31 35 39 35 2e 6a 70 67
                4  7  8  1  5  9  5  .  j  p  g
```

这里 `data_iv` 和正文使用的是同一个固定头字段，但 filename key 和 body key 来自不同轮数的 PBKDF2，因此不是用同一把 CTR key/IV 组合去变换两段数据。

通过认证并得到 `4781595.jpg` 后，原程序的路径处理如下：

```text
.fot 目标路径
   │
   ├─ filepath.Dir(...)                         // 保留恢复目录
   │
verified filename = "4781595.jpg"
   │
   └─ filepath.Join(directory, "4781595.jpg") // 0x74f49c
        │
        └─ os.OpenFile(..., 0x241, 0x1b6)       // 0x74f4d0
             │
             └─ 后续 CopyBuffer 把正文 plaintext 写入这个文件
```

独立恢复器额外拒绝空名字、`.`、`..`、`/` 和 `\`，并确认最终解析路径仍在指定输出目录下。这个额外检查是为了离线恢复时不让经认证但不符合预期的名字越过恢复目录；它不改变 FOT 的文件名认证算法。

## 阶段 5：正文 `[176, 2082)` 和 tag `[2082, 2098)`

正文长度为 1,906 bytes。没有必要把所有密文贴进来，下面保留首尾 32 bytes 和 tag；这足够和自己的恢复结果对照，也不会淹没正文。

```text
ciphertext 起始窗口（正文相对偏移）:
0000: 36 70 db e3 f3 ac 60 b6 ca 93 50 e1 9b c0 0c 17
0010: 71 03 95 fe 5f 38 b8 45 25 e5 0d 34 25 4d f9 f7

ciphertext 结束窗口（最后 32 bytes）:
-020: e0 b2 1e 21 ec 35 68 aa 1b d0 17 bc 41 ab de 02
-010: 43 27 cd 4d d3 35 f7 18 aa f0 19 6d 7a 51 91 08

body tag [16]:
6e f6 47 ff 1e 15 0f 21  e3 7c 53 99 99 43 4f 46
```

构造 Reader 后，逻辑状态可以按下面理解：

```text
底层 reader offset: 176
LimitedReader.N:    1922  （正文 1906 bytes + 尾 tag 16 bytes）
Remain:              1906  （只允许 CTR/HMAC 正文分支消费这部分）
CTR:        AES-256-CTR(body_key, data_iv)
BodyHMAC:   HMAC-SHA256(body_key)，初始为空
Verified:   false
```

这里的双重长度很容易误解：`LimitedReader.N` 留住 tag，是为了正文完成后仍能从同一底层流读取它；`Remain` 则确保普通正文读取不把最后 16 bytes tag 当作正文处理。

`DecryptSliceReader.Read` 每次最多从正文读约 4096 bytes。这份对象比该上限小，所以正文只需一次读取；处理顺序仍然是：

```text
ciphertext chunk
  ├─ BodyHMAC.Write(ciphertext chunk)
  └─ AES-256-CTR(body_key, data_iv) → plaintext chunk
                                      └─ CopyBuffer → os.File
```

全部 1,906 bytes 读完以后，Reader 才读取尾 tag，并计算：

```text
HMAC-SHA256(
    ac0e822ec2d054f4ae93cb9100d829bf7df9e886d087b3c59d417d5c5da5dfb7,
    ciphertext[176:2082],
)[:16]
= 6e f6 47 ff 1e 15 0f 21 e3 7c 53 99 99 43 4f 46
```

结果和文件末尾 tag 一致，`Verified` 才变成 true。CTR 输出的首尾字节如下：

```text
plaintext 起始窗口（JPEG header）:
0000: ff d8 ff db 00 84 00 08 06 06 07 06 05 08 07 07
0010: 07 09 09 08 0a 0c 14 0d 0c 0b 0b 0c 19 12 13 0f

plaintext 结束窗口（JPEG trailer）:
-020: 27 0c 73 c9 ec 2b d8 3c 0c aa be 13 b4 0a 41 c9
-010: 72 71 ee c6 b4 93 bb b9 cb 4a 1e ce 0a 27 ff d9
```

开头 `ff d8` 是 JPEG SOI；在 offset 136 的 SOF0 段中，宽和高都是 64。最后 `ff d9` 是 JPEG EOI，所以这不仅是“HMAC 过了”，恢复后的正文结构也确实是一份完整 JPEG。

恢复出的文件 SHA-256：

```text
93e94f000b9be232f7d6299abf499fdc9f517493b4f88c18daef1a2d92174d8a
```

![从指定 FOT 对象恢复的 4781595.jpg](img/4781595.jpg)

此时对象的最终状态是：

```text
Filename:  4781595.jpg
Plaintext: 1,906 bytes JPEG (64 × 64)
Verified:  true
```

对于原程序，这个“最后再确认”的时机就是为什么它可能先写出明文、随后才在 EOF 宣布成功。本文脚本为了离线恢复安全，会在正文 HMAC 成功以后才提交最终文件。

# 从下载流走到最终源文件



下面开始按实际调用顺序追。这里不是只讲算法，而是要看一份 `.fot` 到底在哪个函数被读、什么时候变成 Reader、什么时候变成 plaintext、最后被写到哪里。

```text
Executor.DownloadFile / Worker.downloadRemoteFile
                 │
                 ▼
      worker/download.Download
                 │
      HTTP Response.Body (io.Reader)
                 │
                 ▼
          NewDecryptReader
                 │
                 ▼
       NewDecryptSliceReader
       ├─ 读固定头和 metadata
       ├─ 验证 usability
       ├─ 验证并解密 filename
       └─ 建立正文 LimitedReader + CTR + HMAC
                 │
                 ▼
             CopyBuffer
                 │
                 ▼
       DecryptSliceReader.Read
       ├─ 读 ciphertext chunk
       ├─ ciphertext → HMAC
       ├─ ciphertext → AES-CTR → plaintext
       └─ plaintext → os.File
                 │
                 ▼
       读最终 16-byte tag，比较 HMAC
                 │
                 ▼
       认证成功：恢复出的原文件
```

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:16px 0">
  <div style="border:1px solid #888;border-radius:8px;padding:10px"><b>输入</b><br><code>Response.Body</code><br>FOT 原始字节</div>
  <div style="border:1px solid #888;border-radius:8px;padding:10px"><b>头解析</b><br>magic / version / length<br>metadata / password</div>
  <div style="border:1px solid #888;border-radius:8px;padding:10px"><b>正文流</b><br>ciphertext → HMAC<br>ciphertext → CTR</div>
  <div style="border:1px solid #888;border-radius:8px;padding:10px"><b>输出</b><br>plaintext → temporary<br>tag success → final</div>
</div>

## 第 0 步：上层只有对象信息，还没有 FOT 字节

两条入口最终都会调用下载器：`Executor.DownloadFile` 在 `0x757540`，`Worker.downloadRemoteFile` 在 `0x760120`。前者偏向单文件任务，后者处理远程对象、driver 和重试。

`Worker.downloadRemoteFile` 会先取得下载链接，在 `0x760774` 或 `0x7607b4` 调用共同下载器。对于失败的恢复，它会走到 `0x7608e0` 尝试清理目标文件。

下载器 `worker/download.Download` 位于 `0x74e690`。它把 HTTP 请求跑成功以后，从 `Response.Body` 取出 `io.Reader`，然后判断目标名是否以 `.fot` 结尾，以及上层是不是给了非空口令。只有这两个条件都满足，`Response.Body` 才会进入 `NewDecryptReader`。

| 输入字节/状态 | 当前函数 | 状态变化 | 下一跳 |
|---|---|---|---|
| 下载 URL、对象信息 | `0x757540` / `0x760120` | driver 返回可读 URL | `0x74e690` |
| HTTP 响应体 | `0x74e690` | `Response.Body` 变为底层 `io.Reader` | `0x74f31c` |
| Reader + password bytes | `0x7240e0` | 构造解密 Reader | `0x724220` |

`Response.Body` 在 Go 反编译里会显示为 `tab/data` 两个字。这个不是两段数据，而是一个接口的运行时表示：`tab` 是方法表，`data` 指向实际 Reader。看这种 Go 代码时，先把它们理解成“同一条输入流”就够了。

## 第 1 步：先读 19 bytes，再读剩余 header

`NewDecryptSliceReader` 的开头会先读 19 bytes：8 bytes magic、1 byte version、2 bytes `header_len`、8 bytes `total_len`。

这里可以先按 19 bytes 理解：8 bytes magic、1 byte version、2 bytes `header_len`、8 bytes `total_len`。后面的“反编译容易误读的地方”会用一小段汇编确认这个长度和字节序，避免在流程正文里重复贴指令。

之后会比较 magic 和 version。`header_len` 是大端 16 位，`total_len` 最终通过 64 位字节翻转转为宿主序。版本变了、格式变了或者有人照抄时把大端写成小端，这两个位置一般最先暴露问题。

```text
FOT bytes [0 : 19]
   │
   ├─ magic[8]       → 格式识别
   ├─ version[1]     → 版本识别
   ├─ header_len[2]  → 读取剩余头、切 metadata
   └─ total_len[8]   → 计算正文和最后 tag 的边界
```

## 第 2 步：metadata 决定能不能继续，以及输出文件叫什么

读完剩余头以后，解析器从固定头的第 45 byte 开始按 entry 循环切 metadata。

`filename` 和 `usability` 是两个关键 entry：

```text
usability
  Base64
    └─ IV[16] || encrypted_fixed_check[38]
         └─ PBKDF2(100) + AES-256-CTR
              └─ fixed-check plaintext

filename
  Base64
    └─ encrypted_filename || hmac_tag[16]
         ├─ PBKDF2(1000) + HMAC-SHA256，先验 tag
         └─ PBKDF2(1000) + AES-256-CTR(data_iv)，后解密文件名
```

文件名必须先认证、后使用。通过以后，下载器会在 `0x74f49c` 调 `filepath.Join`，然后在 `0x74f4d0` `os.OpenFile` 打开输出文件。

```text
metadata["filename"]
   │
   ▼
Base64 decode
   │
   ├─ ciphertext
   └─ last 16 bytes tag
          │
          ▼
HMAC verify
   │ success
   ▼
AES-CTR decrypt(data_iv)
   │
   ▼
verified filename
   │
   ▼
filepath.Join(Dir(fot_path), filename)
   │
   ▼
os.OpenFile
```

这也是原程序和恢复工具的第一个不同点：原程序会直接拿已认证文件名参与 `filepath.Join`；下面给出的独立恢复器在此基础上再拒绝 `/`、`\\`、`.`、`..`，并强制输出只能落在指定目录内。

## 第 3 步：正文密文按块进 HMAC 和 CTR

头、口令、文件名都通过以后，`NewDecryptSliceReader` 不会一次解全文。它返回一个带状态的 Reader，里面保存：

```text
底层 LimitedReader
AES-256-CTR 状态
HMAC-SHA256 状态
remain（正文密文剩余长度）
verified filename
声明的总长度与明文长度
```

`CopyBuffer` / `io.CopyBuffer` 反复调用 `DecryptSliceReader.Read`。每次最多取约 4096 bytes ciphertext：原始 ciphertext 先送进 HMAC，随后走同一个连续的 CTR 状态得到 plaintext，再由 `io.CopyBuffer` 写进已经打开的 `os.File`。

```text
ciphertext chunk
  │
  ├──► HMAC-SHA256.Update(ciphertext chunk)
  │
  └──► AES-256-CTR.XORKeyStream
             │
             ▼
       plaintext chunk
             │
             ▼
          io.CopyBuffer
             │
             ▼
          os.File.Write
```

注意 HMAC 和 CTR 在反编译中的调用顺序不应该只按源代码表面猜。当前 IDA 输出里，CTR 处理调用点在 `0x725390`，HMAC 写入在 `0x7253b0`；两者的输入都是刚从底层 reader 读到的同一个 ciphertext chunk。它们没有对明文做 HMAC。

## 第 4 步：最后 16 bytes 决定这次恢复算不算成功

正文 `remain` 用完以后，Reader 不能马上给成功 EOF。它还要从底层 reader 再取 16 bytes，和累计 HMAC 的前 16 bytes 做逐字节 XOR 聚合比较。

```text
body ciphertext exhausted
       │
       ▼
read final tag[16]
       │
       ▼
computed = HMAC-SHA256(content_key, all ciphertext)[:16]
       │
       ▼
constant-time-style XOR aggregate compare
       │
  ┌────┴────┐
  │         │
match     mismatch / missing tag
  │         │
  ▼         ▼
EOF       error
```

这也解释了一个容易误会的点：原程序是流式写盘的。输出文件在 `0x74f4d0` 就已经打开，正文在最终 tag 验证前可能已经写出一部分。`Worker.downloadRemoteFile` 的失败路径会尝试清理，但下载器本身不是“先全文认证、再原子落盘”的设计。

独立恢复器则选择更保守的方式：先完整认证，随后写入 `.partial`，最后 `rename` 为最终文件。对于离线恢复来说，这个语义更适合做取证和批量恢复。

【待配图】

# 原程序和独立恢复器的写盘差异

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:16px 0">
  <div style="border:1px solid #b66;border-radius:8px;padding:12px">
    <b>原程序：低内存流式路径</b><br>
    header / filename 认证<br>
    → 打开最终文件<br>
    → 解密并写正文<br>
    → 最后比较 body tag<br>
    → 失败时由上层尽力清理
  </div>
  <div style="border:1px solid #6a6;border-radius:8px;padding:12px">
    <b>独立恢复器：保守提交路径</b><br>
    header / filename / body tag 全部认证<br>
    → 写 <code>.partial</code><br>
    → 原子替换最终文件<br>
    → 失败时不提交最终输出
  </div>
</div>

| 项目 | 原程序下载路径 | 本文恢复器 |
|---|---|---|
| `filename` | HMAC 通过后才用于输出路径 | HMAC 通过后还会额外做路径检查 |
| 正文 HMAC | 边读、边累计，EOF 时验证 | 先验证全部正文，再写文件 |
| 输出打开 | 最终 tag 之前 | 所有认证成功后 |
| tag 错误 | 上层某些路径会清理目标 | 不创建最终输出 |

# 可运行的恢复器

下面这份脚本就是按上面的格式写的。它没有任何口令枚举逻辑，只使用当前已验证的恢复口令 `b"0"`；输入不是 FOT、头长度不对、metadata 被截断、口令错误、文件名 HMAC 错误、正文 HMAC 错误，都会失败。

它依赖系统的 `openssl enc -aes-256-ctr`，先用 NIST AES-256-CTR 向量做自检。恢复时不覆盖已有输出，并且在所有认证成功以后才写入最终文件。

```python
#!/usr/bin/env python3
"""使用已验证备份口令 b"0" 恢复经过认证的 FOT 对象。

脚本严格执行格式、文件名和正文 HMAC 检查；不会进行口令猜测，
并且只在所有检查成功后才提交输出文件。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

FOT_MAGIC = bytes.fromhex("464f54a31c77005e")
FOT_VERSION = 1
FIXED_HEADER_LENGTH = 45
TAG_LENGTH = 16
USABILITY_PLAINTEXT = b"teirenfeiniuyunpanbeifencryptoencrypto"
KDF_PREFIX = bytes.fromhex("3ca15ef9d2076b")
RECOVERY_PASSWORD = b"0"


class FOTError(ValueError):
    pass


@dataclass(frozen=True)
class FOTHeader:
    header_length: int
    total_length: int
    data_iv: bytes
    salt: bytes
    padding: int
    metadata: dict[str, bytes]


def derive_key(password: bytes, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, KDF_PREFIX + salt, iterations, 32)


def aes_ctr_crypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    try:
        completed = subprocess.run(
            [
                "openssl",
                "enc",
                "-aes-256-ctr",
                "-nosalt",
                "-K",
                key.hex(),
                "-iv",
                iv.hex(),
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as error:
        raise FOTError("未找到 openssl；需要 OpenSSL 的 aes-256-ctr 支持") from error
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise FOTError(f"OpenSSL AES-CTR 处理失败：{message}")
    return completed.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FOTError(message)


def parse_header(blob: bytes) -> FOTHeader:
    require(len(blob) >= FIXED_HEADER_LENGTH + TAG_LENGTH, "文件小于最小 FOT 长度")
    require(blob[:8] == FOT_MAGIC, "FOT 魔数不匹配")
    require(blob[8] == FOT_VERSION, f"不支持的 FOT 版本：{blob[8]}")

    header_length = int.from_bytes(blob[9:11], "big")
    total_length = int.from_bytes(blob[11:19], "big")
    require(header_length >= FIXED_HEADER_LENGTH, "header_len 小于固定头长度")
    require(header_length <= len(blob) - TAG_LENGTH, "header_len 超出文件边界")
    require(total_length == len(blob), "total_len 与实际文件长度不一致")

    metadata: dict[str, bytes] = {}
    offset = FIXED_HEADER_LENGTH
    while offset < header_length:
        require(offset + 3 <= header_length, "元数据条目头被截断")
        entry_length = int.from_bytes(blob[offset : offset + 2], "big")
        key_length = blob[offset + 2]
        require(entry_length >= key_length + 3, "元数据 entry_len 非法")
        entry_end = offset + entry_length
        require(entry_end <= header_length, "元数据条目越过 header_len")
        key_end = offset + 3 + key_length
        require(key_end <= entry_end, "元数据键长度非法")
        try:
            key = blob[offset + 3 : key_end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise FOTError("元数据键不是 UTF-8") from error
        require(key not in metadata, f"重复的元数据键：{key}")
        metadata[key] = blob[key_end:entry_end]
        offset = entry_end

    require(offset == header_length, "元数据未恰好结束于 header_len")
    return FOTHeader(
        header_length=header_length,
        total_length=total_length,
        data_iv=blob[19:35],
        salt=blob[35:44],
        padding=blob[44],
        metadata=metadata,
    )


def decode_metadata(name: str, metadata: dict[str, bytes]) -> bytes:
    try:
        encoded = metadata[name]
    except KeyError as error:
        raise FOTError(f"缺少必需元数据：{name}") from error
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as error:
        raise FOTError(f"{name} 不是合法 Base64") from error


def validate_password(header: FOTHeader, password: bytes) -> None:
    usability = decode_metadata("usability", header.metadata)
    require(
        len(usability) == TAG_LENGTH + len(USABILITY_PLAINTEXT),
        "usability 长度不符合 IV(16)+固定检查值密文(38) 的格式",
    )
    usability_iv = usability[:TAG_LENGTH]
    recovered = aes_ctr_crypt(derive_key(password, header.salt, 100), usability_iv, usability[TAG_LENGTH:])
    require(
        hmac.compare_digest(recovered, USABILITY_PLAINTEXT),
        "口令错误，或 usability 元数据已损坏",
    )


def decrypt_filename(header: FOTHeader, password: bytes) -> str:
    packed = decode_metadata("filename", header.metadata)
    require(len(packed) >= TAG_LENGTH, "filename 缺少 16 字节 HMAC 标签")
    encrypted_name, stored_tag = packed[:-TAG_LENGTH], packed[-TAG_LENGTH:]
    key = derive_key(password, header.salt, 1000)
    expected_tag = hmac.new(key, encrypted_name, hashlib.sha256).digest()[:TAG_LENGTH]
    require(
        hmac.compare_digest(expected_tag, stored_tag),
        "filename HMAC 不匹配：口令错误或元数据已损坏",
    )
    plaintext = aes_ctr_crypt(key, header.data_iv, encrypted_name)
    try:
        filename = plaintext.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FOTError("已认证的 filename 不是 UTF-8") from error
    require(filename not in ("", ".", ".."), "恢复的文件名为空或无效")
    require("/" not in filename and "\\" not in filename, "恢复的文件名包含路径分隔符")
    return filename


def decrypt_body(blob: bytes, header: FOTHeader, password: bytes) -> bytes:
    ciphertext = blob[header.header_length:-TAG_LENGTH]
    stored_tag = blob[-TAG_LENGTH:]
    key = derive_key(password, header.salt, 10000)
    expected_tag = hmac.new(key, ciphertext, hashlib.sha256).digest()[:TAG_LENGTH]
    require(
        hmac.compare_digest(expected_tag, stored_tag),
        "正文 HMAC 不匹配：口令错误或文件被截断/损坏",
    )
    return aes_ctr_crypt(key, header.data_iv, ciphertext)


def output_path(output_dir: Path, filename: str) -> Path:
    destination = output_dir / filename
    resolved_directory = output_dir.resolve()
    resolved_destination = destination.resolve()
    require(
        os.path.commonpath((str(resolved_directory), str(resolved_destination))) == str(resolved_directory),
        "恢复目标逃逸出输出目录",
    )
    require(not destination.exists(), f"拒绝覆盖已有输出文件：{destination}")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="离线恢复经过认证的 FOT 文件；仅使用提供的口令，不进行任何口令猜测。"
    )
    parser.add_argument("input", type=Path, nargs="?", help="输入 .fot 文件")
    parser.add_argument("--output-dir", type=Path, help="必须不存在或为空的新恢复目录")
    parser.add_argument("--self-test", action="store_true", help="使用 NIST AES-CTR 测试向量验证本机 OpenSSL")
    args = parser.parse_args()

    if args.self_test:
        key = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
        iv = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
        plaintext = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        expected = bytes.fromhex("601ec313775789a5b7a7f504bbf3d228")
        try:
            require(aes_ctr_crypt(key, iv, plaintext) == expected, "AES-CTR 测试向量不匹配")
        except FOTError as error:
            print(f"自检失败：{error}", file=sys.stderr)
            return 1
        print("AES-256-CTR 自检成功")
        return 0

    if args.input is None or args.output_dir is None:
        parser.error("恢复模式需要 input 和 --output-dir")

    try:
        blob = args.input.read_bytes()
        password = RECOVERY_PASSWORD
        require(not args.output_dir.exists() or not any(args.output_dir.iterdir()), "输出目录必须不存在或为空")

        header = parse_header(blob)
        validate_password(header, password)
        filename = decrypt_filename(header, password)
        plaintext = decrypt_body(blob, header, password)

        args.output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_path(args.output_dir, filename)
        temporary = destination.with_name(destination.name + ".partial")
        require(not temporary.exists(), f"临时输出已存在：{temporary}")
        temporary.write_bytes(plaintext)
        temporary.replace(destination)

        print(f"恢复成功：{destination}")
        print(f"明文长度：{len(plaintext)}")
        print(f"FOT 头长度：{header.header_length}；文件总长度：{header.total_length}")
        return 0
    except (OSError, FOTError) as error:
        print(f"恢复失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

保存为 `fot_decrypt.py` 后，先测试本机 CTR：

```shell
python3 fot_decrypt.py --self-test
```

输出应为：

```text
AES-256-CTR 自检成功
```

恢复时指定 `.fot` 和一个不存在或为空的输出目录：

```shell
python3 fot_decrypt.py 03aceacb3e8c37ff8fad811ab48874620000000000000772000000006a6a099f.fot --output-dir recovered_picture
```

# 示例对象的验证

本文只使用这一份对象：

```text
8b36db3766ba752c45e350f777494ed8c14275db8d6c7a197bb5718eeb4114e1  03aceacb3e8c37ff8fad811ab48874620000000000000772000000006a6a099f.fot
```

| FOT 对象 | 头长度 | 对象长度 | 恢复文件 | 明文长度 | 实际内容 |
|---|---:|---:|---|---:|---|
| `03aceacb3e8c37ff8fad811ab48874620000000000000772000000006a6a099f.fot` | 176 | 2,098 | `4781595.jpg` | 1,906 | 64 × 64 JPEG。 |

实际恢复输出：

```text
恢复成功：.../picture/4781595.jpg
明文长度：1906
FOT 头长度：176；文件总长度：2098
```

除了成功情况，也测了几个失败边界：

```text
正文尾 tag 翻转 1 bit
  → 恢复失败：正文 HMAC 不匹配：口令错误或文件被截断/损坏
  → 不创建输出目录

把 FOT 截断
  → 恢复失败：header_len 超出文件边界
  → 不创建输出目录

把恢复目录保留为非空后重复运行
  → 恢复失败：输出目录必须不存在或为空
  → 已有文件不被覆盖
```

【待配图】

# 完整反编译：按数据流看，不要先被 Go ABI 吓到

下面开始保留完整 listing。Go 的反编译里会有 `tab/data`、GC write barrier、切片边界检查和大量 `vNN` 局部变量。这些大多是 ABI 和运行时噪声。

看变体时优先盯住下面几类锚点：

1. `.fot` 后缀判断和 `Response.Body` 进入哪个构造器；
2. 19-byte 首次读取、magic/version、`REV16`、大端总长度；
3. `usability` / `filename` 字符串和三组 PBKDF2 轮数；
4. `NewCTR`、`hmac.New`、`LimitedReader`；
5. `ReadAtLeast(tag[16])`、HMAC tag 比较；
6. `filepath.Join`、`OpenFile`、`CopyBuffer`。

每段 listing 前都会先说它接收什么数据、产出什么状态、下一跳是谁。这样拿去对照变体时，不必期待地址完全相同；只要字段结构、常量、调用关系和数据流还在，通常就能重新定位。

## 1. 外层解密 Reader：第一个对象的入口

`0x7240e0` 把底层 Reader 和 password 交给 `0x724220`。第一个 FOT 头是在这里构造时解析的，不是等第一次正文读取。

```c
// git.teiron-inc.cn/services/backup-cloud/crypto.NewDecryptReader
retval_7240E0 __golang git_teiron_inc_cn_services_backup_cloud_crypto_NewDecryptReader(
        __int64 a1,
        __int64 a2,
        __int64 a3,
        __int64 a4,
        __int64 a5)
{
  __int64 r0; // x3
  __int64 v7; // x4
  __int64 v8; // x5
  __int64 v9; // x6
  _QWORD *v10; // x25
  __int64 v13; // [xsp+38h] [xbp-30h]
  __int64 v14; // [xsp+48h] [xbp-20h]
  retval_7240E0 result; // 0:x0.24
  retval_724220 v21; // 0:kr00_24.24

  v21 = git_teiron_inc_cn_services_backup_cloud_crypto_NewDecryptSliceReader(); /*0x72410c*/
  result._r1 = v21._r1; /*0xf1c0000000000008*/
  result._r2 = v21._r2; /*0xf1c000000000000c*/
  if ( v21._r1 ) /*0x724110*/
  {
    result._r0 = 0; /*0x7241d4*/
  }
  else
  {
    v14 = *(_QWORD *)(v21._r0 + 104LL); /*0x724124*/
    v13 = *(_QWORD *)(v21._r0 + 112LL); /*0x72412c*/
    result._r0 = runtime_newobject(&RTYPE_git_teiron_inc_cn_services_backup_cloud_crypto_DecryptReader); /*0x724138*/
    if ( dword_BE34B0 ) /*0x724144*/
    {
      result._r0 = runtime_gcWriteBarrier4(result._r0); /*0x72415c*/
      r0 = v21._r0; /*0x724160*/
      *v10 = v21._r0; /*0x724164*/
      v7 = a2; /*0x724168*/
      v10[1] = a2; /*0x72416c*/
      v8 = a3; /*0x724170*/
      v10[2] = a3; /*0x724174*/
      v9 = v14; /*0x724178*/
      v10[3] = v14; /*0x72417c*/
    }
    else
    {
      r0 = v21._r0; /*0x724148*/
      v7 = a2; /*0x72414c*/
      v8 = a3; /*0x724150*/
      v9 = v14; /*0x724154*/
    }
    *(_QWORD *)result._r0 = r0; /*0x724180*/
    *(_QWORD *)(result._r0 + 8LL) = a1; /*0x724188*/
    *(_QWORD *)(result._r0 + 16LL) = v7; /*0x72418c*/
    *(_QWORD *)(result._r0 + 32LL) = a4; /*0x724194*/
    *(_QWORD *)(result._r0 + 40LL) = a5; /*0x72419c*/
    *(_QWORD *)(result._r0 + 24LL) = v8; /*0x7241a0*/
    *(_QWORD *)(result._r0 + 56LL) = v13; /*0x7241a8*/
    *(_QWORD *)(result._r0 + 48LL) = v9; /*0x7241ac*/
    *(_QWORD *)(result._r0 + 64LL) = *(_QWORD *)(r0 + 120); /*0x7241b4*/
    *(_QWORD *)(result._r0 + 72LL) = *(_QWORD *)(r0 + 128); /*0x7241bc*/
    result._r1 = 0; /*0x7241c0*/
    result._r2 = v21._r2; /*0x7241c4*/
  }
  return result; /*0x7241d0*/
}
```

## 2. 头解析器：FOT 字节流变成带密码学状态的 Reader

`0x724220` 是整个格式恢复最关键的函数。输入是网络或文件 Reader 和 password；输出则是保存 LimitedReader、CTR、HMAC、剩余正文长度、原文件名和长度信息的解密子 Reader。

需要在变体里对照的点：19-byte 首读、版本检查、`REV16` 的 entry/header 长度、`usability`、`filename`、PBKDF2 的 100/1000/10000、`NewCTR`、`hmac.New` 和 `io.LimitedReader`。

```c
// git.teiron-inc.cn/services/backup-cloud/crypto.NewDecryptSliceReader
retval_724220 __golang git_teiron_inc_cn_services_backup_cloud_crypto_NewDecryptSliceReader(
        void *a1,
        void *a2,
        __int64 a3,
        __int64 a4,
        __int64 a5)
{
  char v7; // w0
  unsigned __int64 v8; // x1
  unsigned __int64 v9; // x1
  __int64 v10; // x2
  __int64 v11; // x0
  __int64 v12; // x0
  unsigned __int64 v13; // x0
  __int64 v14; // x1
  unsigned __int64 v15; // x2
  signed __int64 v16; // x3
  __int64 v17; // x4
  __int64 v18; // x5
  __int64 v19; // x6
  __int64 v20; // x7
  __int64 v21; // x8
  __int64 v23; // x4
  unsigned __int64 v24; // x5
  __int64 v25; // x2
  __int64 r2; // x3
  unsigned __int64 v27; // x9
  unsigned __int64 v28; // x10
  __int64 v29; // x11
  unsigned __int64 v30; // x12
  __int64 v31; // x9
  __int64 v32; // x13
  unsigned __int64 v33; // x14
  unsigned __int64 v34; // x4
  __int64 v35; // x2
  __int64 v36; // x3
  _QWORD *v37; // x0
  __int64 v38; // x5
  _QWORD *v39; // x25
  __int64 v40; // x1
  __int64 v41; // x7
  __int64 v42; // x8
  __int64 v43; // x9
  uint8 *v44; // x10
  __int64 v45; // x0
  __int64 v46; // x2
  __int64 v47; // x7
  __int64 v48; // x8
  __int64 v49; // x1
  uint8 *v50; // x9
  __int64 v51; // x1
  unsigned __int8 v52; // w2
  char v53; // w4
  __int64 v54; // x6
  __int64 v55; // x7
  __int64 v56; // x8
  uint8 *v57; // x9
  io_LimitedReader *p_io_LimitedReader; // x0
  void *v59; // x1
  _QWORD *v60; // x25
  io_LimitedReader *v61; // x3
  io_LimitedReader *v62; // x4
  io_LimitedReader *v63; // x5
  io_LimitedReader **v64; // x25
  unsigned __int64 v65; // x3
  __int64 v66; // x4
  _QWORD *v67; // x25
  __int64 r0; // x0
  unsigned __int64 r1; // x1
  __int64 v70; // [xsp+90h] [xbp-368h]
  __int64 v71; // [xsp+A8h] [xbp-350h]
  __int64 v72; // [xsp+B0h] [xbp-348h]
  __int64 v73; // [xsp+B8h] [xbp-340h]
  unsigned __int64 v74; // [xsp+C0h] [xbp-338h]
  __int64 v75; // [xsp+D0h] [xbp-328h]
  __int64 v76; // [xsp+D8h] [xbp-320h]
  __int64 v77; // [xsp+E0h] [xbp-318h]
  unsigned __int64 v78; // [xsp+E8h] [xbp-310h]
  unsigned __int64 v79; // [xsp+F0h] [xbp-308h]
  __int64 v80; // [xsp+F8h] [xbp-300h]
  __int64 v81; // [xsp+100h] [xbp-2F8h]
  __int64 v82; // [xsp+108h] [xbp-2F0h]
  __int64 v83; // [xsp+110h] [xbp-2E8h]
  __int64 v84; // [xsp+118h] [xbp-2E0h]
  __int64 v85; // [xsp+120h] [xbp-2D8h]
  unsigned __int64 v86; // [xsp+128h] [xbp-2D0h]
  unsigned __int64 v87; // [xsp+130h] [xbp-2C8h]
  unsigned __int64 v88; // [xsp+138h] [xbp-2C0h]
  __int64 v89; // [xsp+140h] [xbp-2B8h]
  __int64 v90; // [xsp+148h] [xbp-2B0h]
  __int64 v91; // [xsp+150h] [xbp-2A8h]
  __int64 v92; // [xsp+158h] [xbp-2A0h]
  __int64 v93; // [xsp+160h] [xbp-298h]
  unsigned __int64 v94; // [xsp+170h] [xbp-288h]
  __int64 v95; // [xsp+178h] [xbp-280h]
  unsigned __int64 v96; // [xsp+198h] [xbp-260h]
  __int64 v97; // [xsp+1A0h] [xbp-258h]
  unsigned __int64 v98; // [xsp+1A8h] [xbp-250h]
  __int64 v99; // [xsp+1A8h] [xbp-250h]
  __int64 v100; // [xsp+1A8h] [xbp-250h]
  __int64 v101; // [xsp+1A8h] [xbp-250h]
  __int64 v102; // [xsp+1A8h] [xbp-250h]
  unsigned __int64 v103; // [xsp+1B0h] [xbp-248h]
  unsigned __int64 v104; // [xsp+1B0h] [xbp-248h]
  __int64 v105; // [xsp+1C8h] [xbp-230h]
  io_LimitedReader *v106; // [xsp+1D8h] [xbp-220h]
  __int64 v107; // [xsp+1E0h] [xbp-218h]
  __int64 v108; // [xsp+1E8h] [xbp-210h]
  __int64 v109; // [xsp+1F0h] [xbp-208h]
  __int64 v110; // [xsp+200h] [xbp-1F8h]
  __int64 v111; // [xsp+208h] [xbp-1F0h]
  uint8 *v112; // [xsp+210h] [xbp-1E8h]
  uint8 *v113; // [xsp+218h] [xbp-1E0h]
  uint8 *v114; // [xsp+220h] [xbp-1D8h]
  __int64 v115; // [xsp+228h] [xbp-1D0h]
  __int64 v116; // [xsp+230h] [xbp-1C8h]
  __int64 v117; // [xsp+238h] [xbp-1C0h]
  __int64 v118; // [xsp+240h] [xbp-1B8h]
  io_LimitedReader *v119; // [xsp+250h] [xbp-1A8h]
  io_LimitedReader *v120; // [xsp+268h] [xbp-190h]
  __int64 v121; // [xsp+270h] [xbp-188h]
  __int64 v122; // [xsp+278h] [xbp-180h]
  __int64 v123; // [xsp+280h] [xbp-178h]
  __int64 v124; // [xsp+288h] [xbp-170h]
  __int64 v125; // [xsp+288h] [xbp-170h]
  __int64 v126; // [xsp+288h] [xbp-170h]
  char v127; // [xsp+290h] [xbp-168h] BYREF
  __int64 v128; // [xsp+3A0h] [xbp-58h] BYREF
  __int64 v129; // [xsp+3A8h] [xbp-50h]
  char *v130; // [xsp+3B0h] [xbp-48h]
  __int64 v131; // [xsp+3B8h] [xbp-40h]
  __int64 v132; // [xsp+3C0h] [xbp-38h]
  __int64 v133; // [xsp+3C8h] [xbp-30h]
  _QWORD v134[2]; // [xsp+3D0h] [xbp-28h] BYREF
  __int64 v135; // [xsp+3E0h] [xbp-18h] BYREF
  __int64 v136; // [xsp+3E8h] [xbp-10h]
  retval_470A10 v142; // 0:x0.16
  retval_470A10 v143; // 0:x0.16
  retval_512A20 v144; // 0:x0.16
  retval_470A10 v145; // 0:x0.16
  retval_512A20 v146; // 0:x0.16
  retval_5B4A70 v147; // 0:x0.16
  retval_512A20 v148; // 0:x0.16
  retval_5B4A70 v149; // 0:x0.16
  retval_724220 result; // 0:x0.24
  retval_470100 v151; // 0:kr20_24.24
  retval_470100 v152; // 0:kr48_24.24
  retval_470100 v153; // 0:kr80_24.24
  retval_470100 v154; // 0:krE0_24.24
  retval_723D20 v155; // 0:kr108_24.24
  retval_724B74 v156; // 0:kr140_24.24
  retval_470100 v157; // 0:kr168_24.24
  retval_723D20 v158; // 0:kr180_24.24
  retval_4A96E0 All; // 0:kr00_32.32
  retval_4F7320 v160; // 0:kr60_32.32
  retval_515D90 v161; // 0:krA0_32.32
  retval_4F7320 v162; // 0:krC0_32.32
  retval_515D90 v163; // 0:kr120_32.32
  retval_515D90 v164; // 0:kr1A0_32.32

  v124 = runtime_makeslice(&RTYPE_uint8); /*0x724268*/
  result._r1 = io_ReadAtLeast(a1, a2, v124)._r1; /*0x724284*/
  if ( result._r1 ) /*0x724288*/
  {
    result._r0 = 0; /*0x7245bc*/
    return result; /*0x7245c8*/
  }
  if ( qword_B73528 <= 19 ) /*0x724298*/
    v7 = runtime_memequal(v124, off_B73520); /*0x7242b0*/
  else
    v7 = 0; /*0x72429c*/
  if ( (v7 & 1) == 0 )
  {
    All = io_ReadAll(a1, a2); /*0x72447c*/
    r0 = All._r0; /*0xf1c0000000000004*/
    r1 = All._r1; /*0xf1c0000000000008*/
    if ( All._r3 )
    {
      v135 = 0; /*0x724490*/
      v136 = 0; /*0x724490*/
      v135 = *(_QWORD *)(All._r3 + 8LL); /*0x72449c*/
      v136 = v23; /*0x7244a0*/
      fmt_Fprintf(off_9081A0, qword_BB97E8, "[NewDecryptSliceReader]Failed to read remaining data: %v\n", 57, &v135);
      r0 = All._r0; /*0x7244d0*/
      r1 = All._r1; /*0x7244d4*/
    }
    v24 = r1 + 19; /*0x7244d8*/
    if ( r1 != 0 && r1 < 0xFFFFFFFFFFFFFFEDLL ) /*0x7244dc*/
    {
      v151 = runtime_growslice(v124, r1 + 19, 19, r1, &RTYPE_uint8); /*0x724508*/
      r2 = v151._r2; /*0x72450c*/
      v24 = v151._r1; /*0x724510*/
      r1 = All._r1; /*0x724514*/
      v25 = v151._r0; /*0x724518*/
      r0 = All._r0; /*0x72451c*/
    }
    else
    {
      v25 = v124; /*0x7244e4*/
      r2 = 19; /*0x7244e8*/
    }
    v89 = r2; /*0x724520*/
    v88 = v24; /*0x724524*/
    v116 = v25; /*0x724528*/
    runtime_memmove(v25 + 19, r0, r1); /*0x724544*/
    v134[0] = &RTYPE__slice_uint8; /*0x724568*/
    v134[1] = runtime_convTslice(v116, v88, v89); /*0x72456c*/
    fmt_Fprintf(off_9081A0, qword_BB97E8, "[NewDecryptSliceReader] full hex: % X\n", 38, v134);
    result._r1 = off_B714C0; /*0x7245a0*/
    result._r2 = off_B714C8; /*0x7245a8*/
    result._r0 = 0; /*0x7245ac*/
    return result; /*0x7245b8*/
  }
  if ( (unsigned __int64)qword_B73528 >= 0x13 ) /*0x7242c4*/
    ((void (__noreturn *)(void))runtime_panicIndex)(); /*0x724f5c*/
  if ( *(unsigned __int8 *)(v124 + qword_B73528) != (unsigned __int8)byte_B859E2 ) /*0x7242dc*/
  {
    result._r1 = off_B714D0; /*0x724458*/
    result._r2 = off_B714D8; /*0x724460*/
    result._r0 = 0; /*0x724464*/
    return result; /*0x724470*/
  }
  v8 = qword_B73528 + 3; /*0x7242e0*/
  if ( (unsigned __int64)(qword_B73528 + 3) > 0x13 ) /*0x7242e8*/
    ((void (__noreturn *)(void))runtime_panicSliceAcap)(); /*0x724f54*/
  if ( v8 < qword_B73528 + 1 ) /*0x7242f4*/
    runtime_panicSliceB(qword_B73528 + 1); /*0x724f4c*/
  if ( (unsigned __int64)(16 - qword_B73528) <= 7 ) /*0x724320*/
    runtime_panicIndex(7, 16 - qword_B73528); /*0x724f44*/
  v79 = (unsigned __int16)__rev16(*(unsigned __int16 *)(v124 + ((qword_B73528 + 1) & ((qword_B73528 - 18) >> 63)))); /*0x72432c*/
  v74 = v79 - 19; /*0x724334*/
  v103 = *(_QWORD *)(v124 + (v8 & ((qword_B73528 - 16) >> 63))); /*0x72433c*/
  v123 = runtime_makeslice(&RTYPE_uint8); /*0x724350*/
  result._r1 = io_ReadAtLeast(a1, a2, v123)._r1; /*0x72436c*/
  if ( result._r1 ) /*0x724370*/
  {
    result._r0 = 0; /*0x724444*/
    return result; /*0x724450*/
  }
  v9 = v79; /*0x724374*/
  if ( v79 > 0x13 ) /*0x72437c*/
  {
    v152 = runtime_growslice(v124, v79, 19, v74, &RTYPE_uint8); /*0x7243a0*/
    v11 = v152._r0; /*0xf1c0000000000020*/
    v9 = v152._r1; /*0xf1c0000000000024*/
    v10 = v152._r2; /*0xf1c0000000000028*/
  }
  else
  {
    v10 = 19; /*0x724380*/
    v11 = v124; /*0x724384*/
  }
  v87 = v10; /*0x7243a4*/
  v86 = v9; /*0x7243a8*/
  v115 = v11; /*0x7243ac*/
  v122 = v11 + 19; /*0x7243b4*/
  v12 = runtime_memmove(v11 + 19, v123, v74); /*0x7243c4*/
  if ( v87 < 0x23 ) /*0x7243d0*/
    runtime_panicSliceAcap(v12, 35); /*0x724f38*/
  if ( v87 < 0x2C ) /*0x7243d8*/
    runtime_panicSliceAcap(v12, 44); /*0x724f30*/
  v128 = 0; /*0x7243e0*/
  v129 = 0; /*0x7243e0*/
  v130 = nullptr; /*0x7243e8*/
  v131 = 0; /*0x7243e8*/
  v132 = 0; /*0x7243f0*/
  v133 = 0; /*0x7243f0*/
  ((void (__golang *)(__int64))loc_4762BC)(v12); /*0x724404*/
  v130 = &v127; /*0x724410*/
  HIDWORD(v129) = runtime_rand32(); /*0x724418*/
  v13 = v87; /*0x72441c*/
  v14 = v115; /*0x724420*/
  v15 = v86; /*0x724424*/
  v16 = v79; /*0x724428*/
  v17 = 45; /*0x72442c*/
  v18 = 0; /*0x724430*/
  v19 = 0; /*0x724434*/
  v20 = 0; /*0x724438*/
  v21 = 0; /*0x72443c*/
  while ( 1 ) /*0x724600*/
  {
    v90 = v20; /*0x724600*/
    v117 = v21; /*0x724604*/
    if ( v17 >= v16 ) /*0x72460c*/
      break; /*0x72460c*/
    v27 = v17 + 2; /*0x724610*/
    if ( v16 < v17 + 2 ) /*0x724618*/
      break; /*0x724618*/
    if ( v13 < v27 ) /*0x724620*/
      runtime_panicSliceAcap(v13, v17 + 2); /*0x724f28*/
    if ( v17 > v27 ) /*0x724628*/
      runtime_panicSliceB(v17); /*0x724f1c*/
    v28 = v13 - v17; /*0x72462c*/
    v29 = (unsigned __int16)__rev16(*(unsigned __int16 *)(v14 + (v17 & ((__int64)(v17 - v13) >> 63)))); /*0x724640*/
    v30 = v29 + v17; /*0x724644*/
    if ( v16 < v29 + v17 || !v29 ) /*0x724650*/
      break; /*0x724650*/
    if ( v15 <= v27 ) /*0x724658*/
      runtime_panicIndex(v17 + 2, v15); /*0x724f10*/
    v31 = *(unsigned __int8 *)(v14 + v27); /*0x72465c*/
    if ( v29 <= v31 ) /*0x724664*/
      break; /*0x724664*/
    v32 = v31 + v17; /*0x724668*/
    v33 = v31 + v17 + 3; /*0x72466c*/
    if ( v13 < v33 ) /*0x724674*/
      runtime_panicSliceAcap(v13, v31 + v17 + 3); /*0x724f04*/
    v34 = v17 + 3; /*0x724678*/
    if ( v33 < v34 ) /*0x724680*/
      runtime_panicSliceB(v34); /*0x724ef8*/
    v92 = v29; /*0x724684*/
    v76 = v31; /*0x724688*/
    v98 = v33; /*0x72468c*/
    v105 = v19; /*0x724690*/
    v70 = v18; /*0x724694*/
    v97 = v32; /*0x724698*/
    v96 = v30; /*0x72469c*/
    v142 = runtime_slicebytetostring(0, v14 + (v34 & ((__int64)(3 - v28) >> 63)), v31); /*0x7246bc*/
    if ( v87 < v96 ) /*0x7246cc*/
      runtime_panicSliceAcap(v142._r0, v96); /*0x724eec*/
    if ( v98 > v96 ) /*0x7246d8*/
      runtime_panicSliceB(v98); /*0x724ee4*/
    v77 = v142._r1; /*0x7246dc*/
    v111 = v142._r0; /*0x7246e0*/
    v143 = runtime_slicebytetostring(0, v115 + (v98 & ((__int64)(3 - (v87 - v97)) >> 63)), v92 - v76 - 3); /*0x724718*/
    v121 = v143._r0; /*0x72471c*/
    v99 = v143._r1; /*0x724720*/
    if ( v77 == 8 ) /*0x72472c*/
    {
      if ( *(_QWORD *)v111 == 0x656D616E656C6966LL ) /*0x72474c*/
      {
        v35 = v70; /*0x724750*/
        v36 = v105; /*0x724754*/
        goto LABEL_27; /*0x724758*/
      }
    }
    else if ( v77 == 9 && *(_QWORD *)v111 == 0x74696C6962617375LL && *(_BYTE *)(v111 + 8) == 121 ) /*0x72478c*/
    {
      v35 = v143._r1; /*0x724790*/
      v36 = v143._r0; /*0x724794*/
      v143._r1 = v90; /*0x724798*/
      v143._r0 = v117; /*0x72479c*/
      goto LABEL_27; /*0x7247a0*/
    }
    v37 = (_QWORD *)runtime_mapassign_faststr(&RTYPE_map_string_string, &v128); /*0x7247b4*/
    v37[1] = v99; /*0x7247bc*/
    if ( dword_BE34B0 ) /*0x7247c8*/
    {
      v37 = (_QWORD *)runtime_gcWriteBarrier2(); /*0x7247d4*/
      v38 = v121; /*0x7247d8*/
      *v39 = v121; /*0x7247dc*/
      v39[1] = *v37; /*0x7247e4*/
    }
    else
    {
      v38 = v121; /*0x7247cc*/
    }
    *v37 = v38; /*0x7247e8*/
    v35 = v70; /*0x7247ec*/
    v36 = v105; /*0x7247f0*/
    v143._r1 = v90; /*0x7247f4*/
    v143._r0 = v117; /*0x7247f8*/
LABEL_27:
    v17 = v96; /*0x7245cc*/
    v18 = v35; /*0x7245e0*/
    v19 = v36; /*0x7245e4*/
    v20 = v143._r1; /*0x7245e8*/
    v21 = v143._r0; /*0x7245ec*/
    v13 = v87; /*0x7245f0*/
    v14 = v115; /*0x7245f4*/
    v15 = v86; /*0x7245f8*/
    v16 = v79; /*0x7245fc*/
  }
  v40 = v14 + 35; /*0x724800*/
  v109 = v40; /*0x724804*/
  if ( !v18 ) /*0x724808*/
    goto LABEL_60; /*0x724808*/
  v160 = encoding_base64__ptr_Encoding_DecodeString(qword_BB9A38, v19, v18); /*0x72481c*/
  if ( v160._r3 ) /*0x724820*/
  {
    result._r1 = off_B714E0; /*0x724c04*/
    result._r2 = off_B714E8; /*0x724c0c*/
    result._r0 = 0; /*0x724c10*/
  }
  else if ( (__int64)v160._r1 < 16 ) /*0x724828*/
  {
    result._r1 = off_B714F0; /*0x724be4*/
    result._r2 = off_B714F8; /*0x724bec*/
    result._r0 = 0; /*0x724bf0*/
  }
  else
  {
    v41 = qword_B73550; /*0x724850*/
    v42 = qword_B73548; /*0x724858*/
    v43 = qword_B73548 + 9; /*0x72485c*/
    v44 = off_B73540; /*0x72486c*/
    if ( qword_B73550 < (unsigned __int64)(qword_B73548 + 9) ) /*0x724874*/
    {
      v100 = qword_B73548; /*0x724878*/
      v153 = runtime_growslice(off_B73540, qword_B73548 + 9, qword_B73550, 9, &RTYPE_uint8); /*0x724894*/
      v42 = v100; /*0x724898*/
      v44 = (uint8 *)v153._r0; /*0x72489c*/
      v43 = v153._r1; /*0x7248a0*/
      v41 = v153._r2; /*0x7248a4*/
    }
    v82 = v43; /*0x7248a8*/
    v114 = v44; /*0x7248ac*/
    v85 = v41; /*0x7248b0*/
    runtime_memmove(&v44[v42], v109, 9); /*0x7248c0*/
    v45 = ((__int64 (__golang *)(__int64, __int64, __int64, uint8 *, __int64, __int64, __int64, __int64, __int64 (__golang **)()))golang_org_x_crypto_pbkdf2_Key)( /*0x7248f0*/
            a3,
            a4,
            a5,
            v114,
            v82,
            v85,
            100,
            32,
            off_890CC8);
    v161 = crypto_aes_NewCipher(v45); /*0x7248f4*/
    if ( v161._r2 ) /*0x7248f8*/
    {
      result._r0 = 0; /*0x724bc8*/
      result._r1 = v161._r2; /*0x724bcc*/
      result._r2 = v161._r3; /*0x724bd0*/
    }
    else
    {
      v144 = crypto_cipher_NewCTR(v161._r0, v161._r1, v160._r0, 16, v160._r2); /*0x724908*/
      v73 = v144._r0; /*0x72490c*/
      v108 = v144._r1; /*0x724910*/
      v125 = runtime_makeslice(&RTYPE_uint8); /*0x724930*/
      (*(void (__golang **)(__int64, __int64))(v73 + 24))(v108, v125); /*0x72495c*/
      if ( qword_B73568 == v160._r1 - 16LL && (runtime_memequal(v125, off_B73560) & 1) != 0 ) /*0x724984*/
      {
        v13 = v87; /*0x724988*/
        v40 = v109; /*0x72498c*/
        v20 = v90; /*0x724994*/
        v21 = v117; /*0x724998*/
LABEL_60:
        v78 = v13 - 19; /*0x72499c*/
        if ( !v20 ) /*0x7249a4*/
        {
          v145._r0 = 0; /*0x7249a8*/
          v46 = 0; /*0x7249ac*/
          goto LABEL_79; /*0x7249b0*/
        }
        v162 = encoding_base64__ptr_Encoding_DecodeString(qword_BB9A38, v21, v20); /*0x7249c4*/
        if ( v162._r3 || (__int64)v162._r1 < 16 ) /*0x7249d0*/
        {
          result._r1 = off_B71510; /*0x7249d8*/
          result._r2 = off_B71518; /*0x7249e0*/
          result._r0 = 0; /*0x7249e4*/
        }
        else
        {
          v95 = v162._r1 - 16LL; /*0x724a00*/
          v47 = qword_B73550; /*0x724a18*/
          v48 = qword_B73548; /*0x724a20*/
          v49 = qword_B73548 + 9; /*0x724a24*/
          v50 = off_B73540; /*0x724a34*/
          if ( qword_B73550 < (unsigned __int64)(qword_B73548 + 9) ) /*0x724a3c*/
          {
            v101 = qword_B73548; /*0x724a40*/
            v154 = runtime_growslice(off_B73540, v49, qword_B73550, 9, &RTYPE_uint8); /*0x724a58*/
            v49 = v154._r1; /*0xf1c000000000006c*/
            v48 = v101; /*0x724a5c*/
            v50 = (uint8 *)v154._r0; /*0x724a60*/
            v47 = v154._r2; /*0x724a64*/
          }
          v84 = v47; /*0x724a68*/
          v113 = v50; /*0x724a6c*/
          v81 = v49; /*0x724a70*/
          runtime_memmove(&v50[v48], v109, 9); /*0x724a80*/
          v155 = golang_org_x_crypto_pbkdf2_Key(a3, a4, a5, v113, v81, v84, 1000, 32, off_890CC8); /*0x724ab0*/
          v163 = crypto_aes_NewCipher(v155._r0); /*0x724ac0*/
          if ( v163._r2 ) /*0x724ac4*/
          {
            result._r0 = 0; /*0x724b90*/
            result._r1 = v163._r2; /*0x724b94*/
            result._r2 = v163._r3; /*0x724b98*/
          }
          else
          {
            v146 = crypto_cipher_NewCTR(v163._r0, v163._r1, v122, 16, v78); /*0x724ad4*/
            v72 = v146._r0; /*0x724ad8*/
            v107 = v146._r1; /*0x724adc*/
            v126 = runtime_makeslice(&RTYPE_uint8); /*0x724af4*/
            (*(void (__golang **)(__int64, __int64))(v72 + 24))(v107, v126); /*0x724b20*/
            v147 = crypto_hmac_New(off_890CC8, v155._r0, v155._r1, v155._r2); /*0x724b38*/
            v75 = v147._r0; /*0x724b3c*/
            v110 = v147._r1; /*0x724b40*/
            (*(void (__golang **)(_QWORD, _QWORD, __int64, _QWORD))(v147._r0 + 56LL))(v147._r1, v162._r0, v95, v162._r2); /*0x724b58*/
            v156 = ((retval_724B74 (__golang *)(__int64, _QWORD, _QWORD, _QWORD))*(_QWORD *)(v75 + 48))(v110, 0, 0, 0); /*0x724b74*/
            if ( v156._r2 < 0x10u ) /*0x724b7c*/
              runtime_panicSliceAcap(v156._r0, 16); /*0x724ed8*/
            v51 = 0; /*0x724b84*/
            v52 = 0; /*0x724b88*/
            while ( v51 < 16 ) /*0x724c38*/
            {
              v53 = *(_BYTE *)(v156._r0 + v51) /*0x724c28*/
                  ^ *(_BYTE *)(v162._r0 + ((v162._r1 - 16LL) & (-(v162._r2 - v162._r1 + 16LL) >> 63)) + v51);
              ++v51; /*0x724c2c*/
              v52 |= v53; /*0x724c30*/
            }
            if ( ((((unsigned __int64)v52 - 1) >> 31) & 1) == 1 ) /*0x724c50*/
            {
              v145 = runtime_slicebytetostring(0, v126, v95); /*0x724c60*/
              v46 = v145._r1; /*0x724c6c*/
              v40 = v109; /*0x724c70*/
LABEL_79:
              v91 = v46; /*0x724c74*/
              v118 = v145._r0; /*0x724c78*/
              v54 = qword_B73550; /*0x724c80*/
              v55 = qword_B73548; /*0x724c88*/
              v56 = qword_B73548 + 9; /*0x724c8c*/
              v57 = off_B73540; /*0x724c94*/
              if ( qword_B73550 < (unsigned __int64)(qword_B73548 + 9) ) /*0x724c9c*/
              {
                v102 = qword_B73548; /*0x724ca0*/
                v157 = runtime_growslice(off_B73540, qword_B73548 + 9, qword_B73550, 9, &RTYPE_uint8); /*0x724cbc*/
                v55 = v102; /*0x724cc0*/
                v57 = (uint8 *)v157._r0; /*0x724cc4*/
                v54 = v157._r2; /*0x724cc8*/
                v56 = v157._r1; /*0x724ccc*/
                v40 = v109; /*0x724cd0*/
              }
              v83 = v54; /*0x724cd4*/
              v80 = v56; /*0x724cd8*/
              v112 = v57; /*0x724cdc*/
              runtime_memmove(&v57[v55], v40, 9); /*0x724ce8*/
              v158 = golang_org_x_crypto_pbkdf2_Key(a3, a4, a5, v112, v80, v83, 10000, 32, off_890CC8); /*0x724d18*/
              v164 = crypto_aes_NewCipher(v158._r0); /*0x724d28*/
              if ( v164._r2 ) /*0x724d2c*/
              {
                result._r0 = 0; /*0x724e9c*/
                result._r1 = v164._r2; /*0x724ea0*/
                result._r2 = v164._r3; /*0x724ea4*/
              }
              else
              {
                v148 = crypto_cipher_NewCTR(v164._r0, v164._r1, v122, 16, v78); /*0x724d3c*/
                v93 = v148._r0; /*0x724d40*/
                v119 = (io_LimitedReader *)v148._r1; /*0x724d44*/
                v149 = crypto_hmac_New(off_890CC8, v158._r0, v158._r1, v158._r2); /*0x724d5c*/
                v71 = v149._r0; /*0x724d60*/
                v106 = (io_LimitedReader *)v149._r1; /*0x724d64*/
                v104 = bswap64(v103); /*0x724d80*/
                v94 = v104 - (qword_B85CE8 + v79); /*0x724d90*/
                p_io_LimitedReader = (io_LimitedReader *)runtime_newobject(&RTYPE_io_LimitedReader); /*0x724d9c*/
                p_io_LimitedReader->R.tab = a1; /*0x724da4*/
                if ( dword_BE34B0 ) /*0x724db0*/
                {
                  p_io_LimitedReader = (io_LimitedReader *)runtime_gcWriteBarrier1(); /*0x724dbc*/
                  v59 = a2; /*0x724dc0*/
                  *v60 = a2; /*0x724dc4*/
                }
                else
                {
                  v59 = a2; /*0x724db4*/
                }
                v120 = p_io_LimitedReader; /*0x724dc8*/
                p_io_LimitedReader->R.data = v59; /*0x724dcc*/
                p_io_LimitedReader->N = v104 - v79; /*0x724dd4*/
                result._r0 = runtime_newobject(&RTYPE_git_teiron_inc_cn_services_backup_cloud_crypto_DecryptSliceReader); /*0x724de0*/
                *(_QWORD *)result._r0 = off_908260; /*0x724dec*/
                if ( dword_BE34B0 ) /*0x724df8*/
                {
                  result._r0 = runtime_gcWriteBarrier3(); /*0x724e0c*/
                  v61 = v120; /*0x724e10*/
                  *v64 = v120; /*0x724e14*/
                  v62 = v119; /*0x724e18*/
                  v64[1] = v119; /*0x724e1c*/
                  v63 = v106; /*0x724e20*/
                  v64[2] = v106; /*0x724e24*/
                }
                else
                {
                  v61 = v120; /*0x724dfc*/
                  v62 = v119; /*0x724e00*/
                  v63 = v106; /*0x724e04*/
                }
                *(_QWORD *)(result._r0 + 8LL) = v61; /*0x724e28*/
                *(_QWORD *)(result._r0 + 16LL) = v93; /*0x724e30*/
                *(_QWORD *)(result._r0 + 24LL) = v62; /*0x724e34*/
                *(_QWORD *)(result._r0 + 32LL) = v71; /*0x724e3c*/
                *(_QWORD *)(result._r0 + 40LL) = v63; /*0x724e40*/
                v65 = v94; /*0x724e44*/
                *(_QWORD *)(result._r0 + 48LL) = v94; /*0x724e48*/
                *(_BYTE *)(result._r0 + 96LL) = 0; /*0x724e4c*/
                *(_QWORD *)(result._r0 + 112LL) = v91; /*0x724e54*/
                if ( dword_BE34B0 ) /*0x724e60*/
                {
                  result._r0 = runtime_gcWriteBarrier1(); /*0x724e6c*/
                  v66 = v118; /*0x724e70*/
                  *v67 = v118; /*0x724e74*/
                }
                else
                {
                  v66 = v118; /*0x724e64*/
                }
                *(_QWORD *)(result._r0 + 104LL) = v66; /*0x724e78*/
                *(_QWORD *)(result._r0 + 120LL) = v104; /*0x724e80*/
                *(_QWORD *)(result._r0 + 128LL) = v65; /*0x724e84*/
                result._r1 = 0; /*0x724e88*/
                result._r2 = 0; /*0x724e8c*/
              }
            }
            else
            {
              result._r1 = off_B71520; /*0x724eb8*/
              result._r2 = off_B71528; /*0x724ec0*/
              result._r0 = 0; /*0x724ec4*/
            }
          }
        }
      }
      else
      {
        result._r1 = off_B71500; /*0x724bac*/
        result._r2 = off_B71508; /*0x724bb4*/
        result._r0 = 0; /*0x724bb8*/
      }
    }
  }
  return result; /*0x724450*/
}
```

## 3. 外层 `Read`：EOF 后才可能衔接下一个 FOT 对象

`0x724fa0` 先转发到当前子 Reader。当前对象真正返回 EOF 后，它才会在 `0x725050` 再次调用头解析器。这不是首个对象的解析点，而是连续对象的续接逻辑。

```c
// git.teiron-inc.cn/services/backup-cloud/crypto.(*DecryptReader).Read
retval_7ABA20 __golang git_teiron_inc_cn_services_backup_cloud_crypto__ptr_DecryptReader_Read(
        _ptr_git_teiron_inc_cn_services_backup_cloud_crypto_DecryptReader a1,
        _slice_uint8 a2)
{
  char v2; // w3
  char v3; // w0
  _ptr_git_teiron_inc_cn_services_backup_cloud_crypto_DecryptReader r4; // x4
  _QWORD *v5; // x25
  git_teiron_inc_cn_services_backup_cloud_crypto_DecryptSliceReader *r0; // x0
  retval_7ABA20 result; // 0:x0.24
  retval_7ABA20 v13; // 0:kr00_24.24
  retval_724220 v14; // 0:kr28_24.24
  retval_7ABA20 v15; // 0:kr40_24.24
  retval_475E60 v16; // 0:kr78_40.40

  v13 = fot_decrypt_reader_read_and_verify_tag(a1->reader, a2); /*0x724fd0*/
  result._r0 = v13._r0; /*0xf1c0000000000004*/
  result._r1.tab = v13._r1.tab; /*0xf1c0000000000008*/
  result._r1.data = v13._r1.data; /*0xf1c000000000000c*/
  if ( v13._r0 <= 0 ) /*0x724fd8*/
  {
    if ( v13._r1.tab ) /*0x724fe4*/
    {
      if ( v13._r1.tab == off_B70F50 ) /*0x724ff4*/
      {
        v3 = runtime_ifaceeq(v13._r1.tab, v13._r1.data, off_B70F58); /*0x725018*/
        result._r1.tab = v13._r1.tab; /*0x72501c*/
        result._r1.data = v13._r1.data; /*0x725020*/
        v2 = v3; /*0x725024*/
        result._r0 = v13._r0; /*0x725028*/
      }
      else
      {
        v2 = 0; /*0x724ff8*/
      }
    }
    else
    {
      v2 = 0; /*0x725030*/
    }
    if ( (v2 & 1) != 0 ) /*0x725034*/
    {
      v14 = fot_parse_header_and_create_decrypt_reader( /*0x725050*/
              a1->inReader.tab,
              a1->inReader.data,
              (__int64)a1->key.array,
              a1->key.len,
              a1->key.cap);
      r0 = (git_teiron_inc_cn_services_backup_cloud_crypto_DecryptSliceReader *)v14._r0; /*0xf1c0000000000010*/
      if ( v14._r1 ) /*0x725054*/
      {
        result._r0 = 0; /*0x7250b0*/
        result._r1.tab = v13._r1.tab; /*0x7250b4*/
        result._r1.data = v13._r1.data; /*0x7250b8*/
      }
      else
      {
        r4 = a1; /*0x725058*/
        a1->CipherLenght += *(_QWORD *)(v14._r0 + 120LL); /*0x725068*/
        a1->PlainLenght += *(_QWORD *)(v14._r0 + 128LL); /*0x725078*/
        if ( dword_BE34B0 ) /*0x725084*/
        {
          v16 = runtime_gcWriteBarrier2(); /*0x725088*/
          r0 = (git_teiron_inc_cn_services_backup_cloud_crypto_DecryptSliceReader *)v16._r0; /*0xf1c0000000000028*/
          r4 = (_ptr_git_teiron_inc_cn_services_backup_cloud_crypto_DecryptReader)v16._r4; /*0xf1c0000000000038*/
          *v5 = v16._r0; /*0x72508c*/
          v5[1] = *(_QWORD *)v16._r4; /*0x725094*/
        }
        r4->reader = r0; /*0x725098*/
        v15 = fot_decrypt_reader_read_and_verify_tag(r0, a2); /*0x7250a8*/
        result._r0 = v15._r0; /*0xf1c000000000001c*/
        result._r1.tab = v15._r1.tab; /*0xf1c0000000000020*/
        result._r1.data = v15._r1.data; /*0xf1c0000000000024*/
      }
    }
  }
  return result; /*0x7250c4*/
}
```

## 4. 正文 `Read`：密文块、CTR、HMAC 和最后 tag

`0x725100` 是正文真正变成明文的位置。它按块从底层 reader 读 ciphertext，维护 HMAC，执行 CTR，把 plaintext 放入 buffer；正文结束后才读最后 16 bytes tag。

```c
// git.teiron-inc.cn/services/backup-cloud/crypto.(*DecryptSliceReader).Read
fot_read_result_t fot_decrypt_reader_read_and_verify_tag(void *decrypt_reader, go_byte_slice_t output)
{
  __int64 v2; // x4
  void *v3; // x4
  unsigned __int8 *v4; // x4
  __int64 v8; // x1
  unsigned __int8 v9; // w2
  __int64 v10; // x2
  __int64 v11; // x0
  char v12; // w3
  _ptr_git_teiron_inc_cn_services_backup_cloud_crypto_DecryptSliceReader v13; // x3
  signed __int64 v14; // x1
  signed __int64 off; // x0
  __int64 v16; // x2
  RTYPE **v17; // x3
  void *v18; // x4
  __int64 v19; // x4
  __int64 v20; // x1
  char v21; // w4
  unsigned __int64 v22; // [xsp+50h] [xbp-48h]
  __int64 v23; // [xsp+58h] [xbp-40h]
  __int64 v24; // [xsp+60h] [xbp-38h]
  uint8 *plaintext_chunk; // [xsp+80h] [xbp-18h]
  __int64 stored_hmac_tag; // [xsp+88h] [xbp-10h]
  __int64 ciphertext_chunk; // [xsp+88h] [xbp-10h]
  _ptr_git_teiron_inc_cn_services_backup_cloud_crypto_DecryptSliceReader v28; // [xsp+A0h] [xbp+8h]
  unsigned __int8 *data; // [xsp+A8h] [xbp+10h]
  __int64 len; // [xsp+B0h] [xbp+18h]
  fot_read_result_t result; // 0:x0.24
  _slice_uint8 v32; // 0:x1.24
  retval_725314 v33; // 0:kr28_24.24
  retval_72528C computed_hmac_tag; // 0:kr40_24.24

  v28 = (_ptr_git_teiron_inc_cn_services_backup_cloud_crypto_DecryptSliceReader)decrypt_reader; /*0x725120*/
  if ( (__int64)(*((_QWORD *)decrypt_reader + 8) - *((_QWORD *)decrypt_reader + 10)) <= 0 )
  {
    *((_QWORD *)decrypt_reader + 8) = 0; /*0x725210*/
    *((_QWORD *)decrypt_reader + 10) = 0; /*0x725214*/
    *((_BYTE *)decrypt_reader + 88) = 0; /*0x725218*/
    output.cap = *((_QWORD *)decrypt_reader + 6); /*0x72521c*/
    if ( output.cap <= 0 ) /*0x725224*/
    {
      if ( (*((_BYTE *)decrypt_reader + 96) & 1) == 0 ) /*0x72522c*/
      {
        stored_hmac_tag = runtime_makeslice(&RTYPE_uint8); /*0x725244*/
        if ( io_ReadAtLeast(v28->reader.tab, v28->reader.data, stored_hmac_tag)._r1 )// 正文结束后必须读取 16 字节 HMAC 截断标签；缺失表示文件不完整。 /*0x725268*/
        {
          result.err_tab = off_B71530; /*0x7252ac*/
          result.err_data = off_B71538; /*0x7252b4*/
          result.count = 0; /*0x7252b8*/
          return result; /*0x7252c4*/
        }
        computed_hmac_tag = ((retval_72528C (__golang *)(void *, _QWORD, _QWORD, _QWORD))*((_QWORD *)v28->tagHasher.tab /*0x72528c*/
                                                                                         + 6))(
                              v28->tagHasher.data,
                              0,
                              0,
                              0);
        if ( computed_hmac_tag._r2 < 0x10u ) /*0x725294*/
          runtime_panicSliceAcap(computed_hmac_tag._r0, 16); /*0x725590*/
        v8 = 0; /*0x72529c*/
        v9 = 0; /*0x7252a0*/
        while ( v8 < 16 ) /*0x725524*/
        {
          v21 = *(_BYTE *)(stored_hmac_tag + v8) ^ *(_BYTE *)(computed_hmac_tag._r0 + v8);// 逐字节 XOR 聚合比较计算的 HMAC 前 16 字节与文件尾标签；不匹配通常表示口令错误或文件损坏。 /*0x725514*/
          ++v8; /*0x725518*/
          v9 |= v21; /*0x72551c*/
        }
        if ( ((((unsigned __int64)v9 - 1) >> 31) & 1) != 1 ) /*0x72553c*/
        {
          result.err_tab = off_B71540; /*0x725570*/
          result.err_data = off_B71548; /*0x725578*/
          result.count = 0; /*0x72557c*/
          return result; /*0x725588*/
        }
        v28->verified = 1; /*0x725548*/
      }
      result.err_tab = off_B70F50; /*0x725550*/
      result.err_data = off_B70F58; /*0x725558*/
      result.count = 0; /*0x72555c*/
      return result; /*0x725568*/
    }
    len = output.len; /*0x7252c8*/
    data = output.data; /*0x7252cc*/
    if ( output.cap >= 4096 ) /*0x7252d8*/
      v10 = 4096; /*0x7252d8*/
    else
      v10 = *((_QWORD *)decrypt_reader + 6); /*0x7252d8*/
    v22 = v10; /*0x7252dc*/
    ciphertext_chunk = runtime_makeslice(&RTYPE_uint8); /*0x7252f0*/
    v33 = ((retval_725314 (__golang *)(void *, __int64, unsigned __int64, unsigned __int64))*((_QWORD *)v28->reader.tab /*0x725314*/
                                                                                            + 3))(
            v28->reader.data,
            ciphertext_chunk,
            v22,
            v22);
    result.err_tab = (void *)v33._r1; /*0xf1c0000000000014*/
    result.err_data = (void *)v33._r2; /*0xf1c0000000000018*/
    if ( v33._r0 > 0 ) /*0x725324*/
    {
      v28->remain -= v33._r0; /*0x725338*/
      v11 = runtime_makeslice(&RTYPE_uint8); /*0x72534c*/
      if ( v33._r0 > v22 ) /*0x72535c*/
        runtime_panicSliceAcap(v11, v33._r0); /*0x725508*/
      plaintext_chunk = (uint8 *)v11; /*0x725360*/
      (*((void (__golang **)(void *, __int64, _QWORD))v28->decryptor.tab + 3))(v28->decryptor.data, v11, v33._r0); /*0x725390*/
      (*((void (__golang **)(void *, __int64, _QWORD, unsigned __int64))v28->tagHasher.tab + 7))( /*0x7253b0*/
        v28->tagHasher.data,
        ciphertext_chunk,
        v33._r0,
        v22);
      v32.array = plaintext_chunk; /*0x7253bc*/
      v32.len = v33._r0; /*0x7253c0*/
      v32.cap = v33._r0; /*0x7253c4*/
      bytes__ptr_Buffer_Write(&v28->buffer, v32); /*0x7253c8*/
      result.err_tab = (void *)v33._r1; /*0x7253cc*/
      result.err_data = (void *)v33._r2; /*0x7253d0*/
    }
    if ( result.err_tab
      && (result.err_tab == off_B70F50
        ? (v12 = runtime_ifaceeq(result.err_tab, result.err_data, off_B70F58) ^ 1,
           result.err_tab = (void *)v33._r1,
           result.err_data = (void *)v33._r2)
        : (void *)(v12 = 1),
          (v12 & 1) != 0) )
    {
      result.count = 0; /*0x725418*/
    }
    else
    {
      v13 = v28; /*0x725428*/
      v28->buffer.lastRead = 0; /*0x72542c*/
      v14 = v28->buffer.buf.len; /*0x725430*/
      off = v28->buffer.off; /*0x725434*/
      if ( v14 > off ) /*0x72543c*/
      {
        if ( v14 < (unsigned __int64)off ) /*0x725480*/
          runtime_panicSliceB(off); /*0x725504*/
        v19 = v14 - off; /*0x725484*/
        v20 = (__int64)&v28->buffer.buf.array[off & ((off - v28->buffer.buf.cap) >> 63)]; /*0x72549c*/
        if ( len <= v19 ) /*0x7254a8*/
          v16 = len; /*0x7254a8*/
        else
          v16 = v19; /*0x7254a8*/
        if ( data != (unsigned __int8 *)v20 ) /*0x7254b4*/
        {
          v23 = v16; /*0x7254b8*/
          runtime_memmove(data, v20, v16); /*0x7254bc*/
          v16 = v23; /*0x7254c0*/
          v13 = v28; /*0x7254c4*/
        }
        v13->buffer.off += v16; /*0x7254d0*/
        if ( v16 > 0 ) /*0x7254d8*/
          v13->buffer.lastRead = -1; /*0x7254e0*/
        v17 = nullptr; /*0x7254e4*/
        v18 = nullptr; /*0x7254e8*/
      }
      else
      {
        v28->buffer.buf.len = 0; /*0x725444*/
        v28->buffer.off = 0; /*0x725448*/
        v28->buffer.lastRead = 0; /*0x72544c*/
        if ( len ) /*0x725454*/
        {
          v17 = off_B70F50; /*0x72546c*/
          v18 = off_B70F58; /*0x725474*/
          v16 = 0; /*0x725478*/
        }
        else
        {
          v16 = 0; /*0x725458*/
          v17 = nullptr; /*0x72545c*/
          v18 = nullptr; /*0x725460*/
        }
      }
      result.count = v16; /*0x7254ec*/
      result.err_tab = v17; /*0x7254f0*/
      result.err_data = v18; /*0x7254f4*/
    }
  }
  else
  {
    *((_BYTE *)decrypt_reader + 88) = 0; /*0x725138*/
    output.cap = *((_QWORD *)decrypt_reader + 8); /*0x72513c*/
    v2 = *((_QWORD *)decrypt_reader + 10); /*0x725140*/
    if ( output.cap > v2 ) /*0x725148*/
    {
      if ( output.cap < (unsigned __int64)v2 ) /*0x725188*/
        runtime_panicSliceB(*((_QWORD *)decrypt_reader + 10)); /*0x72559c*/
      output.cap -= v2; /*0x72518c*/
      v4 = (unsigned __int8 *)(*((_QWORD *)decrypt_reader + 7) + (v2 & ((v2 - *((_QWORD *)decrypt_reader + 9)) >> 63))); /*0x7251a4*/
      if ( output.len > output.cap ) /*0x7251ac*/
        output.len = output.cap; /*0x7251ac*/
      if ( output.data != v4 ) /*0x7251b4*/
      {
        v24 = output.len; /*0x7251b8*/
        runtime_memmove(output.data, v4, output.len); /*0x7251c4*/
        decrypt_reader = v28; /*0x7251c8*/
        output.len = v24; /*0x7251cc*/
      }
      *((_QWORD *)decrypt_reader + 10) += output.len; /*0x7251d8*/
      if ( output.len > 0 ) /*0x7251e0*/
        *((_BYTE *)decrypt_reader + 88) = -1; /*0x7251e8*/
      output.cap = 0; /*0x7251ec*/
      v3 = nullptr; /*0x7251f0*/
    }
    else
    {
      *((_QWORD *)decrypt_reader + 8) = 0; /*0x725150*/
      *((_QWORD *)decrypt_reader + 10) = 0; /*0x725154*/
      *((_BYTE *)decrypt_reader + 88) = 0; /*0x725158*/
      if ( output.len ) /*0x72515c*/
      {
        output.cap = (__int64)off_B70F50; /*0x725174*/
        v3 = off_B70F58; /*0x72517c*/
        output.len = 0; /*0x725180*/
      }
      else
      {
        output.len = 0; /*0x725160*/
        output.cap = 0; /*0x725164*/
        v3 = nullptr; /*0x725168*/
      }
    }
    result.count = output.len; /*0x7251f4*/
    result.err_tab = (void *)output.cap; /*0x7251f8*/
    result.err_data = v3; /*0x7251fc*/
  }
  return result; /*0x725208*/
}
```

## 5. 下载器：HTTP body 从哪里进入解密 Reader，又从哪里落盘

`0x74e690` 是网络下载、普通文件下载和 FOT 恢复逻辑汇合的地方。重点看 `.fot` 判断、`Response.Body`、`NewDecryptReader`、`filepath.Join`、`OpenFile` 和 `CopyBuffer`。

```c
// git.teiron-inc.cn/services/backup-cloud/worker/download.Download
retval_74E690 __golang git_teiron_inc_cn_services_backup_cloud_worker_download_Download(
        __int64 a1,
        __int64 a2,
        __int64 a3,
        __int64 a4,
        _QWORD *a5,
        uint8 *a6,
        __int64 a7,
        __int64 a8,
        __int64 a9,
        __int64 a10,
        __int64 a11)
{
  __int64 v11; // x11
  char v12; // w11
  char v13; // w0
  __int64 v14; // x11
  os_File *v15; // x12
  os_File *v16; // x0
  __int64 v17; // x1
  int64 v18; // x2
  void *v19; // x2
  __int64 v20; // x0
  int64 v21; // x1
  __int64 v22; // x0
  _1_string *p__1_string; // x0
  uint8 *v24; // x4
  uint8 **v25; // x25
  string **v26; // x0
  string *v27; // x3
  string **v28; // x25
  __int64 v29; // x0
  __int64 v33; // x0
  __int64 v34; // x0
  __int64 v35; // x7
  __int64 v36; // x8
  __int64 v37; // x9
  _1_string *v38; // x0
  uint8 *v39; // x4
  uint8 **v40; // x25
  string **v41; // x0
  string *v42; // x2
  string **v43; // x25
  __int64 v44; // x0
  __int64 v45; // x0
  crypto_tls_Config *p_crypto_tls_Config; // x0
  net_http_Transport *v47; // x1
  crypto_tls_Config **v48; // x25
  net_http_Client *p_net_http_Client; // x0
  net_http_Transport *v50; // x8
  net_http_Transport **v51; // x25
  _ptr_net_http_Request v52; // x3
  uint8 **v53; // x5
  __int64 v54; // x0
  __int64 v55; // x4
  __int64 v56; // x1
  __int64 v57; // x0
  net_http_Response *i; // x1
  __int64 v59; // x0
  __int64 v60; // x0
  __int64 v61; // x0
  __int64 v62; // x0
  signed __int64 StatusCode; // x2
  void *v64; // x1
  _QWORD *v65; // x2
  __int64 v66; // x3
  _QWORD *v67; // x5
  __int64 v68; // x2
  void *tab; // x1
  void *v70; // x2
  __int64 v71; // x0
  __int64 v72; // x0
  __int64 v73; // x0
  __int64 v74; // x5
  char v75; // w1
  __int64 v76; // x0
  __int64 v77; // x1
  __int64 v78; // x2
  __int64 v79; // x4
  __int64 v80; // x3
  _QWORD *v81; // x7
  __int64 v82; // x8
  __int64 v83; // x3
  __int64 v84; // x5
  __int64 v85; // x1
  _QWORD *v86; // x7
  __int64 v87; // x5
  __int64 v88; // x9
  __int64 v89; // x10
  char *v90; // x9
  __int64 v91; // x0
  __int64 v92; // x0
  __int64 v93; // x3
  _QWORD *v94; // x2
  _QWORD *v95; // x25
  __int64 v96; // x2
  __int64 v97; // x0
  __int64 v98; // x0
  __int64 v99; // x0
  __int64 v100; // x0
  __int64 v101; // x0
  __int64 v102; // x0
  __int64 v103; // x10
  __int64 v104; // x11
  char *v105; // x10
  __int64 v106; // x0
  __int64 v107; // x0
  __int64 v108; // x0
  __int64 v109; // x0
  __int64 v110; // x0
  __int64 v111; // x7
  __int64 v112; // x8
  char *v113; // x7
  __int64 v114; // x0
  __int64 v115; // x0
  unsigned __int64 v116; // x2
  unsigned __int64 v117; // x1
  string *v118; // x3
  string **v119; // x5
  _QWORD *v120; // x25
  __int64 v121; // x2
  uint8 *v122; // x4
  uint8 **v123; // x25
  uint8 **v124; // x4
  __int64 v125; // x0
  __int64 v126; // x3
  __int64 v127; // x1
  __int64 v128; // x0
  unsigned __int64 v129; // x2
  unsigned __int64 v130; // x1
  string *r3; // x3
  string **v132; // x5
  _QWORD *v133; // x25
  __int64 v134; // x2
  uint8 *v135; // x4
  uint8 **v136; // x25
  _ptr_net_http_Response v137; // x0
  __int64 v138; // x0
  __int64 v139; // x1
  __int64 v140; // x2
  string *v141; // x0
  string *v142; // x0
  unsigned int v143; // [xsp+6Ch] [xbp-5BCh]
  unsigned int v144; // [xsp+6Ch] [xbp-5BCh]
  __int64 v145; // [xsp+70h] [xbp-5B8h]
  __int64 v146; // [xsp+78h] [xbp-5B0h]
  __int64 v147; // [xsp+80h] [xbp-5A8h]
  __int64 v148; // [xsp+88h] [xbp-5A0h]
  __int64 v149; // [xsp+90h] [xbp-598h]
  int64 v150; // [xsp+98h] [xbp-590h]
  __int64 v151; // [xsp+98h] [xbp-590h]
  __int64 v152; // [xsp+98h] [xbp-590h]
  __int64 v153; // [xsp+A0h] [xbp-588h]
  __int64 v154; // [xsp+A8h] [xbp-580h]
  __int64 v155; // [xsp+B0h] [xbp-578h]
  __int64 r1; // [xsp+C0h] [xbp-568h]
  __int64 v157; // [xsp+D0h] [xbp-558h]
  __int64 v158; // [xsp+D8h] [xbp-550h]
  __int64 v159; // [xsp+D8h] [xbp-550h]
  __int64 v160; // [xsp+D8h] [xbp-550h]
  char v161[24]; // [xsp+E0h] [xbp-548h] BYREF
  _QWORD *v162; // [xsp+F8h] [xbp-530h]
  char v163[24]; // [xsp+110h] [xbp-518h] BYREF
  _QWORD *v164; // [xsp+128h] [xbp-500h]
  char v165[24]; // [xsp+140h] [xbp-4E8h] BYREF
  _QWORD *v166; // [xsp+158h] [xbp-4D0h]
  retval_4D6BE0 v167; // [xsp+170h] [xbp-4B8h]
  uint8 *v168; // [xsp+180h] [xbp-4A8h]
  uint8 *v169; // [xsp+188h] [xbp-4A0h]
  uint8 *v170; // [xsp+190h] [xbp-498h]
  __int64 v171; // [xsp+198h] [xbp-490h]
  net_http_Transport *p_net_http_Transport; // [xsp+1A0h] [xbp-488h]
  _ptr_net_http_Response v173; // [xsp+1A8h] [xbp-480h]
  _ptr_net_http_Request v174; // [xsp+1B0h] [xbp-478h]
  __int64 v175; // [xsp+1B8h] [xbp-470h]
  __int64 v176; // [xsp+1C0h] [xbp-468h]
  net_http_Header Header; // [xsp+1C8h] [xbp-460h]
  __int64 v178; // [xsp+1D0h] [xbp-458h]
  _ptr_os_File r0; // [xsp+1D8h] [xbp-450h]
  __int64 v180; // [xsp+1E0h] [xbp-448h]
  void *data; // [xsp+1E8h] [xbp-440h]
  void *r2; // [xsp+1F0h] [xbp-438h]
  _QWORD *v183; // [xsp+1F8h] [xbp-430h]
  _ptr_net_http_Client v184; // [xsp+200h] [xbp-428h]
  __int64 v185; // [xsp+208h] [xbp-420h]
  string **v186; // [xsp+210h] [xbp-418h]
  uint8 **v187; // [xsp+218h] [xbp-410h]
  __int64 v188; // [xsp+220h] [xbp-408h]
  string *v189; // [xsp+228h] [xbp-400h]
  void *v190; // [xsp+230h] [xbp-3F8h]
  RTYPE *v191; // [xsp+238h] [xbp-3F0h] BYREF
  __int64 v192; // [xsp+240h] [xbp-3E8h]
  RTYPE *v193; // [xsp+248h] [xbp-3E0h]
  __int64 v194; // [xsp+250h] [xbp-3D8h]
  RTYPE *v195; // [xsp+258h] [xbp-3D0h]
  __int64 v196; // [xsp+260h] [xbp-3C8h]
  _QWORD v197[2]; // [xsp+268h] [xbp-3C0h] BYREF
  RTYPE *v198; // [xsp+278h] [xbp-3B0h] BYREF
  __int64 v199; // [xsp+280h] [xbp-3A8h]
  __int64 v200; // [xsp+288h] [xbp-3A0h]
  __int64 v201; // [xsp+290h] [xbp-398h]
  retval_4C8E50 v202; // [xsp+298h] [xbp-390h] BYREF
  __int64 v203; // [xsp+2A8h] [xbp-380h]
  __int64 v204; // [xsp+2B0h] [xbp-378h]
  RTYPE *v205; // [xsp+2B8h] [xbp-370h] BYREF
  __int64 v206; // [xsp+2C0h] [xbp-368h]
  __int64 v207; // [xsp+2C8h] [xbp-360h]
  __int64 v208; // [xsp+2D0h] [xbp-358h]
  RTYPE *v209; // [xsp+2D8h] [xbp-350h] BYREF
  __int64 v210; // [xsp+2E0h] [xbp-348h]
  _QWORD v211[3]; // [xsp+2E8h] [xbp-340h] BYREF
  RTYPE *v212; // [xsp+300h] [xbp-328h] BYREF
  __int64 v213; // [xsp+308h] [xbp-320h]
  RTYPE *v214; // [xsp+310h] [xbp-318h]
  __int64 v215; // [xsp+318h] [xbp-310h]
  RTYPE *v216; // [xsp+320h] [xbp-308h]
  __int64 v217; // [xsp+328h] [xbp-300h]
  __int64 v218; // [xsp+330h] [xbp-2F8h]
  void *v219; // [xsp+338h] [xbp-2F0h]
  RTYPE *v220; // [xsp+340h] [xbp-2E8h] BYREF
  __int64 v221; // [xsp+348h] [xbp-2E0h]
  RTYPE *v222; // [xsp+350h] [xbp-2D8h]
  __int64 v223; // [xsp+358h] [xbp-2D0h]
  RTYPE *v224; // [xsp+360h] [xbp-2C8h]
  __int64 v225; // [xsp+368h] [xbp-2C0h]
  RTYPE *v226; // [xsp+370h] [xbp-2B8h] BYREF
  __int64 v227; // [xsp+378h] [xbp-2B0h]
  RTYPE *v228; // [xsp+380h] [xbp-2A8h]
  __int64 v229; // [xsp+388h] [xbp-2A0h]
  char v230; // [xsp+390h] [xbp-298h] BYREF
  __int64 v231; // [xsp+4E0h] [xbp-148h] BYREF
  __int64 v232; // [xsp+4E8h] [xbp-140h]
  char *v233; // [xsp+4F0h] [xbp-138h]
  __int64 v234; // [xsp+4F8h] [xbp-130h]
  __int64 v235; // [xsp+500h] [xbp-128h]
  __int64 v236; // [xsp+508h] [xbp-120h]
  _QWORD v237[2]; // [xsp+510h] [xbp-118h] BYREF
  RTYPE *v238; // [xsp+520h] [xbp-108h] BYREF
  __int64 v239; // [xsp+528h] [xbp-100h]
  __int64 v240; // [xsp+530h] [xbp-F8h]
  __int64 v241; // [xsp+538h] [xbp-F0h]
  RTYPE *v242; // [xsp+540h] [xbp-E8h] BYREF
  __int64 v243; // [xsp+548h] [xbp-E0h]
  __int64 v244; // [xsp+550h] [xbp-D8h]
  void *v245; // [xsp+558h] [xbp-D0h]
  _QWORD v246[2]; // [xsp+560h] [xbp-C8h] BYREF
  _QWORD v247[2]; // [xsp+570h] [xbp-B8h] BYREF
  __int64 v248; // [xsp+580h] [xbp-A8h]
  void *v249; // [xsp+588h] [xbp-A0h]
  RTYPE *v250; // [xsp+590h] [xbp-98h] BYREF
  __int64 v251; // [xsp+598h] [xbp-90h]
  __int64 v252; // [xsp+5A0h] [xbp-88h]
  void *v253; // [xsp+5A8h] [xbp-80h]
  RTYPE *v254; // [xsp+5B0h] [xbp-78h] BYREF
  __int64 v255; // [xsp+5B8h] [xbp-70h]
  __int64 *v256; // [xsp+5C0h] [xbp-68h] BYREF
  __int64 v257; // [xsp+5C8h] [xbp-60h]
  __int64 v258; // [xsp+630h] [xbp+8h]
  __int64 v259; // [xsp+638h] [xbp+10h]
  __int64 v260; // [xsp+640h] [xbp+18h]
  __int64 v261; // [xsp+648h] [xbp+20h]
  _QWORD *v262; // [xsp+650h] [xbp+28h]
  __int64 v265; // [xsp+668h] [xbp+40h]
  __int64 v266; // [xsp+670h] [xbp+48h]
  __int64 v267; // [xsp+678h] [xbp+50h]
  retval_4D0230 v269; // 0:x0.16
  retval_4542D0 v270; // 0:x0.16
  retval_485B90 v271; // 0:x0.16
  retval_4542D0 v272; // 0:x0.16
  retval_4C8E50 v273; // 0:x0.16
  retval_5A2DD0 v274; // 0:x0.16
  retval_4D0230 v275; // 0:x0.16
  retval_470A10 v276; // 0:x0.16
  io_fs_FileInfo v277; // 0:kr00_16.16
  retval_74E690 result; // 0:x0.24
  retval_7B73C0 v279; // 0:kr20_24.24
  retval_7ACE60 v280; // 0:kr48_24.24
  retval_74E2A0 v281; // 0:kr60_24.24
  retval_7240E0 v282; // 0:kr88_24.24
  retval_74E2A0 v283; // 0:krA0_24.24
  retval_470100 v284; // 0:krE0_24.24
  retval_470100 v285; // 0:kr108_24.24
  retval_74E5F0 v286; // 0:kr120_24.24
  retval_639CC0 v287; // 0:kr148_24.24
  retval_74E420 v288; // 0:kr160_24.24
  retval_74E420 v289; // 0:kr1A0_24.24
  retval_4A96E0 All; // 0:krC0_32.32
  retval_475E60 v291; // 0:kr178_40.40
  retval_475E60 v292; // 0:kr1B8_40.40
  retval_475E60 v293; // 0:kr1E0_40.40
  retval_475E60 v294; // 0:kr228_40.40
  retval_475E60 v295; // 0:kr270_40.40

  v262 = a5; /*0x74e6b0*/
  v265 = a8; /*0x74e6b4*/
  v267 = a10; /*0x74e6bc*/
  v266 = a9; /*0x74e6c0*/
  v261 = a4; /*0x74e6cc*/
  v260 = a3; /*0x74e6d0*/
  v259 = a2; /*0x74e6d4*/
  v258 = a1; /*0x74e6d8*/
  v167._r0 = 0; /*0x74e6e0*/
  v167._r1 = 0; /*0x74e6e0*/
  v11 = a5[1]; /*0x74e6e4*/
  if ( v11 >= 4 ) /*0x74e6ec*/
  {
    v13 = runtime_memequal(*a5 + v11 - 4, ".fot"); /*0x74e710*/
    a2 = v259; /*0x74e714*/
    a3 = v260; /*0x74e718*/
    a4 = v261; /*0x74e71c*/
    a5 = v262; /*0x74e720*/
    a8 = v265; /*0x74e72c*/
    a9 = v266; /*0x74e730*/
    a10 = v267; /*0x74e734*/
    v12 = v13; /*0x74e73c*/
    a1 = v258; /*0x74e740*/
  }
  else
  {
    v12 = 0; /*0x74e6f0*/
  }
  if ( (v12 & 1) != 0 && a10 ) /*0x74e748*/
  {
    v14 = 0; /*0x74e74c*/
    v15 = nullptr; /*0x74e750*/
  }
  else
  {
    if ( syscall_Faccessat(-100, *a5, a5[1], 0, 0) ) /*0x74e76c*/
    {
      v16 = nullptr; /*0x74e774*/
      v17 = 0; /*0x74e778*/
      v18 = 0; /*0x74e77c*/
    }
    else
    {
      v269 = os_OpenFile(*v262, v262[1], 2, 438); /*0x74e798*/
      r1 = v269._r1; /*0x74e79c*/
      if ( v269._r1 ) /*0x74e7a0*/
      {
        r2 = v19; /*0x74e7a4*/
        v242 = nullptr; /*0x74e7ac*/
        v243 = 0; /*0x74e7ac*/
        v244 = 0; /*0x74e7b4*/
        v245 = nullptr; /*0x74e7b4*/
        v20 = runtime_convTstring(*v262, v262[1]); /*0x74e7c4*/
        v242 = &RTYPE_string; /*0x74e7d0*/
        v243 = v20; /*0x74e7d4*/
        v244 = *(_QWORD *)(r1 + 8); /*0x74e7ec*/
        v245 = r2; /*0x74e7f4*/
        fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]Failed to open file %v, error:%v\n", 56, &v242); /*0x74e820*/
        v16 = nullptr; /*0x74e824*/
        v21 = 0; /*0x74e828*/
      }
      else
      {
        r0 = (_ptr_os_File)v269._r0; /*0x74e830*/
        v277 = os__ptr_File_Stat((_ptr_os_File)v269._r0)._r0; /*0x74e834*/
        v150 = (*((__int64 (__golang **)(void *))v277.tab + 7))(v277.data); /*0x74e844*/
        v279 = os__ptr_File_Seek(r0, v150, 0); /*0x74e854*/
        if ( v279._r1.tab ) /*0x74e858*/
        {
          data = v279._r1.data; /*0x74e85c*/
          v248 = 0; /*0x74e870*/
          v249 = nullptr; /*0x74e870*/
          v247[0] = &RTYPE_int64; /*0x74e880*/
          v247[1] = runtime_convT64(v279._r0); /*0x74e884*/
          v248 = *((_QWORD *)v279._r1.tab + 1); /*0x74e89c*/
          v249 = v279._r1.data; /*0x74e8a4*/
          fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]Seek failed %v, error:%v\n", 48, v247); /*0x74e8d0*/
          v16 = r0; /*0x74e8d4*/
          if ( r0 ) /*0x74e8d8*/
          {
            os__ptr_file_close(r0->file); /*0x74e8e4*/
            v16 = r0; /*0x74e8e8*/
          }
          v21 = 0; /*0x74e8ec*/
        }
        else
        {
          v246[0] = &RTYPE_int64; /*0x74e90c*/
          v246[1] = runtime_convT64(v150); /*0x74e910*/
          fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]Seek the file, and size is %v\n", 53, v246); /*0x74e940*/
          v16 = r0; /*0x74e944*/
          v21 = v150; /*0x74e948*/
        }
      }
      v18 = v21; /*0x74e94c*/
      v17 = r1; /*0x74e950*/
    }
    v151 = v18; /*0x74e954*/
    if ( !v16 || v17 ) /*0x74e95c*/
    {
      v286 = git_teiron_inc_cn_services_backup_cloud_worker_download_openFileWithParentPermsNoExec(*v262, v262[1], 577); /*0x74e970*/
      v16 = (os_File *)v286._r0; /*0xf1c0000000000078*/
      if ( v286._r1 ) /*0x74e974*/
      {
        r2 = (void *)v286._r2; /*0x74eb5c*/
        v238 = nullptr; /*0x74eb64*/
        v239 = 0; /*0x74eb64*/
        v240 = 0; /*0x74eb6c*/
        v241 = 0; /*0x74eb6c*/
        v33 = runtime_convTstring(*v262, v262[1]); /*0x74eb7c*/
        v238 = &RTYPE_string; /*0x74eb88*/
        v239 = v33; /*0x74eb8c*/
        v240 = *(_QWORD *)(v286._r1 + 8LL); /*0x74eba4*/
        v241 = v286._r2; /*0x74ebac*/
        v34 = fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]Open %v failed %v \n", 42, &v238); /*0x74ebd8*/
        runtime_deferreturn(v34); /*0x74ebf4*/
        result._r0 = 3; /*0x74ebf8*/
        result._r1 = v286._r1; /*0x74ebfc*/
        result._r2 = v286._r2; /*0x74ec00*/
        return result; /*0x74ec0c*/
      }
    }
    r0 = v16; /*0x74e978*/
    v237[0] = git_teiron_inc_cn_services_backup_cloud_worker_download_Download_deferwrap1; /*0x74e984*/
    v237[1] = v16; /*0x74e988*/
    v166 = v237; /*0x74e990*/
    v22 = runtime_deferprocStack(v165); /*0x74e998*/
    if ( v22 ) /*0x74e9a0*/
    {
      runtime_deferreturn(v22); /*0x74eb3c*/
      result._r0 = 0; /*0x74eb40*/
      result._r1 = v167._r0; /*0x74eb44*/
      result._r2 = v167._r1; /*0x74eb48*/
      return result; /*0x74eb54*/
    }
    a1 = v258; /*0x74e9a4*/
    a2 = v259; /*0x74e9a8*/
    a3 = v260; /*0x74e9ac*/
    a4 = v261; /*0x74e9b0*/
    a8 = v265; /*0x74e9c0*/
    a9 = v266; /*0x74e9c4*/
    a10 = v267; /*0x74e9c8*/
    v14 = v151; /*0x74e9d0*/
    v15 = r0; /*0x74e9d4*/
  }
  r0 = v15; /*0x74e9d8*/
  v152 = v14; /*0x74e9dc*/
  if ( a2 ) /*0x74e9e0*/
  {
    v270 = runtime_concatstring3(0, a3, a4, "&access_token=", 14, a1, a2, a8, a9, a10); /*0x74ea04*/
    a4 = v270._r1; /*0x74ea08*/
    a3 = v270._r0; /*0x74ea0c*/
  }
  v171 = a3; /*0x74ea10*/
  v148 = a4; /*0x74ea14*/
  v231 = 0; /*0x74ea1c*/
  v232 = 0; /*0x74ea1c*/
  v233 = nullptr; /*0x74ea24*/
  v234 = 0; /*0x74ea24*/
  v235 = 0; /*0x74ea2c*/
  v236 = 0; /*0x74ea2c*/
  ((void (*)(void))loc_4762AC)(); /*0x74ea40*/
  v233 = &v230; /*0x74ea4c*/
  HIDWORD(v232) = runtime_rand32(); /*0x74ea54*/
  v190 = (void *)net_textproto_CanonicalMIMEHeaderKey("User-Agent", 10)._r0; /*0x74ea70*/
  p__1_string = (_1_string *)runtime_newobject(&RTYPE__1_string); /*0x74ea80*/
  (*p__1_string)[0].len = a7; /*0x74ea88*/
  if ( dword_BE34B0 ) /*0x74ea94*/
  {
    p__1_string = (_1_string *)runtime_gcWriteBarrier1(); /*0x74eaa0*/
    v24 = a6; /*0x74eaa4*/
    *v25 = a6; /*0x74eaa8*/
  }
  else
  {
    v24 = a6; /*0x74ea98*/
  }
  v189 = (string *)p__1_string; /*0x74eaac*/
  (*p__1_string)[0].str = v24; /*0x74eab0*/
  v26 = (string **)runtime_mapassign_faststr(&RTYPE_net_textproto_MIMEHeader, &v231); /*0x74eac8*/
  v26[1] = (string *)1; /*0x74ead0*/
  v26[2] = (string *)1; /*0x74ead4*/
  if ( dword_BE34B0 ) /*0x74eae0*/
  {
    v26 = (string **)((__int64 (*)(void))runtime_gcWriteBarrier2)(); /*0x74eaec*/
    v27 = v189; /*0x74eaf0*/
    *v28 = v189; /*0x74eaf4*/
    v28[1] = *v26; /*0x74eafc*/
  }
  else
  {
    v27 = v189; /*0x74eae4*/
  }
  *v26 = v27; /*0x74eb00*/
  if ( a11 ) /*0x74eb08*/
  {
    v29 = ((__int64 (*)(void))loc_4762E8)(); /*0x74eb1c*/
    runtime_mapiterinit(&RTYPE_net_http_Header, v29, &v256); /*0x74eb34*/
    while ( v256 ) /*0x74fa38*/
    {
      v124 = *(uint8 ***)v257; /*0x74fa40*/
      v125 = *v256; /*0x74fa44*/
      v175 = *v256; /*0x74fa48*/
      v126 = *(_QWORD *)(v257 + 8); /*0x74fa4c*/
      v127 = v256[1]; /*0x74fa50*/
      v149 = v127; /*0x74fa54*/
      while ( v126 > 0 ) /*0x74fa84*/
      {
        v160 = v126; /*0x74fa88*/
        v187 = v124; /*0x74fa8c*/
        v169 = *v124; /*0x74fa94*/
        v146 = (__int64)v124[1]; /*0x74fa9c*/
        net_textproto_CanonicalMIMEHeaderKey(v125, v127); /*0x74faa8*/
        v128 = runtime_mapassign_faststr(&RTYPE_net_textproto_MIMEHeader, &v231); /*0x74fac0*/
        v129 = *(_QWORD *)(v128 + 16); /*0x74fac4*/
        v130 = *(_QWORD *)(v128 + 8) + 1LL; /*0x74facc*/
        r3 = *(string **)v128; /*0x74fad0*/
        if ( v129 < v130 ) /*0x74fad8*/
        {
          v186 = (string **)v128; /*0x74fadc*/
          v285 = runtime_growslice(r3, v130, v129, 1, &RTYPE_string); /*0x74faf0*/
          v142 = (string *)v285._r0; /*0xf1c000000000006c*/
          v130 = v285._r1; /*0xf1c0000000000070*/
          v132 = v186; /*0x74faf4*/
          v186[2] = (string *)v285._r2; /*0x74faf8*/
          if ( dword_BE34B0 ) /*0x74fb04*/
          {
            v294 = runtime_gcWriteBarrier2(v285._r0); /*0x74fb08*/
            v142 = (string *)v294._r0; /*0xf1c00000000000e4*/
            v130 = v294._r1; /*0xf1c00000000000e8*/
            *v133 = v294._r0; /*0x74fb0c*/
            v133[1] = *v132; /*0x74fb14*/
          }
          *v132 = v142; /*0x74fb18*/
          r3 = v142; /*0x74fb1c*/
          v128 = (__int64)v132; /*0x74fb20*/
        }
        *(_QWORD *)(v128 + 8) = v130; /*0x74fb24*/
        r3[v130 - 1].len = v146; /*0x74fb34*/
        v134 = 16 * (v130 - 1); /*0x74fb38*/
        if ( dword_BE34B0 ) /*0x74fb44*/
        {
          v295 = runtime_gcWriteBarrier2(v128); /*0x74fb50*/
          v134 = v295._r2; /*0xf1c0000000000100*/
          r3 = (string *)v295._r3; /*0xf1c0000000000104*/
          v135 = v169; /*0x74fb54*/
          *v136 = v169; /*0x74fb58*/
          v136[1] = *(uint8 **)(v295._r3 + v295._r2); /*0x74fb60*/
        }
        else
        {
          v135 = v169; /*0x74fb48*/
        }
        *(uint8 **)((char *)&r3->str + v134) = v135; /*0x74fa64*/
        v124 = v187 + 2; /*0x74fa6c*/
        v126 = v160 - 1; /*0x74fa74*/
        v125 = v175; /*0x74fa78*/
        v127 = v149; /*0x74fa7c*/
      }
      runtime_mapiternext(&v256); /*0x74fa30*/
    }
  }
  if ( v152 > 0 ) /*0x74ec18*/
  {
    v271 = strconv_FormatInt(); /*0x74ec20*/
    v272 = runtime_concatstring3(0, "bytes=", 6, v271._r0, v271._r1, &unk_903C98, 1, v35, v36, v37); /*0x74ec48*/
    v145 = v272._r1; /*0x74ec4c*/
    v168 = (uint8 *)v272._r0; /*0x74ec50*/
    v190 = (void *)net_textproto_CanonicalMIMEHeaderKey("Range", 5)._r0; /*0x74ec68*/
    v38 = (_1_string *)runtime_newobject(&RTYPE__1_string); /*0x74ec78*/
    (*v38)[0].len = v145; /*0x74ec80*/
    if ( dword_BE34B0 ) /*0x74ec8c*/
    {
      v38 = (_1_string *)runtime_gcWriteBarrier1(); /*0x74ec98*/
      v39 = v168; /*0x74ec9c*/
      *v40 = v168; /*0x74eca0*/
    }
    else
    {
      v39 = v168; /*0x74ec90*/
    }
    v189 = (string *)v38; /*0x74eca4*/
    (*v38)[0].str = v39; /*0x74eca8*/
    v41 = (string **)runtime_mapassign_faststr(&RTYPE_net_textproto_MIMEHeader, &v231); /*0x74ecc0*/
    v41[1] = (string *)1; /*0x74ecc8*/
    v41[2] = (string *)1; /*0x74eccc*/
    if ( dword_BE34B0 ) /*0x74ecd8*/
    {
      v41 = (string **)((__int64 (*)(void))runtime_gcWriteBarrier2)(); /*0x74ece4*/
      v42 = v189; /*0x74ece8*/
      *v43 = v189; /*0x74ecec*/
      v43[1] = *v41; /*0x74ecf4*/
    }
    else
    {
      v42 = v189; /*0x74ecdc*/
    }
    *v41 = v42; /*0x74ecf8*/
    v226 = nullptr; /*0x74ed00*/
    v227 = 0; /*0x74ed00*/
    v228 = nullptr; /*0x74ed08*/
    v229 = 0; /*0x74ed08*/
    v44 = runtime_convTstring(*v262, v262[1]); /*0x74ed18*/
    v226 = &RTYPE_string; /*0x74ed24*/
    v227 = v44; /*0x74ed28*/
    v45 = runtime_convT64(v152); /*0x74ed30*/
    v228 = &RTYPE_int64; /*0x74ed3c*/
    v229 = v45; /*0x74ed40*/
    fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]continue download to %v from size %v\n", 60, &v226); /*0x74ed6c*/
  }
  p_net_http_Transport = (net_http_Transport *)runtime_newobject(&RTYPE_net_http_Transport); /*0x74ed7c*/
  p_net_http_Transport->MaxIdleConnsPerHost = -1; /*0x74ed84*/
  p_crypto_tls_Config = (crypto_tls_Config *)runtime_newobject(&RTYPE_crypto_tls_Config); /*0x74ed90*/
  p_crypto_tls_Config->InsecureSkipVerify = 1; /*0x74ed98*/
  if ( dword_BE34B0 ) /*0x74eda4*/
  {
    p_crypto_tls_Config = (crypto_tls_Config *)((__int64 (*)(void))runtime_gcWriteBarrier2)(); /*0x74edb0*/
    *v48 = p_crypto_tls_Config; /*0x74edb4*/
    v47 = p_net_http_Transport; /*0x74edb8*/
    v48[1] = p_net_http_Transport->TLSClientConfig; /*0x74edc0*/
  }
  else
  {
    v47 = p_net_http_Transport; /*0x74eda8*/
  }
  v47->TLSClientConfig = p_crypto_tls_Config; /*0x74edc4*/
  p_net_http_Client = (net_http_Client *)runtime_newobject(&RTYPE_net_http_Client); /*0x74edd0*/
  p_net_http_Client->Transport.tab = off_908860; /*0x74eddc*/
  if ( dword_BE34B0 ) /*0x74ede8*/
  {
    p_net_http_Client = (net_http_Client *)runtime_gcWriteBarrier1(); /*0x74edf4*/
    v50 = p_net_http_Transport; /*0x74edf8*/
    *v51 = p_net_http_Transport; /*0x74edfc*/
  }
  else
  {
    v50 = p_net_http_Transport; /*0x74edec*/
  }
  v184 = p_net_http_Client; /*0x74ee00*/
  p_net_http_Client->Transport.data = v50; /*0x74ee04*/
  p_net_http_Client->Timeout = 3600000000000LL; /*0x74ee14*/
  v287 = net_http_NewRequestWithContext(off_90CC60, &unk_BE2EC0, "GET", 3, v171, v148, 0, 0); /*0x74ee44*/
  if ( v287._r1 ) /*0x74ee48*/
  {
    v167 = *(retval_4D6BE0 *)&v287._r1; /*0x74ee8c*/
    runtime_deferreturn(1); /*0x74ee94*/
    result._r0 = 1; /*0x74ee98*/
    result._r2 = v167._r1; /*0x74eea0*/
    result._r1 = v167._r0; /*0x74eea0*/
    return result; /*0x74eeac*/
  }
  v174 = (_ptr_net_http_Request)v287._r0; /*0x74ee4c*/
  ((void (*)(void))loc_4762E8)(); /*0x74ee60*/
  runtime_mapiterinit(&RTYPE_net_http_Header, &v231, &v256); /*0x74ee78*/
  v52 = v174; /*0x74ee7c*/
  while ( v256 ) /*0x74eec4*/
  {
    v53 = *(uint8 ***)v257; /*0x74eecc*/
    v54 = *v256; /*0x74eed0*/
    v176 = *v256; /*0x74eed4*/
    v55 = *(_QWORD *)(v257 + 8); /*0x74eed8*/
    v56 = v256[1]; /*0x74eedc*/
    v153 = v56; /*0x74eee0*/
    while ( v55 > 0 ) /*0x74f944*/
    {
      v159 = v55; /*0x74f948*/
      v187 = v53; /*0x74f94c*/
      Header = v52->Header; /*0x74f954*/
      v170 = *v53; /*0x74f95c*/
      v147 = (__int64)v53[1]; /*0x74f964*/
      net_textproto_CanonicalMIMEHeaderKey(v54, v56); /*0x74f96c*/
      v115 = runtime_mapassign_faststr(&RTYPE_net_textproto_MIMEHeader, Header); /*0x74f984*/
      v116 = *(_QWORD *)(v115 + 16); /*0x74f988*/
      v117 = *(_QWORD *)(v115 + 8) + 1LL; /*0x74f990*/
      v118 = *(string **)v115; /*0x74f994*/
      if ( v116 < v117 ) /*0x74f99c*/
      {
        v186 = (string **)v115; /*0x74f9a0*/
        v284 = runtime_growslice(v118, v117, v116, 1, &RTYPE_string); /*0x74f9b4*/
        v141 = (string *)v284._r0; /*0xf1c0000000000060*/
        v117 = v284._r1; /*0xf1c0000000000064*/
        v119 = v186; /*0x74f9b8*/
        v186[2] = (string *)v284._r2; /*0x74f9bc*/
        if ( dword_BE34B0 ) /*0x74f9c8*/
        {
          v292 = runtime_gcWriteBarrier2(v284._r0); /*0x74f9cc*/
          v141 = (string *)v292._r0; /*0xf1c00000000000bc*/
          v117 = v292._r1; /*0xf1c00000000000c0*/
          *v120 = v292._r0; /*0x74f9d0*/
          v120[1] = *v119; /*0x74f9d8*/
        }
        *v119 = v141; /*0x74f9dc*/
        v118 = v141; /*0x74f9e0*/
        v115 = (__int64)v119; /*0x74f9e4*/
      }
      *(_QWORD *)(v115 + 8) = v117; /*0x74f9e8*/
      v118[v117 - 1].len = v147; /*0x74f9f8*/
      v121 = 16 * (v117 - 1); /*0x74f9fc*/
      if ( dword_BE34B0 ) /*0x74fa08*/
      {
        v293 = runtime_gcWriteBarrier2(v115); /*0x74fa14*/
        v121 = v293._r2; /*0xf1c00000000000d8*/
        v118 = (string *)v293._r3; /*0xf1c00000000000dc*/
        v122 = v170; /*0x74fa18*/
        *v123 = v170; /*0x74fa1c*/
        v123[1] = *(uint8 **)(v293._r3 + v293._r2); /*0x74fa24*/
      }
      else
      {
        v122 = v170; /*0x74fa0c*/
      }
      *(uint8 **)((char *)&v118->str + v121) = v122; /*0x74f920*/
      v53 = v187 + 2; /*0x74f928*/
      v55 = v159 - 1; /*0x74f930*/
      v54 = v176; /*0x74f934*/
      v56 = v153; /*0x74f938*/
      v52 = v174; /*0x74f93c*/
    }
    runtime_mapiternext(&v256); /*0x74eeb4*/
    v52 = v174; /*0x74eebc*/
  }
  v57 = 1; /*0x74eee8*/
  for ( i = nullptr; ; i = v173 )
  {
    if ( v57 > 3 ) /*0x74ef04*/
    {
      v137 = i; /*0x74f0c4*/
      goto LABEL_65; /*0x74f0c4*/
    }
    v154 = v57; /*0x74ef08*/
    v280 = net_http__ptr_Client_do(v184, v52); /*0x74ef18*/
    v137 = v280._r0; /*0xf1c0000000000020*/
    v173 = v280._r0; /*0x74ef1c*/
    if ( !v280._r1.tab ) /*0x74ef20*/
      break; /*0x74ef20*/
    r2 = v280._r1.data; /*0x74ef24*/
    v212 = nullptr; /*0x74ef30*/
    v213 = 0; /*0x74ef30*/
    v214 = nullptr; /*0x74ef38*/
    v215 = 0; /*0x74ef38*/
    v216 = nullptr; /*0x74ef40*/
    v217 = 0; /*0x74ef40*/
    v218 = 0; /*0x74ef48*/
    v219 = nullptr; /*0x74ef48*/
    v59 = runtime_convT64(v154); /*0x74ef50*/
    v212 = &RTYPE_int; /*0x74ef5c*/
    v213 = v59; /*0x74ef60*/
    v60 = runtime_convT64(3); /*0x74ef68*/
    v214 = &RTYPE_int; /*0x74ef74*/
    v215 = v60; /*0x74ef78*/
    v61 = runtime_convTstring(*v262, v262[1]); /*0x74ef8c*/
    v216 = &RTYPE_string; /*0x74ef98*/
    v217 = v61; /*0x74ef9c*/
    v218 = *((_QWORD *)v280._r1.tab + 1); /*0x74efb4*/
    v219 = r2; /*0x74efbc*/
    fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]Attempt %d/%d failed for %v, err: %v\n", 60, &v212);
    if ( v154 == 3 )
    {
      v250 = nullptr; /*0x74effc*/
      v251 = 0; /*0x74effc*/
      v252 = 0; /*0x74f004*/
      v253 = nullptr; /*0x74f004*/
      v62 = runtime_convT64(3); /*0x74f00c*/
      v250 = &RTYPE_int; /*0x74f018*/
      v251 = v62; /*0x74f01c*/
      v252 = *((_QWORD *)v280._r1.tab + 1); /*0x74f034*/
      v253 = r2; /*0x74f03c*/
      v167 = fmt_Errorf("failed to open remote after %d retries: %v", 42, &v250, 2, 2);
      runtime_deferreturn(v167._r0); /*0x74f06c*/
      result._r0 = 1; /*0x74f070*/
      result._r2 = v167._r1; /*0x74f078*/
      result._r1 = v167._r0; /*0x74f078*/
      return result; /*0x74f084*/
    }
    v57 = v154 + 1; /*0x74eef4*/
    v52 = v174; /*0x74eef8*/
  }
  StatusCode = v280._r0->StatusCode; /*0x74f088*/
  if ( StatusCode == 200 || StatusCode == 206 )
  {
LABEL_65:
    v173 = v137; /*0x74f0c8*/
    tab = v137->Body.tab; /*0x74f0cc*/
    v70 = v137->Body.data; /*0x74f0d4*/
    v211[0] = git_teiron_inc_cn_services_backup_cloud_worker_download_Download_deferwrap2; /*0x74f0e0*/
    v211[1] = tab; /*0x74f0e4*/
    v211[2] = v70; /*0x74f0e8*/
    v164 = v211; /*0x74f0f0*/
    v71 = runtime_deferprocStack(v163); /*0x74f0f8*/
    if ( !v71 )
    {
      v72 = v265; /*0x74f104*/
      if ( !v265 ) /*0x74f108*/
      {
        v209 = nullptr; /*0x74f110*/
        v210 = 0; /*0x74f110*/
        v73 = runtime_convTstring(*v262, v262[1]); /*0x74f120*/
        v209 = &RTYPE_string; /*0x74f12c*/
        v210 = v73; /*0x74f130*/
        fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]specfied resource size is 0, %v\n", 55, &v209); /*0x74f15c*/
        v72 = 0; /*0x74f160*/
      }
      v74 = v262[1]; /*0x74f168*/
      if ( v74 >= 4 ) /*0x74f170*/
      {
        v75 = runtime_memequal(*v262 + v74 - 4, ".fot"); /*0x74f19c*/
        v72 = v265; /*0x74f1a0*/
      }
      else
      {
        v75 = 0; /*0x74f174*/
      }
      if ( (v75 & 1) == 0 || !v267 ) /*0x74f1ac*/
      {
        v83 = (__int64)v173->Body.data; /*0x74f1f0*/
        v84 = (__int64)v173->Body.tab; /*0x74f1f4*/
        if ( v84 ) /*0x74f1f8*/
        {
          v85 = *(_QWORD *)(v84 + 8); /*0x74f1fc*/
          v86 = (_QWORD *)atomic_load((unsigned __int64 *)&off_B73500); /*0x74f208*/
          v87 = *(unsigned int *)(v84 + 16); /*0x74f20c*/
          while ( 1 ) /*0x74f2dc*/
          {
            v88 = 16 * (v87 & *v86) + 8; /*0x74f2dc*/
            v89 = *(_QWORD *)((char *)v86 + v88); /*0x74f2e0*/
            v90 = (char *)v86 + v88; /*0x74f2e4*/
            if ( v89 == v85 ) /*0x74f2ec*/
              break; /*0x74f2ec*/
            ++v87; /*0x74f2f0*/
            if ( !v89 ) /*0x74f2f4*/
            {
              v190 = v173->Body.data; /*0x74f2f8*/
              v91 = runtime_typeAssert(&off_B73500); /*0x74f300*/
              v83 = (__int64)v190; /*0x74f304*/
              v84 = v91; /*0x74f308*/
              v72 = v265; /*0x74f30c*/
              goto LABEL_78; /*0x74f310*/
            }
          }
          v84 = *((_QWORD *)v90 + 1); /*0x74f314*/
        }
LABEL_78:
        v281 = git_teiron_inc_cn_services_backup_cloud_worker_download_CopyBuffer((__int64)r0, v72, v84, v83); /*0x74f234*/
        v138 = v281._r0; /*0xf1c000000000002c*/
        v139 = v281._r1; /*0xf1c0000000000030*/
        v140 = v281._r2; /*0xf1c0000000000034*/
        if ( LODWORD(v281._r0) ) /*0x74f244*/
          goto LABEL_100; /*0x74f244*/
        v288 = git_teiron_inc_cn_services_backup_cloud_worker_download_checkFileSize(*v262, v262[1], v265); /*0x74f258*/
        LODWORD(v138) = v288._r0; /*0xf1c0000000000090*/
        v139 = v288._r1; /*0xf1c0000000000094*/
        v140 = v288._r2; /*0xf1c0000000000098*/
        if ( v288._r0 ) /*0x74f25c*/
          goto LABEL_100; /*0x74f25c*/
        goto LABEL_82; /*0x74f25c*/
      }
      v76 = runtime_stringtoslicebyte(0, v266, v267); /*0x74f1bc*/
      v79 = (__int64)v173->Body.data; /*0x74f1c4*/
      v80 = (__int64)v173->Body.tab; /*0x74f1c8*/
      if ( v80 ) /*0x74f1cc*/
      {
        v81 = (_QWORD *)atomic_load((unsigned __int64 *)&off_B734E0); /*0x74f1dc*/
        v82 = *(unsigned int *)(v80 + 16); /*0x74f1e0*/
        while ( 1 ) /*0x74f748*/
        {
          v103 = 16 * (v82 & *v81) + 8; /*0x74f748*/
          v104 = *(_QWORD *)((char *)v81 + v103); /*0x74f74c*/
          v105 = (char *)v81 + v103; /*0x74f750*/
          if ( v104 == *(_QWORD *)(v80 + 8) ) /*0x74f758*/
            break; /*0x74f758*/
          ++v82; /*0x74f75c*/
          if ( !v104 ) /*0x74f760*/
          {
            v158 = v78; /*0x74f764*/
            v157 = v77; /*0x74f768*/
            v190 = (void *)v76; /*0x74f76c*/
            v188 = v79; /*0x74f770*/
            v106 = runtime_typeAssert(&off_B734E0); /*0x74f77c*/
            v77 = v157; /*0x74f780*/
            v78 = v158; /*0x74f784*/
            v79 = v188; /*0x74f788*/
            v80 = v106; /*0x74f78c*/
            v76 = (__int64)v190; /*0x74f790*/
            goto LABEL_87; /*0x74f794*/
          }
        }
        v80 = *((_QWORD *)v105 + 1); /*0x74f798*/
      }
LABEL_87:
      v282 = git_teiron_inc_cn_services_backup_cloud_crypto_NewDecryptReader(v80, v79, v76, v77, v78); /*0x74f31c*/
      if ( v282._r1 )
      {
        v180 = v282._r2; /*0x74f344*/
        v205 = nullptr; /*0x74f350*/
        v206 = 0; /*0x74f350*/
        v207 = 0; /*0x74f358*/
        v208 = 0; /*0x74f358*/
        v92 = runtime_convTstring(*v262, v262[1]); /*0x74f368*/
        v205 = &RTYPE_string; /*0x74f374*/
        v206 = v92; /*0x74f378*/
        v207 = *(_QWORD *)(v282._r1 + 8LL); /*0x74f390*/
        v208 = v180; /*0x74f398*/
        fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]failed decryt file %v: %v\n", 49, &v205);
        if ( (errors_Is(v282._r1, v180, off_B71500, off_B71508) & 1) != 0 ) /*0x74f3e4*/
          v143 = 16; /*0x74f3ec*/
        else
          v143 = 13; /*0x74f420*/
        v167._r0 = v282._r1; /*0x74f3f4*/
        v167._r1 = v180; /*0x74f3fc*/
        runtime_deferreturn(v180); /*0x74f400*/
        result._r0 = v143; /*0x74f404*/
        result._r1 = v167._r0; /*0x74f408*/
        result._r2 = v167._r1; /*0x74f40c*/
        return result; /*0x74f418*/
      }
      v183 = (_QWORD *)v282._r0; /*0x74f450*/
      v273 = internal_filepathlite_Dir(*v262, v262[1]); /*0x74f460*/
      v203 = 0; /*0x74f470*/
      v204 = 0; /*0x74f470*/
      v202 = v273; /*0x74f474*/
      v93 = v183[6]; /*0x74f480*/
      v204 = v183[7]; /*0x74f488*/
      v203 = v93; /*0x74f48c*/
      v274 = path_filepath_join(&v202); /*0x74f49c*/
      v94 = v262; /*0x74f4a0*/
      v262[1] = v274._r1; /*0x74f4a4*/
      if ( dword_BE34B0 ) /*0x74f4b0*/
      {
        v291 = runtime_gcWriteBarrier2(v274._r0); /*0x74f4b4*/
        v274 = *(retval_5A2DD0 *)&v291._r0; /*0xf1c000000000009c*/
        v94 = (_QWORD *)v291._r2; /*0xf1c00000000000a4*/
        *v95 = v291._r0; /*0x74f4b8*/
        v95[1] = *(_QWORD *)v291._r2; /*0x74f4c0*/
      }
      *v94 = v274._r0; /*0x74f4c4*/
      v275 = os_OpenFile(v274._r0, v274._r1, 577, 438); /*0x74f4d0*/
      if ( v275._r1 )
      {
        v155 = v275._r1; /*0x74f4d8*/
        v180 = v96; /*0x74f4dc*/
        v198 = nullptr; /*0x74f4e4*/
        v199 = 0; /*0x74f4e4*/
        v200 = 0; /*0x74f4ec*/
        v201 = 0; /*0x74f4ec*/
        v97 = runtime_convTstring(*v262, v262[1]); /*0x74f4fc*/
        v198 = &RTYPE_string; /*0x74f508*/
        v199 = v97; /*0x74f50c*/
        v200 = *(_QWORD *)(v155 + 8); /*0x74f524*/
        v201 = v180; /*0x74f52c*/
        v98 = fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]failed to create file %v: %v", 51, &v198);
        v167._r0 = v155; /*0x74f568*/
        v167._r1 = v180; /*0x74f570*/
        runtime_deferreturn(v98); /*0x74f574*/
        result._r0 = 3; /*0x74f578*/
        result._r1 = v167._r0; /*0x74f57c*/
        result._r2 = v167._r1; /*0x74f580*/
        return result; /*0x74f58c*/
      }
      v178 = v275._r0; /*0x74f590*/
      v197[0] = git_teiron_inc_cn_services_backup_cloud_worker_download_Download_deferwrap3; /*0x74f59c*/
      v197[1] = v275._r0; /*0x74f5a0*/
      v162 = v197; /*0x74f5a8*/
      v71 = runtime_deferprocStack(v161); /*0x74f5b0*/
      if ( !v71 ) /*0x74f5b8*/
      {
        v283 = git_teiron_inc_cn_services_backup_cloud_worker_download_CopyBuffer( /*0x74f5d0*/
                 v178,
                 v265,
                 (__int64)off_908BE0,
                 (__int64)v183);
        v138 = v283._r0; /*0xf1c0000000000044*/
        v139 = v283._r1; /*0xf1c0000000000048*/
        v140 = v283._r2; /*0xf1c000000000004c*/
        if ( LODWORD(v283._r0) ) /*0x74f5d4*/
          goto LABEL_100; /*0x74f5d4*/
        if ( v265 != v183[8] ) /*0x74f5e8*/
        {
          v180 = v283._r2; /*0x74f62c*/
          v191 = nullptr; /*0x74f638*/
          v192 = 0; /*0x74f638*/
          v193 = nullptr; /*0x74f640*/
          v194 = 0; /*0x74f640*/
          v195 = nullptr; /*0x74f648*/
          v196 = 0; /*0x74f648*/
          v99 = runtime_convTstring(v183[6], v183[7]); /*0x74f654*/
          v191 = &RTYPE_string; /*0x74f660*/
          v192 = v99; /*0x74f664*/
          v100 = runtime_convT64(v265); /*0x74f66c*/
          v193 = &RTYPE_uint64; /*0x74f678*/
          v194 = v100; /*0x74f67c*/
          v101 = runtime_convT64(v183[8]); /*0x74f688*/
          v195 = &RTYPE_int64; /*0x74f694*/
          v196 = v101; /*0x74f698*/
          v102 = fmt_Fprintf( /*0x74f6c4*/
                   off_9081A0,
                   qword_BB97E8,
                   "[BackupCloud][Download]decrypt download failed, file %v , expected size %v, actual:%v",
                   85,
                   &v191);
          v167._r0 = v283._r1; /*0x74f6d4*/
          v167._r1 = v180; /*0x74f6dc*/
          runtime_deferreturn(v102); /*0x74f6e0*/
          result._r0 = 13; /*0x74f6e4*/
          result._r1 = v167._r0; /*0x74f6e8*/
          result._r2 = v167._r1; /*0x74f6ec*/
          return result; /*0x74f6f8*/
        }
        v289 = git_teiron_inc_cn_services_backup_cloud_worker_download_checkFileSize(*v262, v262[1], v183[9]); /*0x74f5fc*/
        LODWORD(v138) = v289._r0; /*0xf1c00000000000b0*/
        v139 = v289._r1; /*0xf1c00000000000b4*/
        v140 = v289._r2; /*0xf1c00000000000b8*/
        if ( v289._r0 ) /*0x74f600*/
        {
LABEL_100:
          v144 = v138; /*0x74f604*/
          v167._r0 = v139; /*0x74f608*/
          v167._r1 = v140; /*0x74f60c*/
          runtime_deferreturn(v138); /*0x74f610*/
          result._r0 = v144; /*0x74f614*/
          result._r1 = v167._r0; /*0x74f618*/
          result._r2 = v167._r1; /*0x74f61c*/
          return result; /*0x74f628*/
        }
LABEL_82:
        v167._r0 = 0; /*0x74f2b0*/
        v167._r1 = 0; /*0x74f2b4*/
        runtime_deferreturn(v138); /*0x74f2b8*/
        result._r0 = 0; /*0x74f2bc*/
        result._r1 = v167._r0; /*0x74f2c0*/
        result._r2 = v167._r1; /*0x74f2c4*/
        return result; /*0x74f2d0*/
      }
    }
    runtime_deferreturn(v71); /*0x74f218*/
    result._r0 = 0; /*0x74f21c*/
    result._r1 = v167._r0; /*0x74f220*/
    result._r2 = v167._r1; /*0x74f224*/
    return result; /*0x74f230*/
  }
  v64 = v280._r0->Body.data; /*0x74f09c*/
  v65 = v280._r0->Body.tab; /*0x74f0a0*/
  if ( v65 ) /*0x74f0a4*/
  {
    v66 = v65[1]; /*0x74f0a8*/
    v67 = (_QWORD *)atomic_load((unsigned __int64 *)&off_B734C0); /*0x74f0b4*/
    v68 = *((unsigned int *)v65 + 4); /*0x74f0b8*/
    while ( 1 ) /*0x74f8e0*/
    {
      v111 = 16 * (v68 & *v67) + 8; /*0x74f8e0*/
      v112 = *(_QWORD *)((char *)v67 + v111); /*0x74f8e4*/
      v113 = (char *)v67 + v111; /*0x74f8e8*/
      if ( v112 == v66 ) /*0x74f8f0*/
        break; /*0x74f8f0*/
      ++v68; /*0x74f8f4*/
      if ( !v112 ) /*0x74f8f8*/
      {
        v190 = v280._r0->Body.data; /*0x74f8fc*/
        v114 = runtime_typeAssert(&off_B734C0); /*0x74f908*/
        v64 = v190; /*0x74f90c*/
        v65 = (_QWORD *)v114; /*0x74f910*/
        goto LABEL_108; /*0x74f914*/
      }
    }
    v65 = *((_QWORD **)v113 + 1); /*0x74f918*/
  }
LABEL_108:
  All = io_ReadAll(v65, v64); /*0x74f7a0*/
  v185 = All._r0; /*0x74f7a8*/
  v220 = nullptr; /*0x74f7b4*/
  v221 = 0; /*0x74f7b4*/
  v222 = nullptr; /*0x74f7bc*/
  v223 = 0; /*0x74f7bc*/
  v224 = nullptr; /*0x74f7c4*/
  v225 = 0; /*0x74f7c4*/
  v107 = runtime_convT64(v173->StatusCode); /*0x74f7d4*/
  v220 = &RTYPE_int; /*0x74f7e0*/
  v221 = v107; /*0x74f7e4*/
  v108 = runtime_convTstring(*v262, v262[1]); /*0x74f7f4*/
  v222 = &RTYPE_string; /*0x74f800*/
  v223 = v108; /*0x74f804*/
  v276 = runtime_slicebytetostring(0, v185, All._r1); /*0x74f814*/
  v109 = runtime_convTstring(v276._r0, v276._r1); /*0x74f818*/
  v224 = &RTYPE_string; /*0x74f824*/
  v225 = v109; /*0x74f828*/
  fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]HTTP %d for %v, body: %v\n", 48, &v220);
  (*((void (__golang **)(void *))v173->Body.tab + 3))(v173->Body.data); /*0x74f868*/
  v254 = nullptr; /*0x74f870*/
  v255 = 0; /*0x74f870*/
  v110 = runtime_convT64(v173->StatusCode); /*0x74f87c*/
  v254 = &RTYPE_int; /*0x74f888*/
  v255 = v110; /*0x74f88c*/
  v167 = fmt_Errorf("failed to open remote, status:%d", 32, &v254, 1, 1); /*0x74f8b4*/
  runtime_deferreturn(v167._r0); /*0x74f8bc*/
  result._r0 = 19; /*0x74f8c0*/
  result._r2 = v167._r1; /*0x74f8c8*/
  result._r1 = v167._r0; /*0x74f8c8*/
  return result; /*0x74eb54*/
}
```

## 6. 单文件入口：`Executor.DownloadFile`

这个函数负责取得 driver 的下载信息，再把对象交给共同下载器。

```c
// git.teiron-inc.cn/services/backup-cloud/worker.(*Executor).DownloadFile
void __golang git_teiron_inc_cn_services_backup_cloud_worker__ptr_Executor_DownloadFile(
        _ptr_git_teiron_inc_cn_services_backup_cloud_worker_Executor a1,
        string a2,
        string a3)
{
  git_teiron_inc_cn_services_backup_cloud_internal_model_Object *p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object; // x0
  uint8 *v4; // x11
  uint8 *v5; // x12
  time_Location *v6; // x13
  uint8 *str; // x14
  uint8 **v8; // x25
  __int64 len; // x6
  __int64 v10; // x10
  uint8 *v11; // x8
  __int64 v12; // x0
  __int64 v13; // x0
  __int64 v14; // x1
  __int64 v15; // x2
  __int64 r1; // [xsp+80h] [xbp-138h]
  __int64 v17; // [xsp+88h] [xbp-130h]
  git_teiron_inc_cn_services_backup_cloud_worker_common_Result v18; // [xsp+A0h] [xbp-118h] BYREF
  retval_454240 v19; // [xsp+150h] [xbp-68h] BYREF
  __int64 *v20; // [xsp+160h] [xbp-58h]
  uint8 *r0; // [xsp+168h] [xbp-50h]
  uint8 *v22; // [xsp+170h] [xbp-48h]
  __int64 v23; // [xsp+178h] [xbp-40h]
  __int64 v24; // [xsp+180h] [xbp-38h]
  __int64 v25; // [xsp+188h] [xbp-30h]
  __int64 v26; // [xsp+190h] [xbp-28h]
  __int64 v27; // [xsp+198h] [xbp-20h]
  __int64 v28; // [xsp+1A0h] [xbp-18h]
  time_Location *r2; // [xsp+1A8h] [xbp-10h]
  retval_6E2590 v32; // 0:x0.16
  retval_4C5D80 v33; // 0:x0.16
  retval_470A10 v34; // 0:x0.16
  retval_7578D8 v35; // 0:x0.16
  retval_757900 v36; // 0:x0.16
  retval_74E690 v37; // 0:kr00_24.24
  retval_74E690 v38; // 0:kr28_24.24
  retval_4C3660 v39; // 0:kr40_24.24
  retval_75767C v40; // 0:kr68_24.24
  retval_4FCED0 v41; // 0:kr80_32.32

  v32 = git_teiron_inc_cn_services_backup_cloud_pkg_utils_FixAndCleanPath(a2.str, a2.len); /*0x757578*/
  r0 = (uint8 *)v32._r0; /*0x75757c*/
  r1 = v32._r1; /*0x757580*/
  v33 = path_Base(); /*0x757584*/
  v22 = (uint8 *)v33._r0; /*0x757588*/
  v17 = v33._r1; /*0x75758c*/
  v39 = time_Now(); /*0x757590*/
  r2 = (time_Location *)v39._r2; /*0x757598*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object = (git_teiron_inc_cn_services_backup_cloud_internal_model_Object *)runtime_newobject(&RTYPE_git_teiron_inc_cn_services_backup_cloud_internal_model_Object); /*0x7575a8*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Path.len = r1; /*0x7575b0*/
  if ( dword_BE34B0 ) /*0x7575bc*/
  {
    p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object = (git_teiron_inc_cn_services_backup_cloud_internal_model_Object *)runtime_gcWriteBarrier4(p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object); /*0x7575d4*/
    v4 = r0; /*0x7575d8*/
    *v8 = r0; /*0x7575dc*/
    v5 = v22; /*0x7575e0*/
    v8[1] = v22; /*0x7575e4*/
    v6 = r2; /*0x7575e8*/
    v8[2] = (uint8 *)r2; /*0x7575ec*/
    str = a3.str; /*0x7575f0*/
    v8[3] = a3.str; /*0x7575f4*/
  }
  else
  {
    v4 = r0; /*0x7575c0*/
    v5 = v22; /*0x7575c4*/
    v6 = r2; /*0x7575c8*/
    str = a3.str; /*0x7575cc*/
  }
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Path.str = v4; /*0x7575f8*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Name.len = v17; /*0x757600*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Name.str = v5; /*0x757604*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Modified.wall = v39._r0; /*0x75760c*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Modified.ext = v39._r1; /*0x757614*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Modified.loc = v6; /*0x757618*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->IsFolder = 1; /*0x757620*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->ID.len = a3.len; /*0x757628*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->ID.str = str; /*0x75762c*/
  v23 = 0; /*0x757630*/
  v24 = 0; /*0x757630*/
  v25 = 0; /*0x757634*/
  v26 = 0; /*0x757634*/
  v27 = 0; /*0x757638*/
  v28 = 0; /*0x757638*/
  v40 = ((retval_75767C (__golang *)(void *, void *, void *, RTYPE **, git_teiron_inc_cn_services_backup_cloud_internal_model_Object *, _QWORD, _QWORD, _QWORD, _QWORD, _QWORD, _QWORD))*((_QWORD *)a1->driver.tab + 9))( /*0x75767c*/
          a1->driver.data,
          a1->context.tab,
          a1->context.data,
          off_90E7F8,
          p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object,
          0,
          0,
          0,
          0,
          0,
          0);
  if ( v40._r1 ) /*0x757680*/
  {
    v36 = ((retval_757900 (__golang *)(_QWORD))*(_QWORD *)(v40._r1 + 24LL))(v40._r2); /*0x757900*/
    git_teiron_inc_cn_services_backup_cloud_worker_packFailedResult(6, v36._r0, v36._r1); /*0x757910*/
    return; /*0x757910*/
  }
  v20 = (__int64 *)v40._r0; /*0x757684*/
  v19 = runtime_concatstring2(0, "./", 2, v22, v17); /*0x7576a4*/
  len = a1->driverName.len; /*0x7576b0*/
  v10 = *(_QWORD *)(v40._r0 + 16LL); /*0x7576b8*/
  v11 = a1->driverName.str; /*0x7576bc*/
  if ( len > 8 ) /*0x7576c4*/
  {
    if ( len == 11 ) /*0x757730*/
    {
      if ( *(_QWORD *)v11 != 0x7244656C676F6F47LL || *((_WORD *)v11 + 4) != 30313 || v11[10] != 101 ) /*0x757768*/
        goto LABEL_25; /*0x757768*/
    }
    else if ( len != 15 /*0x7577c0*/
           || *(_QWORD *)v11 != 0x72646E7579696C41LL
           || *((_DWORD *)v11 + 2) != 1332049513
           || *((_WORD *)v11 + 6) != 25968
           || v11[14] != 110 )
    {
      goto LABEL_25; /*0x7577c0*/
    }
    goto LABEL_24; /*0x757768*/
  }
  if ( len != 7 ) /*0x7576cc*/
  {
    if ( len != 8 || *(_QWORD *)v11 != 0x6576697264656E4FLL ) /*0x757724*/
      goto LABEL_25; /*0x757724*/
LABEL_24:
    v37 = git_teiron_inc_cn_services_backup_cloud_worker_download_Download( /*0x7577c4*/
            0,
            0,
            *v20,
            v20[1],
            &v19,
            (uint8 *)off_B714B0,
            qword_B714B8,
            5242880,
            0,
            0,
            v10);
    v14 = v37._r1; /*0xf1c0000000000008*/
    v15 = v37._r2; /*0xf1c000000000000c*/
    goto LABEL_26; /*0x7577f8*/
  }
  if ( *(_DWORD *)v11 == 1886351940 && *((_WORD *)v11 + 2) == 28514 && v11[6] == 120 ) /*0x7576fc*/
    goto LABEL_24; /*0x7576fc*/
LABEL_25:
  v38 = git_teiron_inc_cn_services_backup_cloud_worker_download_Download( /*0x7577fc*/
          (__int64)a1->token.str,
          a1->token.len,
          *v20,
          v20[1],
          &v19,
          (uint8 *)"pan.baidu.com",
          13,
          5242880,
          0,
          0,
          v10);
  v14 = v38._r1; /*0xf1c0000000000014*/
  v15 = v38._r2; /*0xf1c0000000000018*/
LABEL_26:
  if ( v14 ) /*0x75782c*/
  {
    v35 = ((retval_7578D8 (__golang *)(__int64))*(_QWORD *)(v14 + 24))(v15); /*0x7578d8*/
    git_teiron_inc_cn_services_backup_cloud_worker_packFailedResult(6, v35._r0, v35._r1); /*0x7578e8*/
  }
  else
  {
    v12 = (*((__int64 (__golang **)(void *))a1->driver.tab + 7))(a1->driver.data); /*0x757840*/
    ((void (__golang *)(__int64))loc_4762D4)(v12); /*0x757854*/
    ((void (*)(void))loc_4764D8)(); /*0x757870*/
    v13 = runtime_convT(&RTYPE_git_teiron_inc_cn_services_backup_cloud_worker_common_Result, &v18); /*0x757884*/
    v41 = encoding_json_Marshal(&RTYPE_git_teiron_inc_cn_services_backup_cloud_worker_common_Result, v13); /*0x757894*/
    if ( v41._r3 ) /*0x757898*/
    {
      git_teiron_inc_cn_services_backup_cloud_worker_packFailedResult(4, 0, 0); /*0x7578a8*/
    }
    else
    {
      v34 = runtime_slicebytetostring(0, v41._r0, v41._r1); /*0x7578bc*/
      git_teiron_inc_cn_services_backup_cloud_worker_packSucceedResult(v34._r0, v34._r1); /*0x7578c0*/
    }
  }
}
```

## 7. Worker 入口：下载重试和失败清理

这个函数能帮助识别批量/远程恢复变体。`0x760774` 和 `0x7607b4` 都会进入共同下载器；失败时的 `os.Remove` 在 `0x7608e0`。

```c
// git.teiron-inc.cn/services/backup-cloud/worker.(*Worker).downloadRemoteFile
retval_760120 __golang git_teiron_inc_cn_services_backup_cloud_worker__ptr_Worker_downloadRemoteFile(
        __int64 a1,
        uint8 *a2,
        __int64 a3,
        uint8 *a4,
        __int64 a5,
        _QWORD *a6,
        __int64 a7,
        __int64 a8,
        __int64 a9)
{
  __int64 v9; // x0
  signed __int64 *v10; // x3
  signed __int64 v11; // x5
  uint8 *v12; // x6
  __int64 v13; // x7
  int v14; // w0
  __int64 v15; // x0
  uint8 *v19; // x3
  __int64 v20; // x4
  uint8 *v21; // x5
  __int64 v22; // x6
  _QWORD *r2; // x2
  _QWORD *v24; // x25
  git_teiron_inc_cn_services_backup_cloud_internal_model_Object *p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object; // x0
  __int64 v26; // x1
  uint8 *v27; // x2
  uint8 *v28; // x3
  uint8 **v29; // x25
  time_Location *v30; // x5
  uint8 *v31; // x6
  time_Location **v32; // x25
  __int64 v33; // x0
  __int64 v34; // x0
  __int64 v35; // x2
  __int64 v36; // x0
  unsigned int i; // w0
  __int64 v38; // x7
  __int64 v39; // x13
  __int64 v40; // x3
  __int64 v41; // x0
  __int64 v42; // x0
  __int64 v43; // x0
  __int64 v44; // x7
  __int64 r4; // x4
  _QWORD *v46; // x5
  _QWORD *v47; // x25
  __int64 v48; // x4
  _QWORD *v49; // x25
  __int64 v50; // x0
  __int64 v51; // x0
  __int64 v52; // x1
  __int64 v53; // x2
  unsigned int v54; // [xsp+6Ch] [xbp-1DCh]
  unsigned int v55; // [xsp+70h] [xbp-1D8h]
  unsigned int v56; // [xsp+70h] [xbp-1D8h]
  unsigned int v57; // [xsp+74h] [xbp-1D4h]
  __int64 v58; // [xsp+78h] [xbp-1D0h]
  __int64 v59; // [xsp+80h] [xbp-1C8h]
  __int64 v60; // [xsp+80h] [xbp-1C8h]
  __int64 v61; // [xsp+88h] [xbp-1C0h]
  __int64 v62; // [xsp+90h] [xbp-1B8h]
  __int64 v63; // [xsp+98h] [xbp-1B0h]
  __int64 v64; // [xsp+98h] [xbp-1B0h]
  __int64 v65; // [xsp+C0h] [xbp-188h]
  uint8 *r0; // [xsp+C8h] [xbp-180h]
  uint8 *v67; // [xsp+C8h] [xbp-180h]
  git_teiron_inc_cn_services_backup_cloud_internal_model_Object *v68; // [xsp+D0h] [xbp-178h]
  uint8 *v69; // [xsp+D8h] [xbp-170h]
  __int64 *v70; // [xsp+E0h] [xbp-168h]
  __int64 v71; // [xsp+E8h] [xbp-160h] BYREF
  __int64 v72; // [xsp+F0h] [xbp-158h]
  __int64 v73; // [xsp+F8h] [xbp-150h]
  __int64 v74; // [xsp+100h] [xbp-148h]
  __int64 r1; // [xsp+108h] [xbp-140h]
  __int64 v76; // [xsp+110h] [xbp-138h]
  __int64 v77; // [xsp+118h] [xbp-130h]
  __int64 v78; // [xsp+120h] [xbp-128h]
  __int64 v79; // [xsp+128h] [xbp-120h]
  __int64 v80; // [xsp+130h] [xbp-118h]
  __int64 v81; // [xsp+138h] [xbp-110h]
  _QWORD v82[4]; // [xsp+140h] [xbp-108h] BYREF
  RTYPE *v83; // [xsp+160h] [xbp-E8h]
  __int64 v84; // [xsp+168h] [xbp-E0h]
  git_teiron_inc_cn_services_backup_cloud_internal_driver_CloudError v85; // [xsp+170h] [xbp-D8h] BYREF
  time_Location *v86; // [xsp+188h] [xbp-C0h]
  RTYPE *v87; // [xsp+190h] [xbp-B8h] BYREF
  __int64 v88; // [xsp+198h] [xbp-B0h]
  __int64 v89; // [xsp+1A0h] [xbp-A8h]
  __int64 v90; // [xsp+1A8h] [xbp-A0h]
  RTYPE *v91; // [xsp+1B0h] [xbp-98h] BYREF
  __int64 v92; // [xsp+1B8h] [xbp-90h]
  RTYPE *v93; // [xsp+1C0h] [xbp-88h]
  __int64 v94; // [xsp+1C8h] [xbp-80h]
  RTYPE *v95; // [xsp+1D0h] [xbp-78h]
  __int64 v96; // [xsp+1D8h] [xbp-70h]
  __int64 v97; // [xsp+1E0h] [xbp-68h]
  __int64 v98; // [xsp+1E8h] [xbp-60h]
  RTYPE *v99; // [xsp+1F0h] [xbp-58h] BYREF
  __int64 v100; // [xsp+1F8h] [xbp-50h]
  __int64 v101; // [xsp+200h] [xbp-48h]
  __int64 v102; // [xsp+208h] [xbp-40h]
  __int64 v103; // [xsp+210h] [xbp-38h] BYREF
  __int64 v104; // [xsp+218h] [xbp-30h]
  RTYPE *v105; // [xsp+220h] [xbp-28h] BYREF
  __int64 v106; // [xsp+228h] [xbp-20h]
  RTYPE *v107; // [xsp+230h] [xbp-18h]
  __int64 v108; // [xsp+238h] [xbp-10h]
  retval_6E2590 v118; // 0:x0.16
  retval_4C5D80 v119; // 0:x0.16
  retval_6E2590 v120; // 0:x0.16
  retval_760588 v121; // 0:x0.16
  retval_4D1AB0 v122; // 0:x0.16
  retval_4D1AB0 v123; // 0:x0.16
  retval_760120 result; // 0:x0.24
  retval_74E690 v125; // 0:kr00_24.24
  retval_74E690 v126; // 0:kr48_24.24
  retval_75DB80 Driver; // 0:kr148_24.24
  retval_4C3660 v128; // 0:kr160_24.24
  retval_475E60 v129; // 0:kr20_40.40
  retval_475E60 v130; // 0:kr68_40.40
  retval_475E60 v131; // 0:krB0_40.40
  retval_475E60 v132; // 0:krF8_40.40
  retval_475E60 v133; // 0:kr120_40.40

  v83 = nullptr; /*0x76016c*/
  v84 = 0; /*0x76016c*/
  v82[0] = &RTYPE_string; /*0x760184*/
  v82[1] = runtime_convTstring(a2, a3); /*0x760188*/
  v82[2] = &RTYPE_string; /*0x7601a0*/
  v82[3] = runtime_convTstring(a4, a5); /*0x7601a4*/
  v9 = runtime_convTstring(*a6, a6[1]); /*0x7601b4*/
  v83 = &RTYPE_string; /*0x7601c0*/
  v84 = v9; /*0x7601c4*/
  fmt_Fprintf(off_9081A0, qword_BB97E8, "[CloudWorker]Download %v(%v) to %v\n", 35, v82); /*0x7601f0*/
  Driver = git_teiron_inc_cn_services_backup_cloud_worker__ptr_Worker_getDriver(a1); /*0x7601f8*/
  if ( Driver._r2 ) /*0x7601fc*/
  {
    memset(&v85, 0, sizeof(v85)); /*0x760200*/
    if ( (RTYPE **)Driver._r2 == off_908360 ) /*0x760214*/
    {
      v11 = *v10; /*0x760218*/
      v12 = (uint8 *)v10[1]; /*0x76021c*/
      v13 = v10[2]; /*0x760220*/
    }
    else
    {
      v11 = 0; /*0x760228*/
      v13 = 0; /*0x76022c*/
      v12 = nullptr; /*0x760230*/
    }
    v85.errno = v11; /*0x760234*/
    v85.problem.str = v12; /*0x760238*/
    v85.problem.len = v13; /*0x76023c*/
    if ( (RTYPE **)Driver._r2 != off_908360 ) /*0x760240*/
    {
      result._r0 = 84934737; /*0x760354*/
      result._r1 = Driver._r2; /*0x76035c*/
      result._r2 = v10; /*0x760360*/
      return result; /*0x76036c*/
    }
    if ( v11 > -6 ) /*0x760248*/
    {
      if ( v11 > 401 ) /*0x7602a0*/
      {
        switch ( v11 ) /*0x7602d4*/
        {
          case 31045LL: /*0x7602d4*/
            v14 = 84934746; /*0x7602d8*/
            goto LABEL_28; /*0x7602e0*/
          case 31062LL: /*0x7602d4*/
            v14 = 84934747; /*0x7602f0*/
            goto LABEL_28; /*0x7602f8*/
          case 31365LL: /*0x7602d4*/
            v14 = 10; /*0x760308*/
            goto LABEL_28; /*0x76030c*/
        }
      }
      else
      {
        if ( v11 == -3 ) /*0x7602a8*/
        {
          v14 = 84934738; /*0x7602ac*/
          goto LABEL_28; /*0x7602b4*/
        }
        if ( v11 == 401 ) /*0x7602bc*/
        {
          v14 = 84934746; /*0x7602c0*/
          goto LABEL_28; /*0x7602c8*/
        }
      }
    }
    else
    {
      if ( v11 > -8 ) /*0x760250*/
      {
        if ( v11 == -7 ) /*0x760280*/
          v14 = 84934747; /*0x760284*/
        else
          v14 = 84934746; /*0x760290*/
        goto LABEL_28; /*0x76028c*/
      }
      if ( v11 == -9 ) /*0x760258*/
      {
        v14 = 84934738; /*0x76025c*/
LABEL_28:
        v54 = v14; /*0x760318*/
        v85.errno = v11; /*0x76031c*/
        v85.problem.str = v12; /*0x760320*/
        v85.problem.len = v13; /*0x760324*/
        v15 = runtime_convT(&RTYPE_git_teiron_inc_cn_services_backup_cloud_internal_driver_CloudError, &v85); /*0x760334*/
        result._r1 = off_908360; /*0x760338*/
        result._r2 = v15; /*0x760340*/
        result._r0 = v54; /*0x760344*/
        return result; /*0x760350*/
      }
      if ( v11 == -8 ) /*0x76026c*/
      {
        v14 = 84934739; /*0x760270*/
        goto LABEL_28; /*0x760278*/
      }
    }
    v14 = 84934736; /*0x760310*/
    goto LABEL_28; /*0x760310*/
  }
  r1 = Driver._r1; /*0x760370*/
  if ( *(_WORD *)(a1 + 464) == 6 ) /*0x760388*/
  {
    v118 = git_teiron_inc_cn_services_backup_cloud_pkg_utils_FixAndCleanPath(a2, a3); /*0x7603a8*/
    r0 = (uint8 *)v118._r0; /*0x7603ac*/
    v59 = v118._r1; /*0x7603b0*/
    v119 = path_Base(); /*0x7603b4*/
    v19 = r0; /*0x7603b8*/
    v20 = v59; /*0x7603bc*/
    v21 = (uint8 *)v119._r0; /*0x7603c0*/
    v22 = v119._r1; /*0x7603c4*/
  }
  else
  {
    v19 = a2; /*0x76038c*/
    v20 = a3; /*0x760390*/
    v21 = nullptr; /*0x760394*/
    v22 = 0; /*0x760398*/
  }
  v61 = v22; /*0x7603c8*/
  v69 = v21; /*0x7603cc*/
  v60 = v20; /*0x7603d0*/
  v67 = v19; /*0x7603d4*/
  v120 = git_teiron_inc_cn_services_backup_cloud_pkg_utils_FixAndCleanPath(*a6, a6[1]); /*0x7603e4*/
  r2 = a6; /*0x7603e8*/
  a6[1] = v120._r1; /*0x7603ec*/
  if ( dword_BE34B0 ) /*0x7603f8*/
  {
    v129 = runtime_gcWriteBarrier2(v120._r0); /*0x7603fc*/
    v120._r0 = v129._r0; /*0xf1c0000000000004*/
    r2 = (_QWORD *)v129._r2; /*0xf1c000000000000c*/
    *v24 = v129._r0; /*0x760400*/
    v24[1] = *(_QWORD *)v129._r2; /*0x760408*/
  }
  *r2 = v120._r0; /*0x76040c*/
  v128 = time_Now(); /*0x760410*/
  v86 = (time_Location *)v128._r2; /*0x760418*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object = (git_teiron_inc_cn_services_backup_cloud_internal_model_Object *)runtime_newobject(&RTYPE_git_teiron_inc_cn_services_backup_cloud_internal_model_Object); /*0x760428*/
  v26 = v60; /*0x76042c*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Path.len = v60; /*0x760430*/
  if ( dword_BE34B0 ) /*0x76043c*/
  {
    v130 = runtime_gcWriteBarrier2(p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object); /*0x76044c*/
    p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object = (git_teiron_inc_cn_services_backup_cloud_internal_model_Object *)v130._r0; /*0xf1c0000000000018*/
    v26 = v130._r1; /*0xf1c000000000001c*/
    v27 = v67; /*0x760450*/
    *v29 = v67; /*0x760454*/
    v28 = v69; /*0x760458*/
    v29[1] = v69; /*0x76045c*/
  }
  else
  {
    v27 = v67; /*0x760440*/
    v28 = v69; /*0x760444*/
  }
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Path.str = v27; /*0x760460*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Name.len = v61; /*0x760468*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Name.str = v28; /*0x76046c*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Size = 0; /*0x760470*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Modified.wall = v128._r0; /*0x760478*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Modified.ext = v128._r1; /*0x760480*/
  if ( dword_BE34B0 ) /*0x76048c*/
  {
    v131 = runtime_gcWriteBarrier2(p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object); /*0x76049c*/
    p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object = (git_teiron_inc_cn_services_backup_cloud_internal_model_Object *)v131._r0; /*0xf1c000000000002c*/
    v26 = v131._r1; /*0xf1c0000000000030*/
    v27 = (uint8 *)v131._r2; /*0xf1c0000000000034*/
    v30 = v86; /*0x7604a0*/
    *v32 = v86; /*0x7604a4*/
    v31 = a4; /*0x7604a8*/
    v32[1] = (time_Location *)a4; /*0x7604ac*/
  }
  else
  {
    v30 = v86; /*0x760490*/
    v31 = a4; /*0x760494*/
  }
  v68 = p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object; /*0x7604b0*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->Modified.loc = v30; /*0x7604b4*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->IsFolder = 0; /*0x7604b8*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->ID.len = a5; /*0x7604c0*/
  p_git_teiron_inc_cn_services_backup_cloud_internal_model_Object->ID.str = v31; /*0x7604c4*/
  v105 = nullptr; /*0x7604cc*/
  v106 = 0; /*0x7604cc*/
  v107 = nullptr; /*0x7604d4*/
  v108 = 0; /*0x7604d4*/
  v33 = runtime_convTstring(v27, v26); /*0x7604dc*/
  v105 = &RTYPE_string; /*0x7604e8*/
  v106 = v33; /*0x7604ec*/
  v34 = runtime_convTstring(v69, v61); /*0x7604f8*/
  v107 = &RTYPE_string; /*0x760504*/
  v108 = v34; /*0x760508*/
  fmt_Fprintf(off_9081A0, qword_BB97E8, "[CloudWorker]Download object %v, %v\n", 36, &v105); /*0x760534*/
  v76 = 0; /*0x760538*/
  v77 = 0; /*0x760538*/
  v78 = 0; /*0x76053c*/
  v79 = 0; /*0x76053c*/
  v80 = 0; /*0x760540*/
  v81 = 0; /*0x760540*/
  v121 = ((retval_760588 (__golang *)(__int64, _QWORD, _QWORD, RTYPE **, git_teiron_inc_cn_services_backup_cloud_internal_model_Object *, _QWORD, _QWORD, _QWORD, _QWORD, _QWORD, _QWORD))*(_QWORD *)(Driver._r0 + 72LL))( /*0x760588*/
           r1,
           *(_QWORD *)(a1 + 352),
           *(_QWORD *)(a1 + 360),
           off_90E7F8,
           v68,
           0,
           0,
           0,
           0,
           0,
           0);
  if ( v121._r1 )
  {
    v63 = v121._r1; /*0x760644*/
    v103 = 0; /*0x76064c*/
    v104 = 0; /*0x76064c*/
    v38 = *(_QWORD *)(v121._r1 + 8LL); /*0x760654*/
    v74 = v35; /*0x760660*/
    v103 = v38; /*0x760664*/
    v104 = v35; /*0x760668*/
    fmt_Fprintf(
      off_9081A0,
      qword_BB97E8,
      "[CloudWorker]Failed to get target file download link, error: %v\n",
      64,
      &v103);
    result._r0 = 84934741; /*0x760698*/
    result._r1 = v63; /*0x7606a0*/
    result._r2 = v74; /*0x7606a4*/
  }
  else
  {
    v70 = (__int64 *)v121._r0; /*0x760590*/
    if ( !syscall_Faccessat(-100, *a6, a6[1], 0, 0) )
    {
      v122 = os_Remove(*a6, a6[1]); /*0x7605c0*/
      if ( v122._r0 )
      {
        v73 = v122._r1; /*0x7605c8*/
        v62 = v122._r0; /*0x7605cc*/
        v99 = nullptr; /*0x7605d0*/
        v100 = 0; /*0x7605d0*/
        v101 = 0; /*0x7605d4*/
        v102 = 0; /*0x7605d4*/
        v36 = runtime_convTstring(*a6, a6[1]); /*0x7605e4*/
        v99 = &RTYPE_string; /*0x7605f0*/
        v100 = v36; /*0x7605f4*/
        v101 = *(_QWORD *)(v62 + 8); /*0x760604*/
        v102 = v73; /*0x76060c*/
        fmt_Fprintf(off_9081A0, qword_BB97E8, "[CloudWorker]Failed to remove existing file %v: %v\n", 51, &v99);
      }
    }
    for ( i = 0; ; i = v56 ) /*0x76063c*/
    {
      v55 = i; /*0x760714*/
      v39 = a6[1]; /*0x760720*/
      v71 = *a6; /*0x760724*/
      v72 = v39; /*0x760728*/
      if ( *(_WORD *)(a1 + 464) == 6 ) /*0x76073c*/
      {
        v125 = git_teiron_inc_cn_services_backup_cloud_worker_download_Download( /*0x760774*/
                 *(_QWORD *)(a1 + 432),
                 *(_QWORD *)(a1 + 440),
                 *v70,
                 v70[1],
                 &v71,
                 (uint8 *)"pan.baidu.com",
                 13,
                 a7,
                 a8,
                 a9,
                 v70[2]);
        v51 = v125._r0; /*0xf1c0000000000040*/
        v52 = v125._r1; /*0xf1c0000000000044*/
        v53 = v125._r2; /*0xf1c0000000000048*/
      }
      else
      {
        v126 = git_teiron_inc_cn_services_backup_cloud_worker_download_Download( /*0x7607b4*/
                 0,
                 0,
                 *v70,
                 v70[1],
                 &v71,
                 (uint8 *)off_B714B0,
                 qword_B714B8,
                 a7,
                 a8,
                 a9,
                 v70[2]);
        v51 = v126._r0; /*0xf1c000000000004c*/
        v52 = v126._r1; /*0xf1c0000000000050*/
        v53 = v126._r2; /*0xf1c0000000000054*/
      }
      v57 = v51; /*0x7607b8*/
      v74 = v53; /*0x7607bc*/
      v64 = v52; /*0x7607c0*/
      if ( !(_DWORD)v51 || (_DWORD)v51 == 13 || (_DWORD)v51 == 16 || (_DWORD)v51 == 17 ) /*0x7607dc*/
        break; /*0x7607dc*/
      v40 = v55 + 1LL; /*0x7607e4*/
      if ( (unsigned int)v40 > 5 ) /*0x7607ec*/
      {
        r4 = v71; /*0x760874*/
        v46 = a6; /*0x760878*/
        a6[1] = v72; /*0x76087c*/
        if ( dword_BE34B0 ) /*0x760888*/
        {
          v132 = runtime_gcWriteBarrier2(v51); /*0x76088c*/
          v52 = v132._r1; /*0xf1c000000000005c*/
          r4 = v132._r4; /*0xf1c0000000000068*/
          *v47 = v132._r4; /*0x760890*/
          v47[1] = *v46; /*0x760898*/
        }
        *v46 = r4; /*0x76089c*/
        goto LABEL_64; /*0x7608a0*/
      }
      v56 = v55 + 1; /*0x7607f0*/
      v91 = nullptr; /*0x7607f4*/
      v92 = 0; /*0x7607f4*/
      v93 = nullptr; /*0x7607f8*/
      v94 = 0; /*0x7607f8*/
      v95 = nullptr; /*0x7607fc*/
      v96 = 0; /*0x7607fc*/
      v97 = 0; /*0x760800*/
      v98 = 0; /*0x760800*/
      v41 = runtime_convT32(v40); /*0x760808*/
      v91 = &RTYPE_uint32; /*0x760814*/
      v92 = v41; /*0x760818*/
      v42 = runtime_convTstring(*a6, a6[1]); /*0x76082c*/
      v93 = &RTYPE_string; /*0x760838*/
      v94 = v42; /*0x76083c*/
      v43 = runtime_convT32(v57); /*0x760844*/
      v95 = &RTYPE_uint32; /*0x760850*/
      v96 = v43; /*0x760854*/
      if ( v64 ) /*0x76085c*/
        v44 = *(_QWORD *)(v64 + 8); /*0x760860*/
      else
        v44 = 0; /*0x760868*/
      v97 = v44; /*0x7606b4*/
      v98 = v74; /*0x7606bc*/
      fmt_Fprintf(off_9081A0, qword_BB97E8, "[CloudWorker]Download retry %v/5 for %v, code:%v, err:%v\n", 57, &v91); /*0x760704*/
      time_Sleep(4000000000LL * v56); /*0x76070c*/
    }
    v48 = v71; /*0x7608a8*/
    v46 = a6; /*0x7608ac*/
    a6[1] = v72; /*0x7608b0*/
    if ( dword_BE34B0 ) /*0x7608bc*/
    {
      v133 = runtime_gcWriteBarrier2(v51); /*0x7608c0*/
      v52 = v133._r1; /*0xf1c0000000000070*/
      v48 = v133._r4; /*0xf1c000000000007c*/
      *v49 = v133._r4; /*0x7608c4*/
      v49[1] = *v46; /*0x7608cc*/
    }
    *v46 = v48; /*0x7608d0*/
LABEL_64:
    if ( v52 )
    {
      v123 = os_Remove(*v46, v46[1]); /*0x7608e0*/
      if ( v123._r0 )
      {
        v65 = v123._r1; /*0x7608e8*/
        v58 = v123._r0; /*0x7608ec*/
        v87 = nullptr; /*0x7608f0*/
        v88 = 0; /*0x7608f0*/
        v89 = 0; /*0x7608f4*/
        v90 = 0; /*0x7608f4*/
        v50 = runtime_convTstring(*a6, a6[1]); /*0x760904*/
        v87 = &RTYPE_string; /*0x760910*/
        v88 = v50; /*0x760914*/
        v89 = *(_QWORD *)(v58 + 8); /*0x760924*/
        v90 = v65; /*0x76092c*/
        fmt_Fprintf(off_9081A0, qword_BB97E8, "[CloudWorker]Failed to clean up failed download %v: %v\n", 55, &v87);
      }
      result._r0 = v57; /*0x76095c*/
      result._r1 = v64; /*0x760960*/
      result._r2 = v74; /*0x760964*/
    }
    else
    {
      result._r0 = 0; /*0x760974*/
      result._r1 = 0; /*0x760978*/
      result._r2 = 0; /*0x76097c*/
    }
  }
  return result; /*0x760350*/
}
```

## 8. `CopyBuffer`（`0x74e2a0`）：解密 Reader 的输出真正被写到文件

它本身没有加密算法，但正是它不断调用 Reader，并将 Reader 给出的 plaintext 写到已打开的 `os.File`。

```c
// git.teiron-inc.cn/services/backup-cloud/worker/download.CopyBuffer
retval_74E2A0 __golang git_teiron_inc_cn_services_backup_cloud_worker_download_CopyBuffer(
        __int64 a1,
        __int64 a2,
        __int64 a3,
        __int64 a4)
{
  __int64 v4; // x0
  __int64 r1; // x1
  __int64 v6; // x2
  __int64 v10; // [xsp+48h] [xbp-30h]
  __int64 v11; // [xsp+58h] [xbp-20h]
  __int64 v12; // [xsp+60h] [xbp-18h] BYREF
  __int64 v13; // [xsp+68h] [xbp-10h]
  retval_74E2A0 result; // 0:x0.24

  v4 = runtime_makeslice(&RTYPE_uint8); /*0x74e2e4*/
  r1 = io_CopyBuffer(off_9081A0, a1, a3, a4, v4)._r1; /*0x74e308*/
  if ( r1 )
  {
    v10 = r1; /*0x74e310*/
    v12 = 0; /*0x74e314*/
    v13 = 0; /*0x74e314*/
    v11 = v6; /*0x74e328*/
    v12 = *(_QWORD *)(r1 + 8); /*0x74e32c*/
    v13 = v6; /*0x74e330*/
    fmt_Fprintf(off_9081A0, qword_BB97E8, "[BackupCloud][Download]Download failed: %v\n", 43, &v12);
    if ( (errors_Is(v10, v11, off_B71530, off_B71538) & 1) != 0 /*0x74e39c*/
      || (errors_Is(v10, v11, off_B71540, off_B71548) & 1) != 0 )
    {
      result._r0 = 17; /*0x74e3a0*/
      result._r1 = v10; /*0x74e3a4*/
      result._r2 = v11; /*0x74e3a8*/
    }
    else
    {
      result._r0 = 11; /*0x74e3b8*/
      result._r1 = v10; /*0x74e3bc*/
      result._r2 = v11; /*0x74e3c0*/
    }
  }
  else
  {
    result._r0 = 0; /*0x74e3d0*/
    result._r1 = 0; /*0x74e3d4*/
    result._r2 = 0; /*0x74e3d8*/
  }
  return result; /*0x74e3b4*/
}
```

# 反编译容易误读的地方：用三段汇编校正

上面的完整反编译已经够用来跟数据流；汇编只在反编译可能造成误会的地方拿出来对照。没必要把整个函数的八百多条指令再贴一遍，读者真正需要确认的是长度如何解释、metadata 边界怎样计算，以及正文 reader 为什么不会把最后的 tag 当正文。

## 1. 首次读取确实是固定的 19 bytes

反编译里的 `io.ReadAtLeast` 参数受 Go ABI 影响不太直观。这里的 `MOV X1, #19`、随后传入三个 `19`，能直接确认首读范围就是 `magic + version + header_len + total_len`。

```asm
  724254: a0 03 00 d0   adrp    x0, 0x79a000
  724258: 00 00 21 91   add     x0, x0, #2112
  72425c: 61 02 80 d2   mov     x1, #19
  724260: e2 03 01 aa   mov     x2, x1
  724264: 73 2f f5 97   bl      0x470030
  724268: e0 3b 01 f9   str     x0, [sp, #624]
  72426c: e1 fb 41 f9   ldr     x1, [sp, #1008]
  724270: e2 03 00 aa   mov     x2, x0
  724274: 63 02 80 d2   mov     x3, #19
  724278: e4 03 03 aa   mov     x4, x3
  72427c: e5 03 03 aa   mov     x5, x3
  724280: e0 f7 41 f9   ldr     x0, [sp, #1000]
  724284: d7 11 f6 97   bl      0x4a89e0
```

## 2. `header_len` 和 metadata entry 都是大端，不是 Go 的小端内存解释

反编译里会把这段还原成 `__rev16`，看上去像普通函数调用。对应指令是 `REV16`；它先取 16-bit 字段，再把两个字节交换，之后才拿来计算剩余 header 长度或 entry 终点。

```asm
  724318: 64 68 64 78   ldrh    w4, [x3, x4]
  72431c: df 1c 00 f1   cmp     x6, #7
  724320: e9 60 00 54   b.ls    0x724f3c
  724324: 84 04 c0 5a   rev16   w4, w4
  724328: 84 3c 40 d3   ubfx    x4, x4, #0, #16
  72432c: e4 6f 00 f9   str     x4, [sp, #216]
  724330: 82 4c 00 d1   sub     x2, x4, #19

  724630: eb 03 0a cb   neg     x11, x10
  724634: 8b fc 8b 8a   and     x11, x4, x11, asr #63
  724638: 2b 68 6b 78   ldrh    w11, [x1, x11]
  72463c: 6b 05 c0 5a   rev16   w11, w11
  724640: 6b 3d 40 d3   ubfx    x11, x11, #0, #16
  724644: 6c 01 04 8b   add     x12, x11, x4
  724648: 7f 00 0c eb   cmp     x3, x12
```

前半段是 `header_len`，后半段是每个 metadata entry 的 `entry_len`。所以这两个字段都应按 big-endian 解析。

## 3. `total_len` 先翻转字节序，再从中扣掉头和 tag 边界

`total_len` 是 64 位大端字段。反编译里那个 `bswap64` 很容易被忽略，但指令级证据就是 `REV X7, X7`。后面两次 `SUB` 分别生成解密 Reader 记录的长度和正文边界；这就是为什么恢复器不能把最后 16 bytes tag 送进 CTR。

```asm
  724d70: e6 6f 40 f9   ldr     x6, [sp, #216]
  724d74: a5 00 06 8b   add     x5, x5, x6
  724d78: e7 cf 40 f9   ldr     x7, [sp, #408]
  724d7c: e7 0c c0 da   rev     x7, x7
  724d80: e7 cf 00 f9   str     x7, [sp, #408]
  724d84: e6 00 06 cb   sub     x6, x7, x6
  724d88: e6 bf 00 f9   str     x6, [sp, #376]
  724d8c: e5 00 05 cb   sub     x5, x7, x5
  724d90: e5 af 00 f9   str     x5, [sp, #344]
  724d94: c0 05 00 90   adrp    x0, 0x7dc000
  724d98: 00 80 32 91   add     x0, x0, #3232
  724d9c: d1 a4 f3 97   bl      0x40e0e0
```

这一段只用于确认字节序和长度边界。实际 tag 的读取与比较仍发生在 `DecryptSliceReader.Read`：`0x725268` 读取 16 bytes，`0x725514` 比较累计 HMAC 的前 16 bytes。

# 结束语

这次把 FOT 拆开以后，真正需要注意的不是 AES-CTR 本身，而是每层数据的边界和认证顺序。

一个对象从下载流到最终文件，中间至少经过了：固定头长度判断、metadata 边界判断、口令快速检查、文件名认证、正文密文 HMAC、CTR 流解密、尾部 tag 比较和长度检查。漏一步都可能造成“看起来恢复成功”的错觉。

如果后面碰到新版本，建议先别急着拿偏移照抄。先找 `.fot` 分支、19-byte 头读取、三组 PBKDF2、`usability`/`filename`、CTR/HMAC 和最后 16-byte tag，再沿着本文的下载、Reader、CopyBuffer 和尾标签验证顺序逐项对上。地址会变，Go 的局部变量名也会变，但这条数据流没有那么容易一起变掉。
