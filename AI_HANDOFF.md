# AI_HANDOFF — 台股研究

> 上次更新：2026-05-19（Session 16 完成）
> GitHub：https://github.com/jackwahahe-beep/taiwan-stock-research
> 最新 commit：見 git log

---

## 策略一致性鐵則

> 掃描 tab「DCA策略」欄、Discord 推播建議策略、DCA tab 顯示策略，
> **必須全部從最新回測快取動態讀取**，禁止手動維護 CATEGORY rec 欄或 RECOMMENDED_DCA。
>
> - `_load_dca_rec()` 讀取最新 `cache/dca_backtest_*.json`，回傳 `{symbol: short_label}`
> - `tw_discord._dca_context_line()` 以 CAGR 最高者為最佳策略（不再查 RECOMMENDED_DCA）
> - 重新跑 DCA 回測後重啟程式即自動更新全端顯示

---

## Session 16 — 完成事項（2026-05-19）

### 鐵則確立
- **所有策略參數都必須有回測依據，策略調整也必須來自回測結果**
- 組合回測各股策略永遠從 `signal_backtest_*.json` 的 `best_mode` 讀取，不手動覆蓋
- 沒有回測依據的規則（如法人流量過濾）已移除或標注 TODO

### 推播 / 回測一致性修正（tw_discord.py, tw_screener.py）
- Discord `send_scan_results()`：新增 SBUY-only 過濾（bm_mode=SBUY 時，非 STRONG BUY 信號不推播）
- Discord `build_sell_embed()`：BNH 模式股票加警示欄位 "⚠️ 此股回測建議長期持有，不追隨 SELL"
- Discord `_sbt_context_line()`：各 mode 附加退出策略提示（BNH/SBUY/TRIM/TRAIL/ALL_DYN）
- `tw_screener.py`：移除未回測驗證的法人流量 BUY→WATCH 降級邏輯
- `tw_screener.get_market_mode()`：WARN 邊界從硬編碼 ±2% 改為讀 `load_sweep_params()["regime_threshold"]`

### 美股風格改善（tw_ui.py 組合回測 v2）
- 新增 Sortino ratio 到 stats grid（`_V2_METRIC_TIPS` + 紫色標籤）
- 新增「📋 交易明細」popup（3 tabs：B類信號交易 / A類DCA買入 / 年度績效比較）
- 資產曲線改為 2-subplot：上方 equity + 下方 underwater DD% chart
- 新增 Hybrid 模式 checkbox（B類股同時享年初 DCA 50% + 信號買入 50%）

### 參數掃描基礎設施（tw_backtest_signals.py）
- `load_sweep_params()`：新增返回 `bull_mult/warn_mult/bear_mult/a_cash_frac/b_base_pct`（含預設值）
- `save_sweep_params()`：改為部分更新（傳 None 的 section 不覆蓋現有資料）；新增 `alloc_results` 參數
- `sweep_regime_boundary()`：掃描 ±0.5/1/1.5/2/3/5% TWII MA200 切換門檻
- `sweep_crash_buy_gates()`：掃描熊市 BUY 崩跌加碼條件（5 個預設）
- `sweep_allocations()`：**新增**三階段配比掃描
  - Phase 1：bull/warn/bear 倍率組合（4 個預設：均等/溫和/積極/熊市保守）
  - Phase 2：A 類現金部署比例 a_cash_frac（0.40–0.80 共 5 步）
  - Phase 3：B 類每筆基礎投入比例 b_base_pct（0.15–0.35 共 5 步）
  - 每步固定前階段最佳值，避免參數互相干擾
- `run_portfolio_backtest_v2()`：新增 `bull_mult/warn_mult/bear_mult/a_cash_frac/b_base_pct` 參數
  - `_regime()` 改用可掃描的 bull/warn/bear 倍率（不再硬編碼 0.90/0.65/0.40）
  - `deploy_a` 改用 `a_cash_frac`（不再硬編碼 0.60）
  - B 類每筆 spend 改用 `b_base_pct`（不再硬編碼 0.25）

### 參數掃描 UI（tw_ui.py）
- 🔬 參數掃描 popup 新增第三個 tab：**配比掃描**
  - 顯示 14 組 Phase 1/2/3 結果，★ 標示 Calmar 最佳
  - 掃描順序：Regime → Crash → Allocation（約 10–12 分鐘）

---

## Session 15 — 進行中（2026-05-12）

### 市場回升警報
- **tw_screener.py**：`get_market_mode()` detail 新增 `twii_ma60` + `twii_vs_ma60_pct`
- **tw_discord.py**：新增 `build_recovery_alert_embed(alert_type, detail)`
  - `ma60_cross`：TWII 站回 MA60（早期訊號），建議試探性加碼 25–50%
  - `ma200_recovery`：mode 從 WARN/RISK 轉 NORMAL（確認訊號），建議分批加碼 50–100%
- **tw_scheduler.py**：新增 `_save_mode_and_check_recovery(mode, detail)`
  - 每日掃描後比對昨日 `cache/market_mode_history.json`（保留 60 天）
  - 偵測到回升即觸發 Discord 推播

### 組合回測 v2（評分自動分配）
- **tw_backtest_signals.py** 新增：
  - `_score_day(rsi, close, avwap, signal)` → 0–90分（RSI 25 + AVWAP距離 25 + 信號強度 40）
  - `_sell_lot_v2(lot, exec_sell)` → 含手續費+稅的賣出計算 helper
  - `_base_trade(sym, name, lot)` → 交易紀錄 helper
  - `run_portfolio_backtest_v2(symbols_cfg, annual_injection, sbt_cache, start_date, end_date)`
    - **A 類**（best_mode=B&H）：年初依評分比例分配年度注資，永不賣出；保留 40% 現金給 B 類
    - **B 類**（best_mode=信號策略）：各用自己最佳 mode 的進出場，倉位大小 = 10萬×0.25×score_mult×regime_mult
    - **TWII MA200 regime**：>+2%=0.90（牛）/ ±2%=0.65（警戒）/ <-2%=0.40（熊）
    - 自動讀取最新 `signal_backtest_*.json` 決定各股分類
- **tw_ui.py** `_sbt_portfolio_popup`：新增「★ v2 評分自動分配」綠色按鈕（保留 v1 按鈕對比）
  - v2 結果顯示：A類分類標示 + 持倉明細表 + 資產曲線（含 0050 B&H 對比橘虛線）

### 崩盤中個股買入策略調整
- **tw_screener.py** `calc_signals()`：新增 `drop_from_60d_high_pct`（距60日高點跌幅）
- **tw_screener.py** `run_scan()`：RISK 模式 BUY 信號加嚴篩選
  - RSI < 30 **且** 距高點跌逾 15% → 保留為「崩跌加碼 BUY」（附跌幅標注）
  - 否則 → 降級為 WATCH（原行為）
- **tw_discord.py** `build_buy_embed()`：RISK 模式下跌逾 15% 時顯示「💥 崩跌加碼機會」欄位
  - 說明跌幅、建議首批 30–50% 倉位、等 MA60 站回後補倉

---

## Session 14 — 完成事項（2026-05-06 ～ 2026-05-12）

### 持倉追蹤 tw_portfolio.py（新建）
- `add_trade / close_trade / delete_trade` CRUD，資料存 `portfolio_trades.json`
- `fetch_prices(symbols)` — yfinance 批次抓即時報價
- `calc_open_pnl / calc_closed_pnl` — 含手續費(0.1425%) + 證交稅(0.3%) 損益
- `target_price / stop_price` — 每筆持倉可設目標/停損價

### tw_ui.py — 持股 Tab 重構
- 原持股 Tab 拆成兩子 Tab：**📊 持股概覽**（原 config.yaml 不動）+ **📝 交易記錄**
- 交易記錄：+ 新增持倉 popup / ⟳ 刷新報價 / 未平倉 treeview / 已平倉 treeview
- 雙擊未平倉列→平倉 popup；右鍵→刪除記錄
- 停利/停損提醒：觸及 target_price → 金色高亮；觸及 stop_price → 橘紅高亮 + 狀態列提醒

### 掃描表格排序
- 點欄頭切換 升序 → 降序 → 重置（欄頭箭頭 ↑↓ 提示）
- `_sort_scan(col)` + `_sort_col` / `_sort_asc` state

### 匯出 CSV
- 掃描 tab、回測 tab、交易記錄各有「📥 CSV」按鈕
- UTF-8 BOM，Excel 直接開啟無亂碼

### K 線圖升級（`_chart_popup`）
- 週期切換：**日K / 週K / 月K** toggle 按鈕
  - 日K：60日 / 120日 / 180日 / 1年
  - 週K：6個月 / 1年 / 2年 / 3年（resample "W"）
  - 月K：1年 / 3年 / 5年 / 10年（resample "ME"）
- 基本面資訊列（背景載入）：P/E、EPS、殖利率 (Yield)、營收成長 (Rev.G)、ROE
- 信號標記（△▲▽）僅在日K 模式顯示
- MA 線使用全部歷史資料計算（避免短期視窗 MA 偏移）

### 策略一致性修正
- `_load_dca_rec()` 新函數：動態讀取 `dca_backtest_*.json`
- `tw_discord._dca_context_line()` 移除 RECOMMENDED_DCA 依賴
- 信號回測快取重跑：含 TRAIL + ALL_DYN 模式（`signal_backtest_2026-05-11.json`）
- DCA 回測 bug 修正：`_crash_performance(close)` → `_crash_performance(close_adj)`

### 字體 / 字型
- 全介面字體最小 12pt（位置緊的 11pt）；原 8/9/10pt 全面調升

### GitHub Actions
- `.github/workflows/tw_daily.yml`：UTC 01:00（台北09:00）+ 06:30（14:30）weekdays
- `.github/workflows/tw_weekly.yml`：UTC 02:00 Sunday（台北10:00）
- Secret：`DISCORD_WEBHOOK_URL` 在 repo Settings 設定

### 備份
- `backup/2026-05-12-s14-final/`：tw_ui.py / tw_portfolio.py / tw_discord.py / tw_backtest_dca.py / FUNCTION_SPEC.md / AI_HANDOFF.md

---

## Session 13 — 完成事項

### B：法人籌碼整合（完成）
- **tw_screener.py**：新增 `fetch_institutional_flow()` 函數
  - 從 TWSE T86 API（`/rwd/zh/fund/T86`）抓取三大法人每日買賣超
  - 欄位索引：row[4]=外陸資淨買超, row[10]=投信, row[11]=自營商, row[18]=三大合計
  - 單位：張（1張=1000股）；每日快取於 `_INST_CACHE`，避免重複請求
  - 若今日資料未出，自動回退至最近一個有資料的交易日（最多往回5天）
  - `run_scan()` 中一次抓取，附加至每個 entry：`inst_foreign / inst_trust / inst_dealer / inst_total`
- **tw_discord.py**：`build_buy_embed()` + `build_sell_embed()` 各加 "🏦 三大法人動向（昨日）" field
  - 格式：`外資 +X,XXX張  投信 +XXX張  自營 +XXX張  \n合計 **+X,XXX張**`
  - 無資料時靜默不顯示（inst_total is None）

### A1：信號準確度補評工具（tw_scheduler.py）
- 新增 `backfill_outcomes()` 函數：掃描所有 `cache/scan_*.json`，對距今 ≥ 5 交易日的日期自動補評
- 新增 `--backfill` 參數：`python tw_scheduler.py --backfill`
- 現有掃描只有 2026-04-29 / 04-30，需等 2026-05-07 才會有足夠交易日可評
- 準確度 UI tab 已有空資料提示（無需更改）

### A2：週報強化（tw_discord.py `build_weekly_embed()`）
- 追蹤每股本週第一/最後價格，計算漲跌幅
- 新增 "📊 本週漲跌幅排行" field：前3漲幅（🔺）+ 前3跌幅（🔻）
- 新增 "📋 信號股建議策略" field：對本週有 BUY/SELL 信號的前5股，附上 SBT 最佳策略建議
- 載入 `_load_sbt_cache()` + `_sbt_context_line()` 已在 tw_discord 中（Session 12 加入）

### A3：週線 RSI 確認（tw_screener.py + tw_discord.py）
- `calc_signals()` 新增週線 RSI 計算：daily close `resample("W").last()` 後跑 RSI(14)
- 結果存入 `latest["weekly_rsi"]`（None 若資料不足）
- 若週線 RSI > 65 且有 BUY/STRONG BUY 信號，追加 WATCH 提示「週線RSI偏高，日線為逆勢」
- `build_buy_embed()` / `build_sell_embed()` RSI 欄改為 "RSI（日/週）" 顯示兩個數值
- 0050.TW 測試：日線 RSI 83.8，週線 RSI 79.3，運作正常

---

## Session 12 — 完成事項

### Block A：Discord 推播加入信號回測策略建議（tw_discord.py）
- 新增 `_load_sbt_cache()`: 讀取最新 `cache/signal_backtest_*.json`，回傳 dict by symbol
- 新增 `_sbt_context_line(symbol, sbt_cache)`: 從 `best_mode` 產生一行策略摘要
  - 若 BNH 最佳 → "持有（B&H）優於信號操作，建議長期持有"
  - 否則 → "**{label}**  CAGR X%  MDD X%  Calmar X.XX"
- `build_buy_embed()` / `build_sell_embed()` 各加一個 "📋 信號回測建議策略" embed field
- `send_scan_results()` 呼叫 `_load_sbt_cache()` 並傳入 sbt_cache 給兩個 builder

### Block B：資產曲線進出場三角標記（tw_ui.py）
- `_bg()` 找出最佳 Calmar 策略的 rep_trades，傳入 `_draw()`
- `_draw()` 在 equity subplot 疊加 scatter 標記：
  - 進場 → 綠色 ▲（marker="^"）
  - SELL/PERIOD_END 出場 → 紅色 ▼
  - TRIM/TRAILING_STOP 出場 → 青色 ▼
- 日期→索引用 `pd.Timestamp(date, tz=_tz_mk)` + `idx.get_loc(ts, method="nearest")`

### Block C：策略卡片出場細分（tw_ui.py）
- 每張策略卡（`_sbt_show_result()` 內）的指標 row 後加一行出場細分：
  - 統計 TRIM / TRAILING_STOP / SELL / FALLBACK / PERIOD_END 各出場次數
  - 格式：`出場細分：止盈 X  ｜  追蹤止盈 X  ｜  SELL信號 X  ｜  ...`
  - 顏色 `#74a0c0`；零值欄位不顯示

### Block D：trade popup 逐年績效表格（tw_ui.py）
- `_sbt_trade_popup()` 原「逐年入→出」inline label 改為正式 Treeview 表格
- 欄位：年度 / 進場數 / 出場數 / 勝場 / 勝率% / 損益NT$ / 期末持倉
- 色碼：綠=獲利年 / 紅=虧損年 / 橙=僅持倉年 / 灰=無出場
- 視窗高度從 1280×560 → 1280×680，容納新表格

### 股災應對分析（tw_ui.py）
- `CRASH_PERIODS`：4 個台股重要股災事件（貿易戰/COVID/升息/日圓套息）
- `_crash_analysis(trades, cs, ce)`：計算策略在各股災期間的進場、追蹤止盈、SELL 出場次數
- 策略評析末加「📉 歷次股災應對」逐事件文字分析
- 建議文字自動反映股災行為；若從未逢低進場，提示放寬 RSI

### 其他 UI 改善
- 掃描 tab 加 `vs AVWAP %` 欄（負數=低於錨點，買入區域）
- Walk-Forward popup 加穩定性摘要（✅/🟡/⚠️ X/N 策略穩定）
- 回撤圖加股災期間色塊標注（CRASH_PERIODS）
- 策略卡片依 CAGR 降序排列（最優策略置頂）

---

## Session 11 — 全部完成事項

### Feature 1：追蹤止盈 Trailing Stop（tw_backtest_signals.py）
- 新增常數 `TRAIL_STOP_PCT = 0.15`（從進場後最高點回落15%觸發）
- MODES 從 5-tuple 擴展為 7-tuple（加 trail_en, dyn_scale_en）
- 每個持倉 lot 追蹤 `peak_price`；exit_signal = "TRAILING_STOP"
- 新增 MODE：`TRAIL`（混合+追蹤止盈）、`ALL_DYN`（混合+動態倉位）

### Feature 2：動態倉位大小 Dynamic Sizing（tw_backtest_signals.py）
- `size_factor = clamp(1 + rsi_gap + dd_factor, 0.5, 2.0)`
- RSI < 50 時 rsi_gap > 0（深度超賣加碼）；DD > 10% 時 dd_factor > 0

### Feature 3：Walk-Forward 驗證（tw_backtest_signals.py + tw_ui.py）
- `run_walk_forward(symbol, name, split_year)` 按中間點切分，跑 in/out sample
- UI：「🔬 Walk-Forward」按鈕跳出 popup，顯示各策略訓練/驗證期 CAGR 對比
- popup 底部有穩定性摘要（✅/🟡/⚠️ 幾/幾策略穩定）

### Feature 4：準確度回饋迴路 UI 修正
- 修正 `_render_accuracy_tab()` schema：`"date"` 非 `"signal_date"`，`"stock_results"` 非 `"records"`
- 新增「個股準確率排行」區塊，依命中率排序

### Feature 5：板塊相關性控制
- `tw_screener.py`：新增 `SECTOR` dict（半導體4檔/AI供應鏈2檔/高息ETF4檔）
- 掃描 tab 底部 `lbl_sector_warn`：同板塊 ≥2 檔 BUY 時顯示 ⚠️ 警告

### 股災應對分析（tw_ui.py 策略綜合評析）
- `CRASH_PERIODS`：定義中美貿易戰/COVID/升息熊市/日圓套息平倉 4 個事件
- `_crash_analysis(trades, cs, ce)`：計算每策略在各股災期間的進場/出場次數
- 策略綜合評析末尾加「📉 歷次股災應對」逐事件分析
- 建議文字自動融入股災行為（逢低進場次數/追蹤止盈效果/策略保守警告）
- 改善空間：若股災期間從未進場，提示放寬 RSI 門檻

### 自主 UI 改善（本 Session 末段）
1. **掃描 tab `vs AVWAP` 欄**：顯示現價 vs AVWAP 的距離 %（負數=低於錨點=買入區域）
2. **Walk-Forward 穩定性摘要**：popup 底部加一行 ✅/🟡/⚠️ 結論（X/N 策略穩定）
3. **資產曲線回撤圖股災標注**：在回撤子圖（下方 DD% 面板）標注 CRASH_PERIODS 名稱及紅底色
4. **策略卡片依 CAGR 排序**：跟單回測結果中，`modes_sorted` 依 CAGR 高至低顯示（最優策略在最上方）

---

### Feature 1：追蹤止盈 Trailing Stop（tw_backtest_signals.py）
- 新增常數 `TRAIL_STOP_PCT = 0.15`（從進場後最高點回落15%觸發）
- 新增 2 個 MODES：`TRAIL`（混合+追蹤止盈）、`TRAIL_MF`（追蹤止盈+MA200過濾）
- 每個持倉 lot 追蹤 `peak_price`；exit_signal = "TRAILING_STOP"

### Feature 2：動態倉位大小 Dynamic Sizing（tw_backtest_signals.py）
- 依 RSI 深度 + DD 深度計算 `size_factor`（0.5x–2.0x）
- 新增 MODE：`ALL_DYN`（混合策略+動態倉位）
- 公式：`size_factor = clamp(rsi_gap/rsi_buy + abs(dd_pct)/20, 0.5, 2.0)`

### Feature 3：Walk-Forward 驗證（tw_backtest_signals.py + tw_ui.py）
- 新增函數 `run_walk_forward(symbol, name, split_year, ...)`
  - 以 split_year 切分：in-sample (start→split) / out-of-sample (split→end)
  - 兩段都用相同參數跑，對比 CAGR/Calmar 是否退化
- tw_ui.py：在 _sbt_show_result() 加「🔬 Walk-Forward」按鈕，跳出比較 popup

### Feature 4：準確度回饋迴路（tw_scheduler.py + tw_ui.py）
- tw_scheduler.py：每次推播 BUY/STRONG BUY 後記錄到 `cache/signal_log.json`
  - 格式：`{symbol, signal_type, price, date}`
- 新增函數 `check_signal_outcomes()`：對每筆記錄查 5/10/20 日後報酬
- tw_ui.py 準確度 tab：顯示信號命中率統計表

### Feature 5：板塊相關性控制（tw_screener.py + tw_ui.py）
- tw_screener.py：新增 `SECTOR` dict（半導體、ETF、電源、消費...）
- tw_ui.py 掃描 tab：目前 BUY 訊號按板塊分組，標示同板塊過度集中（≥2支同板塊 BUY 時警告）

---

## 鐵則（AI 操作規範）

- **Edit / Read / Bash / Write 直接執行**，不詢問用戶，自行選擇最優解
- 只有高風險操作（刪除重要資料、force push、外部服務操作）才暫停詢問
- `bypassPermissions` 已寫入 `~/.claude/settings.json` 與 `.claude/settings.json`，**新 Session 起效**
- 每次 Session 開始前先更新此文件再動手改程式碼
- **策略一致性鐵則：掃描 tab「DCA策略」欄、Discord 推播建議策略、DCA tab 顯示策略，必須全部從最新回測快取動態讀取，禁止手動維護 `CATEGORY` rec 欄或 `RECOMMENDED_DCA`。**
  - 掃描 tab `rec`：從 `dca_backtest_*.json` 按 CAGR 最高選出（`_load_dca_rec()`）
  - Discord DCA 建議：同上，`_dca_context_line()` 直接從 cache 選最佳，不讀 `RECOMMENDED_DCA`
  - Discord 信號策略建議：從 `signal_backtest_*.json` 的 `best_mode`（Calmar最高）讀取（`_sbt_context_line()`）

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

## Session 8 完成事項（2026-04-30）

### ABCD 四項策略優化 — tw_backtest_signals.py + tw_ui.py

| # | 代號 | 說明 | 實作細節 |
|---|---|---|---|
| A | **年末強制部署** | 若全年未觸發 BUY/SBUY，最後交易日部署全部現金 | `year_last_days` + `deployed_this_year` 標記 |
| B | **TRIM 門檻 30%** | 從 15% 提升，避免牛市過早止盈 | `TRIM_PROFIT = 30.0`；MODES label 改「混合+止盈30%策略」 |
| C | **ETF 跳過 SELL** | ETF 長期持有，RSI 很難達到 SELL 閾值 | `ETF_SYMBOLS` 常量 + `is_etf = symbol in ETF_SYMBOLS` |
| D | **SBUY 全倉出擊** | 深度回調 = 最大配置機會，部署全部積累現金 | `spend = cash`（移除 LOT_SBUY 上限） |

### UI 更新（tw_ui.py）
- 交易明細彈窗加 FALLBACK 紫色列（`tag="fallback"`，顏色 `#a29bfe`）
- 圖例新增「🔵 年末強制部署 FALLBACK」
- INFO bar 中 TRIM% 已自動從 `params["trim_pct"]` 讀取，顯示 30%（不需另改）

---

## Session 9 完成事項（2026-05-04）

### 手續費+交易稅 + MDD
- `COMMISSION_RATE = 0.001425`（買賣），`TAX_RATE = 0.003`（僅賣出）
- 每筆交易附帶 `fees` 欄位；B&H 也同樣扣費以確保比較公平
- `_simulate()` 回傳 `(trades, total_injected, max_dd_pct)`
- MDD：逐日追蹤 `cash + open_lots 市值`，對比歷史峰值
- UI：比較表加 MDD%/手續費NT$ 欄；卡片加 MDD+費用指標；彈窗加費用欄位

### 股票清單調整
- config.yaml：加入統一超 2912.TW；2408/3037 加 `backtest_only: true`
- tw_screener.py：`run_scan()` 過濾 `backtest_only`，推播 12 檔（移除 2408/3037）
- tw_screener.py：加入 2912.TW 的 SIGNAL_CONFIG（防禦型，rsi_buy=42）

### 回測年份可選
- `run_signal_backtest(start_date, end_date)` 完全參數化
- 動態 `max_inject_yrs = end_year - start_year`（準確解決 off-by-one）
- UI 工具列加年份選單（2010–2025），cache key 帶日期範圍
- 切換年份自動清除結果、重設按鈕顏色
- `[研]` 標示僅回測（backtest_only）的股票

### 交易明細彈窗 — 逐年損益列
- 彈窗底部加逐年損益橫排（出場年份 × PnL × 筆數）
- 綠/紅色分別標示盈虧年

---

## Session 10 完成事項（2026-05-04）

### 跟單回測資產曲線圖（tw_ui.py）
- `_sbt_chart_popup()`: 點擊「📈 資產曲線」按鈕，重建 B&H + 四種信號策略的逐日資產曲線
- `_sbt_equity_series()`: 從 trades 清單 + 價格序列還原日頻組合淨值，完全匹配 `_simulate()` 的年度注資邏輯
  - 每年第一交易日注入 `annual_budget`，上限 `max_inject_yrs = end_yr - start_yr`
  - PERIOD_END 倉位在計算結束前持續貢獻市值
- B&H 以藍色虛線繪製，信號策略以實線，方便目視比較
- 下方面板顯示個股 60日回撤（含 -10%/-20% 警戒線），與 DCA 圖格式一致
- 按鈕位置：跟單回測結果的標題列右側（與 DCA Tab 的📉按鈕對稱）

### 補漏
- CATEGORY 補上 2912.TW（"防禦消費", "B&H"），修正掃描 Tab 類別欄空白

---

## Session 8 完成事項（2026-04-30）

### ABCD 四項策略優化 — tw_backtest_signals.py + tw_ui.py

| # | 代號 | 說明 | 實作細節 |
|---|---|---|---|
| A | **年末強制部署** | 若全年未觸發 BUY/SBUY，最後交易日部署全部現金 | `year_last_days` + `deployed_this_year` 標記 |
| B | **TRIM 門檻 30%** | 從 15% 提升，避免牛市過早止盈 | `TRIM_PROFIT = 30.0`；MODES label 改「混合+止盈30%策略」 |
| C | **ETF 跳過 SELL** | ETF 長期持有，RSI 很難達到 SELL 閾值 | `ETF_SYMBOLS` 常量 + `is_etf = symbol in ETF_SYMBOLS` |
| D | **SBUY 全倉出擊** | 深度回調 = 最大配置機會，部署全部積累現金 | `spend = cash`（移除 LOT_SBUY 上限） |

---

## Session 8 待辦（已完成）

- [x] 信號回測核心（tw_backtest_signals.py）
- [x] UI 加入跟單回測 Tab（tw_ui.py）
- [x] tw_scheduler.py 加 `--signal-bt` 旗標
- [x] ABCD 策略優化
- [x] 手續費+MDD
- [x] 回測年份可選
- [x] 統一超加入 + 2408/3037 backtest_only

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
