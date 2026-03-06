# FinanceIQ — Financial Analytics Platform

A comprehensive financial analytics web application built with Python and Streamlit, providing real-time market data visualization and portfolio simulation capabilities.

## Features

### 📊 Interactive Charting
- Real-time candlestick charts with technical indicators
- Customizable timeframes and intervals
- Multiple chart types (line, candlestick, bar)

### 💼 Portfolio Simulation
- **Advanced Order Types:** Market orders, limit orders, stop-loss orders
- **Short Selling:** Full short position support with margin requirements
- **Realistic Market Friction:** Slippage modeling and commission structure
- **Position Tracking:** Real-time P&L calculation and portfolio analytics

### 📈 Risk Management
- Portfolio value tracking
- Position sizing analysis
- Drawdown monitoring

## Tech Stack

| Component | Technology |
|---|---|
| **Frontend** | Streamlit |
| **Data** | yfinance, pandas |
| **Visualization** | Plotly, matplotlib |
| **Backend** | Python 3.x |

## Installation

```bash
pip install streamlit yfinance pandas plotly matplotlib
```

## Usage

```bash
streamlit run app.py
```

## Key Concepts Implemented

- **Market Microstructure:** Bid-ask spread, slippage simulation
- **Order Book Mechanics:** Limit order matching logic
- **Portfolio Theory:** Position sizing, risk-adjusted returns
- **Trading Psychology:** Real-time P&L feedback

## Future Enhancements

- Backtesting engine for strategy validation
- Machine learning price prediction integration
- Multi-asset portfolio optimization
- Real-time news sentiment analysis

---

*Built as part of MSc Financial Technology coursework exploring practical applications of quantitative finance concepts.*
