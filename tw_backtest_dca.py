"""
台股長期定期定額（DCA）回測模組
- 每年 1 月第一個交易日投入 NT$100,000
- 4 策略比較：B&H DCA、RSI擇時 DCA、MA多頭過濾 DCA、組合策略 DCA
- 最少 10 年資料（2015–2025）
- 股災閃避分析：中國股災/貿易戰/COVID/升息熊市
- 輸出 Discord embed 比較表
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from tw_screener import calc_rsi, load_config

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ = ZoneInfo("Asia/Taipei")

ANNUAL_BUDGET = 100_000        # 每年投入 NT$
START_YEAR    = 2015
END_YEAR      = 2025

CRASH_PERIODS = [
    ("2015-06-01", "2015-09-30", "中國股災 2015"),
    ("2018-10-01", "2018-12-31", "美中貿易戰 2018"),
    ("2020-02-01", "2020-03-31", "COVID 崩盤 2020"),
    ("2022-01-01", "2022-10-31", "升息熊市 2022"),
]


# ── 資料抓取 ────────────────────────────────────────────────────────────────────

def fetch_10y(symbol: str) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="10y", auto_adjust=True)
    if df.empty:
        return df
    df.index = df.index.tz_convert(TZ)
    return df


# ── 信號輔助 ────────────────────────────────────────────────────────────────────

def _rsi_oversold_filter(close: pd.Series, period: int = 14, oversold: int = 35) -> pd.Series:
    """RSI < oversold 時允許買入（看漲擇時）"""
    rsi = calc_rsi(close, period)
    return rsi < oversold


def _ma_bull_filter(close: pd.Series, fast: int = 20, slow: int = 60) -> pd.Series:
    """MA fast > MA slow 時允許買入（多頭市場過濾）"""
    return close.rolling(fast).mean() > close.rolling(slow).mean()


# ── 核心 DCA 引擎 ───────────────────────────────────────────────────────────────

def _run_dca(
    close: pd.Series,
    allow_buy: pd.Series | None = None,
    label: str = "DCA",
) -> dict:
    """
    通用 DCA 引擎。
    - 每年 1 月第一個交易日嘗試投入 ANNUAL_BUDGET
    - allow_buy: 布林 Series；None 表示無條件買入（B&H DCA）
    - 若當天不允許買入，資金累積到下次允許日
    """
    close = close.dropna()
    if close.empty:
        return {"label": label, "error": "no data"}

    years = range(START_YEAR, END_YEAR + 1)
    first_trading_days = {}
    for yr in years:
        yr_data = close[close.index.year == yr]
        if not yr_data.empty:
            first_trading_days[yr] = yr_data.index[0]

    cash_reserve = 0.0
    shares = 0.0
    invested_total = 0.0
    transactions = []

    daily_index = close.index

    for yr, inject_date in first_trading_days.items():
        cash_reserve += ANNUAL_BUDGET

    # 重算：按日遍歷
    cash_reserve = 0.0
    shares = 0.0
    invested_total = 0.0
    pending_years = set(first_trading_days.keys())
    transactions = []

    for dt in daily_index:
        yr = dt.year
        # 到達注資年份的第一個交易日 → 加入現金池
        if yr in pending_years and dt >= first_trading_days.get(yr, dt):
            cash_reserve += ANNUAL_BUDGET
            pending_years.discard(yr)

        if cash_reserve <= 0:
            continue

        price = close.loc[dt]
        if np.isnan(price) or price <= 0:
            continue

        # 判斷是否允許買入
        can_buy = True
        if allow_buy is not None:
            can_buy = bool(allow_buy.loc[dt]) if dt in allow_buy.index else False

        if can_buy:
            bought = cash_reserve // price
            if bought > 0:
                cost = bought * price
                shares += bought
                cash_reserve -= cost
                invested_total += cost
                transactions.append({
                    "date": dt.date().isoformat(),
                    "price": round(price, 2),
                    "shares": bought,
                    "cost": round(cost, 0),
                })

    final_price = close.iloc[-1]
    final_value = shares * final_price + cash_reserve
    total_invested = invested_total + cash_reserve   # 含未動用現金

    if total_invested == 0:
        return {"label": label, "error": "zero invested"}

    total_return_pct = (final_value - total_invested) / total_invested * 100
    n_years = max(1, END_YEAR - START_YEAR)
    cagr = ((final_value / total_invested) ** (1 / n_years) - 1) * 100

    # 計算最大回撤（依持股市值序列）
    portfolio_values = []
    running_shares = 0.0
    running_cash = 0.0
    tx_iter = iter(transactions)
    next_tx = next(tx_iter, None)

    for dt in daily_index:
        if next_tx and dt.date().isoformat() == next_tx["date"]:
            running_shares += next_tx["shares"]
            running_cash -= next_tx["cost"]
            next_tx = next(tx_iter, None)
        pv = running_shares * close.loc[dt]
        portfolio_values.append(pv)

    pv_series = pd.Series(portfolio_values, index=daily_index)
    rolling_max = pv_series.cummax()
    drawdown = (pv_series - rolling_max) / rolling_max.replace(0, np.nan) * 100
    max_drawdown = drawdown.min()

    return {
        "label": label,
        "total_invested": round(total_invested, 0),
        "final_value": round(final_value, 0),
        "total_return_pct": round(total_return_pct, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "n_transactions": len(transactions),
        "last_tx": transactions[-3:] if transactions else [],
    }


# ── 股災期間表現 ────────────────────────────────────────────────────────────────

def _crash_performance(close: pd.Series) -> list[dict]:
    results = []
    close = close.dropna()
    for start_str, end_str, label in CRASH_PERIODS:
        start = pd.Timestamp(start_str, tz=TZ)
        end   = pd.Timestamp(end_str,   tz=TZ)
        seg = close[(close.index >= start) & (close.index <= end)]
        if len(seg) < 5:
            results.append({"period": label, "drawdown_pct": None})
            continue
        peak = seg.iloc[0]
        trough = seg.min()
        drawdown = (trough - peak) / peak * 100
        results.append({
            "period": label,
            "drawdown_pct": round(drawdown, 2),
            "peak_date": seg.index[0].date().isoformat(),
            "trough_date": seg.idxmin().date().isoformat(),
        })
    return results


# ── 四策略批次比較 ──────────────────────────────────────────────────────────────

def run_dca_backtest(symbol: str, name: str, cfg: dict) -> dict:
    print(f"  DCA 回測 {symbol} {name}...")

    df = fetch_10y(symbol)
    if df.empty or len(df) < 500:
        print(f"    [!] 資料不足（{len(df)} 天），跳過")
        return {"symbol": symbol, "name": name, "error": "資料不足"}

    close = df["Close"]
    rsi_cfg = cfg["signals"]["rsi"]
    ma_cfg  = cfg["signals"]["ma"]

    rsi_filter = _rsi_oversold_filter(close, rsi_cfg["period"], oversold=40)
    ma_filter  = _ma_bull_filter(close, ma_cfg["fast"], ma_cfg["slow"])
    combined   = rsi_filter | ma_filter   # RSI 超賣 OR MA 多頭均可買入

    strategies = [
        _run_dca(close, allow_buy=None,     label="B&H DCA（無條件）"),
        _run_dca(close, allow_buy=rsi_filter, label="RSI擇時 DCA"),
        _run_dca(close, allow_buy=ma_filter,  label="MA多頭過濾 DCA"),
        _run_dca(close, allow_buy=combined,   label="組合策略 DCA"),
    ]

    crashes = _crash_performance(close)

    result = {
        "symbol": symbol,
        "name": name,
        "period": f"{START_YEAR}–{END_YEAR}",
        "annual_budget": ANNUAL_BUDGET,
        "strategies": strategies,
        "crash_performance": crashes,
    }

    for s in strategies:
        if "error" not in s:
            print(f"    {s['label']}: 總報酬 {s['total_return_pct']}%  CAGR {s['cagr_pct']}%  回撤 {s['max_drawdown_pct']}%")

    return result


def run_dca_all() -> list[dict]:
    cfg = load_config()
    all_stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]
    results = []

    for stock in all_stocks:
        try:
            r = run_dca_backtest(stock["symbol"], stock["name"], cfg)
            results.append(r)
        except Exception as e:
            print(f"    [ERROR] {stock['symbol']}: {e}")

    cache_file = CACHE_DIR / f"dca_backtest_{date.today().isoformat()}.json"
    cache_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDCA 回測快取至 {cache_file}")
    return results


# ── Discord embed ───────────────────────────────────────────────────────────────

def build_dca_embed(bt: dict) -> dict:
    if "error" in bt:
        return {
            "color": 0x95A5A6,
            "title": f"📉 DCA 回測｜{bt['symbol']} {bt['name']}",
            "description": f"⚠️ {bt['error']}",
        }

    strategies = [s for s in bt["strategies"] if "error" not in s]
    if not strategies:
        return {"color": 0x95A5A6, "title": bt["symbol"], "description": "無策略結果"}

    # 找最佳策略（按總報酬排序）
    best = max(strategies, key=lambda s: s["total_return_pct"])

    lines = []
    for s in strategies:
        flag = "🏆" if s["label"] == best["label"] else "  "
        lines.append(
            f"{flag} **{s['label']}**\n"
            f"　總報酬 `{s['total_return_pct']}%`　"
            f"CAGR `{s['cagr_pct']}%`　"
            f"最大回撤 `{s['max_drawdown_pct']}%`"
        )

    # 股災分析
    crash_lines = []
    for c in bt.get("crash_performance", []):
        if c["drawdown_pct"] is not None:
            severity = "🔴" if c["drawdown_pct"] < -30 else ("🟡" if c["drawdown_pct"] < -15 else "🟢")
            crash_lines.append(f"{severity} {c['period']}：`{c['drawdown_pct']}%`")
        else:
            crash_lines.append(f"⬜ {c['period']}：資料不足")

    fields = [
        {
            "name": f"📊 策略比較（每年投入 NT${bt['annual_budget']:,}，{bt['period']}）",
            "value": "\n".join(lines),
            "inline": False,
        },
    ]
    if crash_lines:
        fields.append({
            "name": "🌪️ 股災期間最大跌幅",
            "value": "\n".join(crash_lines),
            "inline": False,
        })

    return {
        "color": 0x1ABC9C,
        "title": f"💰 DCA 長期回測｜{bt['symbol']} {bt['name']}",
        "fields": fields,
        "footer": {"text": f"回測期間 {bt['period']}｜每年 NT${bt['annual_budget']:,}"},
    }


def load_dca_cache() -> list[dict]:
    import glob as _glob
    files = sorted(_glob.glob(str(CACHE_DIR / "dca_backtest_*.json")), reverse=True)
    if not files:
        return []
    return json.loads(Path(files[0]).read_text(encoding="utf-8"))


# ── CLI ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from tw_discord import send_webhook, load_config as discord_cfg

    print(f"=== 台股 DCA 長期回測 {START_YEAR}–{END_YEAR} ===\n")
    results = run_dca_all()

    push = "--push" in sys.argv
    if push:
        cfg = discord_cfg()
        url = cfg["discord"]["webhook_url"]
        embeds = [build_dca_embed(r) for r in results]
        for i in range(0, len(embeds), 10):
            ok = send_webhook({"embeds": embeds[i:i+10]}, url)
            print(f"[Discord] DCA 推播 {len(embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")
    else:
        print("\n加上 --push 可推播至 Discord")

    print(f"\n完成，共回測 {len(results)} 檔")
