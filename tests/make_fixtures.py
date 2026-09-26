#!/usr/bin/env python3
"""テスト用の『偽データ』を作る（本物のFREDデータではない。画面と処理の動作確認専用）"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "fixtures")
OUT.mkdir(parents=True, exist_ok=True)
END = pd.Timestamp("2026-09-24")
rng = np.random.default_rng(7)

cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "indicators.json").read_text(encoding="utf-8"))

# 景気後退期（テスト用に実在の時期を模す）
REC = [("1990-08", "1991-03"), ("2001-04", "2001-11"), ("2008-01", "2009-06"), ("2020-03", "2020-04")]


def dates(freq):
    if freq == "D":
        return pd.bdate_range("1985-01-01", END)
    if freq == "W":
        return pd.date_range("1985-01-05", END, freq="W-SAT")
    if freq == "M":
        return pd.date_range("1985-01-01", END - pd.Timedelta(days=40), freq="MS")
    return pd.date_range("1985-01-01", END - pd.Timedelta(days=150), freq="QS")


def rec_mask(idx):
    m = np.zeros(len(idx), bool)
    for a, b in REC:
        m |= (idx >= pd.Timestamp(a)) & (idx <= pd.Timestamp(b) + pd.offsets.MonthEnd(0))
    return m


def level_series(idx, base, drift, vol, rec_shock):
    r = rec_mask(idx)
    steps = rng.normal(drift, vol, len(idx)) + np.where(r, rec_shock, 0)
    return base * np.exp(np.cumsum(steps))


def rate_series(idx, start, vol, lo, hi):
    x = np.empty(len(idx))
    x[0] = start
    for i in range(1, len(idx)):
        x[i] = np.clip(x[i - 1] + rng.normal(0, vol), lo, hi)
    return x


SPEC = {
    "A191RL1Q225SBEA": lambda i: np.where(rec_mask(i), rng.normal(-2, 1.5, len(i)), rng.normal(2.4, 1.2, len(i))),
    "INDPRO": lambda i: level_series(i, 60, 0.0015, 0.006, -0.012),
    "RSAFS": lambda i: level_series(i, 120000, 0.004, 0.008, -0.01),
    "DGORDER": lambda i: level_series(i, 90000, 0.003, 0.03, -0.03),
    "UMCSENT": lambda i: np.clip(rate_series(i, 90, 2.5, 50, 110) - rec_mask(i) * 15, 45, 112),
    "GACDFSA066MSFRBPHI": lambda i: np.clip(rng.normal(8, 12, len(i)) - rec_mask(i) * 25, -60, 50),
    "PAYEMS": lambda i: level_series(i, 97000, 0.0012, 0.0008, -0.004),
    "SAHMREALTIME": lambda i: np.clip(rng.normal(0.05, 0.1, len(i)) + rec_mask(i) * 0.9, -0.3, 3),
    "UNRATE": lambda i: np.clip(rate_series(i, 7.2, 0.12, 3.4, 9) + rec_mask(i) * 1.5, 3.4, 14.8),
    "ICSA": lambda i: np.clip(level_series(i, 350000, -0.0005, 0.02, 0.01), 180000, 900000),
    "JTSJOL": lambda i: np.clip(level_series(i, 5000, 0.001, 0.02, -0.02), 3000, 12000),
    "CES0500000003": lambda i: level_series(i, 8, 0.003, 0.002, 0),
    "LES1252881600Q": lambda i: level_series(i, 330, 0.002, 0.006, 0),
    "PCEPILFE": lambda i: level_series(i, 50, 0.0022, 0.0012, 0),
    "CPILFESL": lambda i: level_series(i, 100, 0.0025, 0.0012, 0),
    "CPIAUCSL": lambda i: level_series(i, 100, 0.0024, 0.003, -0.002),
    "PPIFIS": lambda i: level_series(i, 100, 0.002, 0.004, -0.004),
    "DFII10": lambda i: rate_series(i, 2.0, 0.03, -1.2, 3.0),
    "DGS2": lambda i: rate_series(i, 8.0, 0.04, 0.1, 9),
    "DFF": lambda i: rate_series(i, 8.0, 0.02, 0.05, 9),
    "DGS10": lambda i: rate_series(i, 10.0, 0.04, 0.5, 11),
    "DTWEXBGS": lambda i: level_series(i, 100, 0, 0.004, 0),
    "WALCL": lambda i: level_series(i, 700000, 0.0015, 0.003, 0.01),
    "T10Y3M": lambda i: rate_series(i, 1.5, 0.03, -1.8, 3.8),
    "PERMIT": lambda i: np.clip(level_series(i, 1500, 0, 0.03, -0.04), 400, 2400),
    "T10Y2Y": lambda i: rate_series(i, 1.0, 0.03, -1.1, 2.9),
    "HOUST": lambda i: np.clip(level_series(i, 1500, 0, 0.04, -0.04), 400, 2400),
    "VIXCLS": lambda i: np.clip(12 + np.abs(rate_series(i, 5, 0.8, -20, 60)) + rec_mask(i) * 20, 9, 82),
    "BOPGSTB": lambda i: rate_series(i, -10000, 1500, -100000, 0),
    "IEABC": lambda i: rate_series(i, -30000, 6000, -300000, 0),
}

releases = {}
for ind in cfg["indicators"]:
    sid, freq = ind["fred"], ind["freq"]
    idx = dates(freq)
    if ind.get("derived"):
        continue
    if sid in ("DFII10",):
        idx = idx[idx >= "2003-01-02"]
    vals = SPEC[sid](idx)
    obs = [{"date": d.strftime("%Y-%m-%d"), "value": f"{v:.4f}"} for d, v in zip(idx, vals)]
    if freq == "D":  # 祝日の欠損値（FREDは "." で返す）も再現
        for k in range(10, len(obs), 250):
            obs[k]["value"] = "."
    (OUT / f"{sid}.json").write_text(json.dumps({"observations": obs}), encoding="utf-8")
    if freq != "D":
        releases[sid] = [(END + pd.Timedelta(days=int(rng.integers(2, 25)))).strftime("%Y-%m-%d")]

idx = pd.date_range("1985-01-01", END - pd.Timedelta(days=60), freq="MS")
usrec = rec_mask(idx).astype(int)
(OUT / "USREC.json").write_text(json.dumps({"observations": [
    {"date": d.strftime("%Y-%m-%d"), "value": str(v)} for d, v in zip(idx, usrec)]}), encoding="utf-8")
(OUT / "_releases.json").write_text(json.dumps(releases), encoding="utf-8")
print(f"fixtures -> {OUT} ({len(cfg['indicators']) + 1} series)")
