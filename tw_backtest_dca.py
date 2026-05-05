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

BASE_DIR         = Path(__file__).parent
CACHE_DIR        = BASE_DIR / "cache"
TZ               = ZoneInfo("Asia/Taipei")
ANNUAL_BUDGET    = 100_000
START_YEAR       = 2015
END_YEAR         = 2025
COMMISSION_RATE  = 0.001425   # 手續費 0.1425%（買賣均收）

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
    close_raw: pd.Series,
    dividends: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    symbol: str,
    allow_buy: pd.Series | None = None,
    label: str = "DCA",
    indicator_series: dict | None = None,
) -> dict:
    """
    通用 DCA 引擎（含手續費 + 股息再投入）。
    close_raw:  原始未調整收盤價（成本計算用）
    dividends:  每股配息金額 Series（以 ex-date 為 index）
    allow_buy:  每日布林 Series；None → 無條件（B&H DCA）
    若當日不允許，資金累積到下次允許日。
    """
    close = close_raw.dropna()
    if close.empty:
        return {"label": label, "error": "no data"}

    years = range(START_YEAR, END_YEAR + 1)
    first_trading_days: dict = {}
    last_trading_days:  dict = {}
    for yr in years:
        yr_data = close[close.index.year == yr]
        if not yr_data.empty:
            first_trading_days[yr] = yr_data.index[0]
            last_trading_days[yr]  = yr_data.index[-1]

    cash_reserve     = 0.0
    shares_held      = 0.0
    invested_total   = 0.0
    total_fees       = 0.0
    div_received     = 0.0   # 累積配息現金（已收到，再投入）
    div_shares       = 0     # 配息再投入買入的總股數
    transactions     = []
    pending_years    = set(first_trading_days.keys())

    # 將 dividends index 轉換為可快速查詢的 set
    div_dates = set(dividends.index.normalize()) if not dividends.empty else set()

    for dt in close.index:
        yr = dt.year
        if yr in pending_years and dt >= first_trading_days.get(yr, dt):
            cash_reserve += ANNUAL_BUDGET
            pending_years.discard(yr)

        price = float(close.loc[dt])
        if np.isnan(price) or price <= 0:
            continue

        # ── 股息再投入（不計入 invested_total，屬於被動收入）──────────
        dt_norm = dt.normalize()
        if dt_norm in div_dates:
            matching = dividends[dividends.index.normalize() == dt_norm]
            if not matching.empty and shares_held > 0:
                div_ps    = float(matching.iloc[0])
                div_cash  = shares_held * div_ps
                div_received += div_cash
                # 用配息現金買入更多股數（扣手續費）
                max_div_sh = int(div_cash / (price * (1 + COMMISSION_RATE)))
                if max_div_sh > 0:
                    d_fee = round(max_div_sh * price * COMMISSION_RATE, 0)
                    shares_held  += max_div_sh
                    div_shares   += max_div_sh
                    total_fees   += d_fee

        if cash_reserve <= 0:
            continue

        can_buy = True
        if allow_buy is not None:
            can_buy = bool(allow_buy.loc[dt]) if dt in allow_buy.index else False

        # 年末強制投入：若擇時策略全年未部署，最後一個交易日強制買入
        is_year_end = (allow_buy is not None
                       and dt == last_trading_days.get(yr)
                       and cash_reserve > 0)

        if can_buy or is_year_end:
            max_sh = int(cash_reserve / (price * (1 + COMMISSION_RATE)))
            if max_sh > 0:
                fee            = round(max_sh * price * COMMISSION_RATE, 0)
                cost           = max_sh * price + fee
                shares_held   += max_sh
                cash_reserve  -= cost
                invested_total += cost
                total_fees    += fee
                is_fallback    = is_year_end and not can_buy
                tx: dict = {
                    "date":     dt.date().isoformat(),
                    "price":    round(price, 2),
                    "shares":   max_sh,
                    "cost":     round(cost, 0),
                    "fallback": is_fallback,
                }
                if not is_fallback and indicator_series:
                    trigger = {}
                    for k, ser in indicator_series.items():
                        if isinstance(ser, pd.Series):
                            v = ser.loc[dt] if dt in ser.index else None
                            if v is not None:
                                if isinstance(v, float) and np.isnan(v):
                                    pass
                                elif isinstance(v, (int, float)):
                                    trigger[k] = round(float(v), 1)
                                else:
                                    trigger[k] = str(v)
                    if trigger:
                        tx["trigger"] = trigger
                transactions.append(tx)

    final_price    = float(close.iloc[-1])
    final_value    = shares_held * final_price + cash_reserve
    total_invested = invested_total + cash_reserve

    if total_invested == 0:
        return {"label": label, "error": "zero invested"}

    total_return_pct = (final_value - total_invested) / total_invested * 100
    n_years          = max(1, END_YEAR - START_YEAR)
    cagr             = ((final_value / total_invested) ** (1 / n_years) - 1) * 100

    # 最大回撤（持股市值序列）
    pv_list = []
    run_sh  = 0.0
    tx_iter = iter(transactions)
    next_tx = next(tx_iter, None)
    for dt in close.index:
        if next_tx and dt.date().isoformat() == next_tx["date"]:
            run_sh  += next_tx["shares"]
            next_tx  = next(tx_iter, None)
        pv_list.append(run_sh * float(close.loc[dt]))
    pv_s     = pd.Series(pv_list, index=close.index)
    roll_max = pv_s.cummax()
    drawdown = (pv_s - roll_max) / roll_max.replace(0, np.nan) * 100
    max_dd   = float(drawdown.min())

    # 年均殖利率估計（配息收入 / 實際注資 / 年數）
    ann_yield_pct = (div_received / invested_total / n_years * 100
                     if invested_total > 0 else 0.0)

    profit = final_value - total_invested
    return {
        "label":             label,
        "total_invested":    round(total_invested, 0),
        "final_value":       round(final_value, 0),
        "profit":            round(profit, 0),
        "total_return_pct":  round(total_return_pct, 2),
        "cagr_pct":          round(cagr, 2),
        "max_drawdown_pct":  round(max_dd, 2),
        "n_transactions":    len(transactions),
        "final_price":       round(final_price, 2),
        "total_fees":        round(total_fees, 0),
        "div_received":      round(div_received, 0),
        "div_shares":        div_shares,
        "ann_yield_pct":     round(ann_yield_pct, 2),
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

def _fetch_dca_data(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    回傳 (df_adj, df_raw)：
      df_adj  — 調整後收盤（用於指標計算：RSI / AVWAP / DD）
      df_raw  — 原始收盤 + Dividends 欄（用於投資組合模擬與成本計算）
    """
    ticker = yf.Ticker(symbol)
    kw = dict(start=f"{START_YEAR}-01-01", end=f"{END_YEAR}-12-31")
    df_adj = ticker.history(**kw, auto_adjust=True)
    df_raw = ticker.history(**kw, auto_adjust=False)
    for df in (df_adj, df_raw):
        if not df.empty:
            df.index = df.index.tz_convert(TZ)
    return df_adj, df_raw


def run_dca_backtest(symbol: str, name: str, cfg: dict,
                     twii_close: pd.Series, etf50_close: pd.Series) -> dict:
    print(f"  DCA 回測 {symbol} {name}...")
    df_adj, df_raw = _fetch_dca_data(symbol)
    if df_adj.empty or len(df_adj) < 500:
        print(f"    [!] 資料不足（{len(df_adj)} 天），跳過")
        return {"symbol": symbol, "name": name, "error": "資料不足"}

    # 調整後價格用於指標計算
    close_adj = df_adj["Close"].dropna()
    high_adj  = df_adj["High"].reindex(close_adj.index)
    low_adj   = df_adj["Low"].reindex(close_adj.index)
    vol_adj   = df_adj["Volume"].reindex(close_adj.index)

    # 原始價格 + 配息用於投資組合模擬
    close_raw = df_raw["Close"].dropna() if not df_raw.empty else close_adj.copy()
    close_raw = close_raw.reindex(close_adj.index, method="ffill")
    dividends = pd.Series(dtype=float)
    if not df_raw.empty and "Dividends" in df_raw.columns:
        dividends = df_raw["Dividends"][df_raw["Dividends"] > 0]

    stock_cfg = SIGNAL_CONFIG.get(symbol, _DEFAULT_CFG)

    # 預計算 v2 指標（使用調整後價格）
    rsi   = calc_rsi(close_adj, 14)
    avwap = calc_rolling_avwap(close_adj, high_adj, low_adj, vol_adj, lookback=60)
    dd    = calc_rolling_dd(close_adj, lookback=60)

    b1 = avwap * stock_cfg["b1"]
    b2 = avwap * stock_cfg["b2"]

    # 布林過濾 Series（基於調整後指標）
    buy_filter   = (dd <= -0.10) & (close_adj < b1) & (rsi <= stock_cfg["rsi_buy"])
    sbuy_filter  = (dd <= -0.20) & (close_adj < b2) & (rsi <= stock_cfg["rsi_sbuy"])

    # 市場模式過濾：WARN/RISK 時才允許加碼（逆向加碼策略）
    mode_series  = _build_market_mode_series(twii_close, etf50_close)
    mode_aligned = mode_series.reindex(close_adj.index, method="ffill").fillna("NORMAL")
    market_dip   = mode_aligned.isin(["WARN", "RISK"])

    # bnh_dca 旗標：超強趨勢股不做擇時，全部策略都用 B&H
    is_bnh_only = stock_cfg.get("bnh_dca", False)

    vs_b1 = ((close_adj / b1) - 1) * 100
    vs_b2 = ((close_adj / b2) - 1) * 100

    _dca_kw = dict(high=high_adj, low=low_adj, volume=vol_adj,
                   symbol=symbol, dividends=dividends)

    if is_bnh_only:
        strategies = [
            _run_dca(close_raw, **_dca_kw,
                     allow_buy=None, label="B&H DCA（趨勢股，無條件）"),
        ]
    else:
        strategies = [
            _run_dca(close_raw, **_dca_kw,
                     allow_buy=None, label="B&H DCA（無條件）"),
            _run_dca(close_raw, **_dca_kw,
                     allow_buy=buy_filter, label="v2 BUY DCA",
                     indicator_series={"DD%": dd * 100, "RSI": rsi, "vs_b1%": vs_b1}),
            _run_dca(close_raw, **_dca_kw,
                     allow_buy=sbuy_filter, label="v2 STRONG BUY DCA",
                     indicator_series={"DD%": dd * 100, "RSI": rsi, "vs_b2%": vs_b2}),
            _run_dca(close_raw, **_dca_kw,
                     allow_buy=market_dip, label="市場警戒逆向加碼",
                     indicator_series={"市場模式": mode_aligned, "DD%": dd * 100}),
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
