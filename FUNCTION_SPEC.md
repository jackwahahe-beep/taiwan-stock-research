# FUNCTION_SPEC — 台股研究

> 版本：v3.0　最後更新：2026-05-11（Session 14）
> GitHub：https://github.com/jackwahahe-beep/taiwan-stock-research

---

## 系統能力總覽

| 能力 | 現況 |
|------|------|
| 追蹤台股清單（ETF + 個股） | ✅ 15 檔，config.yaml 可自由增減 |
| v2 信號掃描（AVWAP + DD + 市場模式 + 個股RSI） | ✅ tw_screener.py v2 |
| 三大法人籌碼整合（外資/投信/自營） | ✅ TWSE T86 API，每日快取 |
| 週線 RSI 確認（逆勢 BUY 警示） | ✅ daily close resample("W").last() |
| Discord 即時推播（買入/賣出分色 embed） | ✅ 含建議股數、DCA脈絡、法人動向 |
| 賣出信號 edge-trigger（新 vs 持續中） | ✅ 比對昨日 scan cache |
| 信號事後驗證（5日後對答案，累積正確率） | ✅ tw_outcome.py，每次掃描後自動跑 |
| DCA 長期回測（10年，4策略對比） | ✅ tw_backtest_dca.py，每週日自動跑 |
| 信號跟單回測（10年，10+策略） | ✅ tw_backtest_signals.py；跟單回測 Tab |
| 追蹤止盈 Trailing Stop（15%回落觸發） | ✅ TRAIL / TRAIL_MF 模式 |
| 動態倉位大小（RSI深度 + DD深度） | ✅ size_factor 0.5x–2.0x |
| Walk-Forward 驗證（訓練期 vs 驗證期） | ✅ run_walk_forward()；UI 彈窗比較 |
| Sharpe Ratio（信號回測 & 組合回測） | ✅ _sharpe(eq_series) 年化 |
| 0050 B&H 比較基準（組合回測） | ✅ _portfolio_bnh_0050() |
| 組合回測（ETF/科技股分倉 DCA 年注資） | ✅ run_portfolio_backtest()；UI 彈窗 |
| 個股 K 線圖 popup（日K + 成交量 + MA + 信號） | ✅ _chart_popup()；雙擊掃描/回測列 |
| 持倉追蹤（新增/平倉/刪除 + 即時損益） | ✅ tw_portfolio.py + 交易記錄 Tab |
| 停利/停損提醒（刷新報價後高亮） | ✅ target_price / stop_price 欄位 |
| 掃描表格欄位排序（點欄頭升/降/取消） | ✅ _sort_scan() |
| 匯出 CSV（掃描 / 跟單回測 / 持倉） | ✅ _export_*_csv() |
| 自動每日排程（盤前 09:00 / 盤後 14:30 台北） | ✅ GitHub Actions tw_daily.yml |
| 週報摘要（週日 10:00 台北自動推播） | ✅ GitHub Actions tw_weekly.yml |
| GitHub Actions 失敗通知 | ✅ if: failure() → Discord |
| 台灣假日過濾 | ✅ is_trading_day()，holidays.TW() |
| 看盤 UI（5 個 Tab，暗色主題） | ✅ tw_ui.py，customtkinter |
| 板塊相關性警告（同板塊 ≥2 BUY） | ✅ SECTOR dict + lbl_sector_warn |
| 股災應對分析（4 個歷史股災期間） | ✅ CRASH_PERIODS + _crash_analysis() |
| 個股基本面資料 | ❌ 未實作（yfinance info 有限） |

---

## 模組架構

```
tw_screener.py   ←→   tw_backtest_signals.py   ←→   tw_backtest_dca.py
      ↓                      ↓                              ↓
tw_discord.py  ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
      ↑
tw_scheduler.py（入口點）
      ↑                ↑                  ↑
config.yaml + .env   tw_portfolio.py   tw_outcome.py

tw_ui.py（獨立桌面 UI）
  → tw_screener / tw_portfolio / tw_backtest_signals / tw_backtest_dca / tw_outcome
```

---

## 策略核心邏輯（v2）

### 信號參數（SIGNAL_CONFIG，tw_screener.py）

| 參數 | 說明 |
|------|------|
| `rsi_buy` | BUY 觸發 RSI 上限（DD ≤ -10%）|
| `rsi_sbuy` | STRONG BUY 觸發 RSI 上限（DD ≤ -20%）|
| `rsi_sell` | SELL 觸發 RSI 下限 |
| `b1` | BUY 觸發 AVWAP 乘數（price < AVWAP × b1）|
| `b2` | STRONG BUY 觸發 AVWAP 乘數（price < AVWAP × b2）|
| `s` | SELL 觸發 AVWAP 乘數（price ≥ AVWAP × s）|
| `bnh_dca` | True → DCA 回測強制用 B&H 策略（超強趨勢股）|

### 買入條件

| 信號等級 | DD 條件 | 價格條件 | RSI 條件 |
|----------|---------|---------|---------|
| STRONG BUY | DD ≤ -20% | price < AVWAP × b2 | RSI ≤ rsi_sbuy |
| BUY | DD ≤ -10% | price < AVWAP × b1 | RSI ≤ rsi_buy |
| 輔助 BUY | — | MA20 黃金交叉 MA60 | — |

### 賣出條件

RSI ≥ rsi_sell **且** price ≥ AVWAP × s **且** price > MA20 × 1.15（或 MA20 死亡交叉 MA60）

### 市場模式過濾

| 模式 | 觸發條件 | 推播行為 |
|------|---------|---------|
| NORMAL | ^TWII > MA200 × 1.02 | 正常推 BUY / STRONG BUY |
| WARN | ^TWII 在 MA200 ±2% 或波動率 > 30% 且偏弱 | BUY 仍推但加警示 |
| RISK | ^TWII < MA200 - 5% 或跌破且波動 > 25% | BUY 降級 WATCH，只推 STRONG BUY |

---

## tw_screener.py — 資料拉取與信號計算

### 主要函數

| 函數 | 說明 |
|------|------|
| `fetch_data(symbol, period)` | yfinance 日線 OHLCV，index 轉 Asia/Taipei |
| `calc_rsi(series, period=14)` | Wilder's RSI |
| `calc_avwap(df, lookback=60)` | Anchored VWAP，最近60天低點為錨 |
| `calc_drawdown(close, lookback=60)` | 從近60天高點計算跌幅（負值）|
| `get_market_mode()` | 回傳 (mode, detail_dict)，當日快取 |
| `calc_signals(df, cfg, symbol)` | 計算單一股票所有 v2 信號 + 週線RSI |
| `fetch_institutional_flow()` | TWSE T86 API，三大法人每日買賣超（張）|
| `run_scan()` | 批次掃描，快取至 cache/scan_YYYY-MM-DD.json |

---

## tw_backtest_signals.py — 信號跟單回測

### 常數

| 常數 | 值 | 說明 |
|------|----|------|
| `TRAIL_STOP_PCT` | 0.15 | 追蹤止盈：從峰值回落 15% 觸發 |
| `TRIM_PROFIT` | 30.0 | 止盈門檻（%）|
| `RISK_FREE` | 0.015 | Sharpe 計算用無風險利率（年化）|
| `ETF_SYMBOLS` | set | ETF 免 SELL 信號出場 |

### 策略模式（MODES）

| 標籤 | 說明 |
|------|------|
| BNH | B&H（基準）|
| BUY_ONLY | 只按 BUY 進場 |
| SBUY_ONLY | 只按 STRONG BUY 進場 |
| MIXED | BUY + STRONG BUY 混合 |
| TRAIL | 混合 + 追蹤止盈 |
| ALL_DYN | 混合 + 動態倉位 |

### 主要函數

| 函數 | 說明 |
|------|------|
| `_simulate(symbol, cfg, mode, annual_injection, start_date, end_date)` | 單策略回測引擎，回傳 (trades, injected, mdd, equity_series) |
| `_sharpe(eq, rf_annual)` | 年化 Sharpe Ratio |
| `_stats(trades, injected, mdd, sharpe)` | 彙整指標 dict |
| `run_signal_backtest(symbol, name, start_date, end_date, annual_injection)` | 單股全策略回測 |
| `run_walk_forward(symbol, name, split_year, ...)` | Walk-Forward 驗證，切分訓練/驗證期 |
| `run_portfolio_backtest(symbols_cfg, annual_injection, lot_pct_etf, lot_pct_tech, ...)` | 多股組合回測，ETF/科技股分倉 |
| `_portfolio_bnh_0050(annual_injection, start_date, end_date)` | 0050 B&H 基準模擬 |

---

## tw_portfolio.py — 持倉追蹤（v2，交易記錄）

> 資料存於 `portfolio_trades.json`（與 config.yaml 分開）

### 常數

| 常數 | 值 |
|------|----|
| `COMMISSION` | 0.001425（買/賣各收）|
| `TAX` | 0.003（賣出時收）|

### 函數

| 函數 | 說明 |
|------|------|
| `load_trades() / save_trades(trades)` | 讀寫 JSON |
| `add_trade(symbol, name, buy_date, buy_price, shares, note, target_price, stop_price)` | 新增持倉，自動計算買入手續費 |
| `close_trade(trade_id, sell_date, sell_price)` | 平倉，計算賣出手續費 + 證交稅 |
| `delete_trade(trade_id)` | 刪除記錄 |
| `get_open(trades) / get_closed(trades)` | 篩選未平倉 / 已平倉 |
| `fetch_prices(symbols)` | yfinance 批次抓最新收盤價 |
| `calc_open_pnl(trade, current_price)` | 未平倉損益（含估算賣出費用）|
| `calc_closed_pnl(trade)` | 已平倉損益 |

### trade 欄位

```json
{
  "id": 1, "symbol": "2330", "name": "台積電",
  "buy_date": "2024-01-15", "buy_price": 750.0, "shares": 1000,
  "buy_commission": 1068,
  "target_price": 900.0, "stop_price": 680.0,
  "sell_date": null, "sell_price": null,
  "sell_commission": null, "sell_tax": null,
  "note": "", "status": "open"
}
```

---

## tw_ui.py — 桌面看盤 UI

> `python tw_ui.py` 啟動，customtkinter 暗色主題（BG #1a1a2e）。

### Tab 結構（5 個）

| Tab | 功能 |
|-----|------|
| 📡 掃描 | 即時信號表 + 欄位排序 + CSV匯出 + K線圖（雙擊）|
| 📈 回測 | DCA 長期回測（摘要比較表 + 策略卡片 + 明細彈窗 + 資產曲線圖）|
| 💼 持股 | 子Tab①持股概覽（config.yaml）+ 子Tab②交易記錄（tw_portfolio）|
| 📊 準確度 | 信號事後驗證命中率統計 |
| 📋 跟單回測 | 信號跟單回測（10年/多策略/Walk-Forward + 組合回測）|

### 掃描 Tab 欄位

代號 / 名稱 / 類別 / DCA策略 / 信號 / 現價 / RSI / AVWAP / vs AVWAP% / 試買(b1) / 加碼(b2) / 賣出參考 / 回撤 / 持股損益

- **欄位排序**：點欄頭升序→降序→取消（`_sort_scan`）
- **雙擊列**：開啟個股 K 線圖 popup（120日日K + MA20/60 + 成交量 + 信號標記）
- **CSV匯出**：「📥 CSV」按鈕匯出當前表格

### 持股 Tab — 交易記錄子Tab

- **未平倉表格**：代號/名稱/買入日/買入價/股數/成本含費/現價/市值/損益/損益%/備註
  - 達停利目標：金黃色高亮
  - 觸停損：橙紅色高亮
  - 雙擊→平倉 popup；右鍵→平倉/刪除
- **已平倉表格**：代號/買入日/賣出日/買入價/賣出價/損益/損益%
- **+ 新增持倉 popup**：含停利價/停損價選填欄位
- **⟳ 刷新報價**：背景抓即時價，更新損益並檢查停利/停損

### 跟單回測 Tab 功能

- 股票列表（左側）按鈕 → 觸發 `_on_sbt_select` 跑回測
- 結果：策略卡片（CAGR/MDD/Sharpe/手續費/vs B&H）+ 出場細分
- 📈 資產曲線：equity series 疊圖 + 進出場三角標記 + 股災色塊
- 🔬 Walk-Forward：訓練/驗證期 CAGR 對比彈窗
- 📊 組合回測：多股組合 + ETF/科技股分倉 + 0050 B&H 基準曲線（橘虛線）
- 📥 CSV：匯出所有策略摘要
- 跟單回測表欄位：策略/交易次/勝率/總注資/損益/報酬%/CAGR%/MDD%/Sharpe/手續費/vs B&H

### K 線圖 popup（_chart_popup）

- 觸發：雙擊掃描列 / 跟單回測「📊 K線圖」按鈕
- 內容：日K candlestick（matplotlib patches）+ MA20/MA60 + 成交量 + 信號標記（△▲▽）
- 天數選擇按鈕：60 / 120 / 250 日

---

## tw_discord.py — Discord 推播

### 主要函數

| 函數 | 說明 |
|------|------|
| `send_webhook(payload, url)` | 單次 HTTP POST，timeout 10s |
| `send_scan_results(results)` | 三分流推播（BUY embed / SELL embed / 日摘要）|
| `build_market_mode_embed(results)` | 每日第一則：市場模式 + edge-trigger 賣出分類 |
| `build_buy_embed(stock, cfg, dca_cache, sbt_cache)` | 含法人動向 + 策略建議 |
| `build_sell_embed(stock, cfg, dca_cache, sbt_cache, in_portfolio)` | 含法人動向 |
| `build_outcome_embed(outcome)` | 信號事後驗證結果 |
| `build_weekly_embed(cache_dir, days=7)` | 週報：模式分布 + 信號次數 + 漲跌排行 + 正確率 |

---

## tw_scheduler.py — 排程執行器

### 執行模式

| 指令 | 行為 |
|------|------|
| `python tw_scheduler.py` | 掃描 + 持股追蹤 + 事後驗證 |
| `python tw_scheduler.py --dca` | 10年 DCA 回測並推播 |
| `python tw_scheduler.py --weekly` | 手動週報 |
| `python tw_scheduler.py --outcome` | 手動查近 30 日信號正確率 |
| `python tw_scheduler.py --backfill` | 補評歷史信號 |
| `python tw_scheduler.py --daemon` | 常駐排程（08:45 / 14:00 台北 + 週五 17:00）|

---

## GitHub Actions 自動化排程

### `.github/workflows/tw_daily.yml`

- **觸發**：UTC 01:00 Mon–Fri（台北 09:00）/ UTC 06:30（台北 14:30）+ 手動
- **行為**：`python tw_scheduler.py`（掃描 + 持股 + 事後驗證）
- **快取**：`actions/cache/restore@v4` + `actions/cache/save@v4`，路徑 `cache/`
- **失敗通知**：`if: failure()` → Discord 推播錯誤訊息

### `.github/workflows/tw_weekly.yml`

- **觸發**：UTC 02:00 Sunday（台北 10:00）+ 手動
- **行為**：`python tw_scheduler.py --dca` → `python tw_scheduler.py --weekly`
- **失敗通知**：同上

### 環境變數

- `DISCORD_WEBHOOK_URL`：GitHub Secrets 設定
- `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true`：避免 Node.js 20 棄用警告

---

## 快取檔案格式

| 檔案 | 說明 |
|------|------|
| `cache/scan_YYYY-MM-DD.json` | 每日掃描結果（含法人籌碼）|
| `cache/dca_backtest_YYYY-MM-DD.json` | DCA 長期回測結果 |
| `cache/signal_backtest_YYYY-MM-DD.json` | 信號跟單回測結果（所有股票）|
| `cache/backtest_YYYY-MM-DD.json` | 組合回測結果 |
| `cache/outcomes/outcome_YYYY-MM-DD.json` | 信號事後驗證記錄 |
| `portfolio_trades.json` | 持倉追蹤交易記錄（根目錄）|

---

## 已知限制

| 項目 | 說明 |
|------|------|
| 個股基本面 | yfinance info 有限，PE/EPS/殖利率不穩定 |
| 盤中即時報價 | yfinance 為日線收盤，非 tick |
| 週K/月K 切換 | K 線圖目前只有日K |
