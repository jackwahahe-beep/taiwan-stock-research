# FUNCTION_SPEC — 台股研究

> 版本：v2.0　最後更新：2026-04-29
> GitHub：https://github.com/jackwahahe-beep/taiwan-stock-research

---

## 系統能力總覽

| 能力 | 現況 |
|------|------|
| 追蹤台股清單（ETF + 個股） | ✅ 15 檔，config.yaml 可自由增減 |
| v2 信號掃描（AVWAP + DD + 市場模式 + 個股RSI） | ✅ tw_screener.py v2 |
| Discord 即時推播（買入/賣出分色 embed） | ✅ 含建議股數、回測佐證、進出場價格 |
| 持股追蹤（P&L + 賣出建議 + 反彈偵測） | ✅ tw_portfolio.py |
| 信號回測（2年，B&H / BUY / STRONG BUY） | ✅ tw_backtest.py v2，滾動 AVWAP，含 MDD / Sharpe |
| DCA 長期回測（10年，4策略對比） | ✅ tw_backtest_dca.py v2 |
| 自動每日排程（盤前 08:45 / 盤後 14:00） | ✅ tw_scheduler.py |
| 週報摘要（每週五 17:00 自動推播） | ✅ build_weekly_embed + run_weekly_report |
| GitHub 自動備份 | ✅ SSH 已設定 |
| 敏感資訊保護 | ✅ Webhook URL 存於 .env |
| 台灣假日過濾 | ✅ is_trading_day()，holidays.TW() |
| 回測 Max Drawdown / Sharpe | ✅ tw_backtest.py v2 |
| 個股基本面資料 | ❌ 未實作（需 FinMind API）|

---

## 模組架構

```
tw_screener.py   ←→   tw_backtest.py
      ↓                     ↓
tw_discord.py  ←←←←←←←←←←←←
      ↑
tw_scheduler.py（入口點）
      ↑                ↑
config.yaml + .env   tw_portfolio.py
```

---

## 策略核心邏輯（v2）

### 信號參數（SIGNAL_CONFIG，tw_screener.py）

每檔股票獨立設定，鍵值說明：

| 參數 | 說明 |
|------|------|
| `rsi_buy` | BUY 觸發 RSI 上限（DD > -10%）|
| `rsi_sbuy` | STRONG BUY 觸發 RSI 上限（DD > -20%）|
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

## tw_backtest.py — 信號回測引擎（v2）

### `fetch_long(symbol, period="2y") → pd.DataFrame`
同 `fetch_data`，預設 2 年。（待合併）

### `calc_rolling_avwap(close, high, low, volume, lookback=60) → pd.Series`
逐日滾動計算 AVWAP，與 `tw_screener.calc_avwap` 邏輯一致，無未來資料洩漏。

### `backtest_signals(df, symbol, period_label) → dict`
v2 信號回測：依 SIGNAL_CONFIG 個股參數，計算 B&H / BUY策略 / STRONG_BUY策略 三組報酬。

**回傳格式：**
```python
{
  "symbol": str,
  "period": str,
  "bnh_return_pct": float,
  "strategies": [
    {
      "label": str,
      "total_return_pct": float,
      "trades": int,
      "win_rate": float | None,
      "avg_pnl_pct": float | None,
      "max_loss_pct": float | None,
      "beats_bnh": bool,
      "trade_log": list[dict]
    }
  ]
}
```

### `run_backtest_all(period="2y") → list[dict]`
批次回測所有追蹤股票，快取至 `cache/backtest_YYYY-MM-DD.json`。

### `load_backtest_cache() → dict`
讀取最新 `cache/backtest_*.json`，回傳 `{symbol: bt_result}` dict，供推播引用。

### `build_backtest_embed(bt) → dict`
格式化為 Discord embed（紫色 0x9B59B6）。✅ 代表策略 > B&H。

---

## tw_backtest_dca.py — DCA 長期回測（v2）

### 四種策略對比

| 策略 | 說明 |
|------|------|
| `bnh_dca` | 每年固定買入（Buy & Hold DCA，基準） |
| `buy_dca` | 只在 BUY 信號時投入，無信號累積現金 |
| `sbuy_dca` | 只在 STRONG BUY 信號時投入 |
| `mixed_dca` | STRONG BUY 投雙倍，BUY 投一倍，無信號累積 |

### `run_dca_all() → list[dict]`
執行所有股票 10 年 DCA 回測，每股 NT$100k/年。快取至 `cache/dca_backtest_YYYY-MM-DD.json`。

### `build_dca_embed(result) → dict`
格式化 DCA 回測結果為 Discord embed，含各策略總報酬 / MDD / CAGR 比較。

### `load_dca_cache() → dict`
讀取最新 DCA 快取。

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

### `_calc_sell_rec(result, action) → dict`
產生具體賣出建議（賣出股數 / 預估回收金額 / 實現損益）：
- SELL_STRONG → 全賣
- EXIT_BOUNCE → 減碼一半
- SELL_WATCH → 全出停損，附 -10% 停損參考價

### `run_portfolio_check() → list[dict]`
批次處理所有持股，附加 `advice` 欄位。

### `build_portfolio_embeds(results) → list[dict]`
永遠輸出一張「持股總覽」embed；另對 `push=True` 的個股輸出操作建議 embed。

---

## tw_discord.py — Discord 推播

### `send_webhook(payload, webhook_url) → bool`
單次 HTTP POST，timeout 10s。

### `send_scan_results(results, bt_cache=None) → None`
三分流推播：
1. 非持股 + BUY/STRONG BUY → `build_buy_embed`（含建議股數 + 回測佐證）
2. 持股 + SELL → `build_sell_embed`
3. 都無 → 每日摘要（市場溫度 + 中性觀察清單）

### `build_buy_embed(stock, cfg, bt_summary=None) → dict`
買入 embed 欄位：
- 現價 / RSI / DD / AVWAP 距離
- 📌 建議進場：掛單價 / 買入股數 / 預估成本
- 回測勝率（若有快取）
- ⚠️ 市場警戒標示（WARN/RISK 模式時）

建議股數 = `trade_budget // price`（from config.yaml）

### `build_sell_embed(stock, holding_result, rec) → dict`
賣出 embed 欄位：現價 / 持股損益 / RSI / 操作依據 / 📌 賣出建議（股數 + 金額）

---

## tw_scheduler.py — 排程執行器

### 執行模式

| 指令 | 行為 |
|------|------|
| `python tw_scheduler.py` | 立即執行一次：掃描 + 持股追蹤（用快取回測）|
| `python tw_scheduler.py --backtest` | 立即執行：掃描 + 持股追蹤 + 重新跑 2 年信號回測 |
| `python tw_scheduler.py --dca` | 執行 10 年 DCA 回測並推播 |
| `python tw_scheduler.py --weekly` | 手動發送週報摘要 |
| `python tw_scheduler.py --daemon` | 常駐排程（08:45 盤前 + 14:00 盤後 + 週五 17:00 週報）|

### `run_once(run_backtest=False)`
流程：
1. 載入或重跑回測快取
2. `run_scan()` → 信號掃描
3. `send_scan_results()` → Discord 推播
4. `run_portfolio_check()` → 持股追蹤（有操作信號才推播）

### `run_daemon()`
`schedule` 套件常駐，每 30 秒 `run_pending()`。

### 抓價頻率
- 每日最多 2 次（08:45 / 14:00）
- 每次：市場模式（當日快取，只 1 次）+ 15 檔掃描 + 3 檔持股 ≈ 19 次 yfinance call
- 無 rate limiting，無盤中即時報價

---

## config.yaml 設定說明

| 區段 | 說明 |
|------|------|
| `watchlist.etf` | ETF 清單（symbol + name）|
| `watchlist.ai_tech` | AI/科技個股清單 |
| `signals` | RSI 週期 / MA 週期 / 爆量倍數（全域預設值）|
| `trade_budget` | 每筆買入建議投入金額（NT$）|
| `discord.webhook_url` | 從 .env 讀取 |
| `portfolio` | 持股清單（symbol / shares / cost / note）|
| `schedule` | 盤前/盤後時間 + 時區 |

---

## 快取檔案格式

### `cache/scan_YYYY-MM-DD.json`
```json
[
  {
    "symbol": "2330.TW", "name": "台積電", "date": "2026-04-29",
    "price": 950.0, "rsi": 48.2, "avwap": 920.5, "dd_pct": -8.3,
    "ma_fast": 940.2, "ma_slow": 910.1,
    "volume": 28000000, "vol_ma20": 25000000,
    "market_mode": "NORMAL",
    "signals": [{"type": "BUY", "reason": "..."}]
  }
]
```

### `cache/backtest_YYYY-MM-DD.json`
```json
[
  {
    "symbol": "2330.TW", "period": "2y", "bnh_return_pct": 45.2,
    "strategies": [
      {"label": "STRONG_BUY策略", "total_return_pct": 38.1,
       "trades": 4, "win_rate": 75.0, "beats_bnh": false, "trade_log": [...]}
    ]
  }
]
```

### `cache/dca_backtest_YYYY-MM-DD.json`
```json
[
  {
    "symbol": "00713.TW", "name": "元大台灣高息低波",
    "bnh_total": 980000, "bnh_cagr": 8.2,
    "strategies": [
      {"label": "BUY_DCA", "total": 1050000, "cagr": 9.1, "mdd": -10.3, "beats_bnh": true}
    ]
  }
]
```

---

## 已知限制與待實作項目

| 項目 | 說明 | 優先 |
|------|------|------|
| scheduler 模式重構 | 以 enum 取代 sys.argv | 🟢 低 |
| 個股基本面資料 | 需 FinMind API | ❄️ 冷凍 |
