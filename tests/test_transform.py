"""前年比・前月比の計算テスト（欠けた月がある時に日付どおりに比べるか）

実行: python tests/test_transform.py
背景：2025年10月のCPIは政府閉鎖で未発表（FREDでは空欄）。以前はデータの個数で12個前と比べていたため、
     2026年9月の前年比を2025年8月と比べてしまい、CPI前年比が3.72%→4.02%と高く出ていた。
"""
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_data as b  # noqa: E402

ok = True


def check(name, cond, detail=""):
    global ok
    print(("OK " if cond else "NG ") + name + (f"  {detail}" if detail else ""))
    ok &= bool(cond)


# FREDのCPI（季節調整済み）の実際の値（2026-10-20時点）。2025年10月は空欄
cpi = pd.Series({"2025-08-01": 323.291, "2025-09-01": 324.245, "2025-11-01": 325.063, "2025-12-01": 326.031})
cpi.index = pd.to_datetime(cpi.index)
# 2026年9月の値は、FREDの前年比 3.71725% から逆算
cpi[pd.Timestamp("2026-08-01")] = 323.291 * 1.0335302
cpi[pd.Timestamp("2026-09-01")] = 324.245 * 1.0371725
cpi = cpi.sort_index()

y = b.transform(cpi, {"transform": "yoy_pct", "freq": "M"})
check("CPI前年比 2026年9月＝3.72%（FREDと一致）", abs(y[pd.Timestamp("2026-09-01")] - 3.71725) < 1e-4, f"{y[pd.Timestamp('2026-09-01')]:.4f}")
check("CPI前年比 2026年8月＝3.35%（FREDと一致）", abs(y[pd.Timestamp("2026-08-01")] - 3.35302) < 1e-4, f"{y[pd.Timestamp('2026-08-01')]:.4f}")

m = b.transform(cpi, {"transform": "mom_pct", "freq": "M"})
check("前月が欠けた月（2025年11月）の前月比は出さない", math.isnan(m[pd.Timestamp("2025-11-01")]))
check("2025年12月の前月比は11月と比べる", abs(m[pd.Timestamp("2025-12-01")] - (326.031 / 325.063 - 1) * 100) < 1e-9)

# 1年前の月だけ欠けている（2026年10月）→ 2025年9月と11月の幾何平均で推定
s2 = pd.concat([cpi, pd.Series({pd.Timestamp("2026-10-01"): 340.0})])
base, est = b.yoy_base(s2, 12, "M")
exp_base = math.sqrt(324.245 * 325.063)
check("1年前が欠けた月は前後の月から推定", abs(base[pd.Timestamp("2026-10-01")] - exp_base) < 1e-9 and bool(est[pd.Timestamp("2026-10-01")]))
check("推定は欠けた月のときだけ", not bool(est[pd.Timestamp("2026-09-01")]))

d = b.transform(pd.Series([100.0, 103.0, 110.0], index=pd.to_datetime(["2025-09-01", "2025-11-01", "2025-12-01"])),
                {"transform": "diff", "freq": "M"})
check("前月差：前月が欠けていれば出さない", math.isnan(d.iloc[1]) and d.iloc[2] == 7.0)

v = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2025-09-01", "2025-11-01", "2026-09-01"]))
check("1年前の値は日付で探す（2025年9月）", b.value_year_ago(v, "M") == 1.0)
v2 = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2025-09-01", "2025-11-01", "2026-10-01"]))
check("1年前の月が欠けていれば出さない（前の月で代用しない）", b.value_year_ago(v2, "M") is None)

me = pd.Series([100.0, 101.0, 102.0], index=pd.to_datetime(["2026-01-31", "2026-02-28", "2026-03-31"]))
mm = b.transform(me, {"transform": "mom_pct", "freq": "M"})
check("月末の日付でも前月比が出る", not math.isnan(mm.iloc[1]) and not math.isnan(mm.iloc[2]))
qe = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-03-31", "2026-06-30"]))
check("四半期末の日付でも前期差が出る", b.transform(qe, {"transform": "diff", "freq": "Q"}).iloc[1] == 1.0)

# 最新の月の前の月が欠けている時、古い変化で「大きな変化」を出さない
idx = pd.date_range("2015-01-01", "2025-09-01", freq="MS").append(pd.DatetimeIndex(["2025-11-01"]))
vals = pd.Series([2.0 + (i % 5) * 0.01 for i in range(len(idx))], index=idx)
vals.iloc[-2] = 5.0  # 2025年9月に大きな変化
ind = {"freq": "M", "direction": "up", "transform": "none", "pub_lag_days": 30}
mt = b.indicator_metrics(ind, vals, vals, {"percentile_years": 10}, pd.Timestamp("2025-12-15").date())
check("前の月が欠けた直後はz値を出さない", mt["z"] is None and mt["z_move"] is None, f"z={mt['z']}")

sys.exit(0 if ok else 1)
