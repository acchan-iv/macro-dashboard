"""市場予想まわりのテスト（暗号化の往復・単位の変換・週の区切り）

実行: python tests/test_forecast.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fc_keygen  # noqa: E402
import forecast as fc  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

ok = True


def check(name, cond, detail=""):
    global ok
    print(("OK " if cond else "NG ") + name + (f"  {detail}" if detail else ""))
    ok &= bool(cond)


# 単位の変換（画面の単位にそろえる）
check("雇用者数 119K → 11.9万人", abs(fc.parse_forecast("119K", "万人") - 11.9) < 1e-9)
check("申請件数 199K → 19.9万件", abs(fc.parse_forecast("199K", "万件") - 19.9) < 1e-9)
check("求人件数 7.20M → 720万件", abs(fc.parse_forecast("7.20M", "万件") - 720) < 1e-9)
check("貿易収支 -60.4B → -60.4十億ドル", abs(fc.parse_forecast("-60.4B", "十億ドル") + 60.4) < 1e-9)
check("CPI 0.3% → 0.3", fc.parse_forecast("0.3%", "%") == 0.3)
check("空欄は無視", fc.parse_forecast("", "%") is None)
check("CPI前年比は季節調整なし系列で比べる", fc.TITLE_SPEC["CPI y/y"][1] == ("CPIAUCNS", "yoy"))

# 週の区切り（日曜はじまり）と対応づけ
ev = [{"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "forecast": "0.6%"},
      {"title": "CPI m/m", "country": "EUR", "date": "2026-10-13T05:00:00-04:00", "forecast": "0.1%"},
      {"title": "Bank Holiday", "country": "USD", "date": "2026-10-12T00:00:00-04:00", "forecast": ""}]
week, items = fc.week_items(ev, {"cpi": "%"})
check("週の始まり＝日曜", week == "2026-10-11", week)
check("米国の対応指標だけ取り出す", [i["id"] for i in items] == ["cpi"])

# 暗号化 → パスワードで復号（テスト用の使い捨て鍵）
with tempfile.TemporaryDirectory() as d:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    kf = Path(d) / "k.json"
    kf.write_text(json.dumps(fc_keygen.wrap(key, "test-password-1234")), encoding="utf-8")
    blob = fc.encrypt_blob({"items": items}, pem)
    check("暗号文に予想値が見えない", "0.6" not in json.dumps(blob))
    pk = fc.load_private_key(kf, "test-password-1234")
    check("正しいパスワードで復号できる", fc.decrypt_blob(blob, pk)["items"] == items)
    try:
        fc.load_private_key(kf, "wrong-password-1234")
        check("違うパスワードは拒否", False)
    except Exception:  # noqa: BLE001
        check("違うパスワードは拒否", True)

sys.exit(0 if ok else 1)
