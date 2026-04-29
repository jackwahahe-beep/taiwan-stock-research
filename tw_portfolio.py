"""
持股追蹤模組
- 讀取 config.yaml portfolio 區塊
- 拉取即時收盤價，計算每筆持股 P&L
- 疊加技術信號，給出賣出 / 持有建議
- 產生 Discord embed
"""

import yfinance as yf
import numpy as np
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from tw_screener import load_config, fetch_data, calc_signals, calc_rsi

BASE_DIR = Path(__file__).parent
TZ = ZoneInfo("Asia/Taipei")


def fetch_latest_price(symbol: str) -> float | None:
    df = fetch_data(symbol, period="6mo")
    if df.empty:
        return None
    close = df["Close"].dropna()
    return float(close.iloc[-1]) if not close.empty else None


def calc_holding(holding: dict, price: float) -> dict:
    shares = holding["shares"]
    cost = holding["cost"]
    market_value = round(price * shares, 0)

    if cost > 0:
        pnl = round((price - cost) * shares, 0)
        pnl_pct = round((price - cost) / cost * 100, 2)
    else:
        # 成本為 0（配股）：以市值全算獲利
        pnl = market_value
        pnl_pct = None

    return {
        "symbol": holding["symbol"],
        "name": holding["name"],
        "shares": shares,
        "cost": cost,
        "price": round(price, 2),
        "market_value": int(market_value),
        "pnl": int(pnl),
        "pnl_pct": pnl_pct,
        "note": holding.get("note", ""),
    }


def get_sell_advice(holding_result: dict, cfg: dict) -> dict:
    """
    疊加技術信號，回傳建議動作與原因。
    - SELL_STRONG：技術信號 + 已有利潤，強烈建議賣出
    - SELL_WATCH：技術信號但虧損中，注意停損
    - HOLD：無明確賣出信號
    - EXIT：持股備注為「待機賣出」，等反彈賣出
    """
    symbol = holding_result["symbol"]
    note = holding_result["note"]
    pnl_pct = holding_result["pnl_pct"]

    # 標記為待機賣出
    if "待機賣出" in note:
        return {"action": "EXIT", "reasons": ["持股標記為待機賣出，逢反彈出場"]}

    # 拉技術信號
    try:
        df = fetch_data(symbol, period="6mo")
        sig = calc_signals(df, cfg) if not df.empty else {}
    except Exception:
        sig = {}

    sell_signals = [s for s in sig.get("signals", []) if s["type"] == "SELL"]
    watch_signals = [s for s in sig.get("signals", []) if s["type"] == "WATCH"]

    reasons = [s["reason"] for s in sell_signals]

    if sell_signals:
        if pnl_pct is not None and pnl_pct > 0:
            return {"action": "SELL_STRONG", "reasons": reasons, "signals": sig}
        else:
            reasons.append(f"目前虧損 {pnl_pct}%，技術面轉弱，考慮停損")
            return {"action": "SELL_WATCH", "reasons": reasons, "signals": sig}

    if watch_signals:
        return {"action": "WATCH", "reasons": [s["reason"] for s in watch_signals], "signals": sig}

    return {"action": "HOLD", "reasons": ["無明確賣出信號"], "signals": sig}


def run_portfolio_check() -> list[dict]:
    cfg = load_config()
    portfolio = cfg.get("portfolio", [])
    results = []

    for holding in portfolio:
        symbol = holding["symbol"]
        name = holding["name"]
        print(f"  持股 {symbol} {name}...")

        price = fetch_latest_price(symbol)
        if price is None:
            print(f"    [!] 無法取得價格，跳過")
            continue

        result = calc_holding(holding, price)
        advice = get_sell_advice(result, cfg)
        result["advice"] = advice

        pnl_str = f"{result['pnl']:+,}"
        pct_str = f"{result['pnl_pct']:+.2f}%" if result["pnl_pct"] is not None else "配股"
        print(f"    現價: {result['price']}  損益: {pnl_str} ({pct_str})  建議: {advice['action']}")
        results.append(result)

    return results


# ── Discord embed ────────────────────────────────────────────────────────────────

ACTION_COLOR = {
    "SELL_STRONG": 0xE74C3C,   # 紅
    "EXIT":        0xE67E22,   # 橘
    "SELL_WATCH":  0xF39C12,   # 黃
    "WATCH":       0xF1C40F,   # 淡黃
    "HOLD":        0x2ECC71,   # 綠
}

ACTION_LABEL = {
    "SELL_STRONG": "🔴 建議賣出",
    "EXIT":        "🟠 逢高出場",
    "SELL_WATCH":  "🟡 注意停損",
    "WATCH":       "🟡 觀察",
    "HOLD":        "🟢 持有",
}


def build_portfolio_embeds(results: list[dict]) -> list[dict]:
    embeds = []

    # 首張：持股總覽
    total_pnl = sum(r["pnl"] for r in results)
    total_value = sum(r["market_value"] for r in results)
    pnl_sign = "+" if total_pnl >= 0 else ""
    summary_lines = []
    for r in results:
        pct_str = f"{r['pnl_pct']:+.2f}%" if r["pnl_pct"] is not None else "配股"
        action = ACTION_LABEL.get(r["advice"]["action"], "")
        summary_lines.append(
            f"**{r['symbol']} {r['name']}** — `NT${r['price']}` {pct_str}　{action}"
        )

    embeds.append({
        "color": 0x3498DB,
        "title": f"💼 持股總覽｜{datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}",
        "description": "\n".join(summary_lines),
        "fields": [
            {"name": "總市值", "value": f"NT${total_value:,}", "inline": True},
            {"name": "未實現損益", "value": f"NT${pnl_sign}{total_pnl:,}", "inline": True},
        ],
    })

    # 每檔個別 embed（有操作建議才發）
    for r in results:
        action = r["advice"]["action"]
        if action == "HOLD":
            continue   # 無事就不多打擾

        color = ACTION_COLOR.get(action, 0x95A5A6)
        label = ACTION_LABEL.get(action, action)
        reasons = "\n".join(f"• {x}" for x in r["advice"]["reasons"])
        pct_str = f"{r['pnl_pct']:+.2f}%" if r["pnl_pct"] is not None else "配股成本"

        sig = r["advice"].get("signals", {})
        rsi_str = str(sig.get("rsi", "N/A"))

        embeds.append({
            "color": color,
            "title": f"{label}｜{r['symbol']} {r['name']}",
            "fields": [
                {"name": "現價", "value": f"NT${r['price']}", "inline": True},
                {"name": "持股損益", "value": f"NT${r['pnl']:+,}（{pct_str}）", "inline": True},
                {"name": "RSI", "value": rsi_str, "inline": True},
                {"name": "建議原因", "value": reasons, "inline": False},
            ],
            "footer": {"text": f"成本均價 NT${r['cost']} × {r['shares']} 股"},
        })

    return embeds


if __name__ == "__main__":
    print("=== 持股檢查 ===\n")
    results = run_portfolio_check()
    print(f"\n完成，共 {len(results)} 筆持股")
