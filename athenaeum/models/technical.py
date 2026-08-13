"""Technical analysis scores."""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("athenaeum")

import time as _time
_nifty_cache = {"ts": 0.0, "r3": None, "r6": None}
_NIFTY_TTL = 3600  # 1 hour

def _get_nifty_momentum():
    """Return (r3, r6) for Nifty 50 — cached for 1 hour to avoid repeated network calls."""
    global _nifty_cache
    now = _time.monotonic()
    if now - _nifty_cache["ts"] < _NIFTY_TTL:
        return _nifty_cache["r3"], _nifty_cache["r6"]
    try:
        n_hist = yf.Ticker("^NSEI").history(period="1y")
        if n_hist is not None and not n_hist.empty and "Close" in n_hist.columns:
            nc = n_hist["Close"].dropna()
            def _r(series, n):
                if len(series) > n and float(series.iloc[-n]) > 0:
                    return (float(series.iloc[-1]) / float(series.iloc[-n]) - 1.0) * 100
                return None
            _nifty_cache = {"ts": now, "r3": _r(nc, 63), "r6": _r(nc, 126)}
        else:
            _nifty_cache["ts"] = now  # avoid hammering on empty response
    except Exception as e:
        logger.debug("Nifty fetch failed: %s", e)
        _nifty_cache["ts"] = now  # avoid hammering on errors
    return _nifty_cache["r3"], _nifty_cache["r6"]


def calculate_vwap_support(df):
    if df is None or df.empty or 'Close' not in df.columns or 'Volume' not in df.columns:
        return None
    d = df.dropna(subset=['Close', 'Volume'])
    if d.empty: return None
    d = d.copy()
    d['PriceBin'] = pd.cut(d['Close'], bins=20)
    vol_by_bin = d.groupby('PriceBin', observed=True)['Volume'].sum()
    if vol_by_bin.empty: return None
    return vol_by_bin.idxmax().mid


def calculate_atr(df, period=14):
    if df is None or len(df) < period + 1: return None
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    val = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else None


def compute_technical_score(hist, current_price=None):
    """Grouped technical score: Trend / Momentum (rel. to Nifty when available) / Risk / Volume.
    Returns (score, regime_label, horizon_hint, drift). Horizon is a thesis placeholder, not a prediction.
    """
    if hist is None or hist.empty or 'Close' not in hist.columns:
        return 50.0, "NEUTRAL", "Thesis-dependent", None
    closes = hist['Close'].dropna()
    if len(closes) < 30:
        return 50.0, "NEUTRAL", "Thesis-dependent", None
    price = float(current_price) if current_price else float(closes.iloc[-1])

    # --- Group 1: Trend (SMA structure + mild drift, averaged) ---
    trend_parts = []
    sma20 = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else None
    sma50 = closes.rolling(50).mean().iloc[-1] if len(closes) >= 50 else None
    sma200 = closes.rolling(200).mean().iloc[-1] if len(closes) >= 200 else None
    trend_pts = 50.0
    if sma50 is not None and pd.notna(sma50):
        trend_pts += 15 if price > sma50 else -15
    if sma200 is not None and pd.notna(sma200):
        trend_pts += 15 if price > sma200 else -20
    if sma20 is not None and sma50 is not None and pd.notna(sma20) and pd.notna(sma50):
        trend_pts += 10 if sma20 > sma50 else -10
    trend_parts.append(max(0, min(100, trend_pts)))
    drift = None
    try:
        log_prices = np.log(closes.values.astype(float))
        if np.all(np.isfinite(log_prices)) and len(log_prices) > 30:
            slope, _ = np.polyfit(np.arange(len(log_prices)), log_prices, 1)
            drift = float(np.exp(slope * 252) - 1)
            trend_parts.append(max(0, min(100, 50 + max(min(drift * 60, 25), -25))))
    except Exception as e:
        logger.debug("Drift calc failed: %s", e)
    trend_score = sum(trend_parts) / len(trend_parts)

    # --- Group 2: Momentum (absolute + relative vs Nifty when available) ---
    def _ret(series, n):
        if len(series) > n and float(series.iloc[-n]) > 0:
            return (float(series.iloc[-1]) / float(series.iloc[-n]) - 1.0) * 100
        return None
    r3 = _ret(closes, 63)
    r6 = _ret(closes, 126)
    # Relative to Nifty 50 — cached at module level (TTL ~1h) to avoid repeated network calls
    nifty_r3 = nifty_r6 = None
    try:
        nifty_r3, nifty_r6 = _get_nifty_momentum()
    except Exception as e:
        logger.debug("Nifty relative momentum unavailable: %s", e)
    mom_parts = []
    if r3 is not None:
        abs_m = 50 + max(min(r3 * 0.9, 20), -20)
        if nifty_r3 is not None:
            excess = r3 - nifty_r3
            abs_m = 50 + max(min(excess * 1.2, 25), -25)
        mom_parts.append(max(0, min(100, abs_m)))
    if r6 is not None:
        abs_m = 50 + max(min(r6 * 0.7, 18), -18)
        if nifty_r6 is not None:
            excess = r6 - nifty_r6
            abs_m = 50 + max(min(excess * 1.0, 22), -22)
        mom_parts.append(max(0, min(100, abs_m)))
    mom_score = sum(mom_parts) / len(mom_parts) if mom_parts else 50.0

    # --- Group 3: Risk / drawdown ---
    high_52 = float(closes.tail(252).max()) if len(closes) >= 50 else float(closes.max())
    dd = (price / high_52 - 1.0) * 100 if high_52 > 0 else 0
    risk_score = max(0, min(100, 80 + dd * 1.5))

    # --- Group 4: Volume ---
    vol_score = 50.0
    if 'Volume' in hist.columns and len(hist) >= 20:
        vol = hist['Volume'].dropna()
        if len(vol) >= 20:
            v_ratio = float(vol.iloc[-5:].mean() / max(vol.iloc[-20:].mean(), 1))
            vol_score = max(0, min(100, 50 + max(min((v_ratio - 1.0) * 40, 25), -20)))

    # Weighted groups (not 6 independent additives)
    tech = round(0.35 * trend_score + 0.30 * mom_score + 0.20 * risk_score + 0.15 * vol_score, 1)

    if tech >= 65:
        regime, horizon = "POSITIVE", "Thesis-dependent (constructive technical regime)"
    elif tech <= 35:
        regime, horizon = "NEGATIVE", "Thesis-dependent (weak technical regime)"
    else:
        regime, horizon = "NEUTRAL", "Thesis-dependent"
    return tech, regime, horizon, drift

