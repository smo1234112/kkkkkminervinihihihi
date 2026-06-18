# -*- coding: utf-8 -*-
"""모멘텀·테마 ETF 연도별 실제 수익률(배당 포함) + 운용보수 비교 → etf_compare.md"""
import sys, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
try:
    import yfinance as yf
except ImportError:
    sys.exit("pip install yfinance pandas")

# (티커, 설명, 연 운용보수 — 2024~25 기준 대략값, 운용사 확인 권장)
ETF = [
    ("MTUM", "iShares MSCI 美 모멘텀(대표)", "0.15%"),
    ("QMOM", "Alpha Architect 美 퀀트모멘텀(집중·공격)", "0.29%"),
    ("SPMO", "Invesco S&P500 모멘텀", "0.13%"),
    ("PDP",  "Invesco DWA 모멘텀(상대강도)", "0.62%"),
    ("FDMO", "Fidelity 모멘텀 팩터", "0.15%"),
    ("NANC", "Unusual Whales 민주당 의원 추종(펠로시)", "0.74%"),
    ("QQQ",  "Invesco 나스닥100", "0.20%"),
]
tickers = [e[0] for e in ETF]

raw = yf.download(tickers, start="2012-01-01", interval="1d", auto_adjust=True,
                  group_by="column", threads=True, progress=False)
close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
close = close.dropna(how="all")
years = sorted(set(close.index.year))

L = ["# 모멘텀·테마 ETF 비교 (실제 수익률, 배당 재투자 포함)", "",
     "- 야후 **수정종가(배당 포함)** 기준 **연도별 총수익률**. 상장연도부터 데이터(첫 해는 부분연도, * 표시).",
     "- 운용보수는 2024~25 기준 **대략값** — 정확한 현재 보수는 운용사 페이지 확인 권장.", ""]
L.append("| 연도 | " + " | ".join(tickers) + " |")
L.append("|---|" + "---|"*len(tickers))
L.append("| **연보수** | " + " | ".join(e[2] for e in ETF) + " |")
for y in years:
    row = []
    for t in tickers:
        s = close[t].dropna()
        sy = s[s.index.year == y]; sp = s[s.index.year == y-1]
        if len(sy) == 0:
            row.append("·")
        elif len(sp) == 0:
            row.append(f"{(sy.iloc[-1]/sy.iloc[0]-1)*100:+.0f}*")   # 상장 첫 해(부분)
        else:
            row.append(f"{(sy.iloc[-1]/sp.iloc[-1]-1)*100:+.0f}")
    L.append(f"| {y} | " + " | ".join(row) + " |")
cagr = []
for t in tickers:
    s = close[t].dropna()
    if len(s) < 60: cagr.append("·"); continue
    yrs = (s.index[-1]-s.index[0]).days/365.25
    cagr.append(f"{((s.iloc[-1]/s.iloc[0])**(1/yrs)-1)*100:+.0f}%")
L.append("| **상장후 연환산** | " + " | ".join(cagr) + " |")
mdd = []
for t in tickers:
    s = close[t].dropna()
    if len(s) < 60: mdd.append("·"); continue
    v = s.values; peak = np.maximum.accumulate(v); mdd.append(f"{((v-peak)/peak).min()*100:.0f}%")
L.append("| 최대낙폭 | " + " | ".join(mdd) + " |")
L += ["", "\\* = 상장 첫 해(부분연도) · 설명: " + " / ".join(f"{e[0]}={e[1]}" for e in ETF)]
open("etf_compare.md", "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
print("\n저장: etf_compare.md")
