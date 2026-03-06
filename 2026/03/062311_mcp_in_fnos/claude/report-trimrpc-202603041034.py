#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trimrpc Client - fnOS trimrpc RPC 协议的 Python 实现
=====================================================

基于对 libtrimrpc.so.0.1.3 和 libframework.so.2.9 的逆向分析实现。
用于在本机通过 Unix Domain Socket 调用 fnOS 的 trimrpc RPC 服务。

用法:
  # 扫描系统中的 UDS socket:
  ./report-trimrpc-202603041034.py --scan

  # 通过 RPC Broker 自动发现服务并调用:
  ./report-trimrpc-202603041034.py '{"service":"com.trim.storage","method":"get_info","params":{}}'

  # 指定 Broker 路径:
  ./report-trimrpc-202603041034.py --broker-uds /tmp/rpc/rpcbroker \
      '{"service":"com.trim.storage","method":"get_info"}'

  # 直接指定 UDS 路径调用 (跳过 Broker):
  ./report-trimrpc-202603041034.py --uds /var/run/rpc/storage \
      '{"service":"com.trim.storage","method":"get_info","params":{}}'

  # 仅查询 Broker 获取服务信息:
  ./report-trimrpc-202603041034.py --discover com.trim.storage com.trim.main

  # 指定调用方身份:
  ./report-trimrpc-202603041034.py --uuid "EECC6649-92CB-4246-A76A-F14358F13C07" \
      --caller "com.trim.eventlogger" '{"service":"com.trim.resmon","method":"check"}'

  # 调整超时 (默认10秒):
  ./report-trimrpc-202603041034.py --timeout 30 '{"service":"com.trim.main","method":"ping"}'

协议参考:
  libtrimrpc.so.0.1.3  RpcPacket 二进制协议 (30 字节头部)
  libframework.so.2.9  ServiceHelper Broker 通信
  详见 report-trimrpc-202603041034.md
"""

import struct
import socket
import json
import sys
import os
import time
import uuid
import argparse
import threading

# =============================================================================
# 常量定义 (来自反汇编 / 反编译)
# =============================================================================

# RpcPacket::RpcPacket() @ libtrimrpc.so:0x1ddf0
#   *((_DWORD *)this + 2) = 1414680643;   // 0x54525043 = "CPRT" LE
#   *((_WORD *)this + 6) = 1;             // version = 1
CPRT_MAGIC = 0x54525043      # 协议魔数 "CPRT" (little-endian)
PROTOCOL_VERSION = 1          # 协议版本
HEADER_SIZE = 30              # 30 字节固定头部

# RpcConnections::InitServiceMap() @ libtrimrpc.so:0x19520
#   默认注册 "com.trim.rpcbroker" 服务
# 注意: 实际路径因系统而异, fnOS 实际使用:
#   - /run/trim_app_cgi/rpcbroker (主路径)
#   - /var/run/trim_app_cgi/rpcbroker (符号链接)
BROKER_UDS_PATH = "/run/trim_app_cgi/rpcbroker"
BROKER_SERVICE = "com.trim.rpcbroker"

# 备选 Broker 路径 (按优先级尝试)
BROKER_UDS_PATHS = [
    "/run/trim_app_cgi/rpcbroker",      # fnOS 实际路径
    "/var/run/trim_app_cgi/rpcbroker",  # fnOS 符号链接
    "/var/run/rpc/rpcbroker",            # 推测路径 (未使用)
    "/tmp/rpc/rpcbroker",
    "/run/rpc/rpcbroker",
    "/var/tmp/rpc/rpcbroker",
]

# Caller::Caller() @ libtrimrpc.so:0x12b60
#   *((_DWORD *)this + 16) = 10;          // 默认超时 10 秒
DEFAULT_TIMEOUT = 10

# Caller::CallOutInSync() @ libtrimrpc.so:0x13cb0
#   pthread_cond_clockwait 超时后 error_code = 100
ERR_TIMEOUT = 100

# RpcClient::VerifyServiceId() @ libtrimrpc.so:0x256c0
#   服务名必须以 "com." 开头, 长度 <= 128
SERVICE_PREFIX = "com."
MAX_SERVICE_NAME_LEN = 128

# RpcConnections::Send() @ libtrimrpc.so:0x192d0
#   发送失败后 callback(session, 102, "")
ERR_SEND_FAILED = 102

# =============================================================================
# 全局会话 ID 计数器 & 调试标志
# =============================================================================

# Caller 中使用全局原子递增计数器生成 session_id:
# Caller::BuildRpcPacket() @ libtrimrpc.so:0x14840
#   *(_QWORD *)(a2 + 112) = _InterlockedIncrement64(trimrpc::Caller::session_id_);
#   *(_QWORD *)(v9 + 32) = *(_QWORD *)(a2 + 112);  // 写入 RpcPacket.session_id
_session_id_counter = 0
_session_id_lock = threading.Lock()

# 全局调试标志 (通过 --verbose 设置)
_debug_enabled = False


def _next_session_id():
    """
    生成下一个全局唯一会话 ID。
    对应 libtrimrpc.so 中 Caller::session_id_ 的原子递增:
      _InterlockedIncrement64(trimrpc::Caller::session_id_)
    """
    global _session_id_counter
    with _session_id_lock:
        _session_id_counter += 1
        return _session_id_counter


def _debug(msg):
    """输出调试信息到 stderr"""
    if _debug_enabled:
        print(f"[DEBUG] {msg}", file=sys.stderr)


# =============================================================================
# RpcPacket — RPC 二进制数据包
# =============================================================================

class RpcPacket:
    """
    trimrpc 二进制数据包，对应 trimrpc::RpcPacket 类。

    头部格式 (30 字节):
    ---------------------------------------------------------------
    从 RpcPacket::DeserializeHeader() @ libtrimrpc.so:0x1dea0 反汇编确认:

      1dea0  mov     eax, [rsi]          ; [0:4]   magic      (uint32 LE)
      1dea5  movzx   eax, word ptr [rsi+4]; [4:6]   version    (uint16 LE)
      1dead  mov     rax, [rsi+6]        ; [6:14]  session_id (uint64 LE)
      1deb5  movzx   eax, word ptr [rsi+0Eh]; [14:16] data_len (uint16 LE)
      1debd  movzx   eax, word ptr [rsi+10h]; [16:18] token_len (uint16 LE)
      1dec5  mov     eax, [rsi+12h]      ; [18:22] extra_data_len (uint32 LE)
      1decb  mov     eax, [rsi+16h]      ; [22:26] reserved   (uint32 LE)
      1ded1  mov     eax, [rsi+1Ah]      ; [26:30] extra_data2_len (uint32 LE)

    struct pack 格式: '<IHQHHIII'  (30 bytes, no padding with '<')

    数据段布局 (紧跟头部):
    ---------------------------------------------------------------
    从 RpcPacket::Serialize() @ libtrimrpc.so:0x1dc30 确认:
    从 RpcPacket::DeserializeData() @ libtrimrpc.so:0x1dee0 确认:

      Section 0: data       (data_len bytes)       — 通常存放 UUID
      Section 1: token      (token_len bytes)      — 认证令牌
      Section 2: extra_data (extra_data_len bytes)  — JSON 载荷
      Section 3: extra_data2(extra_data2_len bytes) — 扩展数据

    关键发现:
    ---------------------------------------------------------------
    RpcPacket::data() 虚函数 @ libtrimrpc.so:0x1ec50 实际返回 extra_data (Section 2):
      v2 = *(_QWORD *)(a2 + 112);     // extra_data.length (offset 112)
      v5 = *(_BYTE **)(a2 + 104);     // extra_data.ptr    (offset 104)
    因此 JSON 载荷在 Section 2 (extra_data)，而非 Section 0 (data)。

    Caller::BuildRpcPacket() @ libtrimrpc.so:0x14840 验证:
      UUID  → Section 0 (data)      :  *(_WORD *)(v9 + 40) = v15;  // data_len
      JSON  → Section 2 (extra_data):  *(_DWORD *)(v9 + 44) = n;   // extra_data_len
    """

    # struct 格式: magic(I) + version(H) + session_id(Q) +
    #              data_len(H) + token_len(H) +
    #              extra_data_len(I) + reserved(I) + extra_data2_len(I)
    HEADER_FMT = '<IHQHHIII'

    def __init__(self):
        self.magic = CPRT_MAGIC
        self.version = PROTOCOL_VERSION
        self.session_id = 0
        # 4 个数据段, 对应 RpcPacket 对象中的 4 个 std::string
        # 对象偏移: data@40, token@72, extra_data@104, extra_data2@136
        self.data = b''          # Section 0: UUID / 标识数据
        self.token = b''         # Section 1: 认证令牌
        self.extra_data = b''    # Section 2: JSON 载荷 (核心数据)
        self.extra_data2 = b''   # Section 3: 扩展数据

    def serialize(self):
        """
        序列化为二进制字节流。
        对应 RpcPacket::Serialize() @ libtrimrpc.so:0x1dc30

        线格式: [30字节头部] + [data] + [token] + [extra_data] + [extra_data2]

        Serialize 中头部写入顺序 (v13 指向 buffer+30):
          *(_DWORD *)(v13 - 30) = magic;           // [0:4]
          *((_WORD *)v13 - 13)  = version;          // [4:6]
          *((_QWORD *)v13 - 3)  = session_id;       // [6:14]
          *((_WORD *)v13 - 8)   = data_len;          // [14:16]
          *((_WORD *)v13 - 7)   = token_len;         // [16:18]
          *((_DWORD *)v13 - 3)  = extra_data_len;    // [18:22]
          *((_DWORD *)v13 - 2)  = reserved;          // [22:26]
          *((_WORD *)v13 - 2)   = extra_data2_len;   // [26:28] (低16位)
        """
        header = struct.pack(
            self.HEADER_FMT,
            self.magic,              # [0:4]   uint32 magic = 0x54525043
            self.version,            # [4:6]   uint16 version = 1
            self.session_id,         # [6:14]  uint64 session_id
            len(self.data),          # [14:16] uint16 data_len
            len(self.token),         # [16:18] uint16 token_len
            len(self.extra_data),    # [18:22] uint32 extra_data_len
            0,                       # [22:26] uint32 reserved = 0
            len(self.extra_data2),   # [26:30] uint32 extra_data2_len
        )
        assert len(header) == HEADER_SIZE, f"Header size mismatch: {len(header)}"
        return header + self.data + self.token + self.extra_data + self.extra_data2

    @classmethod
    def deserialize(cls, raw_bytes):
        """
        从二进制字节流反序列化。
        对应 RpcPacket::DeserializeHeader() @ libtrimrpc.so:0x1dea0
             RpcPacket::DeserializeData()   @ libtrimrpc.so:0x1dee0

        DeserializeHeader 从 buffer 读取 30 字节:
          [rsi+0]  → magic       (4 bytes, mov eax, [rsi])
          [rsi+4]  → version     (2 bytes, movzx word)
          [rsi+6]  → session_id  (8 bytes, mov rax, [rsi+6])
          [rsi+0E] → data_len    (2 bytes, movzx word)
          [rsi+10] → token_len   (2 bytes, movzx word)
          [rsi+12] → extra_data_len  (4 bytes, mov eax)
          [rsi+16] → reserved        (4 bytes, mov eax)
          [rsi+1A] → extra_data2_len (4 bytes, mov eax)

        DeserializeData 按照头部中的长度字段依次读取 4 个数据段:
          Section 0 (data)       → *(QWORD*)this+5  (offset 40)
          Section 1 (token)      → *(QWORD*)this+9  (offset 72)
          Section 2 (extra_data) → *(QWORD*)this+13 (offset 104)
          Section 3 (extra_data2)→ *(QWORD*)this+17 (offset 136)
        """
        if len(raw_bytes) < HEADER_SIZE:
            raise ValueError(
                f"数据不足: 需要至少 {HEADER_SIZE} 字节头部, "
                f"实际收到 {len(raw_bytes)} 字节"
            )

        pkt = cls()
        (
            pkt.magic,
            pkt.version,
            pkt.session_id,
            data_len,
            token_len,
            extra_data_len,
            _reserved,
            extra_data2_len,
        ) = struct.unpack(cls.HEADER_FMT, raw_bytes[:HEADER_SIZE])

        if pkt.magic != CPRT_MAGIC:
            raise ValueError(
                f"魔数不匹配: 期望 0x{CPRT_MAGIC:08X} (CPRT), "
                f"实际 0x{pkt.magic:08X}"
            )

        # 按顺序读取 4 个数据段 (与 DeserializeData 一致)
        offset = HEADER_SIZE
        total_needed = offset + data_len + token_len + extra_data_len + extra_data2_len
        if len(raw_bytes) < total_needed:
            raise ValueError(
                f"数据不足: 头部声明需要 {total_needed} 字节, "
                f"实际收到 {len(raw_bytes)} 字节"
            )

        pkt.data = raw_bytes[offset:offset + data_len]
        offset += data_len

        pkt.token = raw_bytes[offset:offset + token_len]
        offset += token_len

        pkt.extra_data = raw_bytes[offset:offset + extra_data_len]
        offset += extra_data_len

        pkt.extra_data2 = raw_bytes[offset:offset + extra_data2_len]

        return pkt

    def get_json_payload(self):
        """
        提取 JSON 载荷。对应 RpcPacket::data() 虚函数 @ libtrimrpc.so:0x1ec50
        该函数返回 extra_data (Section 2, 对象偏移 104/112):
          v2 = *(_QWORD *)(a2 + 112);  // extra_data.length
          v5 = *(_BYTE **)(a2 + 104);  // extra_data.ptr
        """
        if self.extra_data:
            return self.extra_data.decode('utf-8', errors='replace')
        # 回退: 某些响应可能将数据放在 Section 0
        if self.data:
            return self.data.decode('utf-8', errors='replace')
        return ''

    def __repr__(self):
        return (
            f"RpcPacket(magic=0x{self.magic:08X}, ver={self.version}, "
            f"session=0x{self.session_id:X}, "
            f"data[{len(self.data)}], token[{len(self.token)}], "
            f"extra_data[{len(self.extra_data)}], extra_data2[{len(self.extra_data2)}])"
        )


# =============================================================================
# UDS 通信层
# =============================================================================

def _recv_all(sock, size, timeout=DEFAULT_TIMEOUT):
    """
    从 socket 精确读取 size 字节。

    对应 RpcPacketProcessor::AppendAndParse() @ libtrimrpc.so:0x1cec0 中的
    数据流式读取逻辑:
      1. 先读 30 字节头部: std::istream::read(this + 3, buffer, 30)
      2. 计算 data_length: virtual data_length() 返回所有段总长度
      3. 再读 data_length 字节: std::istream::read(this + 3, buffer, data_length)
    """
    sock.settimeout(timeout)
    buf = b''
    while len(buf) < size:
        try:
            chunk = sock.recv(size - len(buf))
        except socket.timeout:
            raise TimeoutError(
                f"接收超时 ({timeout}s): 已收到 {len(buf)}/{size} 字节"
            )
        if not chunk:
            raise ConnectionError(
                f"连接断开: 已收到 {len(buf)}/{size} 字节"
            )
        buf += chunk
    return buf


def send_rpc_packet(uds_path, packet, timeout=DEFAULT_TIMEOUT):
    """
    通过 Unix Domain Socket 发送 RpcPacket 并接收响应。

    对应两个实现:

    1) ServiceHelper::CallBroker() @ libframework.so:0x5e1b0 (简化版):
       sockfd = socket(AF_UNIX, SOCK_STREAM, 0);
       addr.sun_family = AF_UNIX;
       strcpy(addr.sun_path, "/var/run/rpc/rpcbroker");
       connect(sockfd, &addr, sizeof(addr));
       send(sockfd, serialized.data(), serialized.size(), 0);
       recv(sockfd, response_buffer, sizeof(response_buffer), 0);
       close(sockfd);

    2) RpcConnections::Connect() @ libtrimrpc.so:0x1a920 (完整版):
       neCreateSocket(ne_instance, 1, 0);
       neConnect(inst, sock, uds_path, processor);
       // 使用 ne* 网络引擎管理连接

    本函数采用方法 1 的直连方式, 更简单可靠。
    """
    _debug(f"连接 UDS: {uds_path}")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)

    try:
        sock.connect(uds_path)
        _debug(f"连接成功")
    except FileNotFoundError:
        raise ConnectionError(
            f"UDS 路径不存在: {uds_path}\n"
            f"提示: RPC 服务可能未运行，或路径不正确。\n"
            f"      请检查系统中的 UDS socket: find /var/run /tmp /run -name '*rpc*' -type s 2>/dev/null"
        )
    except PermissionError:
        raise ConnectionError(f"无权限连接 UDS: {uds_path}")
    except socket.timeout:
        raise TimeoutError(f"连接超时: {uds_path}")

    try:
        # 发送序列化的 RpcPacket
        raw = packet.serialize()
        _debug(f"发送数据包: {len(raw)} 字节, session_id={packet.session_id}")
        _debug(f"  data(Section 0): {len(packet.data)} 字节")
        _debug(f"  token(Section 1): {len(packet.token)} 字节")
        _debug(f"  extra_data(Section 2): {len(packet.extra_data)} 字节")
        if packet.extra_data:
            try:
                json_str = packet.extra_data.decode('utf-8')
                _debug(f"  JSON载荷: {json_str}")
            except:
                pass

        sock.sendall(raw)

        # ── 接收响应 ──
        # 对应 RpcPacketProcessor::AppendAndParse 的两阶段读取:
        # 阶段 1: 读取 30 字节头部
        _debug("等待响应...")
        resp_header = _recv_all(sock, HEADER_SIZE, timeout)

        # 解析头部获取各段长度
        (
            _magic, _ver, _session,
            data_len, token_len,
            extra_data_len, _reserved, extra_data2_len
        ) = struct.unpack(RpcPacket.HEADER_FMT, resp_header)

        _debug(f"收到响应头: session={_session}, data_len={data_len}, token_len={token_len}, extra_data_len={extra_data_len}")

        # data_length() @ libtrimrpc.so:0x1db60:
        #   return *(DWORD*)(this+9) + *(DWORD*)(this+7) +
        #          *(uint16*)(this+13) + *(uint16*)(this+12);
        #   即: extra_data2_len + extra_data_len + token_len + data_len
        total_data = data_len + token_len + extra_data_len + extra_data2_len

        # 阶段 2: 读取所有数据段
        resp_body = b''
        if total_data > 0:
            resp_body = _recv_all(sock, total_data, timeout)
            _debug(f"收到响应体: {len(resp_body)} 字节")

        resp_pkt = RpcPacket.deserialize(resp_header + resp_body)

        if resp_pkt.extra_data:
            try:
                json_str = resp_pkt.extra_data.decode('utf-8')
                _debug(f"响应JSON: {json_str}")
            except:
                pass

        return resp_pkt

    finally:
        sock.close()


# =============================================================================
# 请求构建
# =============================================================================

def build_reqid():
    """
    生成 reqid: 当前时间戳转 16 字符十六进制字符串。

    对应 Caller::BuildRpcPacket() @ libtrimrpc.so:0x14840 中的:
      v40 = std::chrono::_V2::system_clock::now();
      // 通过查表 xmmword_29980 ("0123456789abcdef") 将时间戳
      // 逐 nibble 转换为十六进制字符, 生成 16 字节字符串
    """
    ts = int(time.time() * 1000000)  # 微秒级时间戳
    return format(ts & 0xFFFFFFFFFFFFFFFF, '016x')


def build_rpc_request(service, method, params=None,
                      caller_uuid=None, caller_service=None):
    """
    构建 RPC 请求数据包。

    对应 Caller::BuildRpcPacket() @ libtrimrpc.so:0x14840 的完整流程:

    1. 创建 RpcPacket (magic=CPRT, version=1):
       v9 = operator new(0xB8u);
       trimrpc::RpcPacket::RpcPacket((trimrpc::RpcPacket *)(v9 + 16));

    2. 构建 JSON 请求:
       PPJson doc;
       doc["data"]["req"] = "<service>.<method>";   // 完整方法名
       doc["data"]["pid"] = getpid();                // 调用方 PID
       doc["data"]["reqid"] = hex(timestamp);        // 十六进制时间戳

    3. JSON 序列化后存入 extra_data (Section 2):
       *(_QWORD *)(v9 + 128) = v60;     // extra_data.length = json.length
       *(_DWORD *)(v9 + 44) = n;        // header.extra_data_len = json.length

    4. UUID 存入 data (Section 0):
       // 从 Caller 对象复制 UUID 到 RpcPacket 的 data 字段
       *(_QWORD *)(v9 + 64) = v15;      // data.length = uuid.length
       *(_WORD *)(v9 + 40) = v15;       // header.data_len = uuid.length

    5. 设置 session_id (原子递增):
       *(_QWORD *)(v9 + 32) = _InterlockedIncrement64(session_id_);

    参数:
      service:        目标服务名, 如 "com.trim.storage"
      method:         方法名, 如 "get_info"
      params:         请求参数 dict (可选)
      caller_uuid:    调用方 UUID (可选, 默认随机生成)
      caller_service: 调用方服务名 (可选, 默认 "com.trim.pyclient")
    """
    # ── 验证服务名 ──
    # 对应 RpcClient::VerifyServiceId() @ libtrimrpc.so:0x256c0:
    #   if (service.length() > 128) return 5;
    #   if (!service.starts_with("com.")) return 6;
    if len(service) > MAX_SERVICE_NAME_LEN:
        raise ValueError(f"服务名超过 {MAX_SERVICE_NAME_LEN} 字节 (错误码 5)")
    if not service.startswith(SERVICE_PREFIX):
        raise ValueError(f'服务名必须以 "{SERVICE_PREFIX}" 开头 (错误码 6)')

    # ── 构建 JSON 载荷 ──
    # 格式: {"data": {"req": "service.method", "pid": N, "reqid": "hex16"}}
    req_name = f"{service}.{method}"
    json_payload = {
        "data": {
            "req": req_name,
            "pid": os.getpid(),
            "reqid": build_reqid(),
        }
    }

    # 合并用户参数到 data 字段
    if params:
        json_payload["data"].update(params)

    json_str = json.dumps(json_payload, separators=(',', ':'))

    # ── 构建 RpcPacket ──
    pkt = RpcPacket()
    pkt.session_id = _next_session_id()

    # Section 0 (data): 调用方 UUID
    if caller_uuid is None:
        caller_uuid = str(uuid.uuid4()).upper()
    pkt.data = caller_uuid.encode('utf-8')

    # Section 1 (token): 留空 (或可由 ApplyPermission 获取的令牌填充)
    pkt.token = b''

    # Section 2 (extra_data): JSON 载荷 (核心数据)
    pkt.extra_data = json_str.encode('utf-8')

    # Section 3 (extra_data2): 留空
    pkt.extra_data2 = b''

    return pkt


# =============================================================================
# RpcClient — 高层 RPC 客户端
# =============================================================================

class RpcClient:
    """
    trimrpc RPC 客户端, 对应 trimrpc::RpcClient 类。

    RpcClient 对象结构 @ libtrimrpc.so:0x259b0:
      对象大小: 0x88 (136) 字节
      偏移 0:  Caller*        内部调用器
      偏移 8:  std::string    UUID (调用者标识)
      偏移 40: std::string    service_name (调用方服务名)
      偏移 72: int32          error_code
      偏移 80: std::string    response_string
      偏移 112: shared_ptr    response (Response 对象)
      偏移 128: int32         last_call_error
      偏移 132: int32         pid

    使用流程:
      1. 构造 RpcClient(uuid, service_name)
      2. ApplyPermission(target_services) — 从 Broker 获取服务路由信息
      3. Call(service, method, data) — 发送 RPC 请求
    """

    def __init__(self, caller_uuid=None, caller_service="com.trim.pyclient",
                 timeout=DEFAULT_TIMEOUT, broker_uds=None):
        """
        初始化 RPC 客户端。

        对应 RpcClient::RpcClient() @ libtrimrpc.so:0x259b0

        参数:
          broker_uds: 指定 Broker UDS 路径 (可选, 默认自动探测)
        """
        self.uuid = caller_uuid or str(uuid.uuid4()).upper()
        self.caller_service = caller_service
        self.timeout = timeout

        # 自动探测 Broker UDS 路径
        if broker_uds:
            self.broker_uds = broker_uds
        else:
            self.broker_uds = self._find_broker_uds()

        # 服务路由表: service_name → {uds, ip, type, token, ...}
        # 对应 RpcConnections 中的 RB-tree: service_name → ServiceInfo
        # RpcConnections::AddService() @ libtrimrpc.so:0x1a0e0
        self.services = {
            # 默认注册 Broker 服务
            # RpcConnections::InitServiceMap() @ libtrimrpc.so:0x19520
            BROKER_SERVICE: {
                "name": BROKER_SERVICE,
                "uds": self.broker_uds,
                "type": 0,
                "token": "",
            }
        }

        self.last_error = 0
        self.last_response = None

    def _find_broker_uds(self):
        """
        自动探测 Broker UDS 路径。
        按优先级尝试常见路径, 返回第一个存在的路径。
        """
        for path in BROKER_UDS_PATHS:
            if os.path.exists(path):
                return path

        # 如果都不存在, 返回默认路径 (后续连接时会报错)
        return BROKER_UDS_PATH

    def apply_permission(self, target_services):
        """
        向 RPC Broker 申请目标服务的访问权限。

        对应 RpcClient::ApplyPermission() @ libtrimrpc.so:0x27740:

        1. 构建 JSON 请求:
           {"data": {"req": "com.trim.rpcbroker.apply",
                     "pid": <pid>, "reqid": "<hex>",
                     "services": ["com.trim.service1", ...]}}

        2. 调用 Call("com.trim.rpcbroker", "apply", json)

        3. 解析响应 (HandleServiceInfo @ libtrimrpc.so:0x26330):
           响应 JSON 格式:
           {"data": {"result": "succ", "data": [
               {"id": "uuid", "name": "com.trim.xxx",
                "uds": "/var/run/rpc/xxx", "ip": "127.0.0.1",
                "type": 0, "token": "auth_token"}
           ]}}

        4. 将服务信息存入路由表:
           RpcConnections::AddService() @ libtrimrpc.so:0x1a0e0

        返回: 服务信息列表
        """
        if isinstance(target_services, str):
            target_services = [target_services]

        _debug(f"ApplyPermission: 请求服务 {target_services}")
        _debug(f"  Broker UDS: {self.broker_uds}")
        _debug(f"  Caller UUID: {self.uuid}")
        _debug(f"  Caller Service: {self.caller_service}")

        # 构建请求
        params = {"services": target_services}
        pkt = build_rpc_request(
            BROKER_SERVICE, "apply", params,
            caller_uuid=self.uuid,
            caller_service=self.caller_service,
        )

        # 发送到 Broker
        resp_pkt = send_rpc_packet(self.broker_uds, pkt, self.timeout)
        resp_json_str = resp_pkt.get_json_payload()

        if not resp_json_str:
            raise RuntimeError("Broker 返回空响应")

        _debug(f"Broker 响应: {resp_json_str}")

        resp = json.loads(resp_json_str)
        resp_data = resp.get("data", resp)

        if resp_data.get("result") != "succ":
            raise RuntimeError(
                f"ApplyPermission 失败: {json.dumps(resp_data, ensure_ascii=False)}"
            )

        # 解析服务列表
        service_list = resp_data.get("data", [])
        _debug(f"收到 {len(service_list)} 个服务信息")

        for svc_info in service_list:
            # 使用 "id" 字段作为服务标识符 (如 "com.trim.main")
            # "name" 字段是人类可读的名称 (如 "TRIM Service")
            service_id = svc_info.get("id", "")
            if service_id:
                self.services[service_id] = svc_info
                _debug(f"  - {service_id}: uds={svc_info.get('uds', 'N/A')}, name={svc_info.get('name', 'N/A')}")

        return service_list

    def call(self, service, method, params=None):
        """
        执行 RPC 调用。

        对应 RpcClient::Call() @ libtrimrpc.so:0x25bf0:

        1. VerifyServiceId(service) — 验证 "com." 前缀和长度
        2. Caller::Call() @ libtrimrpc.so:0x15480:
           a. RpcConnections::Connect(service) — 建立 UDS 连接
           b. BuildRpcPacket(service, method, data) — 构建数据包
           c. CallOutInSync(connection, packet) — 同步发送等待
        3. 解析响应 → Response::Create(type, data)
        4. 返回 error_code 或 response.error_no()

        参数:
          service: 目标服务名 (如 "com.trim.storage")
          method:  方法名 (如 "get_info")
          params:  请求参数 dict

        返回: 解析后的 JSON 响应 dict
        """
        # 查找服务 UDS 路径
        svc_info = self.services.get(service)
        if not svc_info:
            # 尝试自动从 Broker 获取
            self.apply_permission([service])
            svc_info = self.services.get(service)
            if not svc_info:
                raise RuntimeError(f"服务未找到: {service}")

        uds_path = svc_info.get("uds", "")
        if not uds_path:
            raise RuntimeError(f"服务 {service} 无 UDS 路径")

        # 构建并发送请求
        pkt = build_rpc_request(
            service, method, params,
            caller_uuid=self.uuid,
            caller_service=self.caller_service,
        )

        # 如果有 token, 设置到 Section 1
        token = svc_info.get("token", "")
        if token:
            pkt.token = token.encode('utf-8')

        # 发送请求并接收响应
        resp_pkt = send_rpc_packet(uds_path, pkt, self.timeout)
        resp_json_str = resp_pkt.get_json_payload()

        self.last_response = resp_pkt

        if resp_json_str:
            return json.loads(resp_json_str)
        return {}


# =============================================================================
# 系统扫描工具
# =============================================================================

def scan_uds_sockets(search_paths=None):
    """
    扫描系统中的 Unix Domain Socket 文件。

    参数:
      search_paths: 搜索路径列表 (默认: ['/var/run', '/tmp', '/run'])

    返回: [(path, stat_info), ...]
    """
    if search_paths is None:
        search_paths = ['/var/run', '/tmp', '/run', '/var/tmp']

    sockets = []
    for base_path in search_paths:
        if not os.path.exists(base_path):
            continue

        try:
            for root, dirs, files in os.walk(base_path):
                for name in files:
                    full_path = os.path.join(root, name)
                    try:
                        st = os.stat(full_path)
                        # S_ISSOCK: 检查是否为 socket
                        import stat as stat_module
                        if stat_module.S_ISSOCK(st.st_mode):
                            sockets.append((full_path, st))
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass

    return sockets


# =============================================================================
# 发现模式 — 仅查询 Broker 获取服务信息
# =============================================================================

def discover_services(services, timeout=DEFAULT_TIMEOUT, broker_uds=None):
    """
    从 RPC Broker 查询服务路由信息。

    对应 RpcClient::ApplyPermission() 的前半部分:
    发送 apply 请求到 Broker, 获取 UDS 路径、IP、类型、令牌等信息。

    如果 services 为空列表, 则尝试查询所有已注册服务 (通过特殊方法)。
    """
    client = RpcClient(timeout=timeout, broker_uds=broker_uds)

    # 如果没有指定服务, 尝试查询 Broker 自身获取服务列表
    if not services:
        _debug("未指定服务名, 尝试查询所有服务...")
        # 尝试几种可能的方法获取服务列表
        # 方法1: 查询 Broker 的 list/query 方法
        try:
            _debug("尝试方法1: com.trim.rpcbroker.list")
            pkt = build_rpc_request(
                BROKER_SERVICE, "list", {},
                caller_uuid=client.uuid,
                caller_service=client.caller_service,
            )
            resp_pkt = send_rpc_packet(client.broker_uds, pkt, timeout)
            resp_json_str = resp_pkt.get_json_payload()
            if resp_json_str:
                resp = json.loads(resp_json_str)
                _debug(f"list 响应: {resp}")
                return resp
        except Exception as e:
            _debug(f"方法1失败: {e}")

        # 方法2: 查询空服务列表 (可能返回所有服务)
        try:
            _debug("尝试方法2: apply with empty services")
            return client.apply_permission([])
        except Exception as e:
            _debug(f"方法2失败: {e}")

        # 方法3: 查询 Broker 自身
        try:
            _debug("尝试方法3: apply for rpcbroker itself")
            return client.apply_permission([BROKER_SERVICE])
        except Exception as e:
            _debug(f"方法3失败: {e}")
            raise RuntimeError(
                "无法查询服务列表。请指定具体的服务名，如:\n"
                "  --discover com.trim.storage com.trim.main"
            )

    return client.apply_permission(services)


# =============================================================================
# 直连模式 — 跳过 Broker 直接通过 UDS 调用
# =============================================================================

def direct_call(uds_path, service, method, params=None,
                caller_uuid=None, timeout=DEFAULT_TIMEOUT):
    """
    直接通过指定 UDS 路径发送 RPC 请求, 跳过 Broker 服务发现。

    适用场景:
      - 已知服务 UDS 路径
      - 直接与 Broker 自身通信
      - 调试/测试用途
    """
    pkt = build_rpc_request(
        service, method, params,
        caller_uuid=caller_uuid,
    )
    resp_pkt = send_rpc_packet(uds_path, pkt, timeout)
    resp_json_str = resp_pkt.get_json_payload()
    if resp_json_str:
        return json.loads(resp_json_str)
    return {}


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="trimrpc RPC 客户端 — 基于 fnOS libtrimrpc.so 逆向分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 扫描 UDS socket:
  %(prog)s --scan

  # 通过 Broker 调用服务:
  %(prog)s '{"service":"com.trim.storage","method":"get_info"}'

  # 携带参数:
  %(prog)s '{"service":"com.trim.resmon","method":"send_alert","params":{"type":"test"}}'

  # 指定 Broker 路径:
  %(prog)s --broker-uds /tmp/rpc/rpcbroker '{"service":"com.trim.main","method":"ping"}'

  # 直接指定 UDS:
  %(prog)s --uds /var/run/rpc/storage \\
      '{"service":"com.trim.storage","method":"get_info"}'

  # 查询服务路由:
  %(prog)s --discover com.trim.storage com.trim.main

  # 发送原始 JSON 到指定 UDS (完全自定义):
  %(prog)s --raw --uds /var/run/rpc/rpcbroker \\
      '{"data":{"req":"com.trim.rpcbroker.apply","pid":1,"reqid":"0","services":["com.trim.main"]}}'
""",
    )

    parser.add_argument(
        'json_input', nargs='?', default=None,
        help='JSON 输入: {"service":"...","method":"...","params":{...}}'
    )
    parser.add_argument(
        '--uds', type=str, default=None,
        help='直接指定 UDS 路径 (跳过 Broker 服务发现)'
    )
    parser.add_argument(
        '--discover', nargs='*', metavar='SERVICE',
        help='仅查询 Broker 获取服务路由信息 (不指定服务名则列出所有服务)'
    )
    parser.add_argument(
        '--scan', action='store_true',
        help='扫描系统中的 UDS socket 文件'
    )
    parser.add_argument(
        '--broker-uds', type=str, default=None,
        help='指定 Broker UDS 路径 (默认自动探测)'
    )
    parser.add_argument(
        '--raw', action='store_true',
        help='原始模式: JSON 直接作为 extra_data 发送, 不自动添加 req/pid/reqid'
    )
    parser.add_argument(
        '--uuid', type=str, default=None,
        help='指定调用方 UUID'
    )
    parser.add_argument(
        '--caller', type=str, default="com.trim.pyclient",
        help='指定调用方服务名 (默认: com.trim.pyclient)'
    )
    parser.add_argument(
        '--timeout', type=int, default=DEFAULT_TIMEOUT,
        help=f'超时秒数 (默认: {DEFAULT_TIMEOUT})'
    )
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='输出详细调试信息'
    )

    args = parser.parse_args()

    # 设置全局调试标志
    global _debug_enabled
    _debug_enabled = args.verbose

    # ── 扫描模式 ──
    if args.scan:
        print("扫描系统中的 UDS socket 文件...\n")
        sockets = scan_uds_sockets()
        if not sockets:
            print("未找到任何 UDS socket 文件")
            return

        # 按路径排序
        sockets.sort(key=lambda x: x[0])

        # 过滤出可能与 RPC 相关的 socket
        rpc_sockets = [s for s in sockets if 'rpc' in s[0].lower() or 'broker' in s[0].lower()]

        if rpc_sockets:
            print("=== 可能的 RPC 相关 socket ===")
            for path, st in rpc_sockets:
                print(f"  {path}")
            print()

        print(f"=== 所有 UDS socket ({len(sockets)} 个) ===")
        for path, st in sockets:
            print(f"  {path}")
        return

    # ── 发现模式 ──
    if args.discover is not None:  # 注意: 空列表也是有效的
        try:
            services = discover_services(args.discover, args.timeout, args.broker_uds)
            print(json.dumps(services, indent=2, ensure_ascii=False))
        except ConnectionError as e:
            print(f"连接错误: {e}", file=sys.stderr)
            print("\n提示: 使用 --scan 扫描系统中的 UDS socket", file=sys.stderr)
            print("      或使用 --broker-uds 指定 Broker 路径", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # ── 需要 JSON 输入 ──
    if not args.json_input:
        parser.print_help()
        sys.exit(1)

    try:
        input_data = json.loads(args.json_input)
    except json.JSONDecodeError as e:
        print(f"JSON 解析错误: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # ── 原始模式 ──
        if args.raw:
            uds_path = args.uds or BROKER_UDS_PATH
            caller_uuid = args.uuid or str(uuid.uuid4()).upper()

            pkt = RpcPacket()
            pkt.session_id = _next_session_id()
            pkt.data = caller_uuid.encode('utf-8')
            pkt.extra_data = json.dumps(
                input_data, separators=(',', ':')
            ).encode('utf-8')

            if args.verbose:
                print(f"[DEBUG] UDS: {uds_path}", file=sys.stderr)
                print(f"[DEBUG] 发送: {pkt}", file=sys.stderr)
                print(f"[DEBUG] UUID: {caller_uuid}", file=sys.stderr)
                print(f"[DEBUG] JSON: {pkt.extra_data.decode()}", file=sys.stderr)

            resp_pkt = send_rpc_packet(uds_path, pkt, args.timeout)

            if args.verbose:
                print(f"[DEBUG] 响应: {resp_pkt}", file=sys.stderr)
                if resp_pkt.data:
                    print(f"[DEBUG] resp.data: {resp_pkt.data}", file=sys.stderr)
                if resp_pkt.token:
                    print(f"[DEBUG] resp.token: {resp_pkt.token}", file=sys.stderr)

            resp_str = resp_pkt.get_json_payload()
            if resp_str:
                try:
                    resp_obj = json.loads(resp_str)
                    print(json.dumps(resp_obj, indent=2, ensure_ascii=False))
                except json.JSONDecodeError:
                    print(resp_str)
            else:
                print("(空响应)")
            return

        # ── 标准模式 ──
        service = input_data.get("service")
        method = input_data.get("method")
        params = input_data.get("params", {})

        if not service:
            print("错误: JSON 中缺少 'service' 字段", file=sys.stderr)
            sys.exit(1)
        if not method:
            print("错误: JSON 中缺少 'method' 字段", file=sys.stderr)
            sys.exit(1)

        if args.verbose:
            print(f"[DEBUG] 目标: {service}.{method}", file=sys.stderr)
            print(f"[DEBUG] 参数: {json.dumps(params, ensure_ascii=False)}", file=sys.stderr)

        if args.uds:
            # ── 直连模式 ──
            if args.verbose:
                print(f"[DEBUG] 直连 UDS: {args.uds}", file=sys.stderr)

            result = direct_call(
                args.uds, service, method, params,
                caller_uuid=args.uuid,
                timeout=args.timeout,
            )
        else:
            # ── 通过 Broker ──
            if args.verbose:
                print(f"[DEBUG] 通过 Broker 发现服务...", file=sys.stderr)

            client = RpcClient(
                caller_uuid=args.uuid,
                caller_service=args.caller,
                timeout=args.timeout,
                broker_uds=args.broker_uds,
            )

            # 申请权限 (获取服务 UDS 路径)
            svc_list = client.apply_permission([service])
            if args.verbose:
                print(
                    f"[DEBUG] 服务路由: "
                    f"{json.dumps(svc_list, ensure_ascii=False)}",
                    file=sys.stderr,
                )

            # 执行 RPC 调用
            result = client.call(service, method, params)

        # 输出结果
        print(json.dumps(result, indent=2, ensure_ascii=False))

    except KeyboardInterrupt:
        print("\n中断", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
