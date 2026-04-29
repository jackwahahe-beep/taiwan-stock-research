"""
Discord Webhook 推播模組
- 買入推播：非持股 BUY 信號 → 建議股數 + 成本 + 回測勝率
- 持股推播：只在有操作動作時發送（HOLD 靜默）
- 無信號通知：每日掃描完成摘要
"""

import os
import requests
import yaml
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
TZ = ZoneInfo("Asia/Taipei")
load_dotenv(BASE_DIR / ".env")

COLOR = {
    "BUY":    0x2ECC71,   # 綠
    "SELL":   0xE74C3C,   # 紅
    "WATCH":  0xF39C12,   # 黃
    "INFO":   0x3498DB,   # 藍
    "HOLD":   0x95A5A6,   # 灰
}


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


# ── 買入推播（非持股）────────────────────────────────────────────────────────────

def build_buy_embed(stock: dict, cfg: dict, bt_summary: dict | None = None) -> dict:
    """
    BUY 信號 embed：含建議股數、預估成本、回測勝率確認。
    只用於【未持有】的股票。
    """
    budget = cfg.get("trade_budget", 100000)
    price = stock["price"]
    suggested_shares = int(budget // price)
    estimated_cost = suggested_shares * price

    buy_reasons = [s["reason"] for s in stock["signals"] if s["type"] == "BUY"]
    watch_reasons = [s["reason"] for s in stock["signals"] if s["type"] == "WATCH"]
    reasons_text = "\n".join(f"• {r}" for r in buy_reasons)

    fields = [
        {"name": "現價", "value": f"NT${price}", "inline": True},
        {"name": "RSI", "value": str(stock["rsi"]), "inline": True},
        {"name": "MA20 / MA60", "value": f"{stock['ma_fast']} / {stock['ma_slow']}", "inline": True},
        {"name": "📌 建議進場", "value": f"掛單價 `NT${price}`　買 `{suggested_shares}` 股　預估成本 `NT${estimated_cost:,.0f}`", "inline": False},
        {"name": "觸發信號", "value": reasons_text, "inline": False},
    ]

    # 疊加回測數據
    if bt_summary:
        rsi_bt = next((s for s in bt_summary.get("strategies", []) if "RSI" in s["label"]), None)
        if rsi_bt and rsi_bt["trades"] > 0:
            wr = rsi_bt["win_rate"]
            tr = rsi_bt["total_return_pct"]
            bnh = bt_summary.get("bnh_return_pct", 0)
            beat = "✅ 優於B&H" if tr > bnh else "⚠️ 低於B&H"
            fields.append({
                "name": "📊 歷史回測（RSI策略，2年）",
                "value": (
                    f"總報酬 `{tr}%`　勝率 `{wr}%`　"
                    f"交易 `{rsi_bt['trades']}` 次　{beat}"
                ),
                "inline": False,
            })

    if watch_reasons:
        fields.append({"name": "⚠️ 附加注意", "value": "\n".join(f"• {r}" for r in watch_reasons), "inline": False})

    return {
        "color": COLOR["BUY"],
        "title": f"🟢 買入信號｜{stock['symbol']} {stock['name']}",
        "fields": fields,
        "footer": {"text": f"掃描時間 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} (台北)"},
    }


# ── 賣出推播（持股）─────────────────────────────────────────────────────────────

def build_sell_embed(stock: dict) -> dict:
    """SELL 信號 embed（用於持股出現賣出信號時）"""
    sell_reasons = [s["reason"] for s in stock["signals"] if s["type"] in ("SELL", "WATCH")]
    reasons_text = "\n".join(f"• {r}" for r in sell_reasons)

    return {
        "color": COLOR["SELL"],
        "title": f"🔴 賣出信號｜{stock['symbol']} {stock['name']}",
        "fields": [
            {"name": "現價", "value": f"NT${stock['price']}", "inline": True},
            {"name": "RSI", "value": str(stock["rsi"]), "inline": True},
            {"name": "MA20 / MA60", "value": f"{stock['ma_fast']} / {stock['ma_slow']}", "inline": True},
            {"name": "觸發信號", "value": reasons_text, "inline": False},
        ],
        "footer": {"text": f"掃描時間 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} (台北)"},
    }


# ── 主推播函數 ───────────────────────────────────────────────────────────────────

def send_scan_results(results: list[dict], bt_cache: dict | None = None) -> None:
    """
    分流推播：
    - 非持股有 BUY 信號 → 買入推播（含回測）
    - 持股有 SELL 信號 → 賣出推播
    - 無任何信號 → 單條每日摘要通知
    """
    cfg = load_config()
    webhook_url = cfg["discord"]["webhook_url"]
    portfolio_syms = _portfolio_symbols(cfg)

    triggered = [r for r in results if r.get("signals")]
    buy_embeds = []
    sell_embeds = []

    for r in triggered:
        types = {s["type"] for s in r["signals"]}
        in_portfolio = r["symbol"] in portfolio_syms

        if not in_portfolio and "BUY" in types:
            bt = (bt_cache or {}).get(r["symbol"])
            buy_embeds.append(build_buy_embed(r, cfg, bt))

        if in_portfolio and "SELL" in types:
            sell_embeds.append(build_sell_embed(r))

    # 發送買入推播
    for i in range(0, len(buy_embeds), 10):
        ok = send_webhook({"embeds": buy_embeds[i:i+10]}, webhook_url)
        print(f"[Discord] 買入推播 {len(buy_embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")

    # 發送賣出推播（持股）
    for i in range(0, len(sell_embeds), 10):
        ok = send_webhook({"embeds": sell_embeds[i:i+10]}, webhook_url)
        print(f"[Discord] 持股賣出推播 {len(sell_embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")

    # 無任何推播時發每日摘要（含市場溫度）
    if not buy_embeds and not sell_embeds:
        scanned = len(results)
        overbought = [r for r in results if r.get("rsi", 0) > 70]
        neutral = [r for r in results if 40 <= r.get("rsi", 0) <= 60]
        oversold = [r for r in results if 0 < r.get("rsi", 100) < 30]

        if overbought:
            temp_line = f"🌡️ 市場偏熱：{len(overbought)} 檔 RSI > 70（{', '.join(r['symbol'].replace('.TW','') for r in overbought[:5])}{'...' if len(overbought)>5 else ''}）"
        elif oversold:
            temp_line = f"❄️ 市場偏冷：{len(oversold)} 檔 RSI < 30，留意買入機會"
        else:
            temp_line = f"🟡 市場中性：多數個股 RSI 介於 40–60"

        watch_list = ", ".join(r["symbol"].replace(".TW", "") for r in neutral) if neutral else "無"

        payload = {
            "embeds": [{
                "color": COLOR["INFO"],
                "title": f"📊 台股每日掃描｜{datetime.now(TZ).strftime('%Y-%m-%d')}",
                "description": (
                    f"掃描 **{scanned}** 檔，今日無買入 / 持股賣出信號。\n\n"
                    f"{temp_line}\n"
                    f"📌 中性觀察區（RSI 40–60）：{watch_list}"
                ),
                "footer": {"text": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")},
            }]
        }
        send_webhook(payload, webhook_url)
        print("[Discord] 每日摘要推播")


if __name__ == "__main__":
    cfg = load_config()
    webhook_url = cfg["discord"]["webhook_url"]
    payload = {
        "embeds": [{
            "color": COLOR["INFO"],
            "title": "✅ 台股推播測試",
            "description": "Discord Webhook 連線正常！",
            "footer": {"text": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")},
        }]
    }
    ok = send_webhook(payload, webhook_url)
    print(f"測試推播：{'成功' if ok else '失敗'}")
