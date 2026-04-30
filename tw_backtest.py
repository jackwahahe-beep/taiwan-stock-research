"""
台股信號回測模組 v2
與 tw_screener.py v2 策略完全一致：
  - AVWAP（60天低點錨定）
  - DD（60天高點回撤）
  - 個股化 RSI 閾值（SIGNAL_CONFIG）
  - STRONG BUY / BUY / SELL 三段信號
比較：B&H、BUY策略、STRONG_BUY策略
"""

import pandas as pd
import numpy as np
import yaml
import json
import glob as _glob
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo
from tw_screener import calc_rsi, load_config, fetch_data, SIGNAL_CONFIG, _DEFAULT_CFG

BASE_DIR  = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ        = ZoneInfo("Asia/Taipei")


def fetch_long(symbol: str, period: str = "2y") -> pd.DataFrame:
    return fetch_data(symbol, period=period)


# ── 滾動 AVWAP（與 tw_screener 邏輯一致）──────────────────────────────────────

def calc_rolling_avwap(close: pd.Series, high: pd.Series,
                       low: pd.Series, volume: pd.Series,
                       lookback: int = 60) -> pd.Series:
    """每個時間點用過去資料計算 AVWAP，無未來資料洩漏。"""
    n      = len(close)
    result = np.full(n, np.nan)
    c_arr  = close.values.astype(float)
    h_arr  = high.values.astype(float)
    l_arr  = low.values.astype(float)
    v_arr  = volume.values.astype(float)

    for i in range(n):
        lb      = min(lookback, i + 1)
        win_c   = c_arr[i - lb + 1: i + 1]
        min_rel = int(np.argmin(win_c))
        min_p   = win_c[min_rel]
        curr    = c_arr[i]
        confirmed = curr >= min_p * 1.05
        anchor  = (i - lb + 1 + min_rel) if confirmed else (i - lb + 1)

        tp_seg  = (h_arr[anchor:i+1] + l_arr[anchor:i+1] + c_arr[anchor:i+1]) / 3
        vol_seg = v_arr[anchor:i+1]
        vs      = vol_seg.sum()
        result[i] = float((tp_seg * vol_seg).sum() / vs) if vs > 0 else curr

    return pd.Series(result, index=close.index)


def calc_rolling_dd(close: pd.Series, lookback: int = 60) -> pd.Series:
    """每個時間點相對過去 lookback 天最高點的跌幅（負值）。"""
    peak = close.rolling(lookback, min_periods=1).max()
    return (close - peak) / peak


# ── v2 回測引擎 ────────────────────────────────────────────────────────────────

def _run_backtest_v2(
    df: pd.DataFrame,
    symbol: str,
    label: str,
    min_signal: str = "BUY",
    init_cash: float = 100_000,
) -> dict:
    """
    v2 信號回測（純多頭、逐日）。
    min_signal="BUY"        → BUY 或 STRONG BUY 都進場
    min_signal="STRONG BUY" → 只有 STRONG BUY 才進場
    出場：RSI過熱 + 超過AVWAP目標 + 高於MA20×1.15（三條同時）
    """
    cfg   = SIGNAL_CONFIG.get(symbol, _DEFAULT_CFG)
    close = df["Close"].dropna()
    high  = df["High"].reindex(close.index)
    low   = df["Low"].reindex(close.index)
    vol   = df["Volume"].reindex(close.index)

    rsi   = calc_rsi(close, 14)
    ma20  = close.rolling(20).mean()
    avwap = calc_rolling_avwap(close, high, low, vol, lookback=60)
    dd    = calc_rolling_dd(close, lookback=60)

    cash        = init_cash
    position    = 0.0
    entry_price = 0.0
    entry_date  = ""
    trades      = []
    pv_list     = []   # daily portfolio value for MDD + Sharpe

    for i in range(60, len(close)):
        price   = float(close.iloc[i])
        rsi_v   = float(rsi.iloc[i])
        avwap_v = float(avwap.iloc[i])
        dd_v    = float(dd.iloc[i])
        ma20_v  = float(ma20.iloc[i])

        if any(np.isnan(x) for x in [price, rsi_v, avwap_v, dd_v, ma20_v]):
            continue

        b1 = avwap_v * cfg["b1"]
        b2 = avwap_v * cfg["b2"]
        s  = avwap_v * cfg["s"]

        is_strong = dd_v <= -0.20 and price < b2 and rsi_v <= cfg["rsi_sbuy"]
        is_buy    = dd_v <= -0.10 and price < b1 and rsi_v <= cfg["rsi_buy"]
        enter     = (is_strong or (min_signal == "BUY" and is_buy)) and position == 0

        is_sell = (rsi_v >= cfg["rsi_sell"]
                   and price >= s
                   and price > ma20_v * 1.15)

        if enter:
            shares = cash // price
            if shares > 0:
                position    = shares
                entry_price = price
                entry_date  = close.index[i].date().isoformat()
                cash       -= shares * price

        elif position > 0 and is_sell:
            pnl_pct = (price - entry_price) / entry_price * 100
            trades.append({
                "entry_date": entry_date,
                "date":       close.index[i].date().isoformat(),
                "entry":      round(entry_price, 2),
                "exit":       round(price, 2),
                "pnl_pct":    round(pnl_pct, 2),
            })
            cash    += position * price
            position = 0

        pv_list.append(cash + position * price)

    final_value  = cash + position * float(close.dropna().iloc[-1])
    total_return = (final_value - init_cash) / init_cash * 100

    # MDD
    mdd = None
    if pv_list:
        pv    = pd.Series(pv_list, dtype=float)
        peak  = pv.cummax()
        dd_s  = (pv - peak) / peak.replace(0, np.nan) * 100
        mdd   = round(float(dd_s.min()), 2)

    # Sharpe（年化，無風險利率 0）
    sharpe = None
    if len(pv_list) >= 20:
        pv       = pd.Series(pv_list, dtype=float)
        ret_daily = pv.pct_change().dropna()
        if ret_daily.std() > 0:
            sharpe = round(float(ret_daily.mean() / ret_daily.std() * np.sqrt(252)), 2)

    if not trades:
        return {"label": label, "total_return_pct": round(total_return, 2),
                "trades": 0, "win_rate": None, "avg_pnl_pct": None,
                "max_loss_pct": None, "max_drawdown_pct": mdd, "sharpe": sharpe}

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    return {
        "label":            label,
        "total_return_pct": round(total_return, 2),
        "trades":           len(trades),
        "win_rate":         round(len(wins) / len(trades) * 100, 1),
        "avg_pnl_pct":      round(np.mean(pnls), 2),
        "max_loss_pct":     round(min(pnls), 2),
        "max_drawdown_pct": mdd,
        "sharpe":           sharpe,
        "trade_log":        trades,
    }


def calc_bnh_return(close: pd.Series) -> float:
    clean = close.dropna()
    if len(clean) < 2:
        return 0.0
    return round((clean.iloc[-1] - clean.iloc[0]) / clean.iloc[0] * 100, 2)


# ── 批次回測 ────────────────────────────────────────────────────────────────────

def run_backtest_all(period: str = "2y") -> list[dict]:
    cfg        = load_config()
    results    = []
    all_stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]

    for stock in all_stocks:
        symbol = stock["symbol"]
        name   = stock["name"]
        print(f"  回測 {symbol} {name}...")

        try:
            df = fetch_long(symbol, period)
            if df.empty or len(df) < 100:
                print(f"    [!] 資料不足，跳過")
                continue

            bnh     = calc_bnh_return(df["Close"])
            bt_buy  = _run_backtest_v2(df, symbol, "v2 BUY策略",       min_signal="BUY")
            bt_sbuy = _run_backtest_v2(df, symbol, "v2 STRONG BUY策略", min_signal="STRONG BUY")

            entry = {
                "symbol":         symbol,
                "name":           name,
                "period":         period,
                "bnh_return_pct": bnh,
                "strategies":     [bt_buy, bt_sbuy],
            }
            results.append(entry)

            for s in [bt_buy, bt_sbuy]:
                wr = f"{s['win_rate']}%" if s["win_rate"] is not None else "N/A"
                print(f"    {s['label']}: 總報酬 {s['total_return_pct']}%  勝率 {wr}  交易 {s['trades']} 次")
            print(f"    B&H 基準: {bnh}%")

        except Exception as e:
            print(f"    [ERROR] {symbol}: {e}")

    cache_file = CACHE_DIR / f"backtest_{date.today().isoformat()}.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n回測結果已快取至 {cache_file}")
    return results


# ── Discord embed ──────────────────────────────────────────────────────────────

def build_backtest_embed(bt: dict) -> dict:
    lines = []
    for s in bt["strategies"]:
        wr     = f"{s['win_rate']}%" if s["win_rate"] is not None else "N/A"
        mdd    = f"{s['max_drawdown_pct']}%" if s.get("max_drawdown_pct") is not None else "N/A"
        sharpe = f"{s['sharpe']}" if s.get("sharpe") is not None else "N/A"
        flag   = "✅" if s["total_return_pct"] > bt["bnh_return_pct"] else "⚠️"
        lines.append(
            f"{flag} **{s['label']}** — 總報酬 `{s['total_return_pct']}%`  "
            f"勝率 `{wr}`  MDD `{mdd}`  Sharpe `{sharpe}`"
        )
    lines.append(f"📌 B&H 基準: `{bt['bnh_return_pct']}%`（{bt['period']}）")
    return {
        "color":       0x9B59B6,
        "title":       f"📈 回測 v2｜{bt['symbol']} {bt['name']}",
        "description": "\n".join(lines),
    }


def load_backtest_cache() -> dict:
    files = sorted(_glob.glob(str(CACHE_DIR / "backtest_*.json")), reverse=True)
    if not files:
        return {}
    data = json.loads(Path(files[0]).read_text(encoding="utf-8"))
    return {r["symbol"]: r for r in data}


if __name__ == "__main__":
    print("=== 台股策略回測 v2 ===\n")
    results = run_backtest_all()
    print(f"\n完成，共回測 {len(results)} 檔")
