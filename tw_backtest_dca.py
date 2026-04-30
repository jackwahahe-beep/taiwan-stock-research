"""
台股長期定期定額（DCA）回測模組 v2
與 tw_screener.py v2 策略完全一致（AVWAP + DD + 個股RSI閾值）
策略：
  1. B&H DCA        — 每年無條件投入
  2. v2 BUY DCA     — DD>10% + 價格<AVWAP + RSI<個股閾值 才投入
  3. v2 STRONG DCA  — DD>20% + 價格<AVWAP×b2 + RSI<超賣閾值 才投入
  4. 市場模式 DCA   — 大盤 WARN/RISK 時加碼（逆向加碼）
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import glob as _glob
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo
from tw_screener import calc_rsi, load_config, SIGNAL_CONFIG, _DEFAULT_CFG
from tw_backtest import calc_rolling_avwap, calc_rolling_dd, fetch_long

BASE_DIR      = Path(__file__).parent
CACHE_DIR     = BASE_DIR / "cache"
TZ            = ZoneInfo("Asia/Taipei")
ANNUAL_BUDGET = 100_000
START_YEAR    = 2015
END_YEAR      = 2025

# 各股建議 DCA 策略（根據 10 年回測收斂結果）
RECOMMENDED_DCA = {
    "0050.TW":   "B&H DCA（無條件）",
    "006208.TW": "B&H DCA（無條件）",
    "00878.TW":  "v2 BUY DCA",           # MDD -21%→-10%，風險調整收斂
    "00713.TW":  "v2 BUY DCA",           # MDD -26%→-17%，風險調整收斂
    "00929.TW":  "v2 STRONG BUY DCA",    # +11pp（資料3年，謹慎參考）
    "00919.TW":  "B&H DCA（無條件）",
    "2330.TW":   "B&H DCA（無條件）",
    "2454.TW":   "v2 BUY DCA",           # +7pp
    "3711.TW":   "B&H DCA（無條件）",    # 差距小，任一可
    "2303.TW":   "B&H DCA（無條件）",
    "6770.TW":   "v2 STRONG BUY DCA",    # +22pp
    "2382.TW":   "B&H DCA（無條件）",
    "2308.TW":   "v2 STRONG BUY DCA",    # +191pp
    "3037.TW":   "B&H DCA（趨勢股，無條件）",
    "2408.TW":   "v2 BUY DCA",           # +24pp，2026-04-29 升格
}

CRASH_PERIODS = [
    ("2015-06-01", "2015-09-30", "中國股災 2015"),
    ("2018-10-01", "2018-12-31", "美中貿易戰 2018"),
    ("2020-02-01", "2020-03-31", "COVID 崩盤 2020"),
    ("2022-01-01", "2022-10-31", "升息熊市 2022"),
]


# ── 市場模式（歷史版，用 ^TWII）──────────────────────────────────────────────────

def _build_market_mode_series(twii_close: pd.Series, etf50_close: pd.Series) -> pd.Series:
    """
    與 tw_screener.get_market_mode 邏輯一致的歷史序列版本。
    回傳每個交易日的 'NORMAL'/'WARN'/'RISK'。
    """
    ma200     = twii_close.rolling(200, min_periods=50).mean()
    vs_ma200  = (twii_close - ma200) / ma200 * 100

    log_ret   = np.log(etf50_close / etf50_close.shift(1))
    vol_20    = log_ret.rolling(20).std() * np.sqrt(252) * 100

    # 對齊到 TWII 的索引
    vol_20 = vol_20.reindex(twii_close.index, method="ffill")

    modes = []
    for v, vol in zip(vs_ma200, vol_20):
        if np.isnan(v):
            modes.append("NORMAL")
        elif v < -5 or (v < 0 and (not np.isnan(vol) and vol > 25)):
            modes.append("RISK")
        elif v < 2 or (not np.isnan(vol) and vol > 30 and v < 10):
            modes.append("WARN")
        else:
            modes.append("NORMAL")

    return pd.Series(modes, index=twii_close.index)


# ── 核心 DCA 引擎 ───────────────────────────────────────────────────────────────

def _run_dca(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    symbol: str,
    allow_buy: pd.Series | None = None,
    label: str = "DCA",
) -> dict:
    """
    通用 DCA 引擎。
    allow_buy: 每日布林 Series；None → 無條件（B&H DCA）
    若當日不允許，資金累積到下次允許日。
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

    cash_reserve   = 0.0
    shares_held    = 0.0
    invested_total = 0.0
    transactions   = []
    pending_years  = set(first_trading_days.keys())

    for dt in close.index:
        yr = dt.year
        if yr in pending_years and dt >= first_trading_days.get(yr, dt):
            cash_reserve += ANNUAL_BUDGET
            pending_years.discard(yr)

        if cash_reserve <= 0:
            continue

        price = float(close.loc[dt])
        if np.isnan(price) or price <= 0:
            continue

        can_buy = True
        if allow_buy is not None:
            can_buy = bool(allow_buy.loc[dt]) if dt in allow_buy.index else False

        if can_buy:
            bought = cash_reserve // price
            if bought > 0:
                cost          = bought * price
                shares_held  += bought
                cash_reserve -= cost
                invested_total += cost
                transactions.append({
                    "date":   dt.date().isoformat(),
                    "price":  round(price, 2),
                    "shares": bought,
                    "cost":   round(cost, 0),
                })

    final_price   = float(close.iloc[-1])
    final_value   = shares_held * final_price + cash_reserve
    total_invested = invested_total + cash_reserve

    if total_invested == 0:
        return {"label": label, "error": "zero invested"}

    total_return_pct = (final_value - total_invested) / total_invested * 100
    n_years          = max(1, END_YEAR - START_YEAR)
    cagr             = ((final_value / total_invested) ** (1 / n_years) - 1) * 100

    # 最大回撤（持股市值序列）
    pv_list   = []
    run_sh    = 0.0
    tx_iter   = iter(transactions)
    next_tx   = next(tx_iter, None)
    for dt in close.index:
        if next_tx and dt.date().isoformat() == next_tx["date"]:
            run_sh  += next_tx["shares"]
            next_tx  = next(tx_iter, None)
        pv_list.append(run_sh * float(close.loc[dt]))
    pv_s       = pd.Series(pv_list, index=close.index)
    roll_max   = pv_s.cummax()
    drawdown   = (pv_s - roll_max) / roll_max.replace(0, np.nan) * 100
    max_dd     = float(drawdown.min())

    return {
        "label":             label,
        "total_invested":    round(total_invested, 0),
        "final_value":       round(final_value, 0),
        "total_return_pct":  round(total_return_pct, 2),
        "cagr_pct":          round(cagr, 2),
        "max_drawdown_pct":  round(max_dd, 2),
        "n_transactions":    len(transactions),
        "transactions":      transactions,
    }


# ── 股災期間表現 ────────────────────────────────────────────────────────────────

def _crash_performance(close: pd.Series) -> list[dict]:
    results = []
    close   = close.dropna()
    for start_str, end_str, label in CRASH_PERIODS:
        start = pd.Timestamp(start_str, tz=TZ)
        end   = pd.Timestamp(end_str,   tz=TZ)
        seg   = close[(close.index >= start) & (close.index <= end)]
        if len(seg) < 5:
            results.append({"period": label, "drawdown_pct": None})
            continue
        peak     = seg.iloc[0]
        trough   = seg.min()
        drawdown = (trough - peak) / peak * 100
        results.append({
            "period":       label,
            "drawdown_pct": round(float(drawdown), 2),
            "peak_date":    seg.index[0].date().isoformat(),
            "trough_date":  seg.idxmin().date().isoformat(),
        })
    return results


# ── 四策略批次比較 ──────────────────────────────────────────────────────────────

def run_dca_backtest(symbol: str, name: str, cfg: dict,
                     twii_close: pd.Series, etf50_close: pd.Series) -> dict:
    print(f"  DCA 回測 {symbol} {name}...")
    df = fetch_long(symbol, period="10y")
    if df.empty or len(df) < 500:
        print(f"    [!] 資料不足（{len(df)} 天），跳過")
        return {"symbol": symbol, "name": name, "error": "資料不足"}

    close  = df["Close"].dropna()
    high   = df["High"].reindex(close.index)
    low    = df["Low"].reindex(close.index)
    volume = df["Volume"].reindex(close.index)
    stock_cfg = SIGNAL_CONFIG.get(symbol, _DEFAULT_CFG)

    # 預計算 v2 指標
    rsi   = calc_rsi(close, 14)
    avwap = calc_rolling_avwap(close, high, low, volume, lookback=60)
    dd    = calc_rolling_dd(close, lookback=60)

    b1 = avwap * stock_cfg["b1"]
    b2 = avwap * stock_cfg["b2"]

    # 布林過濾 Series
    buy_filter   = (dd <= -0.10) & (close < b1) & (rsi <= stock_cfg["rsi_buy"])
    sbuy_filter  = (dd <= -0.20) & (close < b2) & (rsi <= stock_cfg["rsi_sbuy"])

    # 市場模式過濾：WARN/RISK 時才允許加碼（逆向加碼策略）
    mode_series  = _build_market_mode_series(twii_close, etf50_close)
    mode_aligned = mode_series.reindex(close.index, method="ffill").fillna("NORMAL")
    market_dip   = mode_aligned.isin(["WARN", "RISK"])

    # bnh_dca 旗標：超強趨勢股不做擇時，全部策略都用 B&H
    is_bnh_only = stock_cfg.get("bnh_dca", False)

    if is_bnh_only:
        strategies = [
            _run_dca(close, high, low, volume, symbol,
                     allow_buy=None, label="B&H DCA（趨勢股，無條件）"),
        ]
    else:
        strategies = [
            _run_dca(close, high, low, volume, symbol,
                     allow_buy=None,        label="B&H DCA（無條件）"),
            _run_dca(close, high, low, volume, symbol,
                     allow_buy=buy_filter,  label="v2 BUY DCA"),
            _run_dca(close, high, low, volume, symbol,
                     allow_buy=sbuy_filter, label="v2 STRONG BUY DCA"),
            _run_dca(close, high, low, volume, symbol,
                     allow_buy=market_dip,  label="市場警戒逆向加碼"),
        ]

    crashes = _crash_performance(close)
    result  = {
        "symbol":           symbol,
        "name":             name,
        "period":           f"{START_YEAR}–{END_YEAR}",
        "annual_budget":    ANNUAL_BUDGET,
        "strategies":       strategies,
        "crash_performance": crashes,
    }

    for s in strategies:
        if "error" not in s:
            print(f"    {s['label']}: 總報酬 {s['total_return_pct']}%  CAGR {s['cagr_pct']}%  回撤 {s['max_drawdown_pct']}%")

    return result


def run_dca_all() -> list[dict]:
    cfg        = load_config()
    all_stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]

    # 預先下載 ^TWII 和 0050.TW（市場模式用）
    print("  下載大盤資料（^TWII、0050）...")
    twii  = yf.Ticker("^TWII").history(period="10y", auto_adjust=True)
    etf50 = yf.Ticker("0050.TW").history(period="10y", auto_adjust=True)
    if not twii.empty:
        twii.index  = twii.index.tz_convert(TZ)
    if not etf50.empty:
        etf50.index = etf50.index.tz_convert(TZ)
    twii_close  = twii["Close"].dropna()  if not twii.empty  else pd.Series(dtype=float)
    etf50_close = etf50["Close"].dropna() if not etf50.empty else pd.Series(dtype=float)

    results = []
    for stock in all_stocks:
        try:
            r = run_dca_backtest(stock["symbol"], stock["name"], cfg,
                                 twii_close, etf50_close)
            results.append(r)
        except Exception as e:
            print(f"    [ERROR] {stock['symbol']}: {e}")

    cache_file = CACHE_DIR / f"dca_backtest_{date.today().isoformat()}.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDCA 回測快取至 {cache_file}")
    return results


# ── Discord embed ───────────────────────────────────────────────────────────────

def build_dca_embed(bt: dict) -> dict:
    if "error" in bt:
        return {"color": 0x95A5A6,
                "title": f"📉 DCA 回測｜{bt['symbol']} {bt['name']}",
                "description": f"⚠️ {bt['error']}"}

    strategies = [s for s in bt["strategies"] if "error" not in s]
    if not strategies:
        return {"color": 0x95A5A6, "title": bt["symbol"], "description": "無策略結果"}

    recommended = RECOMMENDED_DCA.get(bt["symbol"], "")
    best  = max(strategies, key=lambda s: s["total_return_pct"])
    lines = []
    for s in strategies:
        is_rec  = (s["label"] == recommended)
        is_best = (s["label"] == best["label"])
        flag = "🏆" if is_best else "  "
        rec_tag = "　⭐建議" if is_rec else ""
        lines.append(
            f"{flag} **{s['label']}**{rec_tag}\n"
            f"　總報酬 `{s['total_return_pct']}%`　"
            f"CAGR `{s['cagr_pct']}%`　"
            f"最大回撤 `{s['max_drawdown_pct']}%`"
        )

    crash_lines = []
    for c in bt.get("crash_performance", []):
        if c["drawdown_pct"] is not None:
            sev = "🔴" if c["drawdown_pct"] < -30 else ("🟡" if c["drawdown_pct"] < -15 else "🟢")
            crash_lines.append(f"{sev} {c['period']}：`{c['drawdown_pct']}%`")
        else:
            crash_lines.append(f"⬜ {c['period']}：資料不足")

    fields = [{"name": f"📊 策略比較（每年 NT${bt['annual_budget']:,}，{bt['period']}）",
               "value": "\n".join(lines), "inline": False}]
    if recommended:
        fields.append({"name": "⭐ 建議策略", "value": f"`{recommended}`", "inline": False})
    if crash_lines:
        fields.append({"name": "🌪️ 股災期間最大跌幅",
                        "value": "\n".join(crash_lines), "inline": False})

    return {
        "color":  0x1ABC9C,
        "title":  f"💰 DCA 回測 v2｜{bt['symbol']} {bt['name']}",
        "fields": fields,
        "footer": {"text": f"回測期間 {bt['period']}｜每年 NT${bt['annual_budget']:,}"},
    }


def load_dca_cache() -> list[dict]:
    files = sorted(_glob.glob(str(CACHE_DIR / "dca_backtest_*.json")), reverse=True)
    if not files:
        return []
    return json.loads(Path(files[0]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    import sys
    from tw_discord import send_webhook, load_config as discord_cfg

    print(f"=== 台股 DCA 長期回測 v2  {START_YEAR}–{END_YEAR} ===\n")
    results = run_dca_all()

    if "--push" in sys.argv:
        cfg    = discord_cfg()
        url    = cfg["discord"]["webhook_url"]
        embeds = [build_dca_embed(r) for r in results]
        for i in range(0, len(embeds), 10):
            ok = send_webhook({"embeds": embeds[i:i+10]}, url)
            print(f"[Discord] DCA 推播 {len(embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")
    else:
        print("\n加上 --push 可推播至 Discord")

    print(f"\n完成，共回測 {len(results)} 檔")
