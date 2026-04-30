# FUNCTION_SPEC — 台股研究

> 版本：v2.2　最後更新：2026-04-30
> GitHub：https://github.com/jackwahahe-beep/taiwan-stock-research

---

## 系統能力總覽

| 能力 | 現況 |
|------|------|
| 追蹤台股清單（ETF + 個股） | ✅ 13 檔，config.yaml 可自由增減 |
| v2 信號掃描（AVWAP + DD + 市場模式 + 個股RSI） | ✅ tw_screener.py v2 |
| Discord 即時推播（買入/賣出分色 embed） | ✅ 含建議股數、DCA 10年脈絡、進出場價格 |
| 賣出信號 edge-trigger（新 vs 持續中） | ✅ 比對昨日 scan cache |
| 持股追蹤（P&L + 賣出建議 + 反彈偵測） | ✅ tw_portfolio.py |
| DCA 長期回測（10年，4策略對比） | ✅ tw_backtest_dca.py，每週日自動跑 |
| DCA 年末強制投入（year-end fallback） | ✅ 全年未觸發時最後交易日強制部署 |
| DCA 觸發條件記錄（DD%/RSI/vs_b1%） | ✅ 每筆交易附觸發條件快照 |
| 信號事後驗證（5日後對答案，累積正確率） | ✅ tw_outcome.py，每次掃描後自動跑 |
| 自動每日排程（盤前 07:30 / 盤後 12:00 台北） | ✅ GitHub Actions tw_daily.yml（UTC 23:30/04:00） |
| 週報摘要（週日 10:00 台北自動推播） | ✅ GitHub Actions tw_weekly.yml |
| 看盤 UI（掃描/持股/DCA三個Tab） | ✅ tw_ui.py，customtkinter 暗色主題 |
| DCA 策略摘要比較表 | ✅ tw_ui.py，CAGR/總報酬/獲利/終值/MDD 對比網格 |
| DCA 交易明細彈窗（持有報酬%/fallback標記） | ✅ tw_ui.py `_dca_popup` |
| DCA 策略說明彈窗 | ✅ tw_ui.py `_strategy_info_popup` |
| GitHub Actions 雲端自動執行 | ✅ 不需本機長時間開機 |
| 台灣假日過濾 | ✅ is_trading_day()，holidays.TW() |
| 敏感資訊保護 | ✅ Webhook URL 存於 .env / GitHub Secret |
| **DCA 資產曲線圖** | 🔲 Session 7 待實作 |
| **信號準確度儀表板（UI Tab）** | 🔲 Session 7 待實作 |
| **持股即時 P&L 追蹤（UI）** | 🔲 Session 7 待實作 |
| **參數敏感度分析（DCA Tab）** | 🔲 Session 7 待實作 |
| **GitHub Actions 失敗通知** | 🔲 Session 7 待實作 |
| 個股基本面資料 | ❌ 未實作（需 FinMind API）|

---

## 模組架構

```
tw_screener.py   ←→   tw_backtest_dca.py
      ↓                      ↓
tw_discord.py  ←←←←←←←←←←←←
      ↑
tw_scheduler.py（入口點）
      ↑                ↑             ↑
config.yaml + .env   tw_portfolio.py  tw_outcome.py

tw_ui.py（獨立桌面 UI，直接呼叫 tw_screener / tw_portfolio / tw_backtest_dca / tw_outcome）
```

---

## 策略核心邏輯（v2）

### 信號參數（SIGNAL_CONFIG，tw_screener.py）

每檔股票獨立設定，鍵值說明：

| 參數 | 說明 |
|------|------|
| `rsi_buy` | BUY 觸發 RSI 上限（DD ≤ -10%）|
| `rsi_sbuy` | STRONG BUY 觸發 RSI 上限（DD ≤ -20%）|
| `rsi_sell` | SELL 觸發 RSI 下限 |
| `b1` | BUY 觸發 AVWAP 乘數（price < AVWAP × b1）|
| `b2` | STRONG BUY 觸發 AVWAP 乘數（price < AVWAP × b2）|
| `s` | SELL 觸發 AVWAP 乘數（price ≥ AVWAP × s）|
| `bnh_dca` | True → DCA 回測強制用 B&H 策略（超強趨勢股）|

### 買入條件（三條同時成立）

| 信號等級 | DD 條件 | 價格條件 | RSI 條件 |
|----------|---------|---------|---------|
| STRONG BUY | DD ≤ -20% | price < AVWAP × b2 | RSI ≤ rsi_sbuy |
| BUY | DD ≤ -10% | price < AVWAP × b1 | RSI ≤ rsi_buy |
| 輔助 BUY | — | MA20 黃金交叉 MA60 | — |

### 賣出條件（三條同時成立）

RSI ≥ rsi_sell **且** price ≥ AVWAP × s **且** price > MA20 × 1.15

或：MA20 死亡交叉 MA60

### 市場模式過濾（get_market_mode）

| 模式 | 觸發條件 | 推播行為 |
|------|---------|---------|
| NORMAL | ^TWII > MA200 × 1.02 | 正常推 BUY / STRONG BUY |
| WARN | ^TWII 在 MA200 ±2% 或波動率 > 30% 且偏弱 | BUY 仍推但 embed 加警示 |
| RISK | ^TWII < MA200 - 5%，或跌破且波動 > 25% | BUY 降級為 WATCH，只推 STRONG BUY |

市場模式結果當日快取（`_market_mode_cache`），一天只抓一次 ^TWII + 0050.TW。

---

## tw_screener.py — 資料拉取與信號計算

### `fetch_data(symbol, period="6mo") → pd.DataFrame`
從 yfinance 拉取日線 OHLCV 資料。index 轉為 `Asia/Taipei` timezone。

### `calc_rsi(series, period=14) → pd.Series`
Wilder's RSI，rolling EMA 近似。前 `period` 筆為 NaN。

### `calc_avwap(df, lookback=60) → float`
Anchored VWAP：取近 lookback 天的最低收盤點為錨點，從錨點到現在計算成交量加權均價。
- 若當前價格 < 錨點 × 1.05（仍在下跌中），改以窗口起點為錨點（保守計算）

### `calc_drawdown(close, lookback=60) → float`
從近 lookback 天最高點計算當前跌幅（負值，例如 -0.15 代表 -15%）。

### `get_market_mode() → tuple[str, dict]`
回傳 `(mode, detail_dict)`。detail 含：twii_price / twii_ma200 / twii_vs_ma200_pct / vol_20_annualized / mode。

### `calc_signals(df, cfg, symbol="") → dict`
計算單一股票所有 v2 信號。

**回傳格式：**
```python
{
  "price": float,       # 最新收盤
  "rsi": float,         # RSI(14)
  "ma_fast": float,     # MA20
  "ma_slow": float,     # MA60
  "avwap": float,       # Anchored VWAP
  "dd_pct": float,      # 回撤幅度（%，負值）
  "volume": int,
  "vol_ma20": int,
  "market_mode": str,   # 由 run_scan 注入
  "signals": [
    {"type": "STRONG BUY"|"BUY"|"SELL"|"WATCH", "reason": str}
  ]
}
```

**輸入保護：** len(df) < 60 → 回傳 {}；price 或 rsi 為 NaN → 回傳 {}。

### `run_scan() → list[dict]`
批次掃描 config.yaml 所有股票，市場模式過濾後快取至 `cache/scan_YYYY-MM-DD.json`。

---

## tw_backtest_dca.py — DCA 長期回測（v2）

> 每週日自動跑（GitHub Actions tw_weekly.yml），也可手動 `--dca`。

### 常數

| 常數 | 值 | 說明 |
|------|-----|------|
| `START_YEAR` | 2015 | 回測起始年份 |
| `END_YEAR` | 2025 | 回測結束年份 |
| `ANNUAL_BUDGET` | 100,000 | 每年投入金額（NTD）|

### 四種策略對比

| 策略標籤 | 說明 |
|----------|------|
| `B&H DCA（無條件）` | 每年第一個交易日買入（基準策略） |
| `v2 BUY DCA` | 只在 BUY 信號時投入；全年未觸發則年末強制買入 |
| `v2 STRONG BUY DCA` | 只在 STRONG BUY 信號時投入；全年未觸發則年末強制買入 |
| `市場警戒逆向加碼` | WARN/RISK 市場模式時加碼；全年未觸發則年末強制買入 |

### `_fetch_dca_data(symbol) → pd.DataFrame`
用明確日期範圍抓取 10 年資料：`ticker.history(start="2015-01-01", end="2025-12-31", auto_adjust=True)`。
避免 yfinance `period="10y"` 對台股 .TW 後綴不穩定的問題。

### `_run_dca(close, label, allow_buy, indicator_series) → dict`
單一策略回測引擎。

**year-end fallback 邏輯：**
- `last_trading_days[yr]`：每年最後一個交易日
- `is_year_end = allow_buy is not None and dt == last_trading_days[yr] and cash_reserve > 0`
- 若 `can_buy or is_year_end`：執行買入；`is_fallback = is_year_end and not can_buy`
- fallback 交易不記錄觸發條件（`indicator_series` 不寫入）

**回傳格式：**
```python
{
  "label": str,
  "total_invested": float,
  "final_value": float,
  "profit": float,              # final_value - total_invested
  "total_return_pct": float,
  "cagr_pct": float,
  "max_drawdown_pct": float,
  "n_transactions": int,
  "final_price": float,         # 最後一日收盤價，供 UI 計算持有報酬%
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "price": float,
      "shares": int,
      "cost": float,
      "fallback": bool,         # True = 年末強制投入
      "trigger": {              # 只有 signal-triggered 才有此欄位
        "DD%": float,
        "RSI": float,
        "vs_b1%": float         # 或 vs_b2%（STRONG BUY），或 市場模式
      }
    }
  ]
}
```

### `run_dca_backtest(symbol, name, cfg, twii_close, etf50_close) → dict`
單一股票的四策略對比。傳入 indicator_series 給三個擇時策略。

### `run_dca_all() → list[dict]`
執行所有股票 10 年 DCA 回測，快取至 `cache/dca_backtest_YYYY-MM-DD.json`。

### `build_dca_embed(result) → dict`
格式化 DCA 回測結果為 Discord embed，含各策略總報酬 / MDD / CAGR 比較。

### `load_dca_cache() → dict`
讀取最新 DCA 快取，回傳 `{symbol: result}` dict。

---

## tw_outcome.py — 信號事後驗證

> 每次掃描後自動執行，追蹤信號 5 個交易日後的實際漲跌，長期累積正確率。

### 常數

| 常數 | 值 | 說明 |
|------|----|------|
| `LOOK_AHEAD` | 5 | 信號發出後幾個交易日評分 |
| `BUY_THR` | +0.5% | BUY 信號視為「正確」的最低漲幅 |
| `SELL_THR` | -0.5% | SELL 信號視為「正確」的最低跌幅 |

### `grade_date(signal_date, look_ahead=5) → dict | None`
讀取 `cache/scan_{signal_date}.json`，計算每檔最強信號在 look_ahead 個交易日後的報酬，寫入 `cache/outcomes/outcome_{signal_date}.json`。

### `load_recent_outcomes(n=30) → list[dict]`
讀取最近 n 筆 outcome 記錄。

### `compute_rolling_accuracy(n=30) → dict`
統計近 n 日各信號類型（STRONG BUY / BUY / SELL）的正確次數、總次數、正確率、平均報酬。

---

## tw_portfolio.py — 持股追蹤

### `fetch_latest_price(symbol) → float | None`
從 `fetch_data` 取最新收盤價。

### `calc_holding(holding, price) → dict`
計算持股即時 P&L。cost = 0（配股）時 pnl_pct 回傳 None。

### `_detect_bounce(symbol, cfg) → (bool, list[str])`
偵測「待機賣出」股票的反彈機會（私有）。條件之一觸發即推播：
1. RSI 由個股 `rsi_sbuy` 超賣閾值反彈超過 +10（e.g. 35→45）
2. MA5 黃金交叉 MA20
3. 近 5 日連漲
4. 價格從 AVWAP×0.97 以下回升至 AVWAP×0.97（折價→均價回升確認）

### `get_sell_advice(holding_result, cfg) → dict`

| action | push | 條件 |
|--------|------|------|
| `SELL_STRONG` | ✅ | SELL信號 + 獲利 > 5% |
| `EXIT_BOUNCE` | ✅ | 待機賣出 + 反彈偵測觸發 |
| `SELL_WATCH` | ✅ | SELL信號 + 虧損 > 10% |
| `SELL_MONITOR` | ❌ | SELL信號，損益 -10%~+5% |
| `EXIT_WAIT` | ❌ | 待機賣出，無反彈信號 |
| `HOLD` | ❌ | 無賣出信號 |
| `WATCH` | ❌ | 僅爆量信號 |

### `run_portfolio_check() → list[dict]`
批次處理所有持股，附加 `advice` 欄位。

### `build_portfolio_embeds(results) → list[dict]`
永遠輸出一張「持股總覽」embed；另對 `push=True` 的個股輸出操作建議 embed。

---

## tw_discord.py — Discord 推播

### `send_webhook(payload, webhook_url) → bool`
單次 HTTP POST，timeout 10s。

### `send_scan_results(results) → None`
三分流推播：
1. 非持股 + BUY/STRONG BUY → `build_buy_embed`（含建議股數 + DCA脈絡）
2. BUY/STRONG BUY 持股或 SELL 信號 → `build_sell_embed`
3. 都無 → 每日摘要（市場溫度 + 中性觀察清單）

### `build_market_mode_embed(results) → dict`
每日第一則推播：市場模式 header + 近信號預警 + **edge-triggered 賣出分類**。
- `新賣出`（粗體）：昨日 scan 未出現 SELL、今日首次
- `賣出（持續中）`：昨日已有 SELL、今日仍在

### `build_buy_embed(stock, cfg, dca_cache=None) → dict`
買入 embed 欄位：
- 現價 / RSI / DD / AVWAP 距離
- 📌 建議進場：掛單價 / 買入股數 / 預估成本
- ⭐ DCA 10年脈絡：推薦策略 CAGR / MDD（from dca_cache）
- ⚠️ 市場警戒標示（WARN/RISK 模式時）

### `build_sell_embed(stock, cfg, dca_cache=None, in_portfolio=False) → dict`
賣出 embed 欄位：
- 標題：「賣出（持股）」或「賣出（觀察）」
- 現價 / RSI / AVWAP×s 目標價 / DD
- ⭐ DCA 10年脈絡

### `build_outcome_embed(outcome) → dict | None`
事後驗證結果 embed：列出 signal_date 當天各股信號的 5 日後實際報酬，✅/❌ 標示。

### `build_weekly_embed(cache_dir, days=7) → dict | None`
讀取過去 7 天 scan cache，統計：
- 市場模式分布（NORMAL/WARN/RISK 各幾天）
- 各股信號次數（STRONG BUY×N / BUY×N / SELL×N）
- 近 30 日信號正確率（from `compute_rolling_accuracy`）

---

## tw_scheduler.py — 排程執行器

### 執行模式

| 指令 | 行為 |
|------|------|
| `python tw_scheduler.py` | 立即執行一次：掃描 + 持股追蹤 + 事後驗證 |
| `python tw_scheduler.py --dca` | 執行 10 年 DCA 回測並推播（每週日 / 手動）|
| `python tw_scheduler.py --weekly` | 手動發送週報摘要 |
| `python tw_scheduler.py --outcome` | 手動查看近 30 日信號正確率統計 |
| `python tw_scheduler.py --daemon` | 常駐排程（08:45 盤前 + 14:00 盤後 + 週五 17:00 週報）|

### `run_once()`
流程：
1. `run_scan()` → 信號掃描
2. `send_scan_results(results)` → Discord 推播
3. `run_portfolio_check()` → 持股追蹤（有操作信號才推播）
4. 事後驗證：`grade_date()`，推播 `build_outcome_embed`（每次皆跑，快速）

### 抓價頻率
- 每日最多 2 次（08:45 / 14:00 台北時間，GitHub Actions 提前2小時觸發）
- 每次：市場模式（當日快取）+ 13 檔掃描 + 持股 ≈ ~17 次 yfinance call

---

## tw_ui.py — 桌面看盤 UI

> `python tw_ui.py` 啟動，customtkinter 暗色主題（BG #1a1a2e）。

### Tab 結構
- **掃描 Tab**：13 檔股票即時策略狀態表格，色碼行 + 近信號預警
- **持股 Tab**：持股清單 P&L 總覽 + 操作建議
- **回測 Tab**：DCA 長期回測結果（摘要比較表 + 各策略卡片 + 明細彈窗）

### 掃描 Tab 欄位
代號 / 名稱 / 類別 / DCA推薦 / 信號 / 現價 / RSI / AVWAP / 試買價(b1) / 加碼價(b2) / 賣出目標(s) / 回撤 / 損益

### 回測 Tab 功能
- **摘要比較表**：所有股票所有策略的 CAGR/總報酬/獲利/終值/MDD 網格
- **策略卡片**：每張卡含策略指標 + ℹ 策略說明按鈕 + 📋 展開明細按鈕
- **交易明細彈窗（`_dca_popup`）**：
  - 欄位：投資日期 / 買入價 / 股數 / 投入金額 / 持有報酬% / 觸發條件（有才顯示）
  - 上方：總投入 / 期末市值 / 整體報酬%
  - fallback 交易：灰色 + ↩ 標示
  - signal 交易：藍色標示
- **策略說明彈窗（`_strategy_info_popup`）**：各策略的邏輯與適用情境說明

### Session 7 待加功能（tw_ui.py）
- DCA 資產曲線圖（matplotlib FigureCanvasTkAgg 嵌入 Toplevel）
- 信號準確度 Tab（rolling accuracy 表格）
- 參數敏感度分析（DCA Tab 內，DD -8%~-20% 各步驟 CAGR）

---

## GitHub Actions 自動化排程

### `.github/workflows/tw_daily.yml`
- **觸發**：UTC 23:30 Sun–Thu（台北 07:30 Mon–Fri）/ UTC 04:00 Mon–Fri（台北 12:00）+ 手動
- **行為**：`python tw_scheduler.py`（掃描 + 持股追蹤 + 事後驗證）
- **快取**：`actions/cache@v4`，路徑 `cache/`
- **待加**：`if: failure()` 步驟推播 Discord 失敗通知（Session 7）

### `.github/workflows/tw_weekly.yml`
- **觸發**：UTC 02:00 Sunday（台北 10:00）+ 手動
- **行為**：`python tw_scheduler.py --dca` → `python tw_scheduler.py --weekly`
- **待加**：`if: failure()` 步驟推播 Discord 失敗通知（Session 7）

### 時區說明
- Taiwan = UTC+8，無 DST（全年固定）
- GitHub Actions free-tier 高峰期可能延遲 1~2 小時，cron 已提前 2 小時觸發

### 環境變數
- `DISCORD_WEBHOOK_URL`：GitHub Secrets 設定，Actions 執行時注入
- `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true`：避免 Node.js 20 棄用警告

---

## config.yaml 設定說明

| 區段 | 說明 |
|------|------|
| `watchlist.etf` | ETF 清單（symbol + name），目前 5 檔 |
| `watchlist.ai_tech` | AI/科技個股清單，目前 8 檔 |
| `signals` | RSI 週期 / MA 週期 / 爆量倍數（全域預設值）|
| `trade_budget` | 每筆買入建議投入金額（NT$）|
| `discord.webhook_url` | 從 .env 讀取 |
| `portfolio` | 持股清單（symbol / shares / cost / note）|
| `schedule` | 盤前/盤後時間 + 時區（daemon 模式用）|

---

## 快取檔案格式

### `cache/scan_YYYY-MM-DD.json`
```json
[
  {
    "symbol": "2330.TW", "name": "台積電", "date": "2026-04-30",
    "price": 950.0, "rsi": 48.2, "avwap": 920.5, "dd_pct": -8.3,
    "ma_fast": 940.2, "ma_slow": 910.1,
    "volume": 28000000, "vol_ma20": 25000000,
    "market_mode": "NORMAL",
    "signals": [{"type": "BUY", "reason": "..."}]
  }
]
```

### `cache/dca_backtest_YYYY-MM-DD.json`
```json
[
  {
    "symbol": "00713.TW", "name": "元大台灣高息低波",
    "strategies": [
      {
        "label": "v2 BUY DCA",
        "total_invested": 1000000, "final_value": 2099650,
        "profit": 1099650, "total_return_pct": 109.97,
        "cagr_pct": 7.7, "max_drawdown_pct": -17.33,
        "n_transactions": 13, "final_price": 52.30,
        "transactions": [
          {
            "date": "2015-08-25", "price": 36.5, "shares": 2739,
            "cost": 99973, "fallback": false,
            "trigger": {"DD%": -12.3, "RSI": 38.1, "vs_b1%": -3.2}
          }
        ]
      }
    ]
  }
]
```

### `cache/outcomes/outcome_YYYY-MM-DD.json`
```json
{
  "signal_date": "2026-04-22",
  "eval_date": "2026-04-29",
  "records": [
    {
      "symbol": "2330.TW", "name": "台積電",
      "signal": "BUY", "signal_price": 900.0,
      "eval_price": 918.0, "pct": 2.0, "correct": true
    }
  ]
}
```

---

## 已知限制與待實作項目

| 項目 | 說明 | 優先 |
|------|------|------|
| DCA 資產曲線圖 | matplotlib 嵌入 tw_ui.py | 🔴 Session 7 |
| 信號準確度儀表板 | UI Tab + Discord 週報整合 | 🔴 Session 7 |
| 持股即時 P&L UI | config.yaml cost 欄位已有，需渲染 | 🔴 Session 7 |
| 參數敏感度分析 | DCA Tab 內輕量重跑 | 🔴 Session 7 |
| GitHub Actions 失敗通知 | if: failure() + Discord push | 🔴 Session 7 |
| tw_ui.py 盤中即時報價 | yfinance 為日線，非 tick | 🟡 中 |
| 個股基本面資料 | 需 FinMind API | ❄️ 冷凍 |
