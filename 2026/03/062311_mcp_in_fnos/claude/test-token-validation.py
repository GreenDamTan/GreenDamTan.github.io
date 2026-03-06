#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token 验证机制研究脚本
===================

研究 trim_http_cgi 如何通过 trimrpc 验证 token

验证流程:
1. trim_http_cgi 提取 token (Cookie/Header/Query)
2. 调用 com.trim.main.tokenQuery 验证
3. 检查响应 result == "succ"

测试方法:
1. 通过 trimrpc 调用 com.trim.main.tokenQuery
2. 通过 trim 框架的 user.query_uid_by_token
3. 直接连接 /run/trim_srv.socket 调用 user.* 方法
"""

import sys
import os
import json

# 添加 trimrpc 脚本路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入 trimrpc 客户端
try:
    exec(open('report-trimrpc-202603041034.py').read())
except Exception as e:
    print(f"错误: 无法加载 trimrpc 客户端: {e}")
    sys.exit(1)


def test_tokenquery_via_trimrpc(token):
    """
    方法1: 通过 trimrpc 调用 com.trim.main.tokenQuery

    这是 trim_http_cgi 使用的方法
    """
    print("\n" + "="*60)
    print("方法1: trimrpc → com.trim.main.tokenQuery")
    print("="*60)

    try:
        # 构建请求
        request = {
            "service": "com.trim.main",
            "method": "tokenQuery",
            "params": {
                "token": token
            }
        }

        print(f"请求: {json.dumps(request, indent=2)}")

        # 调用 RPC
        # 注意: 需要先通过 --discover 找到 com.trim.main 的 UDS 路径
        print("\n尝试发现 com.trim.main 服务...")

        # 这里需要实际的 trimrpc 调用逻辑
        # 由于我们在分析环境中，先打印调用方式

        print("""
调用方式:
python3 report-trimrpc-202603041034.py --discover com.trim.main

然后使用返回的 UDS 路径:
python3 report-trimrpc-202603041034.py --uds <UDS路径> \\
    '{"service":"com.trim.main","method":"tokenQuery","params":{"token":"<token>"}}'
        """)

    except Exception as e:
        print(f"错误: {e}")


def test_query_uid_by_token_via_trim(token):
    """
    方法2: 通过 trim 框架的 user.query_uid_by_token

    这是直接调用 user.hdl 的方法
    """
    print("\n" + "="*60)
    print("方法2: trim WebSocket → user.query_uid_by_token")
    print("="*60)

    print(f"""
user.hdl 提供的 token 相关方法:
- query_uid_by_token: 通过 token 查询 uid
- query_token_by_session: 通过 session 查询 token
- auth_token_and_add_session: 验证 token 并创建 session

调用方式 (通过 /run/trim_cgi.socket):
1. 连接 WebSocket: /run/trim_cgi.socket
2. 发送请求:
   {{
     "req": "user.query_uid_by_token",
     "token": "{token}"
   }}

注意: 这需要 WebSocket 协议，不是 trimrpc 协议
    """)


def test_via_trim_srv_socket(token):
    """
    方法3: 直接通过 /run/trim_srv.socket 调用

    com.trim.main 可能监听这个 socket
    """
    print("\n" + "="*60)
    print("方法3: 直接连接 /run/trim_srv.socket")
    print("="*60)

    print(f"""
根据之前的分析:
- com.trim.main 服务的 UDS: /run/trim_srv.socket
- 这是一个 trimrpc 服务

调用方式:
python3 report-trimrpc-202603041034.py \\
    --raw --uds /run/trim_srv.socket \\
    '{{"req":"user.query_uid_by_token","token":"{token}"}}'

或者使用 trimrpc 协议:
python3 report-trimrpc-202603041034.py \\
    --uds /run/trim_srv.socket \\
    '{{"service":"com.trim.main","method":"tokenQuery","params":{{"token":"{token}"}}}}'
    """)


def analyze_token_validation_flow():
    """
    分析 token 验证的完整流程
    """
    print("\n" + "="*60)
    print("Token 验证流程分析")
    print("="*60)

    print("""
trim_http_cgi 的验证流程:
┌─────────────────────────────────────┐
│ 1. getToken(request)                │
│    - Cookie: fnos-token             │
│    - Header: Authorization          │
│    - Query: token                   │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 2. checkToken(token)                │
│    ├─ trimrpc.NewClient()           │
│    ├─ ApplyPermission("com.trim.main")
│    └─ Call("tokenQuery", token)    │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 3. com.trim.main.tokenQuery         │
│    - 输入: {"token": "<token>"}     │
│    - 输出: {"result": "succ"} 或错误│
└─────────────────────────────────────┘

可能的实现位置:
1. com.trim.main 是一个独立的 trimrpc 服务
2. 它可能加载了 user.hdl 或类似的 handler
3. tokenQuery 方法可能映射到 user.query_uid_by_token

验证方法:
1. 查找 com.trim.main 的 UDS socket 路径
2. 使用 trimrpc 协议调用 tokenQuery
3. 或者直接调用 user.query_uid_by_token (通过 trim 框架)
    """)


def show_alternative_methods():
    """
    展示替代的 token 验证方法
    """
    print("\n" + "="*60)
    print("替代验证方法")
    print("="*60)

    print("""
方法A: 使用 user.hdl 的 authToken 方法
----------------------------------------
这是用户登录时使用的方法，可以验证 token 并创建 session

调用 (通过 /run/trim_srv.socket):
{
  "req": "user.authToken",
  "token": "<token>"
}

返回:
{
  "errno": 0,
  "result": "succ",
  "session_id": <session_id>,
  "uid": <uid>
}


方法B: 使用 user.hdl 的 query_uid_by_token
------------------------------------------
直接查询 token 对应的 uid

调用 (通过 /run/trim_srv.socket):
{
  "req": "user.query_uid_by_token",
  "token": "<token>"
}

返回:
{
  "errno": 0,
  "uid": <uid>
}


方法C: 通过 trim 框架的认证白名单
---------------------------------
某些方法在 trim 框架中无需认证:
- user.active
- user.login
- util.crypto.getRSAPub
- util.getSI

这些方法可以直接调用，无需 token


方法D: 使用 /tmp/trim.nosign 旁路
---------------------------------
如果 /tmp/trim.nosign 文件存在且属于 root:
- 所有无 token 的请求都会跳过认证
- 这是一个调试/测试功能

创建方式:
sudo touch /tmp/trim.nosign
sudo chown root:root /tmp/trim.nosign
    """)


def main():
    print("""
Token 验证机制研究
==================

trim_http_cgi 使用以下流程验证 token:
1. 从 HTTP 请求提取 token (Cookie/Header/Query)
2. 通过 trimrpc 调用 com.trim.main.tokenQuery
3. 检查响应 result == "succ"

本脚本研究如何复现这个验证流程。
    """)

    # 示例 token (需要替换为实际的 token)
    example_token = "YOUR_TOKEN_HERE"

    if len(sys.argv) > 1:
        example_token = sys.argv[1]

    # 测试各种方法
    test_tokenquery_via_trimrpc(example_token)
    test_query_uid_by_token_via_trim(example_token)
    test_via_trim_srv_socket(example_token)

    # 分析流程
    analyze_token_validation_flow()

    # 展示替代方法
    show_alternative_methods()

    print("\n" + "="*60)
    print("总结")
    print("="*60)
    print("""
要实现 token 验证，有以下几种方式:

1. **完全复现 trim_http_cgi 的方式** (推荐)
   - 使用 trimrpc 协议
   - 调用 com.trim.main.tokenQuery
   - 需要先通过 --discover 找到服务路径

2. **直接使用 user.hdl 方法**
   - 通过 /run/trim_srv.socket
   - 调用 user.query_uid_by_token 或 user.authToken
   - 使用 --raw 模式发送 JSON

3. **通过 trim 框架 WebSocket**
   - 连接 /run/trim_cgi.socket
   - 发送 WebSocket 请求
   - 调用 user.* 方法

4. **使用认证旁路** (仅测试)
   - 创建 /tmp/trim.nosign
   - 跳过所有认证检查

下一步:
1. 运行 --discover 找到 com.trim.main 的实际路径
2. 使用实际 token 测试验证
3. 分析响应格式
    """)


if __name__ == "__main__":
    main()
