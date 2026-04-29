# AI_HANDOFF — 台股研究

> 上次更新：2026-04-29
> GitHub：https://github.com/jackwahahe-beep/taiwan-stock-research

---

## 專案目的

每日自動掃描台灣股票（ETF + AI科技股），計算技術指標信號（RSI、MA交叉、爆量），
並透過 Discord Webhook 推播買入／賣出提醒。附有 pandas 回測引擎驗證信號歷史績效。

---

## 目前狀態（Session 1，2026-04-29）

### 已完成
- [x] yfinance 資料拉取（台股 `.TW` 後綴）
- [x] RSI 超買超賣、MA 黃金/死亡交叉、成交量爆量信號
- [x] Discord Webhook 推播（含顏色 embed、BUY/SELL/WATCH 分色）
- [x] pandas 回測引擎（RSI策略、MA策略 vs B&H基準）
- [x] 每日排程（盤前 08:45 掃描、盤後 14:00 掃描+回測）
- [x] Git 初始化 + GitHub remote 備份

### 已知問題（待修）
| 優先 | 問題 | 位置 |
|------|------|------|
| 🔴 | Discord Webhook URL 明文寫在 config.yaml 且已提交 git | `config.yaml:37` |
| 🔴 | 4 支 ETF（0050/00878/006208/00713）掃描全部回傳 NaN，跳過 | `tw_screener.py:110` — yfinance 近期資料問題 |
| 🟡 | `sys.argv` 被 append/remove 傳遞盤前盤後模式，多執行緒不安全 | `tw_scheduler.py:57-64` |
| 🟡 | `fetch_data`（screener）與 `fetch_long`（backtest）邏輯相同，重複 | `tw_screener.py:27`、`tw_backtest.py:23` |
| 🟡 | 回測缺 Max Drawdown、Sharpe Ratio 指標 | `tw_backtest.py:_run_backtest` |
| 🟢 | 沒有 log 檔，只有 print，出錯難追查 | 全域 |
| 🟢 | 無台灣假日偵測，假日仍觸發掃描推播 | `tw_scheduler.py` |

---

## 檔案結構

```
台股研究/
├── AI_HANDOFF.md          ← 本文件
├── config.yaml            ← 追蹤清單 + 信號參數 + Discord URL（⚠️ 應改用 .env）
├── tw_screener.py         ← 資料拉取 + 信號計算（RSI/MA/Volume）
├── tw_backtest.py         ← 回測引擎（RSI策略 / MA策略 / B&H對比）
├── tw_discord.py          ← Discord Webhook 推播（build embed / send）
├── tw_scheduler.py        ← 排程執行器（手動 / --daemon 常駐 / --backtest）
├── requirements.txt       ← yfinance, pandas, numpy, requests, pyyaml, schedule
├── .gitignore             ← 排除 cache/, logs/, __pycache__
└── cache/                 ← 每日掃描 JSON（scan_YYYY-MM-DD.json）
                              回測 JSON（backtest_YYYY-MM-DD.json）
```

---

## 執行方式

```bash
# 安裝套件（僅第一次）
pip install -r requirements.txt

# 測試 Discord 推播
python tw_discord.py

# 手動執行一次完整掃描
python tw_scheduler.py

# 手動執行掃描 + 回測
python tw_scheduler.py --backtest

# 常駐排程（08:45 盤前掃描 / 14:00 盤後掃描+回測）
python tw_scheduler.py --daemon
```

---

## 信號邏輯

| 信號 | 條件 | 類型 |
|------|------|------|
| RSI 超賣 | RSI < 30 | BUY |
| RSI 超買 | RSI > 70 | SELL |
| 黃金交叉 | MA20 由下穿 MA60 | BUY |
| 死亡交叉 | MA20 由上穿 MA60 | SELL |
| 成交量爆量 | 當日量 > 20日均量 × 1.5 | WATCH |

---

## 資料來源說明

- **有效**：個股（2330/2454/3711/2303/6770）— yfinance 正常
- **⚠️ 異常**：ETF（0050/00878/006208/00713）— 掃描近期資料回傳 NaN
  - 原因推測：yfinance 對台灣 ETF 的除息調整有 bug
  - 建議改用 [FinMind API](https://finmindtrade.com/)（免費，每日 600 次）

---

## 下一步建議（Next Session）

### 高優先
1. **Webhook URL 移出 git** — 新增 `.env` 檔，用 `python-dotenv` 讀取；將 `config.yaml` 中的 URL 改為 `${DISCORD_WEBHOOK_URL}`
2. **ETF 資料源修復** — 嘗試 `yf.download()` 批次拉取取代 `Ticker.history()`，或導入 FinMind API 作為備援

### 中優先
3. **重構 scheduler 模式傳遞** — 用 `enum` 或 `dataclass` 取代 `sys.argv` 操作
4. **合併 fetch 函數** — `tw_screener.fetch_data` 和 `tw_backtest.fetch_long` 合併為一個帶 `period` 參數的共用函數
5. **回測補指標** — 加入 Max Drawdown（最大回撤）、Sharpe Ratio

### 低優先
6. **Log 檔** — 用 Python `logging` 模組輸出至 `logs/YYYY-MM-DD.log`
7. **台灣假日過濾** — 安裝 `chinesecalendar` 或自建假日清單，避免假日觸發

---

## 重要設定提醒

- **Discord Webhook URL** 目前在 `config.yaml:37`，已提交至 git（public repo）
  → 建議立即在 Discord 重新生成 Webhook URL，舊 URL 視為已外洩
- **GitHub PAT token** 已在對話中使用，記得確認是否已撤銷舊 token
