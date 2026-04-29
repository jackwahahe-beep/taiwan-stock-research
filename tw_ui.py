"""
台股策略看盤介面
顯示所有追蹤股票的 AVWAP 目標價位、現況信號、持股損益。
"""

import json
import threading
import glob as _glob
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import customtkinter as ctk
from tkinter import ttk, font as tkfont

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
TZ = ZoneInfo("Asia/Taipei")

# ── 顏色常數 ──────────────────────────────────────────────────────────────────
BG          = "#1a1a2e"
BG_PANEL    = "#16213e"
BG_ROW_A    = "#0f3460"
BG_ROW_B    = "#0a2744"
BG_HEADER   = "#0d1b2a"

C_GREEN     = "#2ecc71"
C_STRONG    = "#1abc9c"
C_RED       = "#e74c3c"
C_YELLOW    = "#f1c40f"
C_ORANGE    = "#e67e22"
C_GRAY      = "#95a5a6"
C_WHITE     = "#ecf0f1"
C_BLUE      = "#3498db"
C_PURPLE    = "#9b59b6"

SIGNAL_COLOR = {
    "STRONG BUY": C_STRONG,
    "BUY":        C_GREEN,
    "SELL":       C_RED,
    "WATCH":      C_YELLOW,
    "HOLD":       C_GRAY,
}

# 每股策略類別標籤
CATEGORY = {
    "0050.TW":   ("ETF 基準", "B&H"),
    "00878.TW":  ("高息 ETF", "BUY"),
    "00713.TW":  ("高息 ETF", "BUY"),
    "00929.TW":  ("高息 ETF", "強買"),
    "00919.TW":  ("高息 ETF", "B&H"),
    "2330.TW":   ("大型科技", "B&H"),
    "2454.TW":   ("大型科技", "BUY"),
    "3711.TW":   ("中型科技", "B&H"),
    "2303.TW":   ("中型科技", "B&H"),
    "2382.TW":   ("AI伺服器", "B&H"),
    "2308.TW":   ("電源散熱", "強買"),
    "3037.TW":   ("趨勢強股", "B&H"),
    "2408.TW":   ("記憶體",  "BUY"),
}

CATEGORY_COLOR = {
    "ETF 基準": "#2980b9",
    "高息 ETF": "#27ae60",
    "大型科技": "#8e44ad",
    "中型科技": "#2c3e50",
    "AI伺服器": "#1a6b4a",
    "電源散熱": "#b7410e",
    "趨勢強股": "#c0392b",
    "記憶體":   "#16537e",
}

REC_COLOR = {"B&H": C_BLUE, "BUY": C_GREEN, "強買": C_STRONG}

COLS = [
    ("symbol",   "代號",         80,  "w"),
    ("name",     "名稱",        100,  "w"),
    ("cat",      "類別",         90,  "center"),
    ("rec",      "DCA策略",      70,  "center"),
    ("signal",   "信號",         90,  "center"),
    ("price",    "現價",         70,  "e"),
    ("rsi",      "RSI",          50,  "center"),
    ("avwap",    "AVWAP",        80,  "e"),
    ("b1",       "試買",         80,  "e"),
    ("b2",       "加碼",         80,  "e"),
    ("s_target", "賣出參考",     85,  "e"),
    ("dd",       "回撤",         60,  "center"),
    ("pnl",      "持股損益",    100,  "e"),
]


def _load_scan_cache() -> list[dict]:
    files = sorted(_glob.glob(str(CACHE_DIR / "scan_*.json")), reverse=True)
    if not files:
        return []
    return json.loads(Path(files[0]).read_text(encoding="utf-8"))


def _load_config() -> dict:
    import yaml
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_rows(scan_records: list[dict], cfg: dict) -> list[dict]:
    from tw_screener import SIGNAL_CONFIG, _DEFAULT_CFG

    portfolio = {h["symbol"]: h for h in cfg.get("portfolio", [])}
    rows = []

    for r in scan_records:
        sym  = r["symbol"]
        scfg = SIGNAL_CONFIG.get(sym, _DEFAULT_CFG)
        avwap = r.get("avwap", 0)
        price = r.get("price", 0)

        # 信號
        sigs = r.get("signals", [])
        sig_types = [s["type"] for s in sigs if s["type"] in ("STRONG BUY", "BUY", "SELL", "WATCH")]
        if "STRONG BUY" in sig_types:
            signal = "STRONG BUY"
        elif "BUY" in sig_types:
            signal = "BUY"
        elif "SELL" in sig_types:
            signal = "SELL"
        elif "WATCH" in sig_types:
            signal = "WATCH"
        else:
            signal = "HOLD"

        # AVWAP 目標價
        b1       = round(avwap * scfg["b1"], 1) if avwap else 0
        b2       = round(avwap * scfg["b2"], 1) if avwap else 0
        s_target = round(avwap * scfg["s"],  1) if avwap else 0

        # 持股損益
        pnl_str = ""
        if sym in portfolio:
            h   = portfolio[sym]
            pnl = round((price - h["cost"]) * h["shares"], 0) if h["cost"] > 0 else None
            pct = round((price - h["cost"]) / h["cost"] * 100, 2) if h["cost"] > 0 else None
            if pnl is not None:
                sign = "+" if pnl >= 0 else ""
                pnl_str = f"{sign}{int(pnl):,} ({pct:+.1f}%)"
            else:
                pnl_str = f"NT${int(price * h['shares']):,} (配股)"

        cat, rec = CATEGORY.get(sym, ("—", "—"))

        rows.append({
            "symbol":   sym.replace(".TW", ""),
            "name":     r.get("name", sym),
            "cat":      cat,
            "rec":      rec,
            "signal":   signal,
            "price":    f"{price:,.1f}" if price else "—",
            "rsi":      str(r.get("rsi", "—")),
            "avwap":    f"{avwap:,.1f}" if avwap else "—",
            "b1":       f"{b1:,.1f}" if b1 else "—",
            "b2":       f"{b2:,.1f}" if b2 else "—",
            "s_target": f"{s_target:,.1f}" if s_target else "—",
            "dd":       f"{r.get('dd_pct', 0):+.1f}%",
            "pnl":      pnl_str,
            "_signal_raw": signal,
            "_sym_full":   sym,
            "_in_portfolio": sym in portfolio,
        })

    return rows


class TwStrategyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("台股策略看盤")
        self.geometry("1400x780")
        self.minsize(1100, 600)
        self.configure(fg_color=BG)

        try:
            families = set(tkfont.families())
            self.ui_font = (
                "Microsoft JhengHei UI" if "Microsoft JhengHei UI" in families
                else "Microsoft JhengHei"
            )
            self.option_add("*Font", (self.ui_font, 11))
        except Exception:
            self.ui_font = "Arial"

        self._loading = False
        self._scan_records: list[dict] = []
        self._cfg: dict = {}

        self._setup_style()
        self._build_ui()

        self.after(200, self._initial_load)

    # ── Style ─────────────────────────────────────────────────────────────────

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Strategy.Treeview",
                        background=BG_ROW_B,
                        foreground=C_WHITE,
                        fieldbackground=BG_ROW_B,
                        rowheight=28,
                        font=(self.ui_font, 11))
        style.configure("Strategy.Treeview.Heading",
                        background=BG_HEADER,
                        foreground=C_BLUE,
                        font=(self.ui_font, 11, "bold"),
                        relief="flat")
        style.map("Strategy.Treeview",
                  background=[("selected", "#1c4f82")],
                  foreground=[("selected", C_WHITE)])
        style.layout("Strategy.Treeview", [
            ("Strategy.Treeview.treearea", {"sticky": "nswe"})
        ])

    # ── UI Layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── 頂部狀態欄 ──────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color=BG_PANEL, height=50, corner_radius=0)
        top.pack(fill="x", padx=0, pady=0)
        top.pack_propagate(False)

        self.lbl_market = ctk.CTkLabel(
            top, text="載入中...",
            font=(self.ui_font, 13),
            text_color=C_GRAY,
        )
        self.lbl_market.pack(side="left", padx=20, pady=12)

        self.lbl_time = ctk.CTkLabel(
            top, text="",
            font=(self.ui_font, 11),
            text_color=C_GRAY,
        )
        self.lbl_time.pack(side="right", padx=20)

        btn_refresh = ctk.CTkButton(
            top, text="⟳ 重新掃描", width=110,
            font=(self.ui_font, 12),
            fg_color="#1f4e79", hover_color="#2980b9",
            command=self._on_refresh,
        )
        btn_refresh.pack(side="right", padx=8, pady=10)

        self.lbl_status = ctk.CTkLabel(
            top, text="", font=(self.ui_font, 11), text_color=C_YELLOW,
        )
        self.lbl_status.pack(side="right", padx=4)

        # ── 圖例 ────────────────────────────────────────────────────────────
        legend = ctk.CTkFrame(self, fg_color=BG_PANEL, height=30, corner_radius=0)
        legend.pack(fill="x", padx=0)
        legend.pack_propagate(False)

        legend_items = [
            ("♦ 強力買入", C_STRONG),
            ("♦ 買入",     C_GREEN),
            ("♦ 賣出",     C_RED),
            ("♦ 量能注意", C_YELLOW),
            ("♦ 持有",     C_GRAY),
            ("  試買 = AVWAP×b1", C_GRAY),
            ("  加碼 = AVWAP×b2", C_GRAY),
            ("  賣出參考 = AVWAP×s", C_GRAY),
        ]
        for text, color in legend_items:
            ctk.CTkLabel(legend, text=text, font=(self.ui_font, 10),
                         text_color=color).pack(side="left", padx=10)

        # ── 表格區 ──────────────────────────────────────────────────────────
        table_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        self.tree = ttk.Treeview(
            table_frame,
            style="Strategy.Treeview",
            columns=[c[0] for c in COLS],
            show="headings",
            selectmode="browse",
        )

        for col_id, heading, width, anchor in COLS:
            self.tree.heading(col_id, text=heading)
            self.tree.column(col_id, width=width, anchor=anchor, minwidth=40)

        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 行顏色標籤
        self.tree.tag_configure("strong_buy",    foreground=C_STRONG,  background=BG_ROW_A)
        self.tree.tag_configure("buy",           foreground=C_GREEN,   background=BG_ROW_A)
        self.tree.tag_configure("sell",          foreground=C_RED,     background=BG_ROW_B)
        self.tree.tag_configure("watch",         foreground=C_YELLOW,  background=BG_ROW_B)
        self.tree.tag_configure("hold",          foreground=C_WHITE,   background=BG_ROW_B)
        self.tree.tag_configure("hold_alt",      foreground=C_WHITE,   background=BG_ROW_A)
        self.tree.tag_configure("portfolio",     foreground="#f0e68c",  background=BG_ROW_B)
        self.tree.tag_configure("portfolio_alt", foreground="#f0e68c",  background=BG_ROW_A)

        # ── 底部摘要 ────────────────────────────────────────────────────────
        bottom = ctk.CTkFrame(self, fg_color=BG_PANEL, height=36, corner_radius=0)
        bottom.pack(fill="x", padx=0, pady=0)
        bottom.pack_propagate(False)

        self.lbl_summary = ctk.CTkLabel(
            bottom, text="",
            font=(self.ui_font, 11),
            text_color=C_GRAY,
        )
        self.lbl_summary.pack(side="left", padx=20, pady=8)

    # ── Data ──────────────────────────────────────────────────────────────────

    def _initial_load(self):
        """啟動時先讀快取，不觸發 yfinance 下載。"""
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
                text=datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
            ))
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
            mode_str = {"NORMAL": "🟢 正常", "WARN": "🟡 警戒", "RISK": "🔴 風險"}.get(mode, mode)
            color    = {
                "NORMAL": C_GREEN, "WARN": C_YELLOW, "RISK": C_RED,
            }.get(mode, C_GRAY)
            text = f"市場 {mode_str}　TWII {twii}  MA200 {ma200}（{vs:+.1f}%）　波動率 {vol}%"
            self.lbl_market.configure(text=text, text_color=color)
        except Exception:
            pass

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self, records: list[dict]):
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self._cfg:
            try:
                self._cfg = _load_config()
            except Exception:
                pass

        rows = _build_rows(records, self._cfg)

        # 排序：有信號的排前面（STRONG BUY > BUY > SELL > WATCH > HOLD）
        order = {"STRONG BUY": 0, "BUY": 1, "SELL": 2, "WATCH": 3, "HOLD": 4}
        rows.sort(key=lambda r: order.get(r["_signal_raw"], 5))

        alt = 0
        counts = {"STRONG BUY": 0, "BUY": 0, "SELL": 0, "WATCH": 0}

        for r in rows:
            sig = r["_signal_raw"]
            in_port = r["_in_portfolio"]

            if sig == "STRONG BUY":
                tag = "strong_buy"; counts["STRONG BUY"] += 1
            elif sig == "BUY":
                tag = "buy"; counts["BUY"] += 1
            elif sig == "SELL":
                tag = "sell"; counts["SELL"] += 1
            elif sig == "WATCH":
                tag = "watch"; counts["WATCH"] += 1
            else:
                if in_port:
                    tag = "portfolio" if alt % 2 == 0 else "portfolio_alt"
                else:
                    tag = "hold" if alt % 2 == 0 else "hold_alt"
                alt += 1

            self.tree.insert("", "end", tags=(tag,), values=[
                r["symbol"], r["name"], r["cat"], r["rec"],
                r["signal"], r["price"], r["rsi"],
                r["avwap"], r["b1"], r["b2"], r["s_target"],
                r["dd"], r["pnl"],
            ])

        # 摘要列
        parts = []
        if counts["STRONG BUY"]: parts.append(f"強買 {counts['STRONG BUY']}")
        if counts["BUY"]:        parts.append(f"買入 {counts['BUY']}")
        if counts["SELL"]:       parts.append(f"賣出 {counts['SELL']}")
        if counts["WATCH"]:      parts.append(f"注意 {counts['WATCH']}")
        summary = "　".join(parts) if parts else "無明確信號"
        total = len(rows)
        self.lbl_summary.configure(
            text=f"共 {total} 檔　{summary}　（NT$ 每格試買=AVWAP×b1 加碼=AVWAP×b2 賣出=AVWAP×s）"
        )


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = TwStrategyApp()
    app.mainloop()


if __name__ == "__main__":
    main()
