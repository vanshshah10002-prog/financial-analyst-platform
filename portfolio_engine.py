"""
FinanceIQ v5.2 — Enhanced Paper Trading Portfolio Engine
SQLite-backed virtual portfolio with $100K starting capital.

Features inspired by hftbacktest:
  - Market, Limit, Stop-Loss, Take-Profit orders
  - Slippage & commission modeling
  - Short selling with margin tracking
  - Equity curve tracking
  - Enhanced analytics (Sortino, Calmar, profit factor, avg hold period)
"""
import sqlite3
import os
import math
import logging
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.environ.get(
    "PORTFOLIO_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.db"),
)
INITIAL_CASH = 100000.00
DEFAULT_SLIPPAGE_BPS = 5      # 0.05% default slippage
DEFAULT_COMMISSION_PER_SHARE = 0.005  # $0.005 per share (IBKR-style)
MIN_COMMISSION = 1.00          # $1 minimum per trade

log = logging.getLogger("financeiq.portfolio")


@contextmanager
def txn(conn):
    """Explicit transaction boundary.

    sqlite3 in autocommit-mode-with-implicit-transactions is a known footgun:
    multi-statement ops can be split if anything between them raises. This wraps
    a block in BEGIN IMMEDIATE / COMMIT and rolls back on any exception so the
    portfolio never ends up half-updated.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:  # already rolled back / connection closed
            pass
        log.exception("portfolio transaction rolled back")
        raise


def get_db():
    """Get database connection and ensure tables exist."""
    # 5s busy timeout — under WAL another writer briefly holding the lock is normal.
    conn = sqlite3.connect(DB_PATH, timeout=5.0, isolation_level=None)  # autocommit, we drive txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # safe + faster than FULL under WAL
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cash REAL NOT NULL DEFAULT 100000.00,
            slippage_bps REAL NOT NULL DEFAULT 5,
            commission_per_share REAL NOT NULL DEFAULT 0.005,
            margin_used REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'stock',
            shares REAL NOT NULL DEFAULT 0,
            avg_cost REAL NOT NULL DEFAULT 0,
            side TEXT NOT NULL DEFAULT 'LONG',
            opened_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(ticker, side)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'stock',
            action TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'LONG',
            shares REAL NOT NULL,
            price REAL NOT NULL,
            slippage REAL NOT NULL DEFAULT 0,
            commission REAL NOT NULL DEFAULT 0,
            total REAL NOT NULL,
            pnl REAL DEFAULT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'stock',
            order_type TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'BUY',
            shares REAL NOT NULL,
            target_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            filled_at TEXT DEFAULT NULL,
            expires_at TEXT DEFAULT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS equity_curve (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            total_value REAL NOT NULL,
            cash REAL NOT NULL,
            positions_value REAL NOT NULL,
            daily_return REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_value REAL NOT NULL,
            cash REAL NOT NULL,
            positions_value REAL NOT NULL
        )
    """)

    cursor = conn.execute("SELECT COUNT(*) FROM portfolio_meta")
    if cursor.fetchone()[0] == 0:
        conn.execute("INSERT INTO portfolio_meta (id, cash) VALUES (1, ?)", (INITIAL_CASH,))
    return conn


def _get_meta(conn):
    row = conn.execute("SELECT * FROM portfolio_meta WHERE id = 1").fetchone()
    return dict(row)


def _calc_slippage(price, shares, slippage_bps, side):
    """Calculate slippage based on order direction."""
    slip_pct = slippage_bps / 10000.0
    if side == "BUY":
        return price * slip_pct  # pay more when buying
    else:
        return -price * slip_pct  # receive less when selling


def _calc_commission(shares, comm_per_share):
    """Calculate commission with minimum."""
    return max(abs(shares) * comm_per_share, MIN_COMMISSION)


def get_cash():
    conn = get_db()
    row = conn.execute("SELECT cash FROM portfolio_meta WHERE id = 1").fetchone()
    conn.close()
    return row["cash"] if row else INITIAL_CASH


def get_positions():
    conn = get_db()
    rows = conn.execute("SELECT * FROM positions WHERE shares > 0").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def buy(ticker, shares, price, asset_type="stock"):
    """Buy shares (long) with slippage and commission. Atomic."""
    if shares <= 0 or price <= 0:
        return {"error": "Invalid shares or price"}

    conn = get_db()
    try:
        with txn(conn):
            meta = _get_meta(conn)
            cash = meta["cash"]
            slippage_bps = meta["slippage_bps"]
            comm_per_share = meta["commission_per_share"]

            slip = _calc_slippage(price, shares, slippage_bps, "BUY")
            exec_price = price + slip
            commission = _calc_commission(shares, comm_per_share)
            total_cost = shares * exec_price + commission

            if total_cost > cash:
                return {"error": f"Insufficient cash. Need ${total_cost:.2f}, have ${cash:.2f}"}

            new_cash = cash - total_cost
            conn.execute(
                "UPDATE portfolio_meta SET cash = ?, updated_at = datetime('now') WHERE id = 1",
                (new_cash,),
            )

            existing = conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND side = 'LONG'", (ticker,)
            ).fetchone()
            if existing:
                old_shares = existing["shares"]
                old_avg = existing["avg_cost"]
                new_shares = old_shares + shares
                new_avg = ((old_shares * old_avg) + (shares * exec_price)) / new_shares
                conn.execute(
                    "UPDATE positions SET shares = ?, avg_cost = ?, updated_at = datetime('now') "
                    "WHERE ticker = ? AND side = 'LONG'",
                    (new_shares, new_avg, ticker),
                )
            else:
                conn.execute(
                    "INSERT INTO positions (ticker, asset_type, shares, avg_cost, side) "
                    "VALUES (?, ?, ?, ?, 'LONG')",
                    (ticker, asset_type, shares, exec_price),
                )

            conn.execute(
                "INSERT INTO transactions (ticker, asset_type, action, side, shares, price, "
                "slippage, commission, total) "
                "VALUES (?, ?, 'BUY', 'LONG', ?, ?, ?, ?, ?)",
                (ticker, asset_type, shares, exec_price, slip * shares, commission, total_cost),
            )
        return {
            "success": True, "action": "BUY", "ticker": ticker,
            "shares": shares, "price": round(exec_price, 4),
            "slippage": round(slip, 4), "commission": round(commission, 2),
            "total": round(total_cost, 2), "remaining_cash": round(new_cash, 2),
        }
    finally:
        conn.close()


def sell(ticker, shares, price):
    """Sell long shares with slippage and commission. Atomic."""
    if shares <= 0 or price <= 0:
        return {"error": "Invalid shares or price"}

    conn = get_db()
    try:
        with txn(conn):
            meta = _get_meta(conn)
            slippage_bps = meta["slippage_bps"]
            comm_per_share = meta["commission_per_share"]

            existing = conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND side = 'LONG'", (ticker,)
            ).fetchone()
            if not existing or existing["shares"] < shares:
                available = existing["shares"] if existing else 0
                return {"error": f"Insufficient shares. Have {available}, trying to sell {shares}"}

            slip = _calc_slippage(price, shares, slippage_bps, "SELL")
            exec_price = price + slip  # slip is negative for sells
            commission = _calc_commission(shares, comm_per_share)
            total_proceeds = shares * exec_price - commission

            new_shares = existing["shares"] - shares
            cash = meta["cash"]
            new_cash = cash + total_proceeds

            conn.execute(
                "UPDATE portfolio_meta SET cash = ?, updated_at = datetime('now') WHERE id = 1",
                (new_cash,),
            )

            if new_shares <= 0:
                conn.execute("DELETE FROM positions WHERE ticker = ? AND side = 'LONG'", (ticker,))
            else:
                conn.execute(
                    "UPDATE positions SET shares = ?, updated_at = datetime('now') "
                    "WHERE ticker = ? AND side = 'LONG'",
                    (new_shares, ticker),
                )

            pnl = (exec_price - existing["avg_cost"]) * shares - commission
            conn.execute(
                "INSERT INTO transactions (ticker, asset_type, action, side, shares, price, "
                "slippage, commission, total, pnl) "
                "VALUES (?, ?, 'SELL', 'LONG', ?, ?, ?, ?, ?, ?)",
                (ticker, existing["asset_type"], shares, exec_price, slip * shares,
                 commission, total_proceeds, pnl),
            )
        return {
            "success": True, "action": "SELL", "ticker": ticker,
            "shares": shares, "price": round(exec_price, 4),
            "slippage": round(slip, 4), "commission": round(commission, 2),
            "total": round(total_proceeds, 2), "pnl": round(pnl, 2),
            "remaining_cash": round(new_cash, 2),
        }
    finally:
        conn.close()


def short_sell(ticker, shares, price, asset_type="stock"):
    """Open a short position. Margin is deducted from cash. Atomic."""
    if shares <= 0 or price <= 0:
        return {"error": "Invalid shares or price"}

    conn = get_db()
    try:
        with txn(conn):
            meta = _get_meta(conn)
            cash = meta["cash"]
            slippage_bps = meta["slippage_bps"]
            comm_per_share = meta["commission_per_share"]

            slip = _calc_slippage(price, shares, slippage_bps, "SELL")
            exec_price = price + slip
            commission = _calc_commission(shares, comm_per_share)
            margin_req = shares * exec_price + commission

            if margin_req > cash:
                return {"error": f"Insufficient funds. Need ${margin_req:.2f}, have ${cash:.2f}"}

            new_cash = cash - margin_req
            new_margin = meta["margin_used"] + (shares * exec_price)
            conn.execute(
                "UPDATE portfolio_meta SET cash = ?, margin_used = ?, updated_at = datetime('now') "
                "WHERE id = 1",
                (new_cash, new_margin),
            )

            existing = conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND side = 'SHORT'", (ticker,)
            ).fetchone()
            if existing:
                old_shares = existing["shares"]
                old_avg = existing["avg_cost"]
                new_shares = old_shares + shares
                new_avg = ((old_shares * old_avg) + (shares * exec_price)) / new_shares
                conn.execute(
                    "UPDATE positions SET shares = ?, avg_cost = ?, updated_at = datetime('now') "
                    "WHERE ticker = ? AND side = 'SHORT'",
                    (new_shares, new_avg, ticker),
                )
            else:
                conn.execute(
                    "INSERT INTO positions (ticker, asset_type, shares, avg_cost, side) "
                    "VALUES (?, ?, ?, ?, 'SHORT')",
                    (ticker, asset_type, shares, exec_price),
                )

            conn.execute(
                "INSERT INTO transactions (ticker, asset_type, action, side, shares, price, "
                "slippage, commission, total) "
                "VALUES (?, ?, 'SHORT', 'SHORT', ?, ?, ?, ?, ?)",
                (ticker, asset_type, shares, exec_price, abs(slip) * shares, commission, margin_req),
            )
        return {
            "success": True, "action": "SHORT", "ticker": ticker,
            "shares": shares, "price": round(exec_price, 4),
            "margin_required": round(margin_req, 2),
            "commission": round(commission, 2),
        }
    finally:
        conn.close()


def cover_short(ticker, shares, price):
    """Cover (buy back) a short position. Releases margin + P&L to cash. Atomic."""
    if shares <= 0 or price <= 0:
        return {"error": "Invalid shares or price"}

    conn = get_db()
    try:
        with txn(conn):
            meta = _get_meta(conn)
            slippage_bps = meta["slippage_bps"]
            comm_per_share = meta["commission_per_share"]

            existing = conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND side = 'SHORT'", (ticker,)
            ).fetchone()
            if not existing or existing["shares"] < shares:
                available = existing["shares"] if existing else 0
                return {"error": f"No short position. Have {available} short shares."}

            slip = _calc_slippage(price, shares, slippage_bps, "BUY")
            exec_price = price + slip
            commission = _calc_commission(shares, comm_per_share)

            entry_value = shares * existing["avg_cost"]
            cover_cost = shares * exec_price
            pnl = entry_value - cover_cost - commission

            cash = meta["cash"]
            margin_release = entry_value
            new_cash = cash + margin_release + pnl
            new_margin = max(0, meta["margin_used"] - margin_release)

            conn.execute(
                "UPDATE portfolio_meta SET cash = ?, margin_used = ?, updated_at = datetime('now') "
                "WHERE id = 1",
                (new_cash, new_margin),
            )

            new_shares = existing["shares"] - shares
            if new_shares <= 0:
                conn.execute("DELETE FROM positions WHERE ticker = ? AND side = 'SHORT'", (ticker,))
            else:
                conn.execute(
                    "UPDATE positions SET shares = ?, updated_at = datetime('now') "
                    "WHERE ticker = ? AND side = 'SHORT'",
                    (new_shares, ticker),
                )

            conn.execute(
                "INSERT INTO transactions (ticker, asset_type, action, side, shares, price, "
                "slippage, commission, total, pnl) "
                "VALUES (?, ?, 'COVER', 'SHORT', ?, ?, ?, ?, ?, ?)",
                (ticker, existing["asset_type"], shares, exec_price, abs(slip) * shares,
                 commission, cover_cost + commission, pnl),
            )
        return {
            "success": True, "action": "COVER", "ticker": ticker,
            "shares": shares, "price": round(exec_price, 4),
            "pnl": round(pnl, 2), "commission": round(commission, 2),
            "remaining_cash": round(new_cash, 2),
        }
    finally:
        conn.close()


# ── Pending Orders (Limit / Stop) ────────────────────────

def place_limit_order(ticker, side, shares, limit_price, asset_type="stock", expires_hours=24):
    """Place a limit order that fills when market price hits the target."""
    if shares <= 0 or limit_price <= 0:
        return {"error": "Invalid order parameters"}

    expires = datetime.now()
    from datetime import timedelta
    expires = (expires + timedelta(hours=expires_hours)).isoformat()

    conn = get_db()
    conn.execute(
        """INSERT INTO pending_orders (ticker, asset_type, order_type, side, shares, target_price, expires_at)
           VALUES (?, ?, 'LIMIT', ?, ?, ?, ?)""",
        (ticker, asset_type, side, shares, limit_price, expires)
    )
    conn.commit()
    order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return {"success": True, "order_id": order_id, "order_type": "LIMIT",
            "side": side, "ticker": ticker, "shares": shares,
            "target_price": limit_price, "expires_at": expires}


def place_stop_order(ticker, side, shares, stop_price, asset_type="stock", expires_hours=24):
    """Place a stop order (stop-loss or take-profit)."""
    if shares <= 0 or stop_price <= 0:
        return {"error": "Invalid order parameters"}

    expires = datetime.now()
    from datetime import timedelta
    expires = (expires + timedelta(hours=expires_hours)).isoformat()

    conn = get_db()
    conn.execute(
        """INSERT INTO pending_orders (ticker, asset_type, order_type, side, shares, target_price, expires_at)
           VALUES (?, ?, 'STOP', ?, ?, ?, ?)""",
        (ticker, asset_type, side, shares, stop_price, expires)
    )
    conn.commit()
    order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return {"success": True, "order_id": order_id, "order_type": "STOP",
            "side": side, "ticker": ticker, "shares": shares,
            "target_price": stop_price, "expires_at": expires}


def get_pending_orders():
    """Get all pending orders."""
    conn = get_db()
    # Expire old orders
    conn.execute("UPDATE pending_orders SET status = 'EXPIRED' WHERE status = 'PENDING' AND expires_at < datetime('now')")
    conn.commit()
    rows = conn.execute("SELECT * FROM pending_orders WHERE status = 'PENDING' ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cancel_order(order_id):
    """Cancel a pending order."""
    conn = get_db()
    conn.execute("UPDATE pending_orders SET status = 'CANCELLED' WHERE id = ? AND status = 'PENDING'", (order_id,))
    conn.commit()
    conn.close()
    return {"success": True, "order_id": order_id, "status": "CANCELLED"}


def check_and_fill_orders(current_prices):
    """Check pending orders against current prices and fill if conditions met.
    Called during live polling.
    current_prices: dict {ticker: price}
    """
    conn = get_db()
    pending = conn.execute("SELECT * FROM pending_orders WHERE status = 'PENDING'").fetchall()
    filled = []

    for order in pending:
        order = dict(order)
        ticker = order["ticker"]
        if ticker not in current_prices:
            continue

        market_price = current_prices[ticker]
        should_fill = False

        if order["order_type"] == "LIMIT":
            if order["side"] == "BUY" and market_price <= order["target_price"]:
                should_fill = True
            elif order["side"] == "SELL" and market_price >= order["target_price"]:
                should_fill = True
        elif order["order_type"] == "STOP":
            if order["side"] == "SELL" and market_price <= order["target_price"]:
                should_fill = True  # stop-loss triggered
            elif order["side"] == "BUY" and market_price >= order["target_price"]:
                should_fill = True  # stop-buy triggered

        if should_fill:
            conn.execute("UPDATE pending_orders SET status = 'FILLED', filled_at = datetime('now') WHERE id = ?",
                         (order["id"],))

            if order["side"] == "BUY":
                result = buy(ticker, order["shares"], market_price, order["asset_type"])
            else:
                result = sell(ticker, order["shares"], market_price)

            filled.append({"order_id": order["id"], "ticker": ticker, "result": result})

    conn.commit()
    conn.close()
    return filled


# ── Equity Curve ──────────────────────────────────────

def record_equity_point(total_value, cash, positions_value):
    """Record a point on the equity curve."""
    conn = get_db()
    # Get last point for daily return calc
    last = conn.execute("SELECT total_value FROM equity_curve ORDER BY id DESC LIMIT 1").fetchone()
    daily_return = 0
    if last and last["total_value"] > 0:
        daily_return = (total_value - last["total_value"]) / last["total_value"]

    conn.execute(
        "INSERT INTO equity_curve (timestamp, total_value, cash, positions_value, daily_return) VALUES (datetime('now'), ?, ?, ?, ?)",
        (total_value, cash, positions_value, daily_return)
    )
    conn.commit()
    conn.close()


def get_equity_curve(limit=500):
    """Get equity curve data for charting."""
    conn = get_db()
    rows = conn.execute(
        "SELECT timestamp, total_value, cash, positions_value, daily_return FROM equity_curve ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


# ── Portfolio Summary ─────────────────────────────────

def get_portfolio_summary(current_prices=None):
    if current_prices is None:
        current_prices = {}

    conn = get_db()
    meta = _get_meta(conn)
    cash = meta["cash"]
    margin_used = meta["margin_used"]
    positions = conn.execute("SELECT * FROM positions WHERE shares > 0").fetchall()
    conn.close()

    pos_list = []
    total_long_value = 0
    total_short_value = 0
    total_cost_basis = 0

    for p in positions:
        ticker = p["ticker"]
        shares = p["shares"]
        avg_cost = p["avg_cost"]
        side = p["side"]
        current_price = current_prices.get(ticker, avg_cost)

        if side == "LONG":
            market_value = shares * current_price
            cost_basis = shares * avg_cost
            pnl = market_value - cost_basis
            total_long_value += market_value
        else:  # SHORT
            market_value = shares * avg_cost  # short value is entry value
            cost_basis = shares * avg_cost
            pnl = (avg_cost - current_price) * shares  # profit when price drops
            total_short_value += shares * current_price

        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
        total_cost_basis += cost_basis

        pos_list.append({
            "ticker": ticker, "asset_type": p["asset_type"],
            "shares": shares, "avg_cost": round(avg_cost, 2),
            "current_price": round(current_price, 2),
            "market_value": round(market_value, 2),
            "side": side,
            "cost_basis": round(cost_basis, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            "allocation_pct": 0
        })

    positions_value = total_long_value
    # total_value = available cash + long positions + margin held + unrealized short P&L
    # margin_used tracks entry value of shorts (deducted from cash at open)
    # short_unrealized_pnl = sum of (entry_price - current_price) * shares for shorts
    short_unrealized_pnl = 0
    for p in pos_list:
        if p["side"] == "SHORT":
            short_unrealized_pnl += p["pnl"]
    total_value = cash + positions_value + margin_used + short_unrealized_pnl
    total_pnl = total_value - INITIAL_CASH
    total_pnl_pct = (total_pnl / INITIAL_CASH * 100)

    for p in pos_list:
        p["allocation_pct"] = round((abs(p["market_value"]) / total_value * 100) if total_value > 0 else 0, 2)

    return {
        "cash": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "initial_capital": INITIAL_CASH,
        "margin_used": round(margin_used, 2),
        "buying_power": round(cash, 2),
        "positions": pos_list,
        "cash_allocation_pct": round((cash / total_value * 100) if total_value > 0 else 100, 2),
        "slippage_bps": round(meta["slippage_bps"], 1),
        "commission_per_share": round(meta["commission_per_share"], 4),
    }


def get_transactions(limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_analytics(current_prices=None):
    """Enhanced analytics with Sortino, Calmar, profit factor."""
    conn = get_db()
    txns = conn.execute("SELECT * FROM transactions ORDER BY timestamp ASC").fetchall()
    equity = conn.execute("SELECT * FROM equity_curve ORDER BY id ASC").fetchall()
    conn.close()

    if not txns:
        return _empty_analytics()

    # Daily returns from equity curve
    daily_returns = [dict(e)["daily_return"] for e in equity if dict(e)["daily_return"] != 0]

    if len(daily_returns) >= 2:
        avg_ret = sum(daily_returns) / len(daily_returns)
        std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns))
        neg_returns = [r for r in daily_returns if r < 0]
        downside_dev = math.sqrt(sum(r ** 2 for r in neg_returns) / len(neg_returns)) if neg_returns else 0

        sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0
        sortino = (avg_ret / downside_dev * math.sqrt(252)) if downside_dev > 0 else 0

        # Max drawdown
        values = [dict(e)["total_value"] for e in equity]
        peak = values[0] if values else INITIAL_CASH
        max_dd = 0
        for v in values:
            if v > peak: peak = v
            dd = (peak - v) / peak
            if dd > max_dd: max_dd = dd

        # Calmar ratio (annualized return / max drawdown)
        total_return = (values[-1] / values[0] - 1) if values and values[0] > 0 else 0
        calmar = (total_return / max_dd) if max_dd > 0 else 0
    else:
        sharpe = sortino = calmar = 0
        max_dd = 0

    # Trade analysis
    pnls = []
    for t in txns:
        t = dict(t)
        if t.get("pnl") is not None:
            pnls.append(t["pnl"])

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_trades = len(pnls)
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

    # Total commissions and slippage
    total_commission = sum(dict(t).get("commission", 0) for t in txns)
    total_slippage = sum(abs(dict(t).get("slippage", 0)) for t in txns)

    summary = get_portfolio_summary(current_prices)

    return {
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "calmar_ratio": round(calmar, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "∞",
        "win_rate": round(win_rate, 1),
        "total_trades": total_trades,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "best_trade": round(max(pnls), 2) if pnls else 0,
        "worst_trade": round(min(pnls), 2) if pnls else 0,
        "total_return_pct": round(summary["total_pnl_pct"], 2),
        "total_commission": round(total_commission, 2),
        "total_slippage": round(total_slippage, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }


def _empty_analytics():
    return {
        "sharpe_ratio": 0, "sortino_ratio": 0, "calmar_ratio": 0,
        "max_drawdown_pct": 0, "profit_factor": 0,
        "win_rate": 0, "total_trades": 0,
        "winning_trades": 0, "losing_trades": 0,
        "avg_win": 0, "avg_loss": 0,
        "best_trade": 0, "worst_trade": 0,
        "total_return_pct": 0, "total_commission": 0, "total_slippage": 0,
        "gross_profit": 0, "gross_loss": 0,
    }


def save_daily_snapshot(total_value, cash, positions_value):
    conn = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR REPLACE INTO portfolio_snapshots (date, total_value, cash, positions_value) VALUES (?, ?, ?, ?)",
        (today, total_value, cash, positions_value)
    )
    conn.commit()
    conn.close()


def reset_portfolio():
    """Reset portfolio to initial state — clears everything. Atomic."""
    conn = get_db()
    try:
        with txn(conn):
            conn.execute("DELETE FROM portfolio_meta")
            conn.execute("INSERT INTO portfolio_meta (id, cash) VALUES (1, ?)", (INITIAL_CASH,))
            conn.execute("DELETE FROM positions")
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM pending_orders")
            conn.execute("DELETE FROM equity_curve")
            conn.execute("DELETE FROM portfolio_snapshots")
        return {"success": True, "message": "Portfolio reset to $100,000"}
    finally:
        conn.close()


def update_settings(slippage_bps=None, commission_per_share=None):
    """Update portfolio simulation settings."""
    conn = get_db()
    if slippage_bps is not None:
        conn.execute("UPDATE portfolio_meta SET slippage_bps = ? WHERE id = 1", (slippage_bps,))
    if commission_per_share is not None:
        conn.execute("UPDATE portfolio_meta SET commission_per_share = ? WHERE id = 1", (commission_per_share,))
    conn.commit()
    conn.close()
    return {"success": True}
