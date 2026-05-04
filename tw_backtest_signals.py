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
TRIM_PROFIT   = 30.0      # % 單筆獲利達此門檻 → TRIM 出場（B：從15%提升至30%）

# (mode_id, label, buy_en, sbuy_en, trim_en)
MODES = [
    ("BUY",   "BUY 策略",          True,  False, False),
    ("SBUY",  "STRONG BUY 策略",   False, True,  False),
    ("ALL",   "混合策略",           True,  True,  False),
    ("TRIM",  "混合+止盈30% 策略",  True,  True,  True ),
]

# C：ETF 不觸發 SELL（高息ETF 幾乎不會達到 SELL 閾值，且長期持有更佳）
ETF_SYMBOLS = {"0050.TW", "00878.TW", "00713.TW", "00929.TW", "00919.TW", "006208.TW"}

COMMISSION_RATE = 0.001425   # 券商手續費 0.1425%（買賣均收）
TAX_RATE        = 0.003      # 台灣證券交易稅 0.3%（僅賣出方）


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
              annual_budget: float = ANNUAL_BUDGET,
              symbol: str = "") -> tuple[list[dict], float, float]:
    """
    年度注資 + 信號跟單模擬，含手續費/交易稅與 MDD 追蹤。
    A：全年未部署 → 年末強制部署全部現金（FALLBACK）。
    B：TRIM 門檻 30%（淨獲利）。
    C：ETF 跳過 SELL。
    D：STRONG BUY 部署全部積累現金。
    費用：買進 COMMISSION_RATE；賣出 COMMISSION_RATE + TAX_RATE。
    回傳 (trades, total_injected, max_drawdown_pct)。
    """
    LOT_BUY  = annual_budget
    is_etf   = symbol in ETF_SYMBOLS

    year_last_days: dict = {}
    for _dt in sig.index:
        year_last_days[_dt.year] = _dt

    cash:              float      = 0.0
    total_injected:    float      = 0.0
    inject_count:      int        = 0
    open_lots:         list[dict] = []
    trades:            list[dict] = []
    prev_sig           = "HOLD"
    current_year       = None
    deployed_this_year = False
    peak_equity:       float      = 0.0
    max_dd_pct:        float      = 0.0

    for dt, row in sig.iterrows():
        yr = dt.year
        if yr != current_year:
            current_year       = yr
            deployed_this_year = False
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

        # ── 工具函式：計算賣出淨回收 ──────────────────────────────────
        def _sell_net(lot, exit_price):
            gross    = lot["shares"] * exit_price
            s_fee    = round(gross * (COMMISSION_RATE + TAX_RATE), 0)
            net      = gross - s_fee
            pnl      = net - lot["cost"]
            all_fees = s_fee + lot.get("buy_fee", 0)
            return net, pnl, all_fees

        # 1. TRIM：淨獲利達門檻即出清（B：門檻 30%）
        if trim_en and open_lots:
            keep = []
            for lot in open_lots:
                net_hyp, pnl_hyp, _ = _sell_net(lot, price)
                profit_pct = pnl_hyp / lot["cost"] * 100
                if profit_pct >= TRIM_PROFIT:
                    net, pnl, all_fees = _sell_net(lot, price)
                    hd = (dt.date() - _date.fromisoformat(lot["entry_date"])).days
                    trades.append({**lot,
                                   "exit_date": date_str, "exit_price": round(price, 2),
                                   "proceeds": round(net, 0), "pnl": round(pnl, 0),
                                   "pnl_pct": round(pnl / lot["cost"] * 100, 2),
                                   "hold_days": hd, "exit_signal": "TRIM",
                                   "fees": round(all_fees, 0),
                                   "exit_cond": {"RSI": round(rsi, 1), "vs_AVWAP%": vs_avwap}})
                    cash += net
                else:
                    keep.append(lot)
            open_lots = keep

        just_sold = False

        # 2. SELL：關閉所有持倉（C：ETF 跳過）
        if curr_sig == "SELL" and open_lots and not is_etf:
            ec = {"RSI": round(rsi, 1), "vs_AVWAP%": vs_avwap}
            for lot in open_lots:
                net, pnl, all_fees = _sell_net(lot, price)
                hd = (dt.date() - _date.fromisoformat(lot["entry_date"])).days
                trades.append({**lot,
                               "exit_date": date_str, "exit_price": round(price, 2),
                               "proceeds": round(net, 0), "pnl": round(pnl, 0),
                               "pnl_pct": round(pnl / lot["cost"] * 100, 2),
                               "hold_days": hd, "exit_signal": "SELL",
                               "fees": round(all_fees, 0),
                               "exit_cond": ec})
                cash += net
            open_lots = []
            just_sold = True

        # ── 工具函式：執行買進（含手續費）─────────────────────────────
        def _buy_lot(signal_name, max_spend):
            nonlocal cash
            shares = int(max_spend // (price * (1 + COMMISSION_RATE)))
            if shares <= 0:
                return False
            gross   = shares * price
            b_fee   = round(gross * COMMISSION_RATE, 0)
            cost    = gross + b_fee
            cash   -= cost
            open_lots.append({
                "entry_date": date_str, "entry_price": round(price, 2),
                "shares": shares, "cost": round(cost, 0), "entry_signal": signal_name,
                "buy_fee": b_fee,
                "entry_cond": {"DD%": round(dd, 1), "RSI": round(rsi, 1),
                               "vs_AVWAP%": vs_avwap},
            })
            return True

        # 3. BUY 進場（edge-triggered）
        if (not just_sold and curr_sig == "BUY" and buy_en
                and prev_sig != "BUY" and cash >= LOT_BUY * 0.3):
            if _buy_lot("BUY", min(cash, LOT_BUY)):
                deployed_this_year = True

        # 4. STRONG BUY 進場（edge-triggered，D：全倉出擊）
        elif (not just_sold and curr_sig == "STRONG BUY" and sbuy_en
              and prev_sig != "STRONG BUY" and cash >= LOT_BUY * 0.3):
            if _buy_lot("STRONG BUY", cash):
                deployed_this_year = True

        # A: 年末強制部署
        if (dt == year_last_days.get(yr) and not deployed_this_year
                and not just_sold and cash >= LOT_BUY * 0.3):
            if _buy_lot("FALLBACK", cash):
                deployed_this_year = True

        # MDD 追蹤
        open_value = sum(l["shares"] * price for l in open_lots)
        equity     = cash + open_value
        if equity > peak_equity:
            peak_equity = equity
        if peak_equity > 0:
            cur_dd = (equity - peak_equity) / peak_equity * 100
            if cur_dd < max_dd_pct:
                max_dd_pct = cur_dd

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
            net, pnl, all_fees = _sell_net(lot, lp)
            hd = (sig.index[-1].date() - _date.fromisoformat(lot["entry_date"])).days
            trades.append({**lot,
                           "exit_date": ld, "exit_price": round(lp, 2),
                           "proceeds": round(net, 0), "pnl": round(pnl, 0),
                           "pnl_pct": round(pnl / lot["cost"] * 100, 2),
                           "hold_days": hd, "exit_signal": "PERIOD_END",
                           "fees": round(all_fees, 0),
                           "exit_cond": ec})
    return trades, total_injected, max_dd_pct


# ── 統計摘要 ──────────────────────────────────────────────────────────────

def _stats(trades: list[dict], bnh_ret: float = 0.0,
           total_injected: float = 0.0, years: float = 10.0,
           mdd_pct: float = 0.0) -> dict:
    if not trades:
        return {"n_trades": 0, "n_wins": 0, "win_rate": 0.0,
                "total_invested": 0, "total_injected": round(total_injected, 0),
                "total_pnl": 0, "return_pct": 0.0, "cagr_pct": 0.0,
                "avg_hold_days": 0, "best_pct": 0.0, "worst_pct": 0.0,
                "beats_bnh": False, "n_open_end": 0,
                "mdd_pct": round(mdd_pct, 1), "total_fees": 0}
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
        "mdd_pct":        round(mdd_pct, 1),
        "total_fees":     round(sum(t.get("fees", 0) for t in trades), 0),
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

    # ── B&H 基準：年度注資版（每年第一交易日買入，含手續費）──────────
    bnh_shares = 0.0
    bnh_cost   = 0.0   # 含買進手續費
    bnh_injected = 0.0
    bnh_txs: list[dict] = []
    bnh_inject_count = 0
    for yr in range(sig.index[0].year, sig.index[-1].year + 1):
        if bnh_inject_count >= MAX_INJECT_YEARS:
            break
        yr_data = sig[sig.index.year == yr]
        if yr_data.empty:
            continue
        bnh_injected     += annual_budget
        bnh_inject_count += 1
        ep     = float(yr_data["close"].iloc[0])
        bought = int(annual_budget // (ep * (1 + COMMISSION_RATE)))
        if bought > 0:
            gross   = bought * ep
            b_fee   = round(gross * COMMISSION_RATE, 0)
            cost    = gross + b_fee
            bnh_shares += bought
            bnh_cost   += cost
            bnh_txs.append({"date": yr_data.index[0].date().isoformat(),
                             "price": round(ep, 2), "shares": bought,
                             "cost": round(cost, 0), "buy_fee": round(b_fee, 0)})

    xp           = float(sig["close"].iloc[-1])
    bnh_gross    = bnh_shares * xp
    bnh_sell_fee = round(bnh_gross * (COMMISSION_RATE + TAX_RATE), 0)
    bnh_net_val  = bnh_gross - bnh_sell_fee
    bnh_pnl      = bnh_net_val - bnh_cost
    bnh_ret      = round(bnh_pnl / bnh_injected * 100, 2) if bnh_injected > 0 else 0.0
    bnh_fv       = bnh_injected + bnh_pnl
    bnh_cagr     = round(((bnh_fv / bnh_injected) ** (1 / years) - 1) * 100, 1) if bnh_injected > 0 else 0.0
    bnh = {
        "start_date":    sig.index[0].date().isoformat(),
        "end_date":      sig.index[-1].date().isoformat(),
        "annual_budget": annual_budget,
        "total_injected": round(bnh_injected, 0),
        "shares":        bnh_shares,
        "cost":          round(bnh_cost, 0),
        "final_value":   round(bnh_net_val, 0),
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
        trades, injected, mdd = _simulate(sig, buy_en, sbuy_en, trim_en, annual_budget, symbol=symbol)
        st = _stats(trades, bnh_ret, total_injected=injected, years=years, mdd_pct=mdd)
        flag = "[+]" if st["beats_bnh"] else "[-]"
        print(f"    {flag} {label:<20}  {st['n_trades']:>2}trade  "
              f"ret{st['return_pct']:+6.1f}%  CAGR{st['cagr_pct']:+5.1f}%  "
              f"MDD{st['mdd_pct']:+5.1f}%  "
              f"fee NT${st['total_fees']:>8,.0f}  pnl NT${st['total_pnl']:+10,.0f}")
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
            "annual_budget":   annual_budget,
            "sbuy_mult":       SBUY_MULT,
            "trim_pct":        TRIM_PROFIT,
            "commission_rate": round(COMMISSION_RATE * 100, 4),
            "tax_rate":        round(TAX_RATE * 100, 2),
        },
    }


def run_signal_backtest_all(annual_budget: float = ANNUAL_BUDGET) -> list[dict]:
    from tw_screener import load_config
    cfg    = load_config()
    stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]

    print(f"\n{'='*60}")
    print(f"信號跟單回測  {START_DATE} -> {END_DATE}")
    print(f"每年注資={annual_budget//10000:.0f}萬  SBUY={SBUY_MULT}x  TRIM={TRIM_PROFIT:.0f}%"
          f"  手續費={COMMISSION_RATE*100:.4f}%  交易稅={TAX_RATE*100:.1f}%")
    print(f"{'='*60}\n")

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
