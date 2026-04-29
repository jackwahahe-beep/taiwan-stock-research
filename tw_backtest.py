"""
台股信號回測模組
- RSI 超賣買入 / 超買賣出策略
- MA 黃金/死亡交叉策略
- 輸出每檔股票回測指標，並附在 Discord 推播中
"""

import yfinance as yf
import pandas as pd
import numpy as np
import yaml
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo
from tw_screener import calc_rsi, load_config

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ = ZoneInfo("Asia/Taipei")


def fetch_long(symbol: str, period: str = "2y") -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, auto_adjust=True)
    if df.empty:
        return df
    df.index = df.index.tz_convert(TZ)
    return df


# ── 核心回測引擎 ────────────────────────────────────────────────────────────────

def backtest_rsi(df: pd.DataFrame, cfg: dict) -> dict:
    """RSI 超賣買入、超買賣出策略"""
    sig = cfg["signals"]["rsi"]
    close = df["Close"]
    rsi = calc_rsi(close, sig["period"])

    entries = (rsi.shift(1) > sig["oversold"]) & (rsi <= sig["oversold"])   # 剛跌破
    exits   = (rsi.shift(1) < sig["overbought"]) & (rsi >= sig["overbought"])  # 剛突破

    return _run_backtest(close, entries, exits, label="RSI策略")


def backtest_ma(df: pd.DataFrame, cfg: dict) -> dict:
    """MA 黃金交叉買入、死亡交叉賣出策略"""
    ma_cfg = cfg["signals"]["ma"]
    close = df["Close"]
    fast = close.rolling(ma_cfg["fast"]).mean()
    slow = close.rolling(ma_cfg["slow"]).mean()

    entries = (fast.shift(1) <= slow.shift(1)) & (fast > slow)   # 黃金交叉
    exits   = (fast.shift(1) >= slow.shift(1)) & (fast < slow)   # 死亡交叉

    return _run_backtest(close, entries, exits, label="MA交叉策略")


def _run_backtest(close: pd.Series, entries: pd.Series, exits: pd.Series,
                  label: str, init_cash: float = 100_000) -> dict:
    """通用逐筆交易回測引擎（純多頭）"""
    cash = init_cash
    position = 0.0
    entry_price = 0.0
    trades = []

    for i in range(1, len(close)):
        price = close.iloc[i]
        if np.isnan(price):
            continue

        if position == 0 and entries.iloc[i]:
            shares = cash // price
            if shares > 0:
                position = shares
                entry_price = price
                cash -= shares * price

        elif position > 0 and exits.iloc[i]:
            proceeds = position * price
            pnl_pct = (price - entry_price) / entry_price * 100
            trades.append({
                "entry": round(entry_price, 2),
                "exit": round(price, 2),
                "pnl_pct": round(pnl_pct, 2),
                "date": close.index[i].date().isoformat(),
            })
            cash += proceeds
            position = 0

    # 未平倉按最後收盤算
    final_value = cash + position * close.dropna().iloc[-1]
    total_return = (final_value - init_cash) / init_cash * 100

    if not trades:
        return {
            "label": label,
            "total_return_pct": round(total_return, 2),
            "trades": 0,
            "win_rate": None,
            "avg_pnl_pct": None,
            "max_loss_pct": None,
        }

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]

    return {
        "label": label,
        "total_return_pct": round(total_return, 2),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_pnl_pct": round(np.mean(pnls), 2),
        "max_loss_pct": round(min(pnls), 2),
        "trade_log": trades[-5:],   # 最近 5 筆
    }


def calc_bnh_return(close: pd.Series) -> float:
    """Buy & Hold 基準報酬"""
    clean = close.dropna()
    if len(clean) < 2:
        return 0.0
    return round((clean.iloc[-1] - clean.iloc[0]) / clean.iloc[0] * 100, 2)


# ── 批次回測 ────────────────────────────────────────────────────────────────────

def run_backtest_all(period: str = "2y") -> list[dict]:
    cfg = load_config()
    results = []
    all_stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]

    for stock in all_stocks:
        symbol = stock["symbol"]
        name = stock["name"]
        print(f"  回測 {symbol} {name}...")

        try:
            df = fetch_long(symbol, period)
            if df.empty or len(df) < 100:
                print(f"    [!] 資料不足，跳過")
                continue

            bnh = calc_bnh_return(df["Close"])
            rsi_bt = backtest_rsi(df, cfg)
            ma_bt  = backtest_ma(df, cfg)

            entry = {
                "symbol": symbol,
                "name": name,
                "period": period,
                "bnh_return_pct": bnh,
                "strategies": [rsi_bt, ma_bt],
            }
            results.append(entry)

            for s in [rsi_bt, ma_bt]:
                wr = f"{s['win_rate']}%" if s["win_rate"] is not None else "N/A"
                print(f"    {s['label']}: 總報酬 {s['total_return_pct']}%  勝率 {wr}  交易 {s['trades']} 次")
            print(f"    B&H 基準: {bnh}%")

        except Exception as e:
            print(f"    [ERROR] {symbol}: {e}")

    cache_file = CACHE_DIR / f"backtest_{date.today().isoformat()}.json"
    cache_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n回測結果已快取至 {cache_file}")
    return results


# ── Discord embed 格式化 ────────────────────────────────────────────────────────

def build_backtest_embed(bt: dict) -> dict:
    lines = []
    for s in bt["strategies"]:
        wr = f"{s['win_rate']}%" if s["win_rate"] is not None else "N/A"
        flag = "✅" if s["total_return_pct"] > bt["bnh_return_pct"] else "⚠️"
        lines.append(
            f"{flag} **{s['label']}** — 總報酬 `{s['total_return_pct']}%`  "
            f"勝率 `{wr}`  交易 `{s['trades']}` 次"
        )
    lines.append(f"📌 B&H 基準: `{bt['bnh_return_pct']}%`（{bt['period']}）")

    return {
        "color": 0x9B59B6,
        "title": f"📈 回測｜{bt['symbol']} {bt['name']}",
        "description": "\n".join(lines),
    }


if __name__ == "__main__":
    print("=== 台股策略回測 ===\n")
    results = run_backtest_all()
    print(f"\n完成，共回測 {len(results)} 檔")
