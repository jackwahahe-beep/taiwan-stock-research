"""
台股信號事後驗證
評分邏輯：
  - BUY / STRONG BUY：信號日後第 LOOK_AHEAD 個交易日的報酬 ≥ BUY_THR  → 正確
  - SELL：信號日後第 LOOK_AHEAD 個交易日的報酬 ≤ SELL_THR → 正確

用法：
    python tw_outcome.py              # 評分昨日信號（1日後）
    python tw_outcome.py 2026-04-29   # 評分指定日期
    python tw_outcome.py --stats      # 顯示近 30 日滾動正確率
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

BASE_DIR    = Path(__file__).parent
CACHE_DIR   = BASE_DIR / "cache"
OUTCOME_DIR = CACHE_DIR / "outcomes"
TZ          = ZoneInfo("Asia/Taipei")

LOOK_AHEAD = 5     # 評分往後看幾個交易日
BUY_THR    =  0.5  # 漲幅 ≥ 0.5% → BUY 信號正確
SELL_THR   = -0.5  # 跌幅 ≤ -0.5% → SELL 信號正確


def _fetch_forward_pct(symbol: str, signal_date: str, days_ahead: int) -> float | None:
    """取信號日後第 days_ahead 個交易日的報酬（相對信號日收盤）。"""
    try:
        dt    = datetime.strptime(signal_date, "%Y-%m-%d")
        start = dt.strftime("%Y-%m-%d")
        end   = (dt + timedelta(days=days_ahead * 2 + 5)).strftime("%Y-%m-%d")
        hist  = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return None
        entry = float(hist["Close"].iloc[0])
        idx   = min(days_ahead, len(hist) - 1)
        exit_ = float(hist["Close"].iloc[idx])
        return round((exit_ / entry - 1) * 100, 2)
    except Exception as e:
        print(f"  [outcome] {symbol} {signal_date}: {e}")
        return None


def _grade(signal_type: str, pct: float | None) -> bool | None:
    if pct is None:
        return None
    if signal_type in ("BUY", "STRONG BUY"):
        return pct >= BUY_THR
    if signal_type == "SELL":
        return pct <= SELL_THR
    return None


def grade_date(signal_date: str, look_ahead: int = LOOK_AHEAD) -> dict | None:
    """評分指定日期的所有買賣信號，寫入 cache/outcomes/。"""
    cache_file = CACHE_DIR / f"scan_{signal_date}.json"
    if not cache_file.exists():
        print(f"[outcome] 找不到 {cache_file}")
        return None

    records = json.loads(cache_file.read_text(encoding="utf-8"))
    signal_records = [
        r for r in records
        if any(s["type"] in ("BUY", "STRONG BUY", "SELL") for s in r.get("signals", []))
    ]

    if not signal_records:
        print(f"[outcome] {signal_date} 無買賣信號，跳過")
        return None

    print(f"[outcome] 評分 {signal_date}（看{look_ahead}日後），共 {len(signal_records)} 檔有信號")

    stock_results: dict[str, dict] = {}
    scored_correct = 0
    scored_total   = 0

    for r in signal_records:
        sym  = r["symbol"]
        name = r["name"]
        # 取第一個主要信號（STRONG BUY > BUY > SELL 優先）
        sig_type = None
        for priority in ("STRONG BUY", "BUY", "SELL"):
            if any(s["type"] == priority for s in r.get("signals", [])):
                sig_type = priority
                break
        if not sig_type:
            continue

        pct     = _fetch_forward_pct(sym, signal_date, look_ahead)
        correct = _grade(sig_type, pct)

        stock_results[sym] = {
            "name":         name,
            "signal":       sig_type,
            "entry_price":  r.get("price"),
            "actual_pct":   pct,
            "look_ahead":   look_ahead,
            "correct":      correct,
        }

        icon = "✅" if correct else ("❌" if correct is False else "?")
        pct_str = f"{pct:+.2f}%" if pct is not None else "N/A"
        print(f"  {sym:12} {sig_type:10} {icon}  {pct_str}")

        if correct is not None:
            scored_total   += 1
            scored_correct += int(correct)

    acc = round(scored_correct / scored_total, 3) if scored_total > 0 else None

    outcome = {
        "date":         signal_date,
        "look_ahead":   look_ahead,
        "stock_results": stock_results,
        "summary": {
            "correct":  scored_correct,
            "total":    scored_total,
            "accuracy": acc,
        },
        "graded_at": datetime.now(TZ).isoformat(timespec="seconds"),
    }

    OUTCOME_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTCOME_DIR / f"outcome_{signal_date}.json"
    out_file.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[outcome] 寫入 → {out_file}")
    if acc is not None:
        print(f"[outcome] 正確率：{scored_correct}/{scored_total}（{acc:.0%}）")
    return outcome


def load_recent_outcomes(n: int = 30) -> list[dict]:
    if not OUTCOME_DIR.exists():
        return []
    files = sorted(OUTCOME_DIR.glob("outcome_*.json"), reverse=True)
    results = []
    for f in list(files)[:n]:
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return results


def compute_rolling_accuracy(n: int = 30) -> dict:
    """計算近 n 筆 outcome 的各信號類型正確率。"""
    outcomes = load_recent_outcomes(n)
    if not outcomes:
        return {}

    by_type: dict[str, list[int]] = {
        "STRONG BUY": [0, 0],
        "BUY":        [0, 0],
        "SELL":       [0, 0],
    }
    total_pnl: dict[str, list[float]] = {k: [] for k in by_type}

    for o in outcomes:
        for sym, r in o.get("stock_results", {}).items():
            sig = r.get("signal")
            if sig not in by_type:
                continue
            if r.get("correct") is not None:
                by_type[sig][1] += 1
                by_type[sig][0] += int(r["correct"])
            if r.get("actual_pct") is not None:
                total_pnl[sig].append(r["actual_pct"])

    result = {"days": len(outcomes), "signals": {}}
    for sig, (correct, total) in by_type.items():
        pnls = total_pnl[sig]
        result["signals"][sig] = {
            "correct":      correct,
            "total":        total,
            "accuracy":     round(correct / total, 3) if total > 0 else None,
            "avg_pct":      round(sum(pnls) / len(pnls), 2) if pnls else None,
        }
    return result


if __name__ == "__main__":
    if "--stats" in sys.argv:
        stats = compute_rolling_accuracy(30)
        if not stats:
            print("無 outcome 資料")
        else:
            print(f"近 {stats['days']} 日滾動正確率：")
            for sig, v in stats["signals"].items():
                acc_str = f"{v['accuracy']:.0%}" if v["accuracy"] is not None else "N/A"
                avg_str = f"{v['avg_pct']:+.2f}%" if v["avg_pct"] is not None else "N/A"
                print(f"  {sig:12} {v['correct']}/{v['total']} 正確率 {acc_str}  平均報酬 {avg_str}")
    else:
        target = sys.argv[1] if len(sys.argv) > 1 else (
            datetime.now(TZ) - timedelta(days=LOOK_AHEAD + 2)
        ).strftime("%Y-%m-%d")
        grade_date(target)
