"""
台股排程執行器
用法：
  python tw_scheduler.py              # 立即執行一次（掃描 + 持股追蹤）
  python tw_scheduler.py --backtest   # 掃描 + 持股追蹤 + 重新回測
  python tw_scheduler.py --dca        # 執行 DCA 長期回測並推播
  python tw_scheduler.py --daemon     # 常駐排程
"""

import sys
import time
import yaml
import schedule
import holidays
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
TZ = ZoneInfo("Asia/Taipei")


def is_trading_day(d: date | None = None) -> bool:
    """回傳 True 代表台灣股市交易日（非週末且非國定假日）。"""
    if d is None:
        d = datetime.now(TZ).date()
    if d.weekday() >= 5:  # 週六=5, 週日=6
        return False
    tw_holidays = holidays.TW(years=d.year)
    return d not in tw_holidays


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_once(run_backtest: bool = False):
    if not is_trading_day():
        today = datetime.now(TZ).strftime('%Y-%m-%d (%a)')
        print(f"[跳過] {today} 非台灣交易日（週末或國定假日）")
        return

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

    # 4. 事後驗證（盤後才跑，評分 LOOK_AHEAD 天前的信號）
    if run_backtest:
        try:
            from tw_outcome import grade_date, LOOK_AHEAD
            import holidays as _holidays
            today_d   = datetime.now(TZ).date()
            candidate = today_d - timedelta(days=1)
            tw_hols   = _holidays.TW(years=candidate.year)
            count = 0
            while count < LOOK_AHEAD:
                if candidate.weekday() < 5 and candidate not in tw_hols:
                    count += 1
                if count < LOOK_AHEAD:
                    candidate -= timedelta(days=1)
            print(f"\n--- 信號事後驗證（評分 {candidate.isoformat()}）---")
            outcome = grade_date(candidate.isoformat())
            if outcome:
                from tw_discord import build_outcome_embed
                embed = build_outcome_embed(outcome)
                if embed:
                    from tw_discord import send_webhook, load_config as discord_cfg
                    cfg2 = discord_cfg()
                    send_webhook({"embeds": [embed]}, cfg2["discord"]["webhook_url"])
                    print("[Discord] 信號驗證推播")
        except Exception as e:
            print(f"    [!] 事後驗證失敗：{e}")

    print(f"\n完成 {datetime.now(TZ).strftime('%H:%M:%S')}")


def run_daemon():
    cfg = load_config()
    pre = cfg["schedule"]["pre_market"]
    post = cfg["schedule"]["post_market"]

    schedule.every().day.at(pre).do(run_once, run_backtest=False)
    schedule.every().day.at(post).do(run_once, run_backtest=True)
    schedule.every().friday.at("17:00").do(run_weekly_report)

    print(f"排程常駐模式啟動")
    print(f"  盤前 {pre}：掃描 + 持股追蹤（用快取回測）")
    print(f"  盤後 {post}：掃描 + 持股追蹤 + 重新回測")
    print(f"  每週五 17:00：週報摘要推播")
    print(f"  按 Ctrl+C 停止\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


def run_weekly_report():
    """每週五發送本週信號統計摘要。"""
    from tw_discord import build_weekly_embed, send_webhook, load_config as discord_cfg

    print(f"\n{'='*50}")
    print(f"週報摘要 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    embed = build_weekly_embed(BASE_DIR / "cache")
    if embed is None:
        print("無快取資料，跳過週報")
        return

    cfg = discord_cfg()
    ok  = send_webhook({"embeds": [embed]}, cfg["discord"]["webhook_url"])
    print(f"[Discord] 週報推播 — {'成功' if ok else '失敗'}")


def run_dca():
    from tw_backtest_dca import run_dca_all, build_dca_embed, load_dca_cache
    from tw_discord import send_webhook, load_config as discord_cfg

    print(f"\n{'='*50}")
    print(f"DCA 長期回測啟動 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    results = run_dca_all()
    cfg = discord_cfg()
    url = cfg["discord"]["webhook_url"]
    embeds = [build_dca_embed(r) for r in results]
    for i in range(0, len(embeds), 10):
        ok = send_webhook({"embeds": embeds[i:i+10]}, url)
        print(f"[Discord] DCA 推播 {len(embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")
    print(f"\n完成 {datetime.now(TZ).strftime('%H:%M:%S')}")


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon()
    elif "--dca" in sys.argv:
        run_dca()
    elif "--weekly" in sys.argv:
        run_weekly_report()
    elif "--outcome" in sys.argv:
        from tw_outcome import grade_date, compute_rolling_accuracy
        target = sys.argv[2] if len(sys.argv) > 2 else None
        if target:
            grade_date(target)
        else:
            stats = compute_rolling_accuracy(30)
            if stats:
                print(f"近 {stats['days']} 日滾動正確率：")
                for sig, v in stats["signals"].items():
                    acc = f"{v['accuracy']:.0%}" if v["accuracy"] else "N/A"
                    avg = f"{v['avg_pct']:+.2f}%" if v["avg_pct"] is not None else "N/A"
                    print(f"  {sig:12} {v['correct']}/{v['total']} 正確率 {acc}  平均報酬 {avg}")
            else:
                print("無 outcome 資料")
    else:
        run_once(run_backtest="--backtest" in sys.argv)
