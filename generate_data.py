# -*- coding: utf-8 -*-
"""
3전략 대시보드 데이터 생성기 (Gabriel Oh 전략실)
- 매일 1회 실행(미국장 마감 후). 결과 data.json → index.html이 표시
- 3개 전략을 각각 독립 장부로 계산:
    minervini : 8조건 + 50일 신고가 돌파 + 거래량 1.5배 / -8% or 50일선 이탈 (최대 10종목)
    stage     : 150일선(≈30주) 위·상승 + 50일 신고가 돌파 + 거래량 1.5배 / 150일선 이탈 (최대 10종목)
    umd       : 매월 12-1개월 모멘텀 상위 10종목 보유, 월말 리밸런스(모멘텀 이탈 시 교체)
- 첫 실행: 지난 10거래일 소급(breakout) / 모멘텀 즉시 1회 구성
- 이후: 마지막 처리일 이후만 시간순 전진(매도일<매수일 오류 방지)
"""
import json, os, re, sys, time, warnings
from datetime import datetime
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    sys.exit("먼저 실행:  pip install yfinance pandas lxml")

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
DATA_FILE  = os.path.join(BASE, "data.json")
NAMES_FILE = os.path.join(BASE, "names.json")

MAX_POS, STOP_PCT, PIVOT_LEN, VOL_MULT = 10, 8.0, 50, 1.5
UMD_TOP = 10         # 2026-07-13 15→10 집중: 백테스트 전 구간 CAGR·Sharpe 동시 개선(단, 종목당 10%라 개별종목 리스크는 커짐)
UMD_DROP_1M = 0.15   # 최근 1개월(21거래일) -15% 이하 급락주는 매수 후보 제외(백테스트 B -15% 리필)
UMD_MOM_CAP = 5.0    # 모멘텀 +500% 초과 과열주 제외 (INTC 사건 후 400→500 상향, 2026-07: 점진적 랠리는 품고 초극단만 배제)
BACKFILL_DAYS = 10

FALLBACK = ["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","LLY","JPM","V","UNH","XOM","MA","COST","HD","PG","NFLX","JNJ","ABBV","WMT","CRM","BAC","ORCL","MRK","KO","CVX","AMD","PEP","TMO","ADBE","LIN","WFC","CSCO","ACN","MCD","ABT","PM","IBM","GE","TXN","QCOM","INTU","DHR","AXP","CAT","VZ","AMGN","NOW","ISRG","NEE","PFE","SPGI","UBER","CMCSA","RTX","LOW","T","GS","AMAT","HON","UNP","BKNG","ELV","SYK","TJX","BLK","COP","VRTX","LRCX","MU","PANW","ANET","KLAC","ADI","SBUX","MDT","BA","PLD","GILD","REGN","MMC","ADP","DE","BX","CB","ETN","SO","MDLZ","SNPS","CDNS","AMT","ICE","LMT","SHW","DUK","CI","MO","FI","MCK","CL","WM","TT","TDG","ITW","EOG","CEG","CRWD","MAR","PLTR","SMCI","ARM","COIN","TSM","SHOP","SNOW","NET","DDOG"]

def _read_html_ua(url):
    import urllib.request
    from io import StringIO
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return pd.read_html(StringIO(r.read().decode("utf-8", "ignore")))

def get_universe():
    t = set()
    try:
        t |= set(_read_html_ua("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]["Symbol"].astype(str).str.replace(".", "-", regex=False))
    except Exception as e:
        print("[경고] S&P500 수집 실패:", e)
    # 나스닥100 구성종목 표가 별도 페이지로 분리됨(2026-07 위키 개편) → 두 URL 순차 시도
    got_ndx = False
    for url in ("https://en.wikipedia.org/wiki/Nasdaq-100", "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"):
        try:
            for tb in _read_html_ua(url):
                col = "Ticker" if "Ticker" in tb.columns else ("Symbol" if "Symbol" in tb.columns else None)
                if col and len(tb) >= 80:   # 구성종목 표(100±)만, 잡표 방지
                    t |= set(tb[col].astype(str)); got_ndx = True; break
            if got_ndx: break
        except Exception as e:
            print(f"[경고] 나스닥100({url}) 수집 실패:", e)
    if not got_ndx: print("[경고] 나스닥100 구성종목 표를 못 찾음 → S&P500만 사용")
    t = {x for x in t if isinstance(x, str) and x.isascii() and 0 < len(x) <= 6}
    if not t:
        print(f"[안내] 내장 리스트 {len(FALLBACK)}종목으로 진행"); t = set(FALLBACK)
    return sorted(t)

def precompute(df, spx_close):
    if len(df) < 300: return None
    c, h, l, v = df["Close"].values, df["High"].values, df["Low"].values, df["Volume"].values
    s = pd.Series(c)
    d = dict(c=c, h=h, l=l, v=v,
             sma50=s.rolling(50).mean().values, sma150=s.rolling(150).mean().values, sma200=s.rolling(200).mean().values,
             vol50=pd.Series(v).rolling(50).mean().values,
             hi260=pd.Series(h).rolling(260).max().values, lo260=pd.Series(l).rolling(260).min().values,
             hi50p=pd.Series(h).rolling(PIVOT_LEN).max().shift(1).values,
             spx=spx_close.reindex(df.index).ffill().values, idx=df.index)
    mom121 = np.full(len(c), np.nan)
    for i in range(252, len(c)):
        if c[i-252] > 0: mom121[i] = c[i-21]/c[i-252] - 1
    d["mom121"] = mom121
    d["pos_map"] = {dt: i for i, dt in enumerate(df.index)}
    return d

def meet8(d, i):
    c = d["c"][i]
    vals = (d["sma50"][i], d["sma150"][i], d["sma200"][i], d["sma200"][i-22], d["hi260"][i], d["lo260"][i])
    if any(np.isnan(x) for x in vals) or i < 253 or np.isnan(d["spx"][i-252]): return False
    def perf(a): return 0.4*a[i]/a[i-63] + 0.2*a[i]/a[i-126] + 0.2*a[i]/a[i-189] + 0.2*a[i]/a[i-252]
    try: rs = perf(d["c"]) / perf(d["spx"]) > 1.0
    except Exception: rs = False
    return (c>d["sma150"][i] and c>d["sma200"][i] and d["sma150"][i]>d["sma200"][i]
            and d["sma200"][i]>d["sma200"][i-22] and d["sma50"][i]>d["sma150"][i] and d["sma50"][i]>d["sma200"][i]
            and c>d["sma50"][i] and (c/d["lo260"][i]-1)*100>=30 and (1-c/d["hi260"][i])*100<=25 and rs)

def buy_signal(d, i):   # 미너비니 매수
    return (meet8(d, i) and not np.isnan(d["hi50p"][i]) and d["c"][i] > d["hi50p"][i]
            and not np.isnan(d["vol50"][i]) and d["v"][i] > VOL_MULT * d["vol50"][i])

def stage_up(d, i):     # 30주(≈150일)선 위 + 상승 = Stage 2 추세
    s = d["sma150"][i]
    return (not np.isnan(s) and not np.isnan(d["sma150"][i-22]) and d["c"][i] > s and s > d["sma150"][i-22])

def stage_entry(d, i):  # Stage 2 + 50일 신고가 돌파 + 거래량
    return (stage_up(d, i) and not np.isnan(d["hi50p"][i]) and d["c"][i] > d["hi50p"][i]
            and not np.isnan(d["vol50"][i]) and d["v"][i] > VOL_MULT * d["vol50"][i])

def _flatten(df):
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy(); df.columns = df.columns.get_level_values(0)
    return df

def download_prices(tickers, period="2y"):
    tickers = sorted(set(tickers)); frames, CHUNK = {}, 80
    for attempt in range(3):
        missing = [t for t in tickers if t not in frames]
        if not missing: break
        if attempt: print(f"[재시도 {attempt}] 미수신 {len(missing)}종목"); time.sleep(5)
        for j in range(0, len(missing), CHUNK):
            batch = missing[j:j + CHUNK]
            try:
                raw = yf.download(batch, period=period, interval="1d", group_by="ticker",
                                  auto_adjust=True, threads=True, progress=False)
            except Exception as e:
                print(f"[경고] 배치 다운로드 실패: {e}"); continue
            if raw is None or raw.empty: continue
            if len(batch) == 1:
                sub = _flatten(raw).dropna(how="all")
                if not sub.empty: frames[batch[0]] = sub
            else:
                for t in batch:
                    try:
                        sub = raw[t].dropna(how="all")
                        if not sub.empty: frames[t] = sub
                    except Exception: pass
    print(f"다운로드 완료: {len(frames)}/{len(tickers)}종목")
    return frames

# ── 전략별 처리 ──────────────────────────────────────────────
def process_breakout(st, data, all_dates, entry_fn, exit_fn, use_stop):
    if not st["processed_dates"]:
        todo = all_dates[-BACKFILL_DAYS:]
    else:
        last_done = max(st["processed_dates"])
        todo = [dt for dt in all_dates if str(dt.date()) > last_done]
    for dt in todo:
        ds = str(dt.date())
        for t in list(st["positions"]):
            d = data.get(t); i = d["pos_map"].get(dt) if d else None
            if i is None: continue
            p = st["positions"][t]; px = float(d["c"][i])
            reason = exit_fn(d, i, p)
            if reason:
                st["closed"].insert(0, dict(ticker=t, entry_date=p["date"], exit_date=ds,
                    ret_pct=round((px/p["entry"]-1)*100, 2), reason=reason))
                del st["positions"][t]
        cands = []
        for t, d in data.items():
            if t in st["positions"]: continue
            i = d["pos_map"].get(dt)
            if i and i >= 261 and entry_fn(d, i):
                cands.append((float(d["c"][i]/d["c"][i-60]), t, float(d["c"][i])))
        cands.sort(reverse=True)
        for _, t, px in cands:
            if len(st["positions"]) >= MAX_POS: break
            pos = dict(entry=round(px, 2), date=ds)
            if use_stop: pos["stop"] = round(px*(1-STOP_PCT/100), 2)
            st["positions"][t] = pos
        st["processed_dates"].append(ds)

def mn_exit(d, i, p):
    px = d["c"][i]
    if px <= p["entry"]*(1-STOP_PCT/100): return f"손절 -{STOP_PCT:.0f}%"
    if not np.isnan(d["sma50"][i]) and px < d["sma50"][i]: return "50일선 이탈"
    return None

def st_exit(d, i, p):
    if not np.isnan(d["sma150"][i]) and d["c"][i] < d["sma150"][i]: return "150일선 이탈"
    return None

def umd_ranked(data, last_dt):
    # 후보 제외 게이트: (1)모멘텀 +500% 초과 과열주(UMD_MOM_CAP) (2)최근 252일 중 하루 +50% 이상 점프(아티팩트)
    #  → 분할/스핀오프/신규상장/M&A·합병 이벤트 가격 왜곡 걸러냄 (정상주는 하루 최대 ~25%)
    ranked = []
    for t, d in data.items():
        i = d["pos_map"].get(last_dt)
        if i is None or i < 261: continue
        mv = d["mom121"][i]
        if np.isnan(mv) or not (-0.95 < mv <= UMD_MOM_CAP): continue
        c = d["c"]; spike = False
        for j in range(max(1, i-251), i+1):
            if c[j-1] > 0 and c[j]/c[j-1] - 1 > 0.50:
                spike = True; break
        if spike: continue   # 이벤트성 폭등(M&A/분할 등) 종목 제외
        if c[i-21] > 0 and c[i]/c[i-21] - 1 < -UMD_DROP_1M: continue   # 최근 1개월 급락주 제외
        ranked.append((mv, t, float(d["c"][i])))
    ranked.sort(reverse=True)
    return ranked

def process_umd(st, data, last_dt, ds, risk_on=True):
    cur_month = ds[:7]
    if st["positions"] and st.get("last_rebalance") == cur_month:
        return
    if not risk_on:
        # 절대모멘텀 OFF(시장 약세) → 전량 현금 회피 (듀얼모멘텀의 핵심)
        for t in list(st["positions"]):
            d = data.get(t); i = d["pos_map"].get(last_dt) if d else None
            px = float(d["c"][i]) if i is not None else st["positions"][t]["entry"]
            p = st["positions"][t]
            st["closed"].insert(0, dict(ticker=t, entry_date=p["date"], exit_date=ds,
                ret_pct=round((px/p["entry"]-1)*100, 2), reason="절대모멘텀 현금회피"))
            del st["positions"][t]
        st["last_rebalance"] = cur_month
        return
    ranked = umd_ranked(data, last_dt)
    target = ranked[:UMD_TOP]; target_set = {t for _, t, _ in target}
    for t in list(st["positions"]):
        if t not in target_set:
            d = data.get(t); i = d["pos_map"].get(last_dt) if d else None
            px = float(d["c"][i]) if i is not None else st["positions"][t]["entry"]
            p = st["positions"][t]
            st["closed"].insert(0, dict(ticker=t, entry_date=p["date"], exit_date=ds,
                ret_pct=round((px/p["entry"]-1)*100, 2), reason="모멘텀 이탈"))
            del st["positions"][t]
    for mv, t, px in target:
        if t not in st["positions"]:
            st["positions"][t] = dict(entry=round(px, 2), date=ds)
    st["last_rebalance"] = cur_month

# ── 출력 블록 ────────────────────────────────────────────────
def summary_of(positions_out, closed):
    rets = [p["ret_pct"] for p in positions_out]
    crets = [c["ret_pct"] for c in closed]
    return dict(open_count=len(positions_out),
                avg_ret=round(float(np.mean(rets)), 2) if rets else 0,
                closed_count=len(crets),
                win_rate=round(100*sum(1 for r in crets if r > 0)/len(crets), 1) if crets else 0,
                realized=round(float(np.sum(crets)), 1) if crets else 0)

def out_breakout(label, desc, st, data, last_dt, market_date, sub_fn, watch_fn):
    positions_out = []
    for t, p in sorted(st["positions"].items(), key=lambda x: x[1]["date"], reverse=True):
        d = data.get(t); i = d["pos_map"].get(last_dt) if d else None
        last = float(d["c"][i]) if i is not None else p["entry"]
        positions_out.append(dict(ticker=t, entry_date=p["date"], entry=p["entry"],
            last=round(last, 2), ret_pct=round((last/p["entry"]-1)*100, 2),
            days=int(np.busday_count(p["date"], market_date)), sub=sub_fn(p, d, i)))
    watch = []
    for t, d in data.items():
        i = d["pos_map"].get(last_dt)
        if i and i >= 261 and t not in st["positions"] and watch_fn(d, i):
            last_px = float(d["c"][i]); pivot = float(d["hi50p"][i]) if not np.isnan(d["hi50p"][i]) else last_px
            volr = float(d["v"][i]/d["vol50"][i]) if not np.isnan(d["vol50"][i]) and d["vol50"][i] > 0 else 0.0
            watch.append(dict(ticker=t, last=round(last_px, 2), pivot=round(pivot, 2),
                gap_pct=round((pivot/last_px-1)*100, 2), vol_ratio=round(volr, 1)))
    watch.sort(key=lambda w: abs(w["gap_pct"]))
    return dict(label=label, desc=desc, mode="breakout", summary=summary_of(positions_out, st["closed"]),
        positions=positions_out, closed=st["closed"][:100],
        today_buys=[p["ticker"] for p in positions_out if p["entry_date"] == market_date], watch=watch[:40])

def out_umd(label, desc, st, data, last_dt, market_date, risk_on=True):
    ranked = umd_ranked(data, last_dt)
    rankpos = {t: r+1 for r, (mv, t, px) in enumerate(ranked)}
    momof = {t: mv for mv, t, px in ranked}
    positions_out = []
    for t, p in sorted(st["positions"].items(), key=lambda x: rankpos.get(x[0], 9999)):  # 현재 순위순 정렬
        d = data.get(t); i = d["pos_map"].get(last_dt) if d else None
        last = float(d["c"][i]) if i is not None else p["entry"]
        mv = momof.get(t); rk = rankpos.get(t)
        sub = f"현재 {rk if rk else '-'}위 · 12개월 모멘텀 {mv*100:+.0f}%" if mv is not None else "월말 리밸런스"
        positions_out.append(dict(ticker=t, entry_date=p["date"], entry=p["entry"],
            last=round(last, 2), ret_pct=round((last/p["entry"]-1)*100, 2),
            days=int(np.busday_count(p["date"], market_date)), sub=sub,
            rank=rk, drop=(rk is None or rk > UMD_TOP)))   # 다음 리밸런스에 빠질 종목(10위 밖)
    # 관심권 = 현재 실시간 모멘텀 순위 TOP 30 (보유중 표시 포함)
    held = set(st["positions"])
    watch = []
    for r, (mv, t, px) in enumerate(ranked[:30]):
        watch.append(dict(ticker=t, last=round(px, 2), rank=r+1, mom_pct=round(mv*100, 1), held=(t in held)))
    return dict(label=label, desc=desc, mode="rank", summary=summary_of(positions_out, st["closed"]),
        positions=positions_out, closed=st["closed"][:100],
        today_buys=[p["ticker"] for p in positions_out if p["entry_date"] == market_date], watch=watch[:30],
        cash=(not risk_on),
        note=("📈 절대모멘텀 ON — 시장 상승추세라 상위 10종목 정상 보유" if risk_on
              else "📉 절대모멘텀 OFF — 시장 약세(SPY 12개월 −)라 현금 100% 회피 중"))

def load_state():
    raw = {}
    if os.path.exists(STATE_FILE):
        raw = json.load(open(STATE_FILE, encoding="utf-8"))
    if "minervini" not in raw:   # 구버전(단일 전략) → minervini로 이관
        mn = raw if ("positions" in raw or "processed_dates" in raw) else {"positions": {}, "closed": [], "processed_dates": []}
        raw = {"minervini": mn}
    raw.setdefault("minervini", {"positions": {}, "closed": [], "processed_dates": []})
    raw.setdefault("stage", {"positions": {}, "closed": [], "processed_dates": []})
    raw.setdefault("umd", {"positions": {}, "closed": [], "last_rebalance": None})
    for s in ("minervini", "stage"):
        raw[s].setdefault("positions", {}); raw[s].setdefault("closed", []); raw[s].setdefault("processed_dates", [])
    raw["umd"].setdefault("positions", {}); raw["umd"].setdefault("closed", []); raw["umd"].setdefault("last_rebalance", None)
    return raw

# ── 종목 한글명/산업 자동 해결 (yfinance, 신규 종목 자동 반영) ──────────
SECTOR_KO = {"Technology":"기술","Healthcare":"헬스케어","Financial Services":"금융","Consumer Cyclical":"경기소비재","Consumer Defensive":"필수소비재","Industrials":"산업재","Energy":"에너지","Communication Services":"커뮤니케이션","Basic Materials":"소재","Real Estate":"부동산","Utilities":"유틸리티"}
INDUSTRY_KO = {"Semiconductors":"반도체","Semiconductor Equipment & Materials":"반도체장비","Software - Infrastructure":"인프라SW","Software - Application":"응용SW","Information Technology Services":"IT서비스","Computer Hardware":"컴퓨터HW","Consumer Electronics":"전자기기","Communication Equipment":"통신장비","Electronic Components":"전자부품","Scientific & Technical Instruments":"계측장비","Solar":"태양광","Electrical Equipment & Parts":"전기장비","Aerospace & Defense":"항공우주/방산","Specialty Industrial Machinery":"산업기계","Building Products & Equipment":"건축자재","Engineering & Construction":"엔지니어링/건설","Farm & Heavy Construction Machinery":"중장비","Integrated Freight & Logistics":"물류","Airlines":"항공","Internet Content & Information":"인터넷","Internet Retail":"인터넷쇼핑","Advertising Agencies":"광고","Entertainment":"엔터","Telecom Services":"통신서비스","Electronic Gaming & Multimedia":"게임","Biotechnology":"바이오","Drug Manufacturers - General":"제약","Drug Manufacturers - Specialty & Generic":"제약","Medical Devices":"의료기기","Diagnostics & Research":"진단/연구","Health Information Services":"헬스케어IT","Medical Instruments & Supplies":"의료기기","Banks - Regional":"은행","Banks - Diversified":"은행","Capital Markets":"자본시장","Credit Services":"여신","Asset Management":"자산운용","Insurance - Diversified":"보험","Oil & Gas E&P":"석유·가스","Oil & Gas Equipment & Services":"석유장비","Oil & Gas Midstream":"에너지수송","Uranium":"우라늄","Utilities - Independent Power Producers":"발전","Utilities - Regulated Electric":"전력","Gold":"금","Copper":"구리","Steel":"철강","Other Industrial Metals & Mining":"광물","Specialty Chemicals":"특수화학","Auto Manufacturers":"자동차","Auto Parts":"자동차부품","Restaurants":"외식","Specialty Retail":"소매","Travel Services":"여행"}
_SUFFIX = re.compile(r"(,?\s+(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|Limited|plc|PLC|N\.V\.|S\.A\.|AG|SE|Holdings|Group))+$", re.I)

def _clean_name(nm):
    nm = (nm or "").strip()
    for _ in range(3):
        nm = _SUFFIX.sub("", nm).strip().rstrip(",")
    return nm

def load_names():
    if os.path.exists(NAMES_FILE):
        try: return json.load(open(NAMES_FILE, encoding="utf-8"))
        except Exception: pass
    return {}

def resolve_names(tickers, cache, limit=40):
    # 캐시에 없는 티커만 yfinance 조회(하루 최대 limit개). 실패는 건너뜀→다음날 재시도.
    fetched = 0
    for t in tickers:
        if t in cache or fetched >= limit: continue
        try:
            info = yf.Ticker(t).info
            raw = info.get("longName") or info.get("shortName") or t
            cat = INDUSTRY_KO.get(info.get("industry")) or SECTOR_KO.get(info.get("sector")) or (info.get("sector") or "-")
            cache[t] = f"{_clean_name(raw)} / {cat}"
            fetched += 1
        except Exception:
            pass
    return fetched


def main():
    state = load_state()
    uni = get_universe()
    held = set(state["minervini"]["positions"]) | set(state["stage"]["positions"]) | set(state["umd"]["positions"])
    print(f"유니버스 {len(uni)}종목 | 다운로드 중...")
    frames = download_prices(set(uni) | held | {"SPY"})
    if "SPY" not in frames:
        try:
            s = yf.download("SPY", period="2y", interval="1d", auto_adjust=True, progress=False)
            if s is not None and not s.empty: frames["SPY"] = _flatten(s)
        except Exception as e: print("[경고] SPY:", e)
    if "SPY" not in frames:
        print("[치명] SPY 실패 → 기존 data.json 유지"); return
    spx = frames["SPY"]["Close"].dropna()

    data = {}
    for t in set(uni) | held:
        try:
            sub = frames.get(t)
            if sub is None: continue
            d = precompute(sub.dropna(), spx)
            if d: data[t] = d
        except Exception: pass

    all_dates = list(spx.index); last_dt = all_dates[-1]; market_date = str(last_dt.date())

    # 절대모멘텀(듀얼모멘텀): SPY 12개월 수익이 +면 위험-ON, -면 위험-OFF(현금)
    spx_arr = spx.values
    risk_on = len(spx_arr) >= 253 and spx_arr[-1] > spx_arr[-253]
    print(f"절대모멘텀: {'ON(상승)' if risk_on else 'OFF(약세→현금)'}")

    process_breakout(state["minervini"], data, all_dates, buy_signal, mn_exit, use_stop=True)
    process_breakout(state["stage"], data, all_dates, stage_entry, st_exit, use_stop=False)
    process_umd(state["umd"], data, last_dt, market_date, risk_on)

    mn_sub = lambda p, d, i: f"손절 ${p.get('stop', round(p['entry']*(1-STOP_PCT/100),2)):.2f}"
    st_sub = lambda p, d, i: (f"150일선 ${d['sma150'][i]:.2f}" if (d is not None and i is not None and not np.isnan(d['sma150'][i])) else "150일선 이탈 시 청산")
    blocks = {
        "minervini": out_breakout("미너비니", "8조건 + 50일 신고가 돌파 + 거래량 1.5배 / -8%·50일선 이탈 청산",
                                   state["minervini"], data, last_dt, market_date, mn_sub,
                                   lambda d, i: meet8(d, i) and not buy_signal(d, i)),
        "stage": out_breakout("Stage", "150일선 위·상승(Stage 2) + 50일 신고가 돌파 / 150일선 이탈 청산",
                              state["stage"], data, last_dt, market_date, st_sub,
                              lambda d, i: stage_up(d, i) and not stage_entry(d, i)),
        "umd": out_umd("UMD 듀얼모멘텀", "12-1개월 모멘텀 상위 10종목(+500% 초과 과열주·최근 1개월 −15%↓ 급락주 제외) + 절대모멘텀(SPY 12개월 +면 보유, −면 현금) · 월말 리밸런스",
                       state["umd"], data, last_dt, market_date, risk_on),
    }
    # 종목 한글명/산업: 캐시(없으면 yfinance 자동조회) → data.json에 실어보냄(신규 종목 자동 반영)
    names_cache = load_names()
    ref = set()
    for _b in blocks.values():
        for _p in _b.get("positions", []): ref.add(_p["ticker"])
        for _w in _b.get("watch", []): ref.add(_w["ticker"])
        for _c in _b.get("closed", []): ref.add(_c["ticker"])
    _nnew = resolve_names(sorted(ref), names_cache)
    if _nnew: json.dump(names_cache, open(NAMES_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    out_names = {t: names_cache[t] for t in ref if t in names_cache}
    print(f"이름 사전: 참조 {len(ref)} · 신규 {_nnew} · 총 {len(names_cache)}")
    out = dict(updated=datetime.now().strftime("%Y-%m-%d %H:%M"), market_date=market_date,
               start_date="6월 11일", order=["minervini", "stage", "umd"], strategies=blocks, names=out_names)
    json.dump(out, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k in out["order"]:
        b = blocks[k]; print(f"[{b['label']}] 보유 {b['summary']['open_count']} 평균 {b['summary']['avg_ret']:+.1f}% | 편출 {b['summary']['closed_count']} | 관심 {len(b['watch'])}")
    print(f"기준일 {market_date} | data.json 생성 완료")

if __name__ == "__main__":
    main()
