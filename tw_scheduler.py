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
    from tw_discord import send_scan_results

    print(f"\n{'='*50}")
    print(f"台股掃描啟動 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    results = run_scan()
    send_scan_results(results)

    print(f"\n完成 {datetime.now(TZ).strftime('%H:%M:%S')}")


def run_daemon():
    cfg = load_config()
    pre = cfg["schedule"]["pre_market"]
    post = cfg["schedule"]["post_market"]

    schedule.every().day.at(pre).do(run_once)
    schedule.every().day.at(post).do(run_once)

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
