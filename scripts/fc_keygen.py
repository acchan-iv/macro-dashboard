#!/usr/bin/env python3
"""
予想の暗号鍵を作る／パスワードを変える（最初の1回と、パスワード変更の時だけ使う）

  新規作成：     FC_PASSWORD=新しいパスワード python scripts/fc_keygen.py
  パスワード変更：FC_PASSWORD_OLD=今のパスワード FC_PASSWORD=新しいパスワード python scripts/fc_keygen.py --change

作るもの：
  config/fc_public.pem … 公開鍵（GitHub側が暗号化に使う。見られても問題ない）
  docs/fc_key.json     … パスワードで暗号化した秘密鍵（パスワードがないと使えない）
パスワードそのものはどこにも保存しない。
"""
import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forecast  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEY_FILE = ROOT / "docs" / "fc_key.json"
ITER = 600_000


def wrap(private_key, password: str) -> dict:
    der = private_key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    salt, iv = os.urandom(16), os.urandom(12)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITER, 32)
    return {"v": 1, "kdf": {"name": "PBKDF2", "hash": "SHA-256", "iter": ITER, "salt": base64.b64encode(salt).decode()},
            "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(AESGCM(dk).encrypt(iv, der, None)).decode()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--change", action="store_true", help="鍵はそのままでパスワードだけ変える（貯めた予想も読める）")
    a = ap.parse_args()
    pw = os.environ.get("FC_PASSWORD", "")
    if len(pw) < 12:
        print("FC_PASSWORD（12文字以上）を指定してください", file=sys.stderr)
        return 1
    if a.change:
        key = forecast.load_private_key(KEY_FILE, os.environ["FC_PASSWORD_OLD"])
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        forecast.PUBLIC_KEY_PATH.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    KEY_FILE.write_text(json.dumps(wrap(key, pw)), encoding="utf-8")
    print("作成しました：", forecast.PUBLIC_KEY_PATH.relative_to(ROOT), KEY_FILE.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
