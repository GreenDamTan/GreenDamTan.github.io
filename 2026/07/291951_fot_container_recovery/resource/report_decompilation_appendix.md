# 附录：FOT 恢复路径的完整 IDA 反编译

## 阅读说明

本附录面向首次阅读 Go 二进制反编译结果的读者。代码块中的内容是保存的 IDA C 风格反编译原文：`a1`、`vNN`、`_rN`、`tab/data`、`RTYPE`、`runtime_gcWriteBarrier*` 等均是 Go ABI、接口表示、垃圾回收写屏障或 IDA 自动命名的产物，不应当把它们当作人工还原后的 Go 源码。为保持证据可复核性，下面每一个 `c` 代码块均逐字保留原始文本；中文说明只放在代码块外，不改写其中任何 IDA C。

可按“入口 → 下载控制 → 解密构造 → 按需读取 → 复制写盘 → 流末认证”的顺序阅读。加密侧函数也一并保留，用来对照 FOT 头格式、CTR 流和尾部 HMAC 的产生方式。

## 完整调用顺序与输入/输出

### 恢复/下载实际路径

1. `0x757540` `Executor.DownloadFile` 或 `0x760120` `Worker.downloadRemoteFile` 取得远程下载链接并准备目标路径。
2. 两条入口均调用 `0x74e690` `worker/download.Download`。
3. 对 `.fot` 且提供口令的分支，`Download` 将 HTTP 响应体和口令传给 `0x7240e0` `NewDecryptReader`。
4. `NewDecryptReader` 调用 `0x724220` `NewDecryptSliceReader`；后者读取并解析头部，建立 AES-CTR、HMAC 和只覆盖正文密文的受限读取器。
5. `Download` 在 **`0x74f4d0`** 调用 `os_OpenFile` 打开目标文件；这是目标文件的创建/打开点。
6. `Download` 在稍后的 **`0x74f5d0`** 调用 `0x74e2a0` `CopyBuffer`，才开始把上层解密 reader 的输出流式复制进已经打开的目标文件。
7. `CopyBuffer` 调用 `io_CopyBuffer`；后者反复触发 `0x725100` `DecryptSliceReader.Read`。该 `Read` 逐块读取密文、CTR 解密、更新 HMAC，并把明文交给复制例程写盘。
8. 仅当正文读取完、之后的读取进入 `remain <= 0` 分支时，`DecryptSliceReader.Read` 才读取 16 字节尾 HMAC、计算并比较标签，随后返回 EOF 或认证错误。`Download` 之后检查解密后长度及文件大小。

因此，这个流程**不是原子认证写入**：原始 Go 程序先在 `0x74f4d0` 打开目标文件，再在 `0x74f5d0` 通过流式复制驱动解密；尾 HMAC 的验证发生在流末。若认证失败，先前写入的明文可能已经到达目标文件，尽管外层错误处理可随后清理失败文件。

### 格式写入对照路径

`0x7255d0` `NewEncryptReader` 生成盐、IV 和头部，调用 `0x725b60` `buildHeader`；`0x7264c0` `EncryptReader.Read` 在读取明文时输出密文并累计 HMAC，在 EOF 后追加标签。`0x7268b0` `encryptHeaderLength` 计算相关的头部长度。这些函数不是恢复期间的主调用链，但与恢复端解析的数据格式相对应。

| 地址 | 函数 | 输入 | 输出 |
|---|---|---|---|
| `0x757540` | `Executor.DownloadFile` | 执行器、远程路径、对象 ID | 无直接返回；打包任务结果 |
| `0x760120` | `Worker.downloadRemoteFile` | 工作器、对象信息、目标路径、下载/口令参数 | 状态码、错误接口 |
| `0x74e690` | `download.Download` | URL、目标路径、大小、口令、HTTP 参数 | 状态码、错误接口 |
| `0x7240e0` | `NewDecryptReader` | 响应 reader、口令材料 | 上层解密 reader、错误 |
| `0x724220` | `NewDecryptSliceReader` | 密文 reader、口令材料 | 下层解密 reader、错误 |
| `0x724fa0` | `DecryptReader.Read` | 上层 reader、输出缓冲 | 字节数、错误；必要时解析下一个 FOT 对象 |
| `0x725100` | `DecryptSliceReader.Read` | 接收者、输出缓冲 | 字节数、错误/EOF/认证错误 |
| `0x74e2a0` | `CopyBuffer` | 目标 writer、大小参数、源 reader | 状态码、错误接口 |
| `0x7255d0` | `NewEncryptReader` | 明文 reader、元数据、口令、长度 | 加密 reader、错误 |
| `0x725b60` | `buildHeader` | 盐、IV、元数据、口令材料、长度 | FOT 头切片、错误 |
| `0x7264c0` | `EncryptReader.Read` | 接收者、输出缓冲 | 字节数、错误/EOF |
| `0x7268b0` | `encryptHeaderLength` | 基础/可选字段长度 | 头部长度 |

## 逐函数完整反编译

### 1. `0x7240e0` — `git.teiron-inc.cn/services/backup-cloud/crypto.NewDecryptReader`

- **输入/输出：** 输入：底层 `io.Reader` 的接口字与数据、口令/密钥材料的 ABI 参数。输出：封装后的 `DecryptReader` 指针及错误接口。
- **为何位于数据流中：** 恢复路径从网络响应体建立可供复制例程读取的解密读取器；它把下层 `DecryptSliceReader`、口令材料和解析出的文件名/长度组合为上层读取器。
- **关键调用点：** 调用 `NewDecryptSliceReader`（`0x72410c`）；成功后分配 `DecryptReader` 并转存下层解析出的元数据。

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

### 2. `0x724220` — `git.teiron-inc.cn/services/backup-cloud/crypto.NewDecryptSliceReader`

- **输入/输出：** 输入：密文 `io.Reader` 接口，以及口令/密钥材料的 ABI 参数。输出：`DecryptSliceReader` 指针及错误接口。
- **为何位于数据流中：** 恢复的格式解析与密码学初始化点：读取 FOT 头、解析元数据、验证受保护字段，并建立 CTR 解密器、HMAC 计算器及仅覆盖密文正文的 `io.LimitedReader`。
- **关键调用点：** 关键调用包括 `io_ReadAtLeast` 读取头部、`pbkdf2_Key`、`crypto_aes_NewCipher`、`crypto_cipher_NewCTR` 与 `crypto_hmac_New`；构造的 `io_LimitedReader->N` 排除尾部认证标签。

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

### 3. `0x724fa0` — `git.teiron-inc.cn/services/backup-cloud/crypto.(*DecryptReader).Read`

- **输入/输出：** 输入：外层 `DecryptReader` 和调用者输出缓冲。输出：当前可提供的明文字节数，或 `io.EOF` / 格式 / 认证错误。
- **为何位于数据流中：** 它是下载器通过 `io.CopyBuffer` 实际消费的外层 Reader。通常将读取请求转发给当前 `DecryptSliceReader`；当当前对象的 Reader 返回 `io.EOF` 时，再以保存的底层输入 Reader 与口令调用头解析器，构建下一个对象的 Reader。
- **关键调用点：** `0x724fd0` 和 `0x7250a8` 转发到正文 Reader；`0x725050` 在 EOF 后调用 `fot_parse_header_and_create_decrypt_reader`；`0x725068` / `0x725078` 累加该对象的密文/明文长度。该函数解释了流式复制为何能触发 FOT parser 与正文认证。

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

### 4. `0x725100` — `git.teiron-inc.cn/services/backup-cloud/crypto.(*DecryptSliceReader).Read`

- **输入/输出：** 输入：`DecryptSliceReader` 接收者和调用者提供的输出字节切片。输出：本次明文字节数与 `error`。
- **为何位于数据流中：** 这是流式恢复的逐块执行点：读取有限密文、CTR 解密并把密文送入 HMAC。正文耗尽后才读取 16 字节尾标签并比较，因此认证结果在流末端才产生。
- **关键调用点：** 关键调用包括底层 reader 的 `Read`、解密器 `XORKeyStream`、HMAC `Write`/`Sum`；在 `remain <= 0` 分支以 `io_ReadAtLeast` 读取尾标签并逐字节比较。

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

### 5. `0x7255d0` — `git.teiron-inc.cn/services/backup-cloud/crypto.NewEncryptReader`

- **输入/输出：** 输入：明文 `io.Reader`、文件名/口令等 ABI 参数、明文长度和可选元数据。输出：`EncryptReader` 指针及错误接口。
- **为何位于数据流中：** 它是恢复格式的写入端对应构造器：生成盐、IV、填充，派生密钥并将格式头放入输出缓冲；保留它可与恢复端的头部解析和密钥派生交叉核对。
- **关键调用点：** 关键调用包括 `crypto_rand_Read`、`pbkdf2_Key(..., 10000, ...)`、`crypto_aes_NewCipher`、`crypto_cipher_NewCTR`、`crypto_hmac_New` 和 `buildHeader`（`0x725964`）。

```c
// git.teiron-inc.cn/services/backup-cloud/crypto.NewEncryptReader
retval_7255D0 __golang git_teiron_inc_cn_services_backup_cloud_crypto_NewEncryptReader(
        __int64 a1,
        __int64 a2,
        __int64 a3,
        __int64 a4,
        __int64 a5,
        __int64 a6,
        __int64 a7,
        __int64 a8,
        __int64 a9)
{
  __int64 r1; // x1
  __int64 v10; // x2
  RTYPE *v11; // x5
  __int64 r2; // x2
  __int64 v16; // x5
  __int64 v17; // x1
  uint8 *r0; // x0
  __int64 v19; // x1
  __int64 v20; // x2
  RTYPE *v21; // x5
  __int64 v22; // x1
  __int64 v23; // x2
  RTYPE *v24; // x5
  RTYPE *v25; // x5
  __int64 v26; // x4
  RTYPE *v27; // x5
  _QWORD *v28; // x0
  _QWORD *v29; // x25
  signed __int64 len; // x4
  signed __int64 cap; // x5
  signed __int64 off; // x6
  __int64 v33; // [xsp+90h] [xbp-B8h]
  __int64 v34; // [xsp+98h] [xbp-B0h]
  __int64 v35; // [xsp+A0h] [xbp-A8h]
  __int64 v36; // [xsp+A8h] [xbp-A0h]
  __int64 v37; // [xsp+B0h] [xbp-98h]
  __int64 v38; // [xsp+C0h] [xbp-88h]
  uint8 *v39; // [xsp+C8h] [xbp-80h]
  __int64 v40; // [xsp+D0h] [xbp-78h]
  bytes_Buffer v41; // [xsp+D8h] [xbp-70h] BYREF
  RTYPE *v42; // [xsp+100h] [xbp-48h] BYREF
  void *v43; // [xsp+108h] [xbp-40h]
  __int64 v44; // [xsp+110h] [xbp-38h]
  __int64 r3; // [xsp+118h] [xbp-30h]
  __int64 v46; // [xsp+120h] [xbp-28h]
  unsigned __int8 *v47; // [xsp+128h] [xbp-20h]
  __int64 v48; // [xsp+130h] [xbp-18h]
  __int64 v49; // [xsp+138h] [xbp-10h]
  retval_4D6BE0 v59; // 0:x0.16
  retval_4D6BE0 v60; // 0:x0.16
  retval_4D6BE0 v61; // 0:x0.16
  retval_4D6BE0 v62; // 0:x0.16
  retval_512A20 v63; // 0:x0.16
  retval_5B4A70 v64; // 0:x0.16
  retval_4D6BE0 v65; // 0:x0.16
  retval_7255D0 result; // 0:x0.24
  retval_470100 v67; // 0:kr00_24.24
  retval_723D20 v68; // 0:kr28_24.24
  retval_515D90 v69; // 0:kr40_32.32
  retval_725B60 v70; // 0:kr80_32.32

  v49 = runtime_makeslice(&RTYPE_uint8); /*0x725628*/
  r1 = crypto_rand_Read(v49, 9, 9)._r1; /*0x725634*/
  if ( r1 )
  {
    v42 = nullptr; /*0x72563c*/
    v43 = nullptr; /*0x72563c*/
    v44 = 0; /*0x725640*/
    r3 = 0; /*0x725640*/
    v11 = (RTYPE *)off_B71550; /*0x725648*/
    if ( off_B71550 ) /*0x72564c*/
      v11 = off_B71550[1]; /*0x725650*/
    v42 = v11; /*0x72565c*/
    v43 = off_B71558; /*0x725660*/
    v44 = *(_QWORD *)(r1 + 8); /*0x72566c*/
    r3 = v10; /*0x725670*/
    v59 = fmt_Errorf("%w: %v", 6, &v42, 2, 2);
    result._r2 = v59._r1; /*0x725690*/
    result._r1 = v59._r0; /*0x725694*/
    result._r0 = 0; /*0x725698*/
  }
  else
  {
    r2 = qword_B73550; /*0x7256ac*/
    v16 = qword_B73548; /*0x7256b4*/
    v17 = qword_B73548 + 9; /*0x7256b8*/
    r0 = off_B73540; /*0x7256c0*/
    if ( qword_B73550 < (unsigned __int64)(qword_B73548 + 9) ) /*0x7256c8*/
    {
      v37 = qword_B73548; /*0x7256cc*/
      v67 = runtime_growslice(off_B73540, v17, qword_B73550, 9, &RTYPE_uint8); /*0x7256dc*/
      r0 = (uint8 *)v67._r0; /*0xf1c0000000000004*/
      v17 = v67._r1; /*0xf1c0000000000008*/
      r2 = v67._r2; /*0xf1c000000000000c*/
      v16 = v37; /*0x7256e0*/
    }
    v35 = r2; /*0x7256e4*/
    v34 = v17; /*0x7256e8*/
    v39 = r0; /*0x7256ec*/
    runtime_memmove(&r0[v16], v49, 9); /*0x725700*/
    v48 = runtime_makeslice(&RTYPE_uint8); /*0x725718*/
    v47 = (unsigned __int8 *)runtime_makeslice(&RTYPE_uint8); /*0x725730*/
    v19 = crypto_rand_Read(v48, 16, 16)._r1; /*0x725740*/
    if ( v19 )
    {
      v42 = nullptr; /*0x725748*/
      v43 = nullptr; /*0x725748*/
      v44 = 0; /*0x72574c*/
      r3 = 0; /*0x72574c*/
      v21 = (RTYPE *)off_B71550; /*0x725754*/
      if ( off_B71550 ) /*0x725758*/
        v21 = off_B71550[1]; /*0x72575c*/
      v42 = v21; /*0x725768*/
      v43 = off_B71558; /*0x72576c*/
      v44 = *(_QWORD *)(v19 + 8); /*0x725778*/
      r3 = v20; /*0x72577c*/
      v60 = fmt_Errorf("%w: failed to generate IV: %v", 29, &v42, 2, 2);
      result._r2 = v60._r1; /*0x72579c*/
      result._r1 = v60._r0; /*0x7257a0*/
      result._r0 = 0; /*0x7257a4*/
    }
    else
    {
      v22 = crypto_rand_Read(v47, 1, 1)._r1; /*0x7257c0*/
      if ( v22 )
      {
        v42 = nullptr; /*0x7257c8*/
        v43 = nullptr; /*0x7257c8*/
        v44 = 0; /*0x7257cc*/
        r3 = 0; /*0x7257cc*/
        v24 = (RTYPE *)off_B71550; /*0x7257d4*/
        if ( off_B71550 ) /*0x7257d8*/
          v24 = off_B71550[1]; /*0x7257dc*/
        v42 = v24; /*0x7257e8*/
        v43 = off_B71558; /*0x7257ec*/
        v44 = *(_QWORD *)(v22 + 8); /*0x7257f8*/
        r3 = v23; /*0x7257fc*/
        v61 = fmt_Errorf("%w: failed to generate padding: %v", 34, &v42, 2, 2);
        result._r2 = v61._r1; /*0x72581c*/
        result._r1 = v61._r0; /*0x725820*/
        result._r0 = 0; /*0x725824*/
      }
      else
      {
        v68 = golang_org_x_crypto_pbkdf2_Key(a5, a6, a7, v39, v34, v35, 10000, 32, off_890CC8); /*0x725860*/
        v69 = crypto_aes_NewCipher(v68._r0); /*0x725870*/
        if ( v69._r2 )
        {
          v42 = nullptr; /*0x725878*/
          v43 = nullptr; /*0x725878*/
          v44 = 0; /*0x72587c*/
          r3 = 0; /*0x72587c*/
          v25 = (RTYPE *)off_B71550; /*0x725884*/
          if ( off_B71550 ) /*0x725888*/
            v25 = off_B71550[1]; /*0x72588c*/
          v42 = v25; /*0x725898*/
          v43 = off_B71558; /*0x72589c*/
          v44 = *(_QWORD *)(v69._r2 + 8LL); /*0x7258a8*/
          r3 = v69._r3; /*0x7258ac*/
          v62 = fmt_Errorf("%w: %v", 6, &v42, 2, 2);
          result._r2 = v62._r1; /*0x7258cc*/
          result._r1 = v62._r0; /*0x7258d0*/
          result._r0 = 0; /*0x7258d4*/
        }
        else
        {
          v63 = crypto_cipher_NewCTR(v69._r0, v69._r1, v48, 16, 16); /*0x7258f0*/
          v36 = v63._r0; /*0x7258f4*/
          v40 = v63._r1; /*0x7258f8*/
          v64 = crypto_hmac_New(off_890CC8, v68._r0, v68._r1, v68._r2); /*0x725910*/
          v33 = v64._r0; /*0x725914*/
          v38 = v64._r1; /*0x725918*/
          v70 = git_teiron_inc_cn_services_backup_cloud_crypto_buildHeader( /*0x725964*/
                  v49,
                  9,
                  9,
                  a3,
                  a4,
                  a5,
                  a6,
                  a7,
                  v48,
                  16,
                  16,
                  *v47,
                  a9,
                  a8 + qword_B85CE8);
          if ( v70._r3 )
          {
            v42 = nullptr; /*0x72596c*/
            v43 = nullptr; /*0x72596c*/
            v44 = 0; /*0x725970*/
            r3 = 0; /*0x725970*/
            v27 = (RTYPE *)off_B71550; /*0x725978*/
            if ( off_B71550 ) /*0x72597c*/
              v27 = off_B71550[1]; /*0x725980*/
            v42 = v27; /*0x72598c*/
            v43 = off_B71558; /*0x725990*/
            v44 = *(_QWORD *)(v70._r3 + 8LL); /*0x72599c*/
            r3 = v26; /*0x7259a0*/
            v65 = fmt_Errorf("%w: %v", 6, &v42, 2, 2);
            result._r2 = v65._r1; /*0x7259c0*/
            result._r1 = v65._r0; /*0x7259c4*/
            result._r0 = 0; /*0x7259c8*/
          }
          else
          {
            memset(&v41, 0, sizeof(v41)); /*0x7259dc*/
            bytes__ptr_Buffer_Write(&v41, *(_slice_uint8 *)&v70._r0); /*0x7259f8*/
            v49 = runtime_makeslice(&RTYPE_uint8); /*0x725a10*/
            result._r0 = runtime_newobject(&RTYPE_git_teiron_inc_cn_services_backup_cloud_crypto_EncryptReader); /*0x725a1c*/
            if ( dword_BE34B0 ) /*0x725a28*/
            {
              v46 = result._r0; /*0x725a2c*/
              v28 = (_QWORD *)runtime_gcWriteBarrier8(); /*0x725a30*/
              *v29 = a2; /*0x725a38*/
              v29[1] = v28[6]; /*0x725a40*/
              v29[2] = v40; /*0x725a48*/
              v29[3] = v28[8]; /*0x725a50*/
              v29[4] = v49; /*0x725a58*/
              v29[5] = v28[9]; /*0x725a60*/
              v29[6] = v38; /*0x725a68*/
              v29[7] = v28[13]; /*0x725a70*/
              runtime_wbMove(&RTYPE_bytes_Buffer, v28, &v41); /*0x725a84*/
              result._r0 = v46; /*0x725a88*/
            }
            len = v41.buf.len; /*0x725a8c*/
            cap = v41.buf.cap; /*0x725a90*/
            off = v41.off; /*0x725a90*/
            *(_QWORD *)result._r0 = v41.buf.array; /*0x725a94*/
            *(_QWORD *)(result._r0 + 8LL) = len; /*0x725a94*/
            *(_QWORD *)(result._r0 + 16LL) = cap; /*0x725a98*/
            *(_QWORD *)(result._r0 + 24LL) = off; /*0x725a98*/
            *(_QWORD *)(result._r0 + 32LL) = *(_QWORD *)&v41.lastRead; /*0x725aa0*/
            *(_QWORD *)(result._r0 + 40LL) = a1; /*0x725aa8*/
            *(_QWORD *)(result._r0 + 48LL) = a2; /*0x725ab0*/
            *(_QWORD *)(result._r0 + 56LL) = v36; /*0x725ab8*/
            *(_QWORD *)(result._r0 + 64LL) = v40; /*0x725ac0*/
            *(_QWORD *)(result._r0 + 80LL) = 4096; /*0x725ac8*/
            *(_QWORD *)(result._r0 + 88LL) = 4096; /*0x725acc*/
            *(_QWORD *)(result._r0 + 72LL) = v49; /*0x725ad4*/
            *(_QWORD *)(result._r0 + 96LL) = v33; /*0x725adc*/
            *(_QWORD *)(result._r0 + 104LL) = v38; /*0x725ae4*/
            *(_BYTE *)(result._r0 + 112LL) = 0; /*0x725ae8*/
            result._r1 = 0; /*0x725aec*/
            result._r2 = 0; /*0x725af0*/
          }
        }
      }
    }
  }
  return result; /*0x7256a4*/
}
```

### 6. `0x725b60` — `git.teiron-inc.cn/services/backup-cloud/crypto.buildHeader`

- **输入/输出：** 输入：盐、文件名、口令材料、IV、填充字节、可选元数据映射与文件长度。输出：序列化的 FOT 头切片及错误。
- **为何位于数据流中：** 该函数给出恢复端必须读取的头部布局：魔数、版本/长度、IV、盐、填充和可选字段，以及写入的总长度字段。
- **关键调用点：** 关键调用包括 `bytes.Buffer.Write`/`WriteByte`、不同迭代次数的 `pbkdf2_Key`、CTR 与 HMAC、Base64 编码、以及 `encoding_binary_Write` 写入元数据项长度。

```c
// git.teiron-inc.cn/services/backup-cloud/crypto.buildHeader
retval_725B60 __golang git_teiron_inc_cn_services_backup_cloud_crypto_buildHeader(
        uint8 *a1,
        signed __int64 a2,
        signed __int64 a3,
        __int64 a4,
        __int64 a5,
        __int64 a6,
        __int64 a7,
        __int64 a8,
        uint8 *a9,
        signed __int64 a10,
        signed __int64 a11,
        uint8 a12,
        __int64 *a13,
        __int64 a14)
{
  bytes_Buffer *p_bytes_Buffer; // x0
  __int64 *v15; // x0
  __int64 v16; // x5
  __int64 r2; // x6
  __int64 v18; // x7
  signed __int64 v19; // x8
  signed __int64 r1; // x9
  uint8 *r0; // x10
  __int64 v22; // x1
  __int64 v23; // x2
  unsigned __int64 v24; // x3
  unsigned __int64 v25; // x1
  __int64 v26; // x0
  __int64 v27; // x2
  _QWORD *v28; // x0
  __int64 v29; // x5
  _QWORD *v30; // x25
  __int64 v31; // x6
  __int64 v32; // x7
  signed __int64 v33; // x8
  signed __int64 v34; // x9
  uint8 *v35; // x10
  unsigned __int64 v36; // x2
  unsigned __int64 v37; // x1
  unsigned __int64 v38; // x3
  __int64 v39; // x4
  _QWORD *v40; // x0
  __int64 v41; // x3
  _QWORD *v42; // x25
  __int64 v48; // x7
  __int64 v49; // x10
  _ptr_bytes_Buffer v50; // x5
  unsigned __int64 v51; // x1
  unsigned __int64 v52; // x0
  unsigned __int64 v53; // x6
  uint8 *v54; // x7
  unsigned __int64 v55; // x0
  unsigned __int64 v56; // x1
  unsigned __int64 v57; // x7
  unsigned __int64 v58; // x0
  unsigned __int64 v59; // x1
  __int64 v60; // x7
  unsigned __int64 v61; // x0
  unsigned __int64 v62; // x6
  unsigned __int64 v63; // x1
  unsigned __int64 v64; // x2
  unsigned __int64 v65; // x1
  unsigned __int64 v66; // x0
  __int64 v67; // x0
  __int64 v68; // x0
  __int64 v69; // [xsp+6Ch] [xbp-30Ch] BYREF
  __int16 v70; // [xsp+74h] [xbp-304h] BYREF
  __int16 v71; // [xsp+76h] [xbp-302h] BYREF
  __int64 v72; // [xsp+78h] [xbp-300h]
  __int64 v73; // [xsp+80h] [xbp-2F8h]
  unsigned __int64 v74; // [xsp+88h] [xbp-2F0h]
  unsigned __int64 v75; // [xsp+90h] [xbp-2E8h]
  __int64 v76; // [xsp+98h] [xbp-2E0h]
  unsigned __int64 v77; // [xsp+A0h] [xbp-2D8h]
  __int64 v78; // [xsp+A8h] [xbp-2D0h]
  __int64 v79; // [xsp+B0h] [xbp-2C8h]
  __int64 v80; // [xsp+B8h] [xbp-2C0h]
  __int64 v81; // [xsp+C0h] [xbp-2B8h]
  unsigned __int64 v82; // [xsp+C8h] [xbp-2B0h]
  __int64 v83; // [xsp+D0h] [xbp-2A8h]
  uint8 v84[8]; // [xsp+D8h] [xbp-2A0h]
  signed __int64 v85; // [xsp+E0h] [xbp-298h]
  signed __int64 v86; // [xsp+E8h] [xbp-290h]
  __int64 v87; // [xsp+F0h] [xbp-288h]
  __int64 v88; // [xsp+F8h] [xbp-280h]
  unsigned __int64 v89; // [xsp+100h] [xbp-278h]
  unsigned __int64 v90; // [xsp+108h] [xbp-270h]
  unsigned __int64 v91; // [xsp+110h] [xbp-268h]
  __int64 v92; // [xsp+118h] [xbp-260h]
  signed __int64 v93; // [xsp+120h] [xbp-258h]
  signed __int64 v94; // [xsp+128h] [xbp-250h]
  signed __int64 off; // [xsp+130h] [xbp-248h]
  signed __int64 len; // [xsp+138h] [xbp-240h]
  __int64 v97; // [xsp+140h] [xbp-238h]
  string v98; // [xsp+148h] [xbp-230h]
  __int64 v99; // [xsp+158h] [xbp-220h]
  __int64 v100; // [xsp+160h] [xbp-218h]
  __int64 v101; // [xsp+168h] [xbp-210h]
  __int64 v102; // [xsp+170h] [xbp-208h]
  __int64 v103; // [xsp+178h] [xbp-200h]
  string v104; // [xsp+180h] [xbp-1F8h]
  uint8 *v105; // [xsp+190h] [xbp-1E8h]
  __int64 v106; // [xsp+198h] [xbp-1E0h]
  __int64 v107; // [xsp+1A0h] [xbp-1D8h]
  _ptr_bytes_Buffer v108; // [xsp+1A8h] [xbp-1D0h]
  _BYTE v109[272]; // [xsp+1B0h] [xbp-1C8h] BYREF
  __int64 v110; // [xsp+2C0h] [xbp-B8h] BYREF
  __int64 v111; // [xsp+2C8h] [xbp-B0h]
  _BYTE *v112; // [xsp+2D0h] [xbp-A8h]
  __int64 v113; // [xsp+2D8h] [xbp-A0h]
  __int64 v114; // [xsp+2E0h] [xbp-98h]
  __int64 v115; // [xsp+2E8h] [xbp-90h]
  _QWORD v116[12]; // [xsp+2F0h] [xbp-88h] BYREF
  __int64 v117; // [xsp+350h] [xbp-28h]
  __int64 v118; // [xsp+358h] [xbp-20h]
  __int64 v119; // [xsp+360h] [xbp-18h]
  __int64 *v120; // [xsp+368h] [xbp-10h]
  retval_512A20 v135; // 0:x0.16
  retval_4F6D10 v136; // 0:x0.16
  retval_512A20 v137; // 0:x0.16
  retval_5B4A70 v138; // 0:x0.16
  retval_4F6D10 v139; // 0:x0.16
  string v140; // 0:x1.16
  string v141; // 0:x1.16
  _slice_uint8 v142; // 0:x1.24
  _slice_uint8 v143; // 0:x1.24
  _slice_uint8 v144; // 0:x1.24
  _slice_uint8 v145; // 0:x1.24
  retval_470100 v146; // 0:krA8_24.24
  retval_470100 v147; // 0:kr100_24.24
  retval_470100 v148; // 0:kr160_24.24
  retval_723D20 v149; // 0:kr188_24.24
  retval_454610 v150; // 0:kr1C0_24.24
  retval_470100 v151; // 0:kr1E8_24.24
  retval_7260C4 v152; // 0:kr260_24.24
  retval_515D90 v153; // 0:krE0_32.32
  retval_515D90 v154; // 0:kr1A0_32.32
  retval_725B60 result; // 0:x0.40

  p_bytes_Buffer = (bytes_Buffer *)runtime_newobject(&RTYPE_bytes_Buffer); /*0x725bc4*/
  v108 = p_bytes_Buffer; /*0x725bc8*/
  p_bytes_Buffer->buf.array = nullptr; /*0x725bcc*/
  bytes__ptr_Buffer_Write(p_bytes_Buffer, *(_slice_uint8 *)byte_B73520); /*0x725be8*/
  bytes__ptr_Buffer_WriteByte(v108, byte_B859E2); /*0x725bf8*/
  len = v108->buf.len; /*0x725c04*/
  off = v108->off; /*0x725c0c*/
  v70 = 0; /*0x725c14*/
  v142.cap = 2; /*0x725c18*/
  v142.array = (uint8 *)&v70; /*0x725c1c*/
  v142.len = 2; /*0x725c20*/
  bytes__ptr_Buffer_Write(v108, v142); /*0x725c24*/
  v94 = v108->buf.len; /*0x725c30*/
  v93 = v108->off; /*0x725c38*/
  v69 = 0; /*0x725c40*/
  v143.cap = 8; /*0x725c44*/
  v143.array = (uint8 *)&v69; /*0x725c48*/
  v143.len = 8; /*0x725c4c*/
  bytes__ptr_Buffer_Write(v108, v143); /*0x725c50*/
  v144.array = a9; /*0x725c58*/
  v144.len = a10; /*0x725c5c*/
  v144.cap = a11; /*0x725c60*/
  bytes__ptr_Buffer_Write(v108, v144); /*0x725c64*/
  v145.array = a1; /*0x725c6c*/
  v145.len = a2; /*0x725c70*/
  v145.cap = a3; /*0x725c74*/
  bytes__ptr_Buffer_Write(v108, v145); /*0x725c78*/
  bytes__ptr_Buffer_WriteByte(v108, a12); /*0x725c84*/
  v75 = len - off; /*0x725c94*/
  v74 = v94 - v93; /*0x725ca4*/
  v15 = a13; /*0x725ca8*/
  if ( !a13 ) /*0x725cac*/
  {
    v110 = 0; /*0x725cb4*/
    v111 = 0; /*0x725cb4*/
    v113 = 0; /*0x725cbc*/
    v114 = 0; /*0x725cc4*/
    v115 = 0; /*0x725cc4*/
    memset(v109, 0, sizeof(v109)); /*0x725cd8*/
    v112 = v109; /*0x725ce4*/
    HIDWORD(v111) = runtime_rand32(v109); /*0x725cec*/
    v15 = &v110; /*0x725d00*/
  }
  v120 = v15; /*0x725d04*/
  v16 = a5; /*0x725d08*/
  if ( a5 ) /*0x725d0c*/
  {
    r2 = qword_B73550; /*0x725d14*/
    v18 = qword_B73548; /*0x725d1c*/
    v19 = a2; /*0x725d20*/
    r1 = a2 + qword_B73548; /*0x725d24*/
    r0 = off_B73540; /*0x725d2c*/
    if ( qword_B73550 < (unsigned __int64)(a2 + qword_B73548) ) /*0x725d34*/
    {
      len = qword_B73548; /*0x725d38*/
      v146 = runtime_growslice(off_B73540, a2 + qword_B73548, qword_B73550, a2, &RTYPE_uint8); /*0x725d54*/
      v18 = len; /*0x725d58*/
      v19 = a2; /*0x725d5c*/
      r0 = (uint8 *)v146._r0; /*0x725d60*/
      r1 = v146._r1; /*0x725d64*/
      r2 = v146._r2; /*0x725d68*/
    }
    v86 = r1; /*0x725d6c*/
    v105 = r0; /*0x725d70*/
    v88 = r2; /*0x725d74*/
    runtime_memmove(&r0[v18], a1, v19); /*0x725d84*/
    v67 = golang_org_x_crypto_pbkdf2_Key(a6, a7, a8, v105, v86, v88, 100, 32, off_890CC8)._r0; /*0xf1c000000000004c*/
    v153 = crypto_aes_NewCipher(v67); /*0x725db8*/
    if ( v153._r2 ) /*0x725dbc*/
    {
      result._r0 = 0; /*0x726218*/
      result._r1 = 0; /*0x72621c*/
      result._r4 = v153._r3; /*0x726220*/
      result._r3 = v153._r2; /*0x726224*/
      result._r2 = 0; /*0x726228*/
      return result; /*0x726234*/
    }
    v107 = v153._r1; /*0x725dc0*/
    v92 = v153._r0; /*0x725dc4*/
    v119 = runtime_makeslice(&RTYPE_uint8); /*0x725ddc*/
    v22 = crypto_rand_Read(v119, 16, 16)._r1; /*0x725de8*/
    if ( v22 ) /*0x725dec*/
    {
      result._r0 = 0; /*0x7261f8*/
      result._r3 = v22; /*0x7261fc*/
      result._r4 = v23; /*0x726200*/
      result._r1 = 0; /*0x726204*/
      result._r2 = 0; /*0x726208*/
      return result; /*0x726214*/
    }
    v135 = crypto_cipher_NewCTR(v92, v107, v119, 16, 16); /*0x725e04*/
    v81 = v135._r0; /*0x725e08*/
    v102 = v135._r1; /*0x725e0c*/
    v91 = qword_B73568; /*0x725e18*/
    v118 = runtime_makeslice(&RTYPE_uint8); /*0x725e2c*/
    (*(void (__golang **)(__int64, __int64))(v81 + 24))(v102, v118); /*0x725e60*/
    v24 = v91; /*0x725e64*/
    v25 = v91 + 16; /*0x725e68*/
    if ( v91 != 0 && v91 < 0xFFFFFFFFFFFFFFF0LL ) /*0x725e6c*/
    {
      v147 = runtime_growslice(v119, v25, 16, v91, &RTYPE_uint8); /*0x725e90*/
      v26 = v147._r0; /*0xf1c0000000000068*/
      v25 = v147._r1; /*0xf1c000000000006c*/
      v27 = v147._r2; /*0xf1c0000000000070*/
      v24 = v91; /*0x725e94*/
    }
    else
    {
      v26 = v119; /*0x725e74*/
      v27 = 16; /*0x725e78*/
    }
    v78 = v27; /*0x725e98*/
    v98.len = v26; /*0x725e9c*/
    v77 = v25; /*0x725ea0*/
    runtime_memmove(v26 + 16, v118, v24); /*0x725eb4*/
    v136 = encoding_base64__ptr_Encoding_EncodeToString(qword_BB9A38, v98.len, v77, v78); /*0x725ecc*/
    len = v136._r1; /*0x725ed0*/
    v117 = v136._r0; /*0x725ed4*/
    v28 = (_QWORD *)runtime_mapassign_faststr(&RTYPE_map_string_string, v120); /*0x725ef0*/
    v28[1] = len; /*0x725ef8*/
    if ( dword_BE34B0 ) /*0x725f04*/
    {
      v28 = (_QWORD *)runtime_gcWriteBarrier2(v28)._r0; /*0xf1c0000000000074*/
      v29 = v117; /*0x725f14*/
      *v30 = v117; /*0x725f18*/
      v30[1] = *v28; /*0x725f20*/
    }
    else
    {
      v29 = v117; /*0x725f08*/
    }
    *v28 = v29; /*0x725f24*/
    v15 = v120; /*0x725f28*/
    v16 = a5; /*0x725f3c*/
  }
  if ( v16 ) /*0x725f40*/
  {
    v31 = qword_B73550; /*0x725f48*/
    v32 = qword_B73548; /*0x725f50*/
    v33 = a2; /*0x725f54*/
    v34 = a2 + qword_B73548; /*0x725f58*/
    v35 = off_B73540; /*0x725f60*/
    if ( qword_B73550 < (unsigned __int64)(a2 + qword_B73548) ) /*0x725f68*/
    {
      len = qword_B73548; /*0x725f6c*/
      v148 = runtime_growslice(off_B73540, a2 + qword_B73548, qword_B73550, a2, &RTYPE_uint8); /*0x725f88*/
      v32 = len; /*0x725f8c*/
      v33 = a2; /*0x725f90*/
      v35 = (uint8 *)v148._r0; /*0x725f94*/
      v31 = v148._r2; /*0x725f98*/
      v34 = v148._r1; /*0x725f9c*/
    }
    v87 = v31; /*0x725fa0*/
    v85 = v34; /*0x725fa4*/
    v104.len = (__int64)v35; /*0x725fa8*/
    runtime_memmove(&v35[v32], a1, v33); /*0x725fb8*/
    v149 = golang_org_x_crypto_pbkdf2_Key(a6, a7, a8, v104.len, v85, v87, 1000, 32, off_890CC8); /*0x725fe8*/
    v97 = v149._r0; /*0x725fec*/
    v72 = v149._r1; /*0x725ff0*/
    v73 = v149._r2; /*0x725ff4*/
    v154 = crypto_aes_NewCipher(v149._r0); /*0x725ff8*/
    if ( v154._r2 ) /*0x725ffc*/
    {
      result._r0 = 0; /*0x7261d8*/
      result._r1 = 0; /*0x7261dc*/
      result._r4 = v154._r3; /*0x7261e0*/
      result._r3 = v154._r2; /*0x7261e4*/
      result._r2 = 0; /*0x7261e8*/
      return result; /*0x7261f4*/
    }
    v137 = crypto_cipher_NewCTR(v154._r0, v154._r1, a9, a10, a11); /*0x72600c*/
    v80 = v137._r0; /*0x726010*/
    v101 = v137._r1; /*0x726014*/
    v150 = runtime_stringtoslicebyte(0, a4, a5); /*0x726024*/
    v103 = v150._r0; /*0x726028*/
    v82 = v150._r1; /*0x72602c*/
    v83 = v150._r2; /*0x726030*/
    v119 = runtime_makeslice(&RTYPE_uint8); /*0x726044*/
    (*(void (__golang **)(__int64, __int64))(v80 + 24))(v101, v119); /*0x726070*/
    v138 = crypto_hmac_New(off_890CC8, v97, v72, v73); /*0x726088*/
    v79 = v138._r0; /*0x72608c*/
    v99 = v138._r1; /*0x726090*/
    (*(void (__golang **)(_QWORD, __int64))(v138._r0 + 56LL))(v138._r1, v119); /*0x7260a8*/
    v152 = ((retval_7260C4 (__golang *)(__int64, _QWORD, _QWORD, _QWORD))*(_QWORD *)(v79 + 48))(v99, 0, 0, 0); /*0x7260c4*/
    v68 = v152._r0; /*0xf1c00000000000f4*/
    if ( v152._r2 < 0x10u ) /*0x7260cc*/
      runtime_panicSliceAcap(v152._r0, 16); /*0x72643c*/
    v36 = v82; /*0x7260d0*/
    v37 = v82 + 16; /*0x7260d4*/
    if ( v82 < v82 + 16 ) /*0x7260dc*/
    {
      v100 = v152._r0; /*0x7260ec*/
      v151 = runtime_growslice(v119, v37, v82, 16, &RTYPE_uint8); /*0x726100*/
      v37 = v151._r1; /*0xf1c00000000000c0*/
      v36 = v151._r2; /*0xf1c00000000000c4*/
      v38 = v82; /*0x726104*/
      v39 = v151._r0; /*0x726108*/
      v68 = v100; /*0x72610c*/
    }
    else
    {
      v38 = v82; /*0x7260e0*/
      v39 = v119; /*0x7260e4*/
    }
    v106 = v39; /*0x726110*/
    v89 = v37; /*0x726114*/
    v90 = v36; /*0x726118*/
    runtime_memmove(v39 + v38, v68, 16); /*0x726130*/
    v139 = encoding_base64__ptr_Encoding_EncodeToString(qword_BB9A38, v106, v89, v90); /*0x726148*/
    len = v139._r1; /*0x72614c*/
    v117 = v139._r0; /*0x726150*/
    v40 = (_QWORD *)runtime_mapassign_faststr(&RTYPE_map_string_string, v120); /*0x72616c*/
    v40[1] = len; /*0x726174*/
    if ( dword_BE34B0 ) /*0x726180*/
    {
      v40 = (_QWORD *)runtime_gcWriteBarrier2(v40)._r0; /*0xf1c00000000000c8*/
      v41 = v117; /*0x726190*/
      *v42 = v117; /*0x726194*/
      v42[1] = *v40; /*0x72619c*/
    }
    else
    {
      v41 = v117; /*0x726184*/
    }
    *v40 = v41; /*0x7261a0*/
    v15 = v120; /*0x7261a4*/
  }
  memset(v116, 0, sizeof(v116)); /*0x7261b8*/
  runtime_mapiterinit(&RTYPE_map_string_string, v15, v116); /*0x7261d0*/
  while ( v116[0] ) /*0x726244*/
  {
    v48 = *(_QWORD *)(v116[0] + 8LL); /*0x726248*/
    v49 = v48 + *(_QWORD *)(v116[1] + 8LL) + 3; /*0x726258*/
    if ( v49 <= 0xFFFF ) /*0x726264*/
    {
      v76 = *(_QWORD *)(v116[1] + 8LL); /*0x726268*/
      *(_QWORD *)v84 = v48; /*0x72626c*/
      v98.str = *(uint8 **)v116[1]; /*0x726274*/
      v104.str = *(uint8 **)v116[0]; /*0x72627c*/
      v71 = v49; /*0x726280*/
      encoding_binary_Write(off_9083A0, v108, off_90E3B0, &unk_BE2EC0, &RTYPE_uint16, &v71); /*0x7262ac*/
      bytes__ptr_Buffer_WriteByte(v108, v84[0]); /*0x7262b8*/
      v140.str = v104.str; /*0x7262c0*/
      v140.len = *(_QWORD *)v84; /*0x7262c4*/
      bytes__ptr_Buffer_WriteString(v108, v140); /*0x7262c8*/
      v141.str = v98.str; /*0x7262d0*/
      v141.len = v76; /*0x7262d4*/
      bytes__ptr_Buffer_WriteString(v108, v141); /*0x7262d8*/
    }
    runtime_mapiternext(v116); /*0x72623c*/
  }
  v50 = v108; /*0x7262e0*/
  v51 = v108->buf.len; /*0x7262e4*/
  v52 = v108->off; /*0x7262e8*/
  v53 = v51 - v52; /*0x7262f0*/
  if ( v51 < v52 ) /*0x7262f8*/
    runtime_panicSliceB(v52); /*0x726434*/
  v54 = &v108->buf.array[v52 & ((__int64)(v52 - v108->buf.cap) >> 63)]; /*0x726310*/
  v55 = v75; /*0x726314*/
  if ( v53 <= v75 ) /*0x72631c*/
    runtime_panicIndex(v75, v53); /*0x726430*/
  v54[v75] = BYTE1(v53); /*0x726324*/
  v56 = v50->buf.len; /*0x726328*/
  v57 = v50->off; /*0x72632c*/
  if ( v57 > v56 ) /*0x726334*/
    runtime_panicSliceB(v50->off); /*0x726428*/
  v58 = v55 + 1; /*0x726338*/
  v59 = v56 - v57; /*0x726344*/
  v60 = (__int64)&v50->buf.array[v57 & ((__int64)(v57 - v50->buf.cap) >> 63)]; /*0x726354*/
  if ( v59 <= v58 ) /*0x72635c*/
    runtime_panicIndex(v58, v59); /*0x726420*/
  *(_BYTE *)(v60 + v58) = v53; /*0x726360*/
  v61 = v50->off; /*0x726368*/
  v62 = v53 + a14; /*0x726370*/
  if ( v61 > v50->buf.len ) /*0x726378*/
    runtime_panicSliceB(v61); /*0x72641c*/
  v63 = v74 + 8; /*0x726380*/
  v64 = v50->buf.cap - v61; /*0x72638c*/
  if ( v64 < v74 + 8 ) /*0x7263a0*/
    runtime_panicSliceAcap(v61, v63); /*0x726418*/
  if ( v74 > v63 ) /*0x7263a8*/
    runtime_panicSliceB(v74); /*0x726414*/
  *(_QWORD *)&v50->buf.array[(v61 & ((__int64)(v61 - v50->buf.cap) >> 63)) /*0x7263c8*/
                           + (v74 & ((__int64)(v94 - (v64 + v93)) >> 63))] = bswap64(v62);
  v65 = v50->buf.len; /*0x7263cc*/
  v66 = v50->off; /*0x7263d0*/
  if ( v66 > v65 ) /*0x7263d8*/
    runtime_panicSliceB(v66); /*0x72640c*/
  result._r2 = v50->buf.cap - v66; /*0x7263e4*/
  result._r1 = v65 - v66; /*0x7263e8*/
  result._r0 = &v50->buf.array[v66 & ((__int64)(v66 - v50->buf.cap) >> 63)]; /*0x7263f4*/
  result._r3 = 0; /*0x7263f8*/
  result._r4 = 0; /*0x7263fc*/
  return result; /*0x7261f4*/
}
```

### 7. `0x7264c0` — `git.teiron-inc.cn/services/backup-cloud/crypto.(*EncryptReader).Read`

- **输入/输出：** 输入：`EncryptReader` 接收者及输出字节切片。输出：本次输出的头部/密文字节数与 `error`。
- **为何位于数据流中：** 它是 `DecryptSliceReader.Read` 的写入端对照：读取明文，CTR 处理后累积 HMAC，并在源 EOF 后追加 16 字节标签。
- **关键调用点：** 关键调用包括输入 reader 的 `Read`、加密器的流变换、`bytes.Buffer.Write`、HMAC `Write` 与 EOF 时的 HMAC `Sum`。

```c
// git.teiron-inc.cn/services/backup-cloud/crypto.(*EncryptReader).Read
retval_7ABA20 __golang git_teiron_inc_cn_services_backup_cloud_crypto__ptr_EncryptReader_Read(
        _ptr_git_teiron_inc_cn_services_backup_cloud_crypto_EncryptReader a1,
        _slice_uint8 a2)
{
  char v2; // w0
  git_teiron_inc_cn_services_backup_cloud_crypto_EncryptReader *v3; // x5
  char v4; // w3
  RTYPE *v5; // x5
  signed __int64 off; // x4
  void *v10; // x4
  uint8 *v11; // x4
  __int64 r1; // x1
  __int64 r2; // x2
  signed __int64 v14; // [xsp+50h] [xbp-58h]
  _slice_uint8 v15; // [xsp+78h] [xbp-30h] BYREF
  __int64 v16; // [xsp+90h] [xbp-18h]
  __int64 v17; // [xsp+98h] [xbp-10h]
  _ptr_git_teiron_inc_cn_services_backup_cloud_crypto_EncryptReader v18; // [xsp+B0h] [xbp+8h]
  uint8 *array; // [xsp+B8h] [xbp+10h]
  signed __int64 len; // [xsp+C0h] [xbp+18h]
  retval_4D6BE0 v21; // 0:x0.16
  retval_7ABA20 result; // 0:x0.24
  _slice_uint8 v23; // 0:x1.24
  _slice_uint8 v24; // 0:x1.24
  retval_726534 v25; // 0:kr40_24.24
  retval_726660 v26; // 0:kr68_24.24

  v18 = a1; /*0x7264e0*/
  if ( a1->buf.buf.len - a1->buf.off <= 0 )
  {
    if ( a1->eof ) /*0x7264f8*/
    {
      result._r1.tab = off_B70F50; /*0x726764*/
      result._r1.data = off_B70F58; /*0x72676c*/
      result._r0 = 0; /*0x726770*/
      return result; /*0x72677c*/
    }
    len = a2.len; /*0x726500*/
    array = a2.array; /*0x726504*/
    a1->buf.buf.len = 0; /*0x72650c*/
    a1->buf.off = 0; /*0x726510*/
    a1->buf.lastRead = 0; /*0x726514*/
    v25 = ((retval_726534 (__golang *)(void *, uint8 *, signed __int64, signed __int64))*((_QWORD *)a1->in.tab + 3))( /*0x726534*/
            a1->in.data,
            a1->cipherBuffer.array,
            a1->cipherBuffer.len,
            a1->cipherBuffer.cap);
    r1 = v25._r1; /*0xf1c0000000000020*/
    r2 = v25._r2; /*0xf1c0000000000024*/
    if ( v25._r0 > 0 ) /*0x726544*/
    {
      if ( v25._r0 > v18->cipherBuffer.cap ) /*0x726554*/
        runtime_panicSliceAcap(v25._r0, v25._r0); /*0x726870*/
      v15.array = (uint8 *)runtime_makeslice(&RTYPE_uint8); /*0x72657c*/
      (*((void (__golang **)(void *, uint8 *))v18->encryptor.tab + 3))(v18->encryptor.data, v15.array); /*0x7265b0*/
      v23.array = v15.array; /*0x7265b8*/
      v23.len = v25._r0; /*0x7265bc*/
      v23.cap = v25._r0; /*0x7265c0*/
      bytes__ptr_Buffer_Write(&v18->buf, v23); /*0x7265c4*/
      (*((void (__golang **)(void *, uint8 *))v18->tagHasher.tab + 7))(v18->tagHasher.data, v15.array); /*0x7265e4*/
      r1 = v25._r1; /*0x7265e8*/
      r2 = v25._r2; /*0x7265ec*/
    }
    if ( (RTYPE **)r1 == off_B70F50 ) /*0x7265fc*/
    {
      v2 = runtime_ifaceeq(r1, r2, off_B70F58); /*0x72661c*/
      r1 = v25._r1; /*0x726620*/
      r2 = v25._r2; /*0x726624*/
    }
    else
    {
      v2 = 0; /*0x726600*/
    }
    if ( (v2 & 1) != 0 ) /*0x726628*/
    {
      v3 = v18; /*0x72662c*/
      if ( v18->buf.buf.len == v18->buf.off ) /*0x72663c*/
      {
        v18->eof = 1; /*0x726644*/
        v26 = ((retval_726660 (__golang *)(void *, _QWORD, _QWORD, _QWORD))*((_QWORD *)v18->tagHasher.tab + 6))( /*0x726660*/
                v18->tagHasher.data,
                0,
                0,
                0);
        if ( v26._r2 < 0x10u ) /*0x726668*/
          runtime_panicSliceAcap(v26._r0, 16); /*0x726864*/
        v24.array = (uint8 *)v26._r0; /*0x72666c*/
        v24.cap = v26._r2; /*0x726670*/
        v24.len = 16; /*0x726678*/
        bytes__ptr_Buffer_Write(&v18->buf, v24); /*0x72667c*/
        r1 = v25._r1; /*0x726680*/
        r2 = v25._r2; /*0x726684*/
        v3 = v18; /*0x726688*/
      }
    }
    else
    {
      v3 = v18; /*0x726690*/
    }
    if ( r1 ) /*0x726694*/
    {
      if ( (RTYPE **)r1 == off_B70F50 ) /*0x7266a4*/
      {
        v4 = runtime_ifaceeq(r1, r2, off_B70F58) ^ 1; /*0x7266c8*/
        r1 = v25._r1; /*0x7266cc*/
        r2 = v25._r2; /*0x7266d0*/
        v3 = v18; /*0x7266d4*/
      }
      else
      {
        v4 = 1; /*0x7266a8*/
      }
    }
    else
    {
      v4 = 0; /*0x7266dc*/
    }
    if ( (v4 & 1) != 0 )
    {
      v15.len = 0; /*0x7266e4*/
      v15.cap = 0; /*0x7266e4*/
      v16 = 0; /*0x7266e8*/
      v17 = 0; /*0x7266e8*/
      v5 = (RTYPE *)off_B71550; /*0x7266f0*/
      if ( off_B71550 ) /*0x7266f4*/
        v5 = off_B71550[1]; /*0x7266f8*/
      v15.len = (signed __int64)v5; /*0x726704*/
      v15.cap = (signed __int64)off_B71558; /*0x726708*/
      if ( r1 ) /*0x72670c*/
        r1 = *(_QWORD *)(r1 + 8); /*0x726710*/
      v16 = r1; /*0x726714*/
      v17 = r2; /*0x726718*/
      v21 = fmt_Errorf("%w: %v", 6, &v15.len, 2, 2);
      result._r1.data = (void *)v21._r1; /*0x726738*/
      result._r1.tab = (void *)v21._r0; /*0x72673c*/
      result._r0 = 0; /*0x726740*/
      return result; /*0x72674c*/
    }
    a1 = v3; /*0x726750*/
    a2.array = array; /*0x726754*/
    a2.len = len; /*0x726758*/
  }
  a1->buf.lastRead = 0; /*0x726780*/
  a2.cap = a1->buf.buf.len; /*0x726784*/
  off = a1->buf.off; /*0x726788*/
  if ( off < a2.cap ) /*0x726790*/
  {
    if ( (unsigned __int64)off > a2.cap ) /*0x7267d0*/
      runtime_panicSliceB(a1->buf.off); /*0x72685c*/
    a2.cap -= off; /*0x7267d4*/
    v11 = &a1->buf.buf.array[off & ((off - a1->buf.buf.cap) >> 63)]; /*0x7267ec*/
    if ( a2.len > a2.cap ) /*0x7267f4*/
      a2.len = a2.cap; /*0x7267f4*/
    if ( a2.array != v11 ) /*0x7267fc*/
    {
      v14 = a2.len; /*0x726800*/
      runtime_memmove(a2.array, v11, a2.len); /*0x72680c*/
      a1 = v18; /*0x726810*/
      a2.len = v14; /*0x726814*/
    }
    a1->buf.off += a2.len; /*0x726820*/
    if ( a2.len > 0 ) /*0x726828*/
      a1->buf.lastRead = -1; /*0x726830*/
    a2.cap = 0; /*0x726834*/
    v10 = nullptr; /*0x726838*/
  }
  else
  {
    a1->buf.buf.len = 0; /*0x726798*/
    a1->buf.off = 0; /*0x72679c*/
    a1->buf.lastRead = 0; /*0x7267a0*/
    if ( a2.len ) /*0x7267a4*/
    {
      a2.cap = (signed __int64)off_B70F50; /*0x7267bc*/
      v10 = off_B70F58; /*0x7267c4*/
      a2.len = 0; /*0x7267c8*/
    }
    else
    {
      a2.len = 0; /*0x7267a8*/
      a2.cap = 0; /*0x7267ac*/
      v10 = nullptr; /*0x7267b0*/
    }
  }
  result._r0 = a2.len; /*0x72683c*/
  result._r1.tab = (void *)a2.cap; /*0x726840*/
  result._r1.data = v10; /*0x726844*/
  return result; /*0x72674c*/
}
```

### 8. `0x7268b0` — `git.teiron-inc.cn/services/backup-cloud/crypto.encryptHeaderLength`

- **输入/输出：** 输入：基础长度和可选字符串/字段长度。输出：计算出的加密头长度（另一个返回槽保留输入值）。
- **为何位于数据流中：** 该辅助函数说明头部长度会随可选 Base64/元数据字段变化，可用于理解恢复端从头部取得的长度边界。
- **关键调用点：** 无高层外部调用；核心是按照 Base64 编码长度和固定字段长度进行整数计算。

```c
// git.teiron-inc.cn/services/backup-cloud/crypto.encryptHeaderLength
retval_7268B0 __golang git_teiron_inc_cn_services_backup_cloud_crypto_encryptHeaderLength(__int64 a1, __int64 a2)
{
  int v2; // w3
  bool v3; // zf
  __int64 v4; // x3
  __int64 v5; // x3
  __int64 v6; // x2
  __int64 v7; // x3
  __int64 v8; // x1
  retval_7268B0 result; // 0:x0.8,8:^8.8

  *(_QWORD *)result._r1 = a1; /*0x7268b0*/
  if ( a2 ) /*0x7268bc*/
  {
    v2 = *(_DWORD *)(qword_BB9A38 + 320); /*0x7268c8*/
    v3 = v2 == -1; /*0x7268d4*/
    if ( v2 == -1 ) /*0x7268d8*/
    {
      v4 = ((__int64)(qword_B73568 /*0x726900*/
                    + ((unsigned __int128)((qword_B73568 + 16) * (__int128)(__int64)0xAAAAAAAAAAAAAAABLL) >> 64)
                    + 16) >> 1)
         - ((qword_B73568 + 16) >> 63);
      v5 = ((__int64)(((unsigned __int128)((8 * (qword_B73568 - 3 * v4 + 16) + 5) /*0x72692c*/
                                         * (__int128)(__int64)0xAAAAAAAAAAAAAAABLL) >> 64)
                    + 8 * (qword_B73568 - 3 * v4 + 16)
                    + 5) >> 2)
         - ((8 * (qword_B73568 - 3 * v4 + 16) + 5) >> 63)
         + 4 * v4;
    }
    else
    {
      v5 = 4 /*0x72695c*/
         * (((__int64)(qword_B73568
                     + ((unsigned __int128)((qword_B73568 + 18) * (__int128)(__int64)0xAAAAAAAAAAAAAAABLL) >> 64)
                     + 18) >> 1)
          - ((qword_B73568 + 18) >> 63));
    }
    v6 = MEMORY[0xB73528] + v5; /*0x726970*/
    if ( v3 ) /*0x726974*/
    {
      v7 = ((__int64)(a2 + ((unsigned __int128)((a2 + 16) * (__int128)(__int64)0xAAAAAAAAAAAAAAABLL) >> 64) + 16) >> 1) /*0x72698c*/
         - ((a2 + 16) >> 63);
      v8 = ((__int64)(((unsigned __int128)((8 * (a2 - 3 * v7 + 16) + 5) * (__int128)(__int64)0xAAAAAAAAAAAAAAABLL) >> 64) /*0x7269b8*/
                    + 8 * (a2 - 3 * v7 + 16)
                    + 5) >> 2)
         - ((8 * (a2 - 3 * v7 + 16) + 5) >> 63)
         + 4 * v7;
    }
    else
    {
      v8 = 4 /*0x7269d8*/
         * (((__int64)(a2 + ((unsigned __int128)((a2 + 18) * (__int128)(__int64)0xAAAAAAAAAAAAAAABLL) >> 64) + 18) >> 1)
          - ((a2 + 18) >> 63));
    }
    result._r0 = v8 + v6 + 60; /*0x7269e0*/
  }
  else
  {
    result._r0 = MEMORY[0xB73528] + 37; /*0x7269e8*/
  }
  return result; /*0x7269e4*/
}
```

### 9. `0x74e690` — `git.teiron-inc.cn/services/backup-cloud/worker/download.Download`

- **输入/输出：** 输入：访问令牌、远程 URL、目标路径指针、User-Agent、期望资源大小、口令字符串及可选 HTTP 头。输出：下载状态码与错误接口。
- **为何位于数据流中：** 恢复下载的主控函数：创建 HTTP 请求、区分普通与 `.fot` 文件；在 `.fot` 且口令存在时建立解密读取器，创建目标文件并把解密流复制到其中。
- **关键调用点：** 关键调用：`NewDecryptReader` 位于 `0x74f31c`；`os_OpenFile` 在 `0x74f4d0` 打开目标文件；随后 `CopyBuffer` 在 `0x74f5d0` 才驱动流式解密和写入。成功后还调用 `checkFileSize`。

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

### 10. `0x757540` — `git.teiron-inc.cn/services/backup-cloud/worker.(*Executor).DownloadFile`

- **输入/输出：** 输入：`Executor`、远程路径字符串和对象 ID 字符串。输出：无直接返回值；通过成功/失败结果打包函数报告。
- **为何位于数据流中：** 这是执行器层入口之一：规范化路径、询问驱动下载链接并调用 `worker/download.Download`，将结果转换为任务结果。
- **关键调用点：** 关键调用包括驱动的链接获取方法、`worker/download.Download`（`0x7577c4` 或 `0x7577fc`）以及成功/失败结果打包函数。

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

### 11. `0x760120` — `git.teiron-inc.cn/services/backup-cloud/worker.(*Worker).downloadRemoteFile`

- **输入/输出：** 输入：`Worker`、远程路径和对象 ID、目标路径指针、User-Agent、期望大小、口令及 HTTP 头相关参数。输出：状态码与错误接口。
- **为何位于数据流中：** 这是工作器层的另一入口：取得下载链接、清理已有目标、调用下载例程并按状态重试或清理失败的下载。
- **关键调用点：** 关键调用包括 `getDriver`、驱动链接获取、`worker/download.Download`（`0x760774`/`0x7607b4`）和失败时的 `os_Remove`。

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

### 12. `0x74e2a0` — `git.teiron-inc.cn/services/backup-cloud/worker/download.CopyBuffer`

- **输入/输出：** 输入：目标 writer、调用方传入的大小参数、源 reader 的接口字与数据。输出：状态码与错误接口。
- **为何位于数据流中：** 它用 `io.CopyBuffer` 把普通下载体或解密 reader 的输出复制至目标文件；在 FOT 分支中，正是它反复请求解密 reader 的数据并使 EOF 时的尾 HMAC 验证实际发生。
- **关键调用点：** 调用 `io_CopyBuffer`（`0x74e308`）；复制错误若匹配 `off_B71530/B71538` 或 `off_B71540/B71548`，映射为状态 `17`，否则映射为 `11`。

以下为工作目录中保存的 `CopyBuffer` 完整 IDA 反编译实际文本。

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

## 附录覆盖范围与局限

本附录覆盖所保存的 12 个逐函数 IDA 反编译文本。代码块是静态保存的 IDA 输出，不是经编译器验证的 Go 源码；类型、参数语义、局部变量名和控制流仍受 Go ABI 还原质量限制。为保证完整性，Go 运行时样板（包括写屏障、切片边界检查、接口分派和 panic 路径）也完整保留，故不应将其误判为业务算法本身。本附录仅整理现有文本：未运行样本，未修改其他文件，亦未进行口令猜测。
