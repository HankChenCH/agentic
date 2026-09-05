"""密码哈希：PBKDF2-SHA256（stdlib 实现，不引第三方）。

存储格式 ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``；空串表示
「不可登录」（迁移回填的默认用户），验密恒 False。
"""

import hashlib
import hmac
import os

_ALGORITHM = "sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_{_ALGORITHM}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        algorithm, iterations_raw, salt_hex, hash_hex = stored.split("$")
        iterations = int(iterations_raw)
        if algorithm != f"pbkdf2_{_ALGORITHM}" or iterations < 1:
            return False
        digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)
