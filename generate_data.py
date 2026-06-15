# -*- coding: utf-8 -*-
"""
미너비니 A전략 대시보드 데이터 생성기
- 매일 1회 실행 (미국장 마감 후, 한국시간 아침 권장)
- 첫 실행: 지난 10거래일을 소급(백필)해서 매매내역을 만들어 줌
- 이후 실행: 오늘 신호 처리(신규 매수 기록 / 보유종목 손절·청산 판정)
- 결과: 같은 폴더의 data.json → index.html이 읽어서 표시

전략 (백테스트로 검증된 A):
  매수 = 미너비니 8조건 + 직전 50일 고점 돌파 종가 + 거래량 50일평균 1.5배 (최대 10종목, 모멘텀 상위 우선)
  매도 = 종가 기준 매수가 -8% 손절  또는  50일선 종가 이탈

사용법:  pip install yfinance pandas lxml   →   python generate_data.py
"""
import json, os, sys, time, warnings
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

MAX_POS, STOP_PCT, PIVOT_LEN, VOL_MULT = 10, 8.0, 50, 1.5
BACKFILL_DAYS = 10   # 첫 실행 시 소급할 거래일 수 (약 2주)

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
    try:
        for tb in _read_html_ua("https://en.wikipedia.org/wiki/Nasdaq-100"):
            col = "Ticker" if "Ticker" in tb.columns else ("Symbol" if "Symbol" in tb.columns else None)
            if col: t |= set(tb[col].astype(str)); break
    except Exception as e:
        print("[경고] 나스닥100 수집 실패:", e)
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

def buy_signal(d, i):
    return (meet8(d, i) and not np.isnan(d["hi50p"][i]) and d["c"][i] > d["hi50p"][i]
            and not np.isnan(d["vol50"][i]) and d["v"][i] > VOL_MULT * d["vol50"][i])

def _flatten(df):
    """단일종목 다운로드가 MultiIndex 컬럼(('Close','SPY')…)으로 와도 OHLCV 단일 레벨로 정규화."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df

def download_prices(tickers, period="2y"):
    """야후 대량 다운로드 안정화: 80종목씩 배치 + 최대 3회 재시도.
    한 방에 550종목을 받으면 rate-limit으로 자주 빈 데이터/크래시가 나므로 분할한다.
    반환: {티커: 일봉DataFrame(OHLCV)} — 받은 것만."""
    tickers = sorted(set(tickers))
    frames, CHUNK = {}, 80
    for attempt in range(3):
        missing = [t for t in tickers if t not in frames]
        if not missing:
            break
        if attempt:
            print(f"[재시도 {attempt}] 미수신 {len(missing)}종목"); time.sleep(5)
        for j in range(0, len(missing), CHUNK):
            batch = missing[j:j + CHUNK]
            try:
                raw = yf.download(batch, period=period, interval="1d", group_by="ticker",
                                  auto_adjust=True, threads=True, progress=False)
            except Exception as e:
                print(f"[경고] 배치 다운로드 실패: {e}"); continue
            if raw is None or raw.empty:
                continue
            if len(batch) == 1:
                sub = _flatten(raw).dropna(how="all")
                if not sub.empty:
                    frames[batch[0]] = sub
            else:
                for t in batch:
                    try:
                        sub = raw[t].dropna(how="all")
                        if not sub.empty:
                            frames[t] = sub
                    except Exception:
                        pass
    print(f"다운로드 완료: {len(frames)}/{len(tickers)}종목 수신")
    return frames

def main():
    state = {"positions": {}, "closed": [], "processed_dates": []}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE, encoding="utf-8"))

    uni = get_universe()
    print(f"유니버스 {len(uni)}종목 | 데이터 다운로드 중...")
    frames = download_prices(set(uni) | set(state["positions"]) | {"SPY"})

    # SPY는 상대강도(RS) 계산의 기준이라 필수. 누락 시 단독 재시도.
    if "SPY" not in frames:
        try:
            s = yf.download("SPY", period="2y", interval="1d", auto_adjust=True, progress=False)
            if s is not None and not s.empty:
                frames["SPY"] = _flatten(s)
        except Exception as e:
            print("[경고] SPY 단독 다운로드 실패:", e)
    if "SPY" not in frames:
        # 기준지수를 못 받으면 계산 불가 → 크래시 대신 기존 data.json 유지하고 정상 종료
        # (워크플로를 빨간 실패로 만들지 않아, 다음 거래일에 자동 복구됨)
        print("[치명] SPY 데이터 수신 실패 → 기존 data.json 유지, 종료")
        return
    spx = frames["SPY"]["Close"].dropna()

    data = {}
    for t in set(uni) | set(state["positions"]):
        try:
            sub = frames.get(t)
            if sub is None:
                continue
            d = precompute(sub.dropna(), spx)
            if d: data[t] = d
        except Exception: pass

    all_dates = list(spx.index)
    if not state["processed_dates"]:
        # 첫 실행: 지난 BACKFILL_DAYS 거래일을 시간순(과거→현재)으로 소급
        todo = all_dates[-BACKFILL_DAYS:]
        print(f"첫 실행 → 지난 {len(todo)}거래일 소급 처리")
    else:
        # 이후 실행: '마지막으로 처리한 날짜' 이후의 날짜만, 항상 시간순으로 전진.
        # (과거 미처리 날짜로 거꾸로 돌아가지 않음 → 매도일<매수일 오류 방지)
        last_done = max(state["processed_dates"])   # ISO 날짜라 문자열 비교 = 시간순 비교
        todo = [dt for dt in all_dates if str(dt.date()) > last_done]

    for dt in todo:
        ds = str(dt.date())
        # 1) 보유종목 청산 판정 (종가 기준)
        for t in list(state["positions"]):
            d = data.get(t); i = d["pos_map"].get(dt) if d else None
            if i is None: continue
            p = state["positions"][t]; px = float(d["c"][i]); reason = None
            if px <= p["entry"] * (1 - STOP_PCT/100): reason = f"손절 -{STOP_PCT:.0f}%"
            elif not np.isnan(d["sma50"][i]) and px < d["sma50"][i]: reason = "50일선 이탈"
            if reason:
                state["closed"].insert(0, dict(ticker=t, entry_date=p["date"], exit_date=ds,
                    entry=p["entry"], exit=round(px,2), ret_pct=round((px/p["entry"]-1)*100,2), reason=reason))
                del state["positions"][t]
        # 2) 신규 매수 신호 (슬롯 남는 만큼, 60일 모멘텀 상위 우선)
        cands = []
        for t, d in data.items():
            if t in state["positions"]: continue
            i = d["pos_map"].get(dt)
            if i and i >= 261 and buy_signal(d, i):
                cands.append((float(d["c"][i]/d["c"][i-60]), t, float(d["c"][i])))
        cands.sort(reverse=True)
        for _, t, px in cands:
            if len(state["positions"]) >= MAX_POS: break
            state["positions"][t] = dict(entry=round(px,2), date=ds, time="16:00 ET(종가)",
                                         stop=round(px*(1-STOP_PCT/100), 2))
        state["processed_dates"].append(ds)

    # 3) 현재가/수익률 계산 + 오늘 신호 + 관심권
    last_dt = all_dates[-1]
    positions_out = []
    for t, p in sorted(state["positions"].items(), key=lambda x: x[1]["date"], reverse=True):
        d = data.get(t); i = d["pos_map"].get(last_dt) if d else None
        last = float(d["c"][i]) if i is not None else p["entry"]
        positions_out.append(dict(ticker=t, entry_date=p["date"], entry_time=p["time"], entry=p["entry"],
            stop=p["stop"], last=round(last,2), ret_pct=round((last/p["entry"]-1)*100,2),
            days=int(np.busday_count(p["date"], str(last_dt.date())))))
    watch = []
    for t, d in data.items():
        i = d["pos_map"].get(last_dt)
        if i and i >= 261 and t not in state["positions"] and meet8(d, i) and not buy_signal(d, i):
            watch.append(t)
    today_buys = [p for p in positions_out if p["entry_date"] == str(last_dt.date())]
    rets = [p["ret_pct"] for p in positions_out]
    closed_rets = [c["ret_pct"] for c in state["closed"]]
    out = dict(
        updated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        market_date=str(last_dt.date()),
        summary=dict(open_count=len(positions_out),
                     avg_ret=round(float(np.mean(rets)), 2) if rets else 0,
                     closed_count=len(closed_rets),
                     win_rate=round(100*sum(1 for r in closed_rets if r > 0)/len(closed_rets), 1) if closed_rets else 0,
                     realized=round(float(np.sum(closed_rets)), 1) if closed_rets else 0),
        positions=positions_out, closed=state["closed"][:100],
        today_buys=[p["ticker"] for p in today_buys], watch=sorted(watch)[:40])
    json.dump(out, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n기준일 {out['market_date']} | 보유 {len(positions_out)} (평균 {out['summary']['avg_ret']:+.1f}%) | "
          f"오늘 매수 {len(today_buys)} | 편출 누적 {len(closed_rets)}")
    print("data.json 생성 완료 → index.html을 열거나 폴더를 Vercel에 배포하세요.")

if __name__ == "__main__":
    main()
