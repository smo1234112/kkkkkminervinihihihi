# -*- coding: utf-8 -*-
"""
유명 전략 9종 + 벤치마크 비교 백테스트 (미국 S&P500+나스닥100, 최근 5년)

운영 모델 (모든 전략 공통):
  - 포지션 개수 제한 없음(10종목 cap 제거). 신호 난 종목을 '동일비중'으로 전부 보유.
    → 같은 날 신호가 N개면 각 1/N. 보유집합이 바뀔 때마다 동일비중 리밸런스.
  - 종가 신호 → 다음날 종가까지의 수익을 반영(룩어헤드 없음).
  - 수수료: 편도 0.1% (리밸런스 회전율에 비례해 차감). 회전 많은 전략이 비용 더 냄(공정).
  - 현금일 때 수익 0.

전략(모두 롱온리, 일봉 근사):
  Minervini : 8조건 + 50일 신고가 돌파 + 거래량 1.5배 / -8% or 50일선 이탈 청산
  Qullamaggie: 20일 신고가 돌파 + 50일선 위 + 10>20일선 + 거래량 1.5배 / -10% or 10일선 이탈
  Darvas    : 20일 박스(범위<15%) 상단 돌파 + 거래량 / -10% or 20일 저가 이탈
  Stage(Weinstein): 150일선(≈30주) 위·상승 + 신고가 돌파 + 거래량 / 150일선 이탈
  Turtle(20): 20일 신고가 돌파 / 10일 저가 이탈
  Donchian(55): 55일 신고가 돌파 / 20일 저가 이탈
  UMD(횡단모멘텀): 매월 12-1개월 수익 상위 10% 동일비중 보유
  ConnorsRSI2: 200일선 위 & RSI(2)<10 매수 / 종가>5일선 청산 (평균회귀)
  IBS       : IBS<0.2 매수 / IBS>0.5 청산 (단기 평균회귀)
  벤치마크   : SPY 보유, QQQ 보유

주의: 일봉 근사 백테스트. 갭/장중 체결·숏·변동성사이징은 미반영. 절대수익보다 '동일조건 상대비교'용.
"""
import sys, warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
try:
    import yfinance as yf
except ImportError:
    sys.exit("pip install yfinance pandas lxml numpy")

COMM = 0.001            # 편도 0.1%
TEST_DAYS = 1260        # 최근 5년(거래일)
MINBARS = 261           # RS/장기지표 워밍업
TZ = None

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
        print("[경고] S&P500:", e)
    try:
        for tb in _read_html_ua("https://en.wikipedia.org/wiki/Nasdaq-100"):
            col = "Ticker" if "Ticker" in tb.columns else ("Symbol" if "Symbol" in tb.columns else None)
            if col: t |= set(tb[col].astype(str)); break
    except Exception as e:
        print("[경고] 나스닥100:", e)
    t = {x for x in t if isinstance(x, str) and x.isascii() and 0 < len(x) <= 6}
    if not t: t = set(FALLBACK)
    return sorted(t)

def _flatten(df):
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy(); df.columns = df.columns.get_level_values(0)
    return df

def download_prices(tickers, start):
    tickers = sorted(set(tickers)); frames = {}; CHUNK = 80
    for attempt in range(3):
        missing = [t for t in tickers if t not in frames]
        if not missing: break
        if attempt: print(f"[재시도{attempt}] {len(missing)}종목");
        for j in range(0, len(missing), CHUNK):
            batch = missing[j:j+CHUNK]
            try:
                raw = yf.download(batch, start=start, interval="1d", group_by="ticker",
                                  auto_adjust=True, threads=True, progress=False)
            except Exception as e:
                print("[경고] batch:", e); continue
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
    print(f"수신 {len(frames)}/{len(tickers)}")
    return frames

def rsi(series, n):
    d = series.diff()
    up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / dn.ewm(alpha=1/n, adjust=False).mean()
    return (100 - 100/(1+rs)).values

def precompute(df, spx_close):
    if len(df) < 320: return None
    c=df["Close"].values; h=df["High"].values; l=df["Low"].values; o=df["Open"].values; v=df["Volume"].values
    s=pd.Series(c); hs=pd.Series(h); ls=pd.Series(l); vs=pd.Series(v)
    rng = np.where((h-l)==0, np.nan, (h-l))
    d=dict(c=c,h=h,l=l,o=o,v=v,
        sma5=s.rolling(5).mean().values, sma10=s.rolling(10).mean().values, sma20=s.rolling(20).mean().values,
        sma50=s.rolling(50).mean().values, sma150=s.rolling(150).mean().values, sma200=s.rolling(200).mean().values,
        vol50=vs.rolling(50).mean().values,
        hi20p=hs.rolling(20).max().shift(1).values, hi50p=hs.rolling(50).max().shift(1).values, hi55p=hs.rolling(55).max().shift(1).values,
        lo10p=ls.rolling(10).min().shift(1).values, lo20p=ls.rolling(20).min().shift(1).values,
        hi260=hs.rolling(260).max().values, lo260=ls.rolling(260).min().values,
        rsi2=rsi(s,2), ibs=(c-l)/rng,
        spx=spx_close.reindex(df.index).ffill().values, idx=df.index)
    mom=np.full(len(c),np.nan)
    for i in range(252,len(c)):
        if c[i-252]>0: mom[i]=c[i]/c[i-252]-1  # 12개월 (UMD는 12-1 보정 아래에서)
    mom121=np.full(len(c),np.nan)
    for i in range(252,len(c)):
        if c[i-252]>0: mom121[i]=c[i-21]/c[i-252]-1
    d["mom121"]=mom121
    d["posmap"]={dt:i for i,dt in enumerate(df.index)}
    return d

def nan(x): return x is None or (isinstance(x,float) and np.isnan(x))

def rs_ok(d,i):
    if i<253 or np.isnan(d["spx"][i-252]): return False
    def perf(a): return 0.4*a[i]/a[i-63]+0.2*a[i]/a[i-126]+0.2*a[i]/a[i-189]+0.2*a[i]/a[i-252]
    try: return perf(d["c"])/perf(d["spx"])>1.0
    except Exception: return False

def meet8(d,i):
    c=d["c"][i]
    vals=(d["sma50"][i],d["sma150"][i],d["sma200"][i],d["sma200"][i-22],d["hi260"][i],d["lo260"][i])
    if i<253 or any(np.isnan(x) for x in vals) or np.isnan(d["spx"][i-252]): return False
    return (c>d["sma150"][i] and c>d["sma200"][i] and d["sma150"][i]>d["sma200"][i]
        and d["sma200"][i]>d["sma200"][i-22] and d["sma50"][i]>d["sma150"][i] and d["sma50"][i]>d["sma200"][i]
        and c>d["sma50"][i] and (c/d["lo260"][i]-1)*100>=30 and (1-c/d["hi260"][i])*100<=25 and rs_ok(d,i))

# ── 전략 정의: entry(d,i)->bool, exit(d,i,entry_px)->bool ──
def mn_e(d,i): return meet8(d,i) and not nan(d["hi50p"][i]) and d["c"][i]>d["hi50p"][i] and not nan(d["vol50"][i]) and d["v"][i]>1.5*d["vol50"][i]
def mn_x(d,i,e): return d["c"][i]<=e*0.92 or (not nan(d["sma50"][i]) and d["c"][i]<d["sma50"][i])
def ql_e(d,i): return (not nan(d["hi20p"][i]) and d["c"][i]>d["hi20p"][i] and not nan(d["sma50"][i]) and d["c"][i]>d["sma50"][i]
                       and not nan(d["sma10"][i]) and not nan(d["sma20"][i]) and d["sma10"][i]>d["sma20"][i] and not nan(d["vol50"][i]) and d["v"][i]>1.5*d["vol50"][i])
def ql_x(d,i,e): return d["c"][i]<=e*0.90 or (not nan(d["sma10"][i]) and d["c"][i]<d["sma10"][i])
def dv_e(d,i): return (not nan(d["hi20p"][i]) and d["c"][i]>d["hi20p"][i] and not nan(d["lo20p"][i]) and d["lo20p"][i]>0
                       and (d["hi20p"][i]/d["lo20p"][i]-1)<0.15 and not nan(d["vol50"][i]) and d["v"][i]>1.2*d["vol50"][i])
def dv_x(d,i,e): return d["c"][i]<=e*0.90 or (not nan(d["lo20p"][i]) and d["c"][i]<d["lo20p"][i])
def st_e(d,i): return (not nan(d["sma150"][i]) and d["c"][i]>d["sma150"][i] and d["sma150"][i]>d["sma150"][i-22]
                       and not nan(d["hi50p"][i]) and d["c"][i]>d["hi50p"][i] and not nan(d["vol50"][i]) and d["v"][i]>1.5*d["vol50"][i])
def st_x(d,i,e): return not nan(d["sma150"][i]) and d["c"][i]<d["sma150"][i]
def tt_e(d,i): return not nan(d["hi20p"][i]) and d["c"][i]>d["hi20p"][i]
def tt_x(d,i,e): return not nan(d["lo10p"][i]) and d["c"][i]<d["lo10p"][i]
def dc_e(d,i): return not nan(d["hi55p"][i]) and d["c"][i]>d["hi55p"][i]
def dc_x(d,i,e): return not nan(d["lo20p"][i]) and d["c"][i]<d["lo20p"][i]
def rsi_e(d,i): return not nan(d["sma200"][i]) and d["c"][i]>d["sma200"][i] and not nan(d["rsi2"][i]) and d["rsi2"][i]<10
def rsi_x(d,i,e): return not nan(d["sma5"][i]) and d["c"][i]>d["sma5"][i]
def ibs_e(d,i): return not nan(d["ibs"][i]) and d["ibs"][i]<0.2
def ibs_x(d,i,e): return not nan(d["ibs"][i]) and d["ibs"][i]>0.5

SIGNAL_STRATS = [
    ("Minervini", mn_e, mn_x), ("Qullamaggie", ql_e, ql_x), ("Darvas", dv_e, dv_x),
    ("Stage(Weinstein)", st_e, st_x), ("Turtle(20일)", tt_e, tt_x), ("Donchian(55일)", dc_e, dc_x),
    ("ConnorsRSI(2)", rsi_e, rsi_x), ("IBS반전", ibs_e, ibs_x),
]

def build_sets_signal(entry, exit_, data, test_dates):
    held=set(); epx={}; sets=[]
    for dt in test_dates:
        for t in list(held):
            d=data[t]; i=d["posmap"].get(dt)
            if i is None: continue
            if exit_(d,i,epx[t]): held.discard(t); epx.pop(t,None)
        for t,d in data.items():
            if t in held: continue
            i=d["posmap"].get(dt)
            if i is None or i<MINBARS: continue
            if entry(d,i): held.add(t); epx[t]=d["c"][i]
        sets.append(frozenset(held))
    return sets

def build_sets_umd(data, test_dates):
    sets=[]; cur=frozenset(); last_month=None
    for dt in test_dates:
        m=(dt.year,dt.month)
        if m!=last_month:
            last_month=m
            cand=[]
            for t,d in data.items():
                i=d["posmap"].get(dt)
                if i is None or i<MINBARS: continue
                mv=d["mom121"][i]
                if not np.isnan(mv): cand.append((mv,t))
            cand.sort(reverse=True)
            k=max(1,int(len(cand)*0.10))
            cur=frozenset(t for _,t in cand[:k])
        sets.append(cur)
    return sets

def turnover(prev,new):
    if not prev and not new: return 0.0
    names=prev|new
    wp=1.0/len(prev) if prev else 0.0; wn=1.0/len(new) if new else 0.0
    s=0.0
    for t in names:
        a=wp if t in prev else 0.0; b=wn if t in new else 0.0
        s+=abs(b-a)
    return 0.5*s

def simulate(sets, data, test_dates):
    eq=1.0; curve=[]; rets=[]
    epx={}; trades=[]
    for k,dt in enumerate(test_dates):
        if k>0:
            prev=sets[k-1]; r=[]
            for t in prev:
                d=data[t]; i=d["posmap"].get(dt); ip=d["posmap"].get(test_dates[k-1])
                if i is None or ip is None or d["c"][ip]<=0: continue
                r.append(d["c"][i]/d["c"][ip]-1)
            g=float(np.mean(r)) if r else 0.0
            eq*=(1+g); rets.append(g)
        cost=turnover(sets[k-1] if k>0 else frozenset(), sets[k])*2*COMM
        eq*=(1-cost); curve.append(eq)
        # 트레이드 통계(진입→청산)
        prevset=sets[k-1] if k>0 else frozenset()
        for t in sets[k]-prevset:
            i=data[t]["posmap"].get(dt); epx[t]=data[t]["c"][i] if i is not None else None
        for t in prevset-sets[k]:
            i=data[t]["posmap"].get(dt)
            if t in epx and epx[t] and i is not None:
                trades.append(data[t]["c"][i]/epx[t]-1); epx.pop(t,None)
    return np.array(curve), np.array(rets), trades

def bh_curve(series, test_dates):
    px=series.reindex(test_dates).ffill().values
    return px/px[0]

def metrics(curve, rets, trades):
    n=len(curve); yrs=n/252.0
    tot=(curve[-1]/curve[0]-1)*100
    cagr=((curve[-1]/curve[0])**(1/yrs)-1)*100 if curve[0]>0 else 0
    peak=np.maximum.accumulate(curve); mdd=((curve-peak)/peak).min()*100
    if len(rets)>2 and rets.std()>0:
        sharpe=rets.mean()/rets.std()*np.sqrt(252); vol=rets.std()*np.sqrt(252)*100
    else: sharpe=0; vol=0
    expo=float(np.mean([1 if r!=0 else 0 for r in rets]))*100 if len(rets) else 0
    tr=np.array(trades) if trades else np.array([])
    nt=len(tr); win=(tr>0).mean()*100 if nt else 0
    aw=tr[tr>0].mean()*100 if (tr>0).any() else 0
    al=tr[tr<=0].mean()*100 if (tr<=0).any() else 0
    return dict(tot=tot,cagr=cagr,mdd=mdd,sharpe=sharpe,vol=vol,expo=expo,nt=nt,win=win,aw=aw,al=al)

def trades_from_sets(sets, data, test_dates):
    """집합 멤버십 변화에서 실제 매매(진입→청산)를 복원: 종목·매수일·매수가·매도일·매도가·손익%."""
    open_pos = {}; trades = []; prev = frozenset()
    for k, dt in enumerate(test_dates):
        cur = sets[k]; ds = str(dt.date())
        for t in cur - prev:
            i = data[t]["posmap"].get(dt)
            if i is not None: open_pos[t] = (ds, float(data[t]["c"][i]))
        for t in prev - cur:
            i = data[t]["posmap"].get(dt)
            if t in open_pos and i is not None:
                ed, ep = open_pos.pop(t); xp = float(data[t]["c"][i])
                trades.append(dict(ticker=t, entry_date=ed, entry=round(ep, 2), exit_date=ds,
                                   exit=round(xp, 2), ret_pct=round((xp/ep-1)*100, 2)))
        prev = cur
    for t, (ed, ep) in open_pos.items():
        i = data[t]["posmap"].get(test_dates[-1]); xp = float(data[t]["c"][i]) if i is not None else ep
        trades.append(dict(ticker=t, entry_date=ed, entry=round(ep, 2), exit_date="보유중",
                           exit=round(xp, 2), ret_pct=round((xp/ep-1)*100, 2)))
    return trades

def monthly_returns(curve, test_dates):
    last_idx = {}
    for k, dt in enumerate(test_dates): last_idx[(dt.year, dt.month)] = k
    months = sorted(last_idx.keys()); out = {}
    for j, m in enumerate(months):
        base = curve[last_idx[months[j-1]]] if j > 0 else 1.0
        out[m] = (curve[last_idx[m]]/base - 1)*100
    return out, months

def monthly_md(name, curve, test_dates):
    mr, months = monthly_returns(curve, test_dates)
    years = sorted({y for y, _ in months})
    L = [f"**{name}**", "", "| 연도 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 연간 |", "|---|" + "---|"*13]
    for y in years:
        cells = []; yf = 1.0
        for mo in range(1, 13):
            if (y, mo) in mr:
                v = mr[(y, mo)]; cells.append(f"{v:+.1f}"); yf *= (1 + v/100)
            else:
                cells.append("·")
        L.append(f"| {y} | " + " | ".join(cells) + f" | **{(yf-1)*100:+.1f}** |")
    return "\n".join(L)

def trades_md(name, trades):
    closed = [t for t in trades if t["exit_date"] != "보유중"]
    n = len(closed); wins = sum(1 for t in closed if t["ret_pct"] > 0)
    wr = wins/n*100 if n else 0
    srt = sorted(closed, key=lambda t: -t["ret_pct"])
    sample = (srt[:6] + srt[-6:]) if len(srt) > 12 else srt
    L = [f"**{name}** — 총 {len(trades)}건 (청산 {n} · 승률 {wr:.0f}%) · 큰 수익/손실 일부",
         "", "| 종목 | 매수일 | 매수가 | 매도일 | 매도가 | 손익% |", "|---|---|---|---|---|---|"]
    for t in sample:
        L.append(f"| {t['ticker']} | {t['entry_date']} | ${t['entry']} | {t['exit_date']} | ${t['exit']} | {t['ret_pct']:+.1f}% |")
    return "\n".join(L)

def main():
    print("=== 유명 전략 비교 백테스트 (최근 5년) ===")
    uni=get_universe()
    start=(datetime.now()-timedelta(days=2400)).strftime("%Y-%m-%d")
    print(f"유니버스 {len(uni)} | {start}~ 다운로드...")
    frames=download_prices(set(uni)|{"SPY","QQQ"}, start)
    if "SPY" not in frames: sys.exit("SPY 실패")
    spx=frames["SPY"]["Close"].dropna()
    data={}
    for t in uni:
        try:
            d=precompute(frames[t].dropna(), spx)
            if d: data[t]=d
        except Exception: pass
    all_dates=list(spx.index)
    test_dates=all_dates[-TEST_DAYS:]
    print(f"기간 {test_dates[0].date()}~{test_dates[-1].date()} | 유효 {len(data)}종목\n")

    rows=[]; curves={}; keep={}
    for name,e,x in SIGNAL_STRATS:
        sets=build_sets_signal(e,x,data,test_dates)
        if name in ("Minervini","Stage(Weinstein)"): keep[name]=sets
        curve,rets,trades=simulate(sets,data,test_dates)
        m=metrics(curve,rets,trades); m["name"]=name; rows.append(m); curves[name]=curve
        print(f"[{name}] CAGR {m['cagr']:+.1f}% MDD {m['mdd']:.1f}% Sharpe {m['sharpe']:.2f} 거래 {m['nt']} 승률 {m['win']:.0f}%")
    # UMD
    sets=build_sets_umd(data,test_dates); keep["UMD(횡단모멘텀)"]=sets; curve,rets,trades=simulate(sets,data,test_dates)
    m=metrics(curve,rets,trades); m["name"]="UMD(횡단모멘텀)"; rows.append(m); curves[m["name"]]=curve
    print(f"[UMD] CAGR {m['cagr']:+.1f}% MDD {m['mdd']:.1f}% Sharpe {m['sharpe']:.2f} 거래 {m['nt']} 승률 {m['win']:.0f}%")
    # 벤치마크
    for bn in ["SPY","QQQ"]:
        if bn in frames:
            cur=bh_curve(frames[bn]["Close"].dropna(), test_dates)
            rr=np.diff(cur)/cur[:-1]
            m=metrics(cur,rr,[]); m["name"]=f"{bn} 보유"; rows.append(m); curves[m["name"]]=cur
            print(f"[{bn}보유] CAGR {m['cagr']:+.1f}% MDD {m['mdd']:.1f}% Sharpe {m['sharpe']:.2f}")

    # ── 블렌드 포트폴리오 (월말 리밸런스) ──
    def _blend(W):
        dr = {k: np.concatenate([[0.0], np.diff(curves[k])/curves[k][:-1]]) for k in W}
        out = []; eq = 1.0; sl = {k: W[k] for k in W}; lastm = None
        for idx, dt in enumerate(test_dates):
            m = (dt.year, dt.month)
            if m != lastm: sl = {k: eq*W[k] for k in W}; lastm = m   # 월말 리밸런스
            if idx > 0:
                for k in W: sl[k] *= (1 + dr[k][idx])
                eq = sum(sl.values())
            out.append(eq)
        return np.array(out)
    BLENDS = [("블렌드 40/35/25", {"Minervini": 0.35, "Stage(Weinstein)": 0.25, "UMD(횡단모멘텀)": 0.40}),
              ("Stage+UMD 50/50", {"Stage(Weinstein)": 0.50, "UMD(횡단모멘텀)": 0.50})]
    for bname, W in BLENDS:
        if all(k in curves for k in W):
            bc = _blend(W); br = np.diff(bc)/bc[:-1]
            mb = metrics(bc, br, []); mb["name"] = bname; rows.append(mb); curves[bname] = bc
            print(f"[{bname}] CAGR {mb['cagr']:+.1f}% MDD {mb['mdd']:.1f}% Sharpe {mb['sharpe']:.2f}")

    rows.sort(key=lambda r:-r["cagr"])

    # ── 연도별(국면별) 수익률 ──
    years=sorted({dt.year for dt in test_dates})
    pos_last={}
    for k,dt in enumerate(test_dates): pos_last[dt.year]=k
    def yret(curve,idx):
        base = curve[pos_last[years[idx-1]]] if idx>0 else 1.0
        return (curve[pos_last[years[idx]]]/base-1)*100
    ylabels=[]
    for idx,Y in enumerate(years):
        suf = "(6월~)" if idx==0 else ("(~6월)" if idx==len(years)-1 else "")
        ylabels.append(f"{Y}{suf}")
    md=["# 전략 비교 백테스트 결과 (최근 5년, S&P500+나스닥100)","",
        f"- 기간: {test_dates[0].date()} ~ {test_dates[-1].date()} | 유효 {len(data)}종목",
        "- 포지션 cap 없음(신호 전부 동일비중) · 수수료 편도 0.1% · 종가신호→익일반영(룩어헤드 없음)",
        "- 일봉 근사. 절대수익보다 동일조건 상대비교용. 과거성과가 미래를 보장하지 않음.","",
        "| 순위 | 전략 | CAGR | 총수익 | 최대낙폭(MDD) | Sharpe | 변동성 | 노출 | 거래수 | 승률 | 평균수익 | 평균손실 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n,r in enumerate(rows,1):
        md.append(f"| {n} | {r['name']} | {r['cagr']:+.1f}% | {r['tot']:+.0f}% | {r['mdd']:.1f}% | {r['sharpe']:.2f} | {r['vol']:.0f}% | {r['expo']:.0f}% | {r['nt']} | {r['win']:.0f}% | {r['aw']:+.1f}% | {r['al']:+.1f}% |")

    # 연도별(국면별) 표
    md += ["","## 국면별(연도별) 수익률","",
           "- 각 연도별 수익률. **2021은 6월부터, 2026은 6월까지** 부분연도.",
           "- 맨 윗줄 SPY/QQQ로 그 해 장세를 읽으세요(▲상승 / ▼하락).",""]
    spy_y=[yret(curves.get("SPY 보유",[1,1]),idx) for idx in range(len(years))]
    regime=[("▲상승" if y>=0 else "▼하락") for y in spy_y]
    md.append("| 전략 | " + " | ".join(ylabels) + " |")
    md.append("|---|" + "---|"*len(years))
    md.append("| **장세(SPY)** | " + " | ".join(f"{regime[i]} {spy_y[i]:+.0f}%" for i in range(len(years))) + " |")
    order=["블렌드 40/35/25","Stage+UMD 50/50","UMD(횡단모멘텀)","Minervini","Stage(Weinstein)","QQQ 보유","SPY 보유","Donchian(55일)","Turtle(20일)","Darvas","Qullamaggie","ConnorsRSI(2)","IBS반전"]
    seen=set()
    for nm in order + [r["name"] for r in rows]:
        if nm in seen or nm not in curves: continue
        seen.add(nm)
        cells=" | ".join(f"{yret(curves[nm],idx):+.0f}%" for idx in range(len(years)))
        md.append(f"| {nm} | {cells} |")

    # ── 방법론 ──
    md += ["","## 백테스트 방법 (어떻게 돌렸나)","",
        "1. **유니버스**: S&P500+나스닥100(위키 자동수집), 야후 일봉(분할·배당 조정 auto_adjust).",
        "2. **신호 평가**: 각 전략 규칙을 매 거래일 종가에 계산. 종가 신호 → **다음날 종가까지 수익 반영**(룩어헤드 없음).",
        "3. **포트폴리오**: 신호 종목을 **동일비중**으로 전부 보유(개수 제한 없음). 집합 변동 시 동일비중 리밸런스, 회전율×수수료(편도 0.1%) 차감.",
        "4. **블렌드**: 단일전략 수익곡선을 UMD 40 / 미너비니 35 / Stage 25로 **월말 리밸런스** 합성.",
        "5. **한계**: 일봉 종가 근사 — 갭/장중 체결·숏·세금 미반영. 절대수익보다 동일조건 상대비교용.",""]

    # ── 월별 수익률 ──
    md += ["## 월별 수익률 (%)",""]
    for nm in ["블렌드 40/35/25","Stage+UMD 50/50","UMD(횡단모멘텀)","Minervini","Stage(Weinstein)"]:
        if nm in curves: md += [monthly_md(nm, curves[nm], test_dates), ""]

    # ── 매매내역 + CSV ──
    import csv as _csv
    md += ["## 매매내역 (실제 진입→청산)","",
        "- 전체 매매는 `trades_minervini.csv` / `trades_stage.csv` / `trades_umd.csv` 에 저장(저장소).",""]
    for key, fn, kor in [("Minervini","trades_minervini.csv","미너비니"),
                          ("Stage(Weinstein)","trades_stage.csv","Stage"),
                          ("UMD(횡단모멘텀)","trades_umd.csv","UMD")]:
        if key in keep:
            trs = trades_from_sets(keep[key], data, test_dates)
            with open(fn, "w", newline="", encoding="utf-8-sig") as f:
                w = _csv.DictWriter(f, fieldnames=["ticker","entry_date","entry","exit_date","exit","ret_pct"])
                w.writeheader(); w.writerows(trs)
            md += [trades_md(kor, trs), ""]

    open("backtest_results.md","w",encoding="utf-8").write("\n".join(md))
    print("저장: backtest_results.md + trades_*.csv")
    print("\nbacktest_results.md 저장 완료")

if __name__=="__main__":
    main()
