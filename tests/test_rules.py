"""判定ルールのテスト（2026-09-25時点でFREDから取得した実際の値を使用）

実行: python tests/test_rules.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_data as b  # noqa: E402

cfg = b.json.loads(b.CONFIG_PATH.read_text(encoding="utf-8"))
P = {q["id"]: q["params"] for q in cfg["questions"]}


def s(pairs):
    return pd.Series([v for _, v in pairs], index=pd.to_datetime([d for d, _ in pairs]), dtype="float64")


def daily(start, end, values_by_date):
    idx = pd.bdate_range(start, end)
    ser = pd.Series(index=idx, dtype="float64")
    for d, v in values_by_date.items():
        ser.loc[pd.Timestamp(d):] = v
    return ser


REAL = {
    "gdp": s([("2026-01-01", 2.1), ("2026-04-01", 1.5)]),
    "indpro": s([("2026-07-01", 1.13), ("2026-08-01", 1.42042)]),
    "sahm": s([("2026-07-01", -0.03), ("2026-08-01", -0.07)]),
    "payems": s([("2026-06-01", 3.1), ("2026-07-01", 2.1), ("2026-08-01", 16.2)]),  # 万人
    "corepce": s([("2026-04-01", 3.32983), ("2026-05-01", 3.46371), ("2026-06-01", 3.34361), ("2026-07-01", 3.34414)]),
    "corecpi": s([("2026-05-01", 2.82294), ("2026-06-01", 2.56579), ("2026-07-01", 2.46652), ("2026-08-01", 2.44616)]),
    # 日次：6月下旬の2年債 約4.17% → 9月下旬 約4.79%、実質金利 約2.71%
    "dgs2": daily("2026-03-01", "2026-09-24", {"2026-03-01": 4.17, "2026-09-18": 4.79}),
    "realrate": daily("2026-03-01", "2026-09-24", {"2026-03-01": 2.71}),
    # 10年-3か月：2025年8月まで逆転（-0.04）、9月以降プラス
    "t10y3m": daily("2025-01-01", "2026-09-25", {"2025-01-01": -0.1, "2025-09-01": 0.05, "2026-01-01": 0.5, "2026-09-01": 0.88}),
    "permit": s([("2026-07-01", 2.35714), ("2026-08-01", 4.15739)]),
}


def run(asof):
    vw = b.View(REAL, {}, pd.Timestamp(asof), use_lag=False)
    return {q: b.EVALUATORS[q](vw, P[q]) for q in P}


res = run("2026-09-26")
expected = {"growth": "good", "jobs": "caution", "inflation": "caution", "financial": "warn", "recession": "good"}
ok = True
for q, exp in expected.items():
    got = res[q]["state"]
    mark = "OK " if got == exp else "NG "
    ok &= got == exp
    print(f"{mark}{q:10s} 期待={exp:8s} 結果={got:8s} {res[q]['reasons']}")

# 1か月前（2026-08末）は、逆イールド解消から12か月以内なので後退予兆＝警戒のはず
prev = run("2026-08-31")["recession"]
print(("OK " if prev["state"] == "warn" else "NG ") + f"recession(2026-08末) 期待=warn 結果={prev['state']} {prev['reasons']}")
ok &= prev["state"] == "warn"
sys.exit(0 if ok else 1)
