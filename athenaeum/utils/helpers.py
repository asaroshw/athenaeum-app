"""Pure helpers, parsing, provenance, formatting."""
from __future__ import annotations
import re
import pandas as pd
import logging
from datetime import datetime, date
from html import escape as html_escape
from athenaeum.config import GREEN, RED, ORANGE, MUTED, GOLD

logger = logging.getLogger("athenaeum")

def to_float(val):
    if val in [None, "N/A", "", "None", "Stock doesn't pay dividends"]:
        return None
    if isinstance(val, bool) or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace('%', '').replace('x', '').replace('₹', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def is_valid_metric(val):
    if val in [None, "N/A", "", "-", "--", "None", "0", "0.00%", "0.00"]:
        return False
    return to_float(val) is not None


def fmt_indian_currency(val, currency="₹"):
    if not is_valid_metric(val):
        return "N/A"
    try:
        num = float(str(val).replace(',', '').replace('₹', '').replace('%', '').strip())
        if abs(num) >= 10000000:
            return f"{currency}{num/10000000:,.2f} Cr"
        elif abs(num) >= 100000:
            return f"{currency}{num/100000:,.2f} Lakh"
        return f"{currency}{num:,.2f}"
    except (TypeError, ValueError):
        return f"{currency} {val}"


def make_metric(value, source="unknown", period="unknown", as_of=None, currency="INR", confidence=0.7):
    """Lightweight provenance wrapper for auditable metrics."""
    return {
        "value": value,
        "source": source,
        "period": period,
        "as_of": as_of,
        "currency": currency,
        "confidence": float(confidence) if confidence is not None else 0.5,
    }


def metric_value(m):
    if isinstance(m, dict) and "value" in m:
        return m.get("value")
    return m


def rating_color(rating):
    r = (rating or "").upper()
    if "DON" in r and "BUY" in r: return RED
    if "OBSERVE" in r: return ORANGE
    if "BUY" in r: return GREEN
    return MUTED


def style_verdict_text(text):
    """Escape AI/user text first, then highlight known verdict tokens only."""
    if not text:
        return text
    safe = html_escape(str(text))
    return re.sub(
        r"(?i)\bDON.?T\s+BUY\b|\bOBSERVE\b|\bSTRONG\s+BUY\b|\bBUY\b|\bABSTAIN\b",
        lambda m: f'<span style="color:{rating_color(m.group(0))}; font-weight:bold;">{m.group(0)}</span>',
        safe,
    )


def _clamp01(x):
    return max(0.0, min(1.0, float(x)))


def _piecewise_score(value, good, excellent, higher_is_better=True):
    """Map a continuous metric to 0–100.
    - At `good` → ~60
    - At `excellent` → ~100
    - Midpoint between bad and good → ~30
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    v = float(value)
    if not higher_is_better:
        # Invert: lower is better (e.g. PE, D/E)
        if v <= excellent:
            return 100.0
        if v >= good * 2.5:
            return 5.0
        if v <= good:
            # excellent..good → 100..60
            span = max(good - excellent, 1e-9)
            return 60.0 + 40.0 * _clamp01((good - v) / span)
        # good..2.5*good → 60..5
        span = max(good * 1.5, 1e-9)
        return 5.0 + 55.0 * _clamp01((good * 2.5 - v) / span)
    # higher is better
    if v >= excellent:
        return 100.0
    if v <= 0:
        return 5.0
    if v >= good:
        span = max(excellent - good, 1e-9)
        return 60.0 + 40.0 * _clamp01((v - good) / span)
    span = max(good, 1e-9)
    return 5.0 + 55.0 * _clamp01(v / span)


def _parse_date_flex(s):
    if not s:
        return None
    s = re.sub(r"[⏱⏰].*$", "", str(s)).strip()
    s = re.sub(r"\s+\d+h.*$", "", s).strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %B %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s[:20].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_money_inr(s):
    if s is None:
        return None
    t = str(s).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    m = re.search(r"([\d.]+)\s*(Cr|crore)?", t, re.I)
    if not m:
        return None
    try:
        v = float(m.group(1))
        if m.group(2):
            return v  # already in Cr
        return v
    except ValueError:
        return None


def _parse_gmp(s):
    """Return (gmp_rupees, gmp_pct) from strings like 'GMP ₹34 ( +35.05% )'."""
    if not s or str(s).strip() in ("—", "-", "N/A", ""):
        return None, None
    t = str(s)
    rupees = None
    pct = None
    m = re.search(r"₹\s*([\d.]+)", t)
    if m:
        try:
            rupees = float(m.group(1))
        except ValueError:
            pass
    m2 = re.search(r"([+-]?[\d.]+)\s*%", t)
    if m2:
        try:
            pct = float(m2.group(1))
        except ValueError:
            pass
    return rupees, pct


def _parse_price_band(s):
    if not s:
        return None, None
    t = str(s).replace("₹", "").replace(",", "")
    nums = re.findall(r"[\d.]+", t)
    if len(nums) >= 2:
        return float(nums[0]), float(nums[1])
    if len(nums) == 1:
        return float(nums[0]), float(nums[0])
    return None, None


def _slug_from_href(href):
    if not href:
        return None
    m = re.search(r"/ipo/([a-z0-9\-]+)", href)
    return m.group(1) if m else None


def _classify_bucket(open_d, close_d, listing_d, status_hint=None):
    today = datetime.today().date()
    st = (status_hint or "").upper()
    if st in ("OPEN", "CURRENT"):
        return "current"
    if st in ("LISTED", "CLOSED") and listing_d and listing_d <= today:
        return "closed"
    if open_d and open_d > today:
        return "upcoming"
    if open_d and close_d and open_d <= today <= close_d:
        return "current"
    if close_d and close_d < today:
        return "closed"
    if listing_d and listing_d <= today:
        return "closed"
    if open_d and open_d > today:
        return "upcoming"
    return "upcoming"



def _rfr_value(rfr):
    """Normalize get_dynamic_risk_free_rate() to a float (handles tuple or scalar)."""
    if isinstance(rfr, (tuple, list)):
        return float(rfr[0])
    return float(rfr)


def _rfr_source(rfr):
    if isinstance(rfr, (tuple, list)) and len(rfr) > 1:
        return str(rfr[1])
    return "unknown"


html_escape_fn = html_escape  # re-export

