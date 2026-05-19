"""
台股排程執行器
用法：
  python tw_scheduler.py              # 立即執行一次（掃描 + 持股追蹤 + 事後驗證）
  python tw_scheduler.py --backtest   # 同上 + 重新跑 2 年信號回測（手動用）
  python tw_scheduler.py --dca        # 執行 10 年 DCA 回測並推播（手動 / 每週日）
  python tw_scheduler.py --weekly     # 週報摘要推播
  python tw_scheduler.py --signal-bt  # 執行跟單回測（10 年，4 種模式），儲存快取
  python tw_scheduler.py --daemon     # 常駐排程
"""

import sys
import json
import time
import yaml
import schedule
import holidays
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR  = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ        = ZoneInfo("Asia/Taipei")

_MODE_HISTORY_FILE = CACHE_DIR / "market_mode_history.json"


def _save_mode_and_check_recovery(mode: str, detail: dict) -> str | None:
    """
    今日 mode/detail 寫入 market_mode_history.json，
    同時比對昨日資料，偵測是否有回升事件。
    回傳: None | "ma60_cross" | "ma200_recovery"
    """
    today = date.today().isoformat()

    # 載入歷史
    history: dict = {}
    if _MODE_HISTORY_FILE.exists():
        try:
            history = json.loads(_MODE_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = {}

    # 找最近一筆「非今日」記錄
    prev_record = None
    for d in sorted(history.keys(), reverse=True):
        if d < today:
            prev_record = history[d]
            break

    # 寫入今日
    history[today] = {"mode": mode, "detail": detail}
    # 保留最近 60 天
    cutoff = (date.today() - timedelta(days=60)).isoformat()
    history = {k: v for k, v in history.items() if k >= cutoff}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _MODE_HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2),
                                  encoding="utf-8")

    if prev_record is None:
        return None

    prev_mode   = prev_record.get("mode", "NORMAL")
    prev_detail = prev_record.get("detail", {})

    # 回升確認：模式從 WARN/RISK → NORMAL（代表 TWII 站回 MA200）
    if prev_mode in ("WARN", "RISK") and mode == "NORMAL":
        return "ma200_recovery"

    # MA60 初步回升：昨日 vs MA60 ≤ 0，今日 > 0
    prev_vs60 = prev_detail.get("twii_vs_ma60_pct", None)
    curr_vs60 = detail.get("twii_vs_ma60_pct", None)
    if prev_vs60 is not None and curr_vs60 is not None:
        if prev_vs60 <= 0 and curr_vs60 > 0:
            return "ma60_cross"

    return None


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


def run_once():
    if not is_trading_day():
        today = datetime.now(TZ).strftime('%Y-%m-%d (%a)')
        print(f"[跳過] {today} 非台灣交易日（週末或國定假日）")
        return

    from tw_screener import run_scan
    from tw_discord import send_scan_results, send_webhook, load_config as discord_cfg
    from tw_portfolio import run_portfolio_check, build_portfolio_embeds

    print(f"\n{'='*50}")
    print(f"台股掃描啟動 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    cfg = discord_cfg()
    webhook_url = cfg["discord"]["webhook_url"]

    # 1. 信號掃描 + 買入/賣出推播
    print(f"\n--- 信號掃描 ---")
    results = run_scan()
    send_scan_results(results)

    # 2. 市場回升偵測（每日掃描後比對昨日 mode）
    try:
        from tw_screener import get_market_mode
        from tw_discord import build_recovery_alert_embed, send_webhook
        cur_mode, cur_detail = get_market_mode()
        alert_type = _save_mode_and_check_recovery(cur_mode, cur_detail)
        if alert_type:
            label = {"ma60_cross": "MA60 回升", "ma200_recovery": "MA200 回升確認"}[alert_type]
            print(f"\n--- 市場回升警報：{label} ---")
            embed = build_recovery_alert_embed(alert_type, cur_detail)
            ok = send_webhook({"embeds": [embed]}, webhook_url)
            print(f"[Discord] 回升警報推播 — {'成功' if ok else '失敗'}")
    except Exception as e:
        print(f"    [!] 市場回升偵測失敗：{e}")

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

    # 4. 事後驗證（每次盤後皆跑，快速操作，評分 LOOK_AHEAD 天前的信號）
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
                send_webhook({"embeds": [embed]}, webhook_url)
                print("[Discord] 信號驗證推播")
    except Exception as e:
        print(f"    [!] 事後驗證失敗：{e}")

    print(f"\n完成 {datetime.now(TZ).strftime('%H:%M:%S')}")


def run_daemon():
    cfg = load_config()
    pre = cfg["schedule"]["pre_market"]
    post = cfg["schedule"]["post_market"]

    schedule.every().day.at(pre).do(run_once)
    schedule.every().day.at(post).do(run_once)
    schedule.every().friday.at("17:00").do(run_weekly_report)

    print(f"排程常駐模式啟動")
    print(f"  盤前 {pre}：掃描 + 持股追蹤")
    print(f"  盤後 {post}：掃描 + 持股追蹤 + 事後驗證")
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


def run_sweep():
    """執行三層參數掃描（regime / crash gate / 配比），結果存入 cache/param_sweep_results.json。"""
    from tw_backtest_signals import (sweep_regime_boundary, sweep_crash_buy_gates,
                                     sweep_allocations, save_sweep_params, load_sweep_params)
    from tw_discord import send_webhook, load_config as discord_cfg

    print(f"\n{'='*50}")
    print(f"參數掃描啟動 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    cfg = discord_cfg()
    webhook_url = cfg["discord"]["webhook_url"]

    raw = cfg.get("watchlist", {})
    symbols_cfg = [
        {"symbol": item["symbol"], "name": item["name"]}
        for items in raw.values()
        for item in items
        if not item.get("backtest_only", False)
    ]
    annual = cfg.get("trade_budget", 100_000)

    print("=== Phase 1: Regime 門檻掃描 ===")
    r_res = sweep_regime_boundary(symbols_cfg, annual_injection=annual)
    best_reg = max(r_res, key=lambda x: x["calmar"])["threshold_pct"] if r_res else 2.0

    print("=== Phase 2: Crash Gate 掃描 ===")
    c_res = sweep_crash_buy_gates(symbols_cfg, annual_injection=annual, _fixed_regime=best_reg)
    save_sweep_params(r_res, c_res, source_meta={
        "trigger": "auto-scheduler",
        "date": datetime.now(TZ).strftime("%Y-%m-%d"),
    })

    print("=== Phase 3: 配比掃描 ===")
    sweep_allocations(symbols_cfg, annual_injection=annual)

    p = load_sweep_params()
    ok = send_webhook({"content": (
        f"\U0001f52c **參數掃描完成** ({datetime.now(TZ).strftime('%Y-%m-%d')})\n"
        f"Regime ±`{p['regime_threshold']}%`  "
        f"熊市 RSI<`{p['bear_rsi_gate']}`  跌>`{p['bear_drop_gate']}%`\n"
        f"bull/warn/bear: `{p['bull_mult']}`/`{p['warn_mult']}`/`{p['bear_mult']}`  "
        f"A-cash: `{p['a_cash_frac']}`  B-base: `{p['b_base_pct']}`"
    )}, webhook_url)
    print(f"[Discord] 掃描結果推播 — {'成功' if ok else '失敗'}")
    print(f"\n完成 {datetime.now(TZ).strftime('%H:%M:%S')}")


def run_signal_bt():
    """執行 10 年跟單回測（4 種模式），結果儲存至 cache/signal_backtest_*.json。"""
    from tw_backtest_signals import run_signal_backtest_all, build_signal_backtest_embed
    from tw_discord import send_webhook, load_config as discord_cfg

    print(f"\n{'='*50}")
    print(f"跟單回測啟動 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    results = run_signal_backtest_all()

    if "--push" in sys.argv:
        cfg = discord_cfg()
        url = cfg["discord"]["webhook_url"]
        for r in results:
            embed = build_signal_backtest_embed(r)
            if embed:
                ok = send_webhook({"embeds": [embed]}, url)
                print(f"[Discord] {r['name']} 跟單回測推播 — {'成功' if ok else '失敗'}")

    print(f"\n完成 {datetime.now(TZ).strftime('%H:%M:%S')}")


def backfill_outcomes():
    """補評所有已有足夠交易日的歷史掃描信號（自動跳過已評分的日期）。"""
    import holidays as _holidays
    from tw_outcome import grade_date, LOOK_AHEAD, OUTCOME_DIR, CACHE_DIR

    today   = datetime.now(TZ).date()
    scored  = 0

    for f in sorted(CACHE_DIR.glob("scan_*.json")):
        scan_date_str = f.stem.replace("scan_", "")
        try:
            scan_date = date.fromisoformat(scan_date_str)
        except ValueError:
            continue

        out_file = OUTCOME_DIR / f"outcome_{scan_date_str}.json"
        if out_file.exists():
            print(f"[backfill] {scan_date_str} 已評分，跳過")
            continue

        tw_hols  = _holidays.TW(years=[scan_date.year, today.year])
        td_count = 0
        d = scan_date + timedelta(days=1)
        while d <= today:
            if d.weekday() < 5 and d not in tw_hols:
                td_count += 1
            d += timedelta(days=1)

        if td_count >= LOOK_AHEAD:
            print(f"[backfill] 補評 {scan_date_str}（已過 {td_count} 交易日）")
            result = grade_date(scan_date_str)
            if result:
                scored += 1
        else:
            print(f"[backfill] {scan_date_str} 距今 {td_count} 交易日，尚不足 {LOOK_AHEAD} 日，跳過")

    print(f"\n[backfill] 完成，補評 {scored} 筆")


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon()
    elif "--dca" in sys.argv:
        run_dca()
    elif "--weekly" in sys.argv:
        run_weekly_report()
    elif "--signal-bt" in sys.argv:
        run_signal_bt()
    elif "--sweep" in sys.argv:
        run_sweep()
    elif "--backfill" in sys.argv:
        backfill_outcomes()
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
        run_once()
