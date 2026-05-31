from fastapi import FastAPI
from fastapi.responses import FileResponse
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from typing import List
from fastapi import Query
import uuid
import random
import csv
import io
import os
import json
import math
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# DRAGONLADY CONFIG
# ============================================================

DRAGONLADY_MODE = "PAPER_ONLY"
DRAGONLADY_VERSION = "1.9.0"

DEFAULT_SYMBOL = "AAPL"
INITIAL_PRICE = 100.0

MARKET_DATA_PROVIDER = "SIMULATED"

MOVING_AVERAGE_WINDOW = 5

ENTRY_THRESHOLD_PERCENT = 1.0
EXIT_THRESHOLD_PERCENT = 1.0

SPREAD_PERCENT = 0.05
SLIPPAGE_PERCENT = 0.03
COMMISSION_PER_TRADE = 1.00

RISK_PER_TRADE_PERCENT = 1.0
STOP_LOSS_PERCENT = 2.0
MAX_POSITION_VALUE_PERCENT = 50.0
MIN_QUANTITY = 1

FMP_API_KEY_ENV_NAME = "FMP_API_KEY"
FMP_DEFAULT_HISTORY_LIMIT = 500

CHART_EQUITY_FILE = "dragonlady_equity_curve.png"
CHART_DRAWDOWN_FILE = "dragonlady_drawdown_curve.png"
CHART_BENCHMARK_FILE = "dragonlady_benchmark_curve.png"
LONG_TREND_WINDOW = 50
USE_LONG_TREND_FILTER = True

TRAILING_STOP_PERCENT = 5.0
USE_TRAILING_STOP = False
HARD_MA_EXIT_PERCENT = 1.5
EXPOSURE_OPTIMIZATION_VALUES = [
    20.0,
    30.0,
    40.0,
    50.0,
    60.0,
    75.0,
    100.0
]
RISK_OPTIMIZATION_VALUES = [
    0.5,
    1.0,
    1.5,
    2.0,
    3.0
]
STRATEGY_OPTIMIZATION_GRID = {
    "moving_average_window": [5, 10, 20, 30, 50],
    "entry_threshold_percent": [0.10, 0.25, 0.50, 1.00],
    "exit_threshold_percent": [0.10, 0.25, 0.50, 1.00]
}
MULTI_SYMBOL_TEST_LIST = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "SPY",
    "QQQ"
]
# ============================================================
# STATE
# ============================================================

state = {
    "cash": 100_000.0,
    "initial_cash": 100_000.0,

    "realized_pnl_gross": 0.0,
    "realized_pnl_net": 0.0,
    "total_trading_costs": 0.0,

    "positions": {},
    "trades": [],
    "signals": [],
    "api_usage": [],

    "market_data_provider": MARKET_DATA_PROVIDER,
    "market_prices": {
        DEFAULT_SYMBOL: INITIAL_PRICE
    },
    "price_history": [],

    "historical_buffers": {},
    "historical_buffer_indexes": {},

    "equity_curve": [],
    "peak_equity": 100_000.0,
    "max_drawdown_amount": 0.0,
    "max_drawdown_percent": 0.0,

    "last_cycle": None,
    "cycle_count": 0
}


app = FastAPI(
    title="DragonLady Paper Trading Lab",
    version=DRAGONLADY_VERSION
)


# ============================================================
# TIME / API LOGGING
# ============================================================
def compute_strategy_score(result: dict) -> float:
    """
    Compute a composite score used to rank strategies.

    Score =
        35% Sharpe
        30% Calmar
        20% Net Return
        15% Alpha
    """

    sharpe = float(result.get("sharpe_ratio", 0) or 0)
    calmar = float(result.get("calmar_ratio", 0) or 0)
    net_return = float(result.get("return_percent_net", 0) or 0)
    alpha = float(result.get("alpha_percent", 0) or 0)

    # Negative alpha should penalize less aggressively
    alpha_component = max(alpha, -20)

    score = (
        0.35 * sharpe
        + 0.30 * calmar
        + 0.20 * net_return
        + 0.15 * alpha_component
    )

    return round(score, 6)

def passes_quality_filters(result: dict,
                           min_sharpe: float = 0.75,
                           min_return: float = 0,
                           min_profit_factor: float = 1.2,
                           max_drawdown_limit: float = 15.0) -> bool:
    """
    Minimum requirements for a strategy to be eligible.
    """

    sharpe = float(result.get("sharpe_ratio", 0) or 0)
    net_return = float(result.get("return_percent_net", 0) or 0)
    profit_factor = float(result.get("profit_factor_net", 0) or 0)
    max_dd = float(result.get("max_drawdown_percent", 999) or 999)

    return (
        sharpe >= min_sharpe
        and net_return > min_return
        and profit_factor >= min_profit_factor
        and max_dd <= max_drawdown_limit
    )
def normalize_weights(candidates: list) -> list:
    """
    Convert scores into portfolio weights summing to 100%.
    """

    total_score = sum(max(c["score"], 0) for c in candidates)

    if total_score <= 0:
        equal_weight = 100 / len(candidates)
        for c in candidates:
            c["weight_percent"] = round(equal_weight, 4)
        return candidates

    for c in candidates:
        c["weight_percent"] = round(
            100 * max(c["score"], 0) / total_score,
            4
        )

    return candidates
def simulate_weighted_portfolio(candidates: list,
                                initial_capital: float = 100000) -> dict:
    """
    Aggregate weighted symbol performances into a single portfolio result.
    """

    total_return_percent = 0.0
    total_alpha_percent = 0.0
    weighted_sharpe = 0.0
    weighted_calmar = 0.0
    weighted_sortino = 0.0
    weighted_drawdown = 0.0

    allocations = []

    for c in candidates:
        w = c["weight_percent"] / 100.0
        r = c["result"]

        total_return_percent += w * float(r.get("return_percent_net", 0) or 0)
        total_alpha_percent += w * float(r.get("alpha_percent", 0) or 0)
        weighted_sharpe += w * float(r.get("sharpe_ratio", 0) or 0)
        weighted_calmar += w * float(r.get("calmar_ratio", 0) or 0)
        weighted_sortino += w * float(r.get("sortino_ratio", 0) or 0)
        weighted_drawdown += w * float(r.get("max_drawdown_percent", 0) or 0)

        allocations.append({
            "symbol": c["symbol"],
            "score": round(c["score"], 4),
            "weight_percent": c["weight_percent"],
            "allocated_capital": round(initial_capital * w, 2),
            "return_percent_net": r.get("return_percent_net", 0),
            "sharpe_ratio": r.get("sharpe_ratio", 0),
            "alpha_percent": r.get("alpha_percent", 0)
        })

    final_value = initial_capital * (1 + total_return_percent / 100)

    return {
        "portfolio_summary": {
            "initial_capital": initial_capital,
            "final_portfolio_value": round(final_value, 2),
            "portfolio_return_percent_net": round(total_return_percent, 4),
            "portfolio_alpha_percent": round(total_alpha_percent, 4),
            "portfolio_sharpe_ratio": round(weighted_sharpe, 4),
            "portfolio_sortino_ratio": round(weighted_sortino, 4),
            "portfolio_calmar_ratio": round(weighted_calmar, 4),
            "portfolio_max_drawdown_percent": round(weighted_drawdown, 4)
        },
        "allocations": allocations
    }
def run_walk_forward_optimization_internal(
    symbol: str,
    train_window: int = 100,
    test_window: int = 100,
    limit: int = 500
):
    symbol = symbol.upper()

    load_result = load_fmp_history_into_buffer(symbol, limit)

    if load_result.get("status") != "ok":
        return {
            "status": "error",
            "message": load_result.get("message")
        }

    full_buffer = state["historical_buffers"][symbol]
    total_records = len(full_buffer)

    segments = []
    start_index = 0

    original_provider = state["market_data_provider"]

    while start_index + train_window + test_window <= total_records:
        train_start = start_index
        train_end = start_index + train_window
        test_start = train_end
        test_end = train_end + test_window

        training_buffer = full_buffer[train_start:train_end]
        testing_buffer = full_buffer[test_start:test_end]

        # -------------------------
        # STEP 1: Optimize on train
        # -------------------------
        state["historical_buffers"][symbol] = training_buffer
        state["historical_buffer_indexes"][symbol] = 0

        optimization_result = optimize_strategy(
            symbol=symbol
        )

        best_parameters = optimization_result["best_by_sharpe"]

        best_ma = best_parameters["moving_average_window"]
        best_entry = best_parameters["entry_threshold_percent"]
        best_exit = best_parameters["exit_threshold_percent"]

        # Save current config
        old_ma = MOVING_AVERAGE_WINDOW
        old_entry = ENTRY_THRESHOLD_PERCENT
        old_exit = EXIT_THRESHOLD_PERCENT

        # Apply best parameters
        globals()["MOVING_AVERAGE_WINDOW"] = best_ma
        globals()["ENTRY_THRESHOLD_PERCENT"] = best_entry
        globals()["EXIT_THRESHOLD_PERCENT"] = best_exit

        # -------------------------
        # STEP 2: Test on OOS data
        # -------------------------
        state["historical_buffers"][symbol] = testing_buffer
        state["historical_buffer_indexes"][symbol] = 0

        reset_dragonlady_core(preserve_buffers=True)

        state["market_data_provider"] = "FMP_HISTORY_BUFFER"

        backtest_result = run_full_backtest_internal(
            symbol=symbol,
            reset_index=True,
            reset_portfolio=False,
            close_end_position=True
        )

        segment_summary = summarize_current_state(symbol)

        segments.append({
            "segment_number": len(segments) + 1,
            "training_records": len(training_buffer),
            "testing_records": len(testing_buffer),
            "best_parameters": {
                "moving_average_window": best_ma,
                "entry_threshold_percent": best_entry,
                "exit_threshold_percent": best_exit
            },
            "test_results": segment_summary
        })

        # Restore config
        globals()["MOVING_AVERAGE_WINDOW"] = old_ma
        globals()["ENTRY_THRESHOLD_PERCENT"] = old_entry
        globals()["EXIT_THRESHOLD_PERCENT"] = old_exit

        start_index += test_window

    state["market_data_provider"] = original_provider

    if len(segments) == 0:
        return {
            "status": "walk_forward_failed",
            "message": "No valid segments."
        }

    average_return = round(
        sum(
            s["test_results"]["return_percent_net"]
            for s in segments
            if s["test_results"]["return_percent_net"] is not None
        ) / len(segments),
        4
    )

    average_sharpe = round(
        sum(
            s["test_results"]["sharpe_ratio"]
            for s in segments
            if s["test_results"]["sharpe_ratio"] is not None
        ) / len(segments),
        4
    )

    average_alpha = round(
        sum(
            s["test_results"]["alpha_percent"]
            for s in segments
            if s["test_results"]["alpha_percent"] is not None
        ) / len(segments),
        4
    )

    average_drawdown = round(
        sum(
            s["test_results"]["max_drawdown_percent"]
            for s in segments
            if s["test_results"]["max_drawdown_percent"] is not None
        ) / len(segments),
        4
    )

    best_segment = max(
        segments,
        key=lambda x: x["test_results"]["return_percent_net"]
        if x["test_results"]["return_percent_net"] is not None else -10**18
    )

    worst_segment = min(
        segments,
        key=lambda x: x["test_results"]["return_percent_net"]
        if x["test_results"]["return_percent_net"] is not None else 10**18
    )

    return {
        "status": "walk_forward_completed",
        "version": DRAGONLADY_VERSION,
        "symbol": symbol,
        "train_window": train_window,
        "test_window": test_window,
        "records_loaded": total_records,
        "segments_count": len(segments),
        "aggregate_results": {
            "average_return_percent_net": average_return,
            "average_sharpe_ratio": average_sharpe,
            "average_alpha_percent": average_alpha,
            "average_max_drawdown_percent": average_drawdown,
            "best_segment": best_segment,
            "worst_segment": worst_segment
        },
        "segments": segments
    }

def summarize_current_state(symbol: str):
    report = build_backtest_report(symbol)

    pnl = report.get("pnl_summary", {})
    risk = report.get("risk_summary", {})
    perf = report.get("performance_metrics", {})
    bench = report.get("benchmark_summary", {})
    stats = report.get("trade_stats", {})
    portfolio = report.get("portfolio_summary", {})

    return {
        "symbol": symbol,
        "return_percent_net": pnl.get("return_percent_net"),
        "portfolio_value": portfolio.get("portfolio_value"),
        "alpha_percent": bench.get("alpha_percent"),
        "cagr_percent": perf.get("cagr_percent"),
        "sharpe_ratio": perf.get("sharpe_ratio"),
        "sortino_ratio": perf.get("sortino_ratio"),
        "calmar_ratio": perf.get("calmar_ratio"),
        "max_drawdown_percent": risk.get("max_drawdown_percent"),
        "profit_factor_net": stats.get("profit_factor_net"),
        "win_rate_percent": stats.get("win_rate_percent"),
        "closed_trades_count": stats.get("closed_trades_count")
    }
def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_api_usage(provider, endpoint, requests_count, latency_ms=None, error=None):
    state["api_usage"].append({
        "timestamp": now_utc(),
        "provider": provider,
        "endpoint": endpoint,
        "requests_count": requests_count,
        "latency_ms": latency_ms,
        "error": error
    })


# ============================================================
# MARKET DATA
# ============================================================

def get_current_price(symbol: str) -> float:
    return state["market_prices"].get(symbol, INITIAL_PRICE)


def get_fmp_api_key():
    api_key = os.environ.get(FMP_API_KEY_ENV_NAME)

    if api_key is None or api_key.strip() == "":
        return None

    return api_key.strip()


def normalize_fmp_historical_payload(raw_payload):
    if isinstance(raw_payload, list):
        return raw_payload

    if isinstance(raw_payload, dict):
        if "historical" in raw_payload and isinstance(raw_payload["historical"], list):
            return raw_payload["historical"]

        if "data" in raw_payload and isinstance(raw_payload["data"], list):
            return raw_payload["data"]

    return []


def parse_fmp_price_record(symbol: str, row: dict):
    date_value = row.get("date") or row.get("Date")
    close_value = row.get("close") or row.get("Close")

    if close_value is None:
        return None

    try:
        close_price = round(float(close_value), 2)
    except Exception:
        return None

    def safe_float(value):
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def safe_int(value):
        try:
            if value is None:
                return None
            return int(float(value))
        except Exception:
            return None

    return {
        "symbol": symbol,
        "date": date_value,
        "price": close_price,
        "close": close_price,
        "open": safe_float(row.get("open") or row.get("Open")),
        "high": safe_float(row.get("high") or row.get("High")),
        "low": safe_float(row.get("low") or row.get("Low")),
        "volume": safe_int(row.get("volume") or row.get("Volume")),
        "raw": row
    }


def fetch_fmp_historical_prices(symbol: str, limit: int = FMP_DEFAULT_HISTORY_LIMIT):
    api_key = get_fmp_api_key()

    if api_key is None:
        add_api_usage(
            provider="FMP_HISTORY",
            endpoint="NO_ENDPOINT",
            requests_count=0,
            error=f"Missing environment variable {FMP_API_KEY_ENV_NAME}"
        )

        return {
            "status": "error",
            "message": f"Missing environment variable {FMP_API_KEY_ENV_NAME}",
            "records": []
        }

    symbol = symbol.upper()
    limit = max(1, int(limit))

    primary_url = (
        "https://financialmodelingprep.com/stable/historical-price-eod/full?"
        + urlencode({
            "symbol": symbol,
            "apikey": api_key
        })
    )

    fallback_url = (
        f"https://financialmodelingprep.com/api/v3/historical-price-full/"
        f"{symbol}?apikey={api_key}"
    )

    urls_to_try = [
        ("FMP_HISTORY_STABLE", primary_url),
        ("FMP_HISTORY_LEGACY", fallback_url)
    ]

    last_error = None

    for provider_name, url in urls_to_try:
        started_at = datetime.now(timezone.utc)

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "DragonLady/1.3 Paper Trading Lab"
                }
            )

            with urlopen(request, timeout=20) as response:
                raw_data = response.read().decode("utf-8")

            ended_at = datetime.now(timezone.utc)
            latency_ms = round((ended_at - started_at).total_seconds() * 1000, 2)

            payload = json.loads(raw_data)
            rows = normalize_fmp_historical_payload(payload)

            parsed_records = []

            for row in rows:
                parsed = parse_fmp_price_record(symbol, row)

                if parsed is not None:
                    parsed_records.append(parsed)

            if len(parsed_records) == 0:
                last_error = "No valid historical price records found"

                add_api_usage(
                    provider=provider_name,
                    endpoint=url,
                    requests_count=1,
                    latency_ms=latency_ms,
                    error=last_error
                )

                continue

            parsed_records = sorted(
                parsed_records,
                key=lambda x: x["date"] or ""
            )

            if len(parsed_records) > limit:
                parsed_records = parsed_records[-limit:]

            add_api_usage(
                provider=provider_name,
                endpoint=url,
                requests_count=1,
                latency_ms=latency_ms,
                error=None
            )

            return {
                "status": "ok",
                "provider": provider_name,
                "symbol": symbol,
                "records_count": len(parsed_records),
                "records": parsed_records
            }

        except HTTPError as e:
            last_error = f"HTTPError: {str(e)}"
            add_api_usage(provider_name, url, 1, error=last_error)

        except URLError as e:
            last_error = f"URLError: {str(e)}"
            add_api_usage(provider_name, url, 1, error=last_error)

        except Exception as e:
            last_error = f"Unexpected error: {str(e)}"
            add_api_usage(provider_name, url, 1, error=last_error)

    return {
        "status": "error",
        "message": last_error or "Unknown FMP error",
        "records": []
    }


def load_fmp_history_into_buffer(symbol: str, limit: int = FMP_DEFAULT_HISTORY_LIMIT):
    symbol = symbol.upper()

    result = fetch_fmp_historical_prices(symbol, limit)

    if result["status"] != "ok":
        return {
            "status": "error",
            "symbol": symbol,
            "message": result.get("message", "Could not load FMP historical data"),
            "records_loaded": 0
        }

    records = result["records"]

    state["historical_buffers"][symbol] = records
    state["historical_buffer_indexes"][symbol] = 0

    return {
        "status": "ok",
        "symbol": symbol,
        "provider": result.get("provider"),
        "records_loaded": len(records),
        "first_record": records[0] if len(records) > 0 else None,
        "last_record": records[-1] if len(records) > 0 else None
    }


def simulate_next_price(symbol: str):
    old_price = get_current_price(symbol)
    change_percent = random.uniform(-0.02, 0.02)
    new_price = round(old_price * (1 + change_percent), 2)

    state["market_prices"][symbol] = new_price

    price_record = {
        "timestamp": now_utc(),
        "symbol": symbol,
        "price": new_price,
        "provider": "SIMULATED",
        "change_percent": round(change_percent * 100, 4)
    }

    state["price_history"].append(price_record)

    add_api_usage(
        provider="SIMULATED",
        endpoint="FAKE_PRICE_FEED",
        requests_count=0,
        latency_ms=0,
        error=None
    )

    return price_record


def fetch_next_fmp_buffer_price(symbol: str):
    symbol = symbol.upper()

    if symbol not in state["historical_buffers"]:
        return {
            "status": "error",
            "symbol": symbol,
            "provider": "FMP_HISTORY_BUFFER",
            "message": "No historical buffer loaded for this symbol."
        }

    buffer = state["historical_buffers"][symbol]
    index = state["historical_buffer_indexes"].get(symbol, 0)

    if index >= len(buffer):
        return {
            "status": "finished",
            "symbol": symbol,
            "provider": "FMP_HISTORY_BUFFER",
            "message": "Historical buffer is finished.",
            "buffer_length": len(buffer),
            "current_index": index
        }

    record = buffer[index]
    previous_price = get_current_price(symbol)
    price = record["price"]

    if previous_price == 0:
        change_percent = 0.0
    else:
        change_percent = round(
            ((price - previous_price) / previous_price) * 100,
            4
        )

    state["historical_buffer_indexes"][symbol] = index + 1
    state["market_prices"][symbol] = price

    price_record = {
        "timestamp": now_utc(),
        "symbol": symbol,
        "price": price,
        "provider": "FMP_HISTORY_BUFFER",
        "change_percent": change_percent,
        "historical_date": record.get("date"),
        "open": record.get("open"),
        "high": record.get("high"),
        "low": record.get("low"),
        "close": record.get("close"),
        "volume": record.get("volume"),
        "buffer_index": index,
        "buffer_length": len(buffer),
        "raw_data": record
    }

    state["price_history"].append(price_record)

    return price_record


def fetch_next_market_price(symbol: str):
    provider = state["market_data_provider"]

    if provider == "SIMULATED":
        return simulate_next_price(symbol)

    if provider == "FMP_HISTORY_BUFFER":
        return fetch_next_fmp_buffer_price(symbol)

    fallback_record = simulate_next_price(symbol)
    fallback_record["provider"] = "SIMULATED_FALLBACK"
    fallback_record["warning"] = f"Unknown provider {provider}; used simulated fallback price."

    return fallback_record


def get_recent_prices(symbol: str, window: int):
    prices = [
        x["price"]
        for x in state["price_history"]
        if x["symbol"] == symbol
    ]

    return prices[-window:]


def calculate_moving_average(symbol: str, window: int):
    prices = get_recent_prices(symbol, window)

    if len(prices) == 0:
        return None

    return round(sum(prices) / len(prices), 2)


def calculate_distance_from_ma_percent(price: float, moving_average: float):
    if moving_average is None or moving_average == 0:
        return None

    return round(((price - moving_average) / moving_average) * 100, 4)


# ============================================================
# COST MODEL
# ============================================================

def calculate_buy_execution_price(mid_price: float) -> float:
    execution_price = mid_price * (
        1
        + (SPREAD_PERCENT / 2) / 100
        + SLIPPAGE_PERCENT / 100
    )

    return round(execution_price, 4)


def calculate_sell_execution_price(mid_price: float) -> float:
    execution_price = mid_price * (
        1
        - (SPREAD_PERCENT / 2) / 100
        - SLIPPAGE_PERCENT / 100
    )

    return round(execution_price, 4)


def calculate_trade_cost(mid_price: float, execution_price: float, quantity: float) -> float:
    execution_disadvantage = abs(execution_price - mid_price) * quantity
    total_cost = execution_disadvantage + COMMISSION_PER_TRADE

    return round(total_cost, 4)


# ============================================================
# PORTFOLIO CALCULATIONS
# ============================================================

def calculate_positions_value() -> float:
    positions_value = 0.0

    for position in state["positions"].values():
        positions_value += (
            position["quantity"] *
            position["current_price"]
        )

    return round(positions_value, 2)


def calculate_portfolio_value() -> float:
    return round(
        state["cash"] + calculate_positions_value(),
        2
    )


def calculate_unrealized_pnl_gross() -> float:
    pnl = 0.0

    for position in state["positions"].values():
        pnl += (
            position["current_price"] -
            position["entry_mid_price"]
        ) * position["quantity"]

    return round(pnl, 2)


def calculate_unrealized_pnl_net() -> float:
    pnl = 0.0

    for position in state["positions"].values():
        estimated_exit_price = calculate_sell_execution_price(
            position["current_price"]
        )

        pnl += (
            estimated_exit_price -
            position["entry_execution_price"]
        ) * position["quantity"]

        pnl -= COMMISSION_PER_TRADE

    return round(pnl, 2)


def calculate_total_pnl_gross() -> float:
    return round(
        state["realized_pnl_gross"] + calculate_unrealized_pnl_gross(),
        2
    )


def calculate_total_pnl_net() -> float:
    return round(
        state["realized_pnl_net"] + calculate_unrealized_pnl_net(),
        2
    )


def calculate_return_percent_gross() -> float:
    return round(
        (calculate_total_pnl_gross() / state["initial_cash"]) * 100,
        4
    )


def calculate_return_percent_net() -> float:
    return round(
        (calculate_total_pnl_net() / state["initial_cash"]) * 100,
        4
    )


def calculate_exposure_percent() -> float:
    portfolio_value = calculate_portfolio_value()

    if portfolio_value == 0:
        return 0.0

    return round(
        (calculate_positions_value() / portfolio_value) * 100,
        4
    )


# ============================================================
# RISK / POSITION SIZING
# ============================================================

def calculate_dynamic_quantity(mid_price: float) -> int:
    portfolio_value = calculate_portfolio_value()

    max_risk_amount = portfolio_value * (RISK_PER_TRADE_PERCENT / 100)
    risk_per_share = mid_price * (STOP_LOSS_PERCENT / 100)

    if risk_per_share <= 0:
        return 0

    quantity_by_risk = int(max_risk_amount / risk_per_share)

    max_position_value = portfolio_value * (MAX_POSITION_VALUE_PERCENT / 100)
    quantity_by_exposure = int(max_position_value / mid_price)

    quantity = min(quantity_by_risk, quantity_by_exposure)

    if quantity < MIN_QUANTITY:
        return 0

    return quantity


def calculate_theoretical_stop_price(entry_price: float) -> float:
    return round(
        entry_price * (1 - STOP_LOSS_PERCENT / 100),
        4
    )


def calculate_position_risk_amount(entry_price: float, quantity: int) -> float:
    risk_per_share = entry_price * (STOP_LOSS_PERCENT / 100)

    return round(risk_per_share * quantity, 2)


def calculate_position_risk_percent(entry_price: float, quantity: int) -> float:
    portfolio_value = calculate_portfolio_value()

    if portfolio_value == 0:
        return 0.0

    risk_amount = calculate_position_risk_amount(entry_price, quantity)

    return round(
        (risk_amount / portfolio_value) * 100,
        4
    )


# ============================================================
# EQUITY / DRAWDOWN
# ============================================================

def update_equity_curve(historical_date=None):
    equity = calculate_portfolio_value()

    if equity > state["peak_equity"]:
        state["peak_equity"] = equity

    drawdown_amount = state["peak_equity"] - equity

    if state["peak_equity"] == 0:
        drawdown_percent = 0.0
    else:
        drawdown_percent = (drawdown_amount / state["peak_equity"]) * 100

    if drawdown_amount > state["max_drawdown_amount"]:
        state["max_drawdown_amount"] = round(drawdown_amount, 2)

    if drawdown_percent > state["max_drawdown_percent"]:
        state["max_drawdown_percent"] = round(drawdown_percent, 4)

    state["equity_curve"].append({
        "timestamp": now_utc(),
        "historical_date": historical_date,
        "cycle_count": state["cycle_count"],
        "equity": round(equity, 2),
        "cash": state["cash"],
        "positions_value": calculate_positions_value(),
        "drawdown_amount": round(drawdown_amount, 2),
        "drawdown_percent": round(drawdown_percent, 4),
        "peak_equity": round(state["peak_equity"], 2)
    })


# ============================================================
# PERFORMANCE METRICS
# ============================================================

def round_or_none(value, decimals=4):
    if value is None:
        return None
    return round(value, decimals)


def get_equity_curve_with_dates():
    enriched = []

    for record in state["equity_curve"]:
        new_record = dict(record)

        if record.get("historical_date"):
            new_record["date"] = record["historical_date"]
        else:
            new_record["date"] = record["timestamp"][:10]

        enriched.append(new_record)

    return enriched


def calculate_daily_returns(equity_curve):
    returns = []

    for i in range(1, len(equity_curve)):
        previous = equity_curve[i - 1]["equity"]
        current = equity_curve[i]["equity"]

        if previous != 0:
            returns.append((current / previous) - 1)

    return returns


def calculate_sharpe_ratio(daily_returns, risk_free_rate=0.0):
    if len(daily_returns) < 2:
        return None

    average_return = sum(daily_returns) / len(daily_returns)
    variance = sum((r - average_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        return None

    return (average_return - risk_free_rate / 252) / std_dev * math.sqrt(252)


def calculate_sortino_ratio(daily_returns, risk_free_rate=0.0):
    if len(daily_returns) < 2:
        return None

    average_return = sum(daily_returns) / len(daily_returns)
    downside_returns = [r for r in daily_returns if r < 0]

    if len(downside_returns) == 0:
        return None

    downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
    downside_std = math.sqrt(downside_variance)

    if downside_std == 0:
        return None

    return (average_return - risk_free_rate / 252) / downside_std * math.sqrt(252)


def calculate_cagr(initial_equity, final_equity, num_days):
    if initial_equity <= 0 or num_days <= 0:
        return None

    years = num_days / 252

    if years <= 0:
        return None

    return (final_equity / initial_equity) ** (1 / years) - 1


def calculate_calmar_ratio(cagr, max_drawdown_percent):
    if cagr is None or max_drawdown_percent in (0, None):
        return None

    return cagr / (max_drawdown_percent / 100)


def calculate_monthly_returns(equity_curve):
    if len(equity_curve) < 2:
        return {}

    monthly_first = {}
    monthly_last = {}

    for record in equity_curve:
        date_str = record["date"]
        equity = record["equity"]

        month_key = date_str[:7]

        if month_key not in monthly_first:
            monthly_first[month_key] = equity

        monthly_last[month_key] = equity

    monthly_returns = {}

    for month in monthly_first:
        start_equity = monthly_first[month]
        end_equity = monthly_last[month]

        if start_equity != 0:
            monthly_returns[month] = round(
                (end_equity / start_equity - 1) * 100,
                4
            )

    return monthly_returns


def calculate_performance_metrics():
    equity_curve = get_equity_curve_with_dates()

    if len(equity_curve) < 2:
        return {
            "daily_returns_count": 0,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "cagr": None,
            "cagr_percent": None,
            "calmar_ratio": None,
            "monthly_returns": {},
            "best_month_percent": None,
            "worst_month_percent": None
        }

    daily_returns = calculate_daily_returns(equity_curve)

    initial_equity = equity_curve[0]["equity"]
    final_equity = equity_curve[-1]["equity"]
    num_days = len(equity_curve)

    sharpe_ratio = calculate_sharpe_ratio(daily_returns)
    sortino_ratio = calculate_sortino_ratio(daily_returns)
    cagr = calculate_cagr(initial_equity, final_equity, num_days)
    calmar_ratio = calculate_calmar_ratio(
        cagr,
        state["max_drawdown_percent"]
    )

    monthly_returns = calculate_monthly_returns(equity_curve)

    if len(monthly_returns) > 0:
        best_month_percent = max(monthly_returns.values())
        worst_month_percent = min(monthly_returns.values())
    else:
        best_month_percent = None
        worst_month_percent = None

    return {
        "daily_returns_count": len(daily_returns),
        "sharpe_ratio": round_or_none(sharpe_ratio),
        "sortino_ratio": round_or_none(sortino_ratio),
        "cagr": round_or_none(cagr),
        "cagr_percent": round_or_none(cagr * 100 if cagr is not None else None),
        "calmar_ratio": round_or_none(calmar_ratio),
        "monthly_returns": monthly_returns,
        "best_month_percent": round_or_none(best_month_percent),
        "worst_month_percent": round_or_none(worst_month_percent)
    }


# ============================================================
# BENCHMARK
# ============================================================

def calculate_buy_and_hold_benchmark(symbol: str):
    symbol_prices = [
        record for record in state["price_history"]
        if record.get("symbol") == symbol and record.get("price") is not None
    ]

    if len(symbol_prices) < 2:
        return {
            "status": "not_enough_data",
            "symbol": symbol,
            "benchmark_return_percent": None,
            "benchmark_final_value": None,
            "strategy_final_value": calculate_portfolio_value(),
            "alpha_percent": None
        }

    first_price = symbol_prices[0]["price"]
    last_price = symbol_prices[-1]["price"]

    if first_price == 0:
        benchmark_return_percent = None
        benchmark_final_value = None
    else:
        benchmark_return_percent = round(((last_price / first_price) - 1) * 100, 4)
        benchmark_final_value = round(
            state["initial_cash"] * (last_price / first_price),
            2
        )

    strategy_final_value = calculate_portfolio_value()
    strategy_return_percent = calculate_return_percent_net()

    if benchmark_return_percent is None:
        alpha_percent = None
    else:
        alpha_percent = round(strategy_return_percent - benchmark_return_percent, 4)

    return {
        "status": "ok",
        "symbol": symbol,
        "first_price": first_price,
        "last_price": last_price,
        "benchmark_return_percent": benchmark_return_percent,
        "benchmark_final_value": benchmark_final_value,
        "strategy_return_percent": strategy_return_percent,
        "strategy_final_value": strategy_final_value,
        "alpha_percent": alpha_percent
    }


def build_benchmark_curve(symbol: str):
    symbol_prices = [
        record for record in state["price_history"]
        if record.get("symbol") == symbol and record.get("price") is not None
    ]

    if len(symbol_prices) < 2:
        return []

    first_price = symbol_prices[0]["price"]

    if first_price == 0:
        return []

    benchmark_curve = []

    for record in symbol_prices:
        benchmark_value = state["initial_cash"] * (record["price"] / first_price)

        benchmark_curve.append({
            "historical_date": record.get("historical_date"),
            "cycle_count": record.get("buffer_index"),
            "benchmark_value": round(benchmark_value, 2),
            "price": record["price"]
        })

    return benchmark_curve


# ============================================================
# LOGGING
# ============================================================

def add_trade_log(
    symbol: str,
    action: str,
    quantity: float,
    mid_price: float,
    execution_price: float,
    trade_cost: float,
    reason: str,
    gross_pnl_trade=None,
    net_pnl_trade=None,
    position_risk_amount=None,
    position_risk_percent=None,
    data_provider=None,
    historical_date=None
):
    trade = {
        "trade_id": str(uuid.uuid4()),
        "timestamp": now_utc(),
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "mid_price": mid_price,
        "execution_price": execution_price,
        "trade_cost": trade_cost,
        "gross_pnl_trade": gross_pnl_trade,
        "net_pnl_trade": net_pnl_trade,
        "position_risk_amount": position_risk_amount,
        "position_risk_percent": position_risk_percent,
        "data_provider": data_provider or state["market_data_provider"],
        "historical_date": historical_date,
        "reason": reason
    }

    state["trades"].append(trade)


# ============================================================
# SIGNAL ENGINE
# ============================================================

def generate_signal(symbol: str, price: float, data_provider: str, historical_date=None):
    moving_average = calculate_moving_average(
        symbol,
        MOVING_AVERAGE_WINDOW
    )

    has_position = symbol in state["positions"]

    distance_percent = calculate_distance_from_ma_percent(
        price,
        moving_average
    )

    if moving_average is None:
        signal = "WAIT"
        reason = "Not enough price data"

    elif not has_position:
        if distance_percent >= ENTRY_THRESHOLD_PERCENT:
            signal = "BUY"
            reason = (
                f"Price {price} is {distance_percent}% above "
                f"MA{MOVING_AVERAGE_WINDOW} {moving_average}; "
                f"entry threshold = +{ENTRY_THRESHOLD_PERCENT}%"
            )
        else:
            signal = "WAIT"
            reason = (
                f"Price {price} is {distance_percent}% from "
                f"MA{MOVING_AVERAGE_WINDOW} {moving_average}; "
                f"not enough above entry threshold +{ENTRY_THRESHOLD_PERCENT}%"
            )

    else:
        if USE_TRAILING_STOP:
            position = state["positions"].get(symbol, {})
            trailing_stop_price = position.get("trailing_stop_price")
            highest_price_since_entry = position.get("highest_price_since_entry")

            if distance_percent <= -HARD_MA_EXIT_PERCENT:
                signal = "SELL"
                reason = (
                    f"Hard MA exit: price {price} is {distance_percent}% below "
                    f"MA{MOVING_AVERAGE_WINDOW} {moving_average}; "
                    f"hard exit threshold = -{HARD_MA_EXIT_PERCENT}%"
                )
            else:
                signal = "HOLD"
                reason = (
                    f"Position open; trailing stop active. "
                    f"Price {price} is {distance_percent}% from "
                    f"MA{MOVING_AVERAGE_WINDOW} {moving_average}; "
                    f"hard MA exit not reached. "
                    f"Highest price since entry = {highest_price_since_entry}; "
                    f"Trailing stop = {trailing_stop_price}"
                )

        elif distance_percent <= -EXIT_THRESHOLD_PERCENT:
            signal = "SELL"
            reason = (
                f"Price {price} is {distance_percent}% below "
                f"MA{MOVING_AVERAGE_WINDOW} {moving_average}; "
                f"exit threshold = -{EXIT_THRESHOLD_PERCENT}%"
            )

        else:
            signal = "HOLD"
            reason = (
                f"Position open; price {price} is {distance_percent}% from "
                f"MA{MOVING_AVERAGE_WINDOW} {moving_average}; "
                f"exit threshold not reached"
            )
    signal_log = {
        "signal_id": str(uuid.uuid4()),
        "timestamp": now_utc(),
        "symbol": symbol,
        "price": price,
        "data_provider": data_provider,
        "historical_date": historical_date,
        "moving_average_window": MOVING_AVERAGE_WINDOW,
        "moving_average": moving_average,
        "distance_from_ma_percent": distance_percent,
        "entry_threshold_percent": ENTRY_THRESHOLD_PERCENT,
        "exit_threshold_percent": EXIT_THRESHOLD_PERCENT,
        "signal": signal,
        "reason": reason
    }

    state["signals"].append(signal_log)

    return signal_log


# ============================================================
# EXECUTION ENGINE
# ============================================================

def execute_buy_if_signal_buy(signal_log):
    if signal_log["signal"] != "BUY":
        return

    symbol = signal_log["symbol"]
    mid_price = signal_log["price"]

    if symbol in state["positions"]:
        return

    quantity = calculate_dynamic_quantity(mid_price)

    if quantity <= 0:
        add_trade_log(
            symbol=symbol,
            action="REJECTED_BUY",
            quantity=0,
            mid_price=mid_price,
            execution_price=mid_price,
            trade_cost=0.0,
            reason="Calculated quantity is zero",
            data_provider=signal_log["data_provider"],
            historical_date=signal_log.get("historical_date")
        )
        return

    execution_price = calculate_buy_execution_price(mid_price)
    trade_cost = calculate_trade_cost(mid_price, execution_price, quantity)

    total_cash_needed = (quantity * execution_price) + COMMISSION_PER_TRADE

    if state["cash"] < total_cash_needed:
        add_trade_log(
            symbol=symbol,
            action="REJECTED_BUY",
            quantity=quantity,
            mid_price=mid_price,
            execution_price=execution_price,
            trade_cost=trade_cost,
            reason="Not enough cash",
            data_provider=signal_log["data_provider"],
            historical_date=signal_log.get("historical_date")
        )
        return

    stop_price = calculate_theoretical_stop_price(execution_price)
    position_risk_amount = calculate_position_risk_amount(execution_price, quantity)
    position_risk_percent = calculate_position_risk_percent(execution_price, quantity)

    state["cash"] = round(
        state["cash"] - total_cash_needed,
        2
    )

    state["total_trading_costs"] = round(
        state["total_trading_costs"] + trade_cost,
        4
    )

    state["positions"][symbol] = {
        "symbol": symbol,
        "quantity": quantity,

        "entry_mid_price": mid_price,
        "entry_execution_price": execution_price,

        "current_price": mid_price,
        "opened_at": now_utc(),

        "theoretical_stop_price": stop_price,
        "position_risk_amount": position_risk_amount,
        "position_risk_percent": position_risk_percent,

        "entry_trade_cost": trade_cost,
        "data_provider": signal_log["data_provider"],
        "entry_historical_date": signal_log.get("historical_date"),
        "highest_price_since_entry": mid_price,
        "trailing_stop_price": round(
            mid_price * (1 - TRAILING_STOP_PERCENT / 100), 4),
    }

    add_trade_log(
        symbol=symbol,
        action="BUY",
        quantity=quantity,
        mid_price=mid_price,
        execution_price=execution_price,
        trade_cost=trade_cost,
        position_risk_amount=position_risk_amount,
        position_risk_percent=position_risk_percent,
        data_provider=signal_log["data_provider"],
        historical_date=signal_log.get("historical_date"),
        reason=(
            f"{signal_log['reason']} | "
            f"Dynamic quantity = {quantity} | "
            f"Theoretical stop = {stop_price} | "
            f"Risk = {position_risk_amount} "
            f"({position_risk_percent}%)"
        )
    )


def execute_sell_if_signal_sell(signal_log):
    if signal_log["signal"] != "SELL":
        return

    symbol = signal_log["symbol"]
    mid_price = signal_log["price"]

    if symbol not in state["positions"]:
        return

    close_position(
        symbol=symbol,
        mid_price=mid_price,
        data_provider=signal_log["data_provider"],
        historical_date=signal_log.get("historical_date"),
        close_reason=signal_log["reason"],
        close_action="SELL"
    )


def close_position(
    symbol: str,
    mid_price: float,
    data_provider: str,
    historical_date=None,
    close_reason="Manual close",
    close_action="SELL"
):
    if symbol not in state["positions"]:
        return None

    position = state["positions"][symbol]

    quantity = position["quantity"]
    entry_mid_price = position["entry_mid_price"]
    entry_execution_price = position["entry_execution_price"]

    execution_price = calculate_sell_execution_price(mid_price)
    trade_cost = calculate_trade_cost(mid_price, execution_price, quantity)

    proceeds = quantity * execution_price

    gross_pnl_trade = (
        mid_price - entry_mid_price
    ) * quantity

    net_pnl_trade = (
        execution_price - entry_execution_price
    ) * quantity

    net_pnl_trade -= COMMISSION_PER_TRADE

    state["cash"] = round(
        state["cash"] + proceeds - COMMISSION_PER_TRADE,
        2
    )

    state["realized_pnl_gross"] = round(
        state["realized_pnl_gross"] + gross_pnl_trade,
        2
    )

    state["realized_pnl_net"] = round(
        state["realized_pnl_net"] + net_pnl_trade,
        2
    )

    state["total_trading_costs"] = round(
        state["total_trading_costs"] + trade_cost,
        4
    )

    del state["positions"][symbol]

    add_trade_log(
        symbol=symbol,
        action=close_action,
        quantity=quantity,
        mid_price=mid_price,
        execution_price=execution_price,
        trade_cost=trade_cost,
        gross_pnl_trade=round(gross_pnl_trade, 2),
        net_pnl_trade=round(net_pnl_trade, 2),
        data_provider=data_provider,
        historical_date=historical_date,
        reason=(
            f"{close_reason} | "
            f"Gross PnL = {round(gross_pnl_trade, 2)} | "
            f"Net PnL = {round(net_pnl_trade, 2)}"
        )
    )

    return {
        "symbol": symbol,
        "quantity": quantity,
        "mid_price": mid_price,
        "execution_price": execution_price,
        "gross_pnl_trade": round(gross_pnl_trade, 2),
        "net_pnl_trade": round(net_pnl_trade, 2),
        "historical_date": historical_date,
        "close_action": close_action
    }


def force_close_all_positions(symbol: str, reason="Forced close at end of backtest"):
    closed = []

    if symbol not in state["positions"]:
        return closed

    current_price = get_current_price(symbol)

    historical_date = None

    if len(state["price_history"]) > 0:
        last_record = state["price_history"][-1]
        historical_date = last_record.get("historical_date")

    close_result = close_position(
        symbol=symbol,
        mid_price=current_price,
        data_provider="FMP_HISTORY_BUFFER",
        historical_date=historical_date,
        close_reason=reason,
        close_action="FORCED_SELL"
    )

    if close_result is not None:
        closed.append(close_result)

    update_equity_curve(historical_date=historical_date)

    return closed


def update_positions_prices(symbol: str, price: float):
    if symbol not in state["positions"]:
        return

    position = state["positions"][symbol]
    position["current_price"] = price

    if USE_TRAILING_STOP:
        if price > position["highest_price_since_entry"]:
            position["highest_price_since_entry"] = price

        position["trailing_stop_price"] = round(
            position["highest_price_since_entry"] *
            (1 - TRAILING_STOP_PERCENT / 100),
            4
        )
def execute_trailing_stop_if_needed(symbol: str, price: float, data_provider: str, historical_date=None):
    if not USE_TRAILING_STOP:
        return None

    if symbol not in state["positions"]:
        return None

    position = state["positions"][symbol]
    trailing_stop_price = position.get("trailing_stop_price")

    if trailing_stop_price is None:
        return None

    if price <= trailing_stop_price:
        return close_position(
            symbol=symbol,
            mid_price=price,
            data_provider=data_provider,
            historical_date=historical_date,
            close_reason=(
                f"TRAILING STOP triggered: price {price} <= "
                f"trailing stop {trailing_stop_price}"
            ),
            close_action="TRAILING_STOP_SELL"
        )

    return None

# ============================================================
# STATS / REPORTING
# ============================================================

def get_closed_trades():
    return [
        trade for trade in state["trades"]
        if trade["action"] in ["SELL", "FORCED_SELL", "TRAILING_STOP_SELL"]
    ]


def calculate_trade_stats():
    buy_count = 0
    sell_count = 0
    forced_sell_count = 0
    rejected_buy_count = 0

    for trade in state["trades"]:
        if trade["action"] == "BUY":
            buy_count += 1
        elif trade["action"] == "SELL":
            sell_count += 1
        elif trade["action"] == "FORCED_SELL":
            forced_sell_count += 1
        elif trade["action"] == "TRAILING_STOP_SELL":
            sell_count += 1
        elif trade["action"] == "REJECTED_BUY":
            rejected_buy_count += 1

    closed_trades = get_closed_trades()

    winning_trades = [
        trade for trade in closed_trades
        if trade["net_pnl_trade"] is not None and trade["net_pnl_trade"] > 0
    ]

    losing_trades = [
        trade for trade in closed_trades
        if trade["net_pnl_trade"] is not None and trade["net_pnl_trade"] < 0
    ]

    closed_trades_count = len(closed_trades)

    if closed_trades_count == 0:
        win_rate_percent = 0.0
    else:
        win_rate_percent = round(
            (len(winning_trades) / closed_trades_count) * 100,
            4
        )

    gross_profit_net = sum(
        trade["net_pnl_trade"]
        for trade in winning_trades
    )

    gross_loss_net = abs(sum(
        trade["net_pnl_trade"]
        for trade in losing_trades
    ))

    if gross_loss_net == 0:
        profit_factor_net = None
    else:
        profit_factor_net = round(gross_profit_net / gross_loss_net, 4)

    if len(winning_trades) == 0:
        average_win_net = 0.0
    else:
        average_win_net = round(
            gross_profit_net / len(winning_trades),
            2
        )

    if len(losing_trades) == 0:
        average_loss_net = 0.0
    else:
        average_loss_net = round(
            gross_loss_net / len(losing_trades),
            2
        )

    if closed_trades_count == 0:
        expectancy_net = 0.0
    else:
        expectancy_net = round(
            sum(trade["net_pnl_trade"] for trade in closed_trades) / closed_trades_count,
            2
        )

    best_trade = None
    worst_trade = None

    if len(closed_trades) > 0:
        best_trade = max(
            closed_trades,
            key=lambda x: x["net_pnl_trade"] if x["net_pnl_trade"] is not None else -10**18
        )

        worst_trade = min(
            closed_trades,
            key=lambda x: x["net_pnl_trade"] if x["net_pnl_trade"] is not None else 10**18
        )

    return {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "forced_sell_count": forced_sell_count,
        "rejected_buy_count": rejected_buy_count,
        "open_positions_count": len(state["positions"]),
        "closed_trades_count": closed_trades_count,
        "winning_trades_count": len(winning_trades),
        "losing_trades_count": len(losing_trades),
        "win_rate_percent": win_rate_percent,
        "profit_factor_net": profit_factor_net,
        "average_win_net": average_win_net,
        "average_loss_net": average_loss_net,
        "expectancy_net": expectancy_net,
        "best_trade": best_trade,
        "worst_trade": worst_trade
    }


def build_backtest_report(symbol: str, requested_cycles=None, executed_cycles=None, forced_closes=None):
    buffer = state["historical_buffers"].get(symbol, [])
    index = state["historical_buffer_indexes"].get(symbol, 0)

    first_date = None
    last_date = None

    symbol_prices = [
        record for record in state["price_history"]
        if record.get("symbol") == symbol
    ]

    if len(symbol_prices) > 0:
        first_date = symbol_prices[0].get("historical_date")
        last_date = symbol_prices[-1].get("historical_date")

    performance_metrics = calculate_performance_metrics()
    benchmark = calculate_buy_and_hold_benchmark(symbol)

    return {
        "project": "DragonLady",
        "version": DRAGONLADY_VERSION,
        "mode": DRAGONLADY_MODE,
        "symbol": symbol,

        "backtest_period": {
            "first_historical_date": first_date,
            "last_historical_date": last_date,
            "records_loaded": len(buffer),
            "records_consumed": index,
            "records_remaining": max(0, len(buffer) - index),
            "requested_cycles": requested_cycles,
            "executed_cycles": executed_cycles
        },

        "portfolio_summary": {
            "initial_cash": state["initial_cash"],
            "cash": state["cash"],
            "positions_value": calculate_positions_value(),
            "portfolio_value": calculate_portfolio_value(),
            "exposure_percent": calculate_exposure_percent()
        },

        "pnl_summary": {
            "realized_pnl_gross": state["realized_pnl_gross"],
            "realized_pnl_net": state["realized_pnl_net"],
            "unrealized_pnl_gross": calculate_unrealized_pnl_gross(),
            "unrealized_pnl_net": calculate_unrealized_pnl_net(),
            "total_pnl_gross": calculate_total_pnl_gross(),
            "total_pnl_net": calculate_total_pnl_net(),
            "return_percent_gross": calculate_return_percent_gross(),
            "return_percent_net": calculate_return_percent_net(),
            "total_trading_costs": state["total_trading_costs"]
        },

        "risk_summary": {
            "peak_equity": round(state["peak_equity"], 2),
            "max_drawdown_amount": state["max_drawdown_amount"],
            "max_drawdown_percent": state["max_drawdown_percent"]
        },

        "benchmark_summary": benchmark,
        "performance_metrics": performance_metrics,
        "trade_stats": calculate_trade_stats(),
        "forced_closes": forced_closes or []
    }


# ============================================================
# MAIN CYCLE / BACKTEST
# ============================================================

def run_dragonlady_cycle(symbol: str):
    state["cycle_count"] += 1
    state["last_cycle"] = now_utc()

    price_record = fetch_next_market_price(symbol)

    if price_record.get("status") in ["error", "finished"]:
        return {
            "status": price_record.get("status"),
            "mode": DRAGONLADY_MODE,
            "cycle_count": state["cycle_count"],
            "timestamp": state["last_cycle"],
            "symbol": symbol,
            "market_data_provider": state["market_data_provider"],
            "price_record": price_record,
            "message": price_record.get("message")
        }

    new_price = price_record["price"]
    data_provider = price_record["provider"]
    historical_date = price_record.get("historical_date")

    update_positions_prices(symbol, new_price)
    trailing_stop_result = execute_trailing_stop_if_needed(
        symbol=symbol,
        price=new_price,
        data_provider=data_provider,
        historical_date=historical_date
    )

    if trailing_stop_result is not None:
        update_equity_curve(historical_date=historical_date)

        return {
            "status": "cycle_completed",
            "mode": DRAGONLADY_MODE,
            "cycle_count": state["cycle_count"],
            "timestamp": state["last_cycle"],
            "symbol": symbol,
            "market_data_provider": state["market_data_provider"],
            "actual_data_provider_used": data_provider,
            "price_record": price_record,
            "signal": "TRAILING_STOP_SELL",
            "signal_reason": trailing_stop_result["close_action"],
            "portfolio_value": calculate_portfolio_value(),
            "total_pnl_net": calculate_total_pnl_net(),
            "return_percent_net": calculate_return_percent_net(),
            "trade_stats": calculate_trade_stats()
        }
    signal_log = generate_signal(
        symbol=symbol,
        price=new_price,
        data_provider=data_provider,
        historical_date=historical_date
    )

    execute_buy_if_signal_buy(signal_log)
    execute_sell_if_signal_sell(signal_log)

    update_equity_curve(historical_date=historical_date)

    return {
        "status": "cycle_completed",
        "mode": DRAGONLADY_MODE,
        "cycle_count": state["cycle_count"],
        "timestamp": state["last_cycle"],

        "symbol": symbol,
        "market_data_provider": state["market_data_provider"],
        "actual_data_provider_used": data_provider,
        "price_record": price_record,

        "signal": signal_log["signal"],
        "signal_reason": signal_log["reason"],
        "moving_average": signal_log["moving_average"],
        "distance_from_ma_percent": signal_log["distance_from_ma_percent"],

        "portfolio_value": calculate_portfolio_value(),
        "total_pnl_net": calculate_total_pnl_net(),
        "return_percent_net": calculate_return_percent_net(),

        "trade_stats": calculate_trade_stats()
    }


def run_historical_test_internal(symbol: str, cycles: int):
    symbol = symbol.upper()
    cycles = max(1, int(cycles))

    results = []

    for _ in range(cycles):
        result = run_dragonlady_cycle(symbol)
        results.append(result)

        if result.get("status") in ["error", "finished"]:
            break

    executed_cycles = len([
        r for r in results
        if r.get("status") == "cycle_completed"
    ])

    return {
        "status": "historical_test_completed",
        "symbol": symbol,
        "requested_cycles": cycles,
        "executed_cycles": executed_cycles,
        "market_data_provider": state["market_data_provider"],
        "last_result": results[-1] if len(results) > 0 else None,
        "results": results,
        "report": build_backtest_report(
            symbol=symbol,
            requested_cycles=cycles,
            executed_cycles=executed_cycles
        )
    }


def run_full_backtest_internal(symbol: str, reset_index: bool = True, reset_portfolio: bool = True, close_end_position: bool = True):
    symbol = symbol.upper()

    if symbol not in state["historical_buffers"]:
        return {
            "status": "error",
            "symbol": symbol,
            "message": "No historical buffer loaded. Use /dragonlady/load_fmp_history first."
        }

    if reset_portfolio:
        reset_dragonlady_core(preserve_buffers=True)

    if reset_index:
        state["historical_buffer_indexes"][symbol] = 0

    previous_provider = state["market_data_provider"]
    state["market_data_provider"] = "FMP_HISTORY_BUFFER"

    results = []

    while True:
        result = run_dragonlady_cycle(symbol)
        results.append(result)

        if result.get("status") == "finished":
            break

        if result.get("status") == "error":
            break

    forced_closes = []

    if close_end_position:
        forced_closes = force_close_all_positions(
            symbol=symbol,
            reason="Forced close at end of full backtest"
        )

    state["market_data_provider"] = previous_provider

    executed_cycles = len([
        r for r in results
        if r.get("status") == "cycle_completed"
    ])

    return {
        "status": "full_backtest_completed",
        "symbol": symbol,
        "executed_cycles": executed_cycles,
        "close_end_position": close_end_position,
        "last_result": results[-1] if len(results) > 0 else None,
        "report": build_backtest_report(
            symbol=symbol,
            requested_cycles="FULL_BUFFER",
            executed_cycles=executed_cycles,
            forced_closes=forced_closes
        )
    }


# ============================================================
# RESET HELPERS
# ============================================================

def reset_dragonlady_core(preserve_buffers: bool = True):
    selected_provider = state["market_data_provider"]

    historical_buffers = state["historical_buffers"] if preserve_buffers else {}
    historical_buffer_indexes = {
        symbol: 0
        for symbol in historical_buffers.keys()
    }

    state["cash"] = 100_000.0
    state["initial_cash"] = 100_000.0

    state["realized_pnl_gross"] = 0.0
    state["realized_pnl_net"] = 0.0
    state["total_trading_costs"] = 0.0

    state["positions"] = {}
    state["trades"] = []
    state["signals"] = []
    state["api_usage"] = []

    state["market_data_provider"] = selected_provider
    state["market_prices"] = {
        DEFAULT_SYMBOL: INITIAL_PRICE
    }
    state["price_history"] = []

    state["historical_buffers"] = historical_buffers
    state["historical_buffer_indexes"] = historical_buffer_indexes

    state["equity_curve"] = []
    state["peak_equity"] = 100_000.0
    state["max_drawdown_amount"] = 0.0
    state["max_drawdown_percent"] = 0.0

    state["last_cycle"] = None
    state["cycle_count"] = 0


# ============================================================
# CHARTS
# ============================================================
def diagnose_symbol_quality(walk_forward_results: dict) -> dict:
    agg = walk_forward_results.get("aggregate_results", {})

    avg_return = agg.get("average_return_percent_net", 0)
    avg_sharpe = agg.get("average_sharpe_ratio", 0)
    avg_alpha = agg.get("average_alpha_percent", 0)
    avg_drawdown = agg.get("average_max_drawdown_percent", 999)

    benchmark_return = avg_return - avg_alpha
    capture_ratio = (
        avg_return / benchmark_return
        if benchmark_return not in [0, None]
        else 0
    )

    if avg_sharpe >= 1 and avg_return > 0:
        status = "GOOD_FIT"
        recommendation = "Trade at full allocation"
        allocation_multiplier = 1.5

    elif avg_sharpe >= 0.5 and avg_return > 0:
        status = "UNDERCAPTURE"
        recommendation = "Trade at reduced allocation"
        allocation_multiplier = 1.0

    else:
        status = "BAD_FIT"
        recommendation = "Skip this symbol"
        allocation_multiplier = 0.0

    return {
        "status": status,
        "recommendation": recommendation,
        "allocation_multiplier": allocation_multiplier,
        "average_return_percent_net": round(avg_return, 4),
        "average_sharpe_ratio": round(avg_sharpe, 4),
        "average_alpha_percent": round(avg_alpha, 4),
        "average_max_drawdown_percent": round(avg_drawdown, 4),
        "capture_ratio": round(capture_ratio, 4),
    }
def generate_equity_curve_chart():
    if len(state["equity_curve"]) == 0:
        return None

    x_values = list(range(1, len(state["equity_curve"]) + 1))
    y_values = [record["equity"] for record in state["equity_curve"]]

    plt.figure(figsize=(12, 6))
    plt.plot(x_values, y_values, linewidth=2)

    plt.title("DragonLady Equity Curve")
    plt.xlabel("Cycle")
    plt.ylabel("Portfolio Value ($)")
    plt.grid(True)

    initial_equity = state["initial_cash"]
    plt.axhline(initial_equity, linestyle="--", linewidth=1)

    plt.tight_layout()
    plt.savefig(CHART_EQUITY_FILE)
    plt.close()

    return CHART_EQUITY_FILE


def generate_drawdown_chart():
    if len(state["equity_curve"]) == 0:
        return None

    x_values = list(range(1, len(state["equity_curve"]) + 1))
    y_values = [record["drawdown_percent"] for record in state["equity_curve"]]

    plt.figure(figsize=(12, 6))
    plt.plot(x_values, y_values, linewidth=2)

    plt.title("DragonLady Drawdown Curve")
    plt.xlabel("Cycle")
    plt.ylabel("Drawdown (%)")
    plt.grid(True)

    plt.tight_layout()
    plt.savefig(CHART_DRAWDOWN_FILE)
    plt.close()

    return CHART_DRAWDOWN_FILE


def generate_benchmark_chart(symbol: str):
    if len(state["equity_curve"]) == 0:
        return None

    benchmark_curve = build_benchmark_curve(symbol)

    if len(benchmark_curve) == 0:
        return None

    x_strategy = list(range(1, len(state["equity_curve"]) + 1))
    y_strategy = [record["equity"] for record in state["equity_curve"]]

    x_benchmark = list(range(1, len(benchmark_curve) + 1))
    y_benchmark = [record["benchmark_value"] for record in benchmark_curve]

    min_length = min(len(x_strategy), len(x_benchmark))

    x_values = list(range(1, min_length + 1))
    y_strategy = y_strategy[:min_length]
    y_benchmark = y_benchmark[:min_length]

    plt.figure(figsize=(12, 6))
    plt.plot(x_values, y_strategy, linewidth=2, label="DragonLady")
    plt.plot(x_values, y_benchmark, linewidth=2, label="Buy & Hold")

    plt.title(f"DragonLady vs Buy & Hold - {symbol}")
    plt.xlabel("Cycle")
    plt.ylabel("Portfolio Value ($)")
    plt.grid(True)
    plt.legend()

    plt.tight_layout()
    plt.savefig(CHART_BENCHMARK_FILE)
    plt.close()

    return CHART_BENCHMARK_FILE
def optimize_exposure_internal(symbol: str):
    global MAX_POSITION_VALUE_PERCENT

    symbol = symbol.upper()

    if symbol not in state["historical_buffers"]:
        return {
            "status": "error",
            "symbol": symbol,
            "message": "No historical buffer loaded. Use /dragonlady/load_fmp_history first."
        }

    original_max_position_value_percent = MAX_POSITION_VALUE_PERCENT
    original_provider = state["market_data_provider"]

    optimization_results = []

    for exposure in EXPOSURE_OPTIMIZATION_VALUES:
        MAX_POSITION_VALUE_PERCENT = exposure

        reset_dragonlady_core(preserve_buffers=True)
        state["historical_buffer_indexes"][symbol] = 0
        state["market_data_provider"] = "FMP_HISTORY_BUFFER"

        backtest_result = run_full_backtest_internal(
            symbol=symbol,
            reset_index=True,
            reset_portfolio=False,
            close_end_position=True
        )

        report = backtest_result.get("report", {})
        pnl_summary = report.get("pnl_summary", {})
        risk_summary = report.get("risk_summary", {})
        benchmark_summary = report.get("benchmark_summary", {})
        performance_metrics = report.get("performance_metrics", {})
        trade_stats = report.get("trade_stats", {})

        optimization_results.append({
            "max_position_value_percent": exposure,
            "return_percent_net": pnl_summary.get("return_percent_net"),
            "cagr_percent": performance_metrics.get("cagr_percent"),
            "sharpe_ratio": performance_metrics.get("sharpe_ratio"),
            "sortino_ratio": performance_metrics.get("sortino_ratio"),
            "calmar_ratio": performance_metrics.get("calmar_ratio"),
            "max_drawdown_percent": risk_summary.get("max_drawdown_percent"),
            "profit_factor_net": trade_stats.get("profit_factor_net"),
            "win_rate_percent": trade_stats.get("win_rate_percent"),
            "closed_trades_count": trade_stats.get("closed_trades_count"),
            "alpha_percent": benchmark_summary.get("alpha_percent"),
            "portfolio_value": report.get("portfolio_summary", {}).get("portfolio_value")
        })

    MAX_POSITION_VALUE_PERCENT = original_max_position_value_percent
    state["market_data_provider"] = original_provider

    best_by_return = max(
        optimization_results,
        key=lambda x: x["return_percent_net"] if x["return_percent_net"] is not None else -10**18
    )

    best_by_sharpe = max(
        optimization_results,
        key=lambda x: x["sharpe_ratio"] if x["sharpe_ratio"] is not None else -10**18
    )

    best_by_calmar = max(
        optimization_results,
        key=lambda x: x["calmar_ratio"] if x["calmar_ratio"] is not None else -10**18
    )

    best_by_low_drawdown = min(
        optimization_results,
        key=lambda x: x["max_drawdown_percent"] if x["max_drawdown_percent"] is not None else 10**18
    )

    return {
        "status": "exposure_optimization_completed",
        "symbol": symbol,
        "version": DRAGONLADY_VERSION,
        "tested_exposures": EXPOSURE_OPTIMIZATION_VALUES,
        "best_by_return": best_by_return,
        "best_by_sharpe": best_by_sharpe,
        "best_by_calmar": best_by_calmar,
        "best_by_low_drawdown": best_by_low_drawdown,
        "all_results": optimization_results
    }
def optimize_risk_internal(symbol: str):
    global RISK_PER_TRADE_PERCENT

    symbol = symbol.upper()

    if symbol not in state["historical_buffers"]:
        return {
            "status": "error",
            "symbol": symbol,
            "message": "No historical buffer loaded. Use /dragonlady/load_fmp_history first."
        }

    original_risk_per_trade_percent = RISK_PER_TRADE_PERCENT
    original_provider = state["market_data_provider"]

    optimization_results = []

    for risk_percent in RISK_OPTIMIZATION_VALUES:
        RISK_PER_TRADE_PERCENT = risk_percent

        reset_dragonlady_core(preserve_buffers=True)
        state["historical_buffer_indexes"][symbol] = 0
        state["market_data_provider"] = "FMP_HISTORY_BUFFER"

        backtest_result = run_full_backtest_internal(
            symbol=symbol,
            reset_index=True,
            reset_portfolio=False,
            close_end_position=True
        )

        report = backtest_result.get("report", {})
        pnl_summary = report.get("pnl_summary", {})
        risk_summary = report.get("risk_summary", {})
        benchmark_summary = report.get("benchmark_summary", {})
        performance_metrics = report.get("performance_metrics", {})
        trade_stats = report.get("trade_stats", {})

        optimization_results.append({
            "risk_per_trade_percent": risk_percent,
            "max_position_value_percent": MAX_POSITION_VALUE_PERCENT,
            "return_percent_net": pnl_summary.get("return_percent_net"),
            "cagr_percent": performance_metrics.get("cagr_percent"),
            "sharpe_ratio": performance_metrics.get("sharpe_ratio"),
            "sortino_ratio": performance_metrics.get("sortino_ratio"),
            "calmar_ratio": performance_metrics.get("calmar_ratio"),
            "max_drawdown_percent": risk_summary.get("max_drawdown_percent"),
            "profit_factor_net": trade_stats.get("profit_factor_net"),
            "win_rate_percent": trade_stats.get("win_rate_percent"),
            "closed_trades_count": trade_stats.get("closed_trades_count"),
            "alpha_percent": benchmark_summary.get("alpha_percent"),
            "portfolio_value": report.get("portfolio_summary", {}).get("portfolio_value")
        })

    RISK_PER_TRADE_PERCENT = original_risk_per_trade_percent
    state["market_data_provider"] = original_provider

    best_by_return = max(
        optimization_results,
        key=lambda x: x["return_percent_net"] if x["return_percent_net"] is not None else -10**18
    )

    best_by_sharpe = max(
        optimization_results,
        key=lambda x: x["sharpe_ratio"] if x["sharpe_ratio"] is not None else -10**18
    )

    best_by_calmar = max(
        optimization_results,
        key=lambda x: x["calmar_ratio"] if x["calmar_ratio"] is not None else -10**18
    )

    best_by_low_drawdown = min(
        optimization_results,
        key=lambda x: x["max_drawdown_percent"] if x["max_drawdown_percent"] is not None else 10**18
    )

    return {
        "status": "risk_optimization_completed",
        "symbol": symbol,
        "version": DRAGONLADY_VERSION,
        "tested_risks": RISK_OPTIMIZATION_VALUES,
        "fixed_max_position_value_percent": MAX_POSITION_VALUE_PERCENT,
        "best_by_return": best_by_return,
        "best_by_sharpe": best_by_sharpe,
        "best_by_calmar": best_by_calmar,
        "best_by_low_drawdown": best_by_low_drawdown,
        "all_results": optimization_results
    }
def optimize_strategy_internal(symbol: str):
    global MOVING_AVERAGE_WINDOW
    global ENTRY_THRESHOLD_PERCENT
    global EXIT_THRESHOLD_PERCENT

    symbol = symbol.upper()

    if symbol not in state["historical_buffers"]:
        return {
            "status": "error",
            "message": "No historical buffer loaded."
        }

    original_ma = MOVING_AVERAGE_WINDOW
    original_entry = ENTRY_THRESHOLD_PERCENT
    original_exit = EXIT_THRESHOLD_PERCENT
    original_provider = state["market_data_provider"]

    results = []

    for ma in STRATEGY_OPTIMIZATION_GRID["moving_average_window"]:
        for entry in STRATEGY_OPTIMIZATION_GRID["entry_threshold_percent"]:
            for exit_ in STRATEGY_OPTIMIZATION_GRID["exit_threshold_percent"]:

                MOVING_AVERAGE_WINDOW = ma
                ENTRY_THRESHOLD_PERCENT = entry
                EXIT_THRESHOLD_PERCENT = exit_

                reset_dragonlady_core(preserve_buffers=True)
                state["historical_buffer_indexes"][symbol] = 0
                state["market_data_provider"] = "FMP_HISTORY_BUFFER"

                backtest = run_full_backtest_internal(
                    symbol=symbol,
                    reset_index=True,
                    reset_portfolio=False,
                    close_end_position=True
                )

                report = backtest.get("report", {})
                pnl = report.get("pnl_summary", {})
                risk = report.get("risk_summary", {})
                perf = report.get("performance_metrics", {})
                bench = report.get("benchmark_summary", {})
                stats = report.get("trade_stats", {})

                results.append({
                    "moving_average_window": ma,
                    "entry_threshold_percent": entry,
                    "exit_threshold_percent": exit_,

                    "return_percent_net": pnl.get("return_percent_net"),
                    "cagr_percent": perf.get("cagr_percent"),
                    "sharpe_ratio": perf.get("sharpe_ratio"),
                    "sortino_ratio": perf.get("sortino_ratio"),
                    "calmar_ratio": perf.get("calmar_ratio"),
                    "max_drawdown_percent": risk.get("max_drawdown_percent"),
                    "profit_factor_net": stats.get("profit_factor_net"),
                    "win_rate_percent": stats.get("win_rate_percent"),
                    "closed_trades_count": stats.get("closed_trades_count"),
                    "alpha_percent": bench.get("alpha_percent"),
                    "portfolio_value": report.get("portfolio_summary", {}).get("portfolio_value")
                })

    # Restore original settings
    MOVING_AVERAGE_WINDOW = original_ma
    ENTRY_THRESHOLD_PERCENT = original_entry
    EXIT_THRESHOLD_PERCENT = original_exit
    state["market_data_provider"] = original_provider

    best_by_return = max(
        results,
        key=lambda x: x["return_percent_net"] if x["return_percent_net"] is not None else -10**18
    )

    best_by_sharpe = max(
        results,
        key=lambda x: x["sharpe_ratio"] if x["sharpe_ratio"] is not None else -10**18
    )

    best_by_calmar = max(
        results,
        key=lambda x: x["calmar_ratio"] if x["calmar_ratio"] is not None else -10**18
    )

    best_by_alpha = max(
        results,
        key=lambda x: x["alpha_percent"] if x["alpha_percent"] is not None else -10**18
    )

    return {
        "status": "strategy_optimization_completed",
        "symbol": symbol,
        "version": DRAGONLADY_VERSION,
        "total_combinations_tested": len(results),
        "best_by_return": best_by_return,
        "best_by_sharpe": best_by_sharpe,
        "best_by_calmar": best_by_calmar,
        "best_by_alpha": best_by_alpha,
        "all_results": results
    }
def run_multi_symbol_validation_internal(symbols=None, limit: int = FMP_DEFAULT_HISTORY_LIMIT):
    if symbols is None:
        symbols = MULTI_SYMBOL_TEST_LIST

    validation_results = []

    original_provider = state["market_data_provider"]

    for symbol in symbols:
        symbol = symbol.upper()

        load_result = load_fmp_history_into_buffer(
            symbol=symbol,
            limit=limit
        )

        if load_result.get("status") != "ok":
            validation_results.append({
                "symbol": symbol,
                "status": "load_failed",
                "message": load_result.get("message"),
                "records_loaded": load_result.get("records_loaded", 0)
            })
            continue

        reset_dragonlady_core(preserve_buffers=True)

        if symbol not in state["historical_buffers"]:
            validation_results.append({
                "symbol": symbol,
                "status": "buffer_missing_after_reset"
            })
            continue

        state["historical_buffer_indexes"][symbol] = 0
        state["market_data_provider"] = "FMP_HISTORY_BUFFER"

        backtest_result = run_full_backtest_internal(
            symbol=symbol,
            reset_index=True,
            reset_portfolio=False,
            close_end_position=True
        )

        report = backtest_result.get("report", {})
        pnl = report.get("pnl_summary", {})
        risk = report.get("risk_summary", {})
        perf = report.get("performance_metrics", {})
        bench = report.get("benchmark_summary", {})
        stats = report.get("trade_stats", {})
        portfolio = report.get("portfolio_summary", {})

        validation_results.append({
            "symbol": symbol,
            "status": "ok",
            "records_loaded": load_result.get("records_loaded"),
            "first_historical_date": report.get("backtest_period", {}).get("first_historical_date"),
            "last_historical_date": report.get("backtest_period", {}).get("last_historical_date"),

            "strategy_return_percent_net": pnl.get("return_percent_net"),
            "strategy_final_value": portfolio.get("portfolio_value"),

            "benchmark_return_percent": bench.get("benchmark_return_percent"),
            "benchmark_final_value": bench.get("benchmark_final_value"),
            "alpha_percent": bench.get("alpha_percent"),

            "cagr_percent": perf.get("cagr_percent"),
            "sharpe_ratio": perf.get("sharpe_ratio"),
            "sortino_ratio": perf.get("sortino_ratio"),
            "calmar_ratio": perf.get("calmar_ratio"),
            "max_drawdown_percent": risk.get("max_drawdown_percent"),

            "profit_factor_net": stats.get("profit_factor_net"),
            "win_rate_percent": stats.get("win_rate_percent"),
            "closed_trades_count": stats.get("closed_trades_count"),
            "best_trade": stats.get("best_trade"),
            "worst_trade": stats.get("worst_trade")
        })

    state["market_data_provider"] = original_provider

    ok_results = [
        r for r in validation_results
        if r.get("status") == "ok"
    ]

    if len(ok_results) == 0:
        return {
            "status": "multi_symbol_validation_failed",
            "version": DRAGONLADY_VERSION,
            "symbols_tested": symbols,
            "results": validation_results
        }

    average_return = round(
        sum(r["strategy_return_percent_net"] for r in ok_results) / len(ok_results),
        4
    )

    average_alpha = round(
        sum(r["alpha_percent"] for r in ok_results if r["alpha_percent"] is not None) /
        len([r for r in ok_results if r["alpha_percent"] is not None]),
        4
    )

    average_sharpe = round(
        sum(r["sharpe_ratio"] for r in ok_results if r["sharpe_ratio"] is not None) /
        len([r for r in ok_results if r["sharpe_ratio"] is not None]),
        4
    )

    average_drawdown = round(
        sum(r["max_drawdown_percent"] for r in ok_results if r["max_drawdown_percent"] is not None) /
        len([r for r in ok_results if r["max_drawdown_percent"] is not None]),
        4
    )

    profitable_count = len([
        r for r in ok_results
        if r["strategy_return_percent_net"] is not None and r["strategy_return_percent_net"] > 0
    ])

    beat_benchmark_count = len([
        r for r in ok_results
        if r["alpha_percent"] is not None and r["alpha_percent"] > 0
    ])

    best_symbol_by_return = max(
        ok_results,
        key=lambda x: x["strategy_return_percent_net"] if x["strategy_return_percent_net"] is not None else -10**18
    )

    best_symbol_by_sharpe = max(
        ok_results,
        key=lambda x: x["sharpe_ratio"] if x["sharpe_ratio"] is not None else -10**18
    )

    worst_symbol_by_return = min(
        ok_results,
        key=lambda x: x["strategy_return_percent_net"] if x["strategy_return_percent_net"] is not None else 10**18
    )

    return {
        "status": "multi_symbol_validation_completed",
        "version": DRAGONLADY_VERSION,
        "strategy_config": {
            "moving_average_window": MOVING_AVERAGE_WINDOW,
            "entry_threshold_percent": ENTRY_THRESHOLD_PERCENT,
            "exit_threshold_percent": EXIT_THRESHOLD_PERCENT,
            "max_position_value_percent": MAX_POSITION_VALUE_PERCENT,
            "risk_per_trade_percent": RISK_PER_TRADE_PERCENT,
            "use_trailing_stop": USE_TRAILING_STOP
        },
        "symbols_tested": symbols,
        "symbols_successful": len(ok_results),
        "summary": {
            "average_return_percent_net": average_return,
            "average_alpha_percent": average_alpha,
            "average_sharpe_ratio": average_sharpe,
            "average_max_drawdown_percent": average_drawdown,
            "profitable_count": profitable_count,
            "beat_benchmark_count": beat_benchmark_count,
            "best_symbol_by_return": best_symbol_by_return,
            "best_symbol_by_sharpe": best_symbol_by_sharpe,
            "worst_symbol_by_return": worst_symbol_by_return
        },
        "results": validation_results
    }
# ============================================================
# ROUTES
# ============================================================
@app.post("/dragonlady/multi_symbol_validation")
def multi_symbol_validation(limit: int = FMP_DEFAULT_HISTORY_LIMIT):
    return run_multi_symbol_validation_internal(
        symbols=MULTI_SYMBOL_TEST_LIST,
        limit=limit
    )
@app.post("/dragonlady/optimize_strategy")
def optimize_strategy(symbol: str = DEFAULT_SYMBOL):
    return optimize_strategy_internal(symbol.upper())
@app.post("/dragonlady/optimize_risk")
def optimize_risk(symbol: str = DEFAULT_SYMBOL):
    return optimize_risk_internal(symbol.upper())

@app.post("/dragonlady/optimize_exposure")
def optimize_exposure(symbol: str = DEFAULT_SYMBOL):
    return optimize_exposure_internal(symbol.upper())

@app.get("/")
def root():
    return {
        "project": "DragonLady",
        "version": DRAGONLADY_VERSION,
        "mode": DRAGONLADY_MODE,
        "market_data_provider": state["market_data_provider"],
        "message": "DragonLady is running in PAPER_ONLY mode."
    }


@app.post("/dragonlady/load_fmp_history")
def load_fmp_history(symbol: str = DEFAULT_SYMBOL, limit: int = FMP_DEFAULT_HISTORY_LIMIT):
    return load_fmp_history_into_buffer(
        symbol=symbol.upper(),
        limit=limit
    )


@app.post("/dragonlady/run_cycle")
def run_cycle():
    return run_dragonlady_cycle(DEFAULT_SYMBOL)


@app.post("/dragonlady/run_historical_cycle")
def run_historical_cycle(symbol: str = DEFAULT_SYMBOL):
    previous_provider = state["market_data_provider"]
    state["market_data_provider"] = "FMP_HISTORY_BUFFER"

    result = run_dragonlady_cycle(symbol.upper())

    state["market_data_provider"] = previous_provider

    return result


@app.post("/dragonlady/run_historical_test")
def run_historical_test(symbol: str = DEFAULT_SYMBOL, cycles: int = 10):
    previous_provider = state["market_data_provider"]
    state["market_data_provider"] = "FMP_HISTORY_BUFFER"

    result = run_historical_test_internal(
        symbol=symbol.upper(),
        cycles=cycles
    )

    state["market_data_provider"] = previous_provider

    return result


@app.post("/dragonlady/run_full_backtest")
def run_full_backtest(
    symbol: str = DEFAULT_SYMBOL,
    reset_index: bool = True,
    reset_portfolio: bool = True,
    close_end_position: bool = True
):
    return run_full_backtest_internal(
        symbol=symbol.upper(),
        reset_index=reset_index,
        reset_portfolio=reset_portfolio,
        close_end_position=close_end_position
    )


@app.post("/dragonlady/set_provider")
def set_provider(provider: str):
    provider = provider.upper()

    allowed_providers = [
        "SIMULATED",
        "FMP_HISTORY_BUFFER"
    ]

    if provider not in allowed_providers:
        return {
            "status": "rejected",
            "message": f"Unknown provider: {provider}",
            "allowed_providers": allowed_providers,
            "current_provider": state["market_data_provider"]
        }

    state["market_data_provider"] = provider

    return {
        "status": "provider_updated",
        "market_data_provider": state["market_data_provider"]
    }


@app.get("/dragonlady/config")
def get_config():
    return {
        "version": DRAGONLADY_VERSION,
        "mode": DRAGONLADY_MODE,
        "market_data_provider": state["market_data_provider"],
        "available_providers": [
            "SIMULATED",
            "FMP_HISTORY_BUFFER"
        ],
        "fmp": {
            "api_key_env_name": FMP_API_KEY_ENV_NAME,
            "api_key_loaded": get_fmp_api_key() is not None,
            "default_history_limit": FMP_DEFAULT_HISTORY_LIMIT
        },
        "symbol": DEFAULT_SYMBOL,
        "initial_price": INITIAL_PRICE,
        "strategy_settings": {
            "moving_average_window": MOVING_AVERAGE_WINDOW,
            "entry_threshold_percent": ENTRY_THRESHOLD_PERCENT,
            "exit_threshold_percent": EXIT_THRESHOLD_PERCENT
        },
        "risk_settings": {
            "risk_per_trade_percent": RISK_PER_TRADE_PERCENT,
            "stop_loss_percent": STOP_LOSS_PERCENT,
            "max_position_value_percent": MAX_POSITION_VALUE_PERCENT,
            "min_quantity": MIN_QUANTITY
        },
        "cost_model": {
            "spread_percent": SPREAD_PERCENT,
            "slippage_percent": SLIPPAGE_PERCENT,
            "commission_per_trade": COMMISSION_PER_TRADE
        }
    }


@app.get("/dragonlady/status")
def get_status():
    return {
        "project": "DragonLady",
        "version": DRAGONLADY_VERSION,
        "mode": DRAGONLADY_MODE,
        "market_data_provider": state["market_data_provider"],

        "cash": state["cash"],
        "positions_value": calculate_positions_value(),
        "portfolio_value": calculate_portfolio_value(),
        "exposure_percent": calculate_exposure_percent(),

        "realized_pnl_gross": state["realized_pnl_gross"],
        "realized_pnl_net": state["realized_pnl_net"],
        "unrealized_pnl_gross": calculate_unrealized_pnl_gross(),
        "unrealized_pnl_net": calculate_unrealized_pnl_net(),
        "total_pnl_gross": calculate_total_pnl_gross(),
        "total_pnl_net": calculate_total_pnl_net(),
        "return_percent_gross": calculate_return_percent_gross(),
        "return_percent_net": calculate_return_percent_net(),

        "total_trading_costs": state["total_trading_costs"],

        "peak_equity": round(state["peak_equity"], 2),
        "max_drawdown_amount": state["max_drawdown_amount"],
        "max_drawdown_percent": state["max_drawdown_percent"],

        "positions_count": len(state["positions"]),
        "trades_count": len(state["trades"]),
        "signals_count": len(state["signals"]),
        "trade_stats": calculate_trade_stats(),

        "cycle_count": state["cycle_count"],
        "last_cycle": state["last_cycle"],

        "historical_buffers": {
            symbol: {
                "records_loaded": len(buffer),
                "current_index": state["historical_buffer_indexes"].get(symbol, 0),
                "remaining_records": max(
                    0,
                    len(buffer) - state["historical_buffer_indexes"].get(symbol, 0)
                )
            }
            for symbol, buffer in state["historical_buffers"].items()
        }
    }


@app.get("/dragonlady/history_buffer")
def get_history_buffer(symbol: str = DEFAULT_SYMBOL):
    symbol = symbol.upper()

    buffer = state["historical_buffers"].get(symbol, [])
    index = state["historical_buffer_indexes"].get(symbol, 0)

    return {
        "symbol": symbol,
        "records_loaded": len(buffer),
        "current_index": index,
        "remaining_records": max(0, len(buffer) - index),
        "first_record": buffer[0] if len(buffer) > 0 else None,
        "next_record": buffer[index] if index < len(buffer) else None,
        "last_record": buffer[-1] if len(buffer) > 0 else None
    }


@app.get("/dragonlady/performance")
def get_performance():
    performance_metrics = calculate_performance_metrics()

    return {
        "equity_curve": state["equity_curve"],
        "trade_stats": calculate_trade_stats(),
        "performance_metrics": performance_metrics,
        "peak_equity": round(state["peak_equity"], 2),
        "max_drawdown_amount": state["max_drawdown_amount"],
        "max_drawdown_percent": state["max_drawdown_percent"],
        "total_pnl_gross": calculate_total_pnl_gross(),
        "total_pnl_net": calculate_total_pnl_net(),
        "return_percent_gross": calculate_return_percent_gross(),
        "return_percent_net": calculate_return_percent_net(),
        "total_trading_costs": state["total_trading_costs"]
    }


@app.get("/dragonlady/backtest_report")
def get_backtest_report(symbol: str = DEFAULT_SYMBOL):
    return build_backtest_report(symbol.upper())


@app.get("/dragonlady/chart/equity")
def get_equity_chart():
    file_path = generate_equity_curve_chart()

    if file_path is None:
        return {
            "status": "error",
            "message": "No equity curve available. Run a backtest first."
        }

    return FileResponse(
        path=file_path,
        media_type="image/png",
        filename=CHART_EQUITY_FILE
    )


@app.get("/dragonlady/chart/drawdown")
def get_drawdown_chart():
    file_path = generate_drawdown_chart()

    if file_path is None:
        return {
            "status": "error",
            "message": "No drawdown curve available. Run a backtest first."
        }

    return FileResponse(
        path=file_path,
        media_type="image/png",
        filename=CHART_DRAWDOWN_FILE
    )


@app.get("/dragonlady/chart/benchmark")
def get_benchmark_chart(symbol: str = DEFAULT_SYMBOL):
    file_path = generate_benchmark_chart(symbol.upper())

    if file_path is None:
        return {
            "status": "error",
            "message": "No benchmark curve available. Run a backtest first."
        }

    return FileResponse(
        path=file_path,
        media_type="image/png",
        filename=CHART_BENCHMARK_FILE
    )


@app.post("/dragonlady/reset")
def reset_dragonlady():
    reset_dragonlady_core(preserve_buffers=True)

    return {
        "status": "reset_completed",
        "message": "DragonLady state has been reset. Historical buffers were preserved and indexes reset.",
        "market_data_provider": state["market_data_provider"],
        "historical_buffers": {
            symbol: {
                "records_loaded": len(buffer),
                "current_index": state["historical_buffer_indexes"].get(symbol, 0)
            }
            for symbol, buffer in state["historical_buffers"].items()
        }
    }
@app.post("/dragonlady/walk_forward_optimization")
def walk_forward_optimization(
    symbol: str = DEFAULT_SYMBOL,
    train_window: int = 100,
    test_window: int = 100,
    limit: int = 500
):
    return run_walk_forward_optimization_internal(
        symbol=symbol.upper(),
        train_window=train_window,
        test_window=test_window,
        limit=limit
    )

@app.post("/dragonlady/clear_history_buffers")
def clear_history_buffers():
    state["historical_buffers"] = {}
    state["historical_buffer_indexes"] = {}

    return {
        "status": "history_buffers_cleared"
    }
@app.post("/dragonlady/adaptive_multi_symbol_simulation")
def adaptive_multi_symbol_simulation(
    symbols: str = "AAPL,MSFT",
    limit: int = 500,
    train_window: int = 100,
    test_window: int = 50,
    min_quality_sharpe: float = 0.5,
    min_quality_return: float = 0.0,
    max_symbols: int = 10
):
    symbol_list = [
        s.strip().upper()
        for s in symbols.split(",")
        if s.strip()
    ][:max_symbols]

    symbol_results = {}
    symbols_traded = []
    symbols_skipped = []

    portfolio_initial_cash = state["initial_cash"]

    portfolio_final_value = 0.0
    portfolio_cash_unallocated = 0.0

    base_allocation_per_symbol = (
        portfolio_initial_cash / max(len(symbol_list), 1)
    )

    for symbol in symbol_list:
        try:
            walk_forward = run_walk_forward_optimization_internal(
                symbol=symbol,
                train_window=train_window,
                test_window=test_window,
                limit=limit
            )

            diagnostic = diagnose_symbol_quality(walk_forward)

            symbol_results[symbol] = {
                "walk_forward_results": walk_forward,
                "diagnostic": diagnostic
            }

            allocation_multiplier = diagnostic["allocation_multiplier"]
            avg_return = diagnostic["average_return_percent_net"]

            allocated_cash = base_allocation_per_symbol * allocation_multiplier
            unallocated_cash = base_allocation_per_symbol * (1 - allocation_multiplier)

            if allocation_multiplier > 0:
                symbols_traded.append(symbol)

                final_value = allocated_cash * (1 + avg_return / 100)

                portfolio_final_value += final_value
                portfolio_cash_unallocated += unallocated_cash

            else:
                symbols_skipped.append(symbol)
                portfolio_cash_unallocated += base_allocation_per_symbol

            symbol_results[symbol]["allocation_result"] = {
                "base_allocation": round(base_allocation_per_symbol, 2),
                "allocation_multiplier": allocation_multiplier,
                "allocated_cash": round(allocated_cash, 2),
                "unallocated_cash": round(unallocated_cash, 2),
                "average_return_percent_net": avg_return,
                "final_value": round(
                    allocated_cash * (1 + avg_return / 100)
                    if allocation_multiplier > 0
                    else 0,
                    2
                )
            }

        except Exception as e:
            symbols_skipped.append(symbol)
            portfolio_cash_unallocated += base_allocation_per_symbol

            symbol_results[symbol] = {
                "error": str(e),
                "allocation_result": {
                    "base_allocation": round(base_allocation_per_symbol, 2),
                    "allocation_multiplier": 0,
                    "allocated_cash": 0,
                    "unallocated_cash": round(base_allocation_per_symbol, 2),
                    "final_value": 0
                }
            }

    portfolio_final_value += portfolio_cash_unallocated

    total_return_percent = (
        (portfolio_final_value - portfolio_initial_cash)
        / portfolio_initial_cash
        * 100
        if portfolio_initial_cash > 0
        else 0
    )

    return {
        "status": "adaptive_multi_symbol_simulation_completed",
        "project": "DragonLady",
        "version": "1.91.0",
        "symbols_requested": symbol_list,
        "symbols_traded": symbols_traded,
        "symbols_skipped": symbols_skipped,
        "simulation_settings": {
            "limit": limit,
            "train_window": train_window,
            "test_window": test_window,
            "min_quality_sharpe": min_quality_sharpe,
            "min_quality_return": min_quality_return,
            "max_symbols": max_symbols,
            "base_allocation_per_symbol": round(base_allocation_per_symbol, 2)
        },
        "portfolio_summary": {
            "initial_cash": portfolio_initial_cash,
            "portfolio_final_value": round(portfolio_final_value, 2),
            "cash_unallocated": round(portfolio_cash_unallocated, 2),
            "return_percent_net": round(total_return_percent, 4)
        },
        "symbol_results": symbol_results
    }
@app.post("/dragonlady/adaptive-portfolio-optimization")
def adaptive_portfolio_optimization(
    symbols: List[str] = Query(
        default=["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "SPY"]
    ),
    history_days: int = 500,
    train_window: int = 200,
    test_window: int = 100,
    top_n: int = 5,
    initial_capital: float = 100000,
    min_sharpe: float = 0.75,
    min_return: float = 0.0,
    min_profit_factor: float = 1.2,
    max_drawdown_limit: float = 15.0
):
    """
    DragonLady V1.92 Adaptive Portfolio Optimization.

    Workflow:
    1. Walk-forward optimize each symbol.
    2. Compute composite score.
    3. Apply quality filters.
    4. Keep top N.
    5. Normalize weights.
    6. Simulate portfolio.
    """

    analyzed_symbols = []

    for symbol in symbols:
        try:
            wf = run_walk_forward_optimization_internal(
                symbol=symbol,
                train_window=train_window,
                test_window=test_window,
                limit=history_days
            )

            agg = wf.get("aggregate_results", {})
            avg_return = agg.get("average_return_percent_net", 0)
            avg_sharpe = agg.get("average_sharpe_ratio", 0)
            avg_alpha = agg.get("average_alpha_percent", 0)
            avg_drawdown = agg.get("average_max_drawdown_percent", 0)

            best_segment = agg.get("best_segment", {})
            best_test = best_segment.get("test_results", {})

            result = {
                "symbol": symbol,
                "return_percent_net": avg_return,
                "sharpe_ratio": avg_sharpe,
                "alpha_percent": avg_alpha,
                "max_drawdown_percent": avg_drawdown,
                "profit_factor_net": best_test.get("profit_factor_net", 0),
                "sortino_ratio": best_test.get("sortino_ratio", 0),
                "calmar_ratio": best_test.get("calmar_ratio", 0),
                "closed_trades_count": best_test.get("closed_trades_count", 0)
            }

            score = compute_strategy_score(result)

            analyzed_symbols.append({
                "symbol": symbol,
                "score": score,
                "result": result,
                "passes_filters": passes_quality_filters(
                    result,
                    min_sharpe=min_sharpe,
                    min_return=min_return,
                    min_profit_factor=min_profit_factor,
                    max_drawdown_limit=max_drawdown_limit
                )
            })

        except Exception as e:
            analyzed_symbols.append({
                "symbol": symbol,
                "error": str(e),
                "passes_filters": False
            })

    # Keep only valid candidates
    candidates = [
        x for x in analyzed_symbols
        if x.get("passes_filters", False)
    ]

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Keep top N
    selected = candidates[:top_n]

    if not selected:
        return {
            "status": "no_eligible_symbols",
            "version": "1.92.0",
            "analyzed_symbols": analyzed_symbols
        }

    # Compute weights
    selected = normalize_weights(selected)

    # Simulate portfolio
    portfolio = simulate_weighted_portfolio(
        selected,
        initial_capital=initial_capital
    )

    return {
        "status": "adaptive_portfolio_optimization_completed",
        "version": "1.92.0",
        "symbols_analyzed": symbols,
        "history_days": history_days,
        "train_window": train_window,
        "test_window": test_window,
        "top_n": top_n,
        "filters": {
            "min_sharpe": min_sharpe,
            "min_return": min_return,
            "min_profit_factor": min_profit_factor,
            "max_drawdown_limit": max_drawdown_limit
        },
        "analyzed_symbols": analyzed_symbols,
        "selected_symbols": [
            {
                "symbol": x["symbol"],
                "score": round(x["score"], 4),
                "weight_percent": x["weight_percent"],
                "result": x["result"]
            }
            for x in selected
        ],
        **portfolio
    }