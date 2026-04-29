"""
台股信號掃描器
- 從 yfinance 拉取台股/ETF 日線資料
- 計算 RSI、MA 交叉、成交量異常信號
- 回傳結構化信號清單
"""

import yfinance as yf
import pandas as pd
import numpy as np
import yaml
import json
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ = ZoneInfo("Asia/Taipei")


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_data(symbol: str, period: str = "3mo") -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, auto_adjust=True)
    if df.empty:
        return df
    df.index = df.index.tz_convert(TZ)
    return df


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_signals(df: pd.DataFrame, cfg: dict) -> dict:
    if len(df) < 60:
        return {}

    sig_cfg = cfg["signals"]
    close = df["Close"]
    volume = df["Volume"]

    rsi = calc_rsi(close, sig_cfg["rsi"]["period"])
    ma_fast = close.rolling(sig_cfg["ma"]["fast"]).mean()
    ma_slow = close.rolling(sig_cfg["ma"]["slow"]).mean()
    vol_ma20 = volume.rolling(20).mean()

    latest = {
        "price": round(close.iloc[-1], 2),
        "rsi": round(rsi.iloc[-1], 1),
        "ma_fast": round(ma_fast.iloc[-1], 2),
        "ma_slow": round(ma_slow.iloc[-1], 2),
        "volume": int(volume.iloc[-1]),
        "vol_ma20": int(vol_ma20.iloc[-1]),
        "signals": [],
    }

    # RSI 超賣
    if rsi.iloc[-1] < sig_cfg["rsi"]["oversold"]:
        latest["signals"].append({"type": "BUY", "reason": f"RSI {latest['rsi']} 低於 {sig_cfg['rsi']['oversold']} (超賣)"})

    # RSI 超買
    if rsi.iloc[-1] > sig_cfg["rsi"]["overbought"]:
        latest["signals"].append({"type": "SELL", "reason": f"RSI {latest['rsi']} 高於 {sig_cfg['rsi']['overbought']} (超買)"})

    # 均線黃金交叉（fast 由下穿上 slow）
    prev_cross = ma_fast.iloc[-2] - ma_slow.iloc[-2]
    curr_cross = ma_fast.iloc[-1] - ma_slow.iloc[-1]
    if prev_cross < 0 and curr_cross > 0:
        latest["signals"].append({"type": "BUY", "reason": f"MA{sig_cfg['ma']['fast']} 黃金交叉 MA{sig_cfg['ma']['slow']}"})

    # 均線死亡交叉
    if prev_cross > 0 and curr_cross < 0:
        latest["signals"].append({"type": "SELL", "reason": f"MA{sig_cfg['ma']['fast']} 死亡交叉 MA{sig_cfg['ma']['slow']}"})

    # 成交量爆量
    if volume.iloc[-1] > vol_ma20.iloc[-1] * sig_cfg["volume_spike"]:
        spike_ratio = round(volume.iloc[-1] / vol_ma20.iloc[-1], 1)
        latest["signals"].append({"type": "WATCH", "reason": f"成交量爆量 {spike_ratio}x 均量"})

    return latest


def run_scan() -> list[dict]:
    cfg = load_config()
    results = []

    all_stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]

    for stock in all_stocks:
        symbol = stock["symbol"]
        name = stock["name"]
        print(f"  掃描 {symbol} {name}...")

        try:
            df = fetch_data(symbol)
            if df.empty:
                print(f"    [!] 無資料，跳過")
                continue

            sig = calc_signals(df, cfg)
            if not sig:
                print(f"    [!] 資料不足，跳過")
                continue

            entry = {
                "symbol": symbol,
                "name": name,
                "date": date.today().isoformat(),
                **sig,
            }
            results.append(entry)

            signal_count = len(sig["signals"])
            print(f"    價格: {sig['price']}  RSI: {sig['rsi']}  信號: {signal_count} 個")

        except Exception as e:
            print(f"    [ERROR] {symbol}: {e}")

    # 快取結果
    cache_file = CACHE_DIR / f"scan_{date.today().isoformat()}.json"
    cache_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n結果已快取至 {cache_file}")

    return results


if __name__ == "__main__":
    print(f"=== 台股信號掃描 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} ===\n")
    results = run_scan()

    triggered = [r for r in results if r.get("signals")]
    print(f"\n觸發信號: {len(triggered)} 檔")
    for r in triggered:
        for s in r["signals"]:
            print(f"  [{s['type']}] {r['symbol']} {r['name']} — {s['reason']}")
