# AI_HANDOFF — 台股研究

> 上次更新：2026-04-30（Session 7 開始）
> GitHub：https://github.com/jackwahahe-beep/taiwan-stock-research
> 最新 commit：3cd7679

---

## 鐵則（AI 操作規範）

- **Edit / Read / Bash / Write 直接執行**，不詢問用戶，自行選擇最優解
- 只有高風險操作（刪除重要資料、force push、外部服務操作）才暫停詢問
- `bypassPermissions` 已寫入 `~/.claude/settings.json` 與 `.claude/settings.json`，**新 Session 起效**
- 每次 Session 開始前先更新此文件再動手改程式碼

---

## 專案目的

每日自動掃描台灣股票（6 ETF + 9 AI科技股），計算技術指標信號，
透過 Discord Webhook 推播買入／賣出提醒，含 10 年 DCA 長期回測分析。

---

## 目前策略版本：v2（2026-04-29 升級）

### 核心邏輯（與美股研究對齊）

| 指標 | 說明 |
|---|---|
| **AVWAP** | 從最近 60 天低點錨定的成交量加權均價，作為買賣核心錨點 |
| **DD** | 從近 60 天高點的回撤幅度 |
| **市場模式** | ^TWII vs MA200 + 0050波動率 → NORMAL / WARN / RISK |
| **個股RSI閾值** | 每檔獨立設定（大型科技 50，高息ETF 42，中型科技 48） |

### 買入條件（三條同時）

| 信號等級 | 條件 |
|---|---|
| **STRONG BUY** | DD ≤ -20% + 價格 < AVWAP×b2 + RSI ≤ rsi_sbuy |
| **BUY** | DD ≤ -10% + 價格 < AVWAP×b1 + RSI ≤ rsi_buy |
| 輔助 | MA20 黃金交叉 MA60（保留，不需DD條件） |

### 賣出條件（三條同時）

RSI ≥ rsi_sell **且** 價格 ≥ AVWAP×s **且** 價格 > MA20×1.15

### 市場模式過濾

| 模式 | 觸發條件 | 推播行為 |
|---|---|---|
| NORMAL | 大盤 > MA200 ×1.02 | 正常推 BUY / STRONG BUY |
| WARN | 大盤在 MA200 ±2% 或高波動偏弱 | BUY 仍推但加警示 |
| RISK | 大盤 < MA200 -5% 或跌破且波動>25% | 只推 STRONG BUY |

---

## 個股信號參數（SIGNAL_CONFIG）

```python
# tw_screener.py / SIGNAL_CONFIG
"0050.TW":   rsi_buy=45, rsi_sbuy=35, rsi_sell=70, b1=0.98, b2=0.93, s=1.12
"006208.TW": rsi_buy=45, rsi_sbuy=35, rsi_sell=70, b1=0.98, b2=0.93, s=1.12
"00878.TW":  rsi_buy=42, rsi_sbuy=32, rsi_sell=65, b1=0.97, b2=0.92, s=1.10
"00713.TW":  rsi_buy=42, rsi_sbuy=32, rsi_sell=65, b1=0.97, b2=0.92, s=1.10
"00929.TW":  rsi_buy=45, rsi_sbuy=35, rsi_sell=68, b1=0.98, b2=0.93, s=1.10
"00919.TW":  rsi_buy=50, rsi_sbuy=40, rsi_sell=68  ← 成立時間短，閾值放寬
"2330.TW":   rsi_buy=50, rsi_sbuy=40, rsi_sell=75, b1=0.97, b2=0.91, s=1.15
"2454.TW":   rsi_buy=50, rsi_sbuy=40, rsi_sell=75
"2382.TW":   rsi_buy=50, rsi_sbuy=40, rsi_sell=72
"2308.TW":   rsi_buy=50, rsi_sbuy=40, rsi_sell=75
"3711.TW":   rsi_buy=48, rsi_sbuy=38, rsi_sell=72
"2303.TW":   rsi_buy=45, rsi_sbuy=45, rsi_sell=70  ← STRONG BUY 合併 BUY（回撤門檻極少觸發）
"3037.TW":   rsi_buy=48, rsi_sbuy=38, rsi_sell=72, bnh_dca=True  ← 超強趨勢股，DCA用B&H
"2408.TW":   rsi_buy=45, rsi_sbuy=35, rsi_sell=70
"6770.TW":   rsi_buy=45, rsi_sbuy=35, rsi_sell=70
```

---

## 10 年 DCA 回測結論（2015–2025，每年 NT$100k）

### 策略選擇建議

| 股票類型 | 建議策略 | 原因 |
|---|---|---|
| ETF（0050/006208） | **B&H DCA** | 牛市中等回調會錯過漲幅，B&H CAGR 13.3% 最優 |
| 高息ETF（00878/00713） | **v2 BUY DCA** | 回撤從 -21% → -10%，少賺但少虧 |
| 台積電（2330） | **B&H DCA** | 趨勢太強，B&H CAGR 21.1% |
| 欣興（3037） | **B&H DCA**（`bnh_dca`旗標） | 超強趨勢股，任何擇時策略都輸 B&H |
| 台達電（2308） | **v2 STRONG BUY DCA** | 532% vs B&H 489%，高波動股擇時有效 |
| 聯發科（2454） | **v2 BUY DCA** | 微勝 B&H（410% vs 403%）|
| 南亞科（2408） | **v2 BUY DCA** | 331% vs B&H 306%（+25pp）|

### 重要細節
- 資料範圍：`start="2015-01-01", end="2025-12-31"`（明確日期，非 period="10y"，後者對台股不穩定）
- 年末強制投入（year-end fallback）：若該年未觸發任何信號，最後一個交易日強制部署全部現金
- 觸發條件記錄：每筆交易的 DD%/RSI/vs_b1% 存入 JSON 供 UI 查閱
- `fallback: True` 旗標：年末強制投入的交易不記錄觸發條件，UI 用灰色 + ↩ 標示

---

## 檔案結構

```
台股研究/
├── AI_HANDOFF.md           ← 本文件（每次 Session 開始先讀）
├── FUNCTION_SPEC.md        ← 功能規格（已更新至 v2.2）
├── config.yaml             ← 追蹤清單 + 信號參數 + 持股 + Discord（URL 用 .env）
├── .env                    ← DISCORD_WEBHOOK_URL（不進 git）
├── tw_screener.py          ← v2 掃描器：AVWAP+DD+市場模式+個股RSI
├── tw_backtest_dca.py      ← v2 DCA長期回測：4策略，明確10年資料，年末fallback
├── tw_discord.py           ← v2 推播：edge-triggered賣出、市場模式header
├── tw_portfolio.py         ← 持股追蹤：含賣出建議（全賣/減碼/等反彈）+ 金額
├── tw_outcome.py           ← 信號事後驗證：5日後對答案，累積正確率
├── tw_scheduler.py         ← 排程：--dca 旗標執行長期回測
├── tw_ui.py                ← 桌面UI：掃描/持股/DCA回測三個Tab
├── requirements.txt
├── backup/2026-04-30/      ← Session 7 開始前備份
└── cache/
    ├── scan_YYYY-MM-DD.json
    ├── dca_backtest_YYYY-MM-DD.json
    └── outcomes/outcome_YYYY-MM-DD.json
```

---

## 執行方式

```bash
# 手動掃描一次
python tw_scheduler.py

# 跑 10年DCA長期回測並推播
python tw_scheduler.py --dca

# 常駐排程
python tw_scheduler.py --daemon

# 週報推播
python tw_scheduler.py --weekly

# 查看信號準確率
python tw_scheduler.py --outcome
```

---

## 推播格式（v2）

### 買入 embed
```
🟢🟢 強力買入信號 / 🟢 買入信號 ｜ XXXX 股名
現價: NT$XXX  RSI: XX  DD/AVWAP距離: -12.5% / -3.2%
📌 建議進場：掛單價 NT$XXX  買 NNN 股  預估成本 NT$100,000
觸發信號：• 買入：回撤-12%，RSI 44，低於AVWAP -3.2%
⚠️ 市場警戒模式（如適用）
```

### 賣出 embed（持股）
```
🔴 建議賣出（獲利出場）｜ XXXX 股名
現價: NT$XX  持股損益: NT$+X,XXX (+11%)  RSI: XX
操作依據：• RSI 89 過熱，超過AVWAP目標...
📌 賣出建議：
  🔴 建議全部賣出（獲利了結）
  賣出 2000 股 @ NT$53  預估回收 NT$106,000  實現損益 NT$+10,840
```

### 賣出 edge-trigger 機制
- 新出現的賣出信號：**🔴 新賣出**（粗體）
- 持續多日的賣出信號：🔴 賣出（持續中）
- 比對邏輯：讀取 `cache/scan_{yesterday}.json` 取出昨日 SELL 股票集合

---

## 持股現況（2026-04-30）

| 代號 | 名稱 | 股數 | 成本 | 現況 |
|---|---|---|---|---|
| 00713.TW | 元大台灣高息低波 | 2,000 | NT$47.58 | 持有中 |
| 2409.TW | 友達 | 400 | NT$27.41 | 待機賣出（虧損中）|
| 2618.TW | 長榮航 | 59 | NT$0（配股）| 觀察中 |

---

## Session 7 計畫（2026-04-30，進行中）

### 五項改善，同步實作

| # | 功能 | 主要改動 |
|---|---|---|
| 1 | **DCA 資產曲線圖** | tw_ui.py：在回測 Tab 加 matplotlib 折線圖（4策略對比 + 買入點標記） |
| 2 | **信號準確度儀表板** | tw_ui.py：新增準確度 Tab；tw_discord.py：週報加正確率；低準確率信號加警示 |
| 3 | **持股 P&L 追蹤** | tw_portfolio.py + tw_ui.py：記錄買入成本，顯示浮盈/浮虧/持有天數 |
| 4 | **參數敏感度分析** | tw_ui.py：DCA Tab 加敏感度表（DD -8%~-20% 每步2% 的 CAGR 變化） |
| 5 | **GitHub Actions 失敗通知** | tw_daily.yml + tw_weekly.yml：加 `if: failure()` Discord 推播步驟 |

### 實作注意事項
- matplotlib 已在 requirements.txt；若無則補上
- 持股 P&L 追蹤的買入成本來自 `config.yaml portfolio.cost`（已有欄位）
- 敏感度分析：在 UI 端重新跑 `_run_dca` 的輕量版，只需當前 cache 資料
- 圖表嵌入：用 `matplotlib.backends.backend_tkagg.FigureCanvasTkAgg`

---

## 待辦事項（Session 7 以外）

### 🟡 中優先
- [ ] tw_ui.py 盤中即時報價（目前為日線收盤，非 tick）
- [ ] 友達（2409）現價 NT$17.30，虧損 -37%，純等反彈推播，無停損機制

### 🟢 低優先
- [ ] scheduler 模式重構（以 enum 取代 sys.argv）

---

## Session 7 完成事項（2026-04-30）

1. **DCA 資料修正** — `_fetch_dca_data()` 改用明確日期範圍（2015-01-01 起），修正 `period="10y"` 對台股無效問題
2. **年末強制投入** — 擇時策略若全年未部署，最後交易日強制買入；`fallback: True` 旗標區分
3. **交易明細彈窗升級** — 顯示持有報酬%、期末市值/總報酬、年末強制標記（↩ 灰色）
4. **策略摘要比較表** — DCA Tab 最上方新增 CAGR/總報酬/獲利/終值/MDD 對比網格
5. **策略說明彈窗** — 每張策略卡加「ℹ 策略說明」按鈕
6. **_dca_popup call site 修正** — 正確傳入 `final_price` 參數
7. **GitHub Actions 提前2小時** — cron 改為 UTC 23:30 / 04:00，預留 free-tier queue 延遲緩衝

---

## Session 6 完成事項（2026-04-29）

1. **`_detect_bounce()` v2 化** — 改用個股 `rsi_sbuy` 取代硬碼 30；新增第 4 條件：價格從 AVWAP×0.97 以下回升至 AVWAP 附近
2. **回測加 MDD / Sharpe** — `_run_backtest_v2()` 新增 `pv_list` 逐日追蹤，計算最大回撤與年化 Sharpe；`build_backtest_embed()` 顯示兩項指標
3. **週報摘要功能** — `tw_discord.build_weekly_embed()` 讀取過去 7 天 scan cache，統計各股買賣信號次數 + 市場模式分布；每週五 17:00 自動觸發

---

## Session 5 完成事項（2026-04-29）

1. **確認友達（2409）出場信號** — 現價 NT$17.30，虧損 -37%，無反彈信號，維持 EXIT_WAIT
2. **FUNCTION_SPEC.md 全面重寫至 v2** — AVWAP+DD+市場模式+個股RSI 架構完整記錄
3. **台灣假日過濾** — `is_trading_day()` 加入 `tw_scheduler.py`；`holidays` 加入 requirements.txt
4. **合併 fetch_data / fetch_long** — tw_backtest.py 改 import tw_screener.fetch_data
5. **市場模式 header 加具體數字** — TWII 現價 / MA200 / 差距% / 波動率 顯示在每日推播
6. **DCA embed 加建議策略** — `RECOMMENDED_DCA` 字典定義各股最佳策略；embed 加 ⭐建議標記
