"""
市場予想（Forex Factoryの週間カレンダー）を取得し、暗号化して保存する。

・予想値は提供元の規約で再公開できないため、公開ページには「暗号化した形」でしか置かない。
  読めるのはパスワードを知っている人だけ（ブラウザ側で復号する）。
・暗号の仕組み：RSA-OAEP(SHA-256) ＋ AES-256-GCM。
  GitHub側は公開鍵（config/fc_public.pem）で暗号化するだけなので、パスワードはGitHubに置かない。
  秘密鍵はパスワードで暗号化して docs/fc_key.json に置く（PBKDF2-SHA256 60万回）。
・無料で取れるのは「今週分」だけなので、毎回の実行で週ごとに貯め、21日より古い週は捨てる。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY_PATH = ROOT / "config" / "fc_public.pem"
SEED_PATH = ROOT / "config" / "fc_seed.json"
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
KEEP_DAYS = 21

# Forex Factoryの指標名 → (このツールの指標ID, 結果の出し方)
#   "disp"            画面に出している値をそのまま使う（基準が同じもの）
#   (FRED系列, "mom") 前月比を計算（季節調整済み＝公式の前月比と同じ基準）
#   (FRED系列, "yoy") 前年比を計算（CPIは公式の前年比と同じ季節調整なし系列を使う）
#   (FRED系列, "level", 倍率) 水準（建築許可・住宅着工は年率の件数）
TITLE_SPEC: dict[str, tuple] = {
    "Non-Farm Employment Change": ("payems", "disp"),
    "Unemployment Rate": ("unrate", "disp"),
    "Average Hourly Earnings m/m": ("ahe", ("CES0500000003", "mom")),
    "Average Hourly Earnings y/y": ("ahe", "disp"),
    "CPI m/m": ("cpi", ("CPIAUCSL", "mom")),
    "CPI y/y": ("cpi", ("CPIAUCNS", "yoy")),
    "Core CPI m/m": ("corecpi", ("CPILFESL", "mom")),
    "Core CPI y/y": ("corecpi", ("CPILFENS", "yoy")),
    "PPI m/m": ("ppi", ("PPIFIS", "mom")),
    "Core PCE Price Index m/m": ("corepce", ("PCEPILFE", "mom")),
    "Core PCE Price Index y/y": ("corepce", "disp"),
    "Retail Sales m/m": ("retail", "disp"),
    "Advance GDP q/q": ("gdp", "disp"),
    "Prelim GDP q/q": ("gdp", "disp"),
    "Final GDP q/q": ("gdp", "disp"),
    "Unemployment Claims": ("icsa", "disp"),
    "JOLTS Job Openings": ("jolts", "disp"),
    "Durable Goods Orders m/m": ("durable", ("DGORDER", "mom")),
    "Building Permits": ("permit", ("PERMIT", "level", 0.1)),
    "Housing Starts": ("houst", ("HOUST", "level", 0.1)),
    "Philly Fed Manufacturing Index": ("philly", "disp"),
    "Industrial Production m/m": ("indpro", ("INDPRO", "mom")),
    "Trade Balance": ("tradebal", "disp"),
}
EXTRA_SERIES = ("CPIAUCNS", "CPILFENS")  # 予想との比較のためだけに取る系列
SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
UNIT_MULT = {"万人": 1e4, "万件": 1e4, "十億ドル": 1e9, "兆ドル": 1e12}


def basis_label(title: str) -> str:
    if title.endswith("m/m"):
        return "前月比"
    if title.endswith("y/y"):
        return "前年比"
    if title.endswith("q/q"):
        return "前期比年率"
    return ""


def spec_unit(title: str, disp_unit: str) -> tuple[str, int]:
    """比較に使う単位と小数桁"""
    how = TITLE_SPEC[title][1]
    if how == "disp":
        return disp_unit, (0 if title == "JOLTS Job Openings" else 1)
    if how[1] == "level":
        return "万件", 1
    return "%", 1


def parse_forecast(s: str, unit: str) -> float | None:
    """'0.3%' '199K' '7.20M' '-60.4B' を画面の単位の数値にする"""
    s = (s or "").strip().replace(",", "")
    m = re.fullmatch(r"[<>]?(-?\d+(?:\.\d+)?)([KMBT%]?)", s)
    if not m:
        return None
    num, suf = float(m.group(1)), m.group(2)
    if suf in SUFFIX:
        if unit not in UNIT_MULT:
            return None
        return num * SUFFIX[suf] / UNIT_MULT[unit]
    return num


# ----------------------------------------------------------------------------
# 暗号化
# ----------------------------------------------------------------------------
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def encrypt_blob(obj, public_pem: bytes) -> dict:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    pub = serialization.load_pem_public_key(public_pem)
    key, iv = AESGCM.generate_key(bit_length=256), os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    ek = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return {"ek": _b64(ek), "iv": _b64(iv), "ct": _b64(ct)}


def decrypt_blob(blob: dict, private_key) -> dict:
    """テスト用（本番のGitHub側では復号しない）"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = private_key.decrypt(base64.b64decode(blob["ek"]),
                              padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return json.loads(AESGCM(key).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["ct"]), None))


def load_private_key(key_file: Path, password: str):
    """テスト用：パスワードで秘密鍵を取り出す（ブラウザと同じ手順）"""
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    k = json.loads(key_file.read_text(encoding="utf-8"))
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(k["kdf"]["salt"]), k["kdf"]["iter"], 32)
    der = AESGCM(dk).decrypt(base64.b64decode(k["iv"]), base64.b64decode(k["ct"]), None)
    return serialization.load_der_private_key(der, password=None)


# ----------------------------------------------------------------------------
# 取得・保存
# ----------------------------------------------------------------------------
def fetch_ff(offline_dir: Path | None, warnings: list[str]) -> list[dict] | None:
    if offline_dir:
        f = offline_dir / "_ff.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    err = ""
    for attempt in range(3):
        try:
            r = requests.get(FF_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0 (personal macro dashboard)"})
            if r.ok:
                return r.json()
            err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            err = type(e).__name__
        time.sleep(10 * (attempt + 1))
    warnings.append(f"市場予想の取得に失敗（{err}）。前回までに貯めた予想だけ表示します")
    return None


def week_items(events: list[dict], disp_units: dict[str, str]) -> tuple[str, list[dict]] | None:
    """今週分の米国指標のうち、このツールの指標に対応するものだけ取り出す"""
    if not events:
        return None
    dates = sorted(e["date"][:10] for e in events if e.get("date"))
    if not dates:
        return None
    first = dt.date.fromisoformat(dates[0])
    week = (first - dt.timedelta(days=(first.weekday() + 1) % 7)).isoformat()  # その週の日曜日
    items = []
    for e in events:
        t = e.get("title")
        if e.get("country") != "USD" or t not in TITLE_SPEC:
            continue
        iid = TITLE_SPEC[t][0]
        unit, _ = spec_unit(t, disp_units.get(iid, ""))
        fc = parse_forecast(e.get("forecast", ""), unit)
        if fc is None:
            continue
        items.append({"id": iid, "date": e["date"][:10], "title": t, "fc": round(fc, 4), "fcs": e.get("forecast", "")})
    return week, items


def load_previous(site_url: str | None, offline: bool) -> dict:
    if offline or not site_url:
        return {}
    for attempt in range(3):
        try:
            r = requests.get(f"{site_url.rstrip('/')}/data/fc.json", timeout=30)
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            return r.json().get("weeks", {})
        except Exception:  # noqa: BLE001
            time.sleep(5 * (attempt + 1))
    return {}


def build(out_path: Path, today: dt.date, disp_units: dict[str, str], site_url: str | None,
          offline_dir: Path | None, warnings: list[str]) -> dict | None:
    """暗号化した予想（週ごと）を out_path に書く。戻り値は件数などの記録（中身の数値は含めない）"""
    if not PUBLIC_KEY_PATH.exists():
        return None
    pem = PUBLIC_KEY_PATH.read_bytes()
    weeks: dict[str, dict] = {}
    if SEED_PATH.exists():
        weeks.update(json.loads(SEED_PATH.read_text(encoding="utf-8")).get("weeks", {}))
    weeks.update(load_previous(site_url, bool(offline_dir)))
    info = {"fetched": False, "items": 0}
    events = fetch_ff(offline_dir, warnings)
    got = week_items(events, disp_units) if events else None
    if got:
        week, items = got
        blob = encrypt_blob({"src": "Forex Factory", "items": items}, pem)
        blob["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        weeks[week] = blob
        info = {"fetched": True, "items": len(items), "week": week}
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    weeks = {k: v for k, v in sorted(weeks.items()) if k >= cutoff}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"v": 1, "alg": "RSA-OAEP-256+A256GCM", "weeks": weeks}, separators=(",", ":")),
                        encoding="utf-8")
    info["weeks"] = sorted(weeks)
    return info
