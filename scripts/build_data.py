#!/usr/bin/env python3
"""
米国マクロ指標トラッカー：データ生成スクリプト

FRED API から指標を取得し、変換・判定・過去検証を行って docs/data/ に JSON を書き出す。

使い方:
  FRED_API_KEY=xxxx python scripts/build_data.py            # 本番（FREDから取得）
  python scripts/build_data.py --offline tests/fixtures     # テスト（手元のファイルから）

環境変数:
  FRED_API_KEY  FREDのAPIキー（本番で必須）
  SITE_URL      公開中のページURL（例 https://xxx.github.io/us-macro-tracker）
                ある指標の取得に失敗した時、前回公開したデータで代替するのに使う
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "indicators.json"
OUT_DIR = ROOT / "docs" / "data"
API = "https://api.stlouisfed.org/fred"
JST = dt.timezone(dt.timedelta(hours=9))

PERIODS_PER_YEAR = {"M": 12, "Q": 4, "W": 52}
MA_WINDOW = {"M": 3, "W": 4}
TREND_WINDOW = {"Q": 2, "M": 3, "W": 4, "D": 21}
CHANGE_LAG = {"Q": 1, "M": 1, "W": 1, "D": 5}
TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}
STATE_LABEL = {"good": "良好", "caution": "注意", "warn": "警戒", "na": "データ不足"}


# ----------------------------------------------------------------------------
# FRED アクセス
# ----------------------------------------------------------------------------
class Fred:
    def __init__(self, api_key: str | None, offline_dir: Path | None, site_url: str | None):
        self.key = api_key
        self.offline = offline_dir
        self.site_url = (site_url or "").rstrip("/")
        self.session = requests.Session()
        self.release_cache: dict[int, list[str]] = {}
        self.warnings: list[str] = []

    def _get(self, path: str, **params) -> dict:
        params.update(api_key=self.key, file_type="json")
        last_err = None
        for attempt in range(4):
            try:
                r = self.session.get(f"{API}/{path}", params=params, timeout=30)
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                time.sleep(0.55)  # FREDの上限（120回/分）に余裕を持たせる
                return r.json()
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"FRED {path} {params.get('series_id') or params.get('release_id')}: {last_err}")

    def observations(self, sid: str, start: str) -> pd.Series:
        if self.offline:
            data = json.loads((self.offline / f"{sid}.json").read_text(encoding="utf-8"))
        else:
            data = self._get("series/observations", series_id=sid, observation_start=start)
        return _obs_to_series(data["observations"])

    def fallback_observations(self, ind_id: str) -> pd.Series | None:
        """取得失敗時：前回公開したデータを読み込む"""
        if not self.site_url:
            return None
        try:
            r = self.session.get(f"{self.site_url}/data/series/{ind_id}.json", timeout=30)
            r.raise_for_status()
            d = r.json()
            s = pd.Series(d["raw"], index=pd.to_datetime(d["dates"]), dtype="float64").dropna()
            return s
        except Exception:  # noqa: BLE001
            return None

    def release_dates(self, sid: str, today: dt.date) -> list[str]:
        """発表日の一覧（米国日付）。直近10日の実績と今後の予定を返す"""
        if self.offline:
            f = self.offline / "_releases.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8")).get(sid, [])
            return []
        try:
            rel = self._get("series/release", series_id=sid)["releases"][0]
            rid = int(rel["id"])
            if rid not in self.release_cache:
                d = self._get(
                    "release/dates", release_id=rid,
                    realtime_start=(today - dt.timedelta(days=10)).isoformat(), realtime_end="9999-12-31",
                    include_release_dates_with_no_data="true", sort_order="asc", limit=40,
                )
                self.release_cache[rid] = sorted({x["date"] for x in d.get("release_dates", [])})
            return self.release_cache[rid]
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"発表予定の取得に失敗: {sid} ({e})")
            return []


def _obs_to_series(obs: list[dict]) -> pd.Series:
    dates, vals = [], []
    for o in obs:
        v = o.get("value")
        if v in (None, "", "."):
            continue
        dates.append(o["date"])
        vals.append(float(v))
    return pd.Series(vals, index=pd.to_datetime(dates), dtype="float64").sort_index()


# ----------------------------------------------------------------------------
# 変換・指標ごとの計算
# ----------------------------------------------------------------------------
def transform(raw: pd.Series, ind: dict) -> pd.Series:
    s = raw * ind.get("scale", 1.0)
    t = ind["transform"]
    if t == "none":
        out = s
    elif t == "diff":
        out = s.diff()
    elif t == "mom_pct":
        out = s.pct_change(fill_method=None) * 100
    elif t == "yoy_pct":
        n = PERIODS_PER_YEAR[ind["freq"]]
        out = (s / s.shift(n) - 1) * 100
    else:
        raise ValueError(f"unknown transform {t}")
    return out.replace([math.inf, -math.inf], math.nan)


def derive(spec: dict, raws: dict) -> pd.Series:
    """2つの系列から作る指標（例：2年債−FF金利）。両方に値がある日だけ計算する"""
    a, b = raws.get(spec["a"]), raws.get(spec["b"])
    if a is None or b is None:
        raise RuntimeError(f"元データ不足: {spec['a']} / {spec['b']}")
    if spec["op"] != "sub":
        raise ValueError(f"unknown op {spec['op']}")
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    return df["a"] - df["b"]


def zone_state(value: float | None, zones: list | None) -> str | None:
    if value is None or zones is None or (isinstance(value, float) and math.isnan(value)):
        return None
    for z in zones:
        if "lt" not in z or value < z["lt"]:
            return z["s"]
    return None


def f(x, nd=4):
    """JSON用：NaNはNone、floatは丸める"""
    if x is None:
        return None
    try:
        if isinstance(x, float) and math.isnan(x):
            return None
    except TypeError:
        pass
    if isinstance(x, (int,)):
        return x
    return round(float(x), nd)


def value_year_ago(v: pd.Series, freq: str) -> float | None:
    if len(v) == 0:
        return None
    if freq in PERIODS_PER_YEAR:
        n = PERIODS_PER_YEAR[freq]
        return float(v.iloc[-1 - n]) if len(v) > n else None
    target = v.index[-1] - pd.DateOffset(years=1)
    prev = v[v.index <= target]
    return float(prev.iloc[-1]) if len(prev) else None


def trend_info(v: pd.Series, ind: dict) -> dict:
    n = TREND_WINDOW[ind["freq"]]
    d = v.diff().dropna()
    if len(d) < 2 * n + 5:
        return {"dir": None, "turned": False, "text": "—"}
    recent, prior = d.iloc[-n:].mean(), d.iloc[-2 * n:-n].mean()
    flat = 0.25 * d.iloc[-240:].std()

    def sgn(x):
        return 0 if abs(x) < flat else (1 if x > 0 else -1)

    r, p = sgn(recent), sgn(prior)
    word = {1: "上向き", -1: "下向き", 0: "横ばい"}[r]
    turned = r != 0 and p != 0 and r != p
    # 良い方向か悪い方向か
    direction = ind["direction"]
    good = None
    if r != 0:
        if direction == "up":
            good = r > 0
        elif direction == "down":
            good = r < 0
        elif direction == "target":
            good = abs(v.iloc[-1] - 2.0) < abs(v.iloc[-1 - n] - 2.0) if len(v) > n else None
    unit = {"Q": "四半期", "M": "か月", "W": "週", "D": "営業日"}[ind["freq"]]
    span = f"直近{n}{unit}" if ind["freq"] != "Q" else f"直近{n}四半期"
    text = f"{span}は{word}" + ("（向きが転換）" if turned else "")
    return {"dir": int(r), "turned": bool(turned), "good": None if good is None else bool(good), "text": text}


def freshness(last_date: pd.Timestamp, ind: dict, today: dt.date) -> dict:
    age = (pd.Timestamp(today) - last_date).days
    lag = ind.get("pub_lag_days", 30)
    limit = {"D": 7, "W": lag + 17, "M": lag + 31 + 25, "Q": lag + 92 + 45}[ind["freq"]]
    return {"age_days": int(age), "stale": bool(age > limit)}


def downsample(v: pd.Series, years: int = 5, max_points: int = 130) -> list:
    v = v.dropna()
    if len(v) == 0:
        return []
    v = v[v.index >= v.index[-1] - pd.DateOffset(years=years)]
    if len(v) > max_points:
        step = math.ceil(len(v) / max_points)
        v = pd.concat([v.iloc[::step], v.iloc[[-1]]])
        v = v[~v.index.duplicated(keep="last")]
    return [f(x, 3) for x in v.values]


def indicator_metrics(ind: dict, raw: pd.Series, v: pd.Series, settings: dict, today: dt.date) -> dict:
    vv = v.dropna()
    if len(vv) == 0:
        return {"ok": False}
    last, last_date = float(vv.iloc[-1]), vv.index[-1]
    prev = float(vv.iloc[-2]) if len(vv) > 1 else None
    ya = value_year_ago(vv, ind["freq"])

    k = CHANGE_LAG[ind["freq"]]
    changes = vv.diff(k).dropna()
    hist = changes[changes.index >= changes.index[-1] - pd.DateOffset(years=20)] if len(changes) else changes
    z = None
    if len(hist) > 30 and hist.std() > 0:
        z = float((hist.iloc[-1] - hist.mean()) / hist.std())

    win = vv[vv.index >= last_date - pd.DateOffset(years=settings["percentile_years"])]
    pct = float((win < last).mean() * 100) if len(win) > 10 else None

    ma = None
    if ind["freq"] in MA_WINDOW and len(vv) >= MA_WINDOW[ind["freq"]]:
        ma = float(vv.iloc[-MA_WINDOW[ind["freq"]]:].mean())

    return {
        "ok": True,
        "latest": f(last), "latest_date": last_date.strftime("%Y-%m-%d"),
        "prev": f(prev), "change": f(last - prev) if prev is not None else None,
        "year_ago": f(ya), "yoy_change": f(last - ya) if ya is not None else None,
        "ma": f(ma),
        "z": f(z, 2), "big_move": bool(z is not None and abs(z) >= settings["big_move_z"]),
        "percentile_10y": f(pct, 0),
        "zone": zone_state(last, ind.get("zones")),
        "trend": trend_info(vv, ind),
        "fresh": freshness(last_date, ind, today),
        "max": {"value": f(vv.max()), "date": vv.idxmax().strftime("%Y-%m-%d")},
        "min": {"value": f(vv.min()), "date": vv.idxmin().strftime("%Y-%m-%d")},
        "n_obs": int(len(vv)),
        "spark": downsample(vv),
    }


# ----------------------------------------------------------------------------
# 5つの問いの判定（現在と過去の各月で同じ関数を使う）
# ----------------------------------------------------------------------------
class View:
    """ある時点(asof)で『発表済みだったデータ』だけを見せる窓"""

    def __init__(self, series: dict[str, pd.Series], lags: dict[str, int], asof: pd.Timestamp, use_lag: bool):
        self.s, self.lags, self.asof, self.use_lag = series, lags, asof, use_lag

    def get(self, key: str) -> pd.Series:
        s = self.s.get(key)
        if s is None:
            return pd.Series(dtype="float64")
        s = s.dropna()
        cutoff = self.asof - pd.Timedelta(days=self.lags.get(key, 1)) if self.use_lag else self.asof
        return s[s.index <= cutoff]

    def last(self, key: str):
        s = self.get(key)
        return (float(s.iloc[-1]), s.index[-1]) if len(s) else (None, None)


def _d(ts) -> str:
    return ts.strftime("%Y-%m") if ts is not None else "—"


def eval_growth(vw: View, p: dict) -> dict:
    gdp, gd = vw.last("gdp")
    ip, idt = vw.last("indpro")
    if gdp is None or ip is None:
        return {"state": "na", "reasons": ["データ不足"]}
    q = f"{gd.year}年Q{(gd.month - 1) // 3 + 1}"
    vals = f"実質GDP {gdp:+.1f}%（{q}）／鉱工業生産 前年比 {ip:+.1f}%（{_d(idt)}）"
    if gdp < p["gdp_bad"] or ip < p["ip_bad"]:
        why = []
        if gdp < p["gdp_bad"]:
            why.append("GDPがマイナス")
        if ip < p["ip_bad"]:
            why.append(f"鉱工業生産が{p['ip_bad']}%未満")
        return {"state": "warn", "reasons": [vals, "・".join(why)]}
    if gdp >= p["gdp_good"] and ip > p["ip_good"]:
        return {"state": "good", "reasons": [vals, "両方の良好条件を満たす"]}
    why = []
    if gdp < p["gdp_good"]:
        why.append(f"GDPが{p['gdp_good']}%未満")
    if ip <= p["ip_good"]:
        why.append("鉱工業生産が前年割れ")
    return {"state": "caution", "reasons": [vals, "・".join(why)]}


def eval_jobs(vw: View, p: dict) -> dict:
    sahm, sd = vw.last("sahm")
    nfp = vw.get("payems")
    if sahm is None or len(nfp) < 3:
        return {"state": "na", "reasons": ["データ不足"]}
    nfp3 = float(nfp.iloc[-3:].mean())
    vals = f"サーム・ルール {sahm:.2f}（{_d(sd)}）／雇用者数 3か月平均 {nfp3:+.1f}万人（{_d(nfp.index[-1])}）"
    if sahm >= p["sahm_bad"] or nfp3 < p["nfp3_bad"]:
        why = []
        if sahm >= p["sahm_bad"]:
            why.append(f"サーム・ルールが{p['sahm_bad']}以上")
        if nfp3 < p["nfp3_bad"]:
            why.append("雇用者数が減少")
        return {"state": "warn", "reasons": [vals, "・".join(why)]}
    if sahm < p["sahm_good"] and nfp3 > p["nfp3_good"]:
        return {"state": "good", "reasons": [vals, "両方の良好条件を満たす"]}
    why = []
    if sahm >= p["sahm_good"]:
        why.append(f"サーム・ルールが{p['sahm_good']}以上")
    if nfp3 <= p["nfp3_good"]:
        why.append(f"雇用の伸びが月{p['nfp3_good']:.0f}万人以下")
    return {"state": "caution", "reasons": [vals, "・".join(why)]}


def eval_inflation(vw: View, p: dict) -> dict:
    pce, cpi = vw.get("corepce"), vw.get("corecpi")
    if len(pce) < 4 or len(cpi) < 4:
        return {"state": "na", "reasons": ["データ不足"]}
    a, b = float(pce.iloc[-1]), float(cpi.iloc[-1])
    a_up, b_up = a > float(pce.iloc[-4]), b > float(cpi.iloc[-4])
    arrow = lambda up: "↑" if up else "↓"  # noqa: E731
    vals = (f"コアPCE {a:.2f}%{arrow(a_up)}（{_d(pce.index[-1])}）／"
            f"コアCPI {b:.2f}%{arrow(b_up)}（{_d(cpi.index[-1])}）　※矢印は3か月前との比較")
    if (a > p["bad_min"] and a_up) or (b > p["bad_min"] and b_up):
        return {"state": "warn", "reasons": [vals, f"{p['bad_min']}%超で上昇中の指標がある"]}
    if a <= p["good_max"] and b <= p["good_max"]:
        return {"state": "good", "reasons": [vals, f"どちらも{p['good_max']}%以下"]}
    return {"state": "caution", "reasons": [vals, f"{p['good_max']}%を上回っている（目標2%まで距離がある）"]}


def eval_financial(vw: View, p: dict) -> dict:
    rr_s, y2 = vw.get("realrate"), vw.get("dgs2")
    if len(rr_s) < 5 or len(y2) < 70:
        return {"state": "na", "reasons": ["データ不足（実質金利は2003年から）"]}
    rr = float(rr_s.iloc[-5:].mean())
    now = float(y2.iloc[-5:].mean())
    back = y2[y2.index <= y2.index[-1] - pd.Timedelta(days=91)]
    if len(back) < 5:
        return {"state": "na", "reasons": ["データ不足"]}
    d2y = now - float(back.iloc[-5:].mean())
    vals = f"実質金利 {rr:.2f}%／2年債 {now:.2f}%（3か月で{d2y:+.2f}pt）　※5営業日平均"
    if rr > p["rr_bad"] and d2y > 0:
        return {"state": "warn", "reasons": [vals, f"実質金利{p['rr_bad']}%超 かつ 2年債が上昇（引き締めが強まっている）"]}
    if rr < p["rr_good"] or d2y <= p["d2y_good"]:
        why = f"実質金利が{p['rr_good']}%未満" if rr < p["rr_good"] else "2年債が大きく低下（利下げの織り込み）"
        return {"state": "good", "reasons": [vals, why]}
    return {"state": "caution", "reasons": [vals, "金利水準は高めだが、引き締めの強まりは見られない" if d2y <= 0 else "金利水準が高め"]}


def eval_recession(vw: View, p: dict) -> dict:
    sp = vw.get("t10y3m")
    permit, pdt = vw.last("permit")
    if len(sp) < 260 or permit is None:
        return {"state": "na", "reasons": ["データ不足"]}
    monthly = sp.resample("ME").mean()
    cur = float(sp.iloc[-21:].mean())
    past12 = monthly.iloc[-13:-1] if len(monthly) >= 13 else monthly.iloc[:-1]
    inverted_recently = bool((past12 < 0).any())
    uninv = cur > 0 and inverted_recently
    vals = f"10年-3か月差 {cur:+.2f}pt（21営業日平均）／建築許可 前年比 {permit:+.1f}%（{_d(pdt)}）"
    if uninv or permit < p["permit_bad"]:
        why = []
        if uninv:
            why.append("逆イールドが解消してから12か月以内")
        if permit < p["permit_bad"]:
            why.append(f"建築許可が{p['permit_bad']}%未満")
        return {"state": "warn", "reasons": [vals, "・".join(why)]}
    if cur > 0 and permit >= p["permit_good"]:
        return {"state": "good", "reasons": [vals, "逆イールドなし・住宅許可も底堅い"]}
    why = []
    if cur <= 0:
        why.append("逆イールド中")
    if permit < p["permit_good"]:
        why.append(f"建築許可が{p['permit_good']}%未満")
    return {"state": "caution", "reasons": [vals, "・".join(why)]}


EVALUATORS = {
    "growth": eval_growth, "jobs": eval_jobs, "inflation": eval_inflation,
    "financial": eval_financial, "recession": eval_recession,
}

CYCLE = {
    (True, True): "逆金融相場", (True, False): "業績相場",
    (False, True): "逆業績相場", (False, False): "金融相場",
}


def cycle_raw(growth_state: str, vw: View) -> str | None:
    ff = vw.get("dff")
    if growth_state == "na" or len(ff) < 200:
        return None
    back = ff[ff.index <= ff.index[-1] - pd.Timedelta(days=182)]
    if len(back) == 0:
        return None
    rising = float(ff.iloc[-1]) - float(back.iloc[-1]) > -0.1
    return CYCLE[(growth_state == "good", rising)]


# ----------------------------------------------------------------------------
# 過去検証
# ----------------------------------------------------------------------------
def run_history(cfg: dict, tseries: dict, lags: dict, usrec: pd.Series, today: dt.date) -> dict:
    start = pd.Timestamp(cfg["settings"]["history_start"])
    months = pd.date_range(start, pd.Timestamp(today), freq="ME")
    if len(months) == 0 or months[-1] < pd.Timestamp(today):
        months = months.append(pd.DatetimeIndex([pd.Timestamp(today)]))  # 今月（途中）も含める
    qids = [q["id"] for q in cfg["questions"]]
    params = {q["id"]: q["params"] for q in cfg["questions"]}
    code = {"good": "g", "caution": "c", "warn": "w", "na": "n"}
    states = {q: [] for q in qids}
    cyc_raw = []
    for m in months:
        vw = View(tseries, lags, m, use_lag=True)
        res = {q: EVALUATORS[q](vw, params[q])["state"] for q in qids}
        for q in qids:
            states[q].append(code[res[q]])
        cyc_raw.append(cycle_raw(res["growth"], vw))

    # 2か月連続で同じ判定の時だけ局面を切り替える
    cycle, cur = [], None
    for i, c in enumerate(cyc_raw):
        if c is not None and i > 0 and cyc_raw[i - 1] == c:
            cur = c
        cycle.append(cur)

    rec = usrec.reindex(months, method="ffill").fillna(0).astype(int) if len(usrec) else pd.Series(0, index=months)
    rec_str = "".join(str(x) for x in rec.values)

    # 景気後退の開始月
    starts = [i for i in range(1, len(rec_str)) if rec_str[i] == "1" and rec_str[i - 1] == "0"]
    leads = []
    for i in starts:
        row = {"start": months[i].strftime("%Y-%m"), "q": {}}
        for q in qids:
            s = states[q]
            window = s[max(0, i - 24): i + 1]
            first = next((k for k, c in enumerate(window) if c == "w"), None)
            row["q"][q] = {
                "months_before": (len(window) - 1 - first) if first is not None else None,
                "warn_at_start": s[i] == "w",
            }
        leads.append(row)

    # 当たり率：警戒の月のうち、24か月以内に景気後退が始まった割合（直近24か月は未確定のため除外）
    precision = {}
    horizon = len(months) - 24
    for q in qids:
        s = states[q]
        cand = [i for i in range(max(0, horizon)) if s[i] == "w" and rec_str[i] == "0"]
        hit = [i for i in cand if any(st for st in starts if i < st <= i + 24)]
        precision[q] = {
            "warn_months": len(cand), "hit_months": len(hit),
            "rate": f(len(hit) / len(cand) * 100, 0) if cand else None,
        }

    return {
        "months": [m.strftime("%Y-%m") for m in months],
        "states": {q: "".join(v) for q, v in states.items()},
        "usrec": rec_str,
        "cycle": cycle,
        "leads": leads,
        "precision": precision,
        "note": "各月末時点で『発表済みだったはずのデータ』だけで判定（発表までの日数は概算）。後からの改定値を使っているため、当時の速報値での判定とは一致しない場合がある。",
    }


def recession_periods(usrec: pd.Series) -> list:
    out, start = [], None
    for d, v in usrec.items():
        if v == 1 and start is None:
            start = d
        if v == 0 and start is not None:
            out.append([start.strftime("%Y-%m-%d"), (d - pd.Timedelta(days=1)).strftime("%Y-%m-%d")])
            start = None
    if start is not None:
        out.append([start.strftime("%Y-%m-%d"), usrec.index[-1].strftime("%Y-%m-%d")])
    return out


# ----------------------------------------------------------------------------
# AIに渡すためのテキスト（要約版・時系列つき）
# ----------------------------------------------------------------------------
FREQ_JA = {"D": "日次", "W": "週次", "M": "月次", "Q": "四半期"}
TRANSFORM_JA = {"none": "水準", "yoy_pct": "前年同期比", "mom_pct": "前月比", "diff": "前月差"}
ZONE_JA = {"good": "良好", "caution": "注意", "warn": "警戒", None: "—"}


def _n(x, d):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:,.{d}f}"


def _s(x, d):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:+,.{d}f}"


def build_ai_text(generated, today, settings, qcfg, q_out, ind_out, tseries, notable, stale, upcoming,
                  failed, fallback_used, history, cycle_now, with_series: bool) -> str:
    tl = settings.get("tier_labels", {})
    qname = {q["id"]: q["name"] for q in qcfg}
    qname["reference"] = "参考"
    L = []
    L.append(f"# 米国マクロ指標トラッカー 分析データ（{generated} 日本時間 更新／基準日 {today}）")
    L.append("")
    L.append("## このデータの扱い方（AIへの指示）")
    L.append("- 出典はFRED（セントルイス連邦準備銀行）。数値はすべてツールがFREDから取得・計算した値で、推測で補っていない。")
    L.append("- 5つの問いの判定は、下記ルールによる機械的な集計であり、投資助言ではない。")
    L.append("- 各数値には「データの日付」がある。月次・四半期の指標は発表が1〜4か月遅れるため、日付を必ず確認して解釈すること。")
    L.append(f"- 「10年位置」は過去10年の中で下から何%の水準か。「z」は直近の変化幅が過去20年の変化の中でどれだけ珍しいか（±{settings.get('big_move_z', 2.0):.1f}以上で大きな変化）。")
    L.append("- " + settings.get("tier_rule", ""))
    L.append("")

    L.append("## 1. 5つの問いの判定")
    L.append("| 問い | 判定 | 根拠（最新値） | 理由 |")
    L.append("|---|---|---|---|")
    for q in q_out:
        L.append(f"| {q['question']} | {q['label']} | {q['reasons'][0] if q['reasons'] else ''} | {q['reasons'][1] if len(q['reasons']) > 1 else ''} |")
    L.append("")
    L.append("判定ルール：")
    iname = {i["id"]: i["name"] for i in ind_out}
    for q in qcfg:
        L.append(f"- {q['name']}（主役：{'・'.join(iname.get(m, m) for m in q['main'])}）：{q['rule_text']}")
    if settings.get("show_cycle") and cycle_now:
        L.append(f"- 景気サイクル（参考）：{cycle_now}")
    L.append("")

    L.append(f"## 2. 指標一覧（{len(ind_out)}本）")
    L.append("| 分類 | 重要度 | 役割 | 指標 | 最新値 | データ日付 | 前回から | 1年前から | 10年位置 | 状態 | 向き | z | 次回発表 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    role_ja = {"main": "主役", "sub": "補助", "ref": "参考"}
    for i in ind_out:
        if not i.get("ok"):
            L.append(f"| {qname.get(i['question'], '')} | {tl.get(i.get('tier'), '')} | {role_ja.get(i['role'], '')} | {i['name']} | 取得失敗 | | | | | | | | |")
            continue
        d, u = i["decimals"], i["unit"]
        cu = "pt" if u == "%" else u
        yoy = "—" if i["transform"] in ("mom_pct", "diff") else f"{_s(i['yoy_change'], d)}{cu}"
        stale_mark = "（更新遅れ）" if i["fresh"]["stale"] else ""
        L.append(
            f"| {qname.get(i['question'], '')} | {tl.get(i.get('tier'), '')} | {role_ja.get(i['role'], '')} | {i['name']} "
            f"| {_n(i['latest'], d)}{u} | {i['latest_date']}{stale_mark} | {_s(i['change'], d)}{cu} | {yoy} "
            f"| {'—' if i['percentile_10y'] is None else str(int(i['percentile_10y'])) + '%'} | {ZONE_JA.get(i.get('zone'))} "
            f"| {i['trend']['text']} | {_s(i['z'], 1)} | {i.get('next_release') or ('毎営業日' if i['freq'] == 'D' else '—')} |")
    L.append("")
    L.append("指標の定義：")
    for i in ind_out:
        src = f"FRED {i['fred']}" if not i.get("derived") else f"FRED {i['fred']}（ツールで計算）"
        L.append(f"- {i['name']}：{src}／{FREQ_JA[i['freq']]}／{TRANSFORM_JA[i['transform']]}（{i['unit'] or '指数'}）。{i['desc']} 見方：{i['read']}")
    L.append("")

    L.append("## 2-2. 最近の発表結果（直近1週間・米国日付）")
    rec = [i for i in ind_out if i.get("ok") and i.get("last_release")]
    rec.sort(key=lambda i: (i["last_release"], -TIER_ORDER.get(i.get("tier"), 9)), reverse=True)
    if not rec:
        L.append("- なし")
    for i in rec:
        cu = "pt" if i["unit"] == "%" else i["unit"]
        L.append(f"- {i['last_release']} {i['name']}【{tl.get(i.get('tier'), '')}】：{_n(i['latest'], i['decimals'])}{i['unit']}"
                 f"（{i['latest_date']}分、前回 {_n(i['prev'], i['decimals'])}{i['unit']}、変化 {_s(i['change'], i['decimals'])}{cu}）")
    L.append("")
    L.append("## 3. 気になる動き")
    if not (notable or stale or failed or fallback_used):
        L.append("- 特になし")
    for n in notable:
        L.append(f"- 大きな変化：{n['name']}（直近の変化 {_s(n['change'], 2)}、z={_s(n['z'], 1)}、{n['latest_date']}）")
    for x in stale:
        L.append(f"- 更新遅れ：{x['name']}（最新データ {x['latest_date']}、{x['age_days']}日前）")
    for x in failed:
        L.append(f"- 取得失敗：{x['name']}")
    if fallback_used:
        L.append("- 前回データで代替：" + "、".join(fallback_used))
    L.append("")

    L.append("## 4. 発表予定（今後3週間・米国日付）")
    if not upcoming:
        L.append("- 取得できず")
    for u in upcoming:
        L.append(f"- {u['date']}：" + "、".join(f"{x['name']}【{tl.get(x['tier'], '')}】" for x in u["items"]))
    L.append("")

    L.append("## 5. 過去の判定（ルールの検証）")
    L.append(history.get("note", ""))
    qs = [q["id"] for q in qcfg]
    L.append("")
    L.append("| 景気後退の開始 | " + " | ".join(qname[q] for q in qs) + " |")
    L.append("|---|" + "---|" * len(qs))
    for l in history["leads"]:
        cells = []
        for q in qs:
            x = l["q"][q]
            cells.append("警戒なし" if x["months_before"] is None else ("開始月に警戒" if x["months_before"] == 0 else f"{x['months_before']}か月前から警戒"))
        L.append(f"| {l['start']} | " + " | ".join(cells) + " |")
    L.append("| 当たり率 | " + " | ".join(
        "—" if history["precision"][q]["rate"] is None else f"{int(history['precision'][q]['rate'])}%（警戒{history['precision'][q]['warn_months']}か月中）" for q in qs) + " |")
    L.append("")
    L.append("直近24か月の判定の推移（g=良好 c=注意 w=警戒 n=データ不足、左が古い）：")
    months = history["months"][-24:]
    L.append(f"- 期間：{months[0]} 〜 {months[-1]}")
    for q in qs:
        L.append(f"- {qname[q]}：{history['states'][q][-24:]}")
    L.append("")

    if with_series:
        L.append("## 6. 直近の推移（月次は月末時点、日次・週次は月平均、四半期は各期）")
        for i in ind_out:
            v = tseries.get(i["id"])
            if v is None or not i.get("ok"):
                continue
            v = v.dropna()
            if i["freq"] == "Q":
                pts = v.iloc[-8:]
                lab = [f"{d.year}Q{(d.month - 1) // 3 + 1}" for d in pts.index]
            elif i["freq"] == "M":
                pts = v.iloc[-24:]
                lab = [d.strftime("%Y-%m") for d in pts.index]
            else:
                pts = v.resample("ME").mean().dropna().iloc[-24:]
                lab = [d.strftime("%Y-%m") for d in pts.index]
            vals = ", ".join(f"{a} {_n(float(b), i['decimals'])}" for a, b in zip(lab, pts.values))
            L.append(f"- {i['name']}（{i['unit'] or '指数'}）：{vals}")
        L.append("")

    L.append("## 注意点")
    L.append("- 過去検証は改定後の値を使っており、当時の速報値での判定とは一致しない場合がある。")
    L.append("- ミシガン大学指数はFREDでは提供元の要請により1か月遅れ。")
    L.append("- S&P500（転載禁止）とハイイールド債スプレッド（公開には事前許可が必要）は含めていない。ISM製造業指数はFREDから削除済みのため、フィラデルフィア連銀の指数で代用。")
    L.append("- 日次の金利は、日本時間1:47の実行時点で取得できる最新（米国の前営業日の終値）。")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------------
def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", type=Path, help="FREDの代わりに読むフィクスチャのフォルダ（テスト用）")
    ap.add_argument("--today", help="基準日 YYYY-MM-DD（テスト用）")
    args = ap.parse_args()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    settings = cfg["settings"]
    now_jst = dt.datetime.now(JST)
    today = dt.date.fromisoformat(args.today) if args.today else now_jst.date()

    key = os.environ.get("FRED_API_KEY")
    if not args.offline and not key:
        print("FRED_API_KEY が設定されていません", file=sys.stderr)
        return 1
    fred = Fred(key, args.offline, os.environ.get("SITE_URL"))

    series_dir = OUT_DIR / "series"
    series_dir.mkdir(parents=True, exist_ok=True)

    tseries: dict[str, pd.Series] = {}
    raws: dict[str, pd.Series] = {}
    lags: dict[str, int] = {}
    ind_out = []
    failed, fallback_used = [], []

    for ind in cfg["indicators"]:
        iid = ind["id"]
        source = "fred"
        try:
            if ind.get("derived"):
                raw = derive(ind["derived"], raws)
            else:
                raw = fred.observations(ind["fred"], settings["fetch_start"])
            if len(raw) == 0:
                raise RuntimeError("データが空")
        except Exception as e:  # noqa: BLE001
            raw = fred.fallback_observations(iid)
            if raw is None or len(raw) == 0:
                failed.append({"id": iid, "name": ind["name"], "error": str(e)[:200]})
                print(f"[失敗] {iid}: {e}", file=sys.stderr)
                continue
            source = "fallback"
            fallback_used.append(ind["name"])
            print(f"[代替] {iid}: 前回データを使用", file=sys.stderr)

        raws[iid] = raw
        v = transform(raw, ind)
        tseries[iid] = v
        lags[iid] = ind.get("pub_lag_days", 1)

        ma = v.rolling(MA_WINDOW[ind["freq"]]).mean() if ind["freq"] in MA_WINDOW else None
        write_json(series_dir / f"{iid}.json", {
            "id": iid,
            "dates": [d.strftime("%Y-%m-%d") for d in raw.index],
            "raw": [f(x, 6) for x in raw.values],
            "v": [f(x, 4) for x in v.values],
            "ma": [f(x, 4) for x in ma.values] if ma is not None else None,
        })

        m = indicator_metrics(ind, raw, v, settings, today)
        m["source"] = source
        rd = [] if ind["freq"] == "D" or ind.get("derived") else fred.release_dates(ind["fred"], today)
        nxt = [d for d in rd if d >= today.isoformat()]
        past = [d for d in rd if (today - dt.timedelta(days=8)).isoformat() <= d < today.isoformat()]
        m["next_release"] = nxt[0] if nxt else None
        m["upcoming"] = nxt[:3]
        m["last_release"] = past[-1] if past else None
        meta = {k: ind.get(k) for k in (
            "id", "fred", "derived", "name", "question", "role", "tier", "freq", "transform", "unit", "decimals",
            "direction", "zones", "ref_line", "desc", "read", "good", "caution", "citation")}
        ind_out.append({**meta, **m})

    # NBER景気後退期
    usrec = pd.Series(dtype="float64")
    for h in cfg.get("hidden_series", []):
        if h["id"] == "usrec":
            try:
                usrec = fred.observations(h["fred"], settings["fetch_start"])
            except Exception as e:  # noqa: BLE001
                fred.warnings.append(f"景気後退期データの取得に失敗 ({e})")

    # 現在の判定（発表済みデータを全部使う）
    vw_now = View(tseries, lags, pd.Timestamp(today) + pd.Timedelta(days=1), use_lag=False)
    q_out = []
    for q in cfg["questions"]:
        r = EVALUATORS[q["id"]](vw_now, q["params"])
        q_out.append({
            "id": q["id"], "name": q["name"], "question": q["question"], "main": q["main"],
            "rule_text": q["rule_text"], "state": r["state"], "label": STATE_LABEL[r["state"]],
            "reasons": r["reasons"],
        })

    history = run_history(cfg, tseries, lags, usrec, today)
    cycle_now = history["cycle"][-1] if history["cycle"] else None

    # 注目（大きな変化・古いデータ）
    notable = [
        {"id": i["id"], "name": i["name"], "z": i["z"], "change": i["change"], "unit": i["unit"],
         "latest": i["latest"], "latest_date": i["latest_date"]}
        for i in ind_out if i.get("ok") and i.get("big_move")
    ]
    stale = [{"id": i["id"], "name": i["name"], "latest_date": i["latest_date"], "age_days": i["fresh"]["age_days"]}
             for i in ind_out if i.get("ok") and i["fresh"]["stale"]]

    # 発表予定（今日から21日）
    cal: dict[str, dict[str, str]] = {}
    horizon = (today + dt.timedelta(days=21)).isoformat()
    for i in ind_out:
        for d in i.get("upcoming", []):
            if today.isoformat() <= d <= horizon:
                cal.setdefault(d, {})[i["name"]] = i.get("tier", "C")
    upcoming = [{"date": d, "items": [{"name": n, "tier": t} for n, t in sorted(x.items(), key=lambda kv: (TIER_ORDER.get(kv[1], 9), kv[0]))]}
                for d, x in sorted(cal.items())]
    tier_label = settings.get("tier_labels", {})

    # 最近の発表結果（直近1週間に発表があった指標。新しい順→重要度順）
    recent = [
        {"date": i["last_release"], "id": i["id"], "name": i["name"], "tier": i.get("tier"), "latest": i["latest"],
         "latest_date": i["latest_date"], "prev": i["prev"], "change": i["change"], "unit": i["unit"],
         "decimals": i["decimals"], "direction": i["direction"], "freq": i["freq"], "big_move": i["big_move"]}
        for i in ind_out if i.get("ok") and i.get("last_release")
    ]
    recent.sort(key=lambda x: (x["date"], -TIER_ORDER.get(x["tier"], 9)), reverse=True)

    mark = {"good": "良好", "caution": "注意", "warn": "警戒", "na": "データ不足"}
    lines = [f"【米国マクロ指標トラッカー {today.isoformat()}】"]
    for q in q_out:
        lines.append(f"・{q['name']}：{mark[q['state']]}｜{q['reasons'][0]}")
    if settings.get("show_cycle") and cycle_now:
        lines.append(f"・景気サイクル（参考）：{cycle_now}")
    if notable:
        lines.append("・大きな変化：" + "、".join(f"{n['name']}(z={n['z']:+.1f})" for n in notable))
    if stale:
        lines.append("・更新が遅れている指標：" + "、".join(s["name"] for s in stale))
    if upcoming:
        def _cal(u):
            return "・".join(f"{x['name']}【{tier_label.get(x['tier'], '')}】" if x["tier"] in ("S", "A") else x["name"] for x in u["items"])
        lines.append("・次の発表：" + "／".join(f"{u['date'][5:]} {_cal(u)}" for u in upcoming[:4]))

    generated = now_jst.strftime("%Y-%m-%d %H:%M")
    common = {"generated_at_jst": generated, "today": today.isoformat(), "test_mode": bool(args.offline)}

    summary = {
        **common,
        "questions": q_out,
        "cycle": cycle_now if settings.get("show_cycle") else None,
        "notable": notable, "stale": stale, "upcoming": upcoming, "recent": recent,
        "failed": failed, "fallback_used": fallback_used, "warnings": fred.warnings,
        "handoff_text": "\n".join(lines),
    }
    write_json(OUT_DIR / "summary.json", summary)
    write_json(OUT_DIR / "indicators.json", {
        **common,
        "settings": {k: settings.get(k) for k in ("show_cycle", "site_title", "big_move_z", "tier_labels", "tier_rule")},
        "questions": [{k: q[k] for k in ("id", "name", "question", "main", "rule_text")} for q in cfg["questions"]],
        "indicators": ind_out,
        "recessions": recession_periods(usrec) if len(usrec) else [],
    })
    write_json(OUT_DIR / "history.json", history)
    for fname, ws in (("ai_brief.md", False), ("ai_full.md", True)):
        (OUT_DIR / fname).write_text(build_ai_text(
            generated, today.isoformat(), settings, cfg["questions"], q_out, ind_out, tseries, notable, stale,
            upcoming, failed, fallback_used, history, cycle_now, with_series=ws), encoding="utf-8")

    print("\n".join(lines))
    print(f"\n取得失敗 {len(failed)}件 / 代替 {len(fallback_used)}件 / 警告 {len(fred.warnings)}件")
    # 主役指標が1つでも欠けたら失敗扱い（壊れたページを公開しない）
    mains = {m for q in cfg["questions"] for m in q["main"]}
    if any(x["id"] in mains for x in failed):
        print("主役指標の取得に失敗したため中止します", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
