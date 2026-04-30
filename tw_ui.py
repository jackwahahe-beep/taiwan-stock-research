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

CATEGORY = {
    "0050.TW":  ("ETF 基準", "B&H"),
    "00878.TW": ("高息 ETF", "BUY"),
    "00713.TW": ("高息 ETF", "BUY"),
    "00929.TW": ("高息 ETF", "強買"),
    "00919.TW": ("高息 ETF", "B&H"),
    "2330.TW":  ("大型科技", "B&H"),
    "2454.TW":  ("大型科技", "BUY"),
    "3711.TW":  ("中型科技", "B&H"),
    "2303.TW":  ("中型科技", "B&H"),
    "2382.TW":  ("AI伺服器", "B&H"),
    "2308.TW":  ("電源散熱", "強買"),
    "3037.TW":  ("趨勢強股", "B&H"),
    "2408.TW":  ("記憶體",   "BUY"),
}

SCAN_COLS = [
    ("symbol",   "代號",     80,  "w"),
    ("name",     "名稱",    100,  "w"),
    ("cat",      "類別",     90,  "center"),
    ("rec",      "DCA策略",  70,  "center"),
    ("signal",   "信號",     90,  "center"),
    ("price",    "現價",     70,  "e"),
    ("rsi",      "RSI",      50,  "center"),
    ("avwap",    "AVWAP",    80,  "e"),
    ("b1",       "試買",     80,  "e"),
    ("b2",       "加碼",     80,  "e"),
    ("s_target", "賣出參考", 85,  "e"),
    ("dd",       "回撤",     60,  "center"),
    ("pnl",      "持股損益",100,  "e"),
]


# ── 資料讀取 ──────────────────────────────────────────────────────────────────

def _load_scan_cache() -> list[dict]:
    files = sorted(_glob.glob(str(CACHE_DIR / "scan_*.json")), reverse=True)
    return json.loads(Path(files[0]).read_text(encoding="utf-8")) if files else []


def _load_config() -> dict:
    import yaml
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_backtest_cache() -> dict:
    files = sorted(_glob.glob(str(CACHE_DIR / "backtest_*.json")), reverse=True)
    if not files:
        return {}
    data = json.loads(Path(files[0]).read_text(encoding="utf-8"))
    return {r["symbol"]: r for r in data}


def _load_dca_cache() -> dict:
    files = sorted(_glob.glob(str(CACHE_DIR / "dca_backtest_*.json")), reverse=True)
    if not files:
        return {}
    data = json.loads(Path(files[0]).read_text(encoding="utf-8"))
    return {r["symbol"]: r for r in data}


def _build_scan_rows(scan_records: list[dict], cfg: dict) -> list[dict]:
    from tw_screener import SIGNAL_CONFIG, _DEFAULT_CFG

    portfolio = {h["symbol"]: h for h in cfg.get("portfolio", [])}
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

        cat, rec = CATEGORY.get(sym, ("—", "—"))
        rows.append({
            "symbol":   sym.replace(".TW", ""),
            "name":     r.get("name", sym),
            "cat":      cat, "rec": rec, "signal": signal,
            "price":    f"{price:,.1f}" if price else "—",
            "rsi":      str(r.get("rsi", "—")),
            "avwap":    f"{avwap:,.1f}" if avwap else "—",
            "b1":       f"{b1:,.1f}" if b1 else "—",
            "b2":       f"{b2:,.1f}" if b2 else "—",
            "s_target": f"{s_target:,.1f}" if s_target else "—",
            "dd":       f"{r.get('dd_pct', 0):+.1f}%",
            "pnl":      pnl_str,
            "_signal_raw":   signal,
            "_in_portfolio": sym in portfolio,
        })
    return rows


# ── 主視窗 ────────────────────────────────────────────────────────────────────

class TwStrategyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
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
        self._bt_cache:     dict       = {}
        self._bt_btns:      dict       = {}

        self._setup_style()
        self._build_ui()
        self.after(200, self._initial_load)

    # ── ttk Style ─────────────────────────────────────────────────────────────

    def _setup_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("S.Treeview", background=BG_ROW_B, foreground=C_WHITE,
                    fieldbackground=BG_ROW_B, rowheight=28, font=(self.ui_font, 11))
        s.configure("S.Treeview.Heading", background=BG_HDR, foreground=C_BLUE,
                    font=(self.ui_font, 11, "bold"), relief="flat")
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
                                     font=(self.ui_font, 11), text_color=C_GRAY)
        self.lbl_time.pack(side="right", padx=20)

        ctk.CTkButton(top, text="⟳ 重新掃描", width=110, font=(self.ui_font, 12),
                      fg_color="#1f4e79", hover_color="#2980b9",
                      command=self._on_refresh).pack(side="right", padx=8, pady=10)

        self.lbl_status = ctk.CTkLabel(top, text="",
                                       font=(self.ui_font, 11), text_color=C_YELLOW)
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
            ctk.CTkLabel(leg, text=txt, font=(self.ui_font, 10),
                         text_color=clr).pack(side="left", padx=10)

        # 表格
        tbl = ctk.CTkFrame(tab, fg_color=BG, corner_radius=0)
        tbl.pack(fill="both", expand=True, padx=10, pady=(4, 4))

        self.tree = ttk.Treeview(tbl, style="S.Treeview",
                                 columns=[c[0] for c in SCAN_COLS],
                                 show="headings", selectmode="browse")
        for cid, hd, w, anc in SCAN_COLS:
            self.tree.heading(cid, text=hd)
            self.tree.column(cid, width=w, anchor=anc, minwidth=40)

        sb = ttk.Scrollbar(tbl, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

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
                                        font=(self.ui_font, 11), text_color=C_GRAY)
        self.lbl_summary.pack(side="left", padx=20, pady=8)

    # ════════════════════════════════════════════════════════════════════
    # 回測 Tab
    # ════════════════════════════════════════════════════════════════════

    def _build_backtest_tab(self, tab):
        tab.configure(fg_color=BG)

        # 工具列
        bar = ctk.CTkFrame(tab, fg_color=BG_PANEL, height=46, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="📈 2年信號回測明細",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE
                     ).pack(side="left", padx=16, pady=12)
        ctk.CTkLabel(bar, text="快取為上次 --backtest 執行結果　點選左側股票查看詳情",
                     font=(self.ui_font, 10), text_color=C_GRAY
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
        self._bt_cache  = _load_backtest_cache()
        self._dca_cache = _load_dca_cache()
        self._bt_btns   = {}

        try:
            cfg = _load_config()
            stocks = cfg["watchlist"]["etf"] + cfg["watchlist"]["ai_tech"]
        except Exception:
            stocks = []

        for s in stocks:
            sym, name = s["symbol"], s["name"]
            has = sym in self._bt_cache
            btn = ctk.CTkButton(
                self._bt_list,
                text=f"{sym.replace('.TW','')}  {name}",
                font=(self.ui_font, 11),
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
        if not self._bt_cache:
            ctk.CTkLabel(self._bt_detail,
                         text="尚無回測快取\n\n請先執行：\npython tw_scheduler.py --backtest",
                         font=(self.ui_font, 13), text_color=C_YELLOW,
                         justify="center").pack(pady=60)
        else:
            ctk.CTkLabel(self._bt_detail,
                         text="← 點選左側股票查看回測明細",
                         font=(self.ui_font, 13), text_color=C_GRAY
                         ).pack(pady=60)

    def _on_bt_select(self, sym: str, name: str):
        for s, b in self._bt_btns.items():
            b.configure(fg_color="#1f4e79" if s == sym else "transparent")

        for w in self._bt_detail.winfo_children():
            w.destroy()

        bt = self._bt_cache.get(sym)
        if not bt:
            ctk.CTkLabel(self._bt_detail,
                         text=f"⚠️ {sym} 無回測資料\n請先執行：python tw_scheduler.py --backtest",
                         font=(self.ui_font, 12), text_color=C_YELLOW).pack(pady=40)
            return

        bnh = bt.get("bnh_return_pct", 0)

        ctk.CTkLabel(self._bt_detail,
                     text=f"{sym.replace('.TW','')} {name}　{bt.get('period','2y')} 回測",
                     font=(self.ui_font, 15, "bold"), text_color=C_WHITE
                     ).pack(anchor="w", padx=16, pady=(16, 2))
        ctk.CTkLabel(self._bt_detail,
                     text=f"📌 B&H 買入持有基準：{bnh:+.2f}%",
                     font=(self.ui_font, 12), text_color=C_BLUE
                     ).pack(anchor="w", padx=16, pady=(0, 12))

        for strat in bt.get("strategies", []):
            self._bt_strat_card(strat, bnh, sym, name)

        # 10Y DCA 區塊
        dca = self._dca_cache.get(sym)
        if dca:
            self._bt_dca_section(dca, sym, name)
        else:
            ctk.CTkLabel(self._bt_detail,
                         text="⚠️ 無 10年DCA快取　請先執行：python tw_scheduler.py --dca",
                         font=(self.ui_font, 11), text_color=C_GRAY
                         ).pack(anchor="w", padx=16, pady=(8, 4))

    def _bt_strat_card(self, strat: dict, bnh: float, sym: str, name: str):
        total  = strat.get("total_return_pct", 0)
        beat   = total > bnh
        lbl    = strat["label"].replace("v2 ", "")

        card = ctk.CTkFrame(self._bt_detail,
                            fg_color="#0d2d0d" if beat else "#2d0d0d",
                            corner_radius=10)
        card.pack(fill="x", padx=12, pady=6)

        ctk.CTkLabel(card, text=f"{'✅' if beat else '⚠️'} {lbl}",
                     font=(self.ui_font, 13, "bold"),
                     text_color=C_GREEN if beat else C_YELLOW
                     ).pack(anchor="w", padx=14, pady=(10, 4))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 6))

        mdd    = strat.get("max_drawdown_pct")
        sharpe = strat.get("sharpe")
        wr     = strat.get("win_rate")

        for label, val, clr in [
            ("總報酬",  f"{total:+.2f}%",               C_GREEN if total >= 0 else C_RED),
            ("MDD",     f"{mdd}%" if mdd else "N/A",     C_RED),
            ("Sharpe",  str(sharpe) if sharpe else "N/A", C_YELLOW),
            ("勝率",    f"{wr}%" if wr else "N/A",        C_WHITE),
            ("交易次數", str(strat.get("trades", 0)),      C_WHITE),
        ]:
            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="left", padx=12)
            ctk.CTkLabel(col, text=label,
                         font=(self.ui_font, 10), text_color=C_GRAY).pack()
            ctk.CTkLabel(col, text=val,
                         font=(self.ui_font, 12, "bold"), text_color=clr).pack()

        trades = strat.get("trade_log", [])
        if trades:
            ctk.CTkButton(
                card,
                text=f"📋 展開明細（{len(trades)} 筆交易）",
                font=(self.ui_font, 11),
                fg_color="#1a3a60", hover_color="#2d5a8e",
                text_color="#74b9ff", height=28,
                command=lambda t=trades, l=lbl: self._trade_popup(
                    t, f"{sym.replace('.TW','')} {name}", l),
            ).pack(anchor="w", padx=14, pady=(4, 12))
        else:
            ctk.CTkLabel(card, text="此區間無交易紀錄（信號未觸發）",
                         font=(self.ui_font, 11), text_color=C_GRAY
                         ).pack(anchor="w", padx=14, pady=(4, 12))

    def _bt_dca_section(self, dca: dict, sym: str, name: str):
        period  = dca.get("period", "10年")
        budget  = dca.get("annual_budget", 100_000)

        sep = ctk.CTkFrame(self._bt_detail, fg_color="#2a2a4a", height=2, corner_radius=0)
        sep.pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(self._bt_detail,
                     text=f"📊 10年 DCA 回測　{period}　每年注資 NT${budget:,}",
                     font=(self.ui_font, 13, "bold"), text_color=C_BLUE
                     ).pack(anchor="w", padx=16, pady=(0, 8))

        # B&H DCA 基準
        bnh_strat = next((s for s in dca.get("strategies", []) if "B&H" in s["label"]), None)
        bnh_cagr  = bnh_strat["cagr_pct"] if bnh_strat else 0

        for strat in dca.get("strategies", []):
            cagr   = strat.get("cagr_pct", 0)
            total  = strat.get("total_return_pct", 0)
            mdd    = strat.get("max_drawdown_pct", 0)
            fval   = strat.get("final_value", 0)
            inv    = strat.get("total_invested", 0)
            ntx    = strat.get("n_transactions", 0)
            lbl    = strat["label"]
            beat   = cagr > bnh_cagr and "B&H" not in lbl

            card = ctk.CTkFrame(self._bt_detail,
                                fg_color="#0d2d0d" if beat else "#0d1a2d",
                                corner_radius=10)
            card.pack(fill="x", padx=12, pady=4)

            flag = "✅" if beat else ("📌" if "B&H" in lbl else "⚠️")
            ctk.CTkLabel(card, text=f"{flag} {lbl}",
                         font=(self.ui_font, 12, "bold"),
                         text_color=C_STRONG if "B&H" in lbl else (C_GREEN if beat else C_YELLOW)
                         ).pack(anchor="w", padx=14, pady=(8, 4))

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
                             font=(self.ui_font, 10), text_color=C_GRAY).pack()
                ctk.CTkLabel(col, text=val,
                             font=(self.ui_font, 11, "bold"), text_color=clr).pack()

            txs = strat.get("transactions", strat.get("last_tx", []))
            if txs:
                ctk.CTkButton(
                    card,
                    text=f"📋 展開注資明細（{len(txs)} 筆）",
                    font=(self.ui_font, 11),
                    fg_color="#1a3a60", hover_color="#2d5a8e",
                    text_color="#74b9ff", height=26,
                    command=lambda t=txs, l=lbl: self._dca_popup(
                        t, f"{sym.replace('.TW','')} {name}", l),
                ).pack(anchor="w", padx=14, pady=(2, 10))

    def _dca_popup(self, transactions: list[dict], stock: str, strategy: str):
        import tkinter as tk
        from tkinter import ttk as _ttk

        win = tk.Toplevel(self)
        win.title(f"{stock}  {strategy}")
        win.geometry("640x480")
        win.configure(bg="#0f1a30")
        win.lift()

        hdr = tk.Frame(win, bg="#0f1a30")
        hdr.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(hdr, text=f"{stock}  {strategy}",
                 fg="#74b9ff", bg="#0f1a30",
                 font=(self.ui_font, 12, "bold")).pack(side="left")
        tk.Label(hdr, text=f"  共 {len(transactions)} 筆注資",
                 fg="#888", bg="#0f1a30",
                 font=("Consolas", 11)).pack(side="left")

        total_cost = sum(t.get("cost", 0) for t in transactions)
        smr = tk.Frame(win, bg="#1a2a40")
        smr.pack(fill="x", padx=10, pady=2)
        tk.Label(smr, text=f"總注資  NT${total_cost:,.0f}",
                 fg="#fdcb6e", bg="#1a2a40",
                 font=("Consolas", 10)).pack(side="left", padx=10, pady=4)

        sty = _ttk.Style(win)
        sty.theme_use("clam")
        sty.configure("D.Treeview",
                      background="#0d1b2a", foreground=C_WHITE,
                      fieldbackground="#0d1b2a", rowheight=24,
                      font=("Consolas", 11))
        sty.configure("D.Treeview.Heading",
                      background="#0a0f1a", foreground="#74b9ff",
                      font=(self.ui_font, 11, "bold"))
        sty.map("D.Treeview", background=[("selected", "#1c4f82")])

        cols   = ("注資日期", "買入價格", "買入股數", "注資金額")
        widths = (130, 110, 110, 130)

        wrap = tk.Frame(win, bg="#0f1a30")
        wrap.pack(fill="both", expand=True, padx=10, pady=6)

        tree = _ttk.Treeview(wrap, style="D.Treeview",
                              columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="center")

        vsb = _ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for t in transactions:
            tree.insert("", "end", values=(
                t.get("date", "?"),
                f"{t.get('price', 0):,.2f}",
                f"{int(t.get('shares', 0)):,}",
                f"NT${t.get('cost', 0):,.0f}",
            ))

    def _trade_popup(self, trades: list[dict], stock: str, strategy: str):
        import tkinter as tk
        from tkinter import ttk as _ttk

        win = tk.Toplevel(self)
        win.title(f"{stock}  {strategy}")
        win.geometry("720x500")
        win.configure(bg="#0f1a30")
        win.lift()

        # 標題列
        hdr = tk.Frame(win, bg="#0f1a30")
        hdr.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(hdr, text=f"{stock}  {strategy}",
                 fg="#74b9ff", bg="#0f1a30",
                 font=(self.ui_font, 12, "bold")).pack(side="left")
        tk.Label(hdr, text=f"  共 {len(trades)} 筆",
                 fg="#888888", bg="#0f1a30",
                 font=("Consolas", 11)).pack(side="left")

        # 勝率摘要
        pnls   = [t.get("pnl_pct", 0) for t in trades]
        wins   = sum(1 for p in pnls if p > 0)
        avg    = sum(pnls) / len(pnls) if pnls else 0
        wr_pct = wins / len(trades) * 100 if trades else 0

        smr = tk.Frame(win, bg="#1a2a40")
        smr.pack(fill="x", padx=10, pady=2)
        tk.Label(smr,
                 text=f"勝率  {wins}/{len(trades)} = {wr_pct:.0f}%    平均報酬  {avg:+.2f}%",
                 fg="#fdcb6e", bg="#1a2a40",
                 font=("Consolas", 10)).pack(side="left", padx=10, pady=4)

        # 表格樣式
        sty = _ttk.Style(win)
        sty.theme_use("clam")
        sty.configure("T.Treeview",
                      background="#0d1b2a", foreground=C_WHITE,
                      fieldbackground="#0d1b2a", rowheight=24,
                      font=("Consolas", 11))
        sty.configure("T.Treeview.Heading",
                      background="#0a0f1a", foreground="#74b9ff",
                      font=(self.ui_font, 11, "bold"))
        sty.map("T.Treeview", background=[("selected", "#1c4f82")])

        cols   = ("進場日", "出場日", "進場價", "出場價", "報酬%")
        widths = (110, 110, 100, 100, 110)

        wrap = tk.Frame(win, bg="#0f1a30")
        wrap.pack(fill="both", expand=True, padx=10, pady=6)

        tree = _ttk.Treeview(wrap, style="T.Treeview",
                              columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="center")

        tree.tag_configure("win",  foreground=C_GREEN)
        tree.tag_configure("loss", foreground=C_RED)
        tree.tag_configure("hold", foreground=C_YELLOW)

        vsb = _ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for t in trades:
            entry_d = t.get("entry_date", "?")
            exit_d  = t.get("exit_date",  t.get("date", "?"))
            entry_p = t.get("entry", 0)
            exit_p  = t.get("exit",  0)
            pnl     = t.get("pnl_pct", 0)

            if exit_p == 0:
                tag, exit_d, pnl_str = "hold", "持倉中", "持倉中"
            else:
                tag     = "win" if pnl > 0 else "loss"
                pnl_str = f"{pnl:+.2f}%"

            tree.insert("", "end", tags=(tag,), values=(
                entry_d,
                exit_d,
                f"{entry_p:,.0f}",
                f"{exit_p:,.0f}" if exit_p > 0 else "—",
                pnl_str,
            ))

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
        rows.sort(key=lambda r: order.get(r["_signal_raw"], 5))

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
                r["avwap"], r["b1"], r["b2"], r["s_target"],
                r["dd"], r["pnl"],
            ])

        parts = [f"{k.replace('STRONG BUY','強買')} {v}"
                 for k, v in counts.items() if v]
        self.lbl_summary.configure(
            text=f"共 {len(rows)} 檔　{'　'.join(parts) or '無明確信號'}"
                 "　（試買=AVWAP×b1  加碼=AVWAP×b2  賣出=AVWAP×s）"
        )


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    TwStrategyApp().mainloop()


if __name__ == "__main__":
    main()
