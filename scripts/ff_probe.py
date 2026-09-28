#!/usr/bin/env python3
"""
【実験】Forex Factoryの週間データをGitHubのサーバーから取得できるかを確かめる。
予想値そのものは規約上公開できないため、出力は「取得の成否・件数・対応づけできた指標ID」だけにする。
結果は GitHub の注釈（::notice）として残す（数値の予想は一切出力しない）。
"""
import json
import sys
import time

import requests

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# Forex Factoryの米国指標名 → このツールの指標ID（予想と結果を並べられるもの）
TITLE_MAP = {
    "Non-Farm Employment Change": "payems", "Unemployment Rate": "unrate",
    "Average Hourly Earnings m/m": "ahe", "CPI m/m": "cpi", "CPI y/y": "cpi", "Core CPI m/m": "corecpi",
    "Core PCE Price Index m/m": "corepce", "PPI m/m": "ppi", "Core PPI m/m": "ppi",
    "Retail Sales m/m": "retail", "Core Retail Sales m/m": "retail",
    "Advance GDP q/q": "gdp", "Prelim GDP q/q": "gdp", "Final GDP q/q": "gdp",
    "Unemployment Claims": "icsa", "JOLTS Job Openings": "jolts",
    "Durable Goods Orders m/m": "durable", "Core Durable Goods Orders m/m": "durable",
    "Building Permits": "permit", "Housing Starts": "houst",
    "Prelim UoM Consumer Sentiment": "umich", "Revised UoM Consumer Sentiment": "umich",
    "Philly Fed Manufacturing Index": "philly", "Industrial Production m/m": "indpro",
    "Trade Balance": "tradebal", "Federal Funds Rate": "dff",
}


def notice(title, msg):
    print(f"::notice title={title}::{msg}")


def main():
    status, events, err = None, [], ""
    for attempt in range(3):
        try:
            r = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0 (macro-dashboard probe)"})
            status = r.status_code
            if r.ok:
                events = r.json()
                break
            err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            err = type(e).__name__
        time.sleep(10)
    if not events:
        notice("FF取得", f"失敗 status={status} err={err}")
        return 0
    usd = [e for e in events if e.get("country") == "USD"]
    hi = [e for e in usd if e.get("impact") in ("High", "Medium")]
    with_fc = [e for e in hi if (e.get("forecast") or "").strip()]
    matched = sorted({TITLE_MAP[e["title"]] for e in hi if e.get("title") in TITLE_MAP})
    unmatched = sorted({e["title"] for e in hi if e.get("title") not in TITLE_MAP})
    keys = sorted({k for e in events for k in e.keys()})
    notice("FF取得", f"成功 status={status} 全{len(events)}件 USD{len(usd)}件 重要(中以上){len(hi)}件 うち予想あり{len(with_fc)}件 項目={keys}")
    notice("FF対応づけ", f"対応できた指標={matched}")
    notice("FF未対応", f"対応表にない重要指標名={unmatched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
