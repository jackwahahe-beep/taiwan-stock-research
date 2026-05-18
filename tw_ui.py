"""
台股策略看盤介面
  📡 掃描 Tab：即時信號 + 目標價位表格
  📈 回測 Tab：2年信號回測明細，可展開每筆進出場
"""

import json
import threading
import glob as _glob
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import customtkinter as ctk
from tkinter import ttk, font as tkfont

BASE_DIR  = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ        = ZoneInfo("Asia/Taipei")

# 跟單回測預設日期（與 tw_backtest_signals.py 一致）
START_DATE = "2015-01-01"
END_DATE   = "2025-12-31"

# ── 顏色常數 ──────────────────────────────────────────────────────────────────
BG       = "#1a1a2e"
BG_PANEL = "#16213e"
BG_ROW_A = "#0f3460"
BG_ROW_B = "#0a2744"
BG_HDR   = "#0d1b2a"

C_GREEN  = "#2ecc71"
C_STRONG = "#1abc9c"
C_RED    = "#e74c3c"
C_YELLOW = "#f1c40f"
C_GRAY   = "#95a5a6"
C_WHITE  = "#ecf0f1"
C_BLUE   = "#3498db"

# 各股最早有效資料年份（上市較晚或早期資料稀少的股票）
# 點擊股票時若選定的開始年早於此值，自動調整
STOCK_EARLIEST_YEAR = {
    "00929.TW": 2023,  # 上市 2023-04
    "00919.TW": 2023,  # 上市 2022-10，但 2022 年資料不足 200 天
    "00878.TW": 2020,  # 上市 2019-08，2019 年資料僅半年
    "00713.TW": 2018,  # 上市 2017-12，2017 年資料僅半個月
}

# 台股重大股災定義（用於回測策略應對分析）
CRASH_PERIODS = [
    ("中美貿易戰",  "2018-10-01", "2018-12-31"),  # TWII -22%
    ("COVID 股災",  "2020-02-20", "2020-04-30"),   # TWII -28%
    ("升息熊市",    "2022-01-01", "2022-10-31"),   # TWII -40%
    ("日圓套息平倉","2024-07-31", "2024-08-20"),   # TWII -12%（急跌）
]


def _crash_analysis(trades: list[dict], crash_start: str, crash_end: str) -> dict:
    """計算某策略在特定股災期間的進出場行為。"""
    from datetime import date as _date, timedelta
    entries = sum(1 for t in trades
                  if crash_start <= t.get("entry_date", "") <= crash_end)
    trail_exits = sum(1 for t in trades
                      if t.get("exit_signal") == "TRAILING_STOP"
                      and crash_start <= t.get("exit_date", "") <= crash_end)
    sell_exits = sum(1 for t in trades
                     if t.get("exit_signal") in ("SELL", "TRIM")
                     and crash_start <= t.get("exit_date", "") <= crash_end)
    pre_start = (_date.fromisoformat(crash_start) - timedelta(days=45)).isoformat()
    pre_exits = sum(1 for t in trades
                    if t.get("exit_signal") not in ("PERIOD_END",)
                    and pre_start <= t.get("exit_date", "") < crash_start
                    and t.get("pnl_net", 0) > 0)
    return {"entries": entries, "trail_exits": trail_exits,
            "sell_exits": sell_exits, "pre_exits": pre_exits}


CATEGORY = {
    "0050.TW":  ("ETF 基準", "B&H"),
    "00878.TW": ("高息 ETF", "跌時加碼"),
    "00713.TW": ("高息 ETF", "跌時加碼"),
    "00929.TW": ("高息 ETF", "深跌加碼"),
    "00919.TW": ("高息 ETF", "B&H"),
    "2330.TW":  ("大型科技", "B&H"),
    "2454.TW":  ("大型科技", "跌時加碼"),
    "3711.TW":  ("中型科技", "B&H"),
    "2303.TW":  ("中型科技", "B&H"),
    "2382.TW":  ("AI伺服器", "B&H"),
    "2308.TW":  ("電源散熱", "深跌加碼"),
    "2912.TW":  ("防禦消費", "B&H"),
    "3037.TW":  ("趨勢強股", "B&H"),
    "2408.TW":  ("記憶體",   "跌時加碼"),
}

# ── 懸停提示工具 ────────────────────────────────────────────────────────────────

class _Tooltip:
    """在任何 tk/ctk widget 上顯示懸停說明泡泡（450ms 延遲）。"""
    _BG = "#1e2d40"
    _FG = "#dce8f0"

    def __init__(self, widget, text: str):
        self._w    = widget
        self._text = text
        self._win  = None
        self._job  = None
        widget.bind("<Enter>",   self._enter,  add="+")
        widget.bind("<Leave>",   self._leave,  add="+")
        widget.bind("<Destroy>", self._leave,  add="+")

    def _enter(self, _=None):
        self._job = self._w.after(450, self._show)

    def _leave(self, _=None):
        if self._job:
            self._w.after_cancel(self._job)
            self._job = None
        if self._win:
            self._win.destroy()
            self._win = None

    def _show(self):
        import tkinter as _tk
        if self._win:
            return
        x = self._w.winfo_rootx() + self._w.winfo_width() // 2
        y = self._w.winfo_rooty() + self._w.winfo_height() + 4
        self._win = tw = _tk.Toplevel(self._w)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass
        f = _tk.Frame(tw, bg=self._BG, bd=1, relief="solid")
        f.pack()
        _tk.Label(f, text=self._text, bg=self._BG, fg=self._FG,
                  font=("Consolas", 12), justify="left",
                  padx=7, pady=5, wraplength=290).pack()


def _add_tree_col_tips(tree, tips: dict) -> None:
    """在 Treeview 欄位標題上顯示懸停說明。"""
    import tkinter as _tk
    _state = {"win": None, "col": None}

    def _hide():
        if _state["win"]:
            _state["win"].destroy()
            _state["win"] = None
        _state["col"] = None

    def _motion(event):
        if tree.identify_region(event.x, event.y) != "heading":
            _hide()
            return
        try:
            col_id = tree["columns"][int(tree.identify_column(event.x)[1:]) - 1]
        except (ValueError, IndexError):
            _hide()
            return
        text = tips.get(col_id)
        if not text:
            _hide()
            return
        if _state["col"] == col_id:
            return
        _hide()
        _state["col"] = col_id
        x = tree.winfo_rootx() + event.x + 12
        y = tree.winfo_rooty() + event.y + 22
        _state["win"] = tw = _tk.Toplevel(tree)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass
        f = _tk.Frame(tw, bg="#1e2d40", bd=1, relief="solid")
        f.pack()
        _tk.Label(f, text=text, bg="#1e2d40", fg="#dce8f0",
                  font=("Consolas", 12), justify="left",
                  padx=7, pady=5, wraplength=270).pack()

    tree.bind("<Motion>", _motion, add="+")
    tree.bind("<Leave>",  lambda e: _hide(), add="+")


# ── 欄位說明文字 ─────────────────────────────────────────────────────────────────

_SCAN_COL_TIPS: dict[str, str] = {
    "rsi":       "RSI（Relative Strength Index）相對強弱指數\n14日計算。< 30 超賣，> 70 超買\n各股買賣閾值依個股設定（ETF 約 42，科技股約 50）",
    "avwap":     "AVWAP（Anchored VWAP）錨定成交量加權均價\n從近60日低點錨定計算，為買賣核心參考錨點\n低於 AVWAP = 相對便宜，高於 = 相對昂貴",
    "avwap_dist":"現價 vs AVWAP 的距離百分比\n負值 = 低於 AVWAP（潛在買入區）\n正值 = 高於 AVWAP（潛在賣出區）",
    "b1":        "試買參考價（AVWAP × b1）\nBUY 信號觸發條件之一：現價 < b1\n代表輕度低估，適合首次建倉",
    "b2":        "加碼參考價（AVWAP × b2）\nSTRONG BUY 觸發條件之一：現價 < b2\n更深度低估，適合加碼",
    "s_target":  "賣出參考價（AVWAP × s）\nSELL 信號觸發條件之一：現價 > s_target\n代表達到目標估值，應考慮獲利了結",
    "dd":        "回撤（Drawdown）\n從近60日最高收盤的跌幅（負值）\n≤ -10% 觸發 BUY，≤ -20% 觸發 STRONG BUY",
    "rec":       "【長期定期投資策略】根據 10 年回測，此股票最適合哪種 DCA 方式\n"
                 "⚠️ 這不是現在要買的意思！是長期每月定投的加碼邏輯：\n"
                 "B&H     = 無條件定期投入，不挑時機\n"
                 "跌時加碼 = 跌 10%+ 且 RSI 偏低才加碼（v2 BUY DCA）\n"
                 "深跌加碼 = 跌 20%+ 且更超賣才大幅加碼（v2 STRONG BUY DCA）\n"
                 "→ 現在要不要進場，請看「信號」欄",
    "pnl":       "估算持股損益（需在持股清單中）\n以現價估算，含未實現損益",
}

_DCA_METRIC_TIPS: dict[str, str] = {
    "CAGR":  "年化複合報酬率（Compound Annual Growth Rate）\n考慮持有年數的真實年化報酬\n> 12% 良好，> 20% 優秀",
    "總報酬": "回測期間的總累積報酬百分比（未年化）",
    "MDD":   "最大回撤（Max Drawdown）\n從投資組合高點到最低點的最大跌幅\n越接近 0 越好；< -30% 代表高風險",
    "終值":   "回測結束時投資組合的估算總市值",
    "注資":   "回測期間的總投入資金（定期定額累計）",
    "交易次": "回測期間的總買入次數（加碼筆數）",
}

_SBT_METRIC_TIPS: dict[str, str] = {
    "報酬率":    "回測期間整體累積報酬率（未年化）",
    "CAGR":     "年化複合報酬率\n> 15% 良好，> 25% 優秀",
    "Calmar":   "Calmar 比率 = CAGR ÷ |MDD|\n每承擔 1% 最大回撤能獲得多少年化報酬\n> 0.3 良好，> 0.5 優秀，> 1.0 卓越",
    "Sharpe":   "Sharpe 比率 = (年化報酬 − 無風險利率 1.5%) ÷ 年化波動率\n衡量每單位風險獲得的超額報酬\n> 0.5 可接受，> 1.0 良好，> 2.0 優秀",
    "總損益":    "回測期間的總實現損益（NT$）",
    "MDD":      "最大回撤（Max Drawdown）\n從淨值高點到最低點的最大虧損幅度\n< -10% 可接受，< -20% 需特別注意",
    "手續費+稅": "回測期間估算的交易成本\n手續費 0.1425% + 證交稅 0.3%（賣方）",
    "勝率":     "獲利交易筆數 ÷ 總交易筆數\n> 60% 良好，但需同時觀察 Calmar",
    "交易次":    "回測期間的總交易筆數\n< 10 筆時統計意義較低",
    "平均持有":  "每筆交易從進場到出場的平均持有天數",
    "最佳":     "單筆最高報酬的交易報酬率",
    "最差":     "單筆最大虧損的交易報酬率",
    "期末未結":  "回測結束時尚未出場的持倉數量\n以期末收盤價估算損益（非真實出場價）",
}

_V2_METRIC_TIPS: dict[str, str] = {
    "總注資":    "回測期間每年注入的累計總金額\n= 年注資 × 年數",
    "最終市值":  "回測結束時，現金 + A類持倉市值 + B類強制平倉後的總和",
    "總獲利":    "最終市值 − 總注資\n正數 = 盈利；負數 = 虧損",
    "CAGR":     "年化複合報酬率（Compound Annual Growth Rate）\n考慮持有年數的真實年化報酬\n> 12% 良好，> 20% 優秀",
    "MDD":      "最大回撤（Max Drawdown）\n從組合市值高點到最低點的最大跌幅\n越接近 0 越好；< -30% 代表高風險",
    "Calmar":   "Calmar 比率 = CAGR ÷ |MDD|\n每承擔 1% 最大回撤能獲得多少年化報酬\n> 0.3 良好，> 0.5 優秀，> 1.0 卓越",
    "Sharpe":   "Sharpe 比率 = (年化報酬 − 無風險利率 1.5%) ÷ 年化波動率\n衡量每單位風險獲得的超額報酬\n> 0.5 可接受，> 1.0 良好，> 2.0 優秀",
    "勝率":     "B類已出場交易中，獲利筆數 ÷ 總筆數\n> 60% 良好（A類永不賣出，不計入勝率）",
    "Sortino":  "Sortino 比率 = 超額報酬 ÷ 下行波動率\n只懲罰虧損的波動，忽略正報酬波動\n> Sharpe 更適合衡量不對稱報酬策略\n> 0.8 良好，> 1.5 優秀",
    "0050 CAGR": "同期 0050 買入持有的年化報酬，作為基準比較\n你的 CAGR > 0050 CAGR 才算跑贏大盤",
}


SCAN_COLS = [
    ("symbol",    "代號",      80,  "w"),
    ("name",      "名稱",     100,  "w"),
    ("cat",       "類別",      90,  "center"),
    ("rec",       "DCA策略",   70,  "center"),
    ("signal",    "信號",      90,  "center"),
    ("price",     "現價",      70,  "e"),
    ("rsi",       "RSI",       50,  "center"),
    ("avwap",     "AVWAP",     80,  "e"),
    ("avwap_dist","vs AVWAP",  68,  "center"),  # 現價 vs AVWAP 距離 %
    ("b1",        "試買",      80,  "e"),
    ("b2",        "加碼",      80,  "e"),
    ("s_target",  "賣出參考",  85,  "e"),
    ("dd",        "回撤",      60,  "center"),
    ("pnl",       "持股損益", 100,  "e"),
]


# ── 資料讀取 ──────────────────────────────────────────────────────────────────

def _load_scan_cache() -> list[dict]:
    files = sorted(_glob.glob(str(CACHE_DIR / "scan_*.json")), reverse=True)
    return json.loads(Path(files[0]).read_text(encoding="utf-8")) if files else []


def _load_config() -> dict:
    import yaml
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_dca_cache() -> dict:
    files = sorted(_glob.glob(str(CACHE_DIR / "dca_backtest_*.json")), reverse=True)
    if not files:
        return {}
    data = json.loads(Path(files[0]).read_text(encoding="utf-8"))
    return {r["symbol"]: r for r in data}


_DCA_SHORT = {
    "B&H DCA（無條件）": "B&H",
    "v2 BUY DCA":        "跌時加碼",
    "v2 STRONG BUY DCA": "深跌加碼",
    "市場警戒逆向加碼":  "逆向加碼",
}

def _load_dca_rec() -> dict:
    """從最新 DCA 回測快取，按 CAGR 選出每股最佳策略，回傳 {symbol: short_label}。"""
    cache = _load_dca_cache()
    result = {}
    for sym, r in cache.items():
        strats = r.get("strategies", [])
        if not strats:
            continue
        best = max(strats, key=lambda s: s.get("cagr_pct") or 0)
        result[sym] = _DCA_SHORT.get(best.get("label", ""), "B&H")
    return result


def _load_sbt_cache() -> dict:
    files = sorted(_glob.glob(str(CACHE_DIR / "signal_backtest_*.json")), reverse=True)
    if not files:
        return {}
    data = json.loads(Path(files[0]).read_text(encoding="utf-8"))
    return {r["symbol"]: r for r in data if "error" not in r}


def _build_scan_rows(scan_records: list[dict], cfg: dict) -> list[dict]:
    from tw_screener import SIGNAL_CONFIG, _DEFAULT_CFG

    portfolio = {h["symbol"]: h for h in cfg.get("portfolio", [])}
    dca_rec   = _load_dca_rec()   # 動態從最新 DCA 回測快取讀取
    rows = []

    for r in scan_records:
        sym   = r["symbol"]
        scfg  = SIGNAL_CONFIG.get(sym, _DEFAULT_CFG)
        avwap = r.get("avwap", 0)
        price = r.get("price", 0)

        sigs = [s["type"] for s in r.get("signals", [])
                if s["type"] in ("STRONG BUY", "BUY", "SELL", "WATCH")]
        if "STRONG BUY" in sigs:   signal = "STRONG BUY"
        elif "BUY" in sigs:        signal = "BUY"
        elif "SELL" in sigs:       signal = "SELL"
        elif "WATCH" in sigs:      signal = "WATCH"
        else:                      signal = "HOLD"

        b1       = round(avwap * scfg["b1"], 1) if avwap else 0
        b2       = round(avwap * scfg["b2"], 1) if avwap else 0
        s_target = round(avwap * scfg["s"],  1) if avwap else 0

        pnl_str = ""
        if sym in portfolio:
            h   = portfolio[sym]
            pnl = round((price - h["cost"]) * h["shares"], 0) if h["cost"] > 0 else None
            pct = round((price - h["cost"]) / h["cost"] * 100, 2) if h["cost"] > 0 else None
            if pnl is not None:
                pnl_str = f"{'+'if pnl>=0 else''}{int(pnl):,} ({pct:+.1f}%)"
            else:
                pnl_str = f"NT${int(price * h['shares']):,} (配股)"

        avwap_dist = round((price / avwap - 1) * 100, 1) if avwap and price else None
        cat, _fallback_rec = CATEGORY.get(sym, ("—", "—"))
        rec = dca_rec.get(sym) or _fallback_rec  # 優先用回測快取，無快取才用預設值
        rows.append({
            "symbol":     sym.replace(".TW", ""),
            "name":       r.get("name", sym),
            "cat":        cat, "rec": rec, "signal": signal,
            "price":      f"{price:,.1f}" if price else "—",
            "rsi":        str(r.get("rsi", "—")),
            "avwap":      f"{avwap:,.1f}" if avwap else "—",
            "avwap_dist": f"{avwap_dist:+.1f}%" if avwap_dist is not None else "—",
            "b1":         f"{b1:,.1f}" if b1 else "—",
            "b2":         f"{b2:,.1f}" if b2 else "—",
            "s_target":   f"{s_target:,.1f}" if s_target else "—",
            "dd":         f"{r.get('dd_pct', 0):+.1f}%",
            "pnl":        pnl_str,
            "_signal_raw":   signal,
            "_in_portfolio": sym in portfolio,
            "_avwap_dist":   avwap_dist,  # raw float for tag coloring
        })
    return rows


# ── 主視窗 ────────────────────────────────────────────────────────────────────

class TwStrategyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        # Suppress harmless "invalid command name" noise from CTk's DPI-scaling
        # after-callbacks and from popup windows closed before their thread finishes.
        _orig_rcb = self.report_callback_exception
        def _filtered_rcb(exc, val, tb):
            msg = str(val)
            if "invalid command name" in msg:
                return
            _orig_rcb(exc, val, tb)
        self.report_callback_exception = _filtered_rcb

        self.title("台股策略看盤")
        self.geometry("1440x820")
        self.minsize(1100, 620)
        self.configure(fg_color=BG)

        try:
            fam = set(tkfont.families())
            self.ui_font = ("Microsoft JhengHei UI" if "Microsoft JhengHei UI" in fam
                            else "Microsoft JhengHei")
            self.option_add("*Font", (self.ui_font, 11))
        except Exception:
            self.ui_font = "Arial"

        self._loading      = False
        self._scan_records: list[dict] = []
        self._cfg:          dict       = {}
        self._bt_btns:      dict       = {}

        self._setup_style()
        self._build_ui()
        self.after(200, self._initial_load)

    # ── ttk Style ─────────────────────────────────────────────────────────────

    def _setup_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("S.Treeview", background=BG_ROW_B, foreground=C_WHITE,
                    fieldbackground=BG_ROW_B, rowheight=28, font=(self.ui_font, 12))
        s.configure("S.Treeview.Heading", background=BG_HDR, foreground=C_BLUE,
                    font=(self.ui_font, 12, "bold"), relief="flat")
        s.map("S.Treeview", background=[("selected", "#1c4f82")],
              foreground=[("selected", C_WHITE)])
        s.layout("S.Treeview", [("S.Treeview.treearea", {"sticky": "nswe"})])

    # ── 頂層 UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.tabs = ctk.CTkTabview(
            self, fg_color=BG, corner_radius=0,
            segmented_button_fg_color=BG_PANEL,
            segmented_button_selected_color=BG_ROW_A,
            segmented_button_selected_hover_color="#1a5a9a",
            segmented_button_unselected_color=BG_PANEL,
            segmented_button_unselected_hover_color=BG_ROW_A,
        )
        self.tabs.pack(fill="both", expand=True)

        self._build_scan_tab(self.tabs.add("  📡 掃描  "))
        self._build_backtest_tab(self.tabs.add("  📈 回測  "))
        self._build_portfolio_tab(self.tabs.add("  💼 持股  "))
        self._build_accuracy_tab(self.tabs.add("  📊 準確度  "))
        self._build_signal_bt_tab(self.tabs.add("  📋 跟單回測  "))

    # ════════════════════════════════════════════════════════════════════
    # 掃描 Tab
    # ════════════════════════════════════════════════════════════════════

    def _build_scan_tab(self, tab):
        tab.configure(fg_color=BG)

        # 頂部狀態欄
        top = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=50, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        self.lbl_market = ctk.CTkLabel(top, text="載入中...",
                                       font=(self.ui_font, 13), text_color=C_GRAY)
        self.lbl_market.pack(side="left", padx=20, pady=12)

        self.lbl_time = ctk.CTkLabel(top, text="",
                                     font=(self.ui_font, 12), text_color=C_GRAY)
        self.lbl_time.pack(side="right", padx=20)

        ctk.CTkButton(top, text="⟳ 重新掃描", width=110, font=(self.ui_font, 12),
                      fg_color="#1f4e79", hover_color="#2980b9",
                      command=self._on_refresh).pack(side="right", padx=8, pady=10)
        ctk.CTkButton(top, text="📥 CSV", width=70, font=(self.ui_font, 12),
                      fg_color="#2c3e50", hover_color="#34495e",
                      command=self._export_scan_csv).pack(side="right", padx=0, pady=10)

        self.lbl_status = ctk.CTkLabel(top, text="",
                                       font=(self.ui_font, 12), text_color=C_YELLOW)
        self.lbl_status.pack(side="right", padx=4)

        # 圖例
        leg = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=30, corner_radius=0)
        leg.pack(fill="x")
        leg.pack_propagate(False)
        for txt, clr in [
            ("♦ 強力買入", C_STRONG), ("♦ 買入", C_GREEN), ("♦ 賣出", C_RED),
            ("♦ 量能注意", C_YELLOW), ("♦ 持有", C_GRAY),
            ("  試買=AVWAP×b1", C_GRAY), ("  加碼=AVWAP×b2", C_GRAY),
            ("  賣出=AVWAP×s", C_GRAY),
        ]:
            ctk.CTkLabel(leg, text=txt, font=(self.ui_font, 12),
                         text_color=clr).pack(side="left", padx=10)

        # 表格
        tbl = ctk.CTkFrame(tab, fg_color=BG, corner_radius=0)
        tbl.pack(fill="both", expand=True, padx=10, pady=(4, 4))

        self._sort_col: str | None = None
        self._sort_asc: bool = True

        self.tree = ttk.Treeview(tbl, style="S.Treeview",
                                 columns=[c[0] for c in SCAN_COLS],
                                 show="headings", selectmode="browse")
        for cid, hd, w, anc in SCAN_COLS:
            self.tree.heading(cid, text=hd,
                              command=lambda c=cid: self._sort_scan(c))
            self.tree.column(cid, width=w, anchor=anc, minwidth=40)

        sb = ttk.Scrollbar(tbl, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        _add_tree_col_tips(self.tree, _SCAN_COL_TIPS)
        self.tree.bind("<Double-1>", self._on_scan_row_dblclick)

        for tag, fg, bg in [
            ("strong_buy",    C_STRONG,  BG_ROW_A),
            ("buy",           C_GREEN,   BG_ROW_A),
            ("sell",          C_RED,     BG_ROW_B),
            ("watch",         C_YELLOW,  BG_ROW_B),
            ("hold",          C_WHITE,   BG_ROW_B),
            ("hold_alt",      C_WHITE,   BG_ROW_A),
            ("portfolio",     "#f0e68c", BG_ROW_B),
            ("portfolio_alt", "#f0e68c", BG_ROW_A),
        ]:
            self.tree.tag_configure(tag, foreground=fg, background=bg)

        # 底部摘要
        bot = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=36, corner_radius=0)
        bot.pack(fill="x")
        bot.pack_propagate(False)
        self.lbl_summary = ctk.CTkLabel(bot, text="",
                                        font=(self.ui_font, 12), text_color=C_GRAY)
        self.lbl_summary.pack(side="left", padx=20, pady=8)
        self.lbl_sector_warn = ctk.CTkLabel(bot, text="",
                                            font=(self.ui_font, 12), text_color=C_YELLOW)
        self.lbl_sector_warn.pack(side="right", padx=20, pady=8)

    # ════════════════════════════════════════════════════════════════════
    # 回測 Tab
    # ════════════════════════════════════════════════════════════════════

    def _build_backtest_tab(self, tab):
        tab.configure(fg_color=BG)

        # 工具列
        bar = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=46, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="📈 10年 DCA 回測",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE
                     ).pack(side="left", padx=16, pady=12)
        ctk.CTkLabel(bar, text="點選左側股票查看詳情",
                     font=(self.ui_font, 12), text_color=C_GRAY
                     ).pack(side="left", padx=4)

        # 主體：左右分割
        body = ctk.CTkFrame(tab, fg_color=BG, corner_radius=0)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # 左側：股票清單
        left = ctk.CTkFrame(body, fg_color=BG_PANEL, width=175, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="股票清單",
                     font=(self.ui_font, 12, "bold"), text_color=C_BLUE
                     ).pack(pady=(12, 4), padx=12, anchor="w")

        self._bt_list = ctk.CTkScrollableFrame(left, fg_color=BG_PANEL)
        self._bt_list.pack(fill="both", expand=True, padx=4, pady=4)

        # 右側：回測詳情
        right = ctk.CTkFrame(body, fg_color=BG, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew")

        self._bt_detail = ctk.CTkScrollableFrame(right, fg_color=BG)
        self._bt_detail.pack(fill="both", expand=True)

        # 載入快取 & 填充股票清單
        self._dca_cache = _load_dca_cache()
        self._bt_btns   = {}

        try:
            cfg = _load_config()
            stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]
        except Exception:
            stocks = []

        for s in stocks:
            sym, name = s["symbol"], s["name"]
            has = sym in self._dca_cache
            btn = ctk.CTkButton(
                self._bt_list,
                text=f"{sym.replace('.TW','')}  {name}",
                font=(self.ui_font, 12),
                fg_color="transparent", hover_color=BG_ROW_A,
                text_color=C_WHITE if has else C_GRAY,
                anchor="w", height=32,
                command=lambda sy=sym, nm=name: self._on_bt_select(sy, nm),
            )
            btn.pack(fill="x", pady=1, padx=2)
            self._bt_btns[sym] = btn

        self._bt_reset_hint()

    def _bt_reset_hint(self):
        for w in self._bt_detail.winfo_children():
            w.destroy()
        if not self._dca_cache:
            ctk.CTkLabel(self._bt_detail,
                         text="尚無 DCA 快取\n\n請先執行：\npython tw_scheduler.py --dca",
                         font=(self.ui_font, 13), text_color=C_YELLOW,
                         justify="center").pack(pady=60)
        else:
            ctk.CTkLabel(self._bt_detail,
                         text="← 點選左側股票查看 10年 DCA 回測",
                         font=(self.ui_font, 13), text_color=C_GRAY
                         ).pack(pady=60)

    def _on_bt_select(self, sym: str, name: str):
        for s, b in self._bt_btns.items():
            b.configure(fg_color="#1f4e79" if s == sym else "transparent")

        for w in self._bt_detail.winfo_children():
            w.destroy()

        dca = self._dca_cache.get(sym)
        if dca:
            self._bt_dca_section(dca, sym, name)
        else:
            ctk.CTkLabel(self._bt_detail,
                         text=f"⚠️ {sym.replace('.TW','')} 無 DCA 快取\n請先執行：python tw_scheduler.py --dca",
                         font=(self.ui_font, 12), text_color=C_YELLOW).pack(pady=40)

    def _bt_dca_section(self, dca: dict, sym: str, name: str):
        period  = dca.get("period", "10年")
        budget  = dca.get("annual_budget", 100_000)

        sep = ctk.CTkFrame(self._bt_detail, fg_color="#2a2a4a", height=2, corner_radius=0)
        sep.pack(fill="x", padx=12, pady=(12, 6))

        hdr_row = ctk.CTkFrame(self._bt_detail, fg_color="transparent")
        hdr_row.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(hdr_row,
                     text=f"📊 10年 DCA 回測　{period}　每年注資 NT${budget:,}",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE
                     ).pack(side="left", padx=4)
        ctk.CTkButton(hdr_row, text="📉 資產曲線",
                      font=(self.ui_font, 12), width=90, height=24,
                      fg_color="#1a3040", hover_color="#2a4a60",
                      text_color="#74b9ff",
                      command=lambda: self._show_chart_popup(sym, name, dca),
                      ).pack(side="right", padx=4)
        ctk.CTkButton(hdr_row, text="📈 敏感度",
                      font=(self.ui_font, 12), width=78, height=24,
                      fg_color="#1a3040", hover_color="#2a4a60",
                      text_color="#a8e6cf",
                      command=lambda: self._show_sensitivity_popup(sym, name, dca),
                      ).pack(side="right", padx=2)

        # ── 摘要比較表 ────────────────────────────────────────────────────────
        strategies = dca.get("strategies", [])
        if strategies:
            tbl = ctk.CTkFrame(self._bt_detail, fg_color="#0a1020", corner_radius=8)
            tbl.pack(fill="x", padx=12, pady=(0, 10))

            headers = ["策略", "CAGR", "總報酬", "獲利", "終值", "MDD"]
            col_w   = [160, 60, 70, 100, 100, 60]
            hdr_row = ctk.CTkFrame(tbl, fg_color="#0f1a30")
            hdr_row.pack(fill="x", padx=2, pady=(2, 0))
            for h, w in zip(headers, col_w):
                ctk.CTkLabel(hdr_row, text=h, font=(self.ui_font, 12, "bold"),
                             text_color="#74b9ff", width=w, anchor="center"
                             ).pack(side="left")

            bnh_cagr    = next((s.get("cagr_pct", 0) for s in strategies if "B&H" in s["label"]), 0)
            best_lbl    = max(strategies, key=lambda s: s.get("cagr_pct") or 0)["label"]
            for strat in strategies:
                lbl    = strat["label"]
                cagr   = strat.get("cagr_pct", 0)
                total  = strat.get("total_return_pct", 0)
                profit = strat.get("profit", strat.get("final_value", 0) - strat.get("total_invested", 0))
                fval   = strat.get("final_value", 0)
                mdd    = strat.get("max_drawdown_pct", 0)
                beat   = cagr > bnh_cagr and "B&H" not in lbl
                is_rec = lbl == best_lbl
                clr    = C_GREEN if beat else (C_STRONG if "B&H" in lbl else C_YELLOW)

                data_row = ctk.CTkFrame(tbl, fg_color="#0d1525" if beat else "transparent")
                data_row.pack(fill="x", padx=2, pady=1)
                short_lbl = lbl.replace("（無條件）","").replace("（趨勢股，無條件）","")
                if is_rec:
                    short_lbl = "📡 " + short_lbl
                for val, w in zip([
                    short_lbl,
                    f"{cagr:.1f}%",
                    f"{total:+.1f}%",
                    f"NT${profit:,.0f}",
                    f"NT${fval:,.0f}",
                    f"{mdd:.1f}%",
                ], col_w):
                    ctk.CTkLabel(data_row, text=val, font=(self.ui_font, 12),
                                 text_color=clr, width=w, anchor="center"
                                 ).pack(side="left")
        else:
            bnh_cagr = 0

        # B&H DCA 基準（供下方策略卡片比較用）
        bnh_strat = next((s for s in strategies if "B&H" in s["label"]), None)
        bnh_cagr  = bnh_strat["cagr_pct"] if bnh_strat else 0

        best_lbl = max(dca.get("strategies", [{"label":"","cagr_pct":0}]),
                       key=lambda s: s.get("cagr_pct") or 0)["label"]

        for strat in dca.get("strategies", []):
            cagr      = strat.get("cagr_pct", 0)
            total     = strat.get("total_return_pct", 0)
            mdd       = strat.get("max_drawdown_pct", 0)
            fval      = strat.get("final_value", 0)
            inv       = strat.get("total_invested", 0)
            ntx       = strat.get("n_transactions", 0)
            fees      = strat.get("total_fees", 0)
            div_rcv   = strat.get("div_received", 0)
            div_yield = strat.get("ann_yield_pct", 0)
            lbl       = strat["label"]
            beat      = cagr > bnh_cagr and "B&H" not in lbl
            is_rec    = lbl == best_lbl

            card = ctk.CTkFrame(self._bt_detail,
                                fg_color="#0d2d0d" if beat else "#0d1a2d",
                                corner_radius=10)
            card.pack(fill="x", padx=12, pady=4)

            flag = "✅" if beat else ("📌" if "B&H" in lbl else "⚠️")
            rec_tag = "  📡 Discord + 掃描 tab 採用此策略" if is_rec else ""
            dca_hdr = ctk.CTkFrame(card, fg_color="transparent")
            dca_hdr.pack(fill="x", padx=14, pady=(8, 4))
            ctk.CTkLabel(dca_hdr, text=f"{flag} {lbl}{rec_tag}",
                         font=(self.ui_font, 12, "bold"),
                         text_color=C_STRONG if "B&H" in lbl else (C_GREEN if beat else C_YELLOW)
                         ).pack(side="left")
            ctk.CTkButton(dca_hdr, text="ℹ 策略說明",
                          font=(self.ui_font, 12),
                          fg_color="#1a2a40", hover_color="#2a3a5a",
                          text_color="#74b9ff", width=84, height=22,
                          command=lambda l=lbl: self._strategy_info_popup(l),
                          ).pack(side="right")

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(0, 6))
            for label, val, clr in [
                ("CAGR",   f"{cagr:.1f}%",              C_GREEN if cagr >= bnh_cagr else C_YELLOW),
                ("總報酬", f"{total:+.1f}%",             C_GREEN if total > 0 else C_RED),
                ("MDD",    f"{mdd:.1f}%",                C_RED),
                ("終值",   f"NT${fval:,.0f}",            C_WHITE),
                ("注資",   f"NT${inv:,.0f}",             C_GRAY),
                ("交易次", str(ntx),                      C_GRAY),
            ]:
                col = ctk.CTkFrame(row, fg_color="transparent")
                col.pack(side="left", padx=10)
                ctk.CTkLabel(col, text=label,
                             font=(self.ui_font, 12), text_color=C_GRAY).pack()
                ctk.CTkLabel(col, text=val,
                             font=(self.ui_font, 12, "bold"), text_color=clr).pack()
                if label in _DCA_METRIC_TIPS:
                    _Tooltip(col, _DCA_METRIC_TIPS[label])

            # ── 費用 + 股息再投入 小計列 ──────────────────────────────
            info_parts = []
            if fees > 0:
                info_parts.append(f"手續費 NT${fees:,.0f}")
            if div_rcv > 0:
                info_parts.append(
                    f"股息再投入 NT${div_rcv:,.0f}（年均殖利率 {div_yield:.1f}%）")
            else:
                info_parts.append("股息資料不足（yfinance 未提供）")
            info_row = ctk.CTkFrame(card, fg_color="transparent")
            info_row.pack(fill="x", padx=14, pady=(0, 4))
            ctk.CTkLabel(info_row,
                         text="  ｜  ".join(info_parts),
                         font=(self.ui_font, 12),
                         text_color=C_GREEN if div_rcv > 0 else C_GRAY
                         ).pack(side="left")

            txs = strat.get("transactions", strat.get("last_tx", []))
            if txs:
                ctk.CTkButton(
                    card,
                    text=f"📋 展開注資明細（{len(txs)} 筆）",
                    font=(self.ui_font, 12),
                    fg_color="#1a3a60", hover_color="#2d5a8e",
                    text_color="#74b9ff", height=26,
                    command=lambda t=txs, l=lbl, fp=strat.get("final_price", 0.0): self._dca_popup(
                        t, f"{sym.replace('.TW','')} {name}", l, final_price=fp),
                ).pack(anchor="w", padx=14, pady=(2, 10))

    # ── 策略說明 ──────────────────────────────────────────────────────────────────
    _STRAT_DESC: dict[str, tuple[str, str]] = {
        "v2 STRONG BUY DCA": (
            "v2 STRONG BUY 擇時加碼（10年 DCA）",
            "觸發條件（三項同時成立）：\n"
            "  • 60日回撤 ≤ -20%\n"
            "  • 價格 < AVWAP × b2\n"
            "  • RSI ≤ rsi_sbuy（個股設定）\n\n"
            "資金邏輯：\n"
            "每年年初注入 NT$100,000，資金累積等待觸發。\n"
            "觸發當日將所有累積資金一次買入。\n"
            "若全年未觸發，資金滾入下一年。\n\n"
            "門檻更高→等待時間更長→買入時機在更深低點。",
        ),
        "v2 BUY DCA": (
            "v2 BUY 擇時加碼（10年 DCA）",
            "觸發條件（三項同時成立）：\n"
            "  • 60日回撤 ≤ -10%\n"
            "  • 價格 < AVWAP × b1\n"
            "  • RSI ≤ rsi_buy（個股設定）\n\n"
            "資金邏輯：\n"
            "每年年初注入 NT$100,000，資金累積等待觸發。\n"
            "觸發當日將所有累積資金一次買入。\n"
            "若全年未觸發，資金滾入下一年。\n\n"
            "相比 B&H，此策略等待技術面低點才買入，\n"
            "長期理論上可取得較佳的平均買入價格。",
        ),
        "市場警戒逆向加碼": (
            "市場警戒逆向加碼（10年 DCA）",
            "觸發條件（大盤進入 WARN 或 RISK 模式）：\n\n"
            "  WARN：加權指數 60日回撤 > -10%\n"
            "        或 ETF50 60日回撤 > -8%\n"
            "  RISK：加權指數 60日回撤 > -20%\n"
            "        或 ETF50 60日回撤 > -15%\n\n"
            "邏輯：\n"
            "整體市場系統性下跌時，優質個股往往被\n"
            "連帶錯殺，逆向加碼具有較高安全邊際。\n"
            "不看個股技術面，只依大盤恐慌程度決定。",
        ),
    }

    def _strategy_info_popup(self, label: str):
        import tkinter as tk

        key = next((k for k in self._STRAT_DESC if k in label), None)
        if key:
            title, body = self._STRAT_DESC[key]
        else:
            title = "B&H DCA（無條件買入）"
            body  = (
                "策略邏輯：\n"
                "  每年年初固定注入資金，無論市況直接買入。\n"
                "  完全不擇時，不看技術指標。\n\n"
                "適合場景：\n"
                "  長期持有高品質資產（如 ETF）。\n"
                "  學術研究顯示多數主動擇時策略\n"
                "  長期難以持續跑贏簡單 B&H。\n\n"
                "此策略作為其他策略的基準比較（Benchmark）。"
            )

        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("500x360")
        win.configure(bg="#0f1a30")
        win.resizable(False, False)
        win.lift()

        tk.Label(win, text=title,
                 fg="#74b9ff", bg="#0f1a30",
                 font=(self.ui_font, 13, "bold"),
                 wraplength=460, justify="left"
                 ).pack(anchor="w", padx=16, pady=(14, 6))

        tk.Frame(win, bg="#2a3a5a", height=1).pack(fill="x", padx=16, pady=(0, 10))

        tk.Label(win, text=body,
                 fg="#d8e8ff", bg="#0f1a30",
                 font=("Consolas", 12),
                 justify="left", wraplength=460
                 ).pack(anchor="w", padx=16, pady=(0, 16))

    # ════════════════════════════════════════════════════════════════════
    # 持股 Tab
    # ════════════════════════════════════════════════════════════════════

    def _build_portfolio_tab(self, tab):
        tab.configure(fg_color=BG)

        bar = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=46, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="💼 持股 P&L 總覽",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE
                     ).pack(side="left", padx=16, pady=12)
        self._pf_lbl_total = ctk.CTkLabel(bar, text="",
                                          font=(self.ui_font, 12), text_color=C_GRAY)
        self._pf_lbl_total.pack(side="left", padx=16)
        ctk.CTkButton(bar, text="⟳ 刷新", width=80, font=(self.ui_font, 12),
                      fg_color="#1f4e79", hover_color="#2980b9",
                      command=self._refresh_portfolio_tab).pack(side="right", padx=12, pady=10)

        inner = ctk.CTkTabview(tab, fg_color=BG,
                               segmented_button_fg_color=BG_PANEL,
                               segmented_button_selected_color="#1f4e79",
                               segmented_button_unselected_color=BG_PANEL,
                               segmented_button_selected_hover_color="#2980b9",
                               text_color=C_WHITE, text_color_disabled=C_GRAY)
        inner.pack(fill="both", expand=True, padx=0, pady=0)

        t1 = inner.add("📊 持股概覽")
        t2 = inner.add("📝 交易記錄")
        t1.configure(fg_color=BG)
        t2.configure(fg_color=BG)

        self._pf_frame = ctk.CTkScrollableFrame(t1, fg_color=BG)
        self._pf_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self._render_portfolio_tab()

        self._build_tracker_tab(t2)

    def _render_portfolio_tab(self, results: list[dict] | None = None):
        for w in self._pf_frame.winfo_children():
            w.destroy()

        cfg = self._cfg or _load_config()
        portfolio = {h["symbol"]: h for h in cfg.get("portfolio", [])}
        if not portfolio:
            ctk.CTkLabel(self._pf_frame, text="config.yaml 中無持股資料",
                         text_color=C_GRAY, font=(self.ui_font, 12)).pack(pady=40)
            return

        scan_price: dict[str, float] = {}
        scan_signal: dict[str, str]  = {}
        for r in (self._scan_records or []):
            s = r["symbol"]
            scan_price[s]  = r.get("price", 0)
            sigs = [x["type"] for x in r.get("signals", [])
                    if x["type"] in ("STRONG BUY","BUY","SELL","WATCH")]
            scan_signal[s] = (
                "STRONG BUY" if "STRONG BUY" in sigs else
                "BUY"        if "BUY"        in sigs else
                "SELL"       if "SELL"       in sigs else
                "WATCH"      if "WATCH"      in sigs else "—"
            )

        from datetime import date as _date
        today = _date.today()

        total_cost   = 0.0
        total_value  = 0.0
        total_pnl    = 0.0

        # 標題列
        hdr = ctk.CTkFrame(self._pf_frame, fg_color="#0a1020", corner_radius=6)
        hdr.pack(fill="x", pady=(0, 4))
        for txt, w in [("代號/名稱", 160), ("股數", 70), ("成本", 80), ("現價", 80),
                        ("市值", 100), ("損益 NT$", 110), ("損益%", 80),
                        ("持有天數", 80), ("信號", 90), ("備註", 100)]:
            ctk.CTkLabel(hdr, text=txt, font=(self.ui_font, 12, "bold"),
                         text_color="#74b9ff", width=w, anchor="center").pack(side="left", padx=2, pady=4)

        for h in cfg.get("portfolio", []):
            sym    = h["symbol"]
            cost   = h.get("cost", 0)
            shares = h.get("shares", 0)
            price  = scan_price.get(sym, 0)
            sig    = scan_signal.get(sym, "—")
            note   = h.get("note", "")

            # 持有天數
            buy_date_str = h.get("buy_date", "")
            hold_days = "—"
            if buy_date_str:
                try:
                    bd = _date.fromisoformat(buy_date_str)
                    hold_days = str((today - bd).days)
                except Exception:
                    pass

            mkt_val = round(price * shares, 0) if price else 0
            pnl     = round((price - cost) * shares, 0) if price and cost > 0 else 0
            pnl_pct = round((price - cost) / cost * 100, 1) if price and cost > 0 else None

            if cost > 0:
                total_cost  += cost * shares
                total_value += mkt_val
                total_pnl   += pnl

            pnl_str = f"{'+' if pnl>=0 else ''}{int(pnl):,}" if pnl_pct is not None else "配股"
            pct_str = f"{pnl_pct:+.1f}%" if pnl_pct is not None else "—"

            is_profit = pnl_pct is not None and pnl_pct >= 0
            row_bg = "#0d2d0d" if is_profit else "#2d0d0d"
            row = ctk.CTkFrame(self._pf_frame, fg_color=row_bg, corner_radius=6)
            row.pack(fill="x", pady=2)

            sig_color = {
                "STRONG BUY": C_STRONG, "BUY": C_GREEN,
                "SELL": C_RED, "WATCH": C_YELLOW,
            }.get(sig, C_GRAY)
            pnl_clr = C_GREEN if is_profit else (C_RED if pnl_pct is not None else C_GRAY)

            for val, w, clr in [
                (f"{sym.replace('.TW','')} {h.get('name','')}", 160, C_WHITE),
                (str(shares),         70,  C_GRAY),
                (f"{cost:.2f}" if cost else "配股", 80, C_GRAY),
                (f"{price:.1f}" if price else "—", 80, C_WHITE),
                (f"NT${int(mkt_val):,}" if mkt_val else "—", 100, C_WHITE),
                (pnl_str,              110, pnl_clr),
                (pct_str,              80,  pnl_clr),
                (hold_days + " 天" if hold_days != "—" else "—", 80, C_GRAY),
                (sig,                  90,  sig_color),
                (note[:10],            100, C_GRAY),
            ]:
                ctk.CTkLabel(row, text=val, font=(self.ui_font, 12),
                             text_color=clr, width=w, anchor="center").pack(side="left", padx=2, pady=5)

        # 合計列
        if total_cost > 0:
            tot_pct = round(total_pnl / total_cost * 100, 1)
            pnl_clr = C_GREEN if total_pnl >= 0 else C_RED
            foot = ctk.CTkFrame(self._pf_frame, fg_color="#0a1020", corner_radius=6)
            foot.pack(fill="x", pady=(6, 0))
            ctk.CTkLabel(foot, text="合計",
                         font=(self.ui_font, 12, "bold"), text_color=C_BLUE,
                         width=160, anchor="center").pack(side="left", padx=2, pady=6)
            for _ in range(3):
                ctk.CTkLabel(foot, text="", width=80).pack(side="left")
            ctk.CTkLabel(foot, text=f"NT${int(total_value):,}",
                         font=(self.ui_font, 12, "bold"), text_color=C_WHITE,
                         width=100, anchor="center").pack(side="left")
            ctk.CTkLabel(foot, text=f"{'+' if total_pnl>=0 else ''}{int(total_pnl):,}",
                         font=(self.ui_font, 12, "bold"), text_color=pnl_clr,
                         width=110, anchor="center").pack(side="left")
            ctk.CTkLabel(foot, text=f"{tot_pct:+.1f}%",
                         font=(self.ui_font, 12, "bold"), text_color=pnl_clr,
                         width=80, anchor="center").pack(side="left")
            self._pf_lbl_total.configure(
                text=f"總市值 NT${int(total_value):,}　未實現損益 NT${'+' if total_pnl>=0 else ''}{int(total_pnl):,}（{tot_pct:+.1f}%）",
                text_color=pnl_clr)

    def _refresh_portfolio_tab(self):
        if self._loading:
            return
        self._loading = True
        threading.Thread(target=self._bg_pf_refresh, daemon=True).start()

    def _bg_pf_refresh(self):
        try:
            from tw_screener import run_scan
            records = run_scan()
            self._scan_records = records
            self.after(0, lambda: self._render(records))
            self.after(0, self._render_portfolio_tab)
        except Exception as e:
            self.after(0, lambda: self._pf_lbl_total.configure(text=f"刷新失敗：{e}", text_color=C_RED))
        finally:
            self._loading = False

    # ════════════════════════════════════════════════════════════════════
    # 持倉追蹤 Tracker
    # ════════════════════════════════════════════════════════════════════

    def _build_tracker_tab(self, tab):
        import tkinter as _tk
        from tkinter import ttk as _ttk

        toolbar = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=40, corner_radius=0)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)
        ctk.CTkButton(toolbar, text="+ 新增持倉", width=100, font=(self.ui_font, 12),
                      fg_color="#1e6b2e", hover_color="#27ae60",
                      command=self._portfolio_add_popup).pack(side="left", padx=8, pady=6)
        ctk.CTkButton(toolbar, text="⟳ 刷新報價", width=100, font=(self.ui_font, 12),
                      fg_color="#1f4e79", hover_color="#2980b9",
                      command=self._portfolio_refresh_trades).pack(side="left", padx=4, pady=6)
        ctk.CTkButton(toolbar, text="📥 CSV", width=70, font=(self.ui_font, 12),
                      fg_color="#2c3e50", hover_color="#34495e",
                      command=self._export_trades_csv).pack(side="left", padx=4, pady=6)
        self._tracker_status = ctk.CTkLabel(toolbar, text="雙擊→平倉  右鍵→刪除",
                                            font=(self.ui_font, 12), text_color=C_GRAY)
        self._tracker_status.pack(side="left", padx=14)

        body = ctk.CTkScrollableFrame(tab, fg_color=BG)
        body.pack(fill="both", expand=True, padx=6, pady=6)

        # ttk style (shared, idempotent)
        style = _ttk.Style()
        style.theme_use("default")
        style.configure("Tracker.Treeview",
                         background="#0d1b2a", fieldbackground="#0d1b2a",
                         foreground=C_WHITE, rowheight=26, font=(self.ui_font, 12))
        style.configure("Tracker.Treeview.Heading",
                         background="#0a1020", foreground="#74b9ff",
                         font=(self.ui_font, 12, "bold"), relief="flat")
        style.map("Tracker.Treeview", background=[("selected", "#1f4e79")])

        # ── 未平倉 ──
        ctk.CTkLabel(body, text="未平倉持股", font=(self.ui_font, 12, "bold"),
                     text_color=C_BLUE).pack(anchor="w", padx=8, pady=(6, 2))
        open_frame = ctk.CTkFrame(body, fg_color=BG_PANEL, corner_radius=6)
        open_frame.pack(fill="x", padx=4, pady=4)

        open_cols   = ("代號", "名稱", "買入日", "買入價", "股數",
                        "成本含費", "現價", "市值", "損益", "損益%", "備註")
        open_widths = (65, 100, 90, 72, 55, 100, 72, 100, 95, 70, 110)
        self._tracker_open_tree = _ttk.Treeview(
            open_frame, columns=open_cols, show="headings",
            height=7, style="Tracker.Treeview")
        for col, w in zip(open_cols, open_widths):
            self._tracker_open_tree.heading(col, text=col)
            self._tracker_open_tree.column(col, width=w, anchor="center", minwidth=40)
        self._tracker_open_tree.pack(fill="x", padx=4, pady=(4, 2))
        self._tracker_open_tree.bind("<Double-1>", self._on_tracker_open_dblclick)

        # right-click menu — open
        self._open_ctx = _tk.Menu(self, tearoff=0,
                                   bg="#0d1b2a", fg=C_WHITE,
                                   activebackground="#1f4e79", activeforeground=C_WHITE)
        self._open_ctx.add_command(label="平倉", command=self._ctx_close_trade)
        self._open_ctx.add_separator()
        self._open_ctx.add_command(label="刪除記錄", command=self._ctx_delete_open_trade)
        self._tracker_open_tree.bind("<Button-3>", self._show_open_ctx)

        # ── 已平倉 ──
        ctk.CTkLabel(body, text="已平倉記錄", font=(self.ui_font, 12, "bold"),
                     text_color="#f39c12").pack(anchor="w", padx=8, pady=(14, 2))
        closed_frame = ctk.CTkFrame(body, fg_color=BG_PANEL, corner_radius=6)
        closed_frame.pack(fill="x", padx=4, pady=4)

        closed_cols   = ("代號", "名稱", "買入日", "賣出日",
                          "買入價", "賣出價", "股數", "損益", "損益%", "備註")
        closed_widths = (65, 100, 90, 90, 72, 72, 55, 95, 70, 110)
        self._tracker_closed_tree = _ttk.Treeview(
            closed_frame, columns=closed_cols, show="headings",
            height=7, style="Tracker.Treeview")
        for col, w in zip(closed_cols, closed_widths):
            self._tracker_closed_tree.heading(col, text=col)
            self._tracker_closed_tree.column(col, width=w, anchor="center", minwidth=40)
        self._tracker_closed_tree.pack(fill="x", padx=4, pady=(4, 2))

        # right-click menu — closed
        self._closed_ctx = _tk.Menu(self, tearoff=0,
                                     bg="#0d1b2a", fg=C_WHITE,
                                     activebackground="#1f4e79", activeforeground=C_WHITE)
        self._closed_ctx.add_command(label="刪除記錄", command=self._ctx_delete_closed_trade)
        self._tracker_closed_tree.bind("<Button-3>", self._show_closed_ctx)

        # load initial data (no live prices yet)
        self._portfolio_load_trees(prices={})

    def _portfolio_load_trees(self, prices: dict):
        import tw_portfolio as pf
        trades = pf.load_trades()

        for iid in self._tracker_open_tree.get_children():
            self._tracker_open_tree.delete(iid)
        for iid in self._tracker_closed_tree.get_children():
            self._tracker_closed_tree.delete(iid)

        for t in pf.get_open(trades):
            sym = t["symbol"].replace(".TW", "")
            price = prices.get(sym, 0.0) or prices.get(t["symbol"].upper(), 0.0)
            cost_total = t["buy_price"] * t["shares"] + t["buy_commission"]
            tp = t.get("target_price")
            sl = t.get("stop_price")
            if price > 0:
                p = pf.calc_open_pnl(t, price)
                pnl = int(p["pnl"])
                mkt_val = int(p["market_value"])
                price_str = f"{price:.1f}"
                mkt_str   = f"NT${mkt_val:,}"
                pnl_str   = f"{'+' if pnl>=0 else ''}{pnl:,}"
                pct_str   = f"{p['pnl_pct']:+.1f}%"
                # alert tags take priority
                if tp and price >= tp:
                    tag = "target_hit"
                elif sl and price <= sl:
                    tag = "stop_hit"
                else:
                    tag = "profit" if pnl >= 0 else "loss"
            else:
                price_str = "—"
                mkt_str   = "—"
                pnl_str   = "—"
                pct_str   = "—"
                tag = "neutral"

            note_disp = t.get("note", "")
            if tp:
                note_disp = f"停利{tp}" + (f" {note_disp}" if note_disp else "")
            if sl:
                note_disp = f"停損{sl} " + note_disp

            self._tracker_open_tree.insert(
                "", "end", iid=str(t["id"]),
                values=(sym, t["name"], t["buy_date"], f"{t['buy_price']:.2f}",
                        t["shares"], f"NT${int(cost_total):,}",
                        price_str, mkt_str, pnl_str, pct_str, note_disp),
                tags=(tag,))

        self._tracker_open_tree.tag_configure("profit",     foreground=C_GREEN)
        self._tracker_open_tree.tag_configure("loss",       foreground=C_RED)
        self._tracker_open_tree.tag_configure("neutral",    foreground=C_WHITE)
        self._tracker_open_tree.tag_configure("target_hit", foreground="#f1c40f", background="#2d2800")
        self._tracker_open_tree.tag_configure("stop_hit",   foreground="#e67e22", background="#2d0a00")

        for t in pf.get_closed(trades):
            sym = t["symbol"].replace(".TW", "")
            p   = pf.calc_closed_pnl(t)
            pnl = int(p["pnl"])
            self._tracker_closed_tree.insert(
                "", "end", iid=str(t["id"]),
                values=(sym, t["name"], t["buy_date"], t["sell_date"],
                        f"{t['buy_price']:.2f}", f"{t['sell_price']:.2f}",
                        t["shares"],
                        f"{'+' if pnl>=0 else ''}{pnl:,}",
                        f"{p['pnl_pct']:+.1f}%", t.get("note", "")),
                tags=("profit" if pnl >= 0 else "loss",))

        self._tracker_closed_tree.tag_configure("profit", foreground=C_GREEN)
        self._tracker_closed_tree.tag_configure("loss",   foreground=C_RED)

    def _portfolio_refresh_trades(self):
        self._tracker_status.configure(text="抓取報價中…", text_color=C_YELLOW)
        import tw_portfolio as pf

        def _bg():
            trades = pf.load_trades()
            syms   = [t["symbol"].replace(".TW", "") for t in pf.get_open(trades)]
            prices = pf.fetch_prices(syms)
            if self.winfo_exists():
                self.after(0, lambda: self._on_tracker_prices(prices))

        threading.Thread(target=_bg, daemon=True).start()

    def _on_tracker_prices(self, prices: dict):
        self._portfolio_load_trees(prices)
        # check alerts
        import tw_portfolio as pf
        alerts = []
        for t in pf.get_open():
            sym = t["symbol"].replace(".TW", "")
            price = prices.get(sym, 0.0)
            if not price:
                continue
            tp = t.get("target_price")
            sl = t.get("stop_price")
            if tp and price >= tp:
                alerts.append(f"⭐ {sym} 達停利 {tp}")
            elif sl and price <= sl:
                alerts.append(f"🔴 {sym} 觸停損 {sl}")
        if alerts:
            self._tracker_status.configure(
                text="  ".join(alerts), text_color=C_YELLOW)
        else:
            self._tracker_status.configure(
                text=f"已更新 {datetime.now().strftime('%H:%M:%S')}", text_color=C_GRAY)

    def _portfolio_add_popup(self):
        win = ctk.CTkToplevel(self)
        win.title("新增持倉")
        win.geometry("400x370")
        win.configure(fg_color=BG)
        win.grab_set()

        ctk.CTkLabel(win, text="新增持倉記錄",
                     font=(self.ui_font, 14, "bold"), text_color=C_BLUE).pack(pady=(16, 8))

        form = ctk.CTkFrame(win, fg_color=BG_PANEL, corner_radius=8)
        form.pack(fill="x", padx=20, pady=4)

        field_defs = [
            ("代號 (eg. 2330)",       "symbol",       ""),
            ("名稱",                   "name",         ""),
            ("買入日 (YYYY-MM-DD)",   "buy_date",     datetime.now().strftime("%Y-%m-%d")),
            ("買入價",                 "buy_price",    ""),
            ("股數",                   "shares",       ""),
            ("停利目標價（選填）",    "target_price", ""),
            ("停損價（選填）",        "stop_price",   ""),
            ("備註",                   "note",         ""),
        ]
        entries = {}
        for label, key, default in field_defs:
            row = ctk.CTkFrame(form, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=5)
            ctk.CTkLabel(row, text=label, font=(self.ui_font, 12),
                         text_color=C_GRAY, width=180, anchor="w").pack(side="left")
            e = ctk.CTkEntry(row, font=(self.ui_font, 12), width=160)
            e.insert(0, default)
            e.pack(side="left")
            entries[key] = e

        err_lbl = ctk.CTkLabel(win, text="", font=(self.ui_font, 12), text_color=C_RED)
        err_lbl.pack(pady=4)

        def _submit():
            try:
                sym       = entries["symbol"].get().strip().upper()
                name      = entries["name"].get().strip()
                buy_date  = entries["buy_date"].get().strip()
                buy_price = float(entries["buy_price"].get().strip())
                shares    = int(entries["shares"].get().strip())
                note      = entries["note"].get().strip()
                tp_str    = entries["target_price"].get().strip()
                sl_str    = entries["stop_price"].get().strip()
                target_price = float(tp_str) if tp_str else 0.0
                stop_price   = float(sl_str) if sl_str else 0.0
                if not sym or not name or not buy_date:
                    err_lbl.configure(text="代號 / 名稱 / 日期不能空白")
                    return
                import tw_portfolio as pf
                pf.add_trade(sym, name, buy_date, buy_price, shares, note,
                             target_price=target_price, stop_price=stop_price)
                self._portfolio_load_trees(prices={})
                win.destroy()
            except ValueError as ex:
                err_lbl.configure(text=f"格式錯誤: {ex}")

        ctk.CTkButton(win, text="確認新增", font=(self.ui_font, 12),
                      fg_color="#1e6b2e", hover_color="#27ae60",
                      command=_submit).pack(pady=12)

    def _on_tracker_open_dblclick(self, _event):
        sel = self._tracker_open_tree.selection()
        if not sel:
            return
        trade_id = int(sel[0])
        import tw_portfolio as pf
        trade = next((t for t in pf.load_trades() if t["id"] == trade_id), None)
        if trade:
            self._portfolio_close_popup(trade)

    def _portfolio_close_popup(self, trade: dict):
        win = ctk.CTkToplevel(self)
        win.title("平倉")
        win.geometry("360x260")
        win.configure(fg_color=BG)
        win.grab_set()

        sym = trade["symbol"].replace(".TW", "")
        ctk.CTkLabel(win, text=f"平倉 — {sym} {trade['name']}",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE).pack(pady=(16, 4))
        ctk.CTkLabel(win, text=f"買入 {trade['buy_date']}  @{trade['buy_price']}  ×{trade['shares']} 股",
                     font=(self.ui_font, 12), text_color=C_GRAY).pack()

        form = ctk.CTkFrame(win, fg_color=BG_PANEL, corner_radius=8)
        form.pack(fill="x", padx=20, pady=14)

        entries = {}
        for label, key, default in [
            ("賣出日 (YYYY-MM-DD)", "sell_date",  datetime.now().strftime("%Y-%m-%d")),
            ("賣出價",              "sell_price", ""),
        ]:
            row = ctk.CTkFrame(form, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=8)
            ctk.CTkLabel(row, text=label, font=(self.ui_font, 12),
                         text_color=C_GRAY, width=180, anchor="w").pack(side="left")
            e = ctk.CTkEntry(row, font=(self.ui_font, 12), width=140)
            e.insert(0, default)
            e.pack(side="left")
            entries[key] = e

        err_lbl = ctk.CTkLabel(win, text="", font=(self.ui_font, 12), text_color=C_RED)
        err_lbl.pack()

        def _submit():
            try:
                sd = entries["sell_date"].get().strip()
                sp = float(entries["sell_price"].get().strip())
                import tw_portfolio as pf
                pf.close_trade(trade["id"], sd, sp)
                self._portfolio_load_trees(prices={})
                win.destroy()
            except ValueError as ex:
                err_lbl.configure(text=f"格式錯誤: {ex}")

        ctk.CTkButton(win, text="確認平倉", font=(self.ui_font, 12),
                      fg_color="#8b1a1a", hover_color="#c0392b",
                      command=_submit).pack(pady=12)

    # ── context menu helpers ──

    def _show_open_ctx(self, event):
        row = self._tracker_open_tree.identify_row(event.y)
        if row:
            self._tracker_open_tree.selection_set(row)
            self._open_ctx.post(event.x_root, event.y_root)

    def _ctx_close_trade(self):
        self._on_tracker_open_dblclick(None)

    def _ctx_delete_open_trade(self):
        sel = self._tracker_open_tree.selection()
        if not sel:
            return
        import tw_portfolio as pf
        pf.delete_trade(int(sel[0]))
        self._portfolio_load_trees(prices={})

    def _show_closed_ctx(self, event):
        row = self._tracker_closed_tree.identify_row(event.y)
        if row:
            self._tracker_closed_tree.selection_set(row)
            self._closed_ctx.post(event.x_root, event.y_root)

    def _ctx_delete_closed_trade(self):
        sel = self._tracker_closed_tree.selection()
        if not sel:
            return
        import tw_portfolio as pf
        pf.delete_trade(int(sel[0]))
        self._portfolio_load_trees(prices={})

    # ════════════════════════════════════════════════════════════════════
    # 準確度 Tab
    # ════════════════════════════════════════════════════════════════════

    def _build_accuracy_tab(self, tab):
        tab.configure(fg_color=BG)

        bar = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=46, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="📊 信號準確度（事後驗證）",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE
                     ).pack(side="left", padx=16, pady=12)
        ctk.CTkLabel(bar, text="信號發出後 5 個交易日評分",
                     font=(self.ui_font, 12), text_color=C_GRAY
                     ).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="⟳ 刷新", width=80, font=(self.ui_font, 12),
                      fg_color="#1f4e79", hover_color="#2980b9",
                      command=self._refresh_accuracy_tab).pack(side="right", padx=12, pady=10)

        self._acc_frame = ctk.CTkScrollableFrame(tab, fg_color=BG)
        self._acc_frame.pack(fill="both", expand=True, padx=10, pady=8)

        self._render_accuracy_tab()

    def _render_accuracy_tab(self):
        for w in self._acc_frame.winfo_children():
            w.destroy()

        try:
            from tw_outcome import compute_rolling_accuracy, load_recent_outcomes
            stats30  = compute_rolling_accuracy(30)
            stats60  = compute_rolling_accuracy(60)
            recent   = load_recent_outcomes(15)
        except Exception as e:
            ctk.CTkLabel(self._acc_frame, text=f"讀取準確度資料失敗：{e}",
                         text_color=C_RED, font=(self.ui_font, 12)).pack(pady=40)
            return

        if not stats30:
            ctk.CTkLabel(self._acc_frame,
                         text="尚無事後驗證資料\n\n每次掃描後約 5 個交易日會自動產生驗證結果",
                         text_color=C_YELLOW, font=(self.ui_font, 12),
                         justify="center").pack(pady=60)
            return

        # ── 滾動準確率比較表 ────────────────────────────────────────────
        ctk.CTkLabel(self._acc_frame, text="滾動準確率比較",
                     font=(self.ui_font, 12, "bold"), text_color=C_BLUE
                     ).pack(anchor="w", padx=4, pady=(4, 2))

        tbl = ctk.CTkFrame(self._acc_frame, fg_color="#0a1020", corner_radius=8)
        tbl.pack(fill="x", pady=(0, 12))

        hdr = ctk.CTkFrame(tbl, fg_color="#0f1a30")
        hdr.pack(fill="x", padx=2, pady=(2, 0))
        for txt, w in [("信號類型", 130), ("近30日 正確/總數", 130), ("近30日 準確率", 110),
                        ("近30日 平均報酬", 120), ("近60日 準確率", 110), ("近60日 平均報酬", 120)]:
            ctk.CTkLabel(hdr, text=txt, font=(self.ui_font, 12, "bold"),
                         text_color="#74b9ff", width=w, anchor="center").pack(side="left", padx=2, pady=4)

        for sig in ("STRONG BUY", "BUY", "SELL"):
            v30 = (stats30 or {}).get("signals", {}).get(sig, {})
            v60 = (stats60 or {}).get("signals", {}).get(sig, {})
            if not v30:
                continue
            acc30 = v30.get("accuracy")
            avg30 = v30.get("avg_pct")
            acc60 = v60.get("accuracy") if v60 else None
            avg60 = v60.get("avg_pct")  if v60 else None

            acc_clr = C_GREEN if acc30 and acc30 >= 0.6 else (C_YELLOW if acc30 and acc30 >= 0.4 else C_RED)
            dr = ctk.CTkFrame(tbl, fg_color="transparent")
            dr.pack(fill="x", padx=2, pady=1)
            for val, w, clr in [
                (sig,                                                   130, C_WHITE),
                (f"{v30.get('correct',0)}/{v30.get('total',0)}",        130, C_GRAY),
                (f"{acc30:.0%}" if acc30 is not None else "—",          110, acc_clr),
                (f"{avg30:+.2f}%" if avg30 is not None else "—",        120, C_GREEN if avg30 and avg30>0 else C_RED),
                (f"{acc60:.0%}" if acc60 is not None else "—",          110, C_GRAY),
                (f"{avg60:+.2f}%" if avg60 is not None else "—",        120, C_GRAY),
            ]:
                ctk.CTkLabel(dr, text=val, font=(self.ui_font, 12),
                             text_color=clr, width=w, anchor="center").pack(side="left", padx=2)

        # ── 個股準確率排行 ────────────────────────────────────────────────
        ctk.CTkLabel(self._acc_frame, text="個股準確率（近 30 日，按 BUY/SBUY 正確率排序）",
                     font=(self.ui_font, 12, "bold"), text_color=C_BLUE
                     ).pack(anchor="w", padx=4, pady=(8, 2))

        stock_acc: dict = {}
        for o in load_recent_outcomes(30):
            for sym, r in o.get("stock_results", {}).items():
                if r.get("correct") is None:
                    continue
                if sym not in stock_acc:
                    stock_acc[sym] = {"name": r.get("name",""), "c": 0, "t": 0, "pnl": []}
                stock_acc[sym]["t"] += 1
                stock_acc[sym]["c"] += int(r["correct"])
                if r.get("actual_pct") is not None:
                    stock_acc[sym]["pnl"].append(r["actual_pct"])

        if stock_acc:
            stk_row = ctk.CTkFrame(self._acc_frame, fg_color="#0a1020", corner_radius=6)
            stk_row.pack(fill="x", pady=(0, 10))
            hdr2 = ctk.CTkFrame(stk_row, fg_color="#0f1a30")
            hdr2.pack(fill="x", padx=2, pady=(2,0))
            for txt, w in [("代號", 70), ("名稱", 100), ("正確/總數", 90), ("正確率", 80), ("平均報酬", 90)]:
                ctk.CTkLabel(hdr2, text=txt, font=(self.ui_font, 12, "bold"),
                             text_color="#74b9ff", width=w, anchor="center").pack(side="left", padx=2, pady=3)
            for sym, v in sorted(stock_acc.items(), key=lambda x: -(x[1]["c"]/max(x[1]["t"],1))):
                acc = v["c"] / v["t"]
                avg = sum(v["pnl"]) / len(v["pnl"]) if v["pnl"] else None
                aclr = C_GREEN if acc >= 0.6 else (C_YELLOW if acc >= 0.4 else C_RED)
                dr2 = ctk.CTkFrame(stk_row, fg_color="transparent")
                dr2.pack(fill="x", padx=2, pady=1)
                for val, w, clr in [
                    (sym.replace(".TW",""),        70, C_WHITE),
                    (v["name"][:8],               100, C_GRAY),
                    (f"{v['c']}/{v['t']}",         90, C_GRAY),
                    (f"{acc:.0%}",                 80, aclr),
                    (f"{avg:+.2f}%" if avg else "—", 90, C_GREEN if avg and avg>0 else C_RED),
                ]:
                    ctk.CTkLabel(dr2, text=val, font=("Consolas", 12),
                                 text_color=clr, width=w, anchor="center").pack(side="left", padx=2)

        # ── 近期逐筆驗證記錄 ─────────────────────────────────────────────
        ctk.CTkLabel(self._acc_frame, text="近期驗證記錄（最新 15 筆信號日）",
                     font=(self.ui_font, 12, "bold"), text_color=C_BLUE
                     ).pack(anchor="w", padx=4, pady=(8, 2))

        if not recent:
            ctk.CTkLabel(self._acc_frame, text="無記錄", text_color=C_GRAY,
                         font=(self.ui_font, 12)).pack(anchor="w", padx=8)
            return

        rec_hdr = ctk.CTkFrame(self._acc_frame, fg_color="#0f1a30", corner_radius=6)
        rec_hdr.pack(fill="x", pady=(0, 2))
        for txt, w in [("信號日", 100), ("看N日後", 70), ("代號", 70),
                        ("信號", 100), ("信號價", 85), ("報酬%", 75), ("結果", 60)]:
            ctk.CTkLabel(rec_hdr, text=txt, font=(self.ui_font, 12, "bold"),
                         text_color="#74b9ff", width=w, anchor="center").pack(side="left", padx=2, pady=3)

        for outcome in sorted(recent, key=lambda x: x.get("date",""), reverse=True):
            sig_date  = outcome.get("date", "")
            look      = outcome.get("look_ahead", 5)
            for sym, rec in outcome.get("stock_results", {}).items():
                pct     = rec.get("actual_pct") or 0.0
                correct = rec.get("correct", False)
                sig     = rec.get("signal", "")
                entry_p = rec.get("entry_price")
                r_clr   = C_GREEN if correct else C_RED
                row = ctk.CTkFrame(self._acc_frame, fg_color="transparent")
                row.pack(fill="x")
                for val, w, clr in [
                    (sig_date,                       100, C_GRAY),
                    (f"+{look}日",                    70, C_GRAY),
                    (sym.replace(".TW",""),            70, C_WHITE),
                    (sig,                             100, C_STRONG if sig=="STRONG BUY" else C_GREEN if sig=="BUY" else C_RED),
                    (f"NT${entry_p:.0f}" if entry_p else "—", 85, C_GRAY),
                    (f"{pct:+.1f}%",                  75, C_GREEN if pct>0 else C_RED),
                    ("✅" if correct else "❌",         60, r_clr),
                ]:
                    ctk.CTkLabel(row, text=val, font=("Consolas", 12),
                                 text_color=clr, width=w, anchor="center").pack(side="left", padx=2)

    def _refresh_accuracy_tab(self):
        threading.Thread(target=lambda: self.after(0, self._render_accuracy_tab), daemon=True).start()

    # ════════════════════════════════════════════════════════════════════
    # DCA 資產曲線圖
    # ════════════════════════════════════════════════════════════════════

    def _show_chart_popup(self, sym: str, name: str, dca: dict):
        import tkinter as tk
        win = tk.Toplevel(self)
        win.title(f"{sym.replace('.TW','')} {name}  資產曲線")
        win.geometry("900x560")
        win.configure(bg="#0f1a30")
        win.lift()

        lbl = tk.Label(win, text="載入價格資料中…",
                       fg="#74b9ff", bg="#0f1a30",
                       font=(self.ui_font, 12))
        lbl.pack(expand=True)

        def _bg():
            try:
                import yfinance as yf
                import pandas as pd
                from tw_backtest_dca import START_YEAR, END_YEAR, ANNUAL_BUDGET

                ticker = yf.Ticker(sym)
                df = ticker.history(start=f"{START_YEAR}-01-01",
                                    end=f"{END_YEAR}-12-31",
                                    auto_adjust=True)
                if df.empty or len(df) < 100:
                    self.after(0, lambda: lbl.configure(text="資料不足，無法繪圖"))
                    return

                from zoneinfo import ZoneInfo
                tz = ZoneInfo("Asia/Taipei")
                if df.index.tzinfo is None:
                    df.index = df.index.tz_localize(tz)
                else:
                    df.index = df.index.tz_convert(tz)

                close = df["Close"].dropna()
                years = range(START_YEAR, END_YEAR + 1)
                first_td: dict = {}
                for yr in years:
                    yd = close[close.index.year == yr]
                    if not yd.empty:
                        first_td[yr] = yd.index[0].date().isoformat()

                strategies = dca.get("strategies", [])
                curves: dict[str, pd.Series] = {}
                for strat in strategies:
                    txs = strat.get("transactions", [])
                    tx_map = {t["date"]: t for t in txs}
                    shares  = 0.0
                    cash    = 0.0
                    pv_vals = []
                    pv_idx  = []
                    for dt, price in close.items():
                        yr       = dt.year
                        date_str = dt.date().isoformat()
                        if date_str == first_td.get(yr):
                            cash += ANNUAL_BUDGET
                        if date_str in tx_map:
                            t = tx_map[date_str]
                            shares += t["shares"]
                            cash   -= t["cost"]
                        pv_vals.append(shares * float(price) + cash)
                        pv_idx.append(dt)
                    curves[strat["label"]] = pd.Series(pv_vals, index=pv_idx)

                self.after(0, lambda: _draw(win, lbl, curves, close, dca))
            except Exception as e:
                self.after(0, lambda: lbl.configure(text=f"繪圖失敗：{e}", fg="#e74c3c"))

        def _draw(win, lbl, curves, close, dca):
            try:
                import matplotlib
                matplotlib.use("TkAgg")
                import matplotlib.pyplot as plt
                from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
                import matplotlib.ticker as mticker

                lbl.destroy()
                fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 5.5),
                                               gridspec_kw={"height_ratios": [3, 1]},
                                               facecolor="#0f1a30")
                fig.subplots_adjust(hspace=0.08, left=0.09, right=0.98, top=0.93, bottom=0.08)

                colors = ["#74b9ff", "#2ecc71", "#1abc9c", "#f39c12", "#e74c3c"]
                for i, (label, series) in enumerate(curves.items()):
                    short = label.replace("（無條件）","").replace("（趨勢股，無條件）","")
                    ax1.plot(series.index, series / 1000, label=short,
                             color=colors[i % len(colors)], linewidth=1.5)

                strats = dca.get("strategies", [])
                for strat in strats:
                    for tx in strat.get("transactions", []):
                        if not tx.get("fallback"):
                            try:
                                import pandas as pd
                                tx_dt = pd.Timestamp(tx["date"], tz=close.index.tzinfo)
                                if tx_dt in curves.get(strat["label"], pd.Series()).index:
                                    y_val = curves[strat["label"]][tx_dt] / 1000
                                    ax1.axvline(tx_dt, color="#636e72", linewidth=0.4, alpha=0.5)
                            except Exception:
                                pass

                ax1.set_facecolor("#0d1b2a")
                ax1.tick_params(colors="#95a5a6", labelsize=9)
                ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"NT${x:.0f}K"))
                ax1.legend(fontsize=8, facecolor="#1a2a40", labelcolor="#ecf0f1",
                           loc="upper left", framealpha=0.8)
                ax1.set_title(f"{sym.replace('.TW','')} {name}  資產曲線（NT$ 千元）",
                              color="#74b9ff", fontsize=11)
                ax1.grid(color="#1e3a5f", linewidth=0.4)
                ax1.spines[:].set_color("#2a3a5a")
                ax1.tick_params(labelbottom=False)

                ax2.fill_between(close.index,
                                 (close / close.rolling(60).max() - 1) * 100,
                                 0, alpha=0.4, color="#e74c3c")
                ax2.axhline(y=-10, color="#f39c12", linewidth=0.6, linestyle="--")
                ax2.axhline(y=-20, color="#e74c3c", linewidth=0.6, linestyle="--")
                ax2.set_facecolor("#0d1b2a")
                ax2.set_ylabel("DD%", color="#95a5a6", fontsize=8)
                ax2.tick_params(colors="#95a5a6", labelsize=8)
                ax2.grid(color="#1e3a5f", linewidth=0.3)
                ax2.spines[:].set_color("#2a3a5a")

                canvas = FigureCanvasTkAgg(fig, master=win)
                canvas.draw()
                canvas.get_tk_widget().pack(fill="both", expand=True)
            except ImportError:
                lbl_new = __import__("tkinter").Label(
                    win, text="需安裝 matplotlib：pip install matplotlib",
                    fg="#f39c12", bg="#0f1a30", font=(self.ui_font, 12))
                lbl.destroy()
                lbl_new.pack(expand=True)

        threading.Thread(target=_bg, daemon=True).start()

    # ════════════════════════════════════════════════════════════════════
    # DCA 敏感度分析
    # ════════════════════════════════════════════════════════════════════

    def _show_sensitivity_popup(self, sym: str, name: str, dca: dict):
        import tkinter as tk
        from tkinter import ttk as _ttk

        win = tk.Toplevel(self)
        win.title(f"{sym.replace('.TW','')} {name}  參數敏感度分析")
        win.geometry("700x520")
        win.configure(bg="#0f1a30")
        win.lift()

        tk.Label(win, text=f"{sym.replace('.TW','')} {name}  觸發分析",
                 fg="#74b9ff", bg="#0f1a30",
                 font=(self.ui_font, 12, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        tk.Label(win, text="根據10年回測交易記錄，分析各策略買入當日的指標分布",
                 fg="#95a5a6", bg="#0f1a30",
                 font=("Consolas", 12)).pack(anchor="w", padx=14, pady=(0, 8))

        nb = _ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        strat_style = _ttk.Style(win)
        strat_style.theme_use("clam")
        strat_style.configure("Sen.Treeview",
                              background="#0d1b2a", foreground="#ecf0f1",
                              fieldbackground="#0d1b2a", rowheight=22,
                              font=("Consolas", 12))
        strat_style.configure("Sen.Treeview.Heading",
                              background="#0a0f1a", foreground="#74b9ff",
                              font=(self.ui_font, 12, "bold"))
        strat_style.map("Sen.Treeview", background=[("selected", "#1c4f82")])

        strategies = dca.get("strategies", [])

        for strat in strategies:
            label = strat["label"]
            txs   = strat.get("transactions", [])
            if not txs:
                continue

            frame = tk.Frame(nb, bg="#0f1a30")
            nb.add(frame, text=label.replace("（無條件）","").replace("（趨勢股，無條件）",""))

            # 統計數字
            n_total    = len(txs)
            n_signal   = sum(1 for t in txs if not t.get("fallback"))
            n_fallback = sum(1 for t in txs if t.get("fallback"))
            years_active = sorted({t["date"][:4] for t in txs})

            summary = (
                f"總買入次數：{n_total}  |  信號觸發：{n_signal}  |  年末強制：{n_fallback}\n"
                f"有買入的年份：{', '.join(years_active)}"
            )
            tk.Label(frame, text=summary, fg="#a0b0c0", bg="#0f1a30",
                     font=("Consolas", 12), justify="left").pack(anchor="w", padx=10, pady=(8, 4))

            # 觸發條件分布
            triggered = [t for t in txs if t.get("trigger")]
            if triggered:
                tk.Label(frame, text="信號觸發當日指標快照：",
                         fg="#74b9ff", bg="#0f1a30",
                         font=(self.ui_font, 12, "bold")).pack(anchor="w", padx=10, pady=(4, 2))

                trigger_keys = []
                for t in triggered:
                    for k in t.get("trigger", {}):
                        if k not in trigger_keys:
                            trigger_keys.append(k)

                base_cols = ("買入日期", "買入價", "持有報酬%")
                cols = base_cols + tuple(trigger_keys)
                widths = (100, 80, 90) + tuple(85 for _ in trigger_keys)

                tree_frame = tk.Frame(frame, bg="#0f1a30")
                tree_frame.pack(fill="both", expand=True, padx=10, pady=4)

                tree = _ttk.Treeview(tree_frame, style="Sen.Treeview",
                                     columns=cols, show="headings")
                for c, w in zip(cols, widths):
                    tree.heading(c, text=c)
                    tree.column(c, width=w, anchor="center")
                tree.tag_configure("fallback", foreground="#636e72")

                fp = strat.get("final_price", 0)
                for t in txs:
                    bp    = t.get("price", 0)
                    ret_s = f"{(fp/bp-1)*100:+.1f}%" if fp > 0 and bp > 0 else "—"
                    is_fb = t.get("fallback", False)
                    vals  = (
                        t.get("date","?") + (" ↩" if is_fb else ""),
                        f"{bp:,.2f}",
                        ret_s,
                    )
                    if not is_fb:
                        trig = t.get("trigger", {})
                        vals += tuple(str(trig.get(k, "—")) for k in trigger_keys)
                    else:
                        vals += tuple("↩" for _ in trigger_keys)
                    tree.insert("", "end", tags=("fallback" if is_fb else "",), values=vals)

                vsb = _ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
                tree.configure(yscrollcommand=vsb.set)
                tree.pack(side="left", fill="both", expand=True)
                vsb.pack(side="right", fill="y")

                # 觸發指標統計摘要
                if triggered and trigger_keys:
                    stats_lines = []
                    for k in trigger_keys:
                        vals_k = []
                        for t in triggered:
                            v = t.get("trigger", {}).get(k)
                            try:
                                vals_k.append(float(v))
                            except (TypeError, ValueError):
                                pass
                        if vals_k:
                            import statistics
                            stats_lines.append(
                                f"  {k:12}  平均 {statistics.mean(vals_k):+.1f}  "
                                f"最低 {min(vals_k):+.1f}  最高 {max(vals_k):+.1f}"
                            )
                    if stats_lines:
                        tk.Label(frame, text="觸發時指標統計（僅含信號觸發，不含年末強制）：\n" + "\n".join(stats_lines),
                                 fg="#a8e6cf", bg="#0f1a30",
                                 font=("Consolas", 12), justify="left"
                                 ).pack(anchor="w", padx=10, pady=(4, 8))
            else:
                tk.Label(frame,
                         text="（此策略無觸發條件記錄，可能全為 B&H 或資料版本較舊）",
                         fg="#636e72", bg="#0f1a30",
                         font=("Consolas", 12)).pack(anchor="w", padx=10, pady=20)

    def _dca_popup(self, transactions: list[dict], stock: str, strategy: str,
                   final_price: float = 0.0):
        import tkinter as tk
        from tkinter import ttk as _ttk

        has_trigger   = any("trigger" in t for t in transactions)
        trigger_keys: list[str] = []
        if has_trigger:
            for t in transactions:
                for k in t.get("trigger", {}):
                    if k not in trigger_keys:
                        trigger_keys.append(k)

        win = tk.Toplevel(self)
        win.title(f"{stock}  {strategy}")
        base_w = 820 if has_trigger else 660
        win.geometry(f"{base_w}x500")
        win.configure(bg="#0f1a30")
        win.lift()

        hdr = tk.Frame(win, bg="#0f1a30")
        hdr.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(hdr, text=f"{stock}  {strategy}",
                 fg="#74b9ff", bg="#0f1a30",
                 font=(self.ui_font, 12, "bold")).pack(side="left")
        tk.Label(hdr, text=f"  共 {len(transactions)} 筆",
                 fg="#888", bg="#0f1a30",
                 font=("Consolas", 12)).pack(side="left")

        total_cost   = sum(t.get("cost", 0) for t in transactions)
        n_fallback   = sum(1 for t in transactions if t.get("fallback"))
        smr = tk.Frame(win, bg="#1a2a40")
        smr.pack(fill="x", padx=10, pady=2)
        tk.Label(smr, text=f"總投入  NT${total_cost:,.0f}",
                 fg="#fdcb6e", bg="#1a2a40",
                 font=("Consolas", 12)).pack(side="left", padx=10, pady=4)
        if final_price > 0:
            total_shares = sum(t.get("shares", 0) for t in transactions)
            cur_val = total_shares * final_price
            ret_pct = (cur_val - total_cost) / total_cost * 100 if total_cost > 0 else 0
            clr = "#00b894" if ret_pct >= 0 else "#d63031"
            tk.Label(smr, text=f"  期末市值  NT${cur_val:,.0f}  ({ret_pct:+.1f}%)",
                     fg=clr, bg="#1a2a40",
                     font=("Consolas", 12)).pack(side="left", padx=4, pady=4)
        if n_fallback:
            tk.Label(smr, text=f"  （含 {n_fallback} 筆年末強制投入）",
                     fg="#636e72", bg="#1a2a40",
                     font=("Consolas", 12)).pack(side="left", padx=4)

        sty = _ttk.Style(win)
        sty.theme_use("clam")
        sty.configure("D.Treeview",
                      background="#0d1b2a", foreground=C_WHITE,
                      fieldbackground="#0d1b2a", rowheight=24,
                      font=("Consolas", 12))
        sty.configure("D.Treeview.Heading",
                      background="#0a0f1a", foreground="#74b9ff",
                      font=(self.ui_font, 12, "bold"))
        sty.map("D.Treeview", background=[("selected", "#1c4f82")])

        base_cols   = ("投資日期", "買入價", "股數", "投入金額", "持有報酬%")
        base_widths = (110, 90, 70, 110, 90)
        trig_cols   = tuple(trigger_keys)
        trig_widths = tuple(88 for _ in trigger_keys)
        cols   = base_cols + trig_cols
        widths = base_widths + trig_widths

        wrap = tk.Frame(win, bg="#0f1a30")
        wrap.pack(fill="both", expand=True, padx=10, pady=6)

        tree = _ttk.Treeview(wrap, style="D.Treeview",
                              columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="center")

        tree.tag_configure("signal",   foreground="#74b9ff")
        tree.tag_configure("fallback", foreground="#636e72")

        vsb = _ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for t in transactions:
            buy_price = t.get("price", 0)
            ret_str   = (f"{(final_price / buy_price - 1) * 100:+.1f}%"
                         if final_price > 0 and buy_price > 0 else "—")
            is_fb     = t.get("fallback", False)
            vals: tuple = (
                t.get("date", "?") + (" ↩" if is_fb else ""),
                f"{buy_price:,.2f}",
                f"{int(t.get('shares', 0)):,}",
                f"NT${t.get('cost', 0):,.0f}",
                ret_str,
            )
            if has_trigger:
                trig = t.get("trigger", {})
                vals += tuple(str(trig.get(k, "—")) for k in trigger_keys)
            tree.insert("", "end", tags=("fallback" if is_fb else "signal",), values=vals)

    # ════════════════════════════════════════════════════════════════════
    # 掃描 Tab 邏輯
    # ════════════════════════════════════════════════════════════════════

    def _initial_load(self):
        try:
            self._cfg = _load_config()
            records   = _load_scan_cache()
            if records:
                self._scan_records = records
                self._render(records)
                cache_date = Path(
                    sorted(_glob.glob(str(CACHE_DIR / "scan_*.json")), reverse=True)[0]
                ).stem.replace("scan_", "")
                self.lbl_time.configure(text=f"快取 {cache_date}")
            else:
                self.lbl_status.configure(text="無快取，請按重新掃描")
            self._update_market_label()
        except Exception as e:
            self.lbl_status.configure(text=f"載入失敗：{e}")

    def _on_refresh(self):
        if self._loading:
            return
        self._loading = True
        self.lbl_status.configure(text="掃描中...")
        threading.Thread(target=self._bg_scan, daemon=True).start()

    def _bg_scan(self):
        try:
            from tw_screener import run_scan
            records = run_scan()
            self._scan_records = records
            self.after(0, lambda: self._render(records))
            self.after(0, self._update_market_label)
            self.after(0, lambda: self.lbl_time.configure(
                text=datetime.now(TZ).strftime("%Y-%m-%d %H:%M")))
            self.after(0, lambda: self.lbl_status.configure(text=""))
        except Exception as e:
            self.after(0, lambda: self.lbl_status.configure(text=f"掃描失敗：{e}"))
        finally:
            self._loading = False

    def _update_market_label(self):
        if not self._scan_records:
            return
        mode = self._scan_records[0].get("market_mode", "NORMAL")
        try:
            from tw_screener import get_market_mode
            _, detail = get_market_mode()
            twii  = detail.get("twii_price", "N/A")
            ma200 = detail.get("twii_ma200", "N/A")
            vs    = detail.get("twii_vs_ma200_pct", 0)
            vol   = detail.get("vol_20_annualized", "N/A")
            ms    = {"NORMAL": "🟢 正常", "WARN": "🟡 警戒", "RISK": "🔴 風險"}.get(mode, mode)
            clr   = {"NORMAL": C_GREEN, "WARN": C_YELLOW, "RISK": C_RED}.get(mode, C_GRAY)
            self.lbl_market.configure(
                text=f"市場 {ms}　TWII {twii}  MA200 {ma200}（{vs:+.1f}%）　波動率 {vol}%",
                text_color=clr)
        except Exception:
            pass

    def _sort_scan(self, col: str):
        if self._sort_col == col:
            if not self._sort_asc:
                self._sort_col = None   # 第三次點：取消排序
            else:
                self._sort_asc = False  # 第二次點：降序
        else:
            self._sort_col = col
            self._sort_asc = True       # 第一次點：升序
        if self._scan_records:
            self._render(self._scan_records)

    # ── CSV 匯出 ──────────────────────────────────────────────────────────

    def _export_scan_csv(self):
        import csv
        from tkinter.filedialog import asksaveasfilename
        path = asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV 檔案", "*.csv")],
            initialfile=f"scan_{datetime.now().strftime('%Y%m%d')}.csv",
        )
        if not path:
            return
        headers = [hd for _, hd, *_ in SCAN_COLS]
        rows = [self.tree.item(iid)["values"] for iid in self.tree.get_children()]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)
        self.lbl_status.configure(text=f"已匯出 {len(rows)} 筆", text_color=C_GREEN)

    def _export_sbt_csv(self):
        import csv
        from tkinter.filedialog import asksaveasfilename
        # 從 SBT cache 匯出所有策略摘要
        rows_out = []
        for key, result in getattr(self, "_sbt_cache", {}).items():
            if "error" in result:
                continue
            sym  = result.get("symbol", "")
            name = result.get("name", "")
            for m in result.get("modes", []):
                rows_out.append([
                    sym, name, m.get("label", ""),
                    m.get("trades", ""), m.get("win_rate_pct", ""),
                    m.get("total_injected", ""), m.get("total_pnl", ""),
                    m.get("return_pct", ""), m.get("cagr_pct", ""),
                    m.get("mdd_pct", ""), m.get("sharpe", ""),
                    m.get("fees", ""),
                ])
        if not rows_out:
            return
        path = asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV 檔案", "*.csv")],
            initialfile=f"backtest_{datetime.now().strftime('%Y%m%d')}.csv",
        )
        if not path:
            return
        hdrs = ["代號","名稱","策略","交易次","勝率%","總注資","損益","報酬%","CAGR%","MDD%","Sharpe","手續費"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(hdrs)
            w.writerows(rows_out)
        self._sbt_status.configure(text=f"已匯出 {len(rows_out)} 筆", text_color=C_GREEN)

    def _export_trades_csv(self):
        import csv, tw_portfolio as pf
        from tkinter.filedialog import asksaveasfilename
        trades = pf.load_trades()
        if not trades:
            return
        path = asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV 檔案", "*.csv")],
            initialfile=f"trades_{datetime.now().strftime('%Y%m%d')}.csv",
        )
        if not path:
            return
        hdrs = ["狀態","代號","名稱","買入日","買入價","股數","買入手續費",
                "賣出日","賣出價","賣出手續費","證交稅","損益","損益%","備註"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(hdrs)
            for t in trades:
                if t["status"] == "closed":
                    p = pf.calc_closed_pnl(t)
                    pnl, pct = p["pnl"], p["pnl_pct"]
                else:
                    pnl, pct = "", ""
                w.writerow([
                    t["status"], t["symbol"], t["name"],
                    t["buy_date"], t["buy_price"], t["shares"], t["buy_commission"],
                    t.get("sell_date",""), t.get("sell_price",""),
                    t.get("sell_commission",""), t.get("sell_tax",""),
                    pnl, pct, t.get("note",""),
                ])
        self._tracker_status.configure(
            text=f"已匯出 {len(trades)} 筆", text_color=C_GREEN)

    def _render(self, records: list[dict]):
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self._cfg:
            try:
                self._cfg = _load_config()
            except Exception:
                pass

        rows  = _build_scan_rows(records, self._cfg)
        order = {"STRONG BUY": 0, "BUY": 1, "SELL": 2, "WATCH": 3, "HOLD": 4}

        if self._sort_col:
            def _sort_key(r):
                v = r.get(self._sort_col, "")
                try:
                    return (0, float(str(v).replace("%", "").replace(",", "").replace("—", "nan")))
                except (ValueError, TypeError):
                    return (1, str(v))
            rows.sort(key=_sort_key, reverse=not self._sort_asc)
            # update heading arrows
            for cid, hd, *_ in SCAN_COLS:
                arrow = (" ▲" if self._sort_asc else " ▼") if cid == self._sort_col else ""
                self.tree.heading(cid, text=hd + arrow)
        else:
            rows.sort(key=lambda r: order.get(r["_signal_raw"], 5))
            for cid, hd, *_ in SCAN_COLS:
                self.tree.heading(cid, text=hd)

        alt    = 0
        counts = {"STRONG BUY": 0, "BUY": 0, "SELL": 0, "WATCH": 0}

        for r in rows:
            sig = r["_signal_raw"]
            if sig == "STRONG BUY":
                tag = "strong_buy"; counts["STRONG BUY"] += 1
            elif sig == "BUY":
                tag = "buy";        counts["BUY"] += 1
            elif sig == "SELL":
                tag = "sell";       counts["SELL"] += 1
            elif sig == "WATCH":
                tag = "watch";      counts["WATCH"] += 1
            else:
                base = "portfolio" if r["_in_portfolio"] else "hold"
                tag  = base if alt % 2 == 0 else f"{base}_alt"
                alt += 1

            self.tree.insert("", "end", tags=(tag,), values=[
                r["symbol"], r["name"], r["cat"], r["rec"],
                r["signal"], r["price"], r["rsi"],
                r["avwap"], r["avwap_dist"], r["b1"], r["b2"], r["s_target"],
                r["dd"], r["pnl"],
            ])

        parts = [f"{k.replace('STRONG BUY','強買')} {v}"
                 for k, v in counts.items() if v]
        self.lbl_summary.configure(
            text=f"共 {len(rows)} 檔　{'　'.join(parts) or '無明確信號'}"
                 "　（試買=AVWAP×b1  加碼=AVWAP×b2  賣出=AVWAP×s）"
        )

        # 板塊集中警告：同板塊 ≥2 檔同時出現 BUY/STRONG BUY
        try:
            from tw_screener import SECTOR
            active_buy = {r["symbol"] + ".TW"
                          for r in rows if r["_signal_raw"] in ("STRONG BUY", "BUY")}
            warns = []
            for sector, syms in SECTOR.items():
                hits = [s for s in syms if s in active_buy]
                if len(hits) >= 2:
                    codes = " + ".join(h.split(".")[0] for h in hits)
                    warns.append(f"⚠️ {sector}集中（{codes}）")
            self.lbl_sector_warn.configure(text="  ".join(warns) if warns else "")
        except Exception:
            pass


    # ════════════════════════════════════════════════════════════════════
    # 跟單回測 Tab
    # ════════════════════════════════════════════════════════════════════

    def _build_signal_bt_tab(self, tab):
        tab.configure(fg_color=BG)

        bar = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=46, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="📋 跟單回測",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE
                     ).pack(side="left", padx=16, pady=12)
        ctk.CTkLabel(bar, text="模擬每年注資，按 BUY / STRONG BUY / SELL 信號操作，逐筆記錄損益",
                     font=(self.ui_font, 12), text_color=C_GRAY
                     ).pack(side="left", padx=4)
        self._sbt_status = ctk.CTkLabel(bar, text="",
                                        font=(self.ui_font, 12), text_color=C_YELLOW)
        self._sbt_status.pack(side="right", padx=12)
        ctk.CTkButton(bar, text="📊 組合回測", width=90, height=28,
                      font=(self.ui_font, 12),
                      fg_color="#2d4a6a", hover_color="#3a6090",
                      command=self._sbt_portfolio_popup
                      ).pack(side="right", padx=(0, 4))
        ctk.CTkButton(bar, text="📥 CSV", width=65, height=28,
                      font=(self.ui_font, 12),
                      fg_color="#2c3e50", hover_color="#34495e",
                      command=self._export_sbt_csv
                      ).pack(side="right", padx=(0, 4))
        # 年度注資金額輸入
        ctk.CTkLabel(bar, text="每年注資 NT$",
                     font=(self.ui_font, 12), text_color=C_GRAY
                     ).pack(side="right", padx=(8, 2))
        self._sbt_budget_var = ctk.StringVar(value="100000")
        ctk.CTkEntry(bar, textvariable=self._sbt_budget_var,
                     width=90, height=28, font=(self.ui_font, 12)
                     ).pack(side="right", padx=(0, 4))
        # 回測年份選擇（右至左 pack）
        _years_end   = [str(y) for y in range(2015, 2026)]
        _years_start = [str(y) for y in range(2010, 2026)]
        self._sbt_end_var    = ctk.StringVar(value="2025")
        self._sbt_start_var  = ctk.StringVar(value="2015")
        self._sbt_user_start = "2015"   # 使用者手動設定的開始年份（不被自動調整覆蓋）
        ctk.CTkOptionMenu(bar, variable=self._sbt_end_var, values=_years_end,
                          command=self._sbt_on_date_change,
                          width=68, height=28, font=(self.ui_font, 12)
                          ).pack(side="right", padx=(0, 4))
        ctk.CTkLabel(bar, text="至",
                     font=(self.ui_font, 12), text_color=C_GRAY
                     ).pack(side="right", padx=2)
        ctk.CTkOptionMenu(bar, variable=self._sbt_start_var, values=_years_start,
                          command=self._sbt_on_date_change,
                          width=68, height=28, font=(self.ui_font, 12)
                          ).pack(side="right", padx=(0, 2))
        ctk.CTkLabel(bar, text="年份",
                     font=(self.ui_font, 12), text_color=C_GRAY
                     ).pack(side="right", padx=(10, 2))

        body = ctk.CTkFrame(tab, fg_color=BG, corner_radius=0)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # 左側：股票清單
        left = ctk.CTkFrame(body, fg_color=BG_PANEL, width=175, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="股票清單",
                     font=(self.ui_font, 12, "bold"), text_color=C_BLUE
                     ).pack(pady=(12, 4), padx=12, anchor="w")

        self._sbt_list   = ctk.CTkScrollableFrame(left, fg_color=BG_PANEL)
        self._sbt_list.pack(fill="both", expand=True, padx=4, pady=4)

        # 右側：結果面板
        right = ctk.CTkFrame(body, fg_color=BG, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew")
        self._sbt_detail = ctk.CTkScrollableFrame(right, fg_color=BG)
        self._sbt_detail.pack(fill="both", expand=True)

        # 載入快取：key = "SYM|start|end"
        _file_cache = _load_sbt_cache()  # {sym: result}
        self._sbt_cache: dict = {
            f"{sym}|{START_DATE}|{END_DATE}": r
            for sym, r in _file_cache.items()
        }
        self._sbt_btns: dict = {}

        try:
            cfg    = _load_config()
            stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]
        except Exception:
            stocks = []

        for s in stocks:
            sym, name = s["symbol"], s["name"]
            is_bt_only = s.get("backtest_only", False)
            ck  = f"{sym}|{START_DATE}|{END_DATE}"
            has = ck in self._sbt_cache
            btn = ctk.CTkButton(
                self._sbt_list,
                text=f"{'[研]' if is_bt_only else ''}{sym.replace('.TW','')}  {name}",
                font=(self.ui_font, 12),
                fg_color="transparent", hover_color=BG_ROW_A,
                text_color=C_WHITE if has else C_GRAY,
                anchor="w", height=32,
                command=lambda sy=sym, nm=name: self._on_sbt_select(sy, nm),
            )
            btn.pack(fill="x", pady=1, padx=2)
            self._sbt_btns[sym] = btn

        self._sbt_hint()

    def _sbt_ck(self, sym: str) -> str:
        """當前日期設定的 cache key。"""
        s = f"{self._sbt_start_var.get()}-01-01"
        e = f"{self._sbt_end_var.get()}-12-31"
        return f"{sym}|{s}|{e}"

    def _sbt_on_date_change(self, _=None):
        """年份選單切換時，更新使用者設定並刷新按鈕顏色。"""
        self._sbt_user_start = self._sbt_start_var.get()  # 記住手動選擇的開始年
        for sym, btn in self._sbt_btns.items():
            has = self._sbt_ck(sym) in self._sbt_cache
            btn.configure(text_color=C_WHITE if has else C_GRAY)
        # 清空右側面板，避免顯示舊日期範圍的結果
        for w in self._sbt_detail.winfo_children():
            w.destroy()
        self._sbt_hint()

    def _sbt_hint(self):
        for w in self._sbt_detail.winfo_children():
            w.destroy()
        if not self._sbt_cache:
            ctk.CTkLabel(self._sbt_detail,
                         text="尚無跟單回測快取\n\n請先執行：\npython tw_scheduler.py --signal-bt",
                         font=(self.ui_font, 13), text_color=C_YELLOW,
                         justify="center").pack(pady=60)
        else:
            ctk.CTkLabel(self._sbt_detail,
                         text="← 點選左側股票查看跟單回測結果\n\n"
                              "每支股票顯示：B&H 基準 + 4 種策略模式比較\n"
                              "每種模式可展開逐筆交易明細",
                         font=(self.ui_font, 12), text_color=C_GRAY,
                         justify="center").pack(pady=60)

    def _on_sbt_select(self, sym: str, name: str):
        for s, b in self._sbt_btns.items():
            b.configure(fg_color="#1f4e79" if s == sym else "transparent")

        # ── 自動調整年份範圍 ──────────────────────────────────────────────
        adjust_msg = None
        min_yr = STOCK_EARLIEST_YEAR.get(sym)
        if min_yr:
            cur_start = int(self._sbt_start_var.get())
            cur_end   = int(self._sbt_end_var.get())
            if cur_start < min_yr:
                new_start = min(min_yr, cur_end - 1)
                self._sbt_start_var.set(str(new_start))
                adjust_msg = (f"ℹ {sym.replace('.TW','')} 最早資料為 {min_yr} 年，"
                              f"開始年份已自動調整為 {new_start}")
                # 更新按鈕顏色
                for sym2, btn in self._sbt_btns.items():
                    btn.configure(text_color=C_WHITE if self._sbt_ck(sym2) in self._sbt_cache else C_GRAY)
        else:
            # 無限制的股票：若開始年份是被前一支股票自動調高的，還原使用者原本設定
            user_start = int(self._sbt_user_start)
            cur_start  = int(self._sbt_start_var.get())
            if cur_start != user_start:
                self._sbt_start_var.set(self._sbt_user_start)
                for sym2, btn in self._sbt_btns.items():
                    btn.configure(text_color=C_WHITE if self._sbt_ck(sym2) in self._sbt_cache else C_GRAY)

        for w in self._sbt_detail.winfo_children():
            w.destroy()
        try:
            self._sbt_detail._parent_canvas.yview_moveto(0)
        except Exception:
            pass

        result   = self._sbt_cache.get(self._sbt_ck(sym))
        yr_range = f"{self._sbt_start_var.get()}–{self._sbt_end_var.get()}"
        if result:
            self._sbt_show_result(sym, name, result)
        else:
            if adjust_msg:
                ctk.CTkLabel(self._sbt_detail, text=adjust_msg,
                             font=(self.ui_font, 12), text_color=C_YELLOW,
                             justify="center").pack(pady=(20, 4))
            ctk.CTkLabel(self._sbt_detail,
                         text=f"{sym.replace('.TW','')} {name}  [{yr_range}]\n無快取",
                         font=(self.ui_font, 13), text_color=C_YELLOW,
                         justify="center").pack(pady=(8, 4))
            ctk.CTkButton(
                self._sbt_detail, text="▶ 執行此股票回測（約 10–30 秒）",
                font=(self.ui_font, 12),
                fg_color="#1f4e79", hover_color="#2980b9",
                command=lambda: self._sbt_run_stock(sym, name),
            ).pack(pady=10)

    def _sbt_run_stock(self, sym: str, name: str):
        start_yr = int(self._sbt_start_var.get())
        end_yr   = int(self._sbt_end_var.get())
        if start_yr >= end_yr:
            self._sbt_status.configure(
                text=f"⚠ 開始年份須小於結束年份（{start_yr} ≥ {end_yr}），請調整範圍",
                text_color=C_YELLOW)
            return
        start_date = f"{self._sbt_start_var.get()}-01-01"
        end_date   = f"{self._sbt_end_var.get()}-12-31"
        ck         = self._sbt_ck(sym)
        yr_range   = f"{self._sbt_start_var.get()}–{self._sbt_end_var.get()}"

        self._sbt_status.configure(text=f"執行中：{sym.replace('.TW','')} [{yr_range}]…")
        for w in self._sbt_detail.winfo_children():
            w.destroy()
        ctk.CTkLabel(self._sbt_detail, text="資料拉取中，請稍候…",
                     font=(self.ui_font, 12), text_color=C_YELLOW).pack(pady=60)

        try:
            annual_budget = float(self._sbt_budget_var.get().replace(",", ""))
            if annual_budget <= 0:
                annual_budget = 100_000
        except ValueError:
            annual_budget = 100_000

        def _bg():
            try:
                from tw_backtest_signals import run_signal_backtest
                result = run_signal_backtest(sym, name, annual_budget=annual_budget,
                                             start_date=start_date, end_date=end_date)
                if "error" not in result:
                    self._sbt_cache[ck] = result
                    if sym in self._sbt_btns:
                        self.after(0, lambda: self._sbt_btns[sym].configure(text_color=C_WHITE))
                    self.after(0, lambda: self._sbt_show_result(sym, name, result))
                else:
                    err = result["error"]
                    self.after(0, lambda: self._sbt_status.configure(text=f"失敗：{err}"))
            except Exception as e:
                self.after(0, lambda: self._sbt_status.configure(text=f"錯誤：{e}"))
            finally:
                self.after(0, lambda: self._sbt_status.configure(text=""))

        threading.Thread(target=_bg, daemon=True).start()

    def _sbt_show_result(self, sym: str, name: str, result: dict):
        for w in self._sbt_detail.winfo_children():
            w.destroy()
        try:
            self._sbt_detail._parent_canvas.yview_moveto(0)
        except Exception:
            pass

        bnh   = result.get("bnh", {})
        modes = result.get("modes", [])
        sc    = result.get("sig_counts", {})
        p     = result.get("params", {})

        # ── 標題列 ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self._sbt_detail, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(hdr,
                     text=f"📋 {sym.replace('.TW','')} {name}   "
                          f"{result.get('start_date','')} → {result.get('end_date','')}",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE
                     ).pack(side="left")
        ctk.CTkButton(hdr, text="📊 K 線圖",
                      font=(self.ui_font, 12), width=80, height=24,
                      fg_color="#1a2a10", hover_color="#2a4020",
                      text_color="#a8e6cf",
                      command=lambda: self._chart_popup(sym, name),
                      ).pack(side="right", padx=4)
        ctk.CTkButton(hdr, text="📈 資產曲線",
                      font=(self.ui_font, 12), width=90, height=24,
                      fg_color="#1a3040", hover_color="#2a4a60",
                      text_color="#74b9ff",
                      command=lambda: self._sbt_chart_popup(sym, name, result),
                      ).pack(side="right", padx=4)
        ctk.CTkButton(hdr, text="🔬 Walk-Forward",
                      font=(self.ui_font, 12), width=110, height=24,
                      fg_color="#1a2a10", hover_color="#2a4a20",
                      text_color="#a8e6cf",
                      command=lambda: self._sbt_walkforward_popup(sym, name, result),
                      ).pack(side="right", padx=4)

        # ── 信號統計 + 回測設定 ───────────────────────────────────────
        info = ctk.CTkFrame(self._sbt_detail, fg_color="#0a1020", corner_radius=6)
        info.pack(fill="x", padx=12, pady=(0, 8))
        ab = p.get("annual_budget", 100_000)
        for txt, clr in [
            (f"STRONG BUY ×{sc.get('STRONG BUY',0)}", C_STRONG),
            (f"  BUY ×{sc.get('BUY',0)}", C_GREEN),
            (f"  SELL ×{sc.get('SELL',0)}", C_RED),
            (f"   │  每年注資 NT${ab:,.0f}  SBUY={p.get('sbuy_mult',1.5):.1f}x  "
             f"TRIM≥{p.get('trim_pct',15):.0f}%  "
             f"手續費{p.get('commission_rate',0.1425):.4f}%+稅{p.get('tax_rate',0.3):.1f}%", C_GRAY),
        ]:
            ctk.CTkLabel(info, text=txt, font=(self.ui_font, 12),
                         text_color=clr).pack(side="left", padx=6, pady=5)

        # ── 推薦策略欄 ────────────────────────────────────────────────
        bm = result.get("best_mode", {})
        if bm:
            bm_is_bnh = bm.get("mode") == "BNH"
            bm_color  = C_YELLOW if bm_is_bnh else C_GREEN
            bm_icon   = "📌" if bm_is_bnh else "★"
            bm_txt    = (f"{bm_icon} 推薦：{bm['label']}  "
                         f"CAGR {bm['cagr']:+.1f}%"
                         + (f"  MDD {bm['mdd']:.1f}%  Calmar {bm['calmar']:.2f}" if bm.get("mdd") else "")
                         + f"  │  {bm['reason']}")
            rec_bar = ctk.CTkFrame(self._sbt_detail, fg_color="#0d2010" if not bm_is_bnh else "#1a1a00",
                                   corner_radius=6)
            rec_bar.pack(fill="x", padx=12, pady=(0, 6))
            ctk.CTkLabel(rec_bar, text=bm_txt,
                         font=(self.ui_font, 12, "bold"), text_color=bm_color
                         ).pack(side="left", padx=10, pady=5)

        # ── 摘要比較表 ────────────────────────────────────────────────
        annual_bgt = bnh.get("annual_budget", p.get("annual_budget", 100_000))
        ctk.CTkLabel(self._sbt_detail,
                     text=f"策略摘要比較（每年注資 NT${annual_bgt:,.0f}，B&H = 年初無條件買入）",
                     font=(self.ui_font, 12, "bold"), text_color=C_BLUE
                     ).pack(anchor="w", padx=16, pady=(4, 2))

        tbl = ctk.CTkFrame(self._sbt_detail, fg_color="#0a1020", corner_radius=8)
        tbl.pack(fill="x", padx=12, pady=(0, 10))

        headers = ["策略", "交易次", "勝率", "總注資", "損益NT$", "報酬%", "CAGR%", "MDD%", "Sharpe", "手續費NT$", "vs B&H"]
        widths  = [165, 55, 60, 105, 105, 75, 70, 65, 55, 90, 65]

        hdr_row = ctk.CTkFrame(tbl, fg_color="#0f1a30")
        hdr_row.pack(fill="x", padx=2, pady=(2, 0))
        for h, w in zip(headers, widths):
            ctk.CTkLabel(hdr_row, text=h, font=(self.ui_font, 12, "bold"),
                         text_color="#74b9ff", width=w, anchor="center"
                         ).pack(side="left")

        # B&H 年度注資基準列
        bnh_row = ctk.CTkFrame(tbl, fg_color="#1a1a0d")
        bnh_row.pack(fill="x", padx=2, pady=1)
        bnh_ret  = bnh.get("return_pct", 0)
        bnh_cagr = bnh.get("cagr_pct", 0)
        for val, w, clr in [
            ("📌 B&H（年初買入）",                  165, C_YELLOW),
            (str(len(bnh.get("transactions", []))),   55, C_GRAY),
            ("—",                                      60, C_GRAY),
            (f"NT${bnh.get('total_injected',0):,.0f}", 105, C_GRAY),
            (f"NT${bnh.get('pnl',0):+,.0f}",          105, C_GREEN if bnh.get("pnl",0)>=0 else C_RED),
            (f"{bnh_ret:+.1f}%",                       75, C_GREEN if bnh_ret>=0 else C_RED),
            (f"{bnh_cagr:+.1f}%",                      70, C_GREEN if bnh_cagr>=0 else C_RED),
            ("—",                                       65, C_GRAY),
            ("—",                                       55, C_GRAY),
            ("含費基準",                                 90, C_GRAY),
            ("基準",                                     65, C_YELLOW),
        ]:
            ctk.CTkLabel(bnh_row, text=val, font=(self.ui_font, 12),
                         text_color=clr, width=w, anchor="center"
                         ).pack(side="left")

        # 各信號策略列
        for m in modes:
            s    = m["stats"]
            beat = s["beats_bnh"]
            ret  = s["return_pct"]
            pnl  = s["total_pnl"]
            cagr = s.get("cagr_pct", 0)
            row_bg = "#0d2d0d" if beat else "transparent"
            dr = ctk.CTkFrame(tbl, fg_color=row_bg)
            dr.pack(fill="x", padx=2, pady=1)
            tag  = "✅" if beat else ("⬜" if s["n_trades"]==0 else "❌")
            pclr = C_GREEN if pnl >= 0 else C_RED
            rclr = C_GREEN if ret >= 0 else C_RED
            wclr = C_GREEN if s["win_rate"] >= 60 else (C_YELLOW if s["win_rate"] >= 40 else C_RED)
            mdd       = s.get("mdd_pct", 0)
            tot_fees  = s.get("total_fees", 0)
            sharpe    = s.get("sharpe", 0.0)
            sclr      = C_GREEN if sharpe >= 1.0 else (C_YELLOW if sharpe >= 0.5 else C_GRAY)
            for val, w, clr in [
                (f"{tag} {m['label']}",              165, C_GREEN if beat else (C_GRAY if s["n_trades"]==0 else C_YELLOW)),
                (str(s["n_trades"]),                  55,  C_GRAY),
                (f"{s['win_rate']:.0f}%" if s["n_trades"] else "—", 60, wclr),
                (f"NT${s['total_injected']:,.0f}",   105, C_GRAY),
                (f"NT${pnl:+,.0f}",                  105, pclr),
                (f"{ret:+.1f}%",                      75,  rclr),
                (f"{cagr:+.1f}%",                     70,  C_GREEN if cagr>=0 else C_RED),
                (f"{mdd:.1f}%",                       65,  "#e17055" if mdd < -20 else (C_YELLOW if mdd < -10 else C_GRAY)),
                (f"{sharpe:.2f}" if s["n_trades"] else "—", 55, sclr),
                (f"NT${tot_fees:,.0f}",               90,  C_GRAY),
                ("勝" if beat else ("—" if s["n_trades"]==0 else "輸"), 65, C_GREEN if beat else (C_GRAY if s["n_trades"]==0 else C_RED)),
            ]:
                ctk.CTkLabel(dr, text=val, font=(self.ui_font, 12),
                             text_color=clr, width=w, anchor="center"
                             ).pack(side="left")

        # ── DCA 定期定額策略列（從快取讀取）────────────────────────────
        try:
            from tw_backtest_dca import load_dca_cache
            dca_list = load_dca_cache()
            dca_row  = next((r for r in dca_list if r.get("symbol") == sym), None)
        except Exception:
            dca_row = None

        if dca_row:
            sep = ctk.CTkFrame(tbl, fg_color="#1a1a2d")
            sep.pack(fill="x", padx=2, pady=(4, 0))
            ctk.CTkLabel(sep, text="── DCA 定期定額策略（參考對比）──",
                         font=(self.ui_font, 12), text_color="#9090c0",
                         width=sum(widths), anchor="center"
                         ).pack(side="left")

            for ds in dca_row.get("strategies", []):
                d_ret  = ds.get("total_return_pct", 0) or 0
                d_cagr = ds.get("cagr_pct", 0) or 0
                d_pnl  = ds.get("profit", 0) or 0
                d_inv  = ds.get("total_invested", 0) or 0
                d_ntx  = ds.get("n_transactions", 0) or 0
                d_lbl  = ds.get("label", "DCA")
                d_fp   = ds.get("final_price", 0.0)
                d_txs  = ds.get("transactions", [])
                beat_d = d_ret > bnh_ret
                dr = ctk.CTkFrame(tbl, fg_color="#0a0a1d")
                dr.pack(fill="x", padx=2, pady=1)
                tag_d  = "✅" if beat_d else "❌"
                pclr   = C_GREEN if d_pnl >= 0 else C_RED
                rclr   = C_GREEN if d_ret >= 0 else C_RED
                for val, w, clr in [
                    (f"{tag_d} {d_lbl}",        165, C_GREEN if beat_d else "#a0a0c0"),
                    (str(d_ntx),                  55, C_GRAY),
                    ("—",                          60, C_GRAY),
                    (f"NT${d_inv:,.0f}",          105, C_GRAY),
                    (f"NT${d_pnl:+,.0f}",         105, pclr),
                    (f"{d_ret:+.1f}%",             75, rclr),
                    (f"{d_cagr:+.1f}%",            70, C_GREEN if d_cagr>=0 else C_RED),
                    ("—",                           65, C_GRAY),
                    ("—",                           90, C_GRAY),
                ]:
                    ctk.CTkLabel(dr, text=val, font=(self.ui_font, 12),
                                 text_color=clr, width=w, anchor="center"
                                 ).pack(side="left")
                # 展開明細按鈕（取代原來的「勝/輸」文字）
                if d_txs:
                    ctk.CTkButton(
                        dr, text="📋 明細",
                        font=(self.ui_font, 12), width=65, height=20,
                        fg_color="#1a3040", hover_color="#2a4a60",
                        text_color="#74b9ff",
                        command=lambda txs=d_txs, lbl=d_lbl, fp=d_fp: self._dca_popup(
                            txs, f"{sym.replace('.TW','')} {name}", lbl, final_price=fp),
                    ).pack(side="left", padx=2)
                else:
                    ctk.CTkLabel(dr, text="勝" if beat_d else "輸", width=65,
                                 font=(self.ui_font, 12),
                                 text_color=C_GREEN if beat_d else C_RED,
                                 anchor="center").pack(side="left")

        # ── 各策略卡片（含交易明細按鈕，依 CAGR 高至低排序）────────
        modes_sorted = sorted(modes,
                              key=lambda m: m["stats"].get("cagr_pct", -999),
                              reverse=True)
        for m in modes_sorted:
            s     = m["stats"]
            beat  = s["beats_bnh"]
            trades = m.get("trades", [])

            card = ctk.CTkFrame(
                self._sbt_detail,
                fg_color="#0d2d0d" if beat else "#0d1a2d",
                corner_radius=10)
            card.pack(fill="x", padx=12, pady=3)

            # 卡片標題
            card_hdr = ctk.CTkFrame(card, fg_color="transparent")
            card_hdr.pack(fill="x", padx=14, pady=(8, 4))
            flag = "✅" if beat else ("⬜" if s["n_trades"] == 0 else "❌")
            ctk.CTkLabel(card_hdr,
                         text=f"{flag} {m['label']}",
                         font=(self.ui_font, 12, "bold"),
                         text_color=C_GREEN if beat else (C_GRAY if s["n_trades"]==0 else C_YELLOW)
                         ).pack(side="left")
            if trades:
                lbl = m["label"]
                ctk.CTkButton(
                    card_hdr, text=f"📋 展開明細（{len(trades)} 筆）",
                    font=(self.ui_font, 12), width=130, height=24,
                    fg_color="#1a3a60", hover_color="#2d5a8e",
                    text_color="#74b9ff",
                    command=lambda t=trades, l=lbl, sn=f"{sym.replace('.TW','')} {name}":
                        self._sbt_trade_popup(t, sn, l),
                ).pack(side="right")

            if s["n_trades"] == 0:
                ctk.CTkLabel(card, text="此策略在此期間無任何交易",
                             font=(self.ui_font, 12), text_color=C_GRAY
                             ).pack(anchor="w", padx=18, pady=(0, 8))
                continue

            # 指標行
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(0, 8))
            ret  = s["return_pct"]
            pnl  = s["total_pnl"]
            cagr = s.get("cagr_pct", 0)
            s_mdd    = s.get("mdd_pct", 0)
            s_fees   = s.get("total_fees", 0)
            s_calmar = s.get("calmar", 0)
            for label, val, clr in [
                ("報酬率",   f"{ret:+.1f}%",              C_GREEN if ret>=0 else C_RED),
                ("CAGR",    f"{cagr:+.1f}%",              C_GREEN if cagr>=0 else C_RED),
                ("Calmar",  f"{s_calmar:.2f}",            C_GREEN if s_calmar>=0.3 else (C_YELLOW if s_calmar>=0.1 else C_GRAY)),
                ("總損益",   f"NT${pnl:+,.0f}",            C_GREEN if pnl>=0 else C_RED),
                ("MDD",     f"{s_mdd:.1f}%",              "#e17055" if s_mdd < -20 else (C_YELLOW if s_mdd < -10 else C_GRAY)),
                ("手續費+稅", f"NT${s_fees:,.0f}",          C_GRAY),
                ("勝率",     f"{s['win_rate']:.0f}%",      C_GREEN if s['win_rate']>=60 else C_YELLOW),
                ("交易次",   str(s["n_trades"]),            C_GRAY),
                ("平均持有", f"{s['avg_hold_days']}天",     C_GRAY),
                ("最佳",     f"{s['best_pct']:+.1f}%",     C_GREEN),
                ("最差",     f"{s['worst_pct']:+.1f}%",    C_RED),
                ("期末未結", str(s["n_open_end"]),           C_YELLOW if s["n_open_end"] else C_GRAY),
            ]:
                col = ctk.CTkFrame(row, fg_color="transparent")
                col.pack(side="left", padx=8)
                ctk.CTkLabel(col, text=label,
                             font=(self.ui_font, 12), text_color=C_GRAY).pack()
                ctk.CTkLabel(col, text=val,
                             font=(self.ui_font, 12, "bold"), text_color=clr).pack()
                if label in _SBT_METRIC_TIPS:
                    _Tooltip(col, _SBT_METRIC_TIPS[label])

            # 出場方式細分
            if trades:
                n_trim  = sum(1 for t in trades if t.get("exit_signal") == "TRIM")
                n_trail = sum(1 for t in trades if t.get("exit_signal") == "TRAILING_STOP")
                n_sell  = sum(1 for t in trades if t.get("exit_signal") == "SELL")
                n_fall  = sum(1 for t in trades if t.get("entry_signal") == "FALLBACK")
                n_pend  = s.get("n_open_end", 0)
                exit_parts = []
                if n_trim:  exit_parts.append(f"止盈 {n_trim}")
                if n_trail: exit_parts.append(f"追蹤止盈 {n_trail}")
                if n_sell:  exit_parts.append(f"SELL信號 {n_sell}")
                if n_fall:  exit_parts.append(f"年末強制 {n_fall}")
                if n_pend:  exit_parts.append(f"期末持倉 {n_pend}")
                if exit_parts:
                    exit_row = ctk.CTkFrame(card, fg_color="transparent")
                    exit_row.pack(fill="x", padx=14, pady=(0, 6))
                    ctk.CTkLabel(exit_row,
                                 text="出場細分：" + "  ｜  ".join(exit_parts),
                                 font=(self.ui_font, 12), text_color="#74a0c0"
                                 ).pack(side="left")

        # ── 策略綜合評析（結論欄）──────────────────────────────────────
        active_modes = [m for m in modes if m["stats"]["n_trades"] > 0]
        if active_modes:
            sep2 = ctk.CTkFrame(self._sbt_detail, fg_color="#2a2a4a", height=2, corner_radius=0)
            sep2.pack(fill="x", padx=12, pady=(14, 6))
            ctk.CTkLabel(self._sbt_detail, text="📊 策略綜合評析",
                         font=(self.ui_font, 12, "bold"), text_color=C_BLUE
                         ).pack(anchor="w", padx=16, pady=(0, 4))

            conc = ctk.CTkFrame(self._sbt_detail, fg_color="#0a1020", corner_radius=8)
            conc.pack(fill="x", padx=12, pady=(0, 14))

            best_cagr_m   = max(active_modes, key=lambda m: m["stats"]["cagr_pct"])
            best_calmar_m = max(active_modes, key=lambda m: m["stats"].get("calmar", 0))
            best_mdd_m    = max(active_modes, key=lambda m: -m["stats"].get("mdd_pct", -99))

            bc_cagr    = best_cagr_m["stats"]["cagr_pct"]
            bcal       = best_calmar_m["stats"].get("calmar", 0)
            bm_mdd     = best_mdd_m["stats"].get("mdd_pct", 0)
            gap        = bnh_cagr - bc_cagr   # B&H vs best signal

            # ── 三格指標卡 ──────────────────────────────────────────────
            r1 = ctk.CTkFrame(conc, fg_color="transparent")
            r1.pack(fill="x", padx=10, pady=(8, 4))
            cagr_winner_lbl = "B&H" if bnh_cagr >= bc_cagr else best_cagr_m["label"]
            cagr_winner_val = bnh_cagr if bnh_cagr >= bc_cagr else bc_cagr
            for title, val, sub, clr in [
                ("CAGR 最高",
                 f"{cagr_winner_val:.1f}%",
                 cagr_winner_lbl,
                 C_YELLOW),
                ("Calmar 最佳（信號）",
                 f"{bcal:.2f}",
                 best_calmar_m["label"],
                 C_GREEN if bcal >= 0.3 else (C_YELLOW if bcal >= 0.15 else C_GRAY)),
                ("MDD 最低（信號）",
                 f"{bm_mdd:.1f}%",
                 best_mdd_m["label"],
                 C_GREEN if bm_mdd > -15 else (C_YELLOW if bm_mdd > -25 else "#e17055")),
            ]:
                card2 = ctk.CTkFrame(r1, fg_color="#0f1a2a", corner_radius=6)
                card2.pack(side="left", padx=6, pady=2)
                ctk.CTkLabel(card2, text=title, font=(self.ui_font, 12),
                             text_color=C_GRAY).pack(pady=(5, 0), padx=14)
                ctk.CTkLabel(card2, text=val, font=(self.ui_font, 14, "bold"),
                             text_color=clr).pack(padx=14)
                ctk.CTkLabel(card2, text=sub, font=(self.ui_font, 12),
                             text_color=C_GRAY).pack(pady=(0, 5), padx=14)

            # ── 信號 vs B&H vs DCA 差距行 ──────────────────────────────
            r2 = ctk.CTkFrame(conc, fg_color="transparent")
            r2.pack(fill="x", padx=14, pady=(2, 2))
            gap_clr = C_GREEN if gap < 2 else (C_YELLOW if gap < 6 else "#e17055")
            sig_line = (f"最佳信號策略  {best_cagr_m['label']}  CAGR {bc_cagr:.1f}%"
                        f"   vs   B&H {bnh_cagr:.1f}%   差距 {gap:+.1f}%")
            ctk.CTkLabel(r2, text=sig_line, font=(self.ui_font, 12),
                         text_color=gap_clr).pack(side="left")

            # DCA 比較（若有快取）
            try:
                dca_strats_for_conc = dca_row.get("strategies", []) if dca_row else []
                if dca_strats_for_conc:
                    best_dca = max(dca_strats_for_conc, key=lambda s: s.get("cagr_pct", 0))
                    dca_cagr = best_dca.get("cagr_pct", 0)
                    dca_gap  = bnh_cagr - dca_cagr
                    dca_line = f"   │   最佳 DCA  {best_dca['label']}  {dca_cagr:.1f}%  差距 {dca_gap:+.1f}%"
                    ctk.CTkLabel(r2, text=dca_line, font=(self.ui_font, 12),
                                 text_color=C_GRAY).pack(side="left")
            except Exception:
                pass

            # ── 股災應對分析 ──────────────────────────────────────────────
            bt_start = result.get("start_date", "2015-01-01")
            bt_end_r = result.get("end_date",   "2025-12-31")
            in_range = [(nm, cs, ce) for nm, cs, ce in CRASH_PERIODS
                        if cs <= bt_end_r and ce >= bt_start]

            rep_trades = best_calmar_m.get("trades", [])
            crash_entries_total = 0
            crash_trail_total   = 0
            crash_no_signal     = 0

            if in_range:
                rc_hdr = ctk.CTkFrame(conc, fg_color="transparent")
                rc_hdr.pack(fill="x", padx=14, pady=(6, 0))
                ctk.CTkLabel(rc_hdr, text="📉 歷次股災應對（代表策略：" + best_calmar_m["label"] + "）",
                             font=(self.ui_font, 12, "bold"), text_color=C_YELLOW
                             ).pack(side="left")
                ctk.CTkLabel(rc_hdr,
                             text="（B&H 無條件持有，依賴長期均值回歸）",
                             font=(self.ui_font, 12), text_color=C_GRAY
                             ).pack(side="left", padx=6)

                for crash_name, cs, ce in in_range:
                    st = _crash_analysis(rep_trades, cs, ce)
                    crash_entries_total += st["entries"]
                    crash_trail_total   += st["trail_exits"]

                    parts = []
                    if st["entries"] > 0:
                        parts.append(f"逢低進場 {st['entries']} 次 ✓")
                    if st["trail_exits"] > 0:
                        parts.append(f"追蹤止盈出場 {st['trail_exits']} 次 ✓")
                    if st["sell_exits"] > 0:
                        parts.append(f"SELL 出場 {st['sell_exits']} 次")
                    if st["pre_exits"] > 0:
                        parts.append(f"股災前 45 天已鎖利出場 {st['pre_exits']} 次 ✓")
                    if not parts:
                        parts.append("無信號觸發，同 B&H 持有")
                        crash_no_signal += 1

                    sig_desc = "；".join(parts)
                    # B&H always holds
                    bnh_desc = "持有不動（靠時間復甦）"
                    row_clr = "#7fba5a" if st["entries"] > 0 or st["trail_exits"] > 0 or st["pre_exits"] > 0 else "#a0a0b8"
                    row_txt = f"  【{crash_name} {cs[:7]}~{ce[:7]}】  信號：{sig_desc}　｜　B&H：{bnh_desc}"
                    ctk.CTkLabel(conc, text=row_txt,
                                 font=(self.ui_font, 12), text_color=row_clr,
                                 wraplength=860, justify="left"
                                 ).pack(anchor="w", padx=12, pady=(1, 0))

            # ── 建議文字 ────────────────────────────────────────────────
            if gap < 1.5:
                verdict = (f"信號策略與 B&H CAGR 差距極小（{gap:.1f}%），"
                           f"但 MDD {bm_mdd:.1f}% 顯著低於 B&H，適合風險控管優先的投資人。"
                           f" 推薦：{best_mdd_m['label']} 作為主策略。")
            elif gap < 5:
                verdict = (f"信號策略 CAGR 較 B&H 少 {gap:.1f}%，"
                           f"但 Calmar {bcal:.2f} 代表每承受 1% 回撤能獲得 {bcal:.2f}% 報酬，風險報酬比可接受。"
                           f" 追求報酬選 B&H；在意下行保護選 {best_calmar_m['label']}。")
            elif gap < 10:
                verdict = (f"B&H 優勢較明顯（差距 {gap:.1f}%）。"
                           f"信號策略主要價值在降低 MDD（{bm_mdd:.1f}%），但犧牲了部分報酬。"
                           f" 建議以 B&H 為主，遇 STRONG BUY 時加碼（{best_calmar_m['label']} 模式）。")
            else:
                verdict = (f"此股票長期趨勢強勁，B&H 大幅領先（差距 {gap:.1f}%）。"
                           f"信號策略因訊號過保守或頻繁錯過強勢段，不建議主動操作。"
                           f" 直接持有為最優解；Calmar {bcal:.2f} 僅供參考。")

            # 把股災行為融入建議文字
            if crash_entries_total > 0:
                verdict += (f" 歷次股災期間逢低進場共 {crash_entries_total} 次，"
                            f"DCA 效果可攤低持倉成本，有助回升後超越 B&H。")
            elif in_range and crash_no_signal == len(in_range):
                verdict += (" 歷次股災期間均無 BUY 信號觸發（條件過嚴或已深套），"
                            "策略與 B&H 同等待機，無法主動攤平。")
            if crash_trail_total > 0:
                verdict += (f" 追蹤止盈在股災期間觸發 {crash_trail_total} 次，"
                            f"部分鎖定了高點利潤，降低了實際持倉虧損。")

            # ── 未來改善空間 ─────────────────────────────────────────────
            tips = []
            if gap >= 8:
                tips.append("Walk-Forward 驗證：確認策略是否真的失效，或只是參數需再調整")
            if abs(bm_mdd) >= 25:
                tips.append(f"追蹤止盈（Trailing Stop 15%）：MDD {bm_mdd:.1f}% 偏高，可提前鎖定回撤期利潤")
            if bcal < 0.2 and gap < 10:
                tips.append("動態倉位（Dynamic Sizing）：Calmar 偏低，回撤擴大時縮小投入可改善風險報酬比")
            if in_range and crash_no_signal == len(in_range):
                tips.append("股災期間從未觸發進場：考慮在大盤 DD>20% 時自動放寬 RSI 門檻以捕捉底部機會")
            if gap >= 5 and gap < 8:
                tips.append("嘗試放寬 AVWAP 乘數（b1/b2）或 RSI 閾值，信號過嚴可能錯過太多進場機會")
            if not tips:
                tips.append("目前各指標表現均衡。建議定期執行 Walk-Forward 驗證，確認策略在新市場環境持續有效")

            r3 = ctk.CTkFrame(conc, fg_color="#0d1525", corner_radius=4)
            r3.pack(fill="x", padx=10, pady=(4, 10))
            ctk.CTkLabel(r3, text=f"💡 {verdict}",
                         font=(self.ui_font, 12), text_color=C_WHITE,
                         wraplength=840, justify="left"
                         ).pack(anchor="w", padx=12, pady=(7, 2))
            ctk.CTkLabel(r3,
                         text="🔧 改善空間：" + "　|　".join(tips),
                         font=(self.ui_font, 12), text_color="#a0b8d0",
                         wraplength=840, justify="left"
                         ).pack(anchor="w", padx=12, pady=(2, 7))

    # ════════════════════════════════════════════════════════════════════
    # 跟單回測 資產曲線圖
    # ════════════════════════════════════════════════════════════════════

    def _sbt_equity_series(self, trades: list[dict], close,
                           annual_budget: float, max_inject_yrs: int,
                           start_yr: int, is_bnh: bool = False):
        """Reconstruct daily portfolio equity from trade records + price series."""
        import pandas as pd

        entries_by_date: dict = {}
        exits_by_date:   dict = {}
        for t in trades:
            ed   = t.get("entry_date", "")
            xd   = t.get("exit_date", "")
            xsig = t.get("exit_signal", "")
            sh   = t.get("shares", 0)
            co   = t.get("cost", 0)
            pr   = t.get("proceeds", 0)
            if ed:
                entries_by_date.setdefault(ed, []).append({"shares": sh, "cost": co})
            if xd and xsig != "PERIOD_END":
                exits_by_date.setdefault(xd, []).append({"shares": sh, "proceeds": pr})

        seen_years:  set   = set()
        inject_count: int  = 0
        cash:        float = 0.0
        open_shares: float = 0.0
        equity_vals        = []
        equity_idx         = []

        for dt, price in close.items():
            yr       = dt.year
            date_str = dt.date().isoformat()

            if yr not in seen_years:
                seen_years.add(yr)
                if inject_count < max_inject_yrs:
                    cash         += annual_budget
                    inject_count += 1

            for ex in exits_by_date.get(date_str, []):
                open_shares -= ex["shares"]
                cash        += ex["proceeds"]

            for en in entries_by_date.get(date_str, []):
                open_shares += en["shares"]
                cash        -= en["cost"]

            equity_vals.append(cash + open_shares * float(price))
            equity_idx.append(dt)

        return pd.Series(equity_vals, index=pd.Index(equity_idx))

    def _sbt_chart_popup(self, sym: str, name: str, result: dict):
        import tkinter as tk
        win = tk.Toplevel(self)
        win.title(f"{sym.replace('.TW', '')} {name}  跟單回測資產曲線")
        win.geometry("900x560")
        win.configure(bg="#0f1a30")
        win.lift()

        lbl = tk.Label(win, text="載入價格資料中…",
                       fg="#74b9ff", bg="#0f1a30",
                       font=(self.ui_font, 12))
        lbl.pack(expand=True)

        def _bg():
            try:
                import yfinance as yf
                import pandas as pd
                from zoneinfo import ZoneInfo

                start    = result.get("start_date", START_DATE)
                end      = result.get("end_date",   END_DATE)
                start_yr = int(start[:4])
                end_yr   = int(end[:4])
                max_inject_yrs = end_yr - start_yr

                annual_budget = result.get("params", {}).get("annual_budget", 100_000)

                tz = ZoneInfo("Asia/Taipei")
                ticker = yf.Ticker(sym)
                df = ticker.history(start=start, end=end, auto_adjust=True)
                if df.empty or len(df) < 100:
                    self.after(0, lambda: lbl.configure(text="資料不足，無法繪圖"))
                    return

                if df.index.tzinfo is None:
                    df.index = df.index.tz_localize(tz)
                else:
                    df.index = df.index.tz_convert(tz)
                close = df["Close"].dropna()

                curves: dict[str, pd.Series] = {}

                bnh_txs = result.get("bnh", {}).get("transactions", [])
                curves["B&H（年初買入）"] = self._sbt_equity_series(
                    bnh_txs, close, annual_budget, max_inject_yrs, start_yr, is_bnh=True)

                for m in result.get("modes", []):
                    curves[m["label"]] = self._sbt_equity_series(
                        m.get("trades", []), close, annual_budget,
                        max_inject_yrs, start_yr)

                # 找最佳 Calmar 策略的 trades 供進出場標記用
                rep_trades = []
                active_m = [m for m in result.get("modes", [])
                            if m["stats"]["n_trades"] > 0]
                if active_m:
                    best_m = max(active_m,
                                 key=lambda m: m["stats"].get("calmar", 0))
                    rep_trades = best_m.get("trades", [])

                self.after(0, lambda: _draw(win, lbl, curves, close, rep_trades))
            except Exception as e:
                self.after(0, lambda: lbl.configure(text=f"繪圖失敗：{e}", fg="#e74c3c"))

        def _draw(win, lbl, curves, close, rep_trades=None):
            try:
                import matplotlib
                matplotlib.use("TkAgg")
                import matplotlib.pyplot as plt
                from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
                import matplotlib.ticker as mticker

                lbl.destroy()
                fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(9, 7.5),
                                               gridspec_kw={"height_ratios": [3, 1, 1]},
                                               facecolor="#0f1a30")
                fig.subplots_adjust(hspace=0.08, left=0.09, right=0.98, top=0.93, bottom=0.06)

                colors = ["#74b9ff", "#2ecc71", "#1abc9c", "#f39c12", "#e74c3c", "#a29bfe"]
                for i, (label, series) in enumerate(curves.items()):
                    if series is not None and len(series) > 0:
                        lw = 2.0 if "B&H" in label else (1.8 if "過濾" in label else 1.4)
                        ls = "--" if "B&H" in label else "-"
                        ax1.plot(series.index, series / 1000, label=label,
                                 color=colors[i % len(colors)],
                                 linewidth=lw, linestyle=ls)

                # 進出場三角標記（最佳 Calmar 策略）
                if rep_trades:
                    try:
                        import pandas as _pd_mk
                        from zoneinfo import ZoneInfo as _ZI_mk
                        _tz_mk = _ZI_mk("Asia/Taipei")
                        # 找曲線（第一條非B&H的曲線）
                        _rep_series = next(
                            (s for lbl2, s in curves.items()
                             if "B&H" not in lbl2 and s is not None and len(s) > 0),
                            None)
                        if _rep_series is not None:
                            _idx = _rep_series.index
                            for _t in rep_trades:
                                # 進場標記 ▲ 綠色
                                _ed = _t.get("entry_date", "")
                                if _ed:
                                    try:
                                        _ts = _pd_mk.Timestamp(_ed, tz=_tz_mk)
                                        _loc = _idx.get_loc(_ts, method="nearest")
                                        _val = _rep_series.iloc[_loc] / 1000
                                        ax1.scatter(_idx[_loc], _val,
                                                    marker="^", color="#00b894",
                                                    s=28, zorder=5, alpha=0.85)
                                    except Exception:
                                        pass
                                # 出場標記 ▼ 紅色（期末持倉不標）
                                _xd  = _t.get("exit_date", "")
                                _xsg = _t.get("exit_signal", "")
                                if _xd and _xsg != "PERIOD_END":
                                    try:
                                        _ts = _pd_mk.Timestamp(_xd, tz=_tz_mk)
                                        _loc = _idx.get_loc(_ts, method="nearest")
                                        _val = _rep_series.iloc[_loc] / 1000
                                        _clr = ("#1abc9c" if _xsg in ("TRIM", "TRAILING_STOP")
                                                else "#e74c3c")
                                        ax1.scatter(_idx[_loc], _val,
                                                    marker="v", color=_clr,
                                                    s=28, zorder=5, alpha=0.85)
                                    except Exception:
                                        pass
                    except Exception:
                        pass

                # 熊市期間（TWII MA200 斜率 ≤ 0）灰底標示
                try:
                    import yfinance as _yf
                    from zoneinfo import ZoneInfo as _ZI
                    _tz = _ZI("Asia/Taipei")
                    _start = result.get("start_date", START_DATE)
                    _end   = result.get("end_date",   END_DATE)
                    _bf = None
                    from tw_backtest_signals import _fetch_twii_bull_series
                    _bf = _fetch_twii_bull_series(_start, _end)
                    if _bf is not None and len(_bf) > 0:
                        _bear = _bf[~_bf]
                        if len(_bear) > 0:
                            _in_bear = False
                            _bear_start = None
                            for _dt, _val in _bf.items():
                                if not _val and not _in_bear:
                                    _in_bear = True
                                    _bear_start = _dt
                                elif _val and _in_bear:
                                    ax1.axvspan(_bear_start, _dt,
                                                color="#e74c3c", alpha=0.08, linewidth=0)
                                    _in_bear = False
                            if _in_bear and _bear_start:
                                ax1.axvspan(_bear_start, _bf.index[-1],
                                            color="#e74c3c", alpha=0.08, linewidth=0)
                except Exception:
                    pass

                ax1.set_facecolor("#0d1b2a")
                ax1.tick_params(colors="#95a5a6", labelsize=9)
                ax1.yaxis.set_major_formatter(
                    mticker.FuncFormatter(lambda x, _: f"NT${x:.0f}K"))
                ax1.legend(fontsize=8, facecolor="#1a2a40", labelcolor="#ecf0f1",
                           loc="upper left", framealpha=0.8)
                ax1.set_title(f"{sym.replace('.TW', '')} {name}  信號策略資產曲線（NT$ 千元）",
                              color="#74b9ff", fontsize=11)
                ax1.grid(color="#1e3a5f", linewidth=0.4)
                ax1.spines[:].set_color("#2a3a5a")
                ax1.tick_params(labelbottom=False)

                ax2.fill_between(close.index,
                                 (close / close.rolling(60).max() - 1) * 100,
                                 0, alpha=0.4, color="#e74c3c")
                ax2.axhline(y=-10, color="#f39c12", linewidth=0.6, linestyle="--")
                ax2.axhline(y=-20, color="#e74c3c", linewidth=0.6, linestyle="--")

                # 股災事件標注（於回撤子圖底部）
                try:
                    import pandas as _pd_c
                    from zoneinfo import ZoneInfo as _ZI_c
                    _tz_c = _ZI_c("Asia/Taipei")
                    _cs_str = result.get("start_date", START_DATE)
                    _ce_str = result.get("end_date",   END_DATE)
                    for c_nm, c_s, c_e in CRASH_PERIODS:
                        if c_s <= _ce_str and c_e >= _cs_str:
                            try:
                                _cs_dt = _pd_c.Timestamp(c_s, tz=_tz_c)
                                _ce_dt = _pd_c.Timestamp(c_e, tz=_tz_c)
                                ax2.axvspan(_cs_dt, _ce_dt,
                                            color="#ff6b6b", alpha=0.20, linewidth=0)
                                ax2.text(_cs_dt, 0.04, f" {c_nm}",
                                         color="#ffaaaa", fontsize=7,
                                         transform=ax2.get_xaxis_transform(),
                                         va="bottom", clip_on=True)
                            except Exception:
                                pass
                except Exception:
                    pass

                ax2.set_facecolor("#0d1b2a")
                ax2.set_ylabel("股價DD%", color="#95a5a6", fontsize=8)
                ax2.tick_params(colors="#95a5a6", labelsize=8)
                ax2.grid(color="#1e3a5f", linewidth=0.3)
                ax2.spines[:].set_color("#2a3a5a")
                ax2.tick_params(labelbottom=False)

                # ── 水下曲線（各策略組合回撤）─────────────────────────────────
                for i, (label, series) in enumerate(curves.items()):
                    if series is None or len(series) == 0:
                        continue
                    uw = (series / series.cummax() - 1) * 100
                    c  = colors[i % len(colors)]
                    ax3.fill_between(series.index, uw, 0, alpha=0.25, color=c)
                    ax3.plot(series.index, uw, color=c, linewidth=0.8,
                             linestyle="--" if "B&H" in label else "-")
                ax3.axhline(y=-10, color="#f39c12", linewidth=0.6, linestyle="--", alpha=0.7)
                ax3.axhline(y=-20, color="#e74c3c", linewidth=0.6, linestyle="--", alpha=0.7)
                ax3.set_facecolor("#0d1b2a")
                ax3.set_ylabel("水下%", color="#95a5a6", fontsize=8)
                ax3.tick_params(colors="#95a5a6", labelsize=8)
                ax3.grid(color="#1e3a5f", linewidth=0.3)
                ax3.spines[:].set_color("#2a3a5a")

                canvas = FigureCanvasTkAgg(fig, master=win)
                canvas.draw()
                canvas.get_tk_widget().pack(fill="both", expand=True)
            except ImportError:
                tk.Label(win, text="需安裝 matplotlib：pip install matplotlib",
                         fg="#f39c12", bg="#0f1a30",
                         font=(self.ui_font, 12)).pack(expand=True)

        threading.Thread(target=_bg, daemon=True).start()

    # ── K 線圖 Popup ───────────────────────────────────────────────────────

    def _on_scan_row_dblclick(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        vals = self.tree.item(item, "values")
        if not vals:
            return
        sym_raw = str(vals[0]).strip()
        name    = str(vals[1]).strip() if len(vals) > 1 else sym_raw
        symbol  = sym_raw if sym_raw.endswith(".TW") else f"{sym_raw}.TW"
        self._chart_popup(symbol, name)

    def _chart_popup(self, symbol: str, name: str, days: int = 120):
        import tkinter as tk

        win = tk.Toplevel(self)
        win.title(f"{symbol.replace('.TW','')}  {name}  — K 線圖")
        win.geometry("960x680")
        win.configure(bg="#0f1a30")
        win.lift()

        # ── 標題列 ───────────────────────────────────────────────────────────
        ctrl = tk.Frame(win, bg="#0f1a30")
        ctrl.pack(fill="x", padx=10, pady=(6, 0))
        tk.Label(ctrl, text=f"{name}（{symbol.replace('.TW','')}）",
                 fg="#74b9ff", bg="#0f1a30", font=(self.ui_font, 12, "bold")).pack(side="left", padx=8)

        # ── 基本面資訊列 ─────────────────────────────────────────────────────
        info_frame = tk.Frame(win, bg="#0f1a30")
        info_frame.pack(fill="x", padx=14, pady=(2, 0))
        info_lbl = tk.Label(info_frame, text="基本面載入中…",
                            fg="#636e72", bg="#0f1a30", font=(self.ui_font, 11))
        info_lbl.pack(side="left")

        # ── 週期 + 範圍切換列 ────────────────────────────────────────────────
        period_frame = tk.Frame(win, bg="#0f1a30")
        period_frame.pack(fill="x", padx=10, pady=(4, 2))

        canvas_holder = [None]
        _period_var   = tk.StringVar(value="日K")

        lbl_load = tk.Label(win, text="載入中…", fg="#a8e6cf", bg="#0f1a30",
                            font=(self.ui_font, 12))
        lbl_load.pack(expand=True)

        _BTN  = dict(bg="#1a2a40", fg="#dce8f0", activebackground="#2d4a6a",
                     activeforeground="white", font=(self.ui_font, 12), relief="flat",
                     padx=8, pady=3)
        _ABTN = dict(bg="#2d5a8e", fg="#ffffff", activebackground="#3a6090",
                     activeforeground="white", font=(self.ui_font, 12), relief="flat",
                     padx=8, pady=3)

        range_frame   = tk.Frame(period_frame, bg="#0f1a30")
        range_frame.pack(side="right")
        period_btns   = {}

        _RANGES = {
            "日K": [(60, "60日"), (120, "120日"), (180, "180日"), (365, "1年")],
            "週K": [(180, "6個月"), (365, "1年"), (730, "2年"), (1095, "3年")],
            "月K": [(365, "1年"), (1095, "3年"), (1825, "5年"), (3650, "10年")],
        }

        def _rebuild_range_btns(period, auto_load=True):
            for w in range_frame.winfo_children():
                w.destroy()
            default_days = _RANGES[period][1][0]
            for n, label in _RANGES[period]:
                tk.Button(range_frame, text=label, **_BTN,
                          command=lambda d=n: _load(d, _period_var.get())
                          ).pack(side="left", padx=2)
            if auto_load:
                _load(default_days, period)

        def _set_period(p):
            _period_var.set(p)
            for btn, pv in period_btns.items():
                btn.configure(**(dict(_ABTN) if pv == p else dict(_BTN)))
            _rebuild_range_btns(p)

        for p in ["日K", "週K", "月K"]:
            b = tk.Button(period_frame, text=p, **(_ABTN if p == "日K" else _BTN),
                          command=lambda pv=p: _set_period(pv))
            b.pack(side="left", padx=2)
            period_btns[b] = p

        # ── 主載入函式 ───────────────────────────────────────────────────────
        def _load(n_days, period="日K"):
            if win.winfo_exists():
                lbl_load.configure(text="載入中…", fg="#a8e6cf")
                lbl_load.pack(expand=True)
            if canvas_holder[0]:
                try:
                    canvas_holder[0].get_tk_widget().destroy()
                except Exception:
                    pass
                canvas_holder[0] = None

            def _bg():
                try:
                    import yfinance as yf
                    import pandas as pd
                    import matplotlib
                    matplotlib.use("TkAgg")
                    import matplotlib.pyplot as plt
                    import matplotlib.patches as mpatches
                    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

                    end_dt   = pd.Timestamp.today()
                    # Fetch extra history for MA warmup
                    fetch_days = max(n_days + 300, n_days * 2)
                    start_dt = end_dt - pd.Timedelta(days=fetch_days)
                    df_raw = yf.Ticker(symbol).history(
                        start=start_dt.strftime("%Y-%m-%d"),
                        end=end_dt.strftime("%Y-%m-%d"),
                        auto_adjust=True,
                    )
                    if df_raw.empty:
                        if win.winfo_exists():
                            win.after(0, lambda: lbl_load.configure(text="無資料", fg="#e74c3c"))
                        return

                    # Resample to weekly / monthly
                    if period == "週K":
                        df_all = df_raw.resample("W").agg(
                            {"Open": "first", "High": "max", "Low": "min",
                             "Close": "last",  "Volume": "sum"}
                        ).dropna()
                        n_bars   = max(10, n_days // 7)
                        date_fmt = "%y/%m/%d"
                    elif period == "月K":
                        df_all = df_raw.resample("ME").agg(
                            {"Open": "first", "High": "max", "Low": "min",
                             "Close": "last",  "Volume": "sum"}
                        ).dropna()
                        n_bars   = max(6, n_days // 30)
                        date_fmt = "%Y/%m"
                    else:
                        df_all   = df_raw
                        n_bars   = n_days
                        date_fmt = "%m/%d"

                    df = df_all.tail(n_bars)
                    if df.empty or len(df) < 3:
                        if win.winfo_exists():
                            win.after(0, lambda: lbl_load.configure(text="資料不足", fg="#e74c3c"))
                        return

                    # 信號標記（僅日K）
                    sigs = None
                    if period == "日K":
                        try:
                            from tw_backtest_signals import _daily_signals, _fetch
                            sig_df = _fetch(symbol,
                                            start=(end_dt - pd.Timedelta(days=n_days + 300)).strftime("%Y-%m-%d"),
                                            end=end_dt.strftime("%Y-%m-%d"))
                            if not sig_df.empty and len(sig_df) >= 100:
                                sigs = _daily_signals(sig_df, symbol).tail(n_days)
                        except Exception:
                            pass

                    def _draw():
                        if not win.winfo_exists():
                            return
                        lbl_load.pack_forget()
                        plt.rcParams.update({
                            "font.sans-serif": ["Microsoft JhengHei", "SimHei", "sans-serif"],
                            "axes.unicode_minus": False,
                        })
                        fig, (ax1, ax2) = plt.subplots(
                            2, 1, figsize=(9.2, 5.2),
                            gridspec_kw={"height_ratios": [4, 1]},
                            facecolor="#0f1a30"
                        )
                        fig.subplots_adjust(hspace=0.04, left=0.07, right=0.97, top=0.94, bottom=0.07)

                        closes = df["Close"]
                        opens  = df["Open"]
                        highs  = df["High"]
                        lows   = df["Low"]
                        vols   = df["Volume"]
                        dates  = list(range(len(df)))
                        idx    = df.index

                        # 蠟燭圖
                        for i, (dt, row) in enumerate(df.iterrows()):
                            o, c, h, l = row["Open"], row["Close"], row["High"], row["Low"]
                            color = "#26a69a" if c >= o else "#ef5350"
                            ax1.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=1)
                            rect = mpatches.FancyBboxPatch(
                                (i - 0.35, min(o, c)), 0.7, abs(c - o) or 0.01,
                                boxstyle="square,pad=0", linewidth=0,
                                facecolor=color, zorder=2,
                            )
                            ax1.add_patch(rect)

                        # MA 線（使用全部歷史資料計算，取最後 n_bars）
                        for ma_n, color, lw in [(20, "#f39c12", 1.0), (60, "#9b59b6", 0.8)]:
                            ma    = df_all["Close"].rolling(ma_n).mean().tail(n_bars)
                            valid = ma.dropna()
                            if len(valid) > 0:
                                vi = [i for i, d in enumerate(idx) if d in valid.index]
                                ax1.plot(vi, valid.values, color=color,
                                         linewidth=lw, alpha=0.8, label=f"MA{ma_n}")

                        # 信號標記（日K only）
                        if sigs is not None:
                            for dt, row in sigs.iterrows():
                                if dt not in df.index:
                                    continue
                                i   = df.index.get_loc(dt)
                                sig = row["signal"]
                                if sig == "STRONG BUY":
                                    ax1.annotate("▲", xy=(i, float(lows.iloc[i]) * 0.993),
                                                 fontsize=8, color="#ff6b35", ha="center",
                                                 fontweight="bold")
                                elif sig == "BUY":
                                    ax1.annotate("△", xy=(i, float(lows.iloc[i]) * 0.993),
                                                 fontsize=7, color="#2ecc71", ha="center")
                                elif sig == "SELL":
                                    ax1.annotate("▽", xy=(i, float(highs.iloc[i]) * 1.007),
                                                 fontsize=7, color="#e74c3c", ha="center")

                        # X 軸
                        step = max(1, len(dates) // 8)
                        ax1.set_xticks(dates[::step])
                        ax1.set_xticklabels(
                            [idx[i].strftime(date_fmt) for i in dates[::step]],
                            fontsize=7, color="#95a5a6",
                        )
                        ax1.set_xlim(-1, len(dates))
                        ax1.set_facecolor("#0d1b2a")
                        period_label = {"日K": f"{n_days}日", "週K": "週K", "月K": "月K"}[period]
                        ax1.set_title(f"{name}（{symbol.replace('.TW','')}）  {period_label} K 線",
                                      color="#74b9ff", fontsize=10)
                        ax1.tick_params(colors="#95a5a6", labelsize=8, labelbottom=False)
                        ax1.grid(color="#1e3a5f", linewidth=0.3, axis="y")
                        ax1.spines[:].set_color("#2a3a5a")

                        # 圖例
                        from matplotlib.lines import Line2D
                        sig_handles = []
                        if sigs is not None:
                            sig_handles = [
                                Line2D([0], [0], marker="^", color="w", markerfacecolor="#ff6b35",
                                       markersize=7, label="STRONG BUY", linestyle="None"),
                                Line2D([0], [0], marker="^", color="w", markerfacecolor="#2ecc71",
                                       markersize=6, label="BUY", linestyle="None"),
                                Line2D([0], [0], marker="v", color="w", markerfacecolor="#e74c3c",
                                       markersize=6, label="SELL", linestyle="None"),
                            ]
                        ma_handles = ax1.get_legend_handles_labels()[0]
                        all_handles = ma_handles + sig_handles
                        if all_handles:
                            ax1.legend(handles=all_handles, loc="upper left", fontsize=7,
                                       facecolor="#0d1b2a", labelcolor="#dce8f0",
                                       framealpha=0.6, ncol=3)

                        # 成交量
                        vol_colors = ["#26a69a" if df["Close"].iloc[i] >= df["Open"].iloc[i]
                                      else "#ef5350" for i in range(len(df))]
                        ax2.bar(dates, vols / 1_000_000, color=vol_colors, alpha=0.7, width=0.8)
                        ax2.set_facecolor("#0d1b2a")
                        ax2.set_ylabel("量(M)", color="#95a5a6", fontsize=7)
                        ax2.set_xlim(-1, len(dates))
                        ax2.set_xticks(dates[::step])
                        ax2.set_xticklabels(
                            [idx[i].strftime(date_fmt) for i in dates[::step]],
                            fontsize=7, color="#95a5a6",
                        )
                        ax2.tick_params(colors="#95a5a6", labelsize=7)
                        ax2.grid(color="#1e3a5f", linewidth=0.3, axis="y")
                        ax2.spines[:].set_color("#2a3a5a")

                        canv = FigureCanvasTkAgg(fig, master=win)
                        canv.draw()
                        canv.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=(0, 4))
                        canvas_holder[0] = canv

                    win.after(0, _draw)
                except Exception as e:
                    if win.winfo_exists():
                        win.after(0, lambda: lbl_load.configure(text=f"Error: {e}", fg="#e74c3c"))

            threading.Thread(target=_bg, daemon=True).start()

        # ── 基本面背景載入 ───────────────────────────────────────────────────
        def _load_fundamentals():
            try:
                import yfinance as yf
                tk_obj   = yf.Ticker(symbol)
                info     = tk_obj.info
                parts    = []
                pe = info.get("trailingPE") or info.get("forwardPE")
                if pe and pe > 0:
                    parts.append(f"P/E: {pe:.1f}x")
                eps = info.get("trailingEps")
                if eps:
                    parts.append(f"EPS: {eps:.2f}")
                dy = info.get("dividendYield")
                if dy:
                    parts.append(f"殖利率 (Yield): {dy * 100:.2f}%")
                rev_g = info.get("revenueGrowth")
                if rev_g is not None:
                    parts.append(f"營收成長 (Rev.G): {rev_g * 100:.1f}%")
                roe = info.get("returnOnEquity")
                if roe is not None:
                    parts.append(f"ROE: {roe * 100:.1f}%")
                text = "  |  ".join(parts) if parts else "無基本面資料"
                if win.winfo_exists():
                    win.after(0, lambda: info_lbl.configure(text=text, fg="#b8d4f0"))
            except Exception:
                if win.winfo_exists():
                    win.after(0, lambda: info_lbl.configure(text="基本面資料不可用", fg="#636e72"))

        threading.Thread(target=_load_fundamentals, daemon=True).start()

        # 初始範圍按鈕（日K）
        _rebuild_range_btns("日K", auto_load=False)
        _load(days, "日K")

    # ── 組合回測 Popup ──────────────────────────────────────────────────────
    def _sbt_portfolio_popup(self):
        import tkinter as tk
        from tkinter import ttk as _ttv

        start = f"{self._sbt_start_var.get()}-01-01"
        end   = f"{self._sbt_end_var.get()}-12-31"

        win = tk.Toplevel(self)
        win.title(f"Portfolio Backtest  {start[:4]}-{end[:4]}")
        win.geometry("960x820")
        win.configure(bg="#0f1a30")
        win.lift()

        cfg = _load_config()
        etf_syms  = [s for s in cfg["watchlist"]["etf"]     if not s.get("backtest_only")]
        tech_syms = [s for s in cfg["watchlist"]["ai_tech"]  if not s.get("backtest_only")]
        all_syms  = etf_syms + tech_syms

        # ── 設定面板（首頁）──
        sf = tk.Frame(win, bg="#0f1a30")
        sf.pack(fill="both", expand=True, padx=30, pady=30)

        tk.Label(sf, text="⚙  組合回測設定",
                 fg="#74b9ff", bg="#0f1a30",
                 font=(self.ui_font, 14, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 24))

        def _row(r, label, widget_fn):
            tk.Label(sf, text=label, fg="#dce8f0", bg="#0f1a30",
                     font=(self.ui_font, 12), anchor="e", width=20
                     ).grid(row=r, column=0, sticky="e", padx=(0, 14), pady=10)
            w = widget_fn()
            w.grid(row=r, column=1, sticky="w", pady=10)
            return w

        inj_var = tk.StringVar(value="100,000")
        _row(1, "每年注資 (NT$):",
             lambda: tk.Entry(sf, textvariable=inj_var, width=16,
                              bg="#1a2a40", fg="#dce8f0", relief="flat",
                              insertbackground="#dce8f0", font=(self.ui_font, 12)))

        lot_etf_var = tk.StringVar(value="15%")
        _row(2, "ETF 保守每筆比例:",
             lambda: _ttv.Combobox(sf, textvariable=lot_etf_var, state="readonly",
                                   values=["10%", "15%", "20%", "25%", "30%"], width=14,
                                   font=(self.ui_font, 12)))

        lot_tech_var = tk.StringVar(value="30%")
        _row(3, "科技股 衝刺每筆比例:",
             lambda: _ttv.Combobox(sf, textvariable=lot_tech_var, state="readonly",
                                   values=["15%", "20%", "30%", "40%", "50%"], width=14,
                                   font=(self.ui_font, 12)))

        sbuy_var = tk.StringVar(value="1.5×")
        _row(4, "STRONG BUY 倍率:",
             lambda: _ttv.Combobox(sf, textvariable=sbuy_var, state="readonly",
                                   values=["1.0×", "1.5×", "2.0×", "3.0×"], width=14,
                                   font=(self.ui_font, 12)))

        scope_choices = [
            f"全部（{len(all_syms)} 檔）",
            f"僅ETF（{len(etf_syms)} 檔）",
            f"僅科技股（{len(tech_syms)} 檔）",
        ]
        scope_var = tk.StringVar(value=scope_choices[0])
        _row(5, "股票範圍:",
             lambda: _ttv.Combobox(sf, textvariable=scope_var, state="readonly",
                                   values=scope_choices, width=22,
                                   font=(self.ui_font, 12)))

        def _run():
            try:
                annual = float(inj_var.get().replace(",", "").replace("，", ""))
            except ValueError:
                annual = 100_000
            lot_etf   = float(lot_etf_var.get().rstrip("%")) / 100
            lot_tech  = float(lot_tech_var.get().rstrip("%")) / 100
            sbuy_mult = float(sbuy_var.get().rstrip("×"))
            scope = scope_var.get()
            if "僅ETF" in scope:
                syms = etf_syms
            elif "僅科技" in scope:
                syms = tech_syms
            else:
                syms = all_syms
            symbols_run = [{"symbol": s["symbol"], "name": s["name"]} for s in syms]

            sf.destroy()
            lbl = tk.Label(win, text="Loading... (~30s)",
                           fg="#a8e6cf", bg="#0f1a30", font=(self.ui_font, 12))
            lbl.pack(expand=True)

            def _bg():
                try:
                    from tw_backtest_signals import run_portfolio_backtest
                    r = run_portfolio_backtest(
                        symbols_run,
                        annual_injection=annual,
                        lot_pct_etf=lot_etf,
                        lot_pct_tech=lot_tech,
                        sbuy_mult=sbuy_mult,
                        start_date=start,
                        end_date=end,
                    )
                    try:
                        win.after(0, lambda: _draw(r, lbl, annual, lot_etf, lot_tech, sbuy_mult) if win.winfo_exists() else None)
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        win.after(0, lambda: lbl.configure(text=f"Error: {e}", fg="#e74c3c") if win.winfo_exists() else None)
                    except Exception:
                        pass

            threading.Thread(target=_bg, daemon=True).start()

        hybrid_var = tk.BooleanVar(value=False)
        hf = tk.Frame(sf, bg="#0f1a30")
        hf.grid(row=6, column=0, columnspan=2, sticky="w", padx=(80, 0), pady=(8, 0))
        tk.Checkbutton(hf, text="混合模式：B類同時享有年初 DCA + 信號進出",
                       variable=hybrid_var, bg="#0f1a30", fg="#a8e6cf",
                       selectcolor="#1a2a40", activebackground="#0f1a30",
                       font=(self.ui_font, 11)).pack(side="left")
        _Tooltip(hf.winfo_children()[0],
                 "勾選後：所有股票年初依評分做 DCA（50% 現金）\n"
                 "B類股票額外在信號觸發時再買入（50% 現金）\n"
                 "適合想要「底倉 + 擇時加碼」的混合策略")

        btn_row = tk.Frame(sf, bg="#0f1a30")
        btn_row.grid(row=7, column=0, columnspan=2, pady=28)

        tk.Button(btn_row, text="  執行回測 v1（手動設定）  ",
                  bg="#2d4a6a", fg="#dce8f0",
                  activebackground="#3a6090", activeforeground="white",
                  font=(self.ui_font, 12), relief="flat",
                  padx=12, pady=8, cursor="hand2",
                  command=_run).pack(side="left", padx=8)

        def _run_v2():
            try:
                annual = float(inj_var.get().replace(",", "").replace("，", ""))
            except ValueError:
                annual = 100_000
            scope = scope_var.get()
            if "僅ETF" in scope:
                syms = etf_syms
            elif "僅科技" in scope:
                syms = tech_syms
            else:
                syms = all_syms
            symbols_run = [{"symbol": s["symbol"], "name": s["name"]} for s in syms]
            hybrid = hybrid_var.get()

            sf.destroy()
            mode_lbl = "v2 混合模式 載入中... (~40s)" if hybrid else "v2 評分回測載入中... (~40s)"
            lbl = tk.Label(win, text=mode_lbl,
                           fg="#a8e6cf", bg="#0f1a30", font=(self.ui_font, 12))
            lbl.pack(expand=True)

            def _bg_v2():
                try:
                    from tw_backtest_signals import run_portfolio_backtest_v2
                    r = run_portfolio_backtest_v2(
                        symbols_run,
                        annual_injection=annual,
                        start_date=start,
                        end_date=end,
                        hybrid=hybrid,
                    )
                    try:
                        win.after(0, lambda: _draw_v2(r, lbl, annual) if win.winfo_exists() else None)
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        win.after(0, lambda: lbl.configure(text=f"Error: {e}", fg="#e74c3c") if win.winfo_exists() else None)
                    except Exception:
                        pass

            threading.Thread(target=_bg_v2, daemon=True).start()

        tk.Button(btn_row, text="  ★ v2 評分自動分配  ",
                  bg="#1e5c2e", fg="#a8e6cf",
                  activebackground="#27ae60", activeforeground="white",
                  font=(self.ui_font, 12, "bold"), relief="flat",
                  padx=12, pady=8, cursor="hand2",
                  command=_run_v2).pack(side="left", padx=8)

        def _run_sweep():
            try:
                annual = float(inj_var.get().replace(",", "").replace("，", ""))
            except ValueError:
                annual = 100_000
            scope = scope_var.get()
            if "僅ETF" in scope:
                syms = etf_syms
            elif "僅科技" in scope:
                syms = tech_syms
            else:
                syms = all_syms
            symbols_run = [{"symbol": s["symbol"], "name": s["name"]} for s in syms]

            sw = tk.Toplevel(win)
            sw.title("🔬 參數掃描結果")
            sw.geometry("820x580")
            sw.configure(bg="#0f1a30")
            sw.lift()
            from tkinter import ttk as _ttv_sw
            nb_sw = _ttv_sw.Notebook(sw)
            nb_sw.pack(fill="both", expand=True, padx=8, pady=8)

            style_sw = _ttv_sw.Style()
            style_sw.theme_use("default")
            style_sw.configure("Sw.Treeview",
                               background="#0d1b2a", foreground="#dce8f0",
                               fieldbackground="#0d1b2a", rowheight=22,
                               font=(self.ui_font, 11))
            style_sw.configure("Sw.Treeview.Heading",
                               background="#1a2a40", foreground="#74b9ff",
                               font=(self.ui_font, 11, "bold"))
            style_sw.map("Sw.Treeview", background=[("selected", "#2a4a70")])

            def _make_sweep_tab(parent, title, cols, loading_msg):
                frame = tk.Frame(parent, bg="#0f1a30")
                nb_sw.add(frame, text=title)
                lbl_sw = tk.Label(frame, text=loading_msg,
                                  fg="#a8e6cf", bg="#0f1a30", font=(self.ui_font, 11))
                lbl_sw.pack(expand=True)
                tree = _ttv_sw.Treeview(frame, style="Sw.Treeview",
                                         columns=[c for c, _ in cols],
                                         show="headings", height=16)
                for col, w in cols:
                    tree.heading(col, text=col)
                    tree.column(col, width=w, anchor="center")
                tree.tag_configure("best", foreground="#2ecc71")
                return lbl_sw, tree, frame

            regime_cols = [("門檻±%",70),("CAGR%",70),("MDD%",70),
                           ("Calmar",70),("Sharpe",70),("Sortino",70),
                           ("交易次",65),("勝率%",65),("推薦",65)]
            crash_cols  = [("設定",80),("RSI門檻",70),("跌幅門檻",70),
                           ("CAGR%",70),("MDD%",70),("Calmar",70),
                           ("Sharpe",70),("Sortino",70),("推薦",65)]

            lbl_r, tree_r, fr_r = _make_sweep_tab(nb_sw, "Regime 門檻掃描", regime_cols, "掃描中… (~3min)")
            lbl_c, tree_c, fr_c = _make_sweep_tab(nb_sw, "崩跌加碼條件掃描", crash_cols, "掃描中… (~3min)")

            alloc_cols = [("Phase",50),("標籤",90),("bull",60),("warn",60),("bear",60),
                          ("A-cash",65),("B-base",65),
                          ("CAGR%",65),("MDD%",65),("Calmar",65),("推薦",55)]
            lbl_a, tree_a, fr_a = _make_sweep_tab(nb_sw, "配比掃描", alloc_cols, "等待前兩個掃描完成…")

            def _bg_sweep():
                try:
                    from tw_backtest_signals import (sweep_regime_boundary,
                                                     sweep_crash_buy_gates,
                                                     sweep_allocations,
                                                     save_sweep_params,
                                                     load_sweep_params)
                    # Regime 掃描先跑（用現有 crash 參數固定）
                    r_res = sweep_regime_boundary(symbols_run, annual_injection=annual,
                                                  start_date=start, end_date=end)
                    # 用 regime 最佳值固定，再跑 crash 掃描
                    best_reg = max(r_res, key=lambda x: x["calmar"])["threshold_pct"] if r_res else 2.0
                    c_res = sweep_crash_buy_gates(symbols_run, annual_injection=annual,
                                                  start_date=start, end_date=end,
                                                  _fixed_regime=best_reg)
                    # 存檔 regime + crash（使用者可手動微調 JSON）
                    save_sweep_params(r_res, c_res, source_meta={
                        "start_date": start, "end_date": end,
                        "annual_injection": annual, "n_symbols": len(symbols_run),
                    })

                    def _fill_regime():
                        if not sw.winfo_exists(): return
                        lbl_r.destroy()
                        tree_r.pack(fill="both", expand=True)
                        best_c = max(r_res, key=lambda x: x["calmar"])["threshold_pct"] if r_res else None
                        for row in r_res:
                            tag = ("best",) if row["threshold_pct"] == best_c else ()
                            tree_r.insert("", "end", tags=tag, values=(
                                f"±{row['threshold_pct']}%",
                                f"{row['cagr']:+.1f}%",
                                f"{row['mdd']:.1f}%",
                                f"{row['calmar']:.2f}",
                                f"{row['sharpe']:.2f}",
                                f"{row['sortino']:.2f}",
                                row["n_trades"],
                                f"{row['win_rate']:.1f}%",
                                "★ 最佳" if row["threshold_pct"] == best_c else "",
                            ))

                    def _fill_crash():
                        if not sw.winfo_exists(): return
                        lbl_c.destroy()
                        tree_c.pack(fill="both", expand=True)
                        best_l = max(c_res, key=lambda x: x["calmar"])["label"] if c_res else None
                        for row in c_res:
                            tag = ("best",) if row["label"] == best_l else ()
                            tree_c.insert("", "end", tags=tag, values=(
                                row["label"],
                                f"<{row['rsi_gate']}" if row["rsi_gate"] else "—",
                                f"<{row['drop_gate']}%" if row["drop_gate"] else "—",
                                f"{row['cagr']:+.1f}%",
                                f"{row['mdd']:.1f}%",
                                f"{row['calmar']:.2f}",
                                f"{row['sharpe']:.2f}",
                                f"{row['sortino']:.2f}",
                                "★ 最佳" if row["label"] == best_l else "",
                            ))

                    sw.after(0, _fill_regime)
                    sw.after(0, _fill_crash)

                    # 配比掃描（使用前兩步驟最佳化後的 regime/crash 參數）
                    sw.after(0, lambda: lbl_a.configure(text="配比掃描中… (~5min)")
                             if sw.winfo_exists() else None)
                    a_res = sweep_allocations(symbols_run, annual_injection=annual,
                                              start_date=start, end_date=end)

                    def _fill_alloc():
                        if not sw.winfo_exists(): return
                        lbl_a.destroy()
                        tree_a.pack(fill="both", expand=True)
                        best_cal = max((x["calmar"] for x in a_res), default=0)
                        for row in a_res:
                            tag = ("best",) if row["calmar"] == best_cal else ()
                            tree_a.insert("", "end", tags=tag, values=(
                                row.get("phase", ""),
                                row.get("label", ""),
                                row.get("bull_mult", ""),
                                row.get("warn_mult", ""),
                                row.get("bear_mult", ""),
                                row.get("a_cash_frac", ""),
                                row.get("b_base_pct", ""),
                                f"{row['cagr']:+.1f}%",
                                f"{row['mdd']:.1f}%",
                                f"{row['calmar']:.2f}",
                                "★ 最佳" if row["calmar"] == best_cal else "",
                            ))

                    sw.after(0, _fill_alloc)
                except Exception as e:
                    try:
                        sw.after(0, lambda: lbl_r.configure(text=f"Error: {e}", fg="#e74c3c")
                                 if sw.winfo_exists() else None)
                    except Exception:
                        pass

            threading.Thread(target=_bg_sweep, daemon=True).start()

        tk.Button(btn_row, text="  🔬 參數掃描  ",
                  bg="#2d3a1e", fg="#a8e6cf",
                  activebackground="#3a5025", activeforeground="white",
                  font=(self.ui_font, 12), relief="flat",
                  padx=12, pady=8, cursor="hand2",
                  command=_run_sweep).pack(side="left", padx=8)

        def _draw(r, lbl, annual_inj, lot_etf, lot_tech, sbuy_mult_used):
            if "error" in r:
                lbl.configure(text=f"Error: {r['error']}", fg="#e74c3c")
                return
            lbl.destroy()

            import matplotlib
            matplotlib.use("TkAgg")
            import matplotlib.pyplot as plt
            import matplotlib.ticker as mticker
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            plt.rcParams.update({"font.sans-serif": ["Microsoft JhengHei", "SimHei", "sans-serif"],
                                  "axes.unicode_minus": False})

            # ── 策略說明 ──
            strat_txt = (
                f"策略：每年注資 NT${annual_inj:,.0f}，BUY/STRONG BUY 信號觸發進場，"
                f"SELL 信號出場（ETF 永不賣出）｜隔日開盤執行 + 滑價 0.1%｜"
                f"ETF 保守 {lot_etf*100:.0f}%（NT${annual_inj*lot_etf:,.0f}/筆），"
                f"科技股 衝刺 {lot_tech*100:.0f}%（NT${annual_inj*lot_tech:,.0f}/筆），"
                f"STRONG BUY {sbuy_mult_used:.1f}x｜共 {len(r['symbols'])} 檔"
            )
            tk.Label(win, text=strat_txt, fg="#74a0c0", bg="#0f1a30",
                     font=(self.ui_font, 12), wraplength=920, justify="left"
                     ).pack(fill="x", padx=12, pady=(6, 0))

            bnh = r.get("bnh_0050", {})

            # ── 指標列：策略 vs 0050 B&H ──
            def _metric_col(parent, label, val, ref_val=None):
                col = tk.Frame(parent, bg="#0f1a30"); col.pack(side="left", padx=6)
                tk.Label(col, text=label, fg="#95a5a6", bg="#0f1a30",
                         font=(self.ui_font, 12)).pack()
                vc = ("#2ecc71" if "+" in str(val) else
                      "#e74c3c" if "-" in str(val) else "#74b9ff")
                tk.Label(col, text=val, fg=vc, bg="#0f1a30",
                         font=(self.ui_font, 12, "bold")).pack()
                if ref_val is not None:
                    tk.Label(col, text=f"0050: {ref_val}", fg="#636e72", bg="#0f1a30",
                             font=(self.ui_font, 12)).pack()

            hdr = tk.Frame(win, bg="#0f1a30")
            hdr.pack(fill="x", padx=12, pady=(4, 2))
            _metric_col(hdr, "每年注資",  f"NT${r['annual_injection']:,.0f}")
            _metric_col(hdr, "總注入",    f"NT${r['total_injected']:,.0f}")
            _metric_col(hdr, "終值",      f"NT${r['final_value']:,.0f}",
                        f"NT${bnh.get('final_value',0):,.0f}" if bnh else None)
            _metric_col(hdr, "總損益",    f"NT${r['total_pnl']:+,.0f}",
                        f"NT${bnh.get('total_pnl',0):+,.0f}" if bnh else None)
            _metric_col(hdr, "CAGR",      f"{r['cagr_pct']:+.1f}%",
                        f"{bnh.get('cagr_pct',0):+.1f}%" if bnh else None)
            _metric_col(hdr, "Sharpe",    f"{r['sharpe']:.2f}",
                        f"{bnh.get('sharpe',0):.2f}" if bnh else None)
            _metric_col(hdr, "MDD",       f"{r['mdd_pct']:.1f}%",
                        f"{bnh.get('mdd_pct',0):.1f}%" if bnh else None)
            _metric_col(hdr, "Calmar",    f"{r['calmar']:.2f}")
            _metric_col(hdr, "交易筆數",  f"{r['n_trades']}筆")
            _metric_col(hdr, "勝率",      f"{r['win_rate']:.0f}%")

            # ── 圖表（資產曲線 + 水下曲線）──
            eq = r["equity_series"]
            uw = r["underwater"]
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 4),
                                           gridspec_kw={"height_ratios": [3, 1]},
                                           facecolor="#0f1a30")
            fig.subplots_adjust(hspace=0.08, left=0.10, right=0.98, top=0.93, bottom=0.06)

            ax1.plot(eq.index, eq / 1000, color="#74b9ff", linewidth=1.8, label="策略")
            ax1.fill_between(eq.index, eq / 1000, float(eq.iloc[0]) / 1000,
                             alpha=0.15, color="#74b9ff")
            if bnh and "equity_series" in bnh:
                bnh_eq = bnh["equity_series"].reindex(eq.index, method="ffill")
                ax1.plot(bnh_eq.index, bnh_eq / 1000, color="#f39c12",
                         linewidth=1.0, linestyle="--", alpha=0.8, label="0050 B&H")
                ax1.legend(loc="upper left", fontsize=7,
                           facecolor="#0d1b2a", labelcolor="#dce8f0", framealpha=0.7)
            ax1.axhline(y=r["total_injected"] / 1000, color="#636e72",
                        linewidth=0.8, linestyle=":", alpha=0.5)
            ax1.set_facecolor("#0d1b2a")
            ax1.set_title(f"Portfolio Equity Curve  {start[:4]}-{end[:4]}",
                          color="#74b9ff", fontsize=10)
            ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"NT${x:.0f}K"))
            ax1.tick_params(colors="#95a5a6", labelsize=8, labelbottom=False)
            ax1.grid(color="#1e3a5f", linewidth=0.4)
            ax1.spines[:].set_color("#2a3a5a")

            ax2.fill_between(uw.index, uw, 0, alpha=0.4, color="#e74c3c")
            ax2.plot(uw.index, uw, color="#e74c3c", linewidth=0.7)
            ax2.axhline(y=-10, color="#f39c12", linewidth=0.6, linestyle="--", alpha=0.7)
            ax2.axhline(y=-20, color="#e74c3c", linewidth=0.6, linestyle="--", alpha=0.7)
            ax2.set_facecolor("#0d1b2a")
            ax2.set_ylabel("DD%", color="#95a5a6", fontsize=8)
            ax2.tick_params(colors="#95a5a6", labelsize=8)
            ax2.grid(color="#1e3a5f", linewidth=0.3)
            ax2.spines[:].set_color("#2a3a5a")

            canvas = FigureCanvasTkAgg(fig, master=win)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="x", padx=4)

            # ── 交易明細 Treeview ──
            tk.Label(win, text="交易明細（不含期末持倉）",
                     fg="#74a0c0", bg="#0f1a30",
                     font=(self.ui_font, 12, "bold")).pack(anchor="w", padx=14, pady=(6, 2))

            tcols = [("代號",60),("名稱",80),("進場日",88),("進場價",70),
                     ("出場日",88),("出場價",70),("股數",55),
                     ("損益NT$",85),("損益%",60),("持有天",55),("出場方式",80)]
            tf = tk.Frame(win, bg="#0f1a30"); tf.pack(fill="both", expand=True, padx=8, pady=(0,8))
            style = _ttv.Style()
            style.theme_use("default")
            style.configure("Port.Treeview",
                            background="#0d1b2a", foreground="#dce8f0",
                            fieldbackground="#0d1b2a", rowheight=20,
                            font=(self.ui_font, 12))
            style.configure("Port.Treeview.Heading",
                            background="#1a2a40", foreground="#74b9ff",
                            font=(self.ui_font, 12, "bold"))
            style.map("Port.Treeview", background=[("selected", "#2a4a70")])

            tree = _ttv.Treeview(tf, style="Port.Treeview", show="headings",
                                 columns=[c for c, _ in tcols], height=10)
            for col, w in tcols:
                tree.heading(col, text=col)
                tree.column(col, width=w, anchor="center" if col not in ("名稱",) else "w",
                            stretch=False)
            sb = tk.Scrollbar(tf, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")

            closed = [t for t in r["trades"] if t.get("exit_signal") != "PERIOD_END"]
            closed.sort(key=lambda t: t.get("entry_date", ""))
            for t in closed:
                pnl   = t.get("pnl", 0)
                color = "win" if pnl > 0 else "lose"
                tree.insert("", "end", tags=(color,), values=(
                    t["symbol"].replace(".TW", ""),
                    t.get("name", ""),
                    t.get("entry_date", ""),
                    f"{t.get('entry_price',0):,.1f}",
                    t.get("exit_date", ""),
                    f"{t.get('exit_price',0):,.1f}",
                    f"{t.get('shares',0):,}",
                    f"{pnl:+,.0f}",
                    f"{t.get('pnl_pct',0):+.1f}%",
                    t.get("hold_days", ""),
                    t.get("exit_signal", ""),
                ))
            tree.tag_configure("win",  foreground="#2ecc71")
            tree.tag_configure("lose", foreground="#e74c3c")

        def _draw_v2(r, lbl, annual_inj):
            if "error" in r:
                lbl.configure(text=f"Error: {r['error']}", fg="#e74c3c")
                return
            lbl.destroy()

            import matplotlib
            matplotlib.use("TkAgg")
            import matplotlib.pyplot as plt
            import matplotlib.ticker as mticker
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            plt.rcParams.update({"font.sans-serif": ["Microsoft JhengHei", "SimHei", "sans-serif"],
                                  "axes.unicode_minus": False})

            type_a = r.get("type_a_symbols", [])
            type_b = r.get("type_b_symbols", [])
            type_b_modes = r.get("type_b_modes", {})

            is_hybrid = r.get("hybrid", False)
            mode_tag = "[v2 混合模式]" if is_hybrid else "[v2 評分自動分配]"
            b_desc = "信號進出+年初DCA" if is_hybrid else "信號進出"
            strat_txt = (
                f"{mode_tag}  年注資 NT${annual_inj:,.0f}  |  "
                f"A類(DCA永不賣): {', '.join(s.replace('.TW','') for s in type_a)}  |  "
                f"B類({b_desc}): "
                + "  ".join(f"{s.replace('.TW','')}({type_b_modes.get(s,'')})" for s in type_b)
            )
            tk.Label(win, text=strat_txt, fg="#a8e6cf", bg="#0f1a30",
                     font=(self.ui_font, 11), wraplength=920, justify="left"
                     ).pack(fill="x", padx=12, pady=(6, 0))

            # 指標列
            bnh   = r.get("bnh_0050", {})
            stats = [
                ("總注資",     f"NT${r['total_injected']:,.0f}", "#95a5a6"),
                ("最終市值",   f"NT${r['final_value']:,.0f}",   "#2ecc71"),
                ("總獲利",     f"NT${r['total_pnl']:+,.0f}",    "#2ecc71" if r["total_pnl"] >= 0 else "#e74c3c"),
                ("CAGR",       f"{r['cagr_pct']:+.1f}%",        "#74b9ff"),
                ("MDD",        f"{r['mdd_pct']:.1f}%",          "#e74c3c"),
                ("Calmar",     f"{r['calmar']:.2f}",            "#f39c12"),
                ("Sharpe",     f"{r['sharpe']:.2f}",            "#9b59b6"),
                ("Sortino",    f"{r.get('sortino', 0):.2f}",    "#a29bfe"),
                ("勝率",       f"{r['win_rate']:.1f}%",         "#2ecc71"),
                ("0050 CAGR",  f"{bnh.get('cagr_pct',0):+.1f}%", "#e67e22"),
            ]
            sf2 = tk.Frame(win, bg="#0f1a30")
            sf2.pack(fill="x", padx=12, pady=6)
            for i, (lbl_t, val, clr) in enumerate(stats):
                lw = tk.Label(sf2, text=lbl_t, fg="#95a5a6", bg="#0f1a30",
                              font=(self.ui_font, 11))
                lw.grid(row=0, column=i*2, padx=(8,2))
                if lbl_t in _V2_METRIC_TIPS:
                    _Tooltip(lw, _V2_METRIC_TIPS[lbl_t])
                tk.Label(sf2, text=val, fg=clr, bg="#0f1a30",
                         font=(self.ui_font, 12, "bold")).grid(row=1, column=i*2, padx=(8,2))

            # A 類持倉明細
            ab = r.get("type_a_breakdown", {})
            if ab:
                tk.Label(win, text="A 類持倉（DCA 長期持有，期末未平倉）",
                         fg="#74b9ff", bg="#0f1a30", font=(self.ui_font, 11, "bold")
                         ).pack(anchor="w", padx=14, pady=(8, 2))
                from tkinter import ttk as _ttv
                af = tk.Frame(win, bg="#0f1a30")
                af.pack(fill="x", padx=12)
                atree = _ttv.Treeview(af, columns=("sym","shares","cost","mktval","pnl"),
                                      show="headings", height=min(8, len(ab)))
                for col, w, anch in [("sym",80,"w"),("shares",70,"e"),
                                     ("cost",110,"e"),("mktval",110,"e"),("pnl",110,"e")]:
                    atree.heading(col, text={"sym":"股票","shares":"股數","cost":"成本",
                                             "mktval":"市值","pnl":"未實現損益"}[col])
                    atree.column(col, width=w, anchor=anch)
                for sym, d in sorted(ab.items()):
                    pnl = d.get("unrealized_pnl", 0)
                    atree.insert("", "end",
                                 values=(sym.replace(".TW",""), f"{d['shares']:,}",
                                         f"NT${d['total_cost']:,.0f}",
                                         f"NT${d['market_value']:,.0f}",
                                         f"NT${pnl:+,.0f}"),
                                 tags=("win",) if pnl >= 0 else ("lose",))
                atree.tag_configure("win",  foreground="#2ecc71")
                atree.tag_configure("lose", foreground="#e74c3c")
                atree.pack(fill="x")

            # 交易明細彈窗
            def _detail_popup(res=r):
                from tkinter import ttk as _ttv2
                dp = tk.Toplevel(win)
                dp.title("v2 交易明細")
                dp.geometry("1000x600")
                dp.configure(bg="#0f1a30")
                dp.lift()

                nb = _ttv2.Notebook(dp)
                nb.pack(fill="both", expand=True, padx=8, pady=8)

                style2 = _ttv2.Style()
                style2.theme_use("default")
                style2.configure("Det.Treeview",
                                 background="#0d1b2a", foreground="#dce8f0",
                                 fieldbackground="#0d1b2a", rowheight=20,
                                 font=(self.ui_font, 11))
                style2.configure("Det.Treeview.Heading",
                                 background="#1a2a40", foreground="#74b9ff",
                                 font=(self.ui_font, 11, "bold"))
                style2.map("Det.Treeview", background=[("selected", "#2a4a70")])

                # B 類信號交易頁
                b_frame = tk.Frame(nb, bg="#0f1a30")
                nb.add(b_frame, text="B類 信號交易")
                b_cols = [("股票",60),("名稱",80),("進場日",88),("進場價",70),
                          ("出場日",88),("出場價",70),("股數",55),
                          ("損益NT$",85),("損益%",60),("持有天",55),("出場信號",90)]
                b_tree = _ttv2.Treeview(b_frame, style="Det.Treeview",
                                        columns=[c for c,_ in b_cols],
                                        show="headings", height=18)
                for col, w in b_cols:
                    b_tree.heading(col, text=col)
                    b_tree.column(col, width=w,
                                  anchor="w" if col == "名稱" else "center",
                                  stretch=False)
                b_sb = tk.Scrollbar(b_frame, orient="vertical", command=b_tree.yview)
                b_tree.configure(yscrollcommand=b_sb.set)
                b_tree.pack(side="left", fill="both", expand=True)
                b_sb.pack(side="right", fill="y")
                b_tree.tag_configure("win",  foreground="#2ecc71")
                b_tree.tag_configure("lose", foreground="#e74c3c")

                b_trades = sorted(
                    [t for t in res["trades"] if t.get("type") == "B" and t.get("exit_date")],
                    key=lambda t: t.get("entry_date", "")
                )
                for t in b_trades:
                    pnl = t.get("pnl") or 0
                    b_tree.insert("", "end",
                                  tags=("win",) if pnl > 0 else ("lose",),
                                  values=(
                                      t["symbol"].replace(".TW", ""),
                                      t.get("name", ""),
                                      t.get("entry_date", ""),
                                      f"{t.get('entry_price',0):,.1f}",
                                      t.get("exit_date", ""),
                                      f"{t.get('exit_price',0):,.1f}",
                                      f"{t.get('shares',0):,}",
                                      f"{pnl:+,.0f}",
                                      f"{t.get('pnl_pct',0):+.1f}%",
                                      t.get("hold_days", ""),
                                      t.get("exit_signal", ""),
                                  ))

                # A 類 DCA 買入記錄頁
                a_frame = tk.Frame(nb, bg="#0f1a30")
                nb.add(a_frame, text="A類 DCA 買入記錄")
                a_cols = [("股票",60),("名稱",80),("買入日",88),("買入價",70),
                          ("股數",60),("成本NT$",95),("進場信號",150)]
                a_tree = _ttv2.Treeview(a_frame, style="Det.Treeview",
                                        columns=[c for c,_ in a_cols],
                                        show="headings", height=18)
                for col, w in a_cols:
                    a_tree.heading(col, text=col)
                    a_tree.column(col, width=w,
                                  anchor="w" if col in ("名稱","進場信號") else "center",
                                  stretch=False)
                a_sb = tk.Scrollbar(a_frame, orient="vertical", command=a_tree.yview)
                a_tree.configure(yscrollcommand=a_sb.set)
                a_tree.pack(side="left", fill="both", expand=True)
                a_sb.pack(side="right", fill="y")

                a_trades = sorted(
                    [t for t in res["trades"] if t.get("type") == "A"],
                    key=lambda t: t.get("entry_date", "")
                )
                for t in a_trades:
                    a_tree.insert("", "end", values=(
                        t["symbol"].replace(".TW", ""),
                        t.get("name", ""),
                        t.get("entry_date", ""),
                        f"{t.get('entry_price',0):,.1f}",
                        f"{t.get('shares',0):,}",
                        f"NT${t.get('cost',0):,.0f}",
                        t.get("entry_signal", ""),
                    ))

                # 年度績效彙整頁
                yr_frame = tk.Frame(nb, bg="#0f1a30")
                nb.add(yr_frame, text="年度績效")
                yr_cols = [("年份",55),("年報酬%",75),("vs 0050",70),
                           ("A類買",55),("B類買",55),("B類賣",55),
                           ("年注資",90),("期末市值",100),("說明",180)]
                yr_tree = _ttv2.Treeview(yr_frame, style="Det.Treeview",
                                         columns=[c for c,_ in yr_cols],
                                         show="headings", height=15)
                for col, w in yr_cols:
                    yr_tree.heading(col, text=col)
                    yr_tree.column(col, width=w,
                                   anchor="w" if col == "說明" else "center",
                                   stretch=False)
                yr_sb = tk.Scrollbar(yr_frame, orient="vertical", command=yr_tree.yview)
                yr_tree.configure(yscrollcommand=yr_sb.set)
                yr_tree.pack(side="left", fill="both", expand=True)
                yr_sb.pack(side="right", fill="y")
                yr_tree.tag_configure("up",   foreground="#2ecc71")
                yr_tree.tag_configure("down", foreground="#e74c3c")

                eq_yr = res.get("equity_series")
                bnh_yr = (res.get("bnh_0050") or {}).get("equity_series")
                all_trades = res.get("trades", [])
                if eq_yr is not None and len(eq_yr) > 0:
                    import pandas as _pd2
                    annual_inj_amt = res.get("annual_injection", 100_000)
                    for yr, grp in eq_yr.groupby(eq_yr.index.year):
                        yr_start = float(grp.iloc[0])
                        yr_end   = float(grp.iloc[-1])
                        yr_ret   = (yr_end - yr_start) / yr_start * 100 if yr_start else 0
                        # 0050 同年報酬
                        bnh_ret_yr = 0.0
                        if bnh_yr is not None:
                            bg = bnh_yr[bnh_yr.index.year == yr]
                            if len(bg) >= 2:
                                bnh_ret_yr = (float(bg.iloc[-1]) - float(bg.iloc[0])) / float(bg.iloc[0]) * 100
                        # 交易計數
                        a_buys = sum(1 for t in all_trades
                                     if t.get("type") == "A" and str(t.get("entry_date",""))[:4] == str(yr))
                        b_buys = sum(1 for t in all_trades
                                     if t.get("type") == "B" and str(t.get("entry_date",""))[:4] == str(yr))
                        b_sells = sum(1 for t in all_trades
                                      if t.get("type") == "B" and t.get("exit_date")
                                      and str(t.get("exit_date",""))[:4] == str(yr)
                                      and t.get("exit_signal") != "PERIOD_END")
                        # 說明文字
                        diff = yr_ret - bnh_ret_yr
                        if diff > 5:
                            note = f"跑贏0050 +{diff:.1f}%"
                        elif diff < -5:
                            note = f"落後0050 {diff:.1f}%"
                        else:
                            note = f"與0050 相近 {diff:+.1f}%"
                        if yr_ret > 20:
                            note += "　強勢年"
                        elif yr_ret < -10:
                            note += "　回撤年"
                        tag = "up" if yr_ret >= 0 else "down"
                        yr_tree.insert("", "end", tags=(tag,), values=(
                            yr,
                            f"{yr_ret:+.1f}%",
                            f"{bnh_ret_yr:+.1f}%",
                            a_buys,
                            b_buys,
                            b_sells,
                            f"NT${annual_inj_amt:,.0f}",
                            f"NT${yr_end:,.0f}",
                            note,
                        ))

            bf = tk.Frame(win, bg="#0f1a30")
            bf.pack(fill="x", padx=12, pady=(4, 0))
            tk.Button(bf, text="📋 交易明細",
                      bg="#1a2a40", fg="#74b9ff",
                      font=(self.ui_font, 11), relief="flat",
                      padx=10, pady=4, cursor="hand2",
                      command=_detail_popup).pack(side="left", padx=4)

            # 資產曲線 + 水下回撤圖（2-subplot）
            eq = r.get("equity_series")
            if eq is not None and len(eq) > 10:
                import pandas as _pd
                uw = ((eq / eq.cummax()) - 1) * 100   # underwater DD%
                fig, (ax1, ax2) = plt.subplots(
                    2, 1, figsize=(9, 4.5), facecolor="#0f1a30",
                    gridspec_kw={"height_ratios": [3, 1]})
                fig.subplots_adjust(hspace=0.06, left=0.09, right=0.97, top=0.92, bottom=0.07)

                ax1.plot(eq.index, eq / 1000, color="#74b9ff", linewidth=1.5, label="v2 評分組合")
                ax1.fill_between(eq.index, eq / 1000,
                                 float(eq.iloc[0]) / 1000, alpha=0.12, color="#74b9ff")
                bnh_eq = bnh.get("equity_series")
                if bnh_eq is not None and len(bnh_eq) > 0:
                    bnh_r = bnh_eq.reindex(eq.index, method="ffill")
                    ax1.plot(bnh_r.index, bnh_r / 1000, color="#e67e22",
                             linewidth=1.0, linestyle="--", alpha=0.7, label="0050 B&H")
                inj_line = r["total_injected"] / max(1, len(eq.index.year.unique())) * len(eq.index.year.unique())
                ax1.axhline(y=r["total_injected"] / 1000, color="#636e72",
                            linewidth=0.8, linestyle=":", alpha=0.5)
                ax1.set_facecolor("#0d1b2a")
                ax1.set_title("v2 評分組合  資產曲線（NT$ 千元）", color="#74b9ff", fontsize=10)
                ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"NT${x:.0f}K"))
                ax1.tick_params(colors="#95a5a6", labelsize=8, labelbottom=False)
                ax1.grid(color="#1e3a5f", linewidth=0.3)
                ax1.spines[:].set_color("#2a3a5a")
                ax1.legend(fontsize=8, facecolor="#1a2a40", labelcolor="#ecf0f1",
                           loc="upper left", framealpha=0.8)

                ax2.fill_between(uw.index, uw, 0, alpha=0.4, color="#e74c3c")
                ax2.plot(uw.index, uw, color="#e74c3c", linewidth=0.7)
                ax2.axhline(y=-10, color="#f39c12", linewidth=0.6, linestyle="--", alpha=0.7)
                ax2.axhline(y=-20, color="#e74c3c", linewidth=0.6, linestyle="--", alpha=0.7)
                ax2.set_facecolor("#0d1b2a")
                ax2.set_ylabel("DD%", color="#95a5a6", fontsize=8)
                ax2.tick_params(colors="#95a5a6", labelsize=8)
                ax2.grid(color="#1e3a5f", linewidth=0.3)
                ax2.spines[:].set_color("#2a3a5a")

                canv = FigureCanvasTkAgg(fig, master=win)
                canv.draw()
                canv.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

    def _sbt_walkforward_popup(self, sym: str, name: str, result: dict):
        import tkinter as tk
        win = tk.Toplevel(self)
        win.title(f"{sym.replace('.TW','')} {name}  Walk-Forward 驗證")
        win.geometry("860x520")
        win.configure(bg="#0f1a30")
        win.lift()

        lbl = tk.Label(win, text="計算中…", fg="#a8e6cf", bg="#0f1a30",
                       font=(self.ui_font, 12))
        lbl.pack(expand=True)

        start = result.get("start_date", START_DATE)
        end   = result.get("end_date",   END_DATE)
        split_yr = int(start[:4]) + (int(end[:4]) - int(start[:4])) // 2

        def _bg():
            try:
                from tw_backtest_signals import run_walk_forward
                wf = run_walk_forward(sym, name,
                                      annual_budget=result.get("params", {}).get("annual_budget", 100_000),
                                      start_date=start, end_date=end, split_year=split_yr)
                self.after(0, lambda: _draw(wf))
            except Exception as e:
                self.after(0, lambda: lbl.configure(text=f"錯誤：{e}", fg="#e74c3c"))

        def _draw(wf):
            if "error" in wf:
                lbl.configure(text=f"⚠ {wf['error']}", fg="#e74c3c")
                return
            lbl.destroy()
            from tkinter import ttk as _ttk

            # 標題
            tk.Label(win, text=f"{sym.replace('.TW','')} {name}   "
                               f"切分點：{wf['split_year']}年  "
                               f"（訓練 {start[:4]}–{wf['split_year']-1}  /  驗證 {wf['split_year']}–{end[:4]}）",
                     fg="#74b9ff", bg="#0f1a30",
                     font=(self.ui_font, 12, "bold")).pack(padx=10, pady=(8, 2), anchor="w")
            tk.Label(win,
                     text=f"B&H 訓練期 CAGR {wf['bnh_in_cagr']:+.1f}%   │   "
                          f"B&H 驗證期 CAGR {wf['bnh_out_cagr']:+.1f}%   "
                          f"（B&H 自身波動：{wf['bnh_out_cagr']-wf['bnh_in_cagr']:+.1f}%）",
                     fg="#fdcb6e", bg="#0f1a30",
                     font=("Consolas", 12)).pack(padx=10, pady=(0, 6), anchor="w")

            sty = _ttk.Style(win)
            sty.theme_use("clam")
            sty.configure("WF.Treeview", background="#0d1b2a", foreground="#ecf0f1",
                          fieldbackground="#0d1b2a", rowheight=24, font=("Consolas", 12))
            sty.configure("WF.Treeview.Heading", background="#0a0f1a",
                          foreground="#74b9ff", font=(self.ui_font, 12, "bold"))
            sty.map("WF.Treeview", background=[("selected", "#1c4f82")])

            cols   = ("策略", "訓練CAGR", "訓練Calmar", "訓練MDD", "訓練筆",
                             "驗證CAGR", "驗證Calmar", "驗證MDD", "驗證筆", "CAGR變化")
            widths = (155, 75, 75, 70, 55, 75, 75, 70, 55, 80)

            wrap = tk.Frame(win, bg="#0f1a30")
            wrap.pack(fill="both", expand=True, padx=10, pady=4)
            tree = _ttk.Treeview(wrap, style="WF.Treeview",
                                 columns=cols, show="headings", selectmode="browse")
            for c, w in zip(cols, widths):
                tree.heading(c, text=c)
                tree.column(c, width=w, anchor="center")
            vsb = _ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            wrap.grid_rowconfigure(0, weight=1)
            wrap.grid_columnconfigure(0, weight=1)

            tree.tag_configure("stable",   foreground="#2ecc71")
            tree.tag_configure("degrade",  foreground="#e74c3c")
            tree.tag_configure("mild",     foreground="#f39c12")

            for m in wf["modes"]:
                ins = m["in_sample"]
                out = m["out_sample"]
                delta = m["cagr_delta"]
                tag = "stable" if delta >= -1 else ("mild" if delta >= -4 else "degrade")
                tree.insert("", "end", tags=(tag,), values=(
                    m["label"],
                    f"{ins['cagr']:+.1f}%",
                    f"{ins['calmar']:.2f}",
                    f"{ins['mdd']:.1f}%",
                    str(ins["n_trades"]),
                    f"{out['cagr']:+.1f}%",
                    f"{out['calmar']:.2f}",
                    f"{out['mdd']:.1f}%",
                    str(out["n_trades"]),
                    f"{delta:+.1f}%",
                ))

            # ── 穩定性摘要 ───────────────────────────────────────────────────
            stable = sum(1 for m in wf["modes"] if m.get("cagr_delta", -99) >= -1)
            mild   = sum(1 for m in wf["modes"] if -4 <= m.get("cagr_delta", -99) < -1)
            weak   = sum(1 for m in wf["modes"] if m.get("cagr_delta", -99) < -4)
            total  = len(wf["modes"])
            if weak >= max(1, total // 2):
                sum_txt = (f"⚠️ {weak}/{total} 策略退化明顯（CAGR 降幅 > 4%），可能有過擬合風險。"
                           f"建議重新調整 RSI 閾值或 AVWAP 乘數後再驗證。")
                sum_clr = "#e74c3c"
            elif stable >= round(total * 0.6):
                sum_txt = (f"✅ {stable}/{total} 策略穩定（CAGR 退化 < 1%），"
                           f"參數在此股票上具可信賴性，可按現有設定操作。")
                sum_clr = "#2ecc71"
            else:
                sum_txt = (f"🟡 {stable} 穩定 / {mild} 輕微退化 / {weak} 明顯退化（共 {total} 策略）。"
                           f"整體尚可接受，建議優先選用穩定策略（綠色列），並定期重跑驗證。")
                sum_clr = "#f39c12"
            tk.Label(win, text=sum_txt,
                     fg=sum_clr, bg="#0f1a30",
                     font=(self.ui_font, 12), wraplength=820, justify="left"
                     ).pack(padx=14, pady=(4, 2), anchor="w")
            # 說明
            tk.Label(win, text="🟢 穩定（CAGR 差 < 1%）   🟡 輕微退化（1–4%）   🔴 明顯退化（> 4%）",
                     fg="#95a5a6", bg="#0f1a30", font=("Consolas", 12)
                     ).pack(side="bottom", pady=4)

        threading.Thread(target=_bg, daemon=True).start()

    def _sbt_trade_popup(self, trades: list[dict], stock: str, mode_label: str):
        import tkinter as tk
        from tkinter import ttk as _ttk

        win = tk.Toplevel(self)
        win.title(f"{stock}  {mode_label}")
        win.geometry("1280x680")
        win.configure(bg="#0f1a30")
        win.lift()

        # 標題 + 統計
        hdr = tk.Frame(win, bg="#0f1a30")
        hdr.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(hdr, text=f"{stock}  {mode_label}",
                 fg="#74b9ff", bg="#0f1a30",
                 font=(self.ui_font, 12, "bold")).pack(side="left")
        tk.Label(hdr, text=f"  共 {len(trades)} 筆交易",
                 fg="#888", bg="#0f1a30",
                 font=("Consolas", 12)).pack(side="left")

        n_win     = sum(1 for t in trades if t["pnl"] > 0)
        tot_pnl   = sum(t["pnl"] for t in trades)
        tot_inv   = sum(t["cost"] for t in trades)
        tot_fees  = sum(t.get("fees", 0) for t in trades)
        ret_pct   = tot_pnl / tot_inv * 100 if tot_inv > 0 else 0
        pnl_clr   = "#00b894" if tot_pnl >= 0 else "#d63031"

        smr = tk.Frame(win, bg="#1a2a40")
        smr.pack(fill="x", padx=10, pady=2)
        for txt, clr in [
            (f"總投入  NT${tot_inv:,.0f}", "#fdcb6e"),
            (f"  總損益  NT${tot_pnl:+,.0f}  ({ret_pct:+.1f}%)", pnl_clr),
            (f"  手續費+稅  NT${tot_fees:,.0f}", "#b2bec3"),
            (f"  勝率  {n_win}/{len(trades)}  ({n_win/len(trades)*100:.0f}%)"
             if trades else "", "#74b9ff"),
        ]:
            tk.Label(smr, text=txt, fg=clr, bg="#1a2a40",
                     font=("Consolas", 12)).pack(side="left", padx=8, pady=4)

        # 樣式
        sty = _ttk.Style(win)
        sty.theme_use("clam")
        sty.configure("T.Treeview",
                      background="#0d1b2a", foreground="#ecf0f1",
                      fieldbackground="#0d1b2a", rowheight=22,
                      font=("Consolas", 12))
        sty.configure("T.Treeview.Heading",
                      background="#0a0f1a", foreground="#74b9ff",
                      font=(self.ui_font, 12, "bold"))
        sty.map("T.Treeview", background=[("selected", "#1c4f82")])

        cols = ("進場日", "進場信號", "DD%", "RSI進", "vs_AVWAP%",
                "進場價", "股數", "成本NT$",
                "出場日", "出場信號", "RSI出", "出場價", "回收NT$",
                "手續費+稅", "損益NT$", "損益%", "持有天")
        widths = (95, 95, 60, 55, 75, 70, 60, 90, 95, 80, 55, 70, 90, 80, 90, 65, 65)

        wrap = tk.Frame(win, bg="#0f1a30")
        wrap.pack(fill="both", expand=True, padx=10, pady=6)

        tree = _ttk.Treeview(wrap, style="T.Treeview",
                              columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="center", minwidth=40)

        tree.tag_configure("win",      foreground="#2ecc71")
        tree.tag_configure("loss",     foreground="#e74c3c")
        tree.tag_configure("open_end", foreground="#f39c12")
        tree.tag_configure("trim",     foreground="#1abc9c")
        tree.tag_configure("fallback", foreground="#a29bfe")

        vsb = _ttk.Scrollbar(wrap, orient="vertical",   command=tree.yview)
        hsb = _ttk.Scrollbar(wrap, orient="horizontal",  command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        for t in trades:
            ec   = t.get("entry_cond", {})
            xc   = t.get("exit_cond", {})
            xsig  = t.get("exit_signal", "")
            esig  = t.get("entry_signal", "")
            pnl   = t.get("pnl", 0)
            tag   = ("fallback" if esig == "FALLBACK"
                     else "open_end" if xsig == "PERIOD_END"
                     else "trim" if xsig == "TRIM"
                     else "win" if pnl > 0 else "loss")
            vals = (
                t.get("entry_date", ""),
                t.get("entry_signal", ""),
                f"{ec.get('DD%', '—')}",
                f"{ec.get('RSI', '—')}",
                f"{ec.get('vs_AVWAP%', '—')}",
                f"{t.get('entry_price', 0):,.2f}",
                f"{t.get('shares', 0):,}",
                f"NT${t.get('cost', 0):,.0f}",
                t.get("exit_date", ""),
                xsig,
                f"{xc.get('RSI', '—')}",
                f"{t.get('exit_price', 0):,.2f}",
                f"NT${t.get('proceeds', 0):,.0f}",
                f"NT${t.get('fees', 0):,.0f}",
                f"NT${pnl:+,.0f}",
                f"{t.get('pnl_pct', 0):+.2f}%",
                f"{t.get('hold_days', 0)}",
            )
            tree.insert("", "end", tags=(tag,), values=vals)

        # 說明列
        legend = tk.Frame(win, bg="#0f1a30")
        legend.pack(fill="x", padx=10, pady=(0, 4))
        for txt, clr in [
            ("🟢 獲利出場", "#2ecc71"),
            ("  🔴 虧損出場", "#e74c3c"),
            ("  🟡 期末未平倉（以最後收盤計算）", "#f39c12"),
            ("  🩵 TRIM 停利出場", "#1abc9c"),
            ("  🔵 年末強制部署 FALLBACK", "#a29bfe"),
        ]:
            tk.Label(legend, text=txt, fg=clr, bg="#0f1a30",
                     font=("Consolas", 12)).pack(side="left", padx=4)

        # ── 逐年績效表 ──────────────────────────────────────────────────
        from collections import defaultdict
        yr_entry: dict = defaultdict(int)
        yr_exit:  dict = defaultdict(int)
        yr_wins:  dict = defaultdict(int)
        yr_pnl:   dict = defaultdict(float)
        yr_open:  dict = defaultdict(int)
        for t in trades:
            ey = t.get("entry_date", "")[:4]
            xy = t.get("exit_date",  "")[:4]
            if ey:
                yr_entry[ey] += 1
            if xy:
                if t.get("exit_signal") == "PERIOD_END":
                    yr_open[xy] += 1
                else:
                    p = t.get("pnl", 0)
                    yr_pnl[xy]  += p
                    yr_exit[xy] += 1
                    if p > 0:
                        yr_wins[xy] += 1

        all_yrs = sorted(set(yr_entry) | set(yr_exit) | set(yr_open))

        sty.configure("YR.Treeview",
                      background="#0a1020", foreground="#ecf0f1",
                      fieldbackground="#0a1020", rowheight=20,
                      font=("Consolas", 12))
        sty.configure("YR.Treeview.Heading",
                      background="#06090f", foreground="#74b9ff",
                      font=(self.ui_font, 12, "bold"))
        sty.map("YR.Treeview", background=[("selected", "#1c4f82")])

        yr_cols   = ("年度", "進場數", "出場數", "勝場", "勝率%", "損益NT$", "期末持倉")
        yr_widths = (65, 60, 60, 55, 70, 115, 75)

        yr_wrap = tk.Frame(win, bg="#0a1020")
        yr_wrap.pack(fill="x", padx=10, pady=(0, 8))

        yr_tree = _ttk.Treeview(yr_wrap, style="YR.Treeview",
                                 columns=yr_cols, show="headings",
                                 selectmode="none", height=5)
        for c, w in zip(yr_cols, yr_widths):
            yr_tree.heading(c, text=c)
            yr_tree.column(c, width=w, anchor="center", minwidth=40)

        yr_tree.tag_configure("pos",    foreground="#2ecc71")
        yr_tree.tag_configure("neg",    foreground="#e74c3c")
        yr_tree.tag_configure("open_y", foreground="#f39c12")
        yr_tree.tag_configure("nodata", foreground="#636e72")

        yr_hsb = _ttk.Scrollbar(yr_wrap, orient="horizontal", command=yr_tree.xview)
        yr_tree.configure(xscrollcommand=yr_hsb.set)
        yr_tree.pack(fill="x")
        yr_hsb.pack(fill="x")

        for yr in all_yrs:
            en = yr_entry.get(yr, 0)
            ex = yr_exit.get(yr, 0)
            op = yr_open.get(yr, 0)
            p  = yr_pnl.get(yr, 0.0)
            wn = yr_wins.get(yr, 0)
            wr = f"{wn/ex*100:.0f}%" if ex > 0 else "—"
            ps = f"NT${p:+,.0f}"     if ex > 0 else "—"
            if op and not ex:
                tag = "open_y"
            elif ex == 0:
                tag = "nodata"
            elif p >= 0:
                tag = "pos"
            else:
                tag = "neg"
            yr_tree.insert("", "end", tags=(tag,),
                           values=(yr, en, ex, wn, wr, ps, op if op else "—"))


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    TwStrategyApp().mainloop()


if __name__ == "__main__":
    main()
