"""
Discord Webhook 推播模組
- 將信號掃描結果格式化成 Discord embed
- 依信號類型配色：BUY=綠、SELL=紅、WATCH=黃
"""

import requests
import yaml
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
TZ = ZoneInfo("Asia/Taipei")

COLOR = {
    "BUY": 0x2ECC71,    # 綠
    "SELL": 0xE74C3C,   # 紅
    "WATCH": 0xF39C12,  # 黃
    "INFO": 0x3498DB,   # 藍
}


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def send_webhook(payload: dict, webhook_url: str) -> bool:
    resp = requests.post(webhook_url, json=payload, timeout=10)
    return resp.status_code in (200, 204)


def build_signal_embed(stock: dict) -> dict:
    signals = stock.get("signals", [])
    if not signals:
        return None

    # 決定主色（優先 BUY/SELL，其次 WATCH）
    types = [s["type"] for s in signals]
    if "SELL" in types:
        color = COLOR["SELL"]
        header = "🔴 賣出信號"
    elif "BUY" in types:
        color = COLOR["BUY"]
        header = "🟢 買入信號"
    else:
        color = COLOR["WATCH"]
        header = "🟡 注意"

    reasons = "\n".join(f"• {s['reason']}" for s in signals)

    return {
        "color": color,
        "title": f"{header}｜{stock['symbol']} {stock['name']}",
        "fields": [
            {"name": "收盤價", "value": f"NT${stock['price']}", "inline": True},
            {"name": "RSI", "value": str(stock["rsi"]), "inline": True},
            {"name": "MA20/MA60", "value": f"{stock['ma_fast']} / {stock['ma_slow']}", "inline": True},
            {"name": "信號", "value": reasons, "inline": False},
        ],
        "footer": {"text": f"掃描時間 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} (台北)"},
    }


def send_scan_results(results: list[dict]) -> None:
    cfg = load_config()
    webhook_url = cfg["discord"]["webhook_url"]

    if webhook_url == "YOUR_DISCORD_WEBHOOK_URL_HERE":
        print("[Discord] ⚠️  尚未設定 Webhook URL，請編輯 config.yaml")
        return

    triggered = [r for r in results if r.get("signals")]

    if not triggered:
        # 無信號時發送一條簡單通知
        payload = {
            "embeds": [{
                "color": COLOR["INFO"],
                "title": "📊 台股每日掃描完成",
                "description": f"掃描 {len(results)} 檔，今日無觸發信號。",
                "footer": {"text": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")},
            }]
        }
        send_webhook(payload, webhook_url)
        print("[Discord] 已推播：無信號通知")
        return

    # 有信號：每次最多 10 個 embed（Discord 限制）
    embeds = [build_signal_embed(r) for r in triggered]
    embeds = [e for e in embeds if e]

    for i in range(0, len(embeds), 10):
        chunk = embeds[i:i+10]
        mention = cfg["discord"].get("mention_role", "")
        payload = {
            "content": mention if mention else None,
            "embeds": chunk,
        }
        ok = send_webhook(payload, webhook_url)
        print(f"[Discord] 推播 {len(chunk)} 個信號 — {'成功' if ok else '失敗'}")


if __name__ == "__main__":
    # 測試用：發送一條測試訊息
    cfg = load_config()
    webhook_url = cfg["discord"]["webhook_url"]
    if webhook_url == "YOUR_DISCORD_WEBHOOK_URL_HERE":
        print("請先在 config.yaml 填入 Discord Webhook URL")
    else:
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
