<p align="center">
  <h1 align="center">📊 FinanceIQ</h1>
  <p align="center"><strong>Multi-Asset Financial Analytics & Paper-Trading Platform</strong></p>
</p>

A full-stack financial analytics web app built with **Flask**, **yfinance / financetoolkit**,
**KLineChart**, and a local-first **AI** layer (Ollama → Gemini fallback). It covers stocks,
futures, currencies, indices and **crypto** — with a modern terminal-style UI, real-time
crypto charting, fundamentals, options analytics, and a $100K paper-trading simulator.

---

## ✨ Features

### 📈 Charting & Market Data
- **KLineChart** candlesticks with built-in indicators (MA, EMA, BOLL, VOL, MACD, RSI, KDJ)
- **Real-time crypto** via the Binance public WebSocket (no API key required)
- **~15-min delayed** stocks / FX / futures / indices via yfinance — each clearly badged
  **LIVE** vs **DELAYED** so you always know your data freshness
- Interval selector (15m · 1H · 4H · 1D · 1W) and a live **Market Pulse** dashboard

### 🏦 Fundamentals & Research
- Income statement, balance sheet, ratios, free cash flow
- Altman Z-Score, dividend analysis, candlestick-pattern recognition
- Sector peer comparison, correlation matrix, FRED macro indicators
- Analyst consensus, insider activity, earnings history (Finnhub)
- Monte Carlo price simulation with percentile bands

### 💼 Portfolio Simulation
- **$100K paper trading** with market / limit / stop orders and short selling
- Slippage + commission modeling; **atomic SQLite (WAL)** transactions
- Performance analytics: Sharpe, Sortino, Calmar, profit factor, max drawdown
- Interactive equity curve and full transaction history

### 🎯 Options
- Black-Scholes pricing, Greeks, and a Newton-Raphson + bisection implied-vol solver
- Live options chain with Greeks and an interactive payoff diagram

### 🤖 AI Advisory
- Local **Ollama** first, **Gemini** fallback — fully env-configurable
- Synthesizes technicals, fundamentals, and news sentiment into a Buy/Hold/Sell view

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Flask, Python 3.10+ |
| **Market data** | yfinance, financetoolkit (FMP), Binance public data |
| **Charts** | KLineChart (main) · TradingView Lightweight Charts (secondary) |
| **AI** | Ollama (local) → Google Gemini (fallback) |
| **Database** | SQLite (WAL mode) |
| **Frontend** | Vanilla JS, CSS design system (IBM Plex Sans/Mono) |

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/vanshshah10002-prog/financial-analyst-platform.git
cd financial-analyst-platform

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env      # then fill in your keys

# Run
python app.py
```

Then open **http://localhost:5000**.

> Real-time crypto (Binance) and delayed data (yfinance) need **no API keys**.
> Fundamentals need `FMP_API_KEY`; AI needs either Ollama running locally or a `GEMINI_API_KEY`.

## 📁 Project Structure

```
financial-analyst-platform/
├── app.py                  # Flask server & all API routes
├── portfolio_engine.py     # SQLite-backed paper trading (WAL + atomic transactions)
├── options_engine.py       # Black-Scholes, Greeks, Newton + bisection IV
├── requirements.txt        # Python dependencies (pinned)
├── .env.example            # Environment variable template
├── templates/index.html    # Single-page dashboard UI
└── static/                 # Frontend (XSS-hardened)
    ├── script.js
    └── style.css
```

The full feature set (technical indicators, news + VADER sentiment, candlestick patterns,
market sentiment, Altman Z-score, Monte Carlo, etc.) lives inside `app.py` and is exposed via
the API routes below — there are no separate helper modules to keep in sync.

## 🔐 Environment

| Var | Purpose |
|-----|---------|
| `FMP_API_KEY` | Fundamentals / ratios (financetoolkit) |
| `FINNHUB_API_KEY` | Analyst, insider, earnings (optional) |
| `FRED_API_KEY` | Macro indicators (optional) |
| `ALPHA_VANTAGE_KEY` | Sector heatmap / FX (optional) |
| `OLLAMA_URL` *or* `GEMINI_API_KEY` | At least one required for `/api/ai` |

The startup banner prints `[ok]` / `[warn]` / `[FAIL]` per key so misconfiguration is obvious.

Rate limits (per-IP, override via env):

| Env var | Default | Endpoint(s) |
|---------|---------|-------------|
| `RATE_LIMIT_DEFAULT` | 120/minute | everything |
| `RATE_LIMIT_SUGGEST` | 30/minute | `/api/suggest` (autocomplete) |
| `RATE_LIMIT_ANALYZE` | 10/minute | `/api/analyze` |
| `RATE_LIMIT_HEAVY` | 20/minute | `/api/klines`, `/api/monte-carlo`, etc. |
| `RATE_LIMIT_AI` | 5/minute | `/api/ai` |

## 📊 Selected API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analyze` | POST | Full multi-asset analysis |
| `/api/klines` | GET | OHLC candles (Binance crypto / yfinance else) |
| `/api/suggest` | GET | Ticker autocomplete |
| `/api/competitors` | POST | Sector peer comparison |
| `/api/correlation` | POST | Benchmark correlation matrix |
| `/api/options/chain` | POST | Live options chain w/ Greeks |
| `/api/ai` | POST | AI advisory (Ollama → Gemini) |
| `/api/portfolio/*` | GET/POST | Paper trading (buy/sell/short/cover/orders/analytics) |
| `/api/market-summary` | GET | Market dashboard data |

## 📜 License

This project is for educational purposes. Built as part of MSc Financial Technology coursework.

---

<p align="center">Built with Flask, KLineChart & yfinance</p>
