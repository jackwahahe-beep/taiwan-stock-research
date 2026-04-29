# FUNCTION_SPEC — 台股研究

> 版本：v1.0　最後更新：2026-04-29
> GitHub：https://github.com/jackwahahe-beep/taiwan-stock-research

---

## 系統能力總覽

| 能力 | 現況 |
|------|------|
| 追蹤台股清單（ETF + 個股） | ✅ 9 檔，config.yaml 可自由增減 |
| 每日技術指標信號掃描 | ✅ RSI / MA交叉 / 成交量爆量 |
| Discord 即時推播 | ✅ 含顏色 embed，BUY/SELL/WATCH 分色 |
| 歷史策略回測 | ✅ RSI策略 / MA策略 vs B&H基準 |
| 自動每日排程 | ✅ 盤前 08:45 / 盤後 14:00 |
| GitHub 自動備份 | ✅ SSH 已設定，`git push` 即可 |
| 敏感資訊保護 | ✅ Webhook URL 存於 .env，不進 git |
| 台灣假日過濾 | ❌ 未實作（假日仍會執行）|
| MACD 指標 | ❌ 未實作 |
| 回測最大回撤 / Sharpe | ❌ 未實作 |
| 個股基本面資料 | ❌ 未實作（需 FinMind API）|

---

## 模組架構

```
tw_screener.py   ←→   tw_backtest.py
      ↓                     ↓
tw_discord.py  ←←←←←←←←←←←←
      ↑
tw_scheduler.py（入口點）
      ↑
config.yaml + .env（設定）
```

---

## tw_screener.py — 資料拉取與信號計算

### `load_config() → dict`
讀取 `config.yaml`，回傳整份設定字典。

---

### `fetch_data(symbol, period="3mo") → pd.DataFrame`
從 yfinance 拉取指定股票的日線 OHLCV 資料。

| 參數 | 說明 |
|------|------|
| `symbol` | 台股代碼，須加 `.TW` 後綴，例如 `"2330.TW"` |
| `period` | yfinance 期間字串：`"3mo"`, `"6mo"`, `"1y"`, `"2y"` 等 |

- 自動將 index timezone 轉換為 `Asia/Taipei`
- 若無資料回傳空 DataFrame

> ⚠️ `run_scan` 實際以 `period="6mo"` 呼叫，保留 `fetch_data` 預設值 `"3mo"` 是歷史遺留，未來可統一。

---

### `calc_rsi(series, period=14) → pd.Series`
Wilder's RSI 計算。使用 rolling EMA 近似（`.rolling().mean()`）。

| 參數 | 說明 |
|------|------|
| `series` | 收盤價 Series |
| `period` | 預設 14 日 |

回傳值域 0–100，前 `period` 筆為 NaN。

---

### `calc_signals(df, cfg) → dict`
計算單一股票當日所有技術信號。

**輸入保護：**
- `len(df) < 60` → 回傳 `{}`（資料不足）
- 最後收盤為 NaN → 自動 `dropna()` 取最後有效收盤（解決盤前 NaN 問題）
- `price` 或 `rsi` 仍為 NaN → 回傳 `{}`

**回傳格式（有效時）：**
```python
{
  "price": float,       # 最新收盤（NT$）
  "rsi": float,         # 最新 RSI（14日）
  "ma_fast": float,     # MA20
  "ma_slow": float,     # MA60
  "volume": int,        # 最新成交量
  "vol_ma20": int,      # 20日均量
  "signals": [          # 可為空 list
    {"type": "BUY"|"SELL"|"WATCH", "reason": str}
  ]
}
```

**信號觸發條件：**

| 信號 | 類型 | 條件 |
|------|------|------|
| RSI 超賣 | BUY | RSI < `oversold`（預設 30） |
| RSI 超買 | SELL | RSI > `overbought`（預設 70） |
| 黃金交叉 | BUY | MA20 前日 ≤ MA60，今日 > MA60 |
| 死亡交叉 | SELL | MA20 前日 ≥ MA60，今日 < MA60 |
| 成交量爆量 | WATCH | 當日量 > 20日均量 × `volume_spike`（預設 1.5x） |

> 多個信號可同時觸發，全部附加至 `signals` list。

---

### `run_scan() → list[dict]`
批次掃描 `config.yaml` 中所有股票（`etf` + `ai_tech`）。

- 逐一呼叫 `fetch_data(symbol, period="6mo")` 再 `calc_signals`
- 無效股票（空資料 / 資料不足）靜默跳過並列印警告
- 結果寫入 `cache/scan_YYYY-MM-DD.json`
- 回傳完整結果 list，包含有信號和無信號的股票

---

## tw_backtest.py — 回測引擎

### `fetch_long(symbol, period="2y") → pd.DataFrame`
同 `fetch_data`，預設拉取 2 年資料供回測使用。（與 `fetch_data` 邏輯重複，待合併）

---

### `backtest_rsi(df, cfg) → dict`
**RSI 策略回測**

- 進場：RSI 由高於 `oversold` 跌破至低於或等於（剛進超賣區）
- 出場：RSI 由低於 `overbought` 突破至高於或等於（剛進超買區）
- 呼叫 `_run_backtest` 執行逐筆模擬

---

### `backtest_ma(df, cfg) → dict`
**MA 交叉策略回測**

- 進場：MA20 黃金交叉 MA60（前日 fast ≤ slow，今日 fast > slow）
- 出場：MA20 死亡交叉 MA60（前日 fast ≥ slow，今日 fast < slow）
- 呼叫 `_run_backtest` 執行逐筆模擬

---

### `_run_backtest(close, entries, exits, label, init_cash=100_000) → dict`
通用純多頭回測引擎（Python for-loop 逐日模擬）。

**假設：**
- 初始資金 NT$100,000
- 全倉進場（`cash // price` 取整股數）
- 無手續費、無滑價
- 未平倉部位以最後一筆有效收盤計算市值

**回傳格式：**
```python
{
  "label": str,                # 策略名稱
  "total_return_pct": float,   # 總報酬率（%）
  "trades": int,               # 已平倉交易次數
  "win_rate": float | None,    # 勝率（%），無交易時為 None
  "avg_pnl_pct": float | None, # 平均每筆損益（%）
  "max_loss_pct": float | None,# 最大單筆虧損（%）
  "trade_log": list[dict]      # 最近 5 筆交易紀錄
}
```

> ⚠️ 缺少：手續費模型、最大回撤（Max Drawdown）、Sharpe Ratio。

---

### `calc_bnh_return(close) → float`
計算 Buy & Hold 基準報酬率（%），用於對比策略績效。

---

### `run_backtest_all(period="2y") → list[dict]`
批次回測所有追蹤股票，每檔執行 RSI策略 + MA策略，並與 B&H 基準對比。

- 最低資料要求：100 筆（約 5 個月交易日）
- 結果寫入 `cache/backtest_YYYY-MM-DD.json`

---

### `build_backtest_embed(bt) → dict`
將回測結果格式化為 Discord embed dict（紫色，`0x9B59B6`）。

- ✅ 代表策略報酬 > B&H 基準
- ⚠️ 代表策略報酬 ≤ B&H 基準

---

## tw_discord.py — Discord 推播

### `load_config() → dict`
讀取 `config.yaml`，並用 `DISCORD_WEBHOOK_URL` 環境變數（`.env`）覆寫 webhook URL。

---

### `send_webhook(payload, webhook_url) → bool`
發送單次 HTTP POST 至 Discord Webhook。

- timeout = 10 秒
- 回傳 `True` 代表 HTTP 200 / 204

---

### `build_signal_embed(stock) → dict | None`
將單一股票的掃描結果格式化為 Discord embed。

**顏色規則（優先順序）：**
1. 有 SELL 信號 → 紅色（`0xE74C3C`）
2. 有 BUY 信號 → 綠色（`0x2ECC71`）
3. 只有 WATCH → 黃色（`0xF39C12`）
4. 無信號 → 回傳 `None`

**embed 欄位：** 收盤價 / RSI / MA20/MA60 / 信號原因清單

---

### `send_scan_results(results) → None`
主推播函數，處理一整批掃描結果。

- 無任何信號觸發 → 發送藍色「掃描完成，無信號」通知
- 有信號 → 每批最多 10 個 embed（Discord 限制），分批發送
- 支援 `mention_role`（在 `config.yaml` 設定 Discord role ID）

---

## tw_scheduler.py — 排程執行器

### 執行模式

| 指令 | 行為 |
|------|------|
| `python tw_scheduler.py` | 立即執行一次：掃描 + Discord 推播 |
| `python tw_scheduler.py --backtest` | 立即執行：掃描 + 推播 + 回測 + 回測推播 |
| `python tw_scheduler.py --daemon` | 常駐排程，依 config.yaml 時間自動執行 |

### `run_once()`
執行一次完整流程：
1. `run_scan()` — 拉資料 + 計算信號
2. `send_scan_results()` — Discord 推播信號
3. 若 `--backtest` 或 `--post` 旗標存在 → 額外執行 `run_backtest_all()` + Discord 推播回測

### `run_daemon()`
常駐排程，使用 `schedule` 套件：
- 盤前時間（預設 08:45）→ 執行 `run_once`（僅掃描）
- 盤後時間（預設 14:00）→ 執行 `run_once` + 回測
- 每 30 秒 `schedule.run_pending()` 輪詢

> ⚠️ 已知問題：盤前/盤後模式透過 `sys.argv` 傳遞，多執行緒環境不安全。

---

## config.yaml 設定說明

```yaml
watchlist:
  etf:       # ETF 清單，symbol 須加 .TW 後綴
  ai_tech:   # AI/科技個股清單

signals:
  rsi:
    period: 14        # RSI 計算週期
    oversold: 30      # BUY 觸發閾值
    overbought: 70    # SELL 觸發閾值
  ma:
    fast: 20          # 快線（短期均線）
    slow: 60          # 慢線（長期均線）
  volume_spike: 1.5   # 爆量倍數（相對 20 日均量）

discord:
  webhook_url: "${DISCORD_WEBHOOK_URL}"   # 從 .env 讀取
  mention_role: ""    # 選填，Discord role ID

schedule:
  pre_market: "08:45"    # 台股 09:00 開盤，盤前掃描
  post_market: "14:00"   # 台股 13:30 收盤，盤後結算
  timezone: "Asia/Taipei"
```

---

## 快取檔案格式

### `cache/scan_YYYY-MM-DD.json`
```json
[
  {
    "symbol": "2330.TW",
    "name": "台積電",
    "date": "2026-04-29",
    "price": 2215.0,
    "rsi": 76.2,
    "ma_fast": 2100.5,
    "ma_slow": 1950.3,
    "volume": 28000000,
    "vol_ma20": 25000000,
    "signals": [
      {"type": "SELL", "reason": "RSI 76.2 高於 70 (超買)"}
    ]
  }
]
```

### `cache/backtest_YYYY-MM-DD.json`
```json
[
  {
    "symbol": "2330.TW",
    "name": "台積電",
    "period": "2y",
    "bnh_return_pct": 187.56,
    "strategies": [
      {
        "label": "RSI策略",
        "total_return_pct": 61.43,
        "trades": 6,
        "win_rate": 83.3,
        "avg_pnl_pct": 12.5,
        "max_loss_pct": -3.2,
        "trade_log": [...]
      }
    ]
  }
]
```

---

## 已知限制與待實作項目

| 項目 | 說明 | 難度 |
|------|------|------|
| 台灣假日過濾 | 假日不應觸發掃描，需 `chinesecalendar` 或自建假日表 | 低 |
| 回測 Max Drawdown | `_run_backtest` 加入最大回撤計算 | 低 |
| 回測 Sharpe Ratio | 需要無風險利率（台灣 10 年債殖利率） | 中 |
| ETF 資料改 FinMind | yfinance 近期 ETF 資料偶有 NaN，FinMind 更穩定 | 中 |
| MACD 信號 | `calc_signals` 加入 MACD 快慢線交叉 | 低 |
| 週報摘要 | 每週五額外發送本週信號統計 | 中 |
| log 檔 | 以 `logging` 模組取代 `print`，輸出至 `logs/` | 低 |
| `fetch_data` 重構 | 合併 `fetch_data` 與 `fetch_long` 為同一函數 | 低 |
| scheduler 模式重構 | 以 `enum` 取代 `sys.argv` 傳遞執行模式 | 低 |
