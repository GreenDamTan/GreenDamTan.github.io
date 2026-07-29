#!/usr/bin/env python3
"""Recover an authenticated FOT object with the verified backup password b"0".

The implementation follows the static analysis in report.md. It never attempts
password guessing and writes output only after every format and HMAC check
succeeds.
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
