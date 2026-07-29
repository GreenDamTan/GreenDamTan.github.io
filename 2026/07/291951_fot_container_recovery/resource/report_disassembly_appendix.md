# `fot_parse_header_and_create_decrypt_reader`（`0x724220`）反汇编附录

## 函数身份与完整数据流位置

本报告中的分析名 `fot_parse_header_and_create_decrypt_reader` 指向 `0x724220`；归档的 IDA 汇编保留的原始 Go 符号名是 `git.teiron_inc.cn_services_backup_cloud_crypto.NewDecryptSliceReader`。它不是网络下载入口或写盘例程，而是 `.fot` 入站内容从 HTTP 响应体切换为可验证明文流的格式解析、密钥验证和解密读取器构造点。

完整入站及写盘链路可由已保存的 IDA C 反编译串联如下：`CloudWorker` 在 `0x760774` 或 `0x7607b4` 调用 `worker/download.Download`；成功的 HTTP 响应在 `0x74f0cc/0x74f0d4` 取出 `Response.Body` 接口；当资源名以 `.fot` 结尾且启用相应选项时，`0x74f31c` 调用 `crypto.NewDecryptReader`。后者在 `0x72410c` 调用本函数，构造出的 `DecryptSliceReader` 由读取路径消费密文、解密正文并验证末尾认证标签。下载例程随后从解密对象取得文件名，在 `0x74f49c` 经 `path/filepath.Join` 生成目标路径，于 `0x74f4d0` 通过 `os.OpenFile(..., 577, 438)` 创建或截断目标文件，并在 `0x74f5d0` 用 `CopyBuffer` 将该 reader 的明文写入文件。

`DecryptSliceReader.Read` 的保存反编译还显示：它分块读取受限密文、将密文写入 HMAC、经 AES-CTR 生成明文；正文读尽后读取 16 字节尾部标签并与计算值逐字节 XOR 聚合比较。因此本函数负责建立“哪一段是密文、用哪套密钥/CTR/HMAC 处理、文件名为何”的上下文，实际流式认证发生在其读取器中。

## 证据范围与提取规则

已保存的 IDA asm 导出属于 `0x724220` 这一函数，但其 `"lines"` 字段在 `0x724a38` 截断；因此下方第一个 `asm` 代码块保留的是**归档的 IDA 指令前段**，而不是将它误称为完整函数汇编。完整 IDA C 反编译仍覆盖整个函数，见附录 A。

为满足“整个函数反汇编”的审计要求，本附录随后另附一份从同一原始 ELF `backup_cloud` 的地址范围 `0x724220–0x724fa0` 直接导出的**完整 AArch64 机器指令 listing**。它由本机 `llvm-objdump` 读取原始 ELF 的 `.text` 节生成，起止地址与 IDA 函数边界一致；二者可按地址逐行对照。由于二进制缺少传统 ELF 函数符号，该工具显示的外层符号标题可能是相邻符号，但每一条指令均以实际虚拟地址为准。

第一个代码块严格从 `.archived_listing_420.txt` 的 JSON 风格字段 `"lines": "...` 提取，终止位置是其后紧接的 `"stack_frame"`。仅把 JSON 外层的 `\n` 还原为实际换行；没有反编译、补写、重排指令、改动地址或符号，也没有对其他转义作解码。

## 按地址范围的中文注释

### `0x724220–0x724288`：读取固定前导、magic 检查入口

函数先分配 `0x13`（19）字节缓冲区，并以 `io.ReadAtLeast` 读取输入流的最小固定前导。`0x72428c–0x7242b4` 把这 19 字节与全局 `byte_B73520` 所指的 magic 进行长度检查和 `runtime.memequal` 比较；读取失败直接返回空结果，magic 不匹配则转入错误/诊断路径，而不是继续把任意响应体解释为 FOT。

### `0x7242b8–0x72436c`：magic 后的版本/标识与大端 header length

magic 通过后，`0x7242c8–0x7242dc` 读取 magic 后指定偏移处的单字节并与 `byte_B859E2` 比较，可视为格式版本或固定标识检查。`0x7242e0–0x72432c` 在经过 Go slice 边界保护后读取 16 位字段，关键的 `REV16 W4, W4` 位于 `0x724324`：这明确将头长度按大端序转换。转换值保存于 `var_308`，并减去固定 19 字节前导得到需要继续读取的字节数；`0x724348–0x72436c` 再次调用 `io.ReadAtLeast` 读取该剩余头部。

### `0x724374–0x724440`：拼接完整头部并初始化 metadata 容器

该段按总 header length 扩展最初的 19 字节 slice（必要时 `runtime.growslice`），随后在 `0x7243b0–0x7243c4` 将后续头数据 `memmove` 到前导之后。`0x7243cc–0x7243d8` 要求容量至少覆盖 `0x23` 和 `0x2c` 两个访问边界；`0x7243dc–0x724418` 初始化字符串 map/迭代状态并取随机哈希种子，最后带着起始偏移 `0x2d` 跳入 metadata 循环。

### `0x724474–0x7245b8`：非 FOT/magic 失败的诊断分支

如果最初 magic 不匹配，`0x724474` 调用 `io.ReadAll` 收集剩余输入，`0x7244a4–0x724598` 打印失败和完整十六进制日志，随后返回 `sInvalidMagic...` 对应错误。该路径用于报告格式错误；不是解密或写盘路径。

### `0x724600–0x7247fc`：metadata 循环、`filename` 与 `usability`

每轮先在 `0x724630–0x724640` 以 `LDRH` 加 `REV16` 取大端记录长度，再在 `0x72465c` 读取一个键名长度字节。`0x7246ac–0x724718` 将键和值的字节片段转换为 Go string，并依靠多组边界比较拒绝越界、零长度或结构不完整的记录。

* `0x724728–0x724758` 比较 8 字节常量 `0x656D616E656C6966`，即小端装载后的 `filename`；命中时保留该值而不写入通用 metadata map。
* `0x72475c–0x7247a0` 比较 9 字节 `usability`（前 8 字节常量 `0x74696C6962617375`，第 9 字节 `y`）；命中时同样单独保留。
* 其他键值在 `0x7247a8–0x7247e8` 通过 `runtime.mapassign_faststr` 存入 map。循环回跳在 `0x7247ec–0x7247fc` 恢复游标和临时状态。

### `0x724800–0x724998`：`usability` 的 Base64、PBKDF2 100 与已知明文验证

循环结束后，`0x724800–0x724820` 定位头内偏移 `0x23` 后的 9 字节材料，并在存在 `usability` 时 Base64 解码。长度至少 16 字节后，`0x724850–0x7248c0` 将这 9 字节附加到全局缓冲区作为 PBKDF2 salt 的组成部分。`0x7248e0–0x7248f0` 设置迭代次数 `0x64`（100）和输出长度 `0x20`（32），调用 `golang.org_x_crypto_pbkdf2.Key`；随后 `0x7248f4–0x72495c` 建立 AES cipher 和 CTR，并解密解码内容中 IV 之后的数据。`0x724960–0x724984` 将结果与静态字符串 `teirenfeiniuyunpanbeifencryptoencrypto` 比较。这是基于 `usability` 的低迭代次数口令/格式可用性预检，而非正文的最终 HMAC 认证。

### `0x72499c–0x724c74`：`filename` 的 PBKDF2 1000、AES-CTR 与 HMAC 验证

该逻辑的完整机器指令出现在后方“完整 ELF 反汇编”代码块中。若存在 `filename`，逻辑 Base64 解码该字段、去掉 16 字节 IV、使用同一 9 字节 salt 组成调用 PBKDF2，迭代次数为 1000（调用点 `0x724ab0`），再以 AES-CTR 解密（`0x724ad4`）。它以由该派生密钥创建的 HMAC 计算标签（`0x724b38–0x724b74`），并在 `0x724c28–0x724c50` 比较前 16 字节认证值；验证通过后在 `0x724c60–0x724c78` 将解密出的文件名转换为 string。该文件名随后成为下载路径拼接的输入。

### `0x724c80–0x724e8c`：PBKDF2 10000、正文 decryptor/HMAC 与 `io.LimitedReader`

完整机器指令同样见后方完整 ELF 反汇编。`0x724d18` 使用迭代次数 10000、输出 32 字节调用 PBKDF2；`0x724d28–0x724d64` 创建 AES-CTR decryptor 和 HMAC。`0x724d80` 对头中的 64 位字段作字节序转换，`0x724d9c–0x724dd4` 分配 `io.LimitedReader`，把原始输入 reader 装入其中并令 `N = 已转换长度 - headerLength`。这使正文读取被限制在声明的密文范围，尾部认证标签不会被当成 CTR 正文读取。

`0x724de0–0x724e8c` 再分配 `DecryptSliceReader`，保存 LimitedReader、CTR decryptor、HMAC、剩余长度、解密文件名及总/明文长度等字段。读取实现的 C 反编译在 `0x725268` 读取 16 字节尾标记、在 `0x7253b0` 向 HMAC 写入每个密文块、在 `0x725514` 做 XOR 聚合比较，因此这些构造字段直接决定后续“解密后写盘”前的认证行为。

## 归档原始 AArch64 listing（仅还原换行）

```asm
git.teiron_inc.cn_services_backup_cloud_crypto.NewDecryptSliceReader (.text @ 0x724220):
724220  LDR             X16, [X28,#0x10]
724224  SUB             X17, SP, #0x360
724228  CMP             X17, X16
72422c  B.LS            loc_724F64
724230  SUB             X20, SP, #0x3E0
724234  STP             X29, X30, [X20,#-8]
724238  MOV             SP, X20
72423c  SUB             X29, SP, #8
724240  STR             X4, [SP,#0x3E0+arg_28]
724244  STR             X3, [SP,#0x3E0+arg_20]
724248  STR             X2, [SP,#0x3E0+arg_18]
72424c  STR             X1, [SP,#0x3E0+arg_10]
724250  STR             X0, [SP,#0x3E0+arg_8]
724254  ADRL            X0, RTYPE_uint8
72425c  MOV             X1, #0x13
724260  MOV             X2, X1
724264  BL              runtime.makeslice
724268  STR             X0, [SP,#0x3E0+var_170]
72426c  LDR             X1, [SP,#0x3E0+arg_10]
724270  MOV             X2, X0
724274  MOV             X3, #0x13
724278  MOV             X4, X3
72427c  MOV             X5, X3
724280  LDR             X0, [SP,#0x3E0+arg_8]
724284  BL              io.ReadAtLeast
724288  CBNZ            X1, loc_7245BC
72428c  ADRP            X27, #(byte_B73520+8)@PAGE
724290  LDR             X2, [X27,#(byte_B73520+8)@PAGEOFF]
724294  CMP             X2, #0x13
724298  B.LE            loc_7242A4
72429c  MOV             X0, XZR
7242a0  B               loc_7242B4
7242a4  ADRP            X27, #byte_B73520@PAGE
7242a8  LDR             X1, [X27,#byte_B73520@PAGEOFF]; unk_B85CF0
7242ac  LDR             X0, [SP,#0x3E0+var_170]
7242b0  BL              runtime.memequal
7242b4  TBZ             W0, #0, loc_724474
7242b8  ADRP            X27, #(byte_B73520+8)@PAGE
7242bc  LDR             X0, [X27,#(byte_B73520+8)@PAGEOFF]
7242c0  CMP             X0, #0x13
7242c4  B.CS            loc_724F58
7242c8  LDR             X3, [SP,#0x3E0+var_170]
7242cc  LDRB            W4, [X3,X0]
7242d0  ADRP            X27, #byte_B859E2@PAGE
7242d4  LDRB            W5, [X27,#byte_B859E2@PAGEOFF]
7242d8  CMP             W4, W5
7242dc  B.NE            loc_724454
7242e0  ADD             X1, X0, #3
7242e4  CMP             X1, #0x13
7242e8  B.HI            loc_724F50
7242ec  ADD             X4, X0, #1
7242f0  CMP             X1, X4
7242f4  B.CC            loc_724F48
7242f8  MOV             X5, #0x12
7242fc  SUB             X5, X5, X0
724300  NEG             X5, X5
724304  AND             X4, X4, X5,ASR#63
724308  MOV             X5, #0x10
72430c  SUB             X6, X5, X0
724310  NEG             X7, X6
724314  AND             X7, X1, X7,ASR#63
724318  LDRH            W4, [X3,X4]
72431c  CMP             X6, #7
724320  B.LS            loc_724F3C
724324  REV16           W4, W4
724328  UBFX            X4, X4, #0, #0x10
72432c  STR             X4, [SP,#0x3E0+var_308]
724330  SUB             X2, X4, #0x13
724334  STR             X2, [SP,#0x3E0+var_338]
724338  LDR             X5, [X3,X7]
72433c  STR             X5, [SP,#0x3E0+var_248]
724340  ADRL            X0, RTYPE_uint8
724348  MOV             X1, X2
72434c  BL              runtime.makeslice
724350  STR             X0, [SP,#0x3E0+var_178]
724354  LDR             X1, [SP,#0x3E0+arg_10]
724358  MOV             X2, X0
72435c  LDR             X3, [SP,#0x3E0+var_338]
724360  MOV             X4, X3
724364  MOV             X5, X3
724368  LDR             X0, [SP,#0x3E0+arg_8]
72436c  BL              io.ReadAtLeast
724370  CBNZ            X1, loc_724444
724374  LDR             X1, [SP,#0x3E0+var_308]
724378  CMP             X1, #0x13
72437c  B.HI            loc_72438C
724380  MOV             X2, #0x13
724384  LDR             X0, [SP,#0x3E0+var_170]
724388  B               loc_7243A4
72438c  LDR             X0, [SP,#0x3E0+var_170]
724390  MOV             X2, #0x13
724394  LDR             X3, [SP,#0x3E0+var_338]
724398  ADRL            X4, RTYPE_uint8
7243a0  BL              runtime.growslice
7243a4  STR             X2, [SP,#0x3E0+var_2C8]
7243a8  STR             X1, [SP,#0x3E0+var_2D0]
7243ac  STR             X0, [SP,#0x3E0+var_1D0]
7243b0  ADD             X3, X0, #0x13
7243b4  STR             X3, [SP,#0x3E0+var_180]
7243b8  MOV             X0, X3
7243bc  LDR             X1, [SP,#0x3E0+var_178]
7243c0  LDR             X2, [SP,#0x3E0+var_338]
7243c4  BL              runtime.memmove
7243c8  LDR             X2, [SP,#0x3E0+var_2C8]
7243cc  CMP             X2, #0x23 ; '#'
7243d0  B.CC            loc_724F34
7243d4  CMP             X2, #0x2C ; ','
7243d8  B.CC            loc_724F2C
7243dc  ADD             X27, SP, #0x3E0+var_58
7243e0  STP             XZR, XZR, [X27]
7243e4  ADD             X27, SP, #0x3E0+var_48
7243e8  STP             XZR, XZR, [X27]
7243ec  ADD             X27, SP, #0x3E0+var_38
7243f0  STP             XZR, XZR, [X27]
7243f4  ADD             X20, SP, #0x3E0+var_168
7243f8  ADR             X27, loc_724408
7243fc  STP             X29, X27, [SP,#0x3E0+var_3F8]
724400  SUB             X29, SP, #0x18
724404  BL              loc_4762BC
724408  SUB             X29, SP, #8
72440c  ADD             X0, SP, #0x3E0+var_168
724410  STR             X0, [SP,#0x3E0+var_48]
724414  BL              runtime.rand32
724418  STR             W0, [SP,#0x3E0+var_4C]
72441c  LDR             X0, [SP,#0x3E0+var_2C8]
724420  LDR             X1, [SP,#0x3E0+var_1D0]
724424  LDR             X2, [SP,#0x3E0+var_2D0]
724428  LDR             X3, [SP,#0x3E0+var_308]
72442c  MOV             X4, #0x2D ; '-'
724430  MOV             X5, XZR
724434  MOV             X6, XZR
724438  MOV             X7, XZR
72443c  MOV             X8, XZR
724440  B               loc_724600
724444  MOV             X0, XZR
724448  LDP             X29, X30, [SP,#0x3E0+var_3E8]
72444c  ADD             SP, SP, #0x3E0
724450  RET
724454  ADRP            X27, #off_B714D0@PAGE
724458  LDR             X1, [X27,#off_B714D0@PAGEOFF]; off_908240
72445c  ADRP            X27, #off_B714D8@PAGE
724460  LDR             X2, [X27,#off_B714D8@PAGEOFF]; unk_B71570
724464  MOV             X0, XZR
724468  LDP             X29, X30, [SP,#0x3E0+var_3E8]
72446c  ADD             SP, SP, #0x3E0
724470  RET
724474  LDR             X0, [SP,#0x3E0+arg_8]
724478  LDR             X1, [SP,#0x3E0+arg_10]
72447c  BL              io.ReadAll
724480  STR             X0, [SP,#0x3E0+var_200]
724484  STR             X1, [SP,#0x3E0+var_330]
724488  CBZ             X3, loc_7244D8
72448c  ADD             X27, SP, #0x3E0+var_18
724490  STP             XZR, XZR, [X27]
724494  CBZ             X3, loc_72449C
724498  LDR             X3, [X3,#8]
72449c  STR             X3, [SP,#0x3E0+var_18]
7244a0  STR             X4, [SP,#0x3E0+var_10]
7244a4  ADRP            X27, #qword_BB97E8@PAGE
7244a8  LDR             X1, [X27,#qword_BB97E8@PAGEOFF]
7244ac  ADRL            X0, off_9081A0
7244b4  ADRL            X2, aNewdecryptslic_0; \"[NewDecryptSliceReader]Failed to read r\"...
7244bc  MOV             X3, #0x39 ; '9'
7244c0  ADD             X4, SP, #0x3E0+var_18
7244c4  MOV             X5, #1
7244c8  MOV             X6, X5
7244cc  BL              fmt.Fprintf
7244d0  LDR             X0, [SP,#0x3E0+var_200]
7244d4  LDR             X1, [SP,#0x3E0+var_330]
7244d8  ADD             X5, X1, #0x13
7244dc  CMP             X5, #0x13
7244e0  B.HI            loc_7244F0
7244e4  LDR             X2, [SP,#0x3E0+var_170]
7244e8  MOV             X3, #0x13
7244ec  B               loc_724520
7244f0  LDR             X0, [SP,#0x3E0+var_170]
7244f4  MOV             X2, #0x13
7244f8  MOV             X3, X1
7244fc  ADRL            X4, RTYPE_uint8
724504  MOV             X1, X5
724508  BL              runtime.growslice
72450c  MOV             X3, X2
724510  MOV             X5, X1
724514  LDR             X1, [SP,#0x3E0+var_330]
724518  MOV             X2, X0
72451c  LDR             X0, [SP,#0x3E0+var_200]
724520  STR             X3, [SP,#0x3E0+var_2B8]
724524  STR             X5, [SP,#0x3E0+var_2C0]
724528  STR             X2, [SP,#0x3E0+var_1C8]
72452c  ADD             X4, X2, #0x13
724530  MOV             X6, X0
724534  MOV             X0, X4
724538  MOV             X7, X1
72453c  MOV             X1, X6
724540  MOV             X2, X7
724544  BL              runtime.memmove
724548  ADD             X27, SP, #0x3E0+var_28
72454c  STP             XZR, XZR, [X27]
724550  LDR             X0, [SP,#0x3E0+var_1C8]
724554  LDR             X1, [SP,#0x3E0+var_2C0]
724558  LDR             X2, [SP,#0x3E0+var_2B8]
72455c  BL              runtime.convTslice
724560  ADRL            X3, RTYPE__slice_uint8
724568  STR             X3, [SP,#0x3E0+var_28]
72456c  STR             X0, [SP,#0x3E0+var_20]
724570  ADRP            X27, #qword_BB97E8@PAGE
724574  LDR             X1, [X27,#qword_BB97E8@PAGEOFF]
724578  ADRL            X0, off_9081A0
724580  ADRL            X2, aNewdecryptslic; \"[NewDecryptSliceReader] full hex: % X\
\"
724588  MOV             X3, #0x26 ; '&'
72458c  ADD             X4, SP, #0x3E0+var_28
724590  MOV             X5, #1
724594  MOV             X6, X5
724598  BL              fmt.Fprintf
72459c  ADRP            X27, #off_B714C0@PAGE
7245a0  LDR             X1, [X27,#off_B714C0@PAGEOFF]; off_908240
7245a4  ADRP            X27, #off_B714C8@PAGE
7245a8  LDR             X2, [X27,#off_B714C8@PAGEOFF]; sInvalidMagicCo
7245ac  MOV             X0, XZR
7245b0  LDP             X29, X30, [SP,#0x3E0+var_3E8]
7245b4  ADD             SP, SP, #0x3E0
7245b8  RET
7245bc  MOV             X0, XZR
7245c0  LDP             X29, X30, [SP,#0x3E0+var_3E8]
7245c4  ADD             SP, SP, #0x3E0
7245c8  RET
7245cc  LDR             X9, [SP,#0x3E0+var_2C8]
7245d0  LDR             X10, [SP,#0x3E0+var_1D0]
7245d4  LDR             X11, [SP,#0x3E0+var_2D0]
7245d8  LDR             X12, [SP,#0x3E0+var_308]
7245dc  LDR             X4, [SP,#0x3E0+var_260]
7245e0  MOV             X5, X2
7245e4  MOV             X6, X3
7245e8  MOV             X7, X1
7245ec  MOV             X8, X0
7245f0  MOV             X0, X9
7245f4  MOV             X1, X10
7245f8  MOV             X2, X11
7245fc  MOV             X3, X12
724600  STR             X7, [SP,#0x3E0+var_2B0]
724604  STR             X8, [SP,#0x3E0+var_1C0]
724608  CMP             X4, X3
72460c  B.GE            loc_724800
724610  ADD             X9, X4, #2
724614  CMP             X3, X9
724618  B.LT            loc_724800
72461c  CMP             X0, X9
724620  B.CC            loc_724F20
724624  CMP             X4, X9
724628  B.HI            loc_724F14
72462c  SUB             X10, X0, X4
724630  NEG             X11, X10
724634  AND             X11, X4, X11,ASR#63
724638  LDRH            W11, [X1,X11]
72463c  REV16           W11, W11
724640  UBFX            X11, X11, #0, #0x10
724644  ADD             X12, X11, X4
724648  CMP             X3, X12
72464c  B.LT            loc_724800
724650  CBZ             X11, loc_724800
724654  CMP             X2, X9
724658  B.LS            loc_724F08
72465c  LDRB            W9, [X1,X9]
724660  CMP             X11, X9
724664  B.LE            loc_724800
724668  ADD             X13, X9, X4
72466c  ADD             X14, X13, #3
724670  CMP             X0, X14
724674  B.CC            loc_724EFC
724678  ADD             X4, X4, #3
72467c  CMP             X14, X4
724680  B.CC            loc_724EF0
724684  STR             X11, [SP,#0x3E0+var_2A0]
724688  STR             X9, [SP,#0x3E0+var_320]
72468c  STR             X14, [SP,#0x3E0+var_250]
724690  STR             X6, [SP,#0x3E0+var_230]
724694  STR             X5, [SP,#0x3E0+var_368]
724698  STR             X13, [SP,#0x3E0+var_258]
72469c  STR             X12, [SP,#0x3E0+var_260]
7246a0  SUB             X3, X10, #3
7246a4  NEG             X3, X3
7246a8  AND             X3, X4, X3,ASR#63
7246ac  ADD             X3, X1, X3
7246b0  MOV             X0, XZR
7246b4  MOV             X2, X9
7246b8  MOV             X1, X3
7246bc  BL              runtime.slicebytetostring
7246c0  LDR             X2, [SP,#0x3E0+var_2C8]
7246c4  LDR             X3, [SP,#0x3E0+var_260]
7246c8  CMP             X2, X3
7246cc  B.CC            loc_724EE8
7246d0  LDR             X4, [SP,#0x3E0+var_250]
7246d4  CMP             X4, X3
7246d8  B.HI            loc_724EDC
7246dc  STR             X1, [SP,#0x3E0+var_318]
7246e0  STR             X0, [SP,#0x3E0+var_1F0]
7246e4  LDR             X3, [SP,#0x3E0+var_258]
7246e8  SUB             X3, X2, X3
7246ec  LDR             X5, [SP,#0x3E0+var_2A0]
7246f0  LDR             X6, [SP,#0x3E0+var_320]
7246f4  SUB             X5, X5, X6
7246f8  SUB             X5, X5, #3
7246fc  SUB             X3, X3, #3
724700  NEG             X3, X3
724704  AND             X3, X4, X3,ASR#63
724708  LDR             X4, [SP,#0x3E0+var_1D0]
72470c  ADD             X1, X4, X3
724710  MOV             X0, XZR
724714  MOV             X2, X5
724718  BL              runtime.slicebytetostring
72471c  STR             X0, [SP,#0x3E0+var_188]
724720  STR             X1, [SP,#0x3E0+var_250]
724724  LDR             X3, [SP,#0x3E0+var_318]
724728  CMP             X3, #8
72472c  B.NE            loc_72475C
724730  LDR             X2, [SP,#0x3E0+var_1F0]
724734  LDR             X5, [X2]
724738  MOV             X6, #0x656D616E656C6966
724748  CMP             X5, X6
72474c  B.NE            loc_7247A8
724750  LDR             X2, [SP,#0x3E0+var_368]
724754  LDR             X3, [SP,#0x3E0+var_230]
724758  B               loc_7245CC
72475c  CMP             X3, #9
724760  B.NE            loc_7247A4
724764  LDR             X2, [SP,#0x3E0+var_1F0]
724768  LDR             X5, [X2]
72476c  MOV             X6, #0x74696C6962617375
72477c  CMP             X5, X6
724780  B.NE            loc_7247A8
724784  LDRB            W5, [X2,#8]
724788  CMP             W5, #0x79 ; 'y'
72478c  B.NE            loc_7247A8
724790  MOV             X2, X1
724794  MOV             X3, X0
724798  LDR             X1, [SP,#0x3E0+var_2B0]
72479c  LDR             X0, [SP,#0x3E0+var_1C0]
7247a0  B               loc_7245CC
7247a4  LDR             X2, [SP,#0x3E0+var_1F0]
7247a8  ADRL            X0, RTYPE_map_string_string
7247b0  ADD             X1, SP, #0x3E0+var_58
7247b4  BL              runtime.mapassign_faststr
7247b8  LDR             X4, [SP,#0x3E0+var_250]
7247bc  STR             X4, [X0,#8]
7247c0  ADRP            X27, #dword_BE34B0@PAGE
7247c4  LDR             W4, [X27,#dword_BE34B0@PAGEOFF]
7247c8  CBNZ            W4, loc_7247D4
7247cc  LDR             X5, [SP,#0x3E0+var_188]
7247d0  B               loc_7247E8
7247d4  BL              runtime.gcWriteBarrier2
7247d8  LDR             X5, [SP,#0x3E0+var_188]
7247dc  STR             X5, [X25]
7247e0  LDR             X6, [X0]
7247e4  STR             X6, [X25,#8]
7247e8  STR             X5, [X0]
7247ec  LDR             X2, [SP,#0x3E0+var_368]
7247f0  LDR             X3, [SP,#0x3E0+var_230]
7247f4  LDR             X1, [SP,#0x3E0+var_2B0]
7247f8  LDR             X0, [SP,#0x3E0+var_1C0]
7247fc  B               loc_7245CC
724800  ADD             X1, X1, #0x23 ; '#'
724804  STR             X1, [SP,#0x3E0+var_208]
724808  CBZ             X5, loc_72499C
72480c  ADRP            X27, #qword_BB9A38@PAGE
724810  LDR             X0, [X27,#qword_BB9A38@PAGEOFF]
724814  MOV             X1, X6
724818  MOV             X2, X5
72481c  BL              encoding_base64._ptr_Encoding.DecodeString
724820  CBNZ            X3, loc_724C00
724824  CMP             X1, #0x10
724828  B.LT            loc_724BE0
72482c  STR             X2, [SP,#0x3E0+var_358]
724830  STR             X1, [SP,#0x3E0+var_360]
724834  STR             X0, [SP,#0x3E0+var_228]
724838  SUB             X5, X2, #0x10
72483c  STR             X5, [SP,#0x3E0+var_270]
724840  NEG             X6, X5
724844  ASR             X6, X6, #0x3F ; '?'
724848  AND             X6, X6, #0x10
72484c  ADRP            X27, #qword_B73550@PAGE
724850  LDR             X7, [X27,#qword_B73550@PAGEOFF]
724854  ADRP            X27, #qword_B73548@PAGE
724858  LDR             X8, [X27,#qword_B73548@PAGEOFF]
72485c  ADD             X9, X8, #9
724860  ADD             X6, X6, X0
724864  STR             X6, [SP,#0x3E0+var_198]
724868  ADRP            X27, #off_B73540@PAGE
72486c  LDR             X10, [X27,#off_B73540@PAGEOFF]; unk_B85C24
724870  CMP             X7, X9
724874  B.CS            loc_7248A8
724878  STR             X8, [SP,#0x3E0+var_250]
72487c  MOV             X0, X10
724880  MOV             X1, X9
724884  MOV             X2, X7
724888  MOV             X3, #9
72488c  ADRL            X4, RTYPE_uint8
724894  BL              runtime.growslice
724898  LDR             X8, [SP,#0x3E0+var_250]
72489c  MOV             X10, X0
7248a0  MOV             X9, X1
7248a4  MOV             X7, X2
7248a8  STR             X9, [SP,#0x3E0+var_2F0]
7248ac  STR             X10, [SP,#0x3E0+var_1D8]
7248b0  STR             X7, [SP,#0x3E0+var_2D8]
7248b4  ADD             X0, X10, X8
7248b8  LDR             X1, [SP,#0x3E0+var_208]
7248bc  MOV             X2, #9
7248c0  BL              runtime.memmove
7248c4  NOP
7248c8  LDR             X0, [SP,#0x3E0+arg_18]
7248cc  LDR             X1, [SP,#0x3E0+arg_20]
7248d0  LDR             X2, [SP,#0x3E0+arg_28]
7248d4  LDR             X3, [SP,#0x3E0+var_1D8]
7248d8  LDR             X4, [SP,#0x3E0+var_2F0]
7248dc  LDR             X5, [SP,#0x3E0+var_2D8]
7248e0  MOV             X6, #0x64 ; 'd'
7248e4  MOV             X7, #0x20 ; ' '
7248e8  ADRL            X8, off_890CC8
7248f0  BL              golang.org_x_crypto_pbkdf2.Key
7248f4  BL              crypto_aes.NewCipher
7248f8  CBNZ            X2, loc_724BC8
7248fc  LDR             X2, [SP,#0x3E0+var_228]
724900  MOV             X3, #0x10
724904  LDR             X4, [SP,#0x3E0+var_358]
724908  BL              crypto_cipher.NewCTR
72490c  STR             X0, [SP,#0x3E0+var_340]
724910  STR             X1, [SP,#0x3E0+var_210]
724914  LDR             X5, [SP,#0x3E0+var_360]
724918  SUB             X2, X5, #0x10
72491c  STR             X2, [SP,#0x3E0+var_278]
724920  ADRL            X0, RTYPE_uint8
724928  MOV             X1, X2
72492c  BL              runtime.makeslice
724930  STR             X0, [SP,#0x3E0+var_170]
724934  LDR             X5, [SP,#0x3E0+var_340]
724938  LDR             X5, [X5,#0x18]
72493c  MOV             X1, X0
724940  LDR             X2, [SP,#0x3E0+var_278]
724944  MOV             X3, X2
724948  LDR             X4, [SP,#0x3E0+var_198]
72494c  LDR             X6, [SP,#0x3E0+var_270]
724950  LDR             X0, [SP,#0x3E0+var_210]
724954  MOV             X7, X5
724958  MOV             X5, X2
72495c  BLR             X7
724960  ADRP            X27, #qword_B73568@PAGE
724964  LDR             X5, [X27,#qword_B73568@PAGEOFF]
724968  LDR             X2, [SP,#0x3E0+var_278]
72496c  CMP             X5, X2
724970  B.NE            loc_724BA8
724974  ADRP            X27, #off_B73560@PAGE; \"teirenfeiniuyunpanbeifencryptoencrypto\"
724978  LDR             X1, [X27,#off_B73560@PAGEOFF]; \"teirenfeiniuyunpanbeifencryptoencrypto\"
72497c  LDR             X0, [SP,#0x3E0+var_170]
724980  BL              runtime.memequal
724984  TBZ             W0, #0, loc_724BA8
724988  LDR             X0, [SP,#0x3E0+var_2C8]
72498c  LDR             X1, [SP,#0x3E0+var_208]
724990  LDR             X3, [SP,#0x3E0+var_308]
724994  LDR             X7, [SP,#0x3E0+var_2B0]
724998  LDR             X8, [SP,#0x3E0+var_1C0]
72499c  SUB             X5, X0, #0x13
7249a0  STR             X5, [SP,#0x3E0+var_310]
7249a4  CBNZ            X7, loc_7249B4
7249a8  MOV             X0, XZR
7249ac  MOV             X2, XZR
7249b0  B               loc_724C74
7249b4  ADRP            X27, #qword_BB9A38@PAGE
7249b8  LDR             X0, [X27,#qword_BB9A38@PAGEOFF]
7249bc  MOV             X1, X8
7249c0  MOV             X2, X7
7249c4  BL              encoding_base64._ptr_Encoding.DecodeString
7249c8  CBNZ            X3, loc_7249D4
7249cc  CMP             X1, #0x10
7249d0  B.GE            loc_7249F4
7249d4  ADRP            X27, #off_B71510@PAGE
7249d8  LDR             X1, [X27,#off_B71510@PAGEOFF]; off_908240
7249dc  ADRP            X27, #off_B71518@PAGE
7249e0  LDR             X2, [X27,#off_B71518@PAGEOFF]; unk_B715B0
7249e4  MOV             X0, XZR
7249e8  LDP             X29, X30, [SP,#0x3E0+var_3E8]
7249ec  ADD             SP, SP, #0x3E0
7249f0  RET
7249f4  STR             X2, [SP,#0x3E0+var_290]
7249f8  STR             X0, [SP,#0x3E0+var_1A0]
7249fc  SUB             X5, X1, #0x10
724a00  STR             X5, [SP,#0x3E0+var_280]
724a04  SUB             X6, X2, X1
724a08  ADD             X6, X6, #0x10
724a0c  NEG             X6, X6
724a10  AND             X6, X5, X6,ASR#63
724a14  ADRP            X27, #qword_B73550@PAGE
724a18  LDR             X7, [X27,#qword_B73550@PAGEOFF]
724a1c  ADRP            X27, #qword_B73548@PAGE
724a20  LDR             X8, [X27,#qword_B73548@PAGEOFF]
724a24  ADD             X1, X8, #9
724a28  ADD             X6, X0, X6
724a2c  STR             X6, [SP,#0x3E0+var_1B0]
724a30  ADRP            X27, #off_B73540@PAGE
724a34  LDR             X9, [X27,#off_B73540@PAGEOFF]; unk_B85C24
724a38  CMP             X7, X1
```

## 同一原始 ELF 的完整 AArch64 机器指令范围

下面的 listing 使用 `llvm-objdump -d --arch-name=aarch64 --start-address=0x724220 --stop-address=0x724fa0 backup_cloud` 从原始样本直接导出，完整覆盖 IDA 中 `fot_parse_header_and_create_decrypt_reader` 的函数范围。它补足前一节 IDA 归档在 `0x724a38` 后的截断；反编译语义、IDA 名称和代码交叉引用仍以附录 A 及本报告主体为准。

```asm

/mnt/d/tmp/backup_cloud/backup_cloud:	file format elf64-littleaarch64

Disassembly of section .text:

0000000000574600 <crosscall2>:
  724220: 90 0b 40 f9  	ldr	x16, [x28, #16]
  724224: f1 83 0d d1  	sub	x17, sp, #864
  724228: 3f 02 10 eb  	cmp	x17, x16
  72422c: c9 69 00 54  	b.ls	0x724f64 <crosscall2+0x1b0964>
  724230: f4 83 0f d1  	sub	x20, sp, #992
  724234: 9d fa 3f a9  	stp	x29, x30, [x20, #-8]
  724238: 9f 02 00 91  	mov	sp, x20
  72423c: fd 23 00 d1  	sub	x29, sp, #8
  724240: e4 07 02 f9  	str	x4, [sp, #1032]
  724244: e3 03 02 f9  	str	x3, [sp, #1024]
  724248: e2 ff 01 f9  	str	x2, [sp, #1016]
  72424c: e1 fb 01 f9  	str	x1, [sp, #1008]
  724250: e0 f7 01 f9  	str	x0, [sp, #1000]
  724254: a0 03 00 d0  	adrp	x0, 0x79a000 <crosscall2+0x1afe2c>
  724258: 00 00 21 91  	add	x0, x0, #2112
  72425c: 61 02 80 d2  	mov	x1, #19
  724260: e2 03 01 aa  	mov	x2, x1
  724264: 73 2f f5 97  	bl	0x470030 <.text+0x6e1b0>
  724268: e0 3b 01 f9  	str	x0, [sp, #624]
  72426c: e1 fb 41 f9  	ldr	x1, [sp, #1008]
  724270: e2 03 00 aa  	mov	x2, x0
  724274: 63 02 80 d2  	mov	x3, #19
  724278: e4 03 03 aa  	mov	x4, x3
  72427c: e5 03 03 aa  	mov	x5, x3
  724280: e0 f7 41 f9  	ldr	x0, [sp, #1000]
  724284: d7 11 f6 97  	bl	0x4a89e0 <_cgo_topofstack+0x32c30>
  724288: a1 19 00 b5  	cbnz	x1, 0x7245bc <crosscall2+0x1affbc>
  72428c: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b0dc8>
  724290: 62 97 42 f9  	ldr	x2, [x27, #1320]
  724294: 5f 4c 00 f1  	cmp	x2, #19
  724298: 6d 00 00 54  	b.le	0x7242a4 <crosscall2+0x1afca4>
  72429c: e0 03 1f aa  	mov	x0, xzr
  7242a0: 05 00 00 14  	b	0x7242b4 <crosscall2+0x1afcb4>
  7242a4: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b0de0>
  7242a8: 61 93 42 f9  	ldr	x1, [x27, #1312]
  7242ac: e0 3b 41 f9  	ldr	x0, [sp, #624]
  7242b0: 2c 7e f3 97  	bl	0x403b60 <.text+0x1ce0>
  7242b4: 00 0e 00 36  	tbz	w0, #0, 0x724474 <crosscall2+0x1afe74>
  7242b8: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b0df4>
  7242bc: 60 97 42 f9  	ldr	x0, [x27, #1320]
  7242c0: 1f 4c 00 f1  	cmp	x0, #19
  7242c4: a2 64 00 54  	b.hs	0x724f58 <crosscall2+0x1b0958>
  7242c8: e3 3b 41 f9  	ldr	x3, [sp, #624]
  7242cc: 64 68 60 38  	ldrb	w4, [x3, x0]
  7242d0: 1b 23 00 b0  	adrp	x27, 0xb85000 <crosscall2+0x1b0e54>
  7242d4: 65 8b 67 39  	ldrb	w5, [x27, #2530]
  7242d8: 9f 00 05 6b  	cmp	w4, w5
  7242dc: c1 0b 00 54  	b.ne	0x724454 <crosscall2+0x1afe54>
  7242e0: 01 0c 00 91  	add	x1, x0, #3
  7242e4: 3f 4c 00 f1  	cmp	x1, #19
  7242e8: 48 63 00 54  	b.hi	0x724f50 <crosscall2+0x1b0950>
  7242ec: 04 04 00 91  	add	x4, x0, #1
  7242f0: 3f 00 04 eb  	cmp	x1, x4
  7242f4: a3 62 00 54  	b.lo	0x724f48 <crosscall2+0x1b0948>
  7242f8: 45 02 80 d2  	mov	x5, #18
  7242fc: a5 00 00 cb  	sub	x5, x5, x0
  724300: e5 03 05 cb  	neg	x5, x5
  724304: 84 fc 85 8a  	and	x4, x4, x5, asr #63
  724308: e5 03 7c b2  	orr	x5, xzr, #0x10
  72430c: a6 00 00 cb  	sub	x6, x5, x0
  724310: e7 03 06 cb  	neg	x7, x6
  724314: 27 fc 87 8a  	and	x7, x1, x7, asr #63
  724318: 64 68 64 78  	ldrh	w4, [x3, x4]
  72431c: df 1c 00 f1  	cmp	x6, #7
  724320: e9 60 00 54  	b.ls	0x724f3c <crosscall2+0x1b093c>
  724324: 84 04 c0 5a  	rev16	w4, w4
  724328: 84 3c 40 d3  	ubfx	x4, x4, #0, #16
  72432c: e4 6f 00 f9  	str	x4, [sp, #216]
  724330: 82 4c 00 d1  	sub	x2, x4, #19
  724334: e2 57 00 f9  	str	x2, [sp, #168]
  724338: 65 68 67 f8  	ldr	x5, [x3, x7]
  72433c: e5 cf 00 f9  	str	x5, [sp, #408]
  724340: a0 03 00 d0  	adrp	x0, 0x79a000 <crosscall2+0x1aff18>
  724344: 00 00 21 91  	add	x0, x0, #2112
  724348: e1 03 02 aa  	mov	x1, x2
  72434c: 39 2f f5 97  	bl	0x470030 <.text+0x6e1b0>
  724350: e0 37 01 f9  	str	x0, [sp, #616]
  724354: e1 fb 41 f9  	ldr	x1, [sp, #1008]
  724358: e2 03 00 aa  	mov	x2, x0
  72435c: e3 57 40 f9  	ldr	x3, [sp, #168]
  724360: e4 03 03 aa  	mov	x4, x3
  724364: e5 03 03 aa  	mov	x5, x3
  724368: e0 f7 41 f9  	ldr	x0, [sp, #1000]
  72436c: 9d 11 f6 97  	bl	0x4a89e0 <_cgo_topofstack+0x32c30>
  724370: a1 06 00 b5  	cbnz	x1, 0x724444 <crosscall2+0x1afe44>
  724374: e1 6f 40 f9  	ldr	x1, [sp, #216]
  724378: 3f 4c 00 f1  	cmp	x1, #19
  72437c: 88 00 00 54  	b.hi	0x72438c <crosscall2+0x1afd8c>
  724380: 62 02 80 d2  	mov	x2, #19
  724384: e0 3b 41 f9  	ldr	x0, [sp, #624]
  724388: 07 00 00 14  	b	0x7243a4 <crosscall2+0x1afda4>
  72438c: e0 3b 41 f9  	ldr	x0, [sp, #624]
  724390: 62 02 80 d2  	mov	x2, #19
  724394: e3 57 40 f9  	ldr	x3, [sp, #168]
  724398: a4 03 00 d0  	adrp	x4, 0x79a000 <crosscall2+0x1aff70>
  72439c: 84 00 21 91  	add	x4, x4, #2112
  7243a0: 58 2f f5 97  	bl	0x470100 <.text+0x6e280>
  7243a4: e2 8f 00 f9  	str	x2, [sp, #280]
  7243a8: e1 8b 00 f9  	str	x1, [sp, #272]
  7243ac: e0 0b 01 f9  	str	x0, [sp, #528]
  7243b0: 03 4c 00 91  	add	x3, x0, #19
  7243b4: e3 33 01 f9  	str	x3, [sp, #608]
  7243b8: e0 03 03 aa  	mov	x0, x3
  7243bc: e1 37 41 f9  	ldr	x1, [sp, #616]
  7243c0: e2 57 40 f9  	ldr	x2, [sp, #168]
  7243c4: bf 48 f5 97  	bl	0x4766c0 <_cgo_topofstack+0x910>
  7243c8: e2 8f 40 f9  	ldr	x2, [sp, #280]
  7243cc: 5f 8c 00 f1  	cmp	x2, #35
  7243d0: 23 5b 00 54  	b.lo	0x724f34 <crosscall2+0x1b0934>
  7243d4: 5f b0 00 f1  	cmp	x2, #44
  7243d8: a3 5a 00 54  	b.lo	0x724f2c <crosscall2+0x1b092c>
  7243dc: fb 23 0e 91  	add	x27, sp, #904
  7243e0: 7f 7f 00 a9  	stp	xzr, xzr, [x27]
  7243e4: fb 63 0e 91  	add	x27, sp, #920
  7243e8: 7f 7f 00 a9  	stp	xzr, xzr, [x27]
  7243ec: fb a3 0e 91  	add	x27, sp, #936
  7243f0: 7f 7f 00 a9  	stp	xzr, xzr, [x27]
  7243f4: f4 e3 09 91  	add	x20, sp, #632
  7243f8: 9b 00 00 10  	adr	x27, #16
  7243fc: fd ef 3e a9  	stp	x29, x27, [sp, #-24]
  724400: fd 63 00 d1  	sub	x29, sp, #24
  724404: ae 47 f5 97  	bl	0x4762bc <_cgo_topofstack+0x50c>
  724408: fd 23 00 d1  	sub	x29, sp, #8
  72440c: e0 e3 09 91  	add	x0, sp, #632
  724410: e0 cf 01 f9  	str	x0, [sp, #920]
  724414: 6b 97 f4 97  	bl	0x44a1c0 <.text+0x48340>
  724418: e0 97 03 b9  	str	w0, [sp, #916]
  72441c: e0 8f 40 f9  	ldr	x0, [sp, #280]
  724420: e1 0b 41 f9  	ldr	x1, [sp, #528]
  724424: e2 8b 40 f9  	ldr	x2, [sp, #272]
  724428: e3 6f 40 f9  	ldr	x3, [sp, #216]
  72442c: a4 05 80 d2  	mov	x4, #45
  724430: e5 03 1f aa  	mov	x5, xzr
  724434: e6 03 1f aa  	mov	x6, xzr
  724438: e7 03 1f aa  	mov	x7, xzr
  72443c: e8 03 1f aa  	mov	x8, xzr
  724440: 70 00 00 14  	b	0x724600 <crosscall2+0x1b0000>
  724444: e0 03 1f aa  	mov	x0, xzr
  724448: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  72444c: ff 83 0f 91  	add	sp, sp, #992
  724450: c0 03 5f d6  	ret
  724454: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b0f88>
  724458: 61 6b 42 f9  	ldr	x1, [x27, #1232]
  72445c: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b0f90>
  724460: 62 6f 42 f9  	ldr	x2, [x27, #1240]
  724464: e0 03 1f aa  	mov	x0, xzr
  724468: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  72446c: ff 83 0f 91  	add	sp, sp, #992
  724470: c0 03 5f d6  	ret
  724474: e0 f7 41 f9  	ldr	x0, [sp, #1000]
  724478: e1 fb 41 f9  	ldr	x1, [sp, #1008]
  72447c: 99 14 f6 97  	bl	0x4a96e0 <_cgo_topofstack+0x33930>
  724480: e0 f3 00 f9  	str	x0, [sp, #480]
  724484: e1 5b 00 f9  	str	x1, [sp, #176]
  724488: 83 02 00 b4  	cbz	x3, 0x7244d8 <crosscall2+0x1afed8>
  72448c: fb 23 0f 91  	add	x27, sp, #968
  724490: 7f 7f 00 a9  	stp	xzr, xzr, [x27]
  724494: 43 00 00 b4  	cbz	x3, 0x72449c <crosscall2+0x1afe9c>
  724498: 63 04 40 f9  	ldr	x3, [x3, #8]
  72449c: e3 e7 01 f9  	str	x3, [sp, #968]
  7244a0: e4 eb 01 f9  	str	x4, [sp, #976]
  7244a4: bb 24 00 b0  	adrp	x27, 0xbb9000 <crosscall2+0x1b10f8>
  7244a8: 61 f7 43 f9  	ldr	x1, [x27, #2024]
  7244ac: 20 0f 00 90  	adrp	x0, 0x908000 <crosscall2+0x1b063c>
  7244b0: 00 80 06 91  	add	x0, x0, #416
  7244b4: c2 09 00 90  	adrp	x2, 0x85c000 <crosscall2+0x1b0394>
  7244b8: 42 4c 31 91  	add	x2, x2, #3155
  7244bc: 23 07 80 d2  	mov	x3, #57
  7244c0: e4 23 0f 91  	add	x4, sp, #968
  7244c4: e5 03 40 b2  	orr	x5, xzr, #0x1
  7244c8: e6 03 05 aa  	mov	x6, x5
  7244cc: ad d4 f6 97  	bl	0x4d9780 <_cgo_topofstack+0x639d0>
  7244d0: e0 f3 40 f9  	ldr	x0, [sp, #480]
  7244d4: e1 5b 40 f9  	ldr	x1, [sp, #176]
  7244d8: 25 4c 00 91  	add	x5, x1, #19
  7244dc: bf 4c 00 f1  	cmp	x5, #19
  7244e0: 88 00 00 54  	b.hi	0x7244f0 <crosscall2+0x1afef0>
  7244e4: e2 3b 41 f9  	ldr	x2, [sp, #624]
  7244e8: 63 02 80 d2  	mov	x3, #19
  7244ec: 0d 00 00 14  	b	0x724520 <crosscall2+0x1aff20>
  7244f0: e0 3b 41 f9  	ldr	x0, [sp, #624]
  7244f4: 62 02 80 d2  	mov	x2, #19
  7244f8: e3 03 01 aa  	mov	x3, x1
  7244fc: a4 03 00 d0  	adrp	x4, 0x79a000 <crosscall2+0x1b00d4>
  724500: 84 00 21 91  	add	x4, x4, #2112
  724504: e1 03 05 aa  	mov	x1, x5
  724508: fe 2e f5 97  	bl	0x470100 <.text+0x6e280>
  72450c: e3 03 02 aa  	mov	x3, x2
  724510: e5 03 01 aa  	mov	x5, x1
  724514: e1 5b 40 f9  	ldr	x1, [sp, #176]
  724518: e2 03 00 aa  	mov	x2, x0
  72451c: e0 f3 40 f9  	ldr	x0, [sp, #480]
  724520: e3 97 00 f9  	str	x3, [sp, #296]
  724524: e5 93 00 f9  	str	x5, [sp, #288]
  724528: e2 0f 01 f9  	str	x2, [sp, #536]
  72452c: 44 4c 00 91  	add	x4, x2, #19
  724530: e6 03 00 aa  	mov	x6, x0
  724534: e0 03 04 aa  	mov	x0, x4
  724538: e7 03 01 aa  	mov	x7, x1
  72453c: e1 03 06 aa  	mov	x1, x6
  724540: e2 03 07 aa  	mov	x2, x7
  724544: 5f 48 f5 97  	bl	0x4766c0 <_cgo_topofstack+0x910>
  724548: fb e3 0e 91  	add	x27, sp, #952
  72454c: 7f 7f 00 a9  	stp	xzr, xzr, [x27]
  724550: e0 0f 41 f9  	ldr	x0, [sp, #536]
  724554: e1 93 40 f9  	ldr	x1, [sp, #288]
  724558: e2 97 40 f9  	ldr	x2, [sp, #296]
  72455c: 31 10 f5 97  	bl	0x468620 <.text+0x667a0>
  724560: 63 03 00 d0  	adrp	x3, 0x792000 <crosscall2+0x1b0118>
  724564: 63 80 3d 91  	add	x3, x3, #3936
  724568: e3 df 01 f9  	str	x3, [sp, #952]
  72456c: e0 e3 01 f9  	str	x0, [sp, #960]
  724570: bb 24 00 b0  	adrp	x27, 0xbb9000 <crosscall2+0x1b11c4>
  724574: 61 f7 43 f9  	ldr	x1, [x27, #2024]
  724578: 20 0f 00 90  	adrp	x0, 0x908000 <crosscall2+0x1b0708>
  72457c: 00 80 06 91  	add	x0, x0, #416
  724580: 82 09 00 90  	adrp	x2, 0x854000 <crosscall2+0x1b0440>
  724584: 42 50 1b 91  	add	x2, x2, #1748
  724588: c3 04 80 d2  	mov	x3, #38
  72458c: e4 e3 0e 91  	add	x4, sp, #952
  724590: e5 03 40 b2  	orr	x5, xzr, #0x1
  724594: e6 03 05 aa  	mov	x6, x5
  724598: 7a d4 f6 97  	bl	0x4d9780 <_cgo_topofstack+0x639d0>
  72459c: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b10d0>
  7245a0: 61 63 42 f9  	ldr	x1, [x27, #1216]
  7245a4: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b10d8>
  7245a8: 62 67 42 f9  	ldr	x2, [x27, #1224]
  7245ac: e0 03 1f aa  	mov	x0, xzr
  7245b0: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  7245b4: ff 83 0f 91  	add	sp, sp, #992
  7245b8: c0 03 5f d6  	ret
  7245bc: e0 03 1f aa  	mov	x0, xzr
  7245c0: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  7245c4: ff 83 0f 91  	add	sp, sp, #992
  7245c8: c0 03 5f d6  	ret
  7245cc: e9 8f 40 f9  	ldr	x9, [sp, #280]
  7245d0: ea 0b 41 f9  	ldr	x10, [sp, #528]
  7245d4: eb 8b 40 f9  	ldr	x11, [sp, #272]
  7245d8: ec 6f 40 f9  	ldr	x12, [sp, #216]
  7245dc: e4 c3 40 f9  	ldr	x4, [sp, #384]
  7245e0: e5 03 02 aa  	mov	x5, x2
  7245e4: e6 03 03 aa  	mov	x6, x3
  7245e8: e7 03 01 aa  	mov	x7, x1
  7245ec: e8 03 00 aa  	mov	x8, x0
  7245f0: e0 03 09 aa  	mov	x0, x9
  7245f4: e1 03 0a aa  	mov	x1, x10
  7245f8: e2 03 0b aa  	mov	x2, x11
  7245fc: e3 03 0c aa  	mov	x3, x12
  724600: e7 9b 00 f9  	str	x7, [sp, #304]
  724604: e8 13 01 f9  	str	x8, [sp, #544]
  724608: 9f 00 03 eb  	cmp	x4, x3
  72460c: aa 0f 00 54  	b.ge	0x724800 <crosscall2+0x1b0200>
  724610: 89 08 00 91  	add	x9, x4, #2
  724614: 7f 00 09 eb  	cmp	x3, x9
  724618: 4b 0f 00 54  	b.lt	0x724800 <crosscall2+0x1b0200>
  72461c: 1f 00 09 eb  	cmp	x0, x9
  724620: 03 48 00 54  	b.lo	0x724f20 <crosscall2+0x1b0920>
  724624: 9f 00 09 eb  	cmp	x4, x9
  724628: 68 47 00 54  	b.hi	0x724f14 <crosscall2+0x1b0914>
  72462c: 0a 00 04 cb  	sub	x10, x0, x4
  724630: eb 03 0a cb  	neg	x11, x10
  724634: 8b fc 8b 8a  	and	x11, x4, x11, asr #63
  724638: 2b 68 6b 78  	ldrh	w11, [x1, x11]
  72463c: 6b 05 c0 5a  	rev16	w11, w11
  724640: 6b 3d 40 d3  	ubfx	x11, x11, #0, #16
  724644: 6c 01 04 8b  	add	x12, x11, x4
  724648: 7f 00 0c eb  	cmp	x3, x12
  72464c: ab 0d 00 54  	b.lt	0x724800 <crosscall2+0x1b0200>
  724650: 8b 0d 00 b4  	cbz	x11, 0x724800 <crosscall2+0x1b0200>
  724654: 5f 00 09 eb  	cmp	x2, x9
  724658: 89 45 00 54  	b.ls	0x724f08 <crosscall2+0x1b0908>
  72465c: 29 68 69 38  	ldrb	w9, [x1, x9]
  724660: 7f 01 09 eb  	cmp	x11, x9
  724664: ed 0c 00 54  	b.le	0x724800 <crosscall2+0x1b0200>
  724668: 2d 01 04 8b  	add	x13, x9, x4
  72466c: ae 0d 00 91  	add	x14, x13, #3
  724670: 1f 00 0e eb  	cmp	x0, x14
  724674: 43 44 00 54  	b.lo	0x724efc <crosscall2+0x1b08fc>
  724678: 84 0c 00 91  	add	x4, x4, #3
  72467c: df 01 04 eb  	cmp	x14, x4
  724680: 83 43 00 54  	b.lo	0x724ef0 <crosscall2+0x1b08f0>
  724684: eb a3 00 f9  	str	x11, [sp, #320]
  724688: e9 63 00 f9  	str	x9, [sp, #192]
  72468c: ee cb 00 f9  	str	x14, [sp, #400]
  724690: e6 db 00 f9  	str	x6, [sp, #432]
  724694: e5 3f 00 f9  	str	x5, [sp, #120]
  724698: ed c7 00 f9  	str	x13, [sp, #392]
  72469c: ec c3 00 f9  	str	x12, [sp, #384]
  7246a0: 43 0d 00 d1  	sub	x3, x10, #3
  7246a4: e3 03 03 cb  	neg	x3, x3
  7246a8: 83 fc 83 8a  	and	x3, x4, x3, asr #63
  7246ac: 23 00 03 8b  	add	x3, x1, x3
  7246b0: e0 03 1f aa  	mov	x0, xzr
  7246b4: e2 03 09 aa  	mov	x2, x9
  7246b8: e1 03 03 aa  	mov	x1, x3
  7246bc: d5 30 f5 97  	bl	0x470a10 <.text+0x6eb90>
  7246c0: e2 8f 40 f9  	ldr	x2, [sp, #280]
  7246c4: e3 c3 40 f9  	ldr	x3, [sp, #384]
  7246c8: 5f 00 03 eb  	cmp	x2, x3
  7246cc: e3 40 00 54  	b.lo	0x724ee8 <crosscall2+0x1b08e8>
  7246d0: e4 cb 40 f9  	ldr	x4, [sp, #400]
  7246d4: 9f 00 03 eb  	cmp	x4, x3
  7246d8: 28 40 00 54  	b.hi	0x724edc <crosscall2+0x1b08dc>
  7246dc: e1 67 00 f9  	str	x1, [sp, #200]
  7246e0: e0 fb 00 f9  	str	x0, [sp, #496]
  7246e4: e3 c7 40 f9  	ldr	x3, [sp, #392]
  7246e8: 43 00 03 cb  	sub	x3, x2, x3
  7246ec: e5 a3 40 f9  	ldr	x5, [sp, #320]
  7246f0: e6 63 40 f9  	ldr	x6, [sp, #192]
  7246f4: a5 00 06 cb  	sub	x5, x5, x6
  7246f8: a5 0c 00 d1  	sub	x5, x5, #3
  7246fc: 63 0c 00 d1  	sub	x3, x3, #3
  724700: e3 03 03 cb  	neg	x3, x3
  724704: 83 fc 83 8a  	and	x3, x4, x3, asr #63
  724708: e4 0b 41 f9  	ldr	x4, [sp, #528]
  72470c: 81 00 03 8b  	add	x1, x4, x3
  724710: e0 03 1f aa  	mov	x0, xzr
  724714: e2 03 05 aa  	mov	x2, x5
  724718: be 30 f5 97  	bl	0x470a10 <.text+0x6eb90>
  72471c: e0 2f 01 f9  	str	x0, [sp, #600]
  724720: e1 cb 00 f9  	str	x1, [sp, #400]
  724724: e3 67 40 f9  	ldr	x3, [sp, #200]
  724728: 7f 20 00 f1  	cmp	x3, #8
  72472c: 81 01 00 54  	b.ne	0x72475c <crosscall2+0x1b015c>
  724730: e2 fb 40 f9  	ldr	x2, [sp, #496]
  724734: 45 00 40 f9  	ldr	x5, [x2]
  724738: c6 2c 8d d2  	mov	x6, #26982
  72473c: 86 ad ac f2  	movk	x6, #25964, lsl #16
  724740: c6 2d cc f2  	movk	x6, #24942, lsl #32
  724744: a6 ad ec f2  	movk	x6, #25965, lsl #48
  724748: bf 00 06 eb  	cmp	x5, x6
  72474c: e1 02 00 54  	b.ne	0x7247a8 <crosscall2+0x1b01a8>
  724750: e2 3f 40 f9  	ldr	x2, [sp, #120]
  724754: e3 db 40 f9  	ldr	x3, [sp, #432]
  724758: 9d ff ff 17  	b	0x7245cc <crosscall2+0x1affcc>
  72475c: 7f 24 00 f1  	cmp	x3, #9
  724760: 21 02 00 54  	b.ne	0x7247a4 <crosscall2+0x1b01a4>
  724764: e2 fb 40 f9  	ldr	x2, [sp, #496]
  724768: 45 00 40 f9  	ldr	x5, [x2]
  72476c: a6 6e 8e d2  	mov	x6, #29557
  724770: 26 4c ac f2  	movk	x6, #25185, lsl #16
  724774: 26 8d cd f2  	movk	x6, #27753, lsl #32
  724778: 26 8d ee f2  	movk	x6, #29801, lsl #48
  72477c: bf 00 06 eb  	cmp	x5, x6
  724780: 41 01 00 54  	b.ne	0x7247a8 <crosscall2+0x1b01a8>
  724784: 45 20 40 39  	ldrb	w5, [x2, #8]
  724788: bf e4 01 71  	cmp	w5, #121
  72478c: e1 00 00 54  	b.ne	0x7247a8 <crosscall2+0x1b01a8>
  724790: e2 03 01 aa  	mov	x2, x1
  724794: e3 03 00 aa  	mov	x3, x0
  724798: e1 9b 40 f9  	ldr	x1, [sp, #304]
  72479c: e0 13 41 f9  	ldr	x0, [sp, #544]
  7247a0: 8b ff ff 17  	b	0x7245cc <crosscall2+0x1affcc>
  7247a4: e2 fb 40 f9  	ldr	x2, [sp, #496]
  7247a8: a0 04 00 90  	adrp	x0, 0x7b8000 <crosscall2+0x1b03f8>
  7247ac: 00 00 05 91  	add	x0, x0, #320
  7247b0: e1 23 0e 91  	add	x1, sp, #904
  7247b4: 67 1e f5 97  	bl	0x46c150 <.text+0x6a2d0>
  7247b8: e4 cb 40 f9  	ldr	x4, [sp, #400]
  7247bc: 04 04 00 f9  	str	x4, [x0, #8]
  7247c0: fb 25 00 f0  	adrp	x27, 0xbe3000 <crosscall2+0x1b14bc>
  7247c4: 64 b3 44 b9  	ldr	w4, [x27, #1200]
  7247c8: 64 00 00 35  	cbnz	w4, 0x7247d4 <crosscall2+0x1b01d4>
  7247cc: e5 2f 41 f9  	ldr	x5, [sp, #600]
  7247d0: 06 00 00 14  	b	0x7247e8 <crosscall2+0x1b01e8>
  7247d4: a3 45 f5 97  	bl	0x475e60 <_cgo_topofstack+0xb0>
  7247d8: e5 2f 41 f9  	ldr	x5, [sp, #600]
  7247dc: 25 03 00 f9  	str	x5, [x25]
  7247e0: 06 00 40 f9  	ldr	x6, [x0]
  7247e4: 26 07 00 f9  	str	x6, [x25, #8]
  7247e8: 05 00 00 f9  	str	x5, [x0]
  7247ec: e2 3f 40 f9  	ldr	x2, [sp, #120]
  7247f0: e3 db 40 f9  	ldr	x3, [sp, #432]
  7247f4: e1 9b 40 f9  	ldr	x1, [sp, #304]
  7247f8: e0 13 41 f9  	ldr	x0, [sp, #544]
  7247fc: 74 ff ff 17  	b	0x7245cc <crosscall2+0x1affcc>
  724800: 21 8c 00 91  	add	x1, x1, #35
  724804: e1 ef 00 f9  	str	x1, [sp, #472]
  724808: a5 0c 00 b4  	cbz	x5, 0x72499c <crosscall2+0x1b039c>
  72480c: bb 24 00 b0  	adrp	x27, 0xbb9000 <crosscall2+0x1b1460>
  724810: 60 1f 45 f9  	ldr	x0, [x27, #2616]
  724814: e1 03 06 aa  	mov	x1, x6
  724818: e2 03 05 aa  	mov	x2, x5
  72481c: c1 4a f7 97  	bl	0x4f7320 <_cgo_topofstack+0x81570>
  724820: 03 1f 00 b5  	cbnz	x3, 0x724c00 <crosscall2+0x1b0600>
  724824: 3f 40 00 f1  	cmp	x1, #16
  724828: cb 1d 00 54  	b.lt	0x724be0 <crosscall2+0x1b05e0>
  72482c: e2 47 00 f9  	str	x2, [sp, #136]
  724830: e1 43 00 f9  	str	x1, [sp, #128]
  724834: e0 df 00 f9  	str	x0, [sp, #440]
  724838: 45 40 00 d1  	sub	x5, x2, #16
  72483c: e5 bb 00 f9  	str	x5, [sp, #368]
  724840: e6 03 05 cb  	neg	x6, x5
  724844: c6 fc 7f 93  	asr	x6, x6, #63
  724848: c6 00 7c 92  	and	x6, x6, #0x10
  72484c: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b1388>
  724850: 67 ab 42 f9  	ldr	x7, [x27, #1360]
  724854: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b1390>
  724858: 68 a7 42 f9  	ldr	x8, [x27, #1352]
  72485c: 09 25 00 91  	add	x9, x8, #9
  724860: c6 00 00 8b  	add	x6, x6, x0
  724864: e6 27 01 f9  	str	x6, [sp, #584]
  724868: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b13a4>
  72486c: 6a a3 42 f9  	ldr	x10, [x27, #1344]
  724870: ff 00 09 eb  	cmp	x7, x9
  724874: a2 01 00 54  	b.hs	0x7248a8 <crosscall2+0x1b02a8>
  724878: e8 cb 00 f9  	str	x8, [sp, #400]
  72487c: e0 03 0a aa  	mov	x0, x10
  724880: e1 03 09 aa  	mov	x1, x9
  724884: e2 03 07 aa  	mov	x2, x7
  724888: 23 01 80 d2  	mov	x3, #9
  72488c: a4 03 00 d0  	adrp	x4, 0x79a000 <crosscall2+0x1b0464>
  724890: 84 00 21 91  	add	x4, x4, #2112
  724894: 1b 2e f5 97  	bl	0x470100 <.text+0x6e280>
  724898: e8 cb 40 f9  	ldr	x8, [sp, #400]
  72489c: ea 03 00 aa  	mov	x10, x0
  7248a0: e9 03 01 aa  	mov	x9, x1
  7248a4: e7 03 02 aa  	mov	x7, x2
  7248a8: e9 7b 00 f9  	str	x9, [sp, #240]
  7248ac: ea 07 01 f9  	str	x10, [sp, #520]
  7248b0: e7 87 00 f9  	str	x7, [sp, #264]
  7248b4: 40 01 08 8b  	add	x0, x10, x8
  7248b8: e1 ef 40 f9  	ldr	x1, [sp, #472]
  7248bc: 22 01 80 d2  	mov	x2, #9
  7248c0: 80 47 f5 97  	bl	0x4766c0 <_cgo_topofstack+0x910>
  7248c4: 1f 20 03 d5  	nop
  7248c8: e0 ff 41 f9  	ldr	x0, [sp, #1016]
  7248cc: e1 03 42 f9  	ldr	x1, [sp, #1024]
  7248d0: e2 07 42 f9  	ldr	x2, [sp, #1032]
  7248d4: e3 07 41 f9  	ldr	x3, [sp, #520]
  7248d8: e4 7b 40 f9  	ldr	x4, [sp, #240]
  7248dc: e5 87 40 f9  	ldr	x5, [sp, #264]
  7248e0: 86 0c 80 d2  	mov	x6, #100
  7248e4: e7 03 7b b2  	orr	x7, xzr, #0x20
  7248e8: 68 0b 00 90  	adrp	x8, 0x890000 <crosscall2+0x1b0898>
  7248ec: 08 21 33 91  	add	x8, x8, #3272
  7248f0: 0c fd ff 97  	bl	0x723d20 <crosscall2+0x1af720>
  7248f4: 27 c5 f7 97  	bl	0x515d90 <_cgo_topofstack+0x9ffe0>
  7248f8: 82 16 00 b5  	cbnz	x2, 0x724bc8 <crosscall2+0x1b05c8>
  7248fc: e2 df 40 f9  	ldr	x2, [sp, #440]
  724900: e3 03 7c b2  	orr	x3, xzr, #0x10
  724904: e4 47 40 f9  	ldr	x4, [sp, #136]
  724908: 46 b8 f7 97  	bl	0x512a20 <_cgo_topofstack+0x9cc70>
  72490c: e0 53 00 f9  	str	x0, [sp, #160]
  724910: e1 eb 00 f9  	str	x1, [sp, #464]
  724914: e5 43 40 f9  	ldr	x5, [sp, #128]
  724918: a2 40 00 d1  	sub	x2, x5, #16
  72491c: e2 b7 00 f9  	str	x2, [sp, #360]
  724920: a0 03 00 d0  	adrp	x0, 0x79a000 <crosscall2+0x1b04f8>
  724924: 00 00 21 91  	add	x0, x0, #2112
  724928: e1 03 02 aa  	mov	x1, x2
  72492c: c1 2d f5 97  	bl	0x470030 <.text+0x6e1b0>
  724930: e0 3b 01 f9  	str	x0, [sp, #624]
  724934: e5 53 40 f9  	ldr	x5, [sp, #160]
  724938: a5 0c 40 f9  	ldr	x5, [x5, #24]
  72493c: e1 03 00 aa  	mov	x1, x0
  724940: e2 b7 40 f9  	ldr	x2, [sp, #360]
  724944: e3 03 02 aa  	mov	x3, x2
  724948: e4 27 41 f9  	ldr	x4, [sp, #584]
  72494c: e6 bb 40 f9  	ldr	x6, [sp, #368]
  724950: e0 eb 40 f9  	ldr	x0, [sp, #464]
  724954: e7 03 05 aa  	mov	x7, x5
  724958: e5 03 02 aa  	mov	x5, x2
  72495c: e0 00 3f d6  	blr	x7
  724960: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b149c>
  724964: 65 b7 42 f9  	ldr	x5, [x27, #1384]
  724968: e2 b7 40 f9  	ldr	x2, [sp, #360]
  72496c: bf 00 02 eb  	cmp	x5, x2
  724970: c1 11 00 54  	b.ne	0x724ba8 <crosscall2+0x1b05a8>
  724974: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b14b0>
  724978: 61 b3 42 f9  	ldr	x1, [x27, #1376]
  72497c: e0 3b 41 f9  	ldr	x0, [sp, #624]
  724980: 78 7c f3 97  	bl	0x403b60 <.text+0x1ce0>
  724984: 20 11 00 36  	tbz	w0, #0, 0x724ba8 <crosscall2+0x1b05a8>
  724988: e0 8f 40 f9  	ldr	x0, [sp, #280]
  72498c: e1 ef 40 f9  	ldr	x1, [sp, #472]
  724990: e3 6f 40 f9  	ldr	x3, [sp, #216]
  724994: e7 9b 40 f9  	ldr	x7, [sp, #304]
  724998: e8 13 41 f9  	ldr	x8, [sp, #544]
  72499c: 05 4c 00 d1  	sub	x5, x0, #19
  7249a0: e5 6b 00 f9  	str	x5, [sp, #208]
  7249a4: 87 00 00 b5  	cbnz	x7, 0x7249b4 <crosscall2+0x1b03b4>
  7249a8: e0 03 1f aa  	mov	x0, xzr
  7249ac: e2 03 1f aa  	mov	x2, xzr
  7249b0: b1 00 00 14  	b	0x724c74 <crosscall2+0x1b0674>
  7249b4: bb 24 00 b0  	adrp	x27, 0xbb9000 <crosscall2+0x1b1608>
  7249b8: 60 1f 45 f9  	ldr	x0, [x27, #2616]
  7249bc: e1 03 08 aa  	mov	x1, x8
  7249c0: e2 03 07 aa  	mov	x2, x7
  7249c4: 57 4a f7 97  	bl	0x4f7320 <_cgo_topofstack+0x81570>
  7249c8: 63 00 00 b5  	cbnz	x3, 0x7249d4 <crosscall2+0x1b03d4>
  7249cc: 3f 40 00 f1  	cmp	x1, #16
  7249d0: 2a 01 00 54  	b.ge	0x7249f4 <crosscall2+0x1b03f4>
  7249d4: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b1508>
  7249d8: 61 8b 42 f9  	ldr	x1, [x27, #1296]
  7249dc: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b1510>
  7249e0: 62 8f 42 f9  	ldr	x2, [x27, #1304]
  7249e4: e0 03 1f aa  	mov	x0, xzr
  7249e8: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  7249ec: ff 83 0f 91  	add	sp, sp, #992
  7249f0: c0 03 5f d6  	ret
  7249f4: e2 ab 00 f9  	str	x2, [sp, #336]
  7249f8: e0 23 01 f9  	str	x0, [sp, #576]
  7249fc: 25 40 00 d1  	sub	x5, x1, #16
  724a00: e5 b3 00 f9  	str	x5, [sp, #352]
  724a04: 46 00 01 cb  	sub	x6, x2, x1
  724a08: c6 40 00 91  	add	x6, x6, #16
  724a0c: e6 03 06 cb  	neg	x6, x6
  724a10: a6 fc 86 8a  	and	x6, x5, x6, asr #63
  724a14: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b1550>
  724a18: 67 ab 42 f9  	ldr	x7, [x27, #1360]
  724a1c: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b1558>
  724a20: 68 a7 42 f9  	ldr	x8, [x27, #1352]
  724a24: 01 25 00 91  	add	x1, x8, #9
  724a28: 06 00 06 8b  	add	x6, x0, x6
  724a2c: e6 1b 01 f9  	str	x6, [sp, #560]
  724a30: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b156c>
  724a34: 69 a3 42 f9  	ldr	x9, [x27, #1344]
  724a38: ff 00 01 eb  	cmp	x7, x1
  724a3c: 62 01 00 54  	b.hs	0x724a68 <crosscall2+0x1b0468>
  724a40: e8 cb 00 f9  	str	x8, [sp, #400]
  724a44: e0 03 09 aa  	mov	x0, x9
  724a48: e2 03 07 aa  	mov	x2, x7
  724a4c: 23 01 80 d2  	mov	x3, #9
  724a50: a4 03 00 d0  	adrp	x4, 0x79a000 <crosscall2+0x1b0628>
  724a54: 84 00 21 91  	add	x4, x4, #2112
  724a58: aa 2d f5 97  	bl	0x470100 <.text+0x6e280>
  724a5c: e8 cb 40 f9  	ldr	x8, [sp, #400]
  724a60: e9 03 00 aa  	mov	x9, x0
  724a64: e7 03 02 aa  	mov	x7, x2
  724a68: e7 83 00 f9  	str	x7, [sp, #256]
  724a6c: e9 03 01 f9  	str	x9, [sp, #512]
  724a70: e1 77 00 f9  	str	x1, [sp, #232]
  724a74: 20 01 08 8b  	add	x0, x9, x8
  724a78: 22 01 80 d2  	mov	x2, #9
  724a7c: e1 ef 40 f9  	ldr	x1, [sp, #472]
  724a80: 10 47 f5 97  	bl	0x4766c0 <_cgo_topofstack+0x910>
  724a84: 1f 20 03 d5  	nop
  724a88: e0 ff 41 f9  	ldr	x0, [sp, #1016]
  724a8c: e1 03 42 f9  	ldr	x1, [sp, #1024]
  724a90: e2 07 42 f9  	ldr	x2, [sp, #1032]
  724a94: e3 03 41 f9  	ldr	x3, [sp, #512]
  724a98: e4 77 40 f9  	ldr	x4, [sp, #232]
  724a9c: e5 83 40 f9  	ldr	x5, [sp, #256]
  724aa0: 06 7d 80 d2  	mov	x6, #1000
  724aa4: e7 03 7b b2  	orr	x7, xzr, #0x20
  724aa8: 68 0b 00 90  	adrp	x8, 0x890000 <crosscall2+0x1b0a58>
  724aac: 08 21 33 91  	add	x8, x8, #3272
  724ab0: 9c fc ff 97  	bl	0x723d20 <crosscall2+0x1af720>
  724ab4: e0 d7 00 f9  	str	x0, [sp, #424]
  724ab8: e1 33 00 f9  	str	x1, [sp, #96]
  724abc: e2 3b 00 f9  	str	x2, [sp, #112]
  724ac0: b4 c4 f7 97  	bl	0x515d90 <_cgo_topofstack+0x9ffe0>
  724ac4: 62 06 00 b5  	cbnz	x2, 0x724b90 <crosscall2+0x1b0590>
  724ac8: e2 33 41 f9  	ldr	x2, [sp, #608]
  724acc: e3 03 7c b2  	orr	x3, xzr, #0x10
  724ad0: e4 6b 40 f9  	ldr	x4, [sp, #208]
  724ad4: d3 b7 f7 97  	bl	0x512a20 <_cgo_topofstack+0x9cc70>
  724ad8: e0 4f 00 f9  	str	x0, [sp, #152]
  724adc: e1 e7 00 f9  	str	x1, [sp, #456]
  724ae0: e2 b3 40 f9  	ldr	x2, [sp, #352]
  724ae4: a0 03 00 d0  	adrp	x0, 0x79a000 <crosscall2+0x1b06bc>
  724ae8: 00 00 21 91  	add	x0, x0, #2112
  724aec: e1 03 02 aa  	mov	x1, x2
  724af0: 50 2d f5 97  	bl	0x470030 <.text+0x6e1b0>
  724af4: e0 3b 01 f9  	str	x0, [sp, #624]
  724af8: e5 4f 40 f9  	ldr	x5, [sp, #152]
  724afc: a5 0c 40 f9  	ldr	x5, [x5, #24]
  724b00: e1 03 00 aa  	mov	x1, x0
  724b04: e2 b3 40 f9  	ldr	x2, [sp, #352]
  724b08: e3 03 02 aa  	mov	x3, x2
  724b0c: e4 23 41 f9  	ldr	x4, [sp, #576]
  724b10: e6 ab 40 f9  	ldr	x6, [sp, #336]
  724b14: e0 e7 40 f9  	ldr	x0, [sp, #456]
  724b18: e7 03 05 aa  	mov	x7, x5
  724b1c: e5 03 02 aa  	mov	x5, x2
  724b20: e0 00 3f d6  	blr	x7
  724b24: 60 0b 00 90  	adrp	x0, 0x890000 <crosscall2+0x1b0ad4>
  724b28: 00 20 33 91  	add	x0, x0, #3272
  724b2c: e1 d7 40 f9  	ldr	x1, [sp, #424]
  724b30: e2 33 40 f9  	ldr	x2, [sp, #96]
  724b34: e3 3b 40 f9  	ldr	x3, [sp, #112]
  724b38: ce 3f fa 97  	bl	0x5b4a70 <crosscall2+0x40470>
  724b3c: e0 5f 00 f9  	str	x0, [sp, #184]
  724b40: e1 f7 00 f9  	str	x1, [sp, #488]
  724b44: 05 1c 40 f9  	ldr	x5, [x0, #56]
  724b48: e2 b3 40 f9  	ldr	x2, [sp, #352]
  724b4c: e3 ab 40 f9  	ldr	x3, [sp, #336]
  724b50: e0 03 01 aa  	mov	x0, x1
  724b54: e1 23 41 f9  	ldr	x1, [sp, #576]
  724b58: a0 00 3f d6  	blr	x5
  724b5c: e5 5f 40 f9  	ldr	x5, [sp, #184]
  724b60: a5 18 40 f9  	ldr	x5, [x5, #48]
  724b64: e0 f7 40 f9  	ldr	x0, [sp, #488]
  724b68: e1 03 1f aa  	mov	x1, xzr
  724b6c: e2 03 1f aa  	mov	x2, xzr
  724b70: e3 03 02 aa  	mov	x3, x2
  724b74: a0 00 3f d6  	blr	x5
  724b78: 5f 40 00 f1  	cmp	x2, #16
  724b7c: c3 1a 00 54  	b.lo	0x724ed4 <crosscall2+0x1b08d4>
  724b80: e3 1b 41 f9  	ldr	x3, [sp, #560]
  724b84: e1 03 1f aa  	mov	x1, xzr
  724b88: e2 03 1f aa  	mov	x2, xzr
  724b8c: 2a 00 00 14  	b	0x724c34 <crosscall2+0x1b0634>
  724b90: e0 03 1f aa  	mov	x0, xzr
  724b94: e1 03 02 aa  	mov	x1, x2
  724b98: e2 03 03 aa  	mov	x2, x3
  724b9c: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  724ba0: ff 83 0f 91  	add	sp, sp, #992
  724ba4: c0 03 5f d6  	ret
  724ba8: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b16dc>
  724bac: 61 83 42 f9  	ldr	x1, [x27, #1280]
  724bb0: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b16e4>
  724bb4: 62 87 42 f9  	ldr	x2, [x27, #1288]
  724bb8: e0 03 1f aa  	mov	x0, xzr
  724bbc: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  724bc0: ff 83 0f 91  	add	sp, sp, #992
  724bc4: c0 03 5f d6  	ret
  724bc8: e0 03 1f aa  	mov	x0, xzr
  724bcc: e1 03 02 aa  	mov	x1, x2
  724bd0: e2 03 03 aa  	mov	x2, x3
  724bd4: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  724bd8: ff 83 0f 91  	add	sp, sp, #992
  724bdc: c0 03 5f d6  	ret
  724be0: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b1714>
  724be4: 61 7b 42 f9  	ldr	x1, [x27, #1264]
  724be8: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b171c>
  724bec: 62 7f 42 f9  	ldr	x2, [x27, #1272]
  724bf0: e0 03 1f aa  	mov	x0, xzr
  724bf4: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  724bf8: ff 83 0f 91  	add	sp, sp, #992
  724bfc: c0 03 5f d6  	ret
  724c00: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b1734>
  724c04: 61 73 42 f9  	ldr	x1, [x27, #1248]
  724c08: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b173c>
  724c0c: 62 77 42 f9  	ldr	x2, [x27, #1256]
  724c10: e0 03 1f aa  	mov	x0, xzr
  724c14: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  724c18: ff 83 0f 91  	add	sp, sp, #992
  724c1c: c0 03 5f d6  	ret
  724c20: 04 68 61 38  	ldrb	w4, [x0, x1]
  724c24: 65 68 61 38  	ldrb	w5, [x3, x1]
  724c28: 84 00 05 ca  	eor	x4, x4, x5
  724c2c: 21 04 00 91  	add	x1, x1, #1
  724c30: 42 00 04 aa  	orr	x2, x2, x4
  724c34: 3f 40 00 f1  	cmp	x1, #16
  724c38: 4b ff ff 54  	b.lt	0x724c20 <crosscall2+0x1b0620>
  724c3c: 43 1c 40 d3  	ubfx	x3, x2, #0, #8
  724c40: 63 04 00 d1  	sub	x3, x3, #1
  724c44: 63 7c 5f d3  	ubfx	x3, x3, #31, #1
  724c48: 1f 20 03 d5  	nop
  724c4c: 7f 04 00 f1  	cmp	x3, #1
  724c50: 21 13 00 54  	b.ne	0x724eb4 <crosscall2+0x1b08b4>
  724c54: e0 03 1f aa  	mov	x0, xzr
  724c58: e1 3b 41 f9  	ldr	x1, [sp, #624]
  724c5c: e2 b3 40 f9  	ldr	x2, [sp, #352]
  724c60: 6c 2f f5 97  	bl	0x470a10 <.text+0x6eb90>
  724c64: e3 6f 40 f9  	ldr	x3, [sp, #216]
  724c68: e5 6b 40 f9  	ldr	x5, [sp, #208]
  724c6c: e2 03 01 aa  	mov	x2, x1
  724c70: e1 ef 40 f9  	ldr	x1, [sp, #472]
  724c74: e2 9f 00 f9  	str	x2, [sp, #312]
  724c78: e0 17 01 f9  	str	x0, [sp, #552]
  724c7c: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b17b8>
  724c80: 66 ab 42 f9  	ldr	x6, [x27, #1360]
  724c84: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b17c0>
  724c88: 67 a7 42 f9  	ldr	x7, [x27, #1352]
  724c8c: e8 24 00 91  	add	x8, x7, #9
  724c90: 7b 22 00 f0  	adrp	x27, 0xb73000 <crosscall2+0x1b17cc>
  724c94: 69 a3 42 f9  	ldr	x9, [x27, #1344]
  724c98: df 00 08 eb  	cmp	x6, x8
  724c9c: c2 01 00 54  	b.hs	0x724cd4 <crosscall2+0x1b06d4>
  724ca0: e7 cb 00 f9  	str	x7, [sp, #400]
  724ca4: e0 03 09 aa  	mov	x0, x9
  724ca8: e1 03 08 aa  	mov	x1, x8
  724cac: e2 03 06 aa  	mov	x2, x6
  724cb0: 23 01 80 d2  	mov	x3, #9
  724cb4: a4 03 00 d0  	adrp	x4, 0x79a000 <crosscall2+0x1b088c>
  724cb8: 84 00 21 91  	add	x4, x4, #2112
  724cbc: 11 2d f5 97  	bl	0x470100 <.text+0x6e280>
  724cc0: e7 cb 40 f9  	ldr	x7, [sp, #400]
  724cc4: e9 03 00 aa  	mov	x9, x0
  724cc8: e6 03 02 aa  	mov	x6, x2
  724ccc: e8 03 01 aa  	mov	x8, x1
  724cd0: e1 ef 40 f9  	ldr	x1, [sp, #472]
  724cd4: e6 7f 00 f9  	str	x6, [sp, #248]
  724cd8: e8 73 00 f9  	str	x8, [sp, #224]
  724cdc: e9 ff 00 f9  	str	x9, [sp, #504]
  724ce0: 20 01 07 8b  	add	x0, x9, x7
  724ce4: 22 01 80 d2  	mov	x2, #9
  724ce8: 76 46 f5 97  	bl	0x4766c0 <_cgo_topofstack+0x910>
  724cec: 1f 20 03 d5  	nop
  724cf0: e0 ff 41 f9  	ldr	x0, [sp, #1016]
  724cf4: e1 03 42 f9  	ldr	x1, [sp, #1024]
  724cf8: e2 07 42 f9  	ldr	x2, [sp, #1032]
  724cfc: e3 ff 40 f9  	ldr	x3, [sp, #504]
  724d00: e4 73 40 f9  	ldr	x4, [sp, #224]
  724d04: e5 7f 40 f9  	ldr	x5, [sp, #248]
  724d08: 06 e2 84 d2  	mov	x6, #10000
  724d0c: e7 03 7b b2  	orr	x7, xzr, #0x20
  724d10: 68 0b 00 90  	adrp	x8, 0x890000 <crosscall2+0x1b0cc0>
  724d14: 08 21 33 91  	add	x8, x8, #3272
  724d18: 02 fc ff 97  	bl	0x723d20 <crosscall2+0x1af720>
  724d1c: e0 d3 00 f9  	str	x0, [sp, #416]
  724d20: e1 2f 00 f9  	str	x1, [sp, #88]
  724d24: e2 37 00 f9  	str	x2, [sp, #104]
  724d28: 1a c4 f7 97  	bl	0x515d90 <_cgo_topofstack+0x9ffe0>
  724d2c: 82 0b 00 b5  	cbnz	x2, 0x724e9c <crosscall2+0x1b089c>
  724d30: e2 33 41 f9  	ldr	x2, [sp, #608]
  724d34: e3 03 7c b2  	orr	x3, xzr, #0x10
  724d38: e4 6b 40 f9  	ldr	x4, [sp, #208]
  724d3c: 39 b7 f7 97  	bl	0x512a20 <_cgo_topofstack+0x9cc70>
  724d40: e0 a7 00 f9  	str	x0, [sp, #328]
  724d44: e1 1f 01 f9  	str	x1, [sp, #568]
  724d48: e2 2f 40 f9  	ldr	x2, [sp, #88]
  724d4c: e3 37 40 f9  	ldr	x3, [sp, #104]
  724d50: 60 0b 00 90  	adrp	x0, 0x890000 <crosscall2+0x1b0d00>
  724d54: 00 20 33 91  	add	x0, x0, #3272
  724d58: e1 d3 40 f9  	ldr	x1, [sp, #416]
  724d5c: 45 3f fa 97  	bl	0x5b4a70 <crosscall2+0x40470>
  724d60: e0 4b 00 f9  	str	x0, [sp, #144]
  724d64: e1 e3 00 f9  	str	x1, [sp, #448]
  724d68: 1b 23 00 b0  	adrp	x27, 0xb85000 <crosscall2+0x1b18ec>
  724d6c: 65 77 46 f9  	ldr	x5, [x27, #3304]
  724d70: e6 6f 40 f9  	ldr	x6, [sp, #216]
  724d74: a5 00 06 8b  	add	x5, x5, x6
  724d78: e7 cf 40 f9  	ldr	x7, [sp, #408]
  724d7c: e7 0c c0 da  	rev	x7, x7
  724d80: e7 cf 00 f9  	str	x7, [sp, #408]
  724d84: e6 00 06 cb  	sub	x6, x7, x6
  724d88: e6 bf 00 f9  	str	x6, [sp, #376]
  724d8c: e5 00 05 cb  	sub	x5, x7, x5
  724d90: e5 af 00 f9  	str	x5, [sp, #344]
  724d94: c0 05 00 90  	adrp	x0, 0x7dc000 <crosscall2+0x1b0a74>
  724d98: 00 80 32 91  	add	x0, x0, #3232
  724d9c: d1 a4 f3 97  	bl	0x40e0e0 <.text+0xc260>
  724da0: e5 f7 41 f9  	ldr	x5, [sp, #1000]
  724da4: 05 00 00 f9  	str	x5, [x0]
  724da8: fb 25 00 f0  	adrp	x27, 0xbe3000 <crosscall2+0x1b1aa4>
  724dac: 65 b3 44 b9  	ldr	w5, [x27, #1200]
  724db0: 65 00 00 35  	cbnz	w5, 0x724dbc <crosscall2+0x1b07bc>
  724db4: e1 fb 41 f9  	ldr	x1, [sp, #1008]
  724db8: 04 00 00 14  	b	0x724dc8 <crosscall2+0x1b07c8>
  724dbc: 25 44 f5 97  	bl	0x475e50 <_cgo_topofstack+0xa0>
  724dc0: e1 fb 41 f9  	ldr	x1, [sp, #1008]
  724dc4: 21 03 00 f9  	str	x1, [x25]
  724dc8: e0 2b 01 f9  	str	x0, [sp, #592]
  724dcc: 01 04 00 f9  	str	x1, [x0, #8]
  724dd0: e1 bf 40 f9  	ldr	x1, [sp, #376]
  724dd4: 01 08 00 f9  	str	x1, [x0, #16]
  724dd8: a0 07 00 b0  	adrp	x0, 0x819000 <crosscall2+0x1b0bac>
  724ddc: 00 80 06 91  	add	x0, x0, #416
  724de0: c0 a4 f3 97  	bl	0x40e0e0 <.text+0xc260>
  724de4: 21 0f 00 90  	adrp	x1, 0x908000 <crosscall2+0x1b0f74>
  724de8: 21 80 09 91  	add	x1, x1, #608
  724dec: 01 00 00 f9  	str	x1, [x0]
  724df0: fb 25 00 f0  	adrp	x27, 0xbe3000 <crosscall2+0x1b1aec>
  724df4: 61 b3 44 b9  	ldr	w1, [x27, #1200]
  724df8: a1 00 00 35  	cbnz	w1, 0x724e0c <crosscall2+0x1b080c>
  724dfc: e3 2b 41 f9  	ldr	x3, [sp, #592]
  724e00: e4 1f 41 f9  	ldr	x4, [sp, #568]
  724e04: e5 e3 40 f9  	ldr	x5, [sp, #448]
  724e08: 08 00 00 14  	b	0x724e28 <crosscall2+0x1b0828>
  724e0c: 19 44 f5 97  	bl	0x475e70 <_cgo_topofstack+0xc0>
  724e10: e3 2b 41 f9  	ldr	x3, [sp, #592]
  724e14: 23 03 00 f9  	str	x3, [x25]
  724e18: e4 1f 41 f9  	ldr	x4, [sp, #568]
  724e1c: 24 07 00 f9  	str	x4, [x25, #8]
  724e20: e5 e3 40 f9  	ldr	x5, [sp, #448]
  724e24: 25 0b 00 f9  	str	x5, [x25, #16]
  724e28: 03 04 00 f9  	str	x3, [x0, #8]
  724e2c: e3 a7 40 f9  	ldr	x3, [sp, #328]
  724e30: 03 08 00 f9  	str	x3, [x0, #16]
  724e34: 04 0c 00 f9  	str	x4, [x0, #24]
  724e38: e3 4b 40 f9  	ldr	x3, [sp, #144]
  724e3c: 03 10 00 f9  	str	x3, [x0, #32]
  724e40: 05 14 00 f9  	str	x5, [x0, #40]
  724e44: e3 af 40 f9  	ldr	x3, [sp, #344]
  724e48: 03 18 00 f9  	str	x3, [x0, #48]
  724e4c: 1f 80 01 39  	strb	wzr, [x0, #96]
  724e50: e4 9f 40 f9  	ldr	x4, [sp, #312]
  724e54: 04 38 00 f9  	str	x4, [x0, #112]
  724e58: fb 25 00 f0  	adrp	x27, 0xbe3000 <crosscall2+0x1b1b54>
  724e5c: 64 b3 44 b9  	ldr	w4, [x27, #1200]
  724e60: 64 00 00 35  	cbnz	w4, 0x724e6c <crosscall2+0x1b086c>
  724e64: e4 17 41 f9  	ldr	x4, [sp, #552]
  724e68: 04 00 00 14  	b	0x724e78 <crosscall2+0x1b0878>
  724e6c: f9 43 f5 97  	bl	0x475e50 <_cgo_topofstack+0xa0>
  724e70: e4 17 41 f9  	ldr	x4, [sp, #552]
  724e74: 24 03 00 f9  	str	x4, [x25]
  724e78: 04 34 00 f9  	str	x4, [x0, #104]
  724e7c: e4 cf 40 f9  	ldr	x4, [sp, #408]
  724e80: 04 3c 00 f9  	str	x4, [x0, #120]
  724e84: 03 40 00 f9  	str	x3, [x0, #128]
  724e88: e1 03 1f aa  	mov	x1, xzr
  724e8c: e2 03 1f aa  	mov	x2, xzr
  724e90: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  724e94: ff 83 0f 91  	add	sp, sp, #992
  724e98: c0 03 5f d6  	ret
  724e9c: e0 03 1f aa  	mov	x0, xzr
  724ea0: e1 03 02 aa  	mov	x1, x2
  724ea4: e2 03 03 aa  	mov	x2, x3
  724ea8: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  724eac: ff 83 0f 91  	add	sp, sp, #992
  724eb0: c0 03 5f d6  	ret
  724eb4: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b19e8>
  724eb8: 61 93 42 f9  	ldr	x1, [x27, #1312]
  724ebc: 7b 22 00 b0  	adrp	x27, 0xb71000 <crosscall2+0x1b19f0>
  724ec0: 62 97 42 f9  	ldr	x2, [x27, #1320]
  724ec4: e0 03 1f aa  	mov	x0, xzr
  724ec8: fd fb 7f a9  	ldp	x29, x30, [sp, #-8]
  724ecc: ff 83 0f 91  	add	sp, sp, #992
  724ed0: c0 03 5f d6  	ret
  724ed4: e1 03 7c b2  	orr	x1, xzr, #0x10
  724ed8: a6 44 f5 97  	bl	0x476170 <_cgo_topofstack+0x3c0>
  724edc: e0 03 04 aa  	mov	x0, x4
  724ee0: e1 03 03 aa  	mov	x1, x3
  724ee4: ab 44 f5 97  	bl	0x476190 <_cgo_topofstack+0x3e0>
  724ee8: e1 03 03 aa  	mov	x1, x3
  724eec: a1 44 f5 97  	bl	0x476170 <_cgo_topofstack+0x3c0>
  724ef0: e0 03 04 aa  	mov	x0, x4
  724ef4: e1 03 0e aa  	mov	x1, x14
  724ef8: a6 44 f5 97  	bl	0x476190 <_cgo_topofstack+0x3e0>
  724efc: e1 03 0e aa  	mov	x1, x14
  724f00: e2 03 00 aa  	mov	x2, x0
  724f04: 9b 44 f5 97  	bl	0x476170 <_cgo_topofstack+0x3c0>
  724f08: e0 03 09 aa  	mov	x0, x9
  724f0c: e1 03 02 aa  	mov	x1, x2
  724f10: 88 44 f5 97  	bl	0x476130 <_cgo_topofstack+0x380>
  724f14: e0 03 04 aa  	mov	x0, x4
  724f18: e1 03 09 aa  	mov	x1, x9
  724f1c: 9d 44 f5 97  	bl	0x476190 <_cgo_topofstack+0x3e0>
  724f20: e1 03 09 aa  	mov	x1, x9
  724f24: e2 03 00 aa  	mov	x2, x0
  724f28: 92 44 f5 97  	bl	0x476170 <_cgo_topofstack+0x3c0>
  724f2c: 81 05 80 d2  	mov	x1, #44
  724f30: 90 44 f5 97  	bl	0x476170 <_cgo_topofstack+0x3c0>
  724f34: 61 04 80 d2  	mov	x1, #35
  724f38: 8e 44 f5 97  	bl	0x476170 <_cgo_topofstack+0x3c0>
  724f3c: e0 0b 40 b2  	orr	x0, xzr, #0x7
  724f40: e1 03 06 aa  	mov	x1, x6
  724f44: 7b 44 f5 97  	bl	0x476130 <_cgo_topofstack+0x380>
  724f48: e0 03 04 aa  	mov	x0, x4
  724f4c: 91 44 f5 97  	bl	0x476190 <_cgo_topofstack+0x3e0>
  724f50: 62 02 80 d2  	mov	x2, #19
  724f54: 87 44 f5 97  	bl	0x476170 <_cgo_topofstack+0x3c0>
  724f58: 61 02 80 d2  	mov	x1, #19
  724f5c: 75 44 f5 97  	bl	0x476130 <_cgo_topofstack+0x380>
  724f60: 1f 20 03 d5  	nop
  724f64: e0 07 00 f9  	str	x0, [sp, #8]
  724f68: e1 0b 00 f9  	str	x1, [sp, #16]
  724f6c: e2 0f 00 f9  	str	x2, [sp, #24]
  724f70: e3 13 00 f9  	str	x3, [sp, #32]
  724f74: e4 17 00 f9  	str	x4, [sp, #40]
  724f78: e3 03 1e aa  	mov	x3, x30
  724f7c: c9 3a f5 97  	bl	0x473aa0 <.text+0x71c20>
  724f80: e0 07 40 f9  	ldr	x0, [sp, #8]
  724f84: e1 0b 40 f9  	ldr	x1, [sp, #16]
  724f88: e2 0f 40 f9  	ldr	x2, [sp, #24]
  724f8c: e3 13 40 f9  	ldr	x3, [sp, #32]
  724f90: e4 17 40 f9  	ldr	x4, [sp, #40]
  724f94: a3 fc ff 17  	b	0x724220 <crosscall2+0x1afc20>
		...
```

## 关联汇编证据表：入站与写盘路径

> 说明：表中地址由保存的完整 IDA C 反编译精确确认。除 `0x724220` 的上述归档 asm 外，不把这些地址伪称为已分别完整导出汇编；表名中的“汇编证据”表示可与二进制地址对应的反编译证据，不表示本目录已保存其完整 AArch64 listing。

| 地址 | 精确确认的动作 | 入站/写盘关联 | 证据范围 |
|---|---|---|---|
| `0x760774` | `CloudWorker` 的一个分支调用 `worker/download.Download`。 | 入站下载调度，分支中服务名为 `pan.baidu.com`。 | 完整 IDA C 反编译：`.0x760120_decomp.txt`；无独立完整 asm 导出。 |
| `0x7607b4` | `CloudWorker` 的另一个分支调用同一 `Download`。 | 入站下载调度的非上述分支。 | 完整 IDA C 反编译：`.0x760120_decomp.txt`；无独立完整 asm 导出。 |
| `0x74f0cc` | 取成功响应的 `Body.tab`。 | 获取 HTTP 响应体接口类型部分。 | 完整 IDA C 反编译：`.0x74e690_decomp.txt`；无独立完整 asm 导出。 |
| `0x74f0d4` | 取成功响应的 `Body.data`。 | 与 `0x74f0cc` 一同形成输入 reader。 | 完整 IDA C 反编译：`.0x74e690_decomp.txt`；无独立完整 asm 导出。 |
| `0x74f31c` | 调用 `crypto.NewDecryptReader`。 | `.fot` 且选项启用时，响应体进入本函数所在的解密链。 | 完整 IDA C 反编译：`.0x74e690_decomp.txt`；无独立完整 asm 导出。 |
| `0x74f49c` | 调用 `path/filepath.Join`。 | 将解密读取器给出的文件名与目标目录组合。 | 完整 IDA C 反编译：`.0x74e690_decomp.txt`；无独立完整 asm 导出。 |
| `0x74f4d0` | 调用 `os.OpenFile(path, 577, 438)`。 | 创建/截断最终写盘目标；`577` 为 `0x241`，`438` 为 `0666`。 | 完整 IDA C 反编译：`.0x74e690_decomp.txt`；无独立完整 asm 导出。 |
| `0x74f5d0` | 调用 `worker/download.CopyBuffer`，目标为已打开文件、源为解密 reader。 | 实际把已解密 reader 的输出写入磁盘。 | 完整 IDA C 反编译：`.0x74e690_decomp.txt`；无独立完整 asm 导出。 |
| `0x725050` | `(*DecryptReader).Read` 调用本函数（分析名 `fot_parse_header_and_create_decrypt_reader`）。 | 当前子 Reader 已返回 `io.EOF` 后，尝试解析并衔接下一个 FOT 对象；首个对象的头解析发生在构造器 `0x72410c`。 | 完整 IDA C 反编译：`.0x724fa0_decomp.txt`；无独立完整 asm 导出。 |
| `0x725268` | `DecryptSliceReader.Read` 以 `io.ReadAtLeast` 读取 16 字节尾部 HMAC 标签。 | 正文结束后的完整性校验输入。 | 完整 IDA C 反编译：`.0x725100_decomp.txt`；无独立完整 asm 导出。 |
| `0x7253b0` | 将当前密文块写入 `tagHasher`。 | 认证的是输入密文块，同时 CTR 解密产生待写出的明文。 | 完整 IDA C 反编译：`.0x725100_decomp.txt`；无独立完整 asm 导出。 |
| `0x725514` | 逐字节 XOR 并 OR 聚合存储标签和计算标签的前 16 字节。 | 标签不一致时拒绝读取结果，防止未验证完成被当作成功。 | 完整 IDA C 反编译：`.0x725100_decomp.txt`；无独立完整 asm 导出。 |

## 结论性范围说明

已证实的关键路径是：网络响应体 → `NewDecryptReader`/本函数解析头和构造受限解密 reader → `DecryptSliceReader.Read` 对密文流做 CTR 解密与 HMAC 尾标记校验 → `CopyBuffer` 写入 `os.OpenFile` 创建的路径。本文没有运行任何 `.fot` 样本，也没有修改除本报告外的文件。对 `0x724220`，代码块保留的是归档 `lines` 字段的实际指令文本；对其它地址，结论明确限于保存的 IDA C 反编译证据。
