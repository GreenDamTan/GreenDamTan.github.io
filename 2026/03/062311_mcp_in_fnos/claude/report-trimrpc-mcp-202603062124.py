#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trimrpc MCP Server — fnOS 系统管理 MCP 工具服务
================================================

通过 Streamable HTTP 传输提供 fnOS 系统管理能力的 MCP 工具服务器。
内置 trimrpc 二进制协议栈，通过 Unix Domain Socket 与本机 fnOS
RPC 服务通信。零外部依赖，仅使用 Python 标准库。

可用工具:
  get_system_logs    获取系统事件日志 (登录、配置变更、SSH 等)
  trimrpc_call       通用 trimrpc RPC 调用 (任意服务/方法)
  discover_services  发现可用的 RPC 服务

用法:
  python3 report-trimrpc-mcp-202603062124.py                  # 默认 0.0.0.0:9800
  python3 report-trimrpc-mcp-202603062124.py --port 8080      # 自定义端口
  python3 report-trimrpc-mcp-202603062124.py --host 127.0.0.1 # 仅本机
  python3 report-trimrpc-mcp-202603062124.py --list-tools     # 列出工具
  python3 report-trimrpc-mcp-202603062124.py -v               # 调试日志

MCP 客户端配置 (Claude Code settings / .mcp.json):
  {
    "mcpServers": {
      "fnos": {
        "url": "http://<fnOS-IP>:9800/mcp"
      }
    }
  }
"""

import sys
import json
import struct
import socket
import os
import time
import uuid
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# ═══════════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════════

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format='[trimrpc-mcp] %(levelname)s %(message)s',
)
log = logging.getLogger('trimrpc-mcp')


# ═══════════════════════════════════════════════════════════════════
# trimrpc 二进制协议核心
#
# 协议格式: 30 字节固定头部 + 4 段变长数据
# 传输层:   Unix Domain Socket (AF_UNIX, SOCK_STREAM)
# ═══════════════════════════════════════════════════════════════════

CPRT_MAGIC       = 0x54525043       # "CPRT" little-endian
PROTOCOL_VERSION = 1
HEADER_SIZE      = 30

BROKER_SERVICE   = "com.trim.rpcbroker"
BROKER_UDS_PATHS = [
    "/run/trim_app_cgi/rpcbroker",
    "/var/run/trim_app_cgi/rpcbroker",
    "/var/run/rpc/rpcbroker",
    "/tmp/rpc/rpcbroker",
    "/run/rpc/rpcbroker",
    "/var/tmp/rpc/rpcbroker",
]
DEFAULT_TIMEOUT      = 10
SERVICE_PREFIX       = "com."
MAX_SERVICE_NAME_LEN = 128

_sid_counter = 0
_sid_lock    = threading.Lock()


def _next_session_id():
    global _sid_counter
    with _sid_lock:
        _sid_counter += 1
        return _sid_counter


class RpcPacket:
    """
    trimrpc 二进制数据包。

    头部 (30 字节, little-endian):
      magic(4) + version(2) + session_id(8) +
      data_len(2) + token_len(2) +
      extra_data_len(4) + reserved(4) + extra_data2_len(4)

    数据段: [data] + [token] + [extra_data(JSON 载荷)] + [extra_data2]
    """
    HEADER_FMT = '<IHQHHIII'

    def __init__(self):
        self.magic = CPRT_MAGIC
        self.version = PROTOCOL_VERSION
        self.session_id = 0
        self.data = b''          # Section 0: 调用方 UUID
        self.token = b''         # Section 1: 认证令牌
        self.extra_data = b''    # Section 2: JSON 载荷
        self.extra_data2 = b''   # Section 3: 扩展数据

    def serialize(self):
        header = struct.pack(
            self.HEADER_FMT,
            self.magic, self.version, self.session_id,
            len(self.data), len(self.token),
            len(self.extra_data), 0, len(self.extra_data2),
        )
        return header + self.data + self.token + self.extra_data + self.extra_data2

    @classmethod
    def deserialize(cls, raw):
        if len(raw) < HEADER_SIZE:
            raise ValueError(f"数据不足: 需要 {HEADER_SIZE} 字节, 实际 {len(raw)}")
        pkt = cls()
        (
            pkt.magic, pkt.version, pkt.session_id,
            d_len, t_len, e_len, _, e2_len,
        ) = struct.unpack(cls.HEADER_FMT, raw[:HEADER_SIZE])
        if pkt.magic != CPRT_MAGIC:
            raise ValueError(f"协议魔数不匹配: 0x{pkt.magic:08X}")
        off = HEADER_SIZE
        pkt.data        = raw[off:off + d_len];  off += d_len
        pkt.token       = raw[off:off + t_len];  off += t_len
        pkt.extra_data  = raw[off:off + e_len];  off += e_len
        pkt.extra_data2 = raw[off:off + e2_len]
        return pkt

    def get_json_payload(self):
        if self.extra_data:
            return self.extra_data.decode('utf-8', errors='replace')
        if self.data:
            return self.data.decode('utf-8', errors='replace')
        return ''


def _recv_all(sock, size, timeout=DEFAULT_TIMEOUT):
    sock.settimeout(timeout)
    buf = b''
    while len(buf) < size:
        try:
            chunk = sock.recv(size - len(buf))
        except socket.timeout:
            raise TimeoutError(f"接收超时 ({timeout}s): {len(buf)}/{size} 字节")
        if not chunk:
            raise ConnectionError(f"连接断开: {len(buf)}/{size} 字节")
        buf += chunk
    return buf


def send_rpc_packet(uds_path, packet, timeout=DEFAULT_TIMEOUT):
    """通过 Unix Domain Socket 发送 RpcPacket 并接收响应"""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(uds_path)
        sock.sendall(packet.serialize())
        resp_header = _recv_all(sock, HEADER_SIZE, timeout)
        (
            _, _, _, d_len, t_len, e_len, _, e2_len,
        ) = struct.unpack(RpcPacket.HEADER_FMT, resp_header)
        total = d_len + t_len + e_len + e2_len
        resp_body = _recv_all(sock, total, timeout) if total > 0 else b''
        return RpcPacket.deserialize(resp_header + resp_body)
    except FileNotFoundError:
        raise ConnectionError(f"UDS 不存在: {uds_path} (RPC 服务可能未运行)")
    except PermissionError:
        raise ConnectionError(f"无权限连接: {uds_path}")
    finally:
        sock.close()


def build_reqid():
    ts = int(time.time() * 1000000)
    return format(ts & 0xFFFFFFFFFFFFFFFF, '016x')


def build_rpc_request(service, method, params=None, caller_uuid=None):
    if len(service) > MAX_SERVICE_NAME_LEN:
        raise ValueError(f"服务名超过 {MAX_SERVICE_NAME_LEN} 字节")
    if not service.startswith(SERVICE_PREFIX):
        raise ValueError(f'服务名必须以 "{SERVICE_PREFIX}" 开头')

    json_payload = {
        "data": {
            "req": f"{service}.{method}",
            "pid": os.getpid(),
            "reqid": build_reqid(),
        }
    }
    if params:
        json_payload["data"].update(params)

    pkt = RpcPacket()
    pkt.session_id = _next_session_id()
    pkt.data = (caller_uuid or str(uuid.uuid4()).upper()).encode('utf-8')
    pkt.extra_data = json.dumps(json_payload, separators=(',', ':')).encode('utf-8')
    return pkt


class RpcClient:
    """trimrpc RPC 客户端 — 自动服务发现与调用"""

    def __init__(self, caller_uuid=None, caller_service="com.trim.pyclient",
                 timeout=DEFAULT_TIMEOUT, broker_uds=None):
        self.uuid = caller_uuid or str(uuid.uuid4()).upper()
        self.caller_service = caller_service
        self.timeout = timeout
        self.broker_uds = broker_uds or self._find_broker()
        self.services = {
            BROKER_SERVICE: {"name": BROKER_SERVICE, "uds": self.broker_uds},
        }

    def _find_broker(self):
        for p in BROKER_UDS_PATHS:
            if os.path.exists(p):
                return p
        return BROKER_UDS_PATHS[0]

    def apply_permission(self, target_services):
        if isinstance(target_services, str):
            target_services = [target_services]
        pkt = build_rpc_request(
            BROKER_SERVICE, "apply",
            {"services": target_services},
            caller_uuid=self.uuid,
        )
        resp_pkt = send_rpc_packet(self.broker_uds, pkt, self.timeout)
        resp_str = resp_pkt.get_json_payload()
        if not resp_str:
            raise RuntimeError("Broker 返回空响应")
        resp = json.loads(resp_str)
        resp_data = resp.get("data", resp)
        if resp_data.get("result") != "succ":
            raise RuntimeError(
                f"ApplyPermission 失败: {json.dumps(resp_data, ensure_ascii=False)}"
            )
        for svc in resp_data.get("data", []):
            sid = svc.get("id", "")
            if sid:
                self.services[sid] = svc
        return resp_data.get("data", [])

    def call(self, service, method, params=None):
        svc_info = self.services.get(service)
        if not svc_info:
            self.apply_permission([service])
            svc_info = self.services.get(service)
            if not svc_info:
                raise RuntimeError(f"服务未找到: {service}")
        uds_path = svc_info.get("uds", "")
        if not uds_path:
            raise RuntimeError(f"服务 {service} 无 UDS 路径")
        pkt = build_rpc_request(service, method, params, caller_uuid=self.uuid)
        token = svc_info.get("token", "")
        if token:
            pkt.token = token.encode('utf-8')
        resp_pkt = send_rpc_packet(uds_path, pkt, self.timeout)
        resp_str = resp_pkt.get_json_payload()
        return json.loads(resp_str) if resp_str else {}


# ═══════════════════════════════════════════════════════════════════
# MCP 工具定义与实现
# ═══════════════════════════════════════════════════════════════════

MCP_TOOLS = [
    {
        "name": "get_system_logs",
        "description": (
            "获取 fnOS 系统事件日志。返回系统操作记录，包括用户登录、"
            "系统配置变更、SSH 连接等事件。支持分页和按模块筛选。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "integer",
                    "description": "页码，从 1 开始",
                    "default": 1,
                },
                "pageSize": {
                    "type": "integer",
                    "description": "每页条数，范围 1-100",
                    "default": 20,
                },
                "module": {
                    "type": "integer",
                    "description": "模块筛选: -1 表示全部模块",
                    "default": -1,
                },
            },
        },
    },
    {
        "name": "trimrpc_call",
        "description": (
            "通用 fnOS trimrpc RPC 调用。通过 RPC Broker 调用 fnOS 上的"
            "任意已注册 RPC 服务。服务名必须以 'com.' 开头。"
            "示例: service='com.trim.storage', method='get_info'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "目标服务名，如 'com.trim.storage'",
                },
                "method": {
                    "type": "string",
                    "description": "方法名，如 'get_info' 或 'common.list'",
                },
                "params": {
                    "type": "object",
                    "description": "调用参数",
                    "default": {},
                },
            },
            "required": ["service", "method"],
        },
    },
    {
        "name": "discover_services",
        "description": (
            "发现 fnOS 上可用的 RPC 服务。查询 RPC Broker 获取服务路由信息，"
            "包括 UDS 路径、IP、类型、认证令牌等。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "services": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "要查询的服务名列表。"
                        "示例: ['com.trim.storage', 'com.trim.main']"
                    ),
                    "default": [],
                },
            },
        },
    },
]

# ── 全局 RPC 客户端 ──

_rpc_client = None
_rpc_client_lock = threading.Lock()
_broker_uds_override = None  # 由 --broker-uds 设置


def _get_client():
    global _rpc_client
    with _rpc_client_lock:
        if _rpc_client is None:
            _rpc_client = RpcClient(broker_uds=_broker_uds_override)
            log.info(f"RPC 客户端初始化, Broker: {_rpc_client.broker_uds}")
        return _rpc_client


# ── 工具处理函数 ──

def tool_get_system_logs(arguments):
    page      = max(1, arguments.get("page", 1))
    page_size = max(1, min(100, arguments.get("pageSize", 20)))
    module    = arguments.get("module", -1)

    result = _get_client().call(
        "com.trim.eventlogger",
        "common.list",
        {"pageSize": page_size, "page": page, "module": module},
    )
    return json.dumps(result, indent=2, ensure_ascii=False)


def tool_trimrpc_call(arguments):
    service = arguments.get("service", "")
    method  = arguments.get("method", "")
    params  = arguments.get("params", {})
    if not service:
        raise ValueError("缺少必填参数 'service'")
    if not method:
        raise ValueError("缺少必填参数 'method'")

    result = _get_client().call(service, method, params if params else None)
    return json.dumps(result, indent=2, ensure_ascii=False)


def tool_discover_services(arguments):
    services = arguments.get("services", [])
    client = _get_client()

    if not services:
        try:
            pkt = build_rpc_request(
                BROKER_SERVICE, "list", {}, caller_uuid=client.uuid,
            )
            resp_pkt = send_rpc_packet(client.broker_uds, pkt, client.timeout)
            resp_str = resp_pkt.get_json_payload()
            if resp_str:
                return json.dumps(json.loads(resp_str), indent=2, ensure_ascii=False)
        except Exception:
            pass
        services = [BROKER_SERVICE]

    result = client.apply_permission(services)
    return json.dumps(result, indent=2, ensure_ascii=False)


TOOL_HANDLERS = {
    "get_system_logs":   tool_get_system_logs,
    "trimrpc_call":      tool_trimrpc_call,
    "discover_services": tool_discover_services,
}


# ═══════════════════════════════════════════════════════════════════
# MCP JSON-RPC 处理
# ═══════════════════════════════════════════════════════════════════

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "trimrpc-mcp", "version": "1.0.0"}


def process_jsonrpc(msg):
    """
    处理一条 JSON-RPC 2.0 消息，返回响应 dict 或 None (通知无需响应)。
    """
    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    # 通知 (无 id) 不需要回复
    if msg_id is None:
        log.debug(f"通知: {method}")
        return None

    def _result(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _error(code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        return _result({
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "ping":
        return _result({})

    if method == "tools/list":
        return _result({"tools": MCP_TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return _error(-32602, f"未知工具: {tool_name}")
        try:
            text = handler(arguments)
            return _result({"content": [{"type": "text", "text": text}]})
        except Exception as e:
            log.error(f"工具 {tool_name} 执行失败: {e}")
            return _result({
                "content": [{"type": "text", "text": f"错误: {e}"}],
                "isError": True,
            })

    return _error(-32601, f"方法不存在: {method}")


# ═══════════════════════════════════════════════════════════════════
# HTTP 传输层 (Streamable HTTP)
#
# POST /mcp → 发送 JSON-RPC 请求, 直接返回 JSON 响应
# ═══════════════════════════════════════════════════════════════════


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class McpRequestHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        log.debug(f"{self.client_address[0]} {fmt % args}")

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json_response(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_DELETE(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        self._json_response(200, {
            "name": SERVER_INFO["name"],
            "version": SERVER_INFO["version"],
            "status": "running",
            "tools": [t["name"] for t in MCP_TOOLS],
        })

    def do_POST(self):
        content_len = int(self.headers.get('Content-Length', 0))
        if content_len == 0:
            self._json_response(400, {"error": "Empty body"})
            return

        try:
            body = self.rfile.read(content_len)
            msg = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._json_response(400, {"error": f"Invalid JSON: {e}"})
            return

        log.debug(f"← {msg.get('method', '?')} (id={msg.get('id')})")
        response = process_jsonrpc(msg)
        if response is not None:
            self._json_response(200, response)
        else:
            self.send_response(202)
            self._cors_headers()
            self.end_headers()


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="trimrpc MCP Server — fnOS 系统管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                          # 默认 0.0.0.0:9800
  %(prog)s --port 8080              # 自定义端口
  %(prog)s --host 127.0.0.1         # 仅允许本机访问
  %(prog)s --broker-uds /tmp/rpc/rpcbroker

MCP 配置:
  {"mcpServers": {"fnos": {"url": "http://<IP>:9800/mcp"}}}
""",
    )
    parser.add_argument('--host', default='0.0.0.0', help='监听地址 (默认: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=9800, help='监听端口 (默认: 9800)')
    parser.add_argument('--broker-uds', default=None, help='指定 RPC Broker UDS 路径')
    parser.add_argument('--list-tools', action='store_true', help='列出可用工具并退出')
    parser.add_argument('-v', '--verbose', action='store_true', help='调试日志')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_tools:
        print("trimrpc MCP Server 可用工具:\n")
        for t in MCP_TOOLS:
            print(f"  {t['name']}")
            print(f"    {t['description']}")
            schema = t.get("inputSchema", {})
            props = schema.get("properties", {})
            if props:
                print("    参数:")
                for k, v in props.items():
                    req = "必填" if k in schema.get("required", []) else "可选"
                    desc = v.get("description", "")
                    default = v.get("default")
                    default_str = f", 默认={default}" if default is not None else ""
                    print(f"      {k} ({req}{default_str}): {desc}")
            print()
        return

    global _broker_uds_override
    _broker_uds_override = args.broker_uds

    server = ThreadedHTTPServer((args.host, args.port), McpRequestHandler)
    host_display = args.host if args.host != '0.0.0.0' else '<IP>'
    print(f"trimrpc MCP Server 已启动")
    print(f"  监听: http://{args.host}:{args.port}")
    print(f"  端点: POST http://{host_display}:{args.port}/mcp")
    print(f"  工具: {', '.join(t['name'] for t in MCP_TOOLS)}")
    if args.broker_uds:
        print(f"  Broker: {args.broker_uds}")
    print(f'\n  MCP 配置: {{"mcpServers": {{"fnos": {{"url": "http://{host_display}:{args.port}/mcp"}}}}}}')
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        server.shutdown()


if __name__ == '__main__':
    main()
