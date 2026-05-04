"""
Discord Webhook 推播模組 v2
- STRONG BUY / BUY 分級推播
- 市場模式警示（警戒/風險）
- 持股賣出含具體建議股數與回收金額
"""

import os
import json
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

def _load_sbt_cache() -> dict:
    """載入最新的信號回測快取，以 symbol 為 key 的 dict。找不到快取則回傳 {}。"""
    cache_dir = BASE_DIR / "cache"
    files = sorted(cache_dir.glob("signal_backtest_*.json"))
    if not files:
        return {}
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        return {r["symbol"]: r for r in data if isinstance(r, dict) and "symbol" in r}
    except Exception:
        return {}


def _sbt_context_line(symbol: str, sbt_cache: dict) -> str | None:
    """從信號回測快取取出 best_mode，產生推播用的建議文字。"""
    try:
        r = sbt_cache.get(symbol)
        if not r:
            return None
        bm = r.get("best_mode", {})
        if not bm:
            return None
        label  = bm.get("label", "")
        cagr   = bm.get("cagr", 0) or 0
        mdd    = bm.get("mdd")
        calmar = bm.get("calmar", 0) or 0
        mode   = bm.get("mode", "")
        if mode == "BNH":
            return (f"持有（B&H）優於信號操作  CAGR `{cagr:.1f}%`  "
                    f"→ 建議長期持有，信號僅用於加碼時機")
        parts = [f"**{label}**", f"CAGR `{cagr:.1f}%`"]
        if mdd is not None:
            parts.append(f"MDD `{mdd:.1f}%`")
        if calmar:
            parts.append(f"Calmar `{calmar:.2f}`")
        return "  ".join(parts)
    except Exception:
        return None


def _dca_context_line(symbol: str, dca_cache: dict) -> str | None:
    """從 DCA 快取取出建議策略的 10 年 CAGR / MDD，供 embed 顯示。"""
    try:
        from tw_backtest_dca import RECOMMENDED_DCA
        r = dca_cache.get(symbol)
        if not r:
            return None
        rec_label = RECOMMENDED_DCA.get(symbol)
        if not rec_label:
            return None
        for s in r.get("strategies", []):
            if s["label"] == rec_label:
                total = s.get("total_return_pct")
                cagr  = s.get("cagr_pct")
                mdd   = s.get("max_drawdown_pct")
                parts = [f"**{rec_label}**"]
                if total is not None: parts.append(f"10年報酬 `{total}%`")
                if cagr  is not None: parts.append(f"CAGR `{cagr}%`")
                if mdd   is not None: parts.append(f"MDD `{mdd}%`")
                return "⭐ " + "  ".join(parts)
    except Exception:
        pass
    return None


def build_buy_embed(stock: dict, cfg: dict, dca_cache: dict | None = None,
                    sbt_cache: dict | None = None) -> dict:
    budget      = cfg.get("trade_budget", 100000)
    price       = stock["price"]
    avwap       = stock.get("avwap", 0)
    dd_pct      = stock.get("dd_pct", 0)
    rsi         = stock["rsi"]
    weekly_rsi  = stock.get("weekly_rsi")
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
        {"name": "現價",            "value": f"`NT${price}`", "inline": True},
        {"name": "RSI（日/週）",
         "value": f"`{rsi}`" + (f"  週 `{weekly_rsi}`" if weekly_rsi else ""),
         "inline": True},
        {"name": "DD / AVWAP距離", "value": f"`{dd_pct}%` / `{avwap_diff}`", "inline": True},
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

    # 信號回測最佳策略建議（勝率最高 / Calmar 最佳）
    sbt_line = _sbt_context_line(stock["symbol"], sbt_cache or {})
    if sbt_line:
        fields.append({"name": "📋 信號回測建議策略", "value": sbt_line, "inline": False})

    # 10 年 DCA 建議策略
    dca_line = _dca_context_line(stock["symbol"], dca_cache or {})
    if dca_line:
        fields.append({"name": "📈 10年DCA建議策略", "value": dca_line, "inline": False})

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


# ── 賣出推播 ───────────────────────────────────────────────────────────────────

def build_sell_embed(stock: dict, cfg: dict, dca_cache: dict | None = None,
                     sbt_cache: dict | None = None,
                     in_portfolio: bool = False) -> dict:
    from tw_screener import SIGNAL_CONFIG, _DEFAULT_CFG

    sym        = stock["symbol"]
    price      = stock["price"]
    avwap      = stock.get("avwap", 0)
    rsi        = stock["rsi"]
    weekly_rsi = stock.get("weekly_rsi")
    dd_pct     = stock.get("dd_pct", 0)
    budget     = cfg.get("trade_budget", 100_000)
    stock_cfg  = SIGNAL_CONFIG.get(sym, _DEFAULT_CFG)

    sell_reasons = [s["reason"] for s in stock["signals"] if s["type"] in ("SELL", "WATCH")]
    reasons_text = "\n".join(f"• {r}" for r in sell_reasons)

    # 策略目標出場區（AVWAP × s）
    s_target     = round(avwap * stock_cfg["s"], 1) if avwap > 0 else 0
    above_target = round((price / s_target - 1) * 100, 1) if s_target > 0 else 0
    avwap_dist   = f"{((price / avwap - 1) * 100):+.1f}%" if avwap > 0 else "N/A"

    # 建議出場（以 trade_budget 倉位估算）
    suggested_shares  = int(budget // price)
    suggested_proceed = int(suggested_shares * price)

    title_prefix = "🔴 賣出信號（持股）" if in_portfolio else "🔴 賣出信號（觀察）"

    fields = [
        {"name": "現價",            "value": f"`NT${price}`",    "inline": True},
        {"name": "RSI（日/週）",
         "value": f"`{rsi}`" + (f"  週 `{weekly_rsi}`" if weekly_rsi else ""),
         "inline": True},
        {"name": "DD / AVWAP距離", "value": f"`{dd_pct}%` / `{avwap_dist}`", "inline": True},
        {"name": "📌 策略目標出場",
         "value": (
             f"目標區 `NT${s_target}`（AVWAP `{avwap}` × {stock_cfg['s']}）\n"
             f"現價已超過目標 `+{above_target}%`"
         ),
         "inline": False},
        {"name": "觸發信號", "value": reasons_text, "inline": False},
        {"name": "📌 建議出場（以 NT$100k 倉位估算）",
         "value": (
             f"賣出 `{suggested_shares}` 股 @ `NT${price}`\n"
             f"估回收 `NT${suggested_proceed:,}`"
         ),
         "inline": False},
    ]

    # 信號回測最佳策略建議
    sbt_line = _sbt_context_line(sym, sbt_cache or {})
    if sbt_line:
        fields.append({"name": "📋 信號回測建議策略", "value": sbt_line, "inline": False})

    # 10 年 DCA 建議策略
    dca_line = _dca_context_line(sym, dca_cache or {})
    if dca_line:
        fields.append({"name": "📈 10年DCA建議策略", "value": dca_line, "inline": False})

    return {
        "color":  COLOR["SELL"],
        "title":  f"{title_prefix}｜{sym.replace('.TW','')} {stock['name']}",
        "fields": fields,
        "footer": {"text": f"AVWAP NT${avwap}　{datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"},
    }


# ── 每日市場模式 header ─────────────────────────────────────────────────────────

def build_market_mode_embed(results: list[dict]) -> dict | None:
    if not results:
        return None
    from tw_screener import get_market_mode
    mode, detail = get_market_mode()
    label  = MODE_LABEL.get(mode, "🟢 正常")
    color  = COLOR.get(mode, COLOR["INFO"]) if mode != "NORMAL" else COLOR["INFO"]

    twii_price = detail.get("twii_price", "N/A")
    twii_ma200 = detail.get("twii_ma200", "N/A")
    vs_pct     = detail.get("twii_vs_ma200_pct", "N/A")
    vol_20     = detail.get("vol_20_annualized", "N/A")

    strong_buys = [r for r in results if any(s["type"] == "STRONG BUY" for s in r.get("signals", []))]
    buys        = [r for r in results if any(s["type"] == "BUY"         for s in r.get("signals", []))]
    sells       = [r for r in results if any(s["type"] == "SELL"        for s in r.get("signals", []))]

    lines = [
        f"市場模式：**{label}**",
        f"TWII `{twii_price}` vs MA200 `{twii_ma200}` （`{vs_pct:+.2f}%`）　波動率 `{vol_20}%`\n",
    ]
    if strong_buys:
        lines.append("🟢🟢 強力買入：" + "、".join(r["symbol"].replace(".TW","") for r in strong_buys))
    if buys:
        lines.append("🟢 買入：" + "、".join(r["symbol"].replace(".TW","") for r in buys))

    # 賣出：邊緣觸發——區分「今日新觸發」vs「持續中（昨日已有）」
    if sells:
        yesterday_sell_syms: set[str] = set()
        try:
            import json as _json
            from datetime import timedelta
            ydate  = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
            yfile  = BASE_DIR / "cache" / f"scan_{ydate}.json"
            if not yfile.exists():
                ydate2 = (datetime.now(TZ) - timedelta(days=3)).strftime("%Y-%m-%d")
                yfile  = BASE_DIR / "cache" / f"scan_{ydate2}.json"
            if yfile.exists():
                yrecs = _json.loads(yfile.read_text(encoding="utf-8"))
                yesterday_sell_syms = {
                    r["symbol"] for r in yrecs
                    if any(s["type"] == "SELL" for s in r.get("signals", []))
                }
        except Exception:
            pass

        new_sells  = [r for r in sells if r["symbol"] not in yesterday_sell_syms]
        cont_sells = [r for r in sells if r["symbol"] in yesterday_sell_syms]

        if new_sells:
            lines.append("🔴 **新賣出**：" + "、".join(r["symbol"].replace(".TW","") for r in new_sells))
        if cont_sells:
            lines.append("🔴 賣出（持續中）：" + "、".join(r["symbol"].replace(".TW","") for r in cont_sells))

    if not strong_buys and not buys and not sells:
        lines.append("今日無明確買入/賣出信號")

    fields = []

    # 今日關鍵價位總覽
    try:
        from tw_screener import SIGNAL_CONFIG, _DEFAULT_CFG
        near = []
        price_lines = []
        for r in results:
            sym   = r["symbol"]
            price = r.get("price", 0)
            avwap = r.get("avwap", 0)
            if not avwap or not price:
                continue
            scfg = SIGNAL_CONFIG.get(sym, _DEFAULT_CFG)
            b1   = avwap * scfg["b1"]
            b2   = avwap * scfg["b2"]
            s    = avwap * scfg["s"]

            # 信號標示
            sigs = [x["type"] for x in r.get("signals", [])]
            if "STRONG BUY" in sigs:
                flag = "🟢🟢"
            elif "BUY" in sigs:
                flag = "🟢"
            elif "SELL" in sigs:
                flag = "🔴"
            else:
                flag = "⬜"

            # 近信號偵測（尚未觸發但距試買 < 5%）
            if flag == "⬜":
                dist = (price - b1) / b1 * 100
                if 0 < dist < 5:
                    flag = "📍"
                    near.append(f"{sym.replace('.TW','')} 距試買 `+{dist:.1f}%`")

            short = sym.replace(".TW", "").ljust(5)
            price_lines.append(
                f"{flag} `{short}` 現 **{price:,.0f}**　"
                f"試買 `{b1:,.0f}`　加碼 `{b2:,.0f}`　賣出 `{s:,.0f}`"
            )

        if price_lines:
            fields.append({
                "name":  "📋 今日關鍵價位（試買=AVWAP×b1　加碼=AVWAP×b2　賣出=AVWAP×s）",
                "value": "\n".join(price_lines),
                "inline": False,
            })

        if near:
            lines.append("\n📍 準備預算（距試買觸發 < 5%）：" + "、".join(near))
    except Exception:
        pass

    return {
        "color":       color,
        "title":       f"📊 台股每日掃描｜{datetime.now(TZ).strftime('%Y-%m-%d')}",
        "description": "\n".join(lines),
        "fields":      fields,
        "footer":      {"text": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")},
    }


# ── 事後驗證推播 ───────────────────────────────────────────────────────────────

def build_outcome_embed(outcome: dict) -> dict | None:
    """將 tw_outcome.grade_date() 結果格式化為 Discord embed。"""
    summary = outcome.get("summary", {})
    total   = summary.get("total", 0)
    if total == 0:
        return None

    correct = summary.get("correct", 0)
    acc     = summary.get("accuracy")
    date    = outcome.get("date", "")
    look    = outcome.get("look_ahead", 5)
    acc_str = f"{acc:.0%}" if acc is not None else "N/A"

    color = (COLOR["BUY"] if (acc or 0) >= 0.6
             else COLOR["WARN"] if (acc or 0) >= 0.4
             else COLOR["SELL"])

    lines = []
    for sym, r in outcome.get("stock_results", {}).items():
        icon    = "✅" if r.get("correct") else ("❌" if r.get("correct") is False else "—")
        pct     = r.get("actual_pct")
        pct_str = f"{pct:+.2f}%" if pct is not None else "N/A"
        lines.append(
            f"{icon} **{sym.replace('.TW','')} {r['name']}**　"
            f"{r['signal']} → {pct_str}（{look}日後）"
        )

    return {
        "color":       color,
        "title":       f"🎯 信號驗證｜{date}（{look}日後對答）",
        "description": "\n".join(lines),
        "fields": [
            {"name": "正確率", "value": f"`{correct}/{total}` ({acc_str})", "inline": True},
            {"name": "門檻",
             "value": f"買入 ≥+0.5%　賣出 ≤-0.5%", "inline": True},
        ],
        "footer": {"text": f"評分於 {outcome.get('graded_at', '')}"},
    }


# ── 主推播函數 ────────────────────────────────────────────────────────────────

def send_scan_results(results: list[dict], dca_cache: dict | None = None) -> None:
    cfg         = load_config()
    webhook_url = cfg["discord"]["webhook_url"]
    portfolio_syms = _portfolio_symbols(cfg)
    market_mode = results[0].get("market_mode", "NORMAL") if results else "NORMAL"

    # 載入 DCA 快取（若未傳入）
    if dca_cache is None:
        try:
            from tw_backtest_dca import load_dca_cache
            dca_cache = load_dca_cache()
        except Exception:
            dca_cache = {}

    # 載入信號回測快取（best_mode 建議策略）
    sbt_cache = _load_sbt_cache()

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
            buy_embeds.append(build_buy_embed(r, cfg, dca_cache=dca_cache, sbt_cache=sbt_cache))

        # 賣出推播（僅限持股）
        if "SELL" in types and in_portfolio:
            sell_embeds.append(build_sell_embed(r, cfg, dca_cache=dca_cache,
                                                sbt_cache=sbt_cache,
                                                in_portfolio=in_portfolio))

    # 先發市場模式 header
    header = build_market_mode_embed(results)
    if header:
        send_webhook({"embeds": [header]}, webhook_url)

    for i in range(0, len(buy_embeds), 10):
        ok = send_webhook({"embeds": buy_embeds[i:i+10]}, webhook_url)
        print(f"[Discord] 買入推播 {len(buy_embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")

    for i in range(0, len(sell_embeds), 10):
        ok = send_webhook({"embeds": sell_embeds[i:i+10]}, webhook_url)
        print(f"[Discord] 賣出推播 {len(sell_embeds[i:i+10])} 個 — {'成功' if ok else '失敗'}")

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


# ── 週報摘要 ──────────────────────────────────────────────────────────────────

def build_weekly_embed(cache_dir: Path, days: int = 7) -> dict | None:
    """
    讀取過去 N 天的 scan cache，統計各股信號次數與市場模式分布。
    回傳 Discord embed dict，無快取則回傳 None。
    """
    import glob as _glob
    import json
    from datetime import date, timedelta

    today  = date.today()
    cutoff = today - timedelta(days=days)

    files = sorted(_glob.glob(str(cache_dir / "scan_*.json")), reverse=True)
    daily: list[list[dict]] = []
    for f in files:
        fname_date = Path(f).stem.replace("scan_", "")
        try:
            d = date.fromisoformat(fname_date)
        except ValueError:
            continue
        if d < cutoff:
            break
        daily.append(json.loads(Path(f).read_text(encoding="utf-8")))

    if not daily:
        return None

    # 市場模式分布 + 信號累計 + 本週漲跌幅追蹤
    mode_count: dict[str, int] = {"NORMAL": 0, "WARN": 0, "RISK": 0}
    signal_tally: dict[str, dict[str, int]] = {}
    last_prices:  dict[str, float] = {}   # 最新價（daily[0] = 最新）
    first_prices: dict[str, tuple[str, float]] = {}  # 最舊價（daily[-1]）

    for day_records in daily:   # newest → oldest
        if day_records:
            day_mode = day_records[0].get("market_mode", "NORMAL")
            if day_mode in mode_count:
                mode_count[day_mode] += 1

        for r in day_records:
            sym = r["symbol"]
            name = r.get("name", sym)
            p    = r.get("price", 0)

            if sym not in signal_tally:
                signal_tally[sym] = {"name": name, "STRONG BUY": 0, "BUY": 0, "SELL": 0}
            for s in r.get("signals", []):
                t = s.get("type", "")
                if t in signal_tally[sym]:
                    signal_tally[sym][t] += 1

            if p > 0:
                if sym not in last_prices:
                    last_prices[sym] = p          # 首次遇到 = 最新一天
                first_prices[sym] = (name, p)    # 持續覆蓋 = 最終為最舊一天

    actual_days = len(daily)
    mode_line = (
        f"NORMAL `{mode_count['NORMAL']}天`　"
        f"WARN `{mode_count['WARN']}天`　"
        f"RISK `{mode_count['RISK']}天`"
    )

    # 有信號的股票排序：STRONG BUY > BUY > SELL
    active = {
        sym: v for sym, v in signal_tally.items()
        if v["STRONG BUY"] + v["BUY"] + v["SELL"] > 0
    }
    sorted_syms = sorted(
        active,
        key=lambda s: (active[s]["STRONG BUY"], active[s]["BUY"], -active[s]["SELL"]),
        reverse=True,
    )

    lines = []
    for sym in sorted_syms:
        v    = active[sym]
        name = v["name"]
        parts = []
        if v["STRONG BUY"]:
            parts.append(f"🟢🟢×{v['STRONG BUY']}")
        if v["BUY"]:
            parts.append(f"🟢×{v['BUY']}")
        if v["SELL"]:
            parts.append(f"🔴×{v['SELL']}")
        lines.append(f"**{sym.replace('.TW','')} {name}** — {' '.join(parts)}")

    if not lines:
        lines.append("本週無明確買入/賣出信號")

    # 本週漲跌幅排行
    week_perf: dict[str, dict] = {}
    for sym, (name, start_p) in first_prices.items():
        end_p = last_prices.get(sym, start_p)
        if start_p > 0:
            week_perf[sym] = {"name": name,
                              "pct":  round((end_p / start_p - 1) * 100, 2)}
    sorted_perf = sorted(week_perf.items(), key=lambda x: x[1]["pct"], reverse=True)
    winners = [(s, v) for s, v in sorted_perf[:3] if v["pct"] > 0]
    losers  = [(s, v) for s, v in sorted_perf[-3:][::-1] if v["pct"] < 0]

    perf_parts: list[str] = []
    if winners:
        perf_parts.append("📈 **本週漲幅**")
        for sym, v in winners:
            perf_parts.append(f"🔺 `{sym.replace('.TW','')} {v['name']}`  **+{v['pct']:.2f}%**")
    if losers:
        if perf_parts:
            perf_parts.append("")
        perf_parts.append("📉 **本週跌幅**")
        for sym, v in losers:
            perf_parts.append(f"🔻 `{sym.replace('.TW','')} {v['name']}`  **{v['pct']:.2f}%**")

    # 信號股 SBT 策略建議
    sbt_cache = _load_sbt_cache()
    sbt_lines: list[str] = []
    for sym in sorted_syms[:5]:
        line = _sbt_context_line(sym, sbt_cache)
        if line:
            name = active[sym]["name"]
            sbt_lines.append(f"**{sym.replace('.TW','')} {name}**\n{line}")

    # 信號正確率（近 30 日 outcome）
    _LOOK_AHEAD = 5   # 與 tw_outcome.LOOK_AHEAD 一致
    accuracy_fields = []
    try:
        from tw_outcome import compute_rolling_accuracy
        stats = compute_rolling_accuracy(30)
        if stats and stats.get("days", 0) > 0:
            acc_parts = []
            for sig, v in stats["signals"].items():
                if v["total"] == 0:
                    continue
                acc_str = f"{v['accuracy']:.0%}" if v["accuracy"] is not None else "N/A"
                avg_str = f"{v['avg_pct']:+.2f}%" if v["avg_pct"] is not None else "N/A"
                label   = sig.replace("STRONG BUY", "強買").replace("BUY", "買入").replace("SELL", "賣出")
                acc_parts.append(f"**{label}** `{v['correct']}/{v['total']}` 正確率 `{acc_str}` 平均報酬 `{avg_str}`")
            if acc_parts:
                accuracy_fields.append({
                    "name":  f"🎯 信號正確率（近 {stats['days']} 日，{_LOOK_AHEAD}日後驗證）",
                    "value": "\n".join(acc_parts),
                    "inline": False,
                })
    except Exception:
        pass

    extra_fields = []
    if perf_parts:
        extra_fields.append({
            "name":    "📊 本週漲跌幅排行",
            "value":   "\n".join(perf_parts),
            "inline":  False,
        })
    if sbt_lines:
        extra_fields.append({
            "name":    "📋 信號股建議策略",
            "value":   "\n\n".join(sbt_lines),
            "inline":  False,
        })

    return {
        "color":       COLOR["INFO"],
        "title":       f"📋 台股週報｜{(today - timedelta(days=actual_days-1)).isoformat()} ～ {today.isoformat()}",
        "description": "\n".join(lines),
        "fields": [
            {"name": "市場模式分布", "value": mode_line, "inline": False},
            {"name": "統計天數",     "value": f"{actual_days} 個交易日", "inline": True},
        ] + extra_fields + accuracy_fields,
        "footer": {"text": f"每週五自動發送　{datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"},
    }


if __name__ == "__main__":
    cfg = load_config()
    ok  = send_webhook({"embeds": [{"color": COLOR["INFO"],
                                     "title": "✅ 台股推播測試 v2",
                                     "description": "Discord Webhook 連線正常",
                                     "footer": {"text": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")}}]},
                        cfg["discord"]["webhook_url"])
    print(f"測試推播：{'成功' if ok else '失敗'}")
