"""
持股追蹤模組
- HOLD → 靜默（不推播）
- EXIT（待機賣出）→ 偵測反彈條件才推播
- SELL_STRONG / SELL_WATCH → 立即推播
"""

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


def _detect_bounce(symbol: str, cfg: dict) -> tuple[bool, list[str]]:
    """
    偵測反彈信號，用於「待機賣出」股票（v2）。
    使用個股 SIGNAL_CONFIG 的 rsi_sbuy 閾值，並加入 AVWAP 回升確認。
    """
    try:
        from tw_screener import SIGNAL_CONFIG, _DEFAULT_CFG, calc_avwap
        df = fetch_data(symbol, period="6mo")
        if df.empty or len(df) < 20:
            return False, []

        stock_cfg = SIGNAL_CONFIG.get(symbol, _DEFAULT_CFG)
        rsi_sbuy  = stock_cfg["rsi_sbuy"]

        close = df["Close"].dropna()
        rsi   = calc_rsi(close)
        ma5   = close.rolling(5).mean()
        ma20  = close.rolling(20).mean()
        avwap = calc_avwap(df, lookback=60)

        price_now = float(close.iloc[-1])
        rsi_now   = float(rsi.iloc[-1])

        reasons = []

        # 1. RSI 從個股超賣閾值反彈（v2：用個股 rsi_sbuy 取代硬碼 30）
        rsi_prev = rsi.iloc[-6:-1]
        if any(v <= rsi_sbuy for v in rsi_prev) and rsi_now > rsi_sbuy + 10:
            reasons.append(f"RSI 由超賣（≤{rsi_sbuy}）反彈至 {round(rsi_now, 1)}")

        # 2. MA5 黃金交叉 MA20
        if (ma5.iloc[-2] <= ma20.iloc[-2]) and (ma5.iloc[-1] > ma20.iloc[-1]):
            reasons.append("MA5 黃金交叉 MA20，短線動能回升")

        # 3. 近 5 日連漲
        recent = close.iloc[-5:]
        if all(recent.iloc[i] < recent.iloc[i+1] for i in range(len(recent)-1)):
            reasons.append("近 5 日連續收紅，逢高減碼機會")

        # 4. v2 新增：價格從 AVWAP 以下回升至 AVWAP 附近（折價→均價）
        if avwap > 0 and len(close) >= 6:
            price_5ago = float(close.iloc[-6])
            if price_5ago < avwap * 0.97 and price_now >= avwap * 0.97:
                reasons.append(f"價格回升至 AVWAP 附近（NT${round(avwap, 1)}），反彈確認")

        return len(reasons) > 0, reasons
    except Exception:
        return False, []


def get_sell_advice(holding_result: dict, cfg: dict) -> dict:
    symbol = holding_result["symbol"]
    note = holding_result["note"]
    pnl_pct = holding_result["pnl_pct"]

    # 待機賣出：只在偵測到反彈時才推播
    if "待機賣出" in note:
        is_bounce, bounce_reasons = _detect_bounce(symbol, cfg)
        if is_bounce:
            return {"action": "EXIT_BOUNCE", "reasons": bounce_reasons, "push": True}
        return {"action": "EXIT_WAIT", "reasons": ["等待反彈機會，目前無明確出場信號"], "push": False}

    # 拉技術信號
    try:
        df = fetch_data(symbol, period="6mo")
        sig = calc_signals(df, cfg, symbol=symbol) if not df.empty else {}
    except Exception:
        sig = {}

    sell_signals = [s for s in sig.get("signals", []) if s["type"] == "SELL"]
    watch_signals = [s for s in sig.get("signals", []) if s["type"] == "WATCH"]
    reasons = [s["reason"] for s in sell_signals]

    if sell_signals:
        if pnl_pct is not None and pnl_pct > 5:
            return {"action": "SELL_STRONG", "reasons": reasons, "signals": sig, "push": True}
        elif pnl_pct is not None and pnl_pct < -10:
            reasons.append(f"虧損 {pnl_pct}%，技術面轉弱，考慮停損")
            return {"action": "SELL_WATCH", "reasons": reasons, "signals": sig, "push": True}
        else:
            return {"action": "SELL_MONITOR", "reasons": reasons, "signals": sig, "push": False}

    if watch_signals:
        return {"action": "WATCH", "reasons": [s["reason"] for s in watch_signals], "signals": sig, "push": False}

    return {"action": "HOLD", "reasons": [], "signals": sig, "push": False}


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
        push_flag = "📢" if advice["push"] else "🔇"
        print(f"    現價: {result['price']}  損益: {pnl_str} ({pct_str})  建議: {advice['action']} {push_flag}")
        results.append(result)

    return results


# ── 賣出建議計算 ─────────────────────────────────────────────────────────────────

def _calc_sell_rec(result: dict, action: str) -> dict:
    """
    根據 action 給出具體賣出建議：
    - SELL_STRONG  → 全賣（獲利出場）
    - EXIT_BOUNCE  → 減碼一半（保留另半等更高點）
    - SELL_WATCH   → 全賣停損，附帶 -10% 停損參考價
    - 其他         → 不操作
    """
    shares = result["shares"]
    cost   = result["cost"]
    price  = result["price"]

    if action == "SELL_STRONG":
        sell_shares = shares
        suggestion  = "🔴 建議全部賣出（獲利了結）"

    elif action == "EXIT_BOUNCE":
        sell_shares = max(1, shares // 2)
        suggestion  = "🟠 建議先出一半（保留另半等反彈高點再出）"

    elif action == "SELL_WATCH":
        sell_shares = shares
        stop_ref    = round(cost * 0.9, 2) if cost > 0 else price
        suggestion  = f"🟡 建議全出停損（成本 -10% 參考價 NT${stop_ref}）"

    else:
        sell_shares = 0
        suggestion  = "⏳ 暫不操作，持續觀察"

    proceeds     = round(sell_shares * price, 0)
    realized_pnl = round((price - cost) * sell_shares, 0) if cost > 0 else int(proceeds)

    return {
        "sell_shares":  sell_shares,
        "sell_price":   price,
        "proceeds":     int(proceeds),
        "realized_pnl": int(realized_pnl),
        "suggestion":   suggestion,
    }


# ── Discord embeds ───────────────────────────────────────────────────────────────

ACTION_COLOR = {
    "SELL_STRONG":  0xE74C3C,
    "EXIT_BOUNCE":  0xE67E22,
    "SELL_WATCH":   0xF39C12,
    "SELL_MONITOR": 0xF1C40F,
    "HOLD":         0x2ECC71,
    "EXIT_WAIT":    0x95A5A6,
    "WATCH":        0xF1C40F,
}

ACTION_LABEL = {
    "SELL_STRONG":  "🔴 建議賣出（獲利出場）",
    "EXIT_BOUNCE":  "🟠 反彈機會，逢高出場",
    "SELL_WATCH":   "🟡 注意停損",
    "SELL_MONITOR": "👀 技術轉弱，持續觀察",
    "HOLD":         "🟢 續持",
    "EXIT_WAIT":    "⏳ 待機賣出，等待中",
    "WATCH":        "🟡 量能異常，注意",
}


def build_portfolio_embeds(results: list[dict]) -> list[dict]:
    embeds = []

    # 持股總覽
    total_pnl = sum(r["pnl"] for r in results)
    total_value = sum(r["market_value"] for r in results)
    summary_lines = []
    for r in results:
        pct_str = f"{r['pnl_pct']:+.2f}%" if r["pnl_pct"] is not None else "配股"
        label = ACTION_LABEL.get(r["advice"]["action"], "")
        summary_lines.append(f"**{r['symbol']} {r['name']}** `NT${r['price']}` {pct_str}　{label}")

    pnl_sign = "+" if total_pnl >= 0 else ""
    embeds.append({
        "color": 0x3498DB,
        "title": f"💼 持股總覽｜{datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}",
        "description": "\n".join(summary_lines),
        "fields": [
            {"name": "總市值", "value": f"NT${total_value:,}", "inline": True},
            {"name": "未實現損益", "value": f"NT${pnl_sign}{total_pnl:,}", "inline": True},
        ],
    })

    # 個別 embed：只有 push=True 的才發
    for r in results:
        advice = r["advice"]
        if not advice.get("push", False):
            continue

        action = advice["action"]
        color = ACTION_COLOR.get(action, 0x95A5A6)
        label = ACTION_LABEL.get(action, action)
        reasons_text = "\n".join(f"• {x}" for x in advice["reasons"])
        pct_str = f"{r['pnl_pct']:+.2f}%" if r["pnl_pct"] is not None else "配股"

        sig = advice.get("signals", {})
        rsi_str = str(sig.get("rsi", "N/A")) if sig else "N/A"

        rec = _calc_sell_rec(r, action)
        pnl_sign = "+" if rec["realized_pnl"] >= 0 else ""

        if rec["sell_shares"] > 0:
            sell_detail = (
                f"{rec['suggestion']}\n"
                f"賣出 `{rec['sell_shares']}` 股 @ `NT${rec['sell_price']}`　"
                f"預估回收 `NT${rec['proceeds']:,}`　"
                f"實現損益 `NT${pnl_sign}{rec['realized_pnl']:,}`"
            )
        else:
            sell_detail = rec["suggestion"]

        embeds.append({
            "color": color,
            "title": f"{label}｜{r['symbol']} {r['name']}",
            "fields": [
                {"name": "現價",     "value": f"NT${r['price']}", "inline": True},
                {"name": "持股損益", "value": f"NT${r['pnl']:+,}（{pct_str}）", "inline": True},
                {"name": "RSI",      "value": rsi_str, "inline": True},
                {"name": "操作依據", "value": reasons_text, "inline": False},
                {"name": "📌 賣出建議", "value": sell_detail, "inline": False},
            ],
            "footer": {"text": f"成本 NT${r['cost']} × {r['shares']} 股"},
        })

    return embeds


if __name__ == "__main__":
    print("=== 持股檢查 ===\n")
    results = run_portfolio_check()
    actionable = [r for r in results if r["advice"].get("push")]
    print(f"\n完成，共 {len(results)} 筆持股，{len(actionable)} 筆需操作")
