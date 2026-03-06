#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token 验证实现脚本
================

基于 trim_http_cgi 的逆向分析，实现 token 验证功能

支持的验证方式:
1. trimrpc → com.trim.main.tokenQuery (trim_http_cgi 使用的方式)
2. 直接调用 user.query_uid_by_token (通过 /run/trim_srv.socket)
3. 调用 user.authToken (验证并创建 session)
"""

import socket
import struct
import json
import time
import sys
import os


class TokenValidator:
    """Token 验证器"""

    def __init__(self, debug=False):
        self.debug = debug
        self.session_counter = int(time.time() * 1000) & 0xFFFFFFFF

    def _log(self, msg):
        if self.debug:
            print(f"[DEBUG] {msg}", file=sys.stderr)

    def _build_rpc_packet(self, service, method, data):
        """
        构建 trimrpc RpcPacket

        格式 (30 字节头部):
        - magic: 0x54525043 ("CPRT")
        - version: 1
        - session_id: 8 字节
        - data_len: 2 字节
        - token_len: 2 字节
        - extra_data_len: 4 字节
        - reserved: 4 字节
        - extra_data2_len: 4 字节
        """
        # 生成 session ID
        self.session_counter += 1
        session_id = self.session_counter

        # 构建 JSON data
        json_data = {
            "data": {
                "req": f"{service}.{method}",
                "pid": os.getpid(),
                "reqid": f"{int(time.time()*1000):016x}"
            }
        }
        if data:
            json_data["data"].update(data)

        json_str = json.dumps(json_data, separators=(',', ':'))
        json_bytes = json_str.encode('utf-8')

        # 构建头部
        magic = 0x54525043  # "CPRT"
        version = 1
        data_len = len(json_bytes)
        token_len = 0
        extra_data_len = 0
        reserved = 0
        extra_data2_len = 0

        header = struct.pack(
            '<IHQ HHIII',
            magic,
            version,
            session_id,
            data_len,
            token_len,
            extra_data_len,
            reserved,
            extra_data2_len
        )

        return header + json_bytes, session_id

    def _parse_rpc_response(self, data):
        """解析 trimrpc 响应"""
        if len(data) < 30:
            raise ValueError(f"响应太短: {len(data)} 字节")

        # 解析头部
        magic, version, session_id, data_len, token_len, extra_data_len, reserved, extra_data2_len = \
            struct.unpack('<IHQ HHIII', data[:30])

        self._log(f"响应头: magic=0x{magic:08x}, version={version}, session={session_id}, data_len={data_len}")

        if magic != 0x54525043:
            raise ValueError(f"无效的魔数: 0x{magic:08x}")

        # 提取 data
        offset = 30
        if data_len > 0:
            json_data = data[offset:offset+data_len].decode('utf-8')
            return json.loads(json_data)

        return {}

    def _send_raw_json(self, uds_path, req_data):
        """
        发送原始 JSON 请求 (用于 /run/trim_srv.socket)

        这是 com.trim.main 加载的 handler 使用的格式
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(uds_path)
            sock.settimeout(10)

            # 发送 JSON
            json_str = json.dumps(req_data, separators=(',', ':'))
            sock.sendall(json_str.encode('utf-8'))

            self._log(f"发送: {json_str}")

            # 接收响应
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                # 尝试解析 JSON
                try:
                    return json.loads(response.decode('utf-8'))
                except:
                    continue

            return json.loads(response.decode('utf-8'))

        finally:
            sock.close()

    def validate_via_trimrpc(self, token, uds_path="/run/trim_srv.socket"):
        """
        方法1: 通过 trimrpc 协议调用 com.trim.main.tokenQuery

        这是 trim_http_cgi 使用的方法
        """
        print(f"\n[方法1] trimrpc → com.trim.main.tokenQuery")
        print(f"UDS: {uds_path}")

        try:
            # 构建 RPC 请求
            packet, session_id = self._build_rpc_packet(
                "com.trim.main",
                "tokenQuery",
                {"token": token}
            )

            self._log(f"发送 RPC 包: {len(packet)} 字节, session={session_id}")

            # 连接并发送
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(uds_path)
                sock.settimeout(10)
                sock.sendall(packet)

                # 接收响应
                response = sock.recv(8192)
                self._log(f"收到响应: {len(response)} 字节")

                # 解析响应
                result = self._parse_rpc_response(response)
                print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

                # 检查结果
                if result.get("data", {}).get("result") == "succ":
                    print("✓ Token 验证成功")
                    return True, result
                else:
                    print("✗ Token 验证失败")
                    return False, result

            finally:
                sock.close()

        except Exception as e:
            print(f"✗ 错误: {e}")
            return False, {"error": str(e)}

    def validate_via_user_query(self, token, uds_path="/run/trim_srv.socket"):
        """
        方法2: 直接调用 user.query_uid_by_token

        通过 /run/trim_srv.socket 发送原始 JSON
        """
        print(f"\n[方法2] 原始 JSON → user.query_uid_by_token")
        print(f"UDS: {uds_path}")

        try:
            req_data = {
                "req": "user.query_uid_by_token",
                "token": token
            }

            result = self._send_raw_json(uds_path, req_data)
            print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

            # 检查结果
            if result.get("errno") == 0 and "uid" in result:
                print(f"✓ Token 验证成功, UID: {result['uid']}")
                return True, result
            else:
                print("✗ Token 验证失败")
                return False, result

        except Exception as e:
            print(f"✗ 错误: {e}")
            return False, {"error": str(e)}

    def validate_via_auth_token(self, token, uds_path="/run/trim_srv.socket"):
        """
        方法3: 调用 user.authToken (验证并创建 session)
        """
        print(f"\n[方法3] 原始 JSON → user.authToken")
        print(f"UDS: {uds_path}")

        try:
            req_data = {
                "req": "user.authToken",
                "token": token
            }

            result = self._send_raw_json(uds_path, req_data)
            print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

            # 检查结果
            if result.get("errno") == 0 and result.get("result") == "succ":
                print(f"✓ Token 验证成功")
                if "session_id" in result:
                    print(f"  Session ID: {result['session_id']}")
                if "uid" in result:
                    print(f"  UID: {result['uid']}")
                return True, result
            else:
                print("✗ Token 验证失败")
                return False, result

        except Exception as e:
            print(f"✗ 错误: {e}")
            return False, {"error": str(e)}


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Token 验证工具 - 基于 trim_http_cgi 逆向分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用所有方法验证 token
  %(prog)s <token>

  # 指定 UDS 路径
  %(prog)s --uds /run/trim_srv.socket <token>

  # 只使用特定方法
  %(prog)s --method trimrpc <token>
  %(prog)s --method query <token>
  %(prog)s --method auth <token>

  # 启用调试输出
  %(prog)s --debug <token>
        """
    )

    parser.add_argument("token", help="要验证的 token")
    parser.add_argument("--uds", default="/run/trim_srv.socket",
                        help="UDS socket 路径 (默认: /run/trim_srv.socket)")
    parser.add_argument("--method", choices=["all", "trimrpc", "query", "auth"],
                        default="all", help="验证方法")
    parser.add_argument("--debug", action="store_true", help="启用调试输出")

    args = parser.parse_args()

    validator = TokenValidator(debug=args.debug)

    print("="*60)
    print("Token 验证工具")
    print("="*60)
    print(f"Token: {args.token[:20]}..." if len(args.token) > 20 else f"Token: {args.token}")
    print(f"UDS: {args.uds}")
    print(f"方法: {args.method}")

    results = {}

    # 方法1: trimrpc
    if args.method in ["all", "trimrpc"]:
        success, result = validator.validate_via_trimrpc(args.token, args.uds)
        results["trimrpc"] = {"success": success, "result": result}

    # 方法2: user.query_uid_by_token
    if args.method in ["all", "query"]:
        success, result = validator.validate_via_user_query(args.token, args.uds)
        results["query"] = {"success": success, "result": result}

    # 方法3: user.authToken
    if args.method in ["all", "auth"]:
        success, result = validator.validate_via_auth_token(args.token, args.uds)
        results["auth"] = {"success": success, "result": result}

    # 总结
    print("\n" + "="*60)
    print("验证结果总结")
    print("="*60)

    for method, data in results.items():
        status = "✓ 成功" if data["success"] else "✗ 失败"
        print(f"{method:10s}: {status}")

    # 返回状态码
    if any(data["success"] for data in results.values()):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
