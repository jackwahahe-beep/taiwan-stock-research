"""
tw_portfolio.py - 持倉追蹤模組
儲存：portfolio_trades.json（與 config.yaml 分開）
"""

import json
import math
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import yfinance as yf
import pandas as pd

TRADES_FILE = Path(__file__).parent / "portfolio_trades.json"
COMMISSION = 0.001425
TAX = 0.003


def _to_str(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def load_trades() -> list:
    if not TRADES_FILE.exists():
        return []
    try:
        raw = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
        return raw
    except Exception:
        return []


def save_trades(trades: list) -> None:
    TRADES_FILE.write_text(
        json.dumps(trades, ensure_ascii=False, indent=2, default=_to_str),
        encoding="utf-8",
    )


def _next_id(trades: list) -> int:
    if not trades:
        return 1
    return max(t["id"] for t in trades) + 1


def add_trade(symbol: str, name: str, buy_date: str, buy_price: float, shares: int,
              note: str = "", target_price: float = 0.0, stop_price: float = 0.0) -> dict:
    trades = load_trades()
    commission = math.floor(buy_price * shares * COMMISSION)
    trade = {
        "id": _next_id(trades),
        "symbol": symbol.upper(),
        "name": name,
        "buy_date": buy_date,
        "buy_price": round(buy_price, 2),
        "shares": int(shares),
        "buy_commission": commission,
        "target_price": round(target_price, 2) if target_price else None,
        "stop_price": round(stop_price, 2) if stop_price else None,
        "sell_date": None,
        "sell_price": None,
        "sell_commission": None,
        "sell_tax": None,
        "note": note,
        "status": "open",
    }
    trades.append(trade)
    save_trades(trades)
    return trade


def close_trade(trade_id: int, sell_date: str, sell_price: float):
    trades = load_trades()
    for t in trades:
        if t["id"] == trade_id and t["status"] == "open":
            sell_commission = math.floor(sell_price * t["shares"] * COMMISSION)
            sell_tax = math.floor(sell_price * t["shares"] * TAX)
            t["sell_date"] = sell_date
            t["sell_price"] = round(sell_price, 2)
            t["sell_commission"] = sell_commission
            t["sell_tax"] = sell_tax
            t["status"] = "closed"
            save_trades(trades)
            return t
    return None


def delete_trade(trade_id: int) -> bool:
    trades = load_trades()
    new = [t for t in trades if t["id"] != trade_id]
    if len(new) == len(trades):
        return False
    save_trades(new)
    return True


def get_open(trades=None) -> list:
    if trades is None:
        trades = load_trades()
    return [t for t in trades if t["status"] == "open"]


def get_closed(trades=None) -> list:
    if trades is None:
        trades = load_trades()
    return [t for t in trades if t["status"] == "closed"]


def fetch_prices(symbols: list) -> dict:
    if not symbols:
        return {}
    result = {}
    tickers = [s if "." in s else s + ".TW" for s in symbols]
    try:
        data = yf.download(
            tickers,
            period="5d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        close = data["Close"] if "Close" in data.columns else data
        if isinstance(close, pd.Series):
            last = float(close.dropna().iloc[-1]) if not close.dropna().empty else 0.0
            result[symbols[0].upper()] = last
        else:
            for col in close.columns:
                s = close[col].dropna()
                raw_sym = str(col).replace(".TW", "").upper()
                result[raw_sym] = float(s.iloc[-1]) if not s.empty else 0.0
    except Exception:
        pass
    return result


def calc_open_pnl(trade: dict, current_price: float) -> dict:
    cost = trade["buy_price"] * trade["shares"] + trade["buy_commission"]
    mkt_val = current_price * trade["shares"]
    est_sell_commission = math.floor(current_price * trade["shares"] * COMMISSION)
    est_sell_tax = math.floor(current_price * trade["shares"] * TAX)
    net_val = mkt_val - est_sell_commission - est_sell_tax
    pnl = net_val - cost
    pnl_pct = pnl / cost * 100 if cost else 0.0
    return {
        "current_price": current_price,
        "market_value": round(mkt_val, 0),
        "pnl": round(pnl, 0),
        "pnl_pct": round(pnl_pct, 2),
    }


def calc_closed_pnl(trade: dict) -> dict:
    cost = trade["buy_price"] * trade["shares"] + trade["buy_commission"]
    revenue = (
        trade["sell_price"] * trade["shares"]
        - trade["sell_commission"]
        - trade["sell_tax"]
    )
    pnl = revenue - cost
    pnl_pct = pnl / cost * 100 if cost else 0.0
    return {
        "pnl": round(pnl, 0),
        "pnl_pct": round(pnl_pct, 2),
    }
