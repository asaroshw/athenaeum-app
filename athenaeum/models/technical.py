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


def _compute_technical_signals(hist, current_price=None):
    """Single shared computation of every raw technical signal used by BOTH
    compute_technical_score (the scoring/verdict pipeline) and
    compute_technical_regime_badges (the display-only 'traffic light'
    badges).

    Previously these were two independently-implemented calculations — the
    badges' SMA Trend used ONLY 50D/200D while the score's trend component
    used 20D/50D/200D together with different point weightings — and could
    disagree (a "BULLISH" badge next to a "NEUTRAL" overall regime, with no
    explanation for the apparent contradiction). Sourcing both public
    functions from this one computation makes that disagreement
    structurally impossible: they now read the same numbers, not two
    separate implementations of "the same" calculation that can silently
    drift apart. Neither public function's signature changes.

    Returns a dict of raw signal values, or None if there isn't enough
    price history (<30 closes) for any of this to be meaningful.
    """
    if hist is None or hist.empty or 'Close' not in hist.columns:
        return None
    closes = hist['Close'].dropna()
    if len(closes) < 30:
        return None
    price = float(current_price) if current_price else float(closes.iloc[-1])

    sma20_raw = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else None
    sma50_raw = closes.rolling(50).mean().iloc[-1] if len(closes) >= 50 else None
    sma200_raw = closes.rolling(200).mean().iloc[-1] if len(closes) >= 200 else None
    sma20 = float(sma20_raw) if sma20_raw is not None and pd.notna(sma20_raw) else None
    sma50 = float(sma50_raw) if sma50_raw is not None and pd.notna(sma50_raw) else None
    sma200 = float(sma200_raw) if sma200_raw is not None and pd.notna(sma200_raw) else None

    trend_parts = []
    trend_pts = 50.0
    if sma50 is not None:
        trend_pts += 15 if price > sma50 else -15
    if sma200 is not None:
        trend_pts += 15 if price > sma200 else -20
    if sma20 is not None and sma50 is not None:
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

    def _ret(series, n):
        if len(series) > n and float(series.iloc[-n]) > 0:
            return (float(series.iloc[-1]) / float(series.iloc[-n]) - 1.0) * 100
        return None
    r3 = _ret(closes, 63)
    r6 = _ret(closes, 126)
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

    high_52 = float(closes.tail(252).max()) if len(closes) >= 50 else float(closes.max())
    dd = (price / high_52 - 1.0) * 100 if high_52 > 0 else 0
    risk_score = max(0, min(100, 80 + dd * 1.5))

    v_ratio = None
    vol_score = 50.0
    if 'Volume' in hist.columns and len(hist) >= 20:
        vol = hist['Volume'].dropna()
        if len(vol) >= 20:
            v20_mean = max(float(vol.iloc[-20:].mean()), 1)
            v_ratio = float(vol.iloc[-5:].mean()) / v20_mean
            vol_score = max(0, min(100, 50 + max(min((v_ratio - 1.0) * 40, 25), -20)))

    return {
        "price": price, "sma20": sma20, "sma50": sma50, "sma200": sma200,
        "trend_score": trend_score, "drift": drift,
        "r3": r3, "r6": r6, "nifty_r3": nifty_r3, "nifty_r6": nifty_r6, "mom_score": mom_score,
        "risk_score": risk_score, "v_ratio": v_ratio, "vol_score": vol_score,
    }


def compute_technical_regime_badges(hist, current_price=None):
    """Three headline technical signals for the UI's 'traffic light' badges:
    SMA Trend, Volume Ratio (5D/20D), and Nifty-Relative Momentum. Sourced
    from _compute_technical_signals — the SAME computation
    compute_technical_score uses — so a badge can no longer disagree with
    the overall technical regime it sits next to (see that helper's
    docstring for why this used to be possible). Nothing this function
    returns feeds the scoring/verdict pipeline; it is display-only.

    Returns a dict of three {"label": str, "detail": str, "tone": str} entries keyed
    "sma_trend", "volume_ratio", "nifty_momentum". tone is one of
    "positive" / "neutral" / "negative" / "na" for traffic-light coloring.
    """
    na = {"label": "N/A", "detail": "Not enough price history.", "tone": "na"}
    out = {"sma_trend": dict(na), "volume_ratio": dict(na), "nifty_momentum": dict(na)}
    sig = _compute_technical_signals(hist, current_price)
    if sig is None:
        return out

    # --- SMA Trend: label derived from trend_score (the same value that
    # feeds the overall technical score), not a standalone 50D/200D-only
    # comparison — the detail text still names the specific SMA structure. ---
    if sig["sma50"] is not None and sig["sma200"] is not None:
        if sig["price"] > sig["sma50"] and sig["price"] > sig["sma200"]:
            detail = "Price above 50D & 200D SMA"
        elif sig["price"] < sig["sma50"] and sig["price"] < sig["sma200"]:
            detail = "Price below 50D & 200D SMA"
        else:
            detail = "Price between 50D & 200D SMA"
    elif sig["sma50"] is not None:
        detail = f"Price {'above' if sig['price'] > sig['sma50'] else 'below'} 50D SMA (200D N/A)"
    else:
        detail = None
    if detail is not None:
        if sig["trend_score"] >= 60:
            out["sma_trend"] = {"label": "BULLISH", "detail": detail, "tone": "positive"}
        elif sig["trend_score"] <= 40:
            out["sma_trend"] = {"label": "BEARISH", "detail": detail, "tone": "negative"}
        else:
            out["sma_trend"] = {"label": "MIXED", "detail": detail, "tone": "neutral"}

    # --- Volume Ratio ---
    if sig["v_ratio"] is not None:
        v_ratio = sig["v_ratio"]
        if v_ratio >= 1.3:
            out["volume_ratio"] = {"label": f"{v_ratio:.2f}x", "detail": "5D volume well above 20D average", "tone": "positive"}
        elif v_ratio <= 0.7:
            out["volume_ratio"] = {"label": f"{v_ratio:.2f}x", "detail": "5D volume well below 20D average", "tone": "negative"}
        else:
            out["volume_ratio"] = {"label": f"{v_ratio:.2f}x", "detail": "5D volume near 20D average", "tone": "neutral"}

    # --- Nifty-Relative Momentum: ~3-month stock return minus ~3-month Nifty return ---
    r3, nifty_r3 = sig["r3"], sig["nifty_r3"]
    if r3 is not None and nifty_r3 is not None:
        excess = r3 - nifty_r3
        if excess >= 3:
            out["nifty_momentum"] = {"label": f"{excess:+.1f}%", "detail": "Outperforming Nifty 50 (~3M)", "tone": "positive"}
        elif excess <= -3:
            out["nifty_momentum"] = {"label": f"{excess:+.1f}%", "detail": "Underperforming Nifty 50 (~3M)", "tone": "negative"}
        else:
            out["nifty_momentum"] = {"label": f"{excess:+.1f}%", "detail": "Tracking Nifty 50 (~3M)", "tone": "neutral"}
    elif r3 is not None:
        # Nifty comparison unavailable (network/cache miss) — fall back to absolute momentum
        if r3 >= 8:
            out["nifty_momentum"] = {"label": f"{r3:+.1f}%", "detail": "Strong absolute 3M return (Nifty n/a)", "tone": "positive"}
        elif r3 <= -8:
            out["nifty_momentum"] = {"label": f"{r3:+.1f}%", "detail": "Weak absolute 3M return (Nifty n/a)", "tone": "negative"}
        else:
            out["nifty_momentum"] = {"label": f"{r3:+.1f}%", "detail": "Flat absolute 3M return (Nifty n/a)", "tone": "neutral"}

    return out


def compute_technical_score(hist, current_price=None):
    """Grouped technical score: Trend / Momentum (rel. to Nifty when available) / Risk / Volume.
    Returns (score, regime_label, horizon_hint, drift). Horizon is a thesis placeholder, not a prediction.
    Signature and return shape unchanged — see _compute_technical_signals's
    docstring for what changed underneath it.
    """
    sig = _compute_technical_signals(hist, current_price)
    if sig is None:
        return 50.0, "NEUTRAL", "Thesis-dependent", None

    # Weighted groups (not 4 independent additives)
    tech = round(0.35 * sig["trend_score"] + 0.30 * sig["mom_score"]
                 + 0.20 * sig["risk_score"] + 0.15 * sig["vol_score"], 1)

    if tech >= 65:
        regime, horizon = "POSITIVE", "Thesis-dependent (constructive technical regime)"
    elif tech <= 35:
        regime, horizon = "NEGATIVE", "Thesis-dependent (weak technical regime)"
    else:
        regime, horizon = "NEUTRAL", "Thesis-dependent"
    return tech, regime, horizon, sig["drift"]

