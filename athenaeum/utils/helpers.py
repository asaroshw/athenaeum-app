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


def safe_pct_change(new_val, old_val):
    """Percent change from old_val to new_val: (new - old) / |old| * 100.

    Dividing by abs(old_val) rather than old_val handles a loss-to-profit
    turnaround correctly (old=-100, new=50 -> +150%, not the sign-flipped
    -150% that a plain (new-old)/old would give).

    Returns None when old_val is missing, NaN, or exactly 0 — a zero or absent
    base makes percent change mathematically undefined, so this returns None
    (letting the caller skip the metric) instead of silently substituting a
    placeholder divisor. Several call sites in data/equity.py used to write
    `value_that_could_be_zero or 1` to dodge a ZeroDivisionError, which
    computes a wrong (sometimes wildly wrong, e.g. thousands of percent)
    number instead of correctly declining to answer when the base is
    genuinely zero. This is the one place that logic should live.
    """
    if new_val is None or old_val is None:
        return None
    try:
        new_val = float(new_val)
        old_val = float(old_val)
    except (TypeError, ValueError):
        return None
    if pd.isna(new_val) or pd.isna(old_val) or old_val == 0:
        return None
    return ((new_val - old_val) / abs(old_val)) * 100


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


def render_ai_markdown(text):
    """Convert the small, common markdown subset AI narratives actually use
    (bold, bullet-list markers) into real HTML, then run style_verdict_text's
    verdict-word highlighting on the result.

    Streamlit's markdown parser only fires for content passed directly to
    st.markdown() as markdown source — it does NOT re-parse markdown syntax
    found INSIDE HTML you've written yourself once unsafe_allow_html=True is
    active (as every card() call wrapping AI narrative text in a hand-written
    <p>/<div> does). AI text was rendering literally, asterisks and all, e.g.
    "* **Robust Top-Line Growth:**" instead of a bolded bullet point.

    Escapes first (so this can never become an HTML/script injection vector
    for AI-generated or scraped text), converts bold and bullets, THEN
    highlights verdict words — in that order, so verdict-highlighting's
    <span> tags land around already-converted text rather than the other
    way around. Call sites that used to call style_verdict_text(narrative)
    directly should call this instead; style_verdict_text itself is
    unchanged for callers that only need verdict-word coloring on
    non-markdown text (e.g. a bare "DON'T BUY" verdict label).
    """
    if not text:
        return text
    safe = html_escape(str(text))

    # Bold: **text** (non-greedy, paired only — an unpaired stray ** is left
    # as literal text rather than guessed at, same principle as any
    # regex-based converter without a full parser).
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)

    # Leading bullet marker on its own line ("* item" or "- item") -> a real
    # bullet glyph. Deliberately not building actual <ul>/<li> structure —
    # every call site already renders this text inside a
    # white-space:pre-wrap <p>, which already preserves line breaks
    # correctly, so swapping just the marker character is enough to fix the
    # visible "raw asterisk" bug without needing multi-line list-state
    # tracking that would be more code for the same visual result.
    #
    # Runs BEFORE italic, not after: a bullet line whose text ALSO contains
    # genuine italic emphasis later on the same line (e.g.
    # "* **Robust Growth:** Revenue grew at a *very* strong clip.") would
    # otherwise leave the leading bullet "*" as the only asterisk before the
    # italic pair — the non-greedy italic regex would then pair the BULLET
    # marker with the italic phrase's OPENING "*", swallowing everything
    # between them as "italic" and leaving the phrase's closing "*"
    # dangling. Converting the bullet marker first removes that leftover
    # asterisk before italic pairing ever runs, so it can't happen.
    safe = re.sub(r"(?m)^(\s*)[-*]\s+", r"\1• ", safe)

    # Italic: *text* — runs after bold AND the bullet-marker conversion
    # above, so every ** pair and every leading bullet "*" is already
    # consumed; any "*" remaining at this point can only be a genuine
    # single-asterisk emphasis pair (or a stray unpaired one, left as
    # literal text same as an unpaired ** would be).
    safe = re.sub(r"\*(.+?)\*", r"<em>\1</em>", safe)

    return style_verdict_text_from_escaped(safe)


def style_verdict_text_from_escaped(safe):
    """The verdict-word-highlighting half of style_verdict_text, factored out
    so render_ai_markdown can run it AFTER markdown conversion on text that's
    already been through html_escape, without escaping twice."""
    return re.sub(
        r"(?i)\bDON(?:'|&#x27;|&#39;|&apos;)?T\s+BUY\b|\bOBSERVE\b|\bSTRONG\s+BUY\b|\bBUY\b|\bABSTAIN\b",
        lambda m: f'<span style="color:{rating_color(m.group(0))}; font-weight:bold;">{m.group(0)}</span>',
        safe,
    )


def style_verdict_text(text):
    """Escape AI/user text first, then highlight known verdict tokens only.
    For text that may also contain AI-generated markdown (bold, bullets),
    use render_ai_markdown instead — see its docstring.

    The whole-phrase "DON'T BUY" pattern used `.?` (0-or-1 characters) to
    span the apostrophe — correct for the RAW apostrophe ('), but
    html_escape runs first and turns it into the 6-character entity
    "&#x27;" (Python's html.escape default). `.?` can't span 6 characters,
    so the whole-phrase match silently failed, and the regex engine fell
    through to the standalone \bBUY\b alternative later in the string —
    "DON'T" stayed unstyled while "BUY" alone got wrapped in a GREEN span,
    exactly backwards for a DON'T BUY verdict. Explicitly enumerating the
    realistic apostrophe forms (raw, and the common HTML entity spellings)
    fixes the match without weakening it to "any character(s)", which would
    risk swallowing real text between DON and T.
    """
    if not text:
        return text
    safe = html_escape(str(text))
    return style_verdict_text_from_escaped(safe)


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
    """Parse a messy IPO date string into a date, or None if it can't be
    read with reasonable confidence.

    Hardened after a live bug report: an already-closed/listed IPO (Milky
    Mist Dairy Food — closed 13 Aug 2026, listed 18 Aug 2026) was still
    showing under "Upcoming". Investigation traced the actual live-app
    mechanism to the merge/dedup gap fixed separately in this file (a
    stale, unmerged fragment from one source with no closing evidence could
    sit alongside a correctly-closed record from another source without
    ever combining) — _bucket_ipo's own classification logic already
    handles a past listing/close date correctly once it receives one.
    But while investigating, two real, independent date-format gaps were
    found in the sources this app already scrapes and are hardened here
    regardless, since they will otherwise silently produce unparseable
    (None) dates for other IPOs even now that the merge issue is fixed:
      - Chittorgarh's individual IPO detail page renders Open/Close/
        Allotment/Listing dates almost exclusively with a leading
        day-of-week: "Tue, Aug 11, 2026" — no format below matched that.
      - A combined open-close range in one string ("11 to 13 Aug, 2026"),
        which some cells on Chittorgarh's site use for the "IPO Date" field.
    Also centralizes ordinal-suffix stripping ("10th", "3rd") here — this
    was previously done locally inside _scrape_screener_ipo_list only,
    meaning any OTHER caller passing an ordinal-suffixed string had no
    protection at all.
    """
    if not s:
        return None
    s = str(s)
    # BeautifulSoup commonly turns &nbsp; into \xa0, not a plain space — left
    # alone, that silently breaks an otherwise-correct strptime match since
    # \xa0 isn't treated as equivalent to the literal space in the format
    # string.
    s = s.replace("\xa0", " ")
    s = re.sub(r"[⏱⏰].*$", "", s).strip()
    s = re.sub(r"\s+\d+h.*$", "", s).strip()
    # Strip a leading day-of-week ("Tue, Aug 11, 2026" -> "Aug 11, 2026") —
    # see the docstring above; this is Chittorgarh's dominant format for
    # exactly the fields IPO bucketing depends on most.
    s = re.sub(r"^(mon|tue|tues|wed|weds|thu|thurs|fri|sat|sun)[a-z]*\.?,?\s+",
                "", s, flags=re.IGNORECASE)
    # A combined range in one string ("11 to 13 Aug, 2026", "11-13 Aug 2026",
    # "11 Aug - 13 Aug, 2026") — take the LATER date. Callers that need both
    # ends of a range already have their own dedicated parsers
    # (_parse_dd_mon_range, _scrape_screener_ipo_list's _parse_period); a
    # single date fed to THIS function from a range-shaped cell is far more
    # often used as a close/listing/allotment date than an open date, and a
    # too-early date is what actually causes an IPO to misclassify as still
    # open/upcoming rather than closed.
    range_match = re.search(
        r"\d{1,2}\s*(?:to|-|–)\s*(\d{1,2})\s+([A-Za-z]{3,9})[,.]?\s+(\d{4})", s)
    if range_match:
        d2, mon, yr = range_match.group(1), range_match.group(2), range_match.group(3)
        s = f"{d2} {mon} {yr}"
    # Strip ordinal suffixes ("10th", "3rd", "1st", "22nd") — see docstring.
    s = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %B %Y",
                "%b %d, %Y", "%b %d %Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b, %Y"):
        try:
            return datetime.strptime(s[:24].strip(), fmt).date()
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



def _rfr_value(rfr):
    """Normalize get_dynamic_risk_free_rate() to a float (handles tuple or scalar)."""
    if isinstance(rfr, (tuple, list)):
        return float(rfr[0])
    return float(rfr)


def _rfr_source(rfr):
    if isinstance(rfr, (tuple, list)) and len(rfr) > 1:
        return str(rfr[1])
    return "unknown"


def compute_risk_reward(entry_low, entry_high, target_price, stop_loss):
    """Risk/Reward ratio from the entry-range midpoint, expressed as (ratio, 'entry_mid').

    Risk   = entry_mid - stop_loss   (distance down to the stop)
    Reward = target_price - entry_mid (distance up to the modeled target)
    Returns (ratio, entry_mid) where ratio = Reward / Risk, or (None, entry_mid)
    if a required input is missing or the risk leg is non-positive (guards
    against divide-by-zero / a nonsensical stop placed at or above entry).
    """
    entry_low = to_float(entry_low)
    entry_high = to_float(entry_high)
    target_price = to_float(target_price)
    stop_loss = to_float(stop_loss)
    if entry_low is None or entry_high is None or target_price is None or stop_loss is None:
        return None, None
    entry_mid = (entry_low + entry_high) / 2.0
    risk = entry_mid - stop_loss
    reward = target_price - entry_mid
    if risk <= 0:
        return None, entry_mid
    return round(reward / risk, 2), entry_mid


def compute_entry_stop_range(support, current_price, atr):
    """Suggested entry-price range and stop-loss from a support proxy, current
    price, and ATR (volatility). Returns (entry_low, entry_high, stop_loss),
    each rounded to 2dp, with entry_high guaranteed >= entry_low.

    Extracted out of run_predictive_pipeline so it's unit-testable on its own —
    a prior inline version derived entry_high from `support` directly instead
    of from entry_low, which silently inverted the range (entry_low >
    entry_high) whenever current_price*0.85 sat above support — common once a
    stock has rallied away from its historical high-volume zone. See
    test_entry_range_never_inverts for the regression coverage.
    """
    entry_low = round(max(support, current_price * 0.85), 2)
    offset = 0.5 * atr if atr else current_price * 0.02
    entry_high = round(max(entry_low + offset, entry_low), 2)
    if entry_low > current_price:
        entry_low, entry_high = round(current_price * 0.95, 2), round(current_price, 2)
    raw_stop_loss = entry_low - (1.5 * atr if atr else entry_low * 0.05)
    stop_loss = round(max(entry_low * 0.80, raw_stop_loss), 2)
    return entry_low, entry_high, stop_loss


html_escape_fn = html_escape  # re-export

