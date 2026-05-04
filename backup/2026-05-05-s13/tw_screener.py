"""
台股信號掃描器 v2
- AVWAP（從近期低點錨定的成交量加權均價）作為核心買賣錨點
- 市場模式（^TWII vs MA200 + 0050波動率）→ 正常/警戒/風險
- 個股化 RSI 閾值（高成長股用50，高息ETF用40）
- 信號分級：STRONG BUY / BUY / SELL / WATCH
"""

import yfinance as yf
import pandas as pd
import numpy as np
import yaml
import json
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR  = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ        = ZoneInfo("Asia/Taipei")

# ── 個股信號參數（RSI買賣閾值、AVWAP倍率）─────────────────────────────────────
# rsi_buy  : 一般買入（DD>10% + 價格<AVWAP*b1）
# rsi_sbuy : 強力買入（DD>20% + 價格<AVWAP*b2）
# rsi_sell : 賣出（price>AVWAP*s + price>MA20*1.15）
# b1/b2/s  : AVWAP 乘數（試買/強買/賣出觸發）

SIGNAL_CONFIG = {
    # ETF — 高息低波，RSI要夠低才進
    "0050.TW":   {"rsi_buy": 45, "rsi_sbuy": 35, "rsi_sell": 70, "b1": 0.98, "b2": 0.93, "s": 1.12},
    "006208.TW": {"rsi_buy": 45, "rsi_sbuy": 35, "rsi_sell": 70, "b1": 0.98, "b2": 0.93, "s": 1.12},
    "00878.TW":  {"rsi_buy": 42, "rsi_sbuy": 32, "rsi_sell": 65, "b1": 0.97, "b2": 0.92, "s": 1.10},
    "00713.TW":  {"rsi_buy": 42, "rsi_sbuy": 32, "rsi_sell": 65, "b1": 0.97, "b2": 0.92, "s": 1.10},
    "00929.TW":  {"rsi_buy": 45, "rsi_sbuy": 35, "rsi_sell": 68, "b1": 0.98, "b2": 0.93, "s": 1.10},
    "00919.TW":  {"rsi_buy": 50, "rsi_sbuy": 40, "rsi_sell": 68, "b1": 0.98, "b2": 0.93, "s": 1.10},
    # 大型科技 — 強者恆強，RSI 50 以下才算便宜
    "2330.TW":   {"rsi_buy": 50, "rsi_sbuy": 40, "rsi_sell": 75, "b1": 0.97, "b2": 0.91, "s": 1.15},
    "2454.TW":   {"rsi_buy": 50, "rsi_sbuy": 40, "rsi_sell": 75, "b1": 0.97, "b2": 0.91, "s": 1.15},
    "2382.TW":   {"rsi_buy": 50, "rsi_sbuy": 40, "rsi_sell": 72, "b1": 0.97, "b2": 0.92, "s": 1.13},
    "2308.TW":   {"rsi_buy": 50, "rsi_sbuy": 40, "rsi_sell": 75, "b1": 0.97, "b2": 0.91, "s": 1.15},
    # 中型科技 — 波動較大，閾值稍放寬
    "3711.TW":   {"rsi_buy": 48, "rsi_sbuy": 38, "rsi_sell": 72, "b1": 0.97, "b2": 0.91, "s": 1.13},
    "2303.TW":   {"rsi_buy": 45, "rsi_sbuy": 45, "rsi_sell": 70, "b1": 0.97, "b2": 0.97, "s": 1.12},
    "3037.TW":   {"rsi_buy": 48, "rsi_sbuy": 38, "rsi_sell": 72, "b1": 0.97, "b2": 0.91, "s": 1.13, "bnh_dca": True},
    "2408.TW":   {"rsi_buy": 45, "rsi_sbuy": 35, "rsi_sell": 70, "b1": 0.97, "b2": 0.91, "s": 1.12},
    "6770.TW":   {"rsi_buy": 45, "rsi_sbuy": 35, "rsi_sell": 70, "b1": 0.97, "b2": 0.91, "s": 1.12},
    # 防禦型消費 — 統一超（7-Eleven），低波動，RSI 門檻放寬
    "2912.TW":   {"rsi_buy": 42, "rsi_sbuy": 32, "rsi_sell": 72, "b1": 0.97, "b2": 0.92, "s": 1.10},
}
_DEFAULT_CFG = {"rsi_buy": 45, "rsi_sbuy": 35, "rsi_sell": 70, "b1": 0.97, "b2": 0.92, "s": 1.12}

# 板塊分類（用於相關性集中警告）：同板塊 ≥2 檔同時出現 BUY/STRONG BUY → UI 顯示警告
SECTOR = {
    "半導體":   ["2330.TW", "2454.TW", "2303.TW", "3711.TW"],  # 台積電/聯發科/聯電/日月光
    "AI供應鏈": ["2382.TW", "2308.TW"],                         # 廣達/台達電
    "高息ETF":  ["00878.TW", "00713.TW", "00929.TW", "00919.TW"],  # 四檔高息 ETF
}

# 市場模式乘數：風險期只推 STRONG BUY，警戒期忽略普通 BUY
MARKET_MULTIPLIER = {"NORMAL": 1.0, "WARN": 0.7, "RISK": 0.4}


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_data(symbol: str, period: str = "6mo") -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, auto_adjust=True)
    if df.empty:
        return df
    df.index = df.index.tz_convert(TZ)
    return df


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── AVWAP（從近期低點錨定）─────────────────────────────────────────────────────

def calc_avwap(df: pd.DataFrame, lookback: int = 60) -> float:
    """
    Anchored VWAP：取最近 lookback 天的最低收盤點為錨點，
    從該點到現在計算成交量加權均價。
    """
    close  = df["Close"].dropna()
    volume = df["Volume"]
    high   = df["High"]
    low    = df["Low"]

    if len(close) < 10:
        return float(close.iloc[-1])

    window = min(lookback, len(close))
    win_close = close.iloc[-window:]
    min_rel   = int(win_close.values.argmin())

    # 確認從低點已反彈 5%（避免仍在下跌中）
    current   = float(close.iloc[-1])
    min_price = float(win_close.iloc[min_rel])
    confirmed = current >= min_price * 1.05
    anchor_i  = (len(close) - window + min_rel) if confirmed else (len(close) - window)

    tp  = (high.iloc[anchor_i:] + low.iloc[anchor_i:] + close.iloc[anchor_i:]) / 3
    vol = volume.iloc[anchor_i:]
    vol_sum = vol.sum()
    if vol_sum <= 0:
        return current
    return float((tp * vol).sum() / vol_sum)


# ── 回撤（從近期高點）─────────────────────────────────────────────────────────

def calc_drawdown(close: pd.Series, lookback: int = 60) -> float:
    """從近 lookback 天高點的跌幅（負值，例如 -0.15 代表跌 15%）"""
    window  = close.iloc[-lookback:] if len(close) >= lookback else close
    peak    = float(window.max())
    current = float(close.iloc[-1])
    if peak <= 0:
        return 0.0
    return (current - peak) / peak


# ── 市場模式（台灣大盤）─────────────────────────────────────────────────────────

_market_mode_cache: dict = {}

def get_market_mode() -> tuple[str, dict]:
    """
    用 ^TWII（加權指數）vs MA200 判斷台股大盤狀態。
    同時用 0050 近 20 日年化波動率作為恐慌度指標（替代 VIX）。
    回傳 (mode, detail_dict)
    """
    global _market_mode_cache
    today = date.today().isoformat()
    if _market_mode_cache.get("date") == today:
        return _market_mode_cache["mode"], _market_mode_cache["detail"]

    try:
        twii = yf.Ticker("^TWII").history(period="1y", auto_adjust=True)
        if twii.empty or len(twii) < 200:
            return "NORMAL", {"reason": "^TWII 資料不足，預設正常"}
        twii_close = twii["Close"].dropna()
        twii_price = float(twii_close.iloc[-1])
        twii_ma200 = float(twii_close.rolling(200).mean().iloc[-1])

        # 0050 波動率（代替 VIX）
        etf50  = yf.Ticker("0050.TW").history(period="3mo", auto_adjust=True)
        vol_20 = 0.0
        if not etf50.empty:
            log_ret = np.log(etf50["Close"] / etf50["Close"].shift(1)).dropna()
            vol_20  = float(log_ret.iloc[-20:].std() * np.sqrt(252) * 100)  # 年化%

        twii_vs_ma200 = (twii_price - twii_ma200) / twii_ma200 * 100

        # 判斷條件
        bear_count = sum([
            twii_vs_ma200 < -2,   # 大盤跌破MA200超過2%
            twii_vs_ma200 < 0,    # 大盤在MA200以下
            vol_20 > 25,          # 年化波動率 >25%（相當於VIX偏高）
            vol_20 > 20,          # 年化波動率 >20%
        ])

        # RISK：大盤確實跌破MA200才觸發（高波動率只在跌破時加重判定）
        if twii_vs_ma200 < -5 or (twii_vs_ma200 < 0 and vol_20 > 25):
            mode = "RISK"
        # WARN：大盤在MA200附近（±2%）或波動率明顯偏高且大盤偏弱
        elif twii_vs_ma200 < 2 or (vol_20 > 30 and twii_vs_ma200 < 10):
            mode = "WARN"
        else:
            mode = "NORMAL"

        detail = {
            "twii_price": round(twii_price, 0),
            "twii_ma200": round(twii_ma200, 0),
            "twii_vs_ma200_pct": round(twii_vs_ma200, 2),
            "vol_20_annualized": round(vol_20, 1),
            "mode": mode,
        }
        _market_mode_cache = {"date": today, "mode": mode, "detail": detail}
        return mode, detail

    except Exception as e:
        return "NORMAL", {"reason": f"市場模式計算失敗: {e}"}


# ── 核心信號計算（v2）─────────────────────────────────────────────────────────

def calc_signals(df: pd.DataFrame, cfg: dict, symbol: str = "") -> dict:
    if len(df) < 60:
        return {}

    sig_cfg   = cfg["signals"]
    stock_cfg = SIGNAL_CONFIG.get(symbol, _DEFAULT_CFG)

    close  = df["Close"].dropna()
    volume = df["Volume"]

    if close.empty:
        return {}

    rsi     = calc_rsi(close, sig_cfg["rsi"]["period"])
    ma_fast = close.rolling(sig_cfg["ma"]["fast"]).mean()
    ma_slow = close.rolling(sig_cfg["ma"]["slow"]).mean()
    ma20    = close.rolling(20).mean()
    vol_ma20 = volume.rolling(20).mean()

    # 對齊索引
    rsi      = rsi.reindex(close.index)
    ma_fast  = ma_fast.reindex(close.index)
    ma_slow  = ma_slow.reindex(close.index)
    ma20     = ma20.reindex(close.index)
    volume   = volume.reindex(close.index)
    vol_ma20 = vol_ma20.reindex(close.index)

    price_val = float(close.iloc[-1])
    rsi_val   = float(rsi.iloc[-1])
    if np.isnan(price_val) or np.isnan(rsi_val):
        return {}

    # 週線 RSI（將日線收盤重採樣至週線後計算）
    weekly_rsi_val: float | None = None
    try:
        weekly_close = close.resample("W").last().dropna()
        if len(weekly_close) >= 15:
            w_rsi = calc_rsi(weekly_close, 14)
            v = float(w_rsi.iloc[-1])
            if not np.isnan(v):
                weekly_rsi_val = round(v, 1)
    except Exception:
        pass

    # AVWAP + DD
    avwap = calc_avwap(df, lookback=60)
    dd    = calc_drawdown(close, lookback=60)

    b1 = avwap * stock_cfg["b1"]
    b2 = avwap * stock_cfg["b2"]
    s  = avwap * stock_cfg["s"]

    latest = {
        "price":      round(price_val, 2),
        "rsi":        round(rsi_val, 1),
        "weekly_rsi": weekly_rsi_val,
        "ma_fast":    round(float(ma_fast.iloc[-1]), 2),
        "ma_slow":    round(float(ma_slow.iloc[-1]), 2),
        "avwap":      round(avwap, 2),
        "dd_pct":     round(dd * 100, 1),
        "volume":     int(volume.iloc[-1]),
        "vol_ma20":   int(vol_ma20.iloc[-1]),
        "signals":    [],
    }

    # ── 買入信號 ──────────────────────────────────────────────────────────────

    # STRONG BUY：大幅回撤 + 深度超賣 + 價格遠低於AVWAP
    if dd <= -0.20 and price_val < b2 and rsi_val <= stock_cfg["rsi_sbuy"]:
        latest["signals"].append({
            "type": "STRONG BUY",
            "reason": f"強力買入：回撤 {dd*100:.1f}%，RSI {rsi_val:.0f}，價格低於AVWAP {((price_val/avwap)-1)*100:.1f}%",
        })

    # BUY：適度回撤 + RSI未過熱 + 價格在AVWAP以下
    elif dd <= -0.10 and price_val < b1 and rsi_val <= stock_cfg["rsi_buy"]:
        latest["signals"].append({
            "type": "BUY",
            "reason": f"買入：回撤 {dd*100:.1f}%，RSI {rsi_val:.0f}，低於AVWAP {((price_val/avwap)-1)*100:.1f}%",
        })

    # 輔助：MA黃金交叉（保留）
    prev_cross = float(ma_fast.iloc[-2]) - float(ma_slow.iloc[-2])
    curr_cross = float(ma_fast.iloc[-1]) - float(ma_slow.iloc[-1])
    if prev_cross < 0 and curr_cross > 0:
        sig_type = "BUY" if not any(s["type"] in ("BUY", "STRONG BUY") for s in latest["signals"]) else "WATCH"
        latest["signals"].append({
            "type": sig_type,
            "reason": f"MA{sig_cfg['ma']['fast']} 黃金交叉 MA{sig_cfg['ma']['slow']}",
        })

    # ── 賣出信號 ──────────────────────────────────────────────────────────────

    ma20_val = float(ma20.iloc[-1])
    sell_conditions = [
        rsi_val >= stock_cfg["rsi_sell"],
        price_val >= s,
        not np.isnan(ma20_val) and price_val > ma20_val * 1.15,
    ]

    if all(sell_conditions):
        latest["signals"].append({
            "type": "SELL",
            "reason": (
                f"賣出：RSI {rsi_val:.0f} 過熱，"
                f"價格超過AVWAP目標 {((price_val/s)-1)*100:.1f}%，"
                f"高於MA20 {((price_val/ma20_val)-1)*100:.1f}%"
            ),
        })
    elif prev_cross > 0 and curr_cross < 0:
        latest["signals"].append({
            "type": "SELL",
            "reason": f"MA{sig_cfg['ma']['fast']} 死亡交叉 MA{sig_cfg['ma']['slow']}",
        })

    # ── 成交量爆量提醒 ────────────────────────────────────────────────────────

    vol_spike = sig_cfg["volume_spike"]
    if volume.iloc[-1] > vol_ma20.iloc[-1] * vol_spike:
        spike_ratio = round(volume.iloc[-1] / vol_ma20.iloc[-1], 1)
        latest["signals"].append({
            "type": "WATCH",
            "reason": f"成交量爆量 {spike_ratio}x 均量",
        })

    # ── 週線 RSI 偏高警示（買入信號時提醒逆勢風險）─────────────────────────
    if (weekly_rsi_val is not None and weekly_rsi_val > 65
            and any(s["type"] in ("BUY", "STRONG BUY") for s in latest["signals"])):
        latest["signals"].append({
            "type": "WATCH",
            "reason": f"週線RSI {weekly_rsi_val:.0f} 偏高，日線為逆勢超賣反彈，建議謹慎",
        })

    return latest


# ── 掃描主函數 ────────────────────────────────────────────────────────────────

def run_scan() -> list[dict]:
    cfg = load_config()

    # 先取得市場模式
    market_mode, market_detail = get_market_mode()
    mode_label = {"NORMAL": "正常", "WARN": "警戒", "RISK": "風險"}.get(market_mode, "正常")
    print(f"\n市場模式：{mode_label}（大盤 {market_detail.get('twii_vs_ma200_pct', 'N/A')}% vs MA200，波動率 {market_detail.get('vol_20_annualized', 'N/A')}%）")

    results     = []
    all_stocks  = [s for s in cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]
                   if not s.get("backtest_only", False)]

    for stock in all_stocks:
        symbol = stock["symbol"]
        name   = stock["name"]
        print(f"  掃描 {symbol} {name}...")

        try:
            df = fetch_data(symbol, period="6mo")
            if df.empty:
                print(f"    [!] 無資料，跳過")
                continue

            sig = calc_signals(df, cfg, symbol=symbol)
            if not sig:
                print(f"    [!] 資料不足或無效，跳過")
                continue

            # 市場風險過濾：RISK模式只保留 STRONG BUY
            filtered_signals = []
            for s in sig["signals"]:
                if market_mode == "RISK" and s["type"] == "BUY":
                    s = {**s, "type": "WATCH", "reason": f"[風險模式降級] {s['reason']}"}
                elif market_mode == "WARN" and s["type"] == "BUY":
                    pass  # WARN模式仍推播，但Discord embed會加警示
                filtered_signals.append(s)
            sig["signals"] = filtered_signals
            sig["market_mode"] = market_mode

            entry = {
                "symbol": symbol,
                "name":   name,
                "date":   date.today().isoformat(),
                **sig,
            }
            results.append(entry)

            sig_summary = ", ".join(f"[{s['type']}]" for s in sig["signals"]) or "無信號"
            print(f"    價格:{sig['price']}  RSI:{sig['rsi']}  DD:{sig['dd_pct']}%  AVWAP:{sig['avwap']}  {sig_summary}")

        except Exception as e:
            print(f"    [ERROR] {symbol}: {e}")

    # 快取
    cache_file = CACHE_DIR / f"scan_{date.today().isoformat()}.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n結果已快取至 {cache_file}")
    return results


if __name__ == "__main__":
    print(f"=== 台股信號掃描 v2  {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} ===\n")
    results = run_scan()
    triggered = [r for r in results if r.get("signals")]
    print(f"\n觸發信號: {len(triggered)} 檔")
    for r in triggered:
        for s in r["signals"]:
            print(f"  [{s['type']}] {r['symbol']} {r['name']} — {s['reason']}")
