"""
信號跟單回測
模擬 2015-2025 年嚴格按 BUY / STRONG BUY / SELL 信號操作，
逐筆記錄每筆交易的進出場條件、損益，比較四種策略與 B&H 基準。
"""

import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date as _date
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR  = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ        = ZoneInfo("Asia/Taipei")

START_DATE    = "2015-01-01"
END_DATE      = "2025-12-31"
ANNUAL_BUDGET = 100_000   # NT$ 每年注資金額（可由呼叫方覆寫）
MAX_INJECT_YEARS = 10     # 最多注資年數（避免跨 11 個日曆年超過預期）
SBUY_MULT     = 1.5       # STRONG BUY 單筆投入倍率（相對 ANNUAL_BUDGET）
TRIM_PROFIT   = 15.0      # % 單筆獲利達此門檻 → TRIM 出場

# (mode_id, label, buy_en, sbuy_en, trim_en)
MODES = [
    ("BUY",   "BUY 策略",         True,  False, False),
    ("SBUY",  "STRONG BUY 策略",  False, True,  False),
    ("ALL",   "混合策略",          True,  True,  False),
    ("TRIM",  "混合+TRIM 策略",    True,  True,  True ),
]


# ── 資料拉取 ──────────────────────────────────────────────────────────────

def _fetch(symbol: str) -> pd.DataFrame:
    df = yf.Ticker(symbol).history(
        start=START_DATE, end=END_DATE, auto_adjust=True)
    if df.empty:
        return df
    df.index = (df.index.tz_localize(TZ)
                if df.index.tzinfo is None
                else df.index.tz_convert(TZ))
    return df


# ── 滾動 AVWAP（與 tw_screener.calc_avwap 邏輯一致）──────────────────────

def _rolling_avwap(close: pd.Series, volume: pd.Series,
                   high: pd.Series, low: pd.Series,
                   lookback: int = 60) -> pd.Series:
    """
    複製 calc_avwap 的錨點邏輯：
    - 若當前價已從 60 日低點反彈 ≥5%（confirmed）→ 錨點 = 60 日最低收盤
    - 否則（仍在下跌）→ 錨點 = 60 日窗口起點，使 AVWAP 明顯高於現價
    使用 typical price (H+L+C)/3 計算 VWAP，與即時掃描器一致。
    """
    c  = close.values.astype(float)
    v  = volume.values.astype(float)
    tp = ((high.values + low.values + c) / 3).astype(float)
    n  = len(c)
    out = np.full(n, np.nan)
    for i in range(lookback - 1, n):
        s        = max(0, i - lookback + 1)
        win_c    = c[s: i + 1]
        min_rel  = int(np.argmin(win_c))
        min_px   = win_c[min_rel]
        confirmed = c[i] >= min_px * 1.05   # 反彈 5% 才以低點為錨
        anchor   = s + (min_rel if confirmed else 0)
        sl = tp[anchor: i + 1]
        sv = v[anchor: i + 1]
        tv = sv.sum()
        out[i] = (sl * sv).sum() / tv if tv > 0 else sl.mean()
    return pd.Series(out, index=close.index)


# ── 逐日信號計算 ─────────────────────────────────────────────────────────

def _daily_signals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    from tw_screener import SIGNAL_CONFIG, _DEFAULT_CFG, calc_rsi
    scfg = SIGNAL_CONFIG.get(symbol, _DEFAULT_CFG)

    close  = df["Close"].dropna()
    volume = df["Volume"].reindex(close.index).fillna(0).clip(lower=1)
    high   = df["High"].reindex(close.index).fillna(close)
    low    = df["Low"].reindex(close.index).fillna(close)

    rsi     = calc_rsi(close)
    avwap   = _rolling_avwap(close, volume, high, low, lookback=60)
    ma20    = close.rolling(20).mean()
    roll_hi = close.rolling(60).max()
    dd_pct  = (close / roll_hi - 1) * 100   # 恆 ≤ 0

    b1, b2   = scfg["b1"], scfg["b2"]
    s_mul    = scfg["s"]
    rsi_buy  = scfg["rsi_buy"]
    rsi_sbuy = scfg["rsi_sbuy"]
    rsi_sell = scfg["rsi_sell"]

    sig = pd.DataFrame(
        {"close": close, "rsi": rsi, "avwap": avwap,
         "dd_pct": dd_pct, "ma20": ma20}
    ).dropna()

    av = sig["avwap"]
    is_sbuy = (sig["dd_pct"] <= -20) & (sig["close"] < av * b2) & (sig["rsi"] <= rsi_sbuy)
    is_buy  = (~is_sbuy) & (sig["dd_pct"] <= -10) & (sig["close"] < av * b1) & (sig["rsi"] <= rsi_buy)
    is_sell = (sig["rsi"] >= rsi_sell) & (sig["close"] >= av * s_mul) & (sig["close"] > sig["ma20"] * 1.15)

    sig["signal"] = "HOLD"
    sig.loc[is_sbuy, "signal"] = "STRONG BUY"
    sig.loc[is_buy,  "signal"] = "BUY"
    sig.loc[is_sell, "signal"] = "SELL"
    return sig


# ── 模擬單一策略（年度注資模型）────────────────────────────────────────────

def _simulate(sig: pd.DataFrame,
              buy_en: bool, sbuy_en: bool, trim_en: bool,
              annual_budget: float = ANNUAL_BUDGET) -> tuple[list[dict], float]:
    """
    每年年初注入 annual_budget，信號觸發時動用現金買入（最多 annual_budget/次）。
    SELL/TRIM 出場後現金回收，可再次投入。
    回傳 (trades, total_injected)。
    """
    LOT_BUY  = annual_budget
    LOT_SBUY = annual_budget * SBUY_MULT

    cash:           float       = 0.0
    total_injected: float       = 0.0
    inject_count:   int         = 0
    open_lots:      list[dict]  = []
    trades:         list[dict]  = []
    prev_sig        = "HOLD"
    current_year    = None

    for dt, row in sig.iterrows():
        yr = dt.year
        if yr != current_year:
            current_year = yr
            if inject_count < MAX_INJECT_YEARS:
                cash           += annual_budget
                total_injected += annual_budget
                inject_count   += 1

        price    = float(row["close"])
        rsi      = float(row["rsi"])
        avwap    = float(row["avwap"])
        dd       = float(row["dd_pct"])
        curr_sig = str(row["signal"])
        date_str = dt.date().isoformat()
        vs_avwap = round((price / avwap - 1) * 100, 1) if avwap > 0 else None

        # 1. TRIM：每日檢查各持倉，獲利達門檻即出清並回收現金
        if trim_en and open_lots:
            keep = []
            for lot in open_lots:
                profit = (price - lot["entry_price"]) / lot["entry_price"] * 100
                if profit >= TRIM_PROFIT:
                    proceeds = lot["shares"] * price
                    pnl      = proceeds - lot["cost"]
                    hd       = (dt.date() - _date.fromisoformat(lot["entry_date"])).days
                    trades.append({**lot,
                                   "exit_date": date_str, "exit_price": round(price, 2),
                                   "proceeds": round(proceeds, 0), "pnl": round(pnl, 0),
                                   "pnl_pct": round(pnl / lot["cost"] * 100, 2),
                                   "hold_days": hd, "exit_signal": "TRIM",
                                   "exit_cond": {"RSI": round(rsi, 1), "vs_AVWAP%": vs_avwap}})
                    cash += proceeds
                else:
                    keep.append(lot)
            open_lots = keep

        # 2. SELL：關閉所有持倉，現金回收
        if curr_sig == "SELL" and open_lots:
            ec = {"RSI": round(rsi, 1), "vs_AVWAP%": vs_avwap}
            for lot in open_lots:
                proceeds = lot["shares"] * price
                pnl      = proceeds - lot["cost"]
                hd       = (dt.date() - _date.fromisoformat(lot["entry_date"])).days
                trades.append({**lot,
                               "exit_date": date_str, "exit_price": round(price, 2),
                               "proceeds": round(proceeds, 0), "pnl": round(pnl, 0),
                               "pnl_pct": round(pnl / lot["cost"] * 100, 2),
                               "hold_days": hd, "exit_signal": "SELL",
                               "exit_cond": ec})
                cash += proceeds
            open_lots = []

        # 3. BUY 進場（edge-triggered）
        elif curr_sig == "BUY" and buy_en and prev_sig != "BUY" and cash >= LOT_BUY * 0.3:
            spend  = min(cash, LOT_BUY)
            shares = int(spend // price)
            if shares > 0:
                cost  = round(shares * price, 0)
                cash -= cost
                open_lots.append({
                    "entry_date": date_str, "entry_price": round(price, 2),
                    "shares": shares, "cost": cost, "entry_signal": "BUY",
                    "entry_cond": {"DD%": round(dd, 1), "RSI": round(rsi, 1),
                                   "vs_AVWAP%": vs_avwap},
                })

        # 4. STRONG BUY 進場（edge-triggered）
        elif curr_sig == "STRONG BUY" and sbuy_en and prev_sig != "STRONG BUY" and cash >= LOT_SBUY * 0.3:
            spend  = min(cash, LOT_SBUY)
            shares = int(spend // price)
            if shares > 0:
                cost  = round(shares * price, 0)
                cash -= cost
                open_lots.append({
                    "entry_date": date_str, "entry_price": round(price, 2),
                    "shares": shares, "cost": cost, "entry_signal": "STRONG BUY",
                    "entry_cond": {"DD%": round(dd, 1), "RSI": round(rsi, 1),
                                   "vs_AVWAP%": vs_avwap},
                })

        prev_sig = curr_sig

    # 5. 期末：以最後收盤價關閉剩餘持倉
    if open_lots:
        last = sig.iloc[-1]
        lp   = float(last["close"])
        ld   = sig.index[-1].date().isoformat()
        la   = float(last["avwap"])
        ec   = {"RSI": round(float(last["rsi"]), 1),
                "vs_AVWAP%": round((lp / la - 1) * 100, 1) if la > 0 else None}
        for lot in open_lots:
            proceeds = lot["shares"] * lp
            pnl      = proceeds - lot["cost"]
            hd       = (sig.index[-1].date() - _date.fromisoformat(lot["entry_date"])).days
            trades.append({**lot,
                           "exit_date": ld, "exit_price": round(lp, 2),
                           "proceeds": round(proceeds, 0), "pnl": round(pnl, 0),
                           "pnl_pct": round(pnl / lot["cost"] * 100, 2),
                           "hold_days": hd, "exit_signal": "PERIOD_END",
                           "exit_cond": ec})
    return trades, total_injected


# ── 統計摘要 ──────────────────────────────────────────────────────────────

def _stats(trades: list[dict], bnh_ret: float = 0.0,
           total_injected: float = 0.0, years: float = 10.0) -> dict:
    """
    total_injected: 全期累計注資金額（return_pct 基準）
    years:          回測年數（用於 CAGR 計算）
    """
    if not trades:
        return {"n_trades": 0, "n_wins": 0, "win_rate": 0.0,
                "total_invested": 0, "total_injected": round(total_injected, 0),
                "total_pnl": 0, "return_pct": 0.0, "cagr_pct": 0.0,
                "avg_hold_days": 0, "best_pct": 0.0, "worst_pct": 0.0,
                "beats_bnh": False, "n_open_end": 0}
    deployed = sum(t["cost"] for t in trades)
    pnl      = sum(t["pnl"]  for t in trades)
    n_win    = sum(1 for t in trades if t["pnl"] > 0)
    base     = total_injected if total_injected > 0 else deployed
    ret      = round(pnl / base * 100, 2) if base > 0 else 0.0
    final_v  = base + pnl
    cagr     = round(((final_v / base) ** (1 / years) - 1) * 100, 1) if base > 0 and years > 0 else 0.0
    return {
        "n_trades":       len(trades),
        "n_wins":         n_win,
        "win_rate":       round(n_win / len(trades) * 100, 1),
        "total_invested": round(deployed, 0),
        "total_injected": round(total_injected, 0),
        "total_pnl":      round(pnl, 0),
        "return_pct":     ret,
        "cagr_pct":       cagr,
        "avg_hold_days":  round(sum(t["hold_days"] for t in trades) / len(trades)),
        "best_pct":       round(max(t["pnl_pct"] for t in trades), 2),
        "worst_pct":      round(min(t["pnl_pct"] for t in trades), 2),
        "beats_bnh":      ret > bnh_ret,
        "n_open_end":     sum(1 for t in trades if t["exit_signal"] == "PERIOD_END"),
    }


# ── 主函式 ────────────────────────────────────────────────────────────────

def run_signal_backtest(symbol: str, name: str,
                        annual_budget: float = ANNUAL_BUDGET) -> dict:
    print(f"  信號回測 {symbol} {name}...")
    df = _fetch(symbol)
    if df.empty or len(df) < 200:
        print(f"    [!] 資料不足（{len(df) if not df.empty else 0} 天），跳過")
        return {"symbol": symbol, "name": name, "error": "資料不足"}

    sig = _daily_signals(df, symbol)
    if len(sig) < 100:
        return {"symbol": symbol, "name": name, "error": "指標計算資料不足"}

    years = max(1.0, (sig.index[-1] - sig.index[0]).days / 365.25)

    # ── B&H 基準：年度注資版（每年第一交易日買入 annual_budget）──────────
    bnh_shares = 0.0
    bnh_cost   = 0.0
    bnh_injected = 0.0
    bnh_txs: list[dict] = []
    bnh_inject_count = 0
    for yr in range(sig.index[0].year, sig.index[-1].year + 1):
        if bnh_inject_count >= MAX_INJECT_YEARS:
            break
        yr_data = sig[sig.index.year == yr]
        if yr_data.empty:
            continue
        bnh_injected += annual_budget
        bnh_inject_count += 1
        ep    = float(yr_data["close"].iloc[0])
        bought = int(annual_budget // ep)
        if bought > 0:
            cost = bought * ep
            bnh_shares += bought
            bnh_cost   += cost
            bnh_txs.append({"date": yr_data.index[0].date().isoformat(),
                             "price": round(ep, 2), "shares": bought,
                             "cost": round(cost, 0)})

    xp      = float(sig["close"].iloc[-1])
    bnh_val = bnh_shares * xp
    bnh_pnl = bnh_val - bnh_cost
    bnh_ret = round(bnh_pnl / bnh_injected * 100, 2) if bnh_injected > 0 else 0.0
    bnh_fv  = bnh_injected + bnh_pnl
    bnh_cagr = round(((bnh_fv / bnh_injected) ** (1 / years) - 1) * 100, 1) if bnh_injected > 0 else 0.0
    bnh = {
        "start_date":    sig.index[0].date().isoformat(),
        "end_date":      sig.index[-1].date().isoformat(),
        "annual_budget": annual_budget,
        "total_injected": round(bnh_injected, 0),
        "shares":        bnh_shares,
        "cost":          round(bnh_cost, 0),
        "final_value":   round(bnh_val, 0),
        "pnl":           round(bnh_pnl, 0),
        "return_pct":    bnh_ret,
        "cagr_pct":      bnh_cagr,
        "hold_days":     (sig.index[-1] - sig.index[0]).days,
        "transactions":  bnh_txs,
    }

    sc = sig["signal"].value_counts().to_dict()
    print(f"    信號統計  STRONG BUY×{sc.get('STRONG BUY',0)}"
          f"  BUY×{sc.get('BUY',0)}  SELL×{sc.get('SELL',0)}")

    modes_out = []
    for mode_id, label, buy_en, sbuy_en, trim_en in MODES:
        trades, injected = _simulate(sig, buy_en, sbuy_en, trim_en, annual_budget)
        st = _stats(trades, bnh_ret, total_injected=injected, years=years)
        flag = "[+]" if st["beats_bnh"] else "[-]"
        print(f"    {flag} {label:<18}  {st['n_trades']:>2}筆  "
              f"報酬{st['return_pct']:+6.1f}%  CAGR{st['cagr_pct']:+5.1f}%  "
              f"勝率{st['win_rate']:5.1f}%  損益 NT${st['total_pnl']:+10,.0f}")
        modes_out.append({
            "mode": mode_id, "label": label,
            "stats": st, "trades": trades,
        })

    return {
        "symbol":        symbol,
        "name":          name,
        "start_date":    sig.index[0].date().isoformat(),
        "end_date":      sig.index[-1].date().isoformat(),
        "bnh":           bnh,
        "sig_counts":    sc,
        "modes":         modes_out,
        "params": {
            "annual_budget": annual_budget,
            "sbuy_mult":     SBUY_MULT,
            "trim_pct":      TRIM_PROFIT,
        },
    }


def run_signal_backtest_all(annual_budget: float = ANNUAL_BUDGET) -> list[dict]:
    from tw_screener import load_config
    cfg    = load_config()
    stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]

    print(f"\n{'='*55}")
    print(f"信號跟單回測  {START_DATE} -> {END_DATE}")
    print(f"每年注資={annual_budget//10000:.0f}萬  SBUY={SBUY_MULT}x  TRIM={TRIM_PROFIT:.0f}%")
    print(f"{'='*55}\n")

    results = []
    for s in stocks:
        r = run_signal_backtest(s["symbol"], s["name"], annual_budget=annual_budget)
        results.append(r)
        print()

    today = _date.today().isoformat()
    cache_path = CACHE_DIR / f"signal_backtest_{today}.json"
    CACHE_DIR.mkdir(exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"快取已儲存 {cache_path}")
    return results


def load_signal_backtest_cache() -> dict:
    files = sorted(CACHE_DIR.glob("signal_backtest_*.json"), reverse=True)
    if not files:
        return {}
    data = json.loads(files[0].read_text(encoding="utf-8"))
    return {r["symbol"]: r for r in data if "error" not in r}


# ── Discord embed ─────────────────────────────────────────────────────────

def build_signal_backtest_embed(result: dict) -> dict:
    sym   = result["symbol"]
    name  = result["name"]
    bnh   = result["bnh"]
    modes = result.get("modes", [])
    sc    = result.get("sig_counts", {})
    p     = result.get("params", {})

    lines = [
        f"[{result['start_date']} -> {result['end_date']}]",
        f"信號次數  SBUY×{sc.get('STRONG BUY',0)}  BUY×{sc.get('BUY',0)}  SELL×{sc.get('SELL',0)}",
        "",
    ]
    for m in modes:
        s   = m["stats"]
        tag = "[+]" if s["beats_bnh"] else "[-]"
        if s["n_trades"] == 0:
            lines.append(f"  **{m['label']}** — 無交易")
        else:
            lines.append(
                f"{tag} **{m['label']}** — "
                f"{s['n_trades']}筆  `{s['return_pct']:+.1f}%`  "
                f"勝率`{s['win_rate']:.0f}%`  NT${s['total_pnl']:+,.0f}"
            )

    return {
        "color": 0x5DADE2,
        "title": f"📋 跟單回測 | {sym.replace('.TW','')} {name}",
        "description": "\n".join(lines),
        "fields": [
            {"name": "B&H 基準", "value": f"`{bnh['return_pct']:+.1f}%`  NT${bnh['pnl']:+,.0f}", "inline": True},
            {"name": "回測設定",  "value": f"BUY={p.get('budget',0)//10000}萬  "
                                           f"SBUY={p.get('sbuy_budget',0)//10000}萬  "
                                           f"TRIM≥{p.get('trim_pct',15):.0f}%", "inline": True},
        ],
    }


if __name__ == "__main__":
    results = run_signal_backtest_all()
