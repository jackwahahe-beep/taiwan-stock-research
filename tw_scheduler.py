"""
台股排程執行器
用法：
  python tw_scheduler.py           # 立即執行一次（手動觸發）
  python tw_scheduler.py --daemon  # 背景常駐，依 config.yaml 排程自動執行
"""

import sys
import time
import yaml
import schedule
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
TZ = ZoneInfo("Asia/Taipei")


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_once():
    from tw_screener import run_scan
    from tw_discord import send_scan_results, send_webhook, load_config
    from tw_backtest import run_backtest_all, build_backtest_embed

    print(f"\n{'='*50}")
    print(f"台股掃描啟動 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    # 1. 信號掃描 + 推播
    results = run_scan()
    send_scan_results(results)

    # 2. 回測（僅盤後執行，或手動觸發時）
    if "--backtest" in sys.argv or "--post" in sys.argv:
        print(f"\n--- 執行策略回測 ---")
        bt_results = run_backtest_all()
        cfg = load_config()
        webhook_url = cfg["discord"]["webhook_url"]
        embeds = [build_backtest_embed(r) for r in bt_results]
        for i in range(0, len(embeds), 10):
            send_webhook({"embeds": embeds[i:i+10]}, webhook_url)
        print(f"回測摘要已推播至 Discord")

    print(f"\n完成 {datetime.now(TZ).strftime('%H:%M:%S')}")


def run_daemon():
    cfg = load_config()
    pre = cfg["schedule"]["pre_market"]
    post = cfg["schedule"]["post_market"]

    sys.argv.append("--pre")
    schedule.every().day.at(pre).do(run_once)

    def run_post():
        if "--pre" in sys.argv:
            sys.argv.remove("--pre")
        sys.argv.append("--post")
        run_once()

    schedule.every().day.at(post).do(run_post)

    print(f"排程常駐模式啟動")
    print(f"  盤前掃描: {pre} (台北)")
    print(f"  盤後掃描: {post} (台北)")
    print(f"  按 Ctrl+C 停止\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon()
    else:
        run_once()
