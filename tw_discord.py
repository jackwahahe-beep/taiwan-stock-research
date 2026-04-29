"""
Discord Webhook 推播模組 v2
- STRONG BUY / BUY 分級推播
- 市場模式警示（警戒/風險）
- 持股賣出含具體建議股數與回收金額
"""

import os
import requests
import yaml
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
TZ       = ZoneInfo("Asia/Taipei")
load_dotenv(BASE_DIR / ".env")

COLOR = {
    "STRONG BUY": 0x1ABC9C,  # 青綠
    "BUY":        0x2ECC71,  # 綠
    "SELL":       0xE74C3C,  # 紅
    "WATCH":      0xF39C12,  # 黃
    "INFO":       0x3498DB,  # 藍
    "HOLD":       0x95A5A6,  # 灰
    "WARN":       0xE67E22,  # 橙
    "RISK":       0x8E44AD,  # 紫
}

MODE_LABEL = {"NORMAL": "🟢 正常", "WARN": "🟡 警戒", "RISK": "🔴 風險"}


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    env_url = os.getenv("DISCORD_WEBHOOK_URL")
    if env_url:
        cfg["discord"]["webhook_url"] = env_url
    return cfg


def send_webhook(payload: dict, webhook_url: str) -> bool:
    resp = requests.post(webhook_url, json=payload, timeout=10)
    return resp.status_code in (200, 204)


def _portfolio_symbols(cfg: dict) -> set[str]:
    return {h["symbol"] for h in cfg.get("portfolio", [])}


# ── 買入推播 ────────────────────────────────────────────────────────────────────

def build_buy_embed(stock: dict, cfg: dict, bt_summary: dict | None = None) -> dict:
    budget   = cfg.get("trade_budget", 100000)
    price    = stock["price"]
    avwap    = stock.get("avwap", 0)
    dd_pct   = stock.get("dd_pct", 0)
    rsi      = stock["rsi"]
    market_mode = stock.get("market_mode", "NORMAL")

    buy_types = {s["type"] for s in stock["signals"] if s["type"] in ("BUY", "STRONG BUY")}
    is_strong = "STRONG BUY" in buy_types

    suggested_shares = int(budget // price)
    estimated_cost   = suggested_shares * price

    buy_reasons   = [s["reason"] for s in stock["signals"] if s["type"] in ("BUY", "STRONG BUY")]
    watch_reasons = [s["reason"] for s in stock["signals"] if s["type"] == "WATCH"]
    reasons_text  = "\n".join(f"• {r}" for r in buy_reasons)

    avwap_diff = f"{((price / avwap) - 1) * 100:+.1f}%" if avwap > 0 else "N/A"

    color = COLOR["STRONG BUY"] if is_strong else COLOR["BUY"]
    title_icon = "🟢🟢 強力買入信號" if is_strong else "🟢 買入信號"

    fields = [
        {"name": "現價",          "value": f"`NT${price}`", "inline": True},
        {"name": "RSI",           "value": f"`{rsi}`",      "inline": True},
        {"name": "DD / AVWAP距離","value": f"`{dd_pct}%` / `{avwap_diff}`", "inline": True},
        {"name": "📌 建議進場",
         "value": f"掛單價 `NT${price}`　買 `{suggested_shares}` 股　預估成本 `NT${estimated_cost:,.0f}`",
         "inline": False},
        {"name": "觸發信號",      "value": reasons_text,    "inline": False},
    ]

    # 市場模式警示
    if market_mode == "WARN":
        fields.append({"name": "⚠️ 市場警戒模式",
                        "value": "大盤偏弱，建議分批進場，勿一次全押",
                        "inline": False})
    elif market_mode == "RISK":
        fields.append({"name": "🔴 市場風險模式",
                        "value": "大盤趨勢向下，此信號為 STRONG BUY 才推播，嚴控倉位",
                        "inline": False})

    # 回測佐證
    if bt_summary:
        rsi_bt = next((s for s in bt_summary.get("strategies", []) if "RSI" in s["label"]), None)
        if rsi_bt and rsi_bt["trades"] > 0:
            wr  = rsi_bt["win_rate"]
            tr  = rsi_bt["total_return_pct"]
            bnh = bt_summary.get("bnh_return_pct", 0)
            beat = "✅ 優於B&H" if tr > bnh else "⚠️ 低於B&H"
            fields.append({
                "name":  "📊 歷史回測（RSI策略，2年）",
                "value": f"總報酬 `{tr}%`　勝率 `{wr}%`　交易 `{rsi_bt['trades']}` 次　{beat}",
                "inline": False,
            })

    if watch_reasons:
        fields.append({"name": "⚠️ 附加注意",
                        "value": "\n".join(f"• {r}" for r in watch_reasons),
                        "inline": False})

    return {
        "color":  color,
        "title":  f"{title_icon}｜{stock['symbol'].replace('.TW','')} {stock['name']}",
        "fields": fields,
        "footer": {"text": f"AVWAP NT${avwap}　掃描時間 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"},
    }


# ── 賣出推播（持股掃描觸發）────────────────────────────────────────────────────

def build_sell_embed(stock: dict) -> dict:
    sell_reasons = [s["reason"] for s in stock["signals"] if s["type"] in ("SELL", "WATCH")]
    reasons_text = "\n".join(f"• {r}" for r in sell_reasons)
    avwap = stock.get("avwap", 0)

    return {
        "color": COLOR["SELL"],
        "title": f"🔴 賣出信號｜{stock['symbol'].replace('.TW','')} {stock['name']}",
        "fields": [
            {"name": "現價",          "value": f"`NT${stock['price']}`", "inline": True},
            {"name": "RSI",           "value": f"`{stock['rsi']}`",      "inline": True},
            {"name": "DD / AVWAP距離",
             "value": f"`{stock.get('dd_pct',0)}%` / `{((stock['price']/avwap-1)*100):+.1f}%`" if avwap > 0 else "N/A",
             "inline": True},
            {"name": "觸發信號",      "value": reasons_text,             "inline": False},
        ],
        "footer": {"text": f"AVWAP NT${avwap}　{datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"},
    }


# ── 每日市場模式 header ─────────────────────────────────────────────────────────

def build_market_mode_embed(results: list[dict]) -> dict | None:
    if not results:
        return None
    mode   = results[0].get("market_mode", "NORMAL")
    label  = MODE_LABEL.get(mode, "🟢 正常")
    color  = COLOR.get(mode, COLOR["INFO"]) if mode != "NORMAL" else COLOR["INFO"]

    strong_buys = [r for r in results if any(s["type"] == "STRONG BUY" for s in r.get("signals", []))]
    buys        = [r for r in results if any(s["type"] == "BUY"         for s in r.get("signals", []))]
    sells       = [r for r in results if any(s["type"] == "SELL"        for s in r.get("signals", []))]

    lines = [f"市場模式：**{label}**\n"]
    if strong_buys:
        lines.append("🟢🟢 強力買入：" + "、".join(r["symbol"].replace(".TW","") for r in strong_buys))
    if buys:
        lines.append("🟢 買入：" + "、".join(r["symbol"].replace(".TW","") for r in buys))
    if sells:
        lines.append("🔴 賣出：" + "、".join(r["symbol"].replace(".TW","") for r in sells))
    if not strong_buys and not buys and not sells:
        lines.append("今日無明確買入/賣出信號")

    return {
        "color":       color,
        "title":       f"📊 台股每日掃描｜{datetime.now(TZ).strftime('%Y-%m-%d')}",
        "description": "\n".join(lines),
        "footer":      {"text": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")},
    }


# ── 主推播函數 ────────────────────────────────────────────────────────────────

def send_scan_results(results: list[dict], bt_cache: dict | None = None) -> None:
    cfg         = load_config()
    webhook_url = cfg["discord"]["webhook_url"]
    portfolio_syms = _portfolio_symbols(cfg)
    market_mode = results[0].get("market_mode", "NORMAL") if results else "NORMAL"

    buy_embeds  = []
    sell_embeds = []

    for r in results:
        if not r.get("signals"):
            continue
        types       = {s["type"] for s in r["signals"]}
        in_portfolio = r["symbol"] in portfolio_syms

        # 買入推播（非持股）
        if not in_portfolio and types & {"BUY", "STRONG BUY"}:
            # 風險模式：只推 STRONG BUY
            if market_mode == "RISK" and "STRONG BUY" not in types:
                continue
            bt = (bt_cache or {}).get(r["symbol"])
            buy_embeds.append(build_buy_embed(r, cfg, bt))

        # 賣出推播（持股）
        if in_portfolio and "SELL" in types:
            sell_embeds.append(build_sell_embed(r))

    # 先發市場模式 header
    header = build_market_mode_embed(results)
    if header:
        send_webhook({"embeds": [header]}, webhook_url)

    for i in range(0, len(buy_embeds), 10):
        ok = send_webhook({"embeds": buy_embeds[i:i+10]}, webhook_url)
        print(f"[Discord] 買入推播 {len(buy_embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")

    for i in range(0, len(sell_embeds), 10):
        ok = send_webhook({"embeds": sell_embeds[i:i+10]}, webhook_url)
        print(f"[Discord] 持股賣出推播 {len(sell_embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")

    if not buy_embeds and not sell_embeds:
        # 補充市場溫度
        overbought = [r for r in results if r.get("rsi", 0) > 70]
        oversold   = [r for r in results if 0 < r.get("rsi", 100) < 35]

        if overbought:
            temp = f"🌡️ 偏熱：{len(overbought)} 檔 RSI > 70"
        elif oversold:
            temp = f"❄️ 偏冷：{len(oversold)} 檔 RSI < 35，留意機會"
        else:
            temp = "🟡 中性：多數個股 RSI 40–60"

        payload = {"embeds": [{
            "color":       COLOR["INFO"],
            "title":       f"📊 台股每日掃描｜{datetime.now(TZ).strftime('%Y-%m-%d')}",
            "description": f"掃描 **{len(results)}** 檔，無買入/賣出信號。\n\n{temp}",
            "footer":      {"text": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")},
        }]}
        send_webhook(payload, webhook_url)
        print("[Discord] 每日摘要推播")


if __name__ == "__main__":
    cfg = load_config()
    ok  = send_webhook({"embeds": [{"color": COLOR["INFO"],
                                     "title": "✅ 台股推播測試 v2",
                                     "description": "Discord Webhook 連線正常",
                                     "footer": {"text": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")}}]},
                        cfg["discord"]["webhook_url"])
    print(f"測試推播：{'成功' if ok else '失敗'}")
