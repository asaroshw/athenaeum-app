"""News and order-book qualitative signals."""
from __future__ import annotations
import logging
import re
import streamlit as st
from athenaeum.config import CATALYST_KEYWORDS, RISK_KEYWORDS, ORDER_BOOK_KEYWORDS
from athenaeum.utils.helpers import to_float

logger = logging.getLogger("athenaeum")
GROWTH_PCT_PATTERN = re.compile(
    r'(\d{1,2})\s*%\s*(?:growth|guidance)|(?:growth|guidance).{0,25}?(\d{1,2})\s*%',
    re.IGNORECASE,
)
try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

def _gemini_key():
    try:
        return st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        return ""

def scan_news_sentiment(recent_news, business_summary):
    """Title-only keyword scan with per-title negation and capped impact.
    Optional Stage-2 LLM classification when _gemini_key() is available.
    """
    NEGATION = ['denies', 'deny', 'clears', 'cleared', 'no evidence', 'exonerated',
                'completed successfully', 'dismissed', 'baseless', 'unfounded',
                'false report', 'not involved', 'refutes', 'rejects claims']
    titles = [n.get('title', '') for n in (recent_news or [])]
    catalyst_hits, risk_hits, confirmed_risks = [], [], []
    for t in titles:
        tl = t.lower()
        cats = [kw.strip() for kw in CATALYST_KEYWORDS if kw in tl]
        risks = [kw.strip() for kw in RISK_KEYWORDS if kw in tl]
        catalyst_hits.extend(cats)
        risk_hits.extend(risks)
        has_neg = any(neg in tl for neg in NEGATION)
        if risks and not has_neg:
            confirmed_risks.extend(risks)
    catalyst_hits = sorted(set(catalyst_hits))
    risk_hits = sorted(set(risk_hits))
    confirmed_risks = sorted(set(confirmed_risks))
    bonus, notes = 0, []
    if len(catalyst_hits) >= 2:
        bonus += 4
        notes.append(f"News catalyst keywords (+4, auxiliary): {', '.join(catalyst_hits[:4])}.")
    elif len(catalyst_hits) == 1:
        bonus += 2
        notes.append(f"News catalyst keyword (+2, auxiliary): {catalyst_hits[0]}.")
    if confirmed_risks:
        bonus -= 6
        notes.append(f"Confirmed risk keywords in news (−6, auxiliary): {', '.join(confirmed_risks[:3])}.")
    elif risk_hits:
        notes.append(f"Risk keywords appear with negation — no penalty ({', '.join(risk_hits[:3])}).")

    # Stage 2: optional LLM materiality classification (fails soft)
    llm_adj = _llm_news_score(titles[:5])
    if llm_adj is not None:
        adj, llm_note = llm_adj
        bonus = max(min(bonus + adj, 5), -5)  # news is auxiliary only
        notes.append(llm_note)

    if bonus != 0 or notes:
        notes.append("News signal is auxiliary only; not a substitute for filings or primary research.")
    return bonus, notes


def _llm_news_score(titles):
    """Optional Gemini classification of headline materiality. Returns (adj, note) or None."""
    if not _gemini_key() or not titles:
        return None
    try:
        client = genai.Client(api_key=_gemini_key())
        prompt = (
            "Classify these stock news headlines for equity materiality. "
            "Return ONLY JSON: {\"sentiment\": -1 to 1, \"materiality\": 0 to 1, "
            "\"confidence\": 0 to 1, \"one_line\": \"...\"}. Headlines:\n"
            + "\n".join(f"- {t}" for t in titles)
        )
        resp = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1),
        )
        text = (resp.text or "").strip()
        m = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if not m:
            return None
        import json
        data = json.loads(m.group(0))
        sent = float(data.get("sentiment", 0))
        mat = float(data.get("materiality", 0))
        conf = float(data.get("confidence", 0))
        score = sent * mat * conf * 10  # roughly −10..+10
        score = max(min(score, 8), -10)
        note = (f"LLM news overlay ({score:+.1f}): sentiment={sent:.2f}, "
                f"materiality={mat:.2f}, conf={conf:.2f}. {data.get('one_line', '')}")
        return round(score), note
    except Exception as e:
        logger.warning("LLM news classification failed: %s", e)
        return None


def extract_order_book_signal(recent_news, business_summary, trailing_revenue_cr=None):
    """Detect order-book/guidance language. Never treat headline % as earnings growth.
    Returns (hits, guidance_hint_pct) where guidance_hint is heavily discounted and optional.
    """
    titles = [n.get('title', '') for n in (recent_news or [])]
    text = " ".join(titles)
    text_lower = text.lower()
    order_hits = sorted(set(kw for kw in ORDER_BOOK_KEYWORDS if kw in text_lower))
    growth_pct_found = None
    match = GROWTH_PCT_PATTERN.search(text)
    if match:
        val = match.group(1) or match.group(2)
        try:
            v = float(val)
            # Headline "20% growth" is not EPS growth — only allow as a soft hint, capped low
            if 5 <= v <= 40:
                growth_pct_found = min(v * 0.4, 12.0)  # 40% of headline, max 12pp contribution
        except (TypeError, ValueError) as e:
            logger.debug("Order-book growth parse failed: %s", e)
    if order_hits and trailing_revenue_cr and trailing_revenue_cr > 0:
        m_size = re.search(r'(?:rs\.?|inr|₹)\s*([\d,]+)\s*(?:crore|cr)', text, re.IGNORECASE)
        if m_size:
            try:
                order_cr = float(m_size.group(1).replace(",", ""))
                if order_cr / trailing_revenue_cr < 0.05:
                    growth_pct_found = None  # immaterial order
            except (TypeError, ValueError) as e:
                logger.debug("Order size parse failed: %s", e)
    return order_hits, growth_pct_found

# ============================================================
# 5. CHECKLISTS
# ============================================================

