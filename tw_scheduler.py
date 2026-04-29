"""
台股排程執行器
用法：
  python tw_scheduler.py              # 立即執行一次（掃描 + 持股追蹤）
  python tw_scheduler.py --backtest   # 掃描 + 持股追蹤 + 重新回測
  python tw_scheduler.py --daemon     # 常駐排程
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


def run_once(run_backtest: bool = False):
    from tw_screener import run_scan
    from tw_discord import send_scan_results, send_webhook, load_config as discord_cfg
    from tw_backtest import run_backtest_all, build_backtest_embed, load_backtest_cache
    from tw_portfolio import run_portfolio_check, build_portfolio_embeds

    print(f"\n{'='*50}")
    print(f"台股掃描啟動 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    cfg = discord_cfg()
    webhook_url = cfg["discord"]["webhook_url"]

    # 1. 回測（若旗標或無快取則重新執行，否則讀快取）
    bt_cache = load_backtest_cache()
    if run_backtest or not bt_cache:
        print(f"\n--- 執行策略回測 ---")
        bt_results = run_backtest_all()
        bt_cache = {r["symbol"]: r for r in bt_results}
        bt_embeds = [build_backtest_embed(r) for r in bt_results]
        for i in range(0, len(bt_embeds), 10):
            send_webhook({"embeds": bt_embeds[i:i+10]}, webhook_url)
        print(f"回測摘要已推播至 Discord")

    # 2. 信號掃描 + 買入/賣出推播（含回測佐證）
    print(f"\n--- 信號掃描 ---")
    results = run_scan()
    send_scan_results(results, bt_cache=bt_cache)

    # 3. 持股追蹤（只在有操作信號時推播）
    print(f"\n--- 持股追蹤 ---")
    portfolio_results = run_portfolio_check()
    actionable = [r for r in portfolio_results if r["advice"].get("push")]
    if actionable:
        portfolio_embeds = build_portfolio_embeds(portfolio_results)
        for i in range(0, len(portfolio_embeds), 10):
            send_webhook({"embeds": portfolio_embeds[i:i+10]}, webhook_url)
        print(f"持股推播：{len(actionable)} 筆有操作信號")
    else:
        print(f"持股無操作信號，靜默（持有中）")

    print(f"\n完成 {datetime.now(TZ).strftime('%H:%M:%S')}")


def run_daemon():
    cfg = load_config()
    pre = cfg["schedule"]["pre_market"]
    post = cfg["schedule"]["post_market"]

    schedule.every().day.at(pre).do(run_once, run_backtest=False)
    schedule.every().day.at(post).do(run_once, run_backtest=True)

    print(f"排程常駐模式啟動")
    print(f"  盤前 {pre}：掃描 + 持股追蹤（用快取回測）")
    print(f"  盤後 {post}：掃描 + 持股追蹤 + 重新回測")
    print(f"  按 Ctrl+C 停止\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon()
    else:
        run_once(run_backtest="--backtest" in sys.argv)
