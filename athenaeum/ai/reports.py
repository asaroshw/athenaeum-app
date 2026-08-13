"""Gemini synthesis layer — explains quantitative engine, does not replace it."""
from __future__ import annotations
import logging
import streamlit as st
from athenaeum.utils.helpers import style_verdict_text

logger = logging.getLogger("athenaeum")
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

def generate_comprehensive_report(metrics, ticker):
    if not _gemini_key() or genai is None:
        return "AI narrative unavailable (GEMINI_API_KEY not configured or google-genai not installed)."
    client = genai.Client(api_key=_gemini_key())
    sys = """You are a research narrative synthesis layer over a quantitative screening model.
You are NOT an independent senior equity analyst with access to filings, auditor notes, or
segment economics. Explain and stress-test the quantitative outputs only.

Output exactly 8 numbered sections:
1. VALUATION & FAIR VALUE
2. FUTURE GROWTH & OUTLOOK
3. PAST PERFORMANCE & EARNINGS QUALITY
4. FINANCIAL HEALTH & BALANCE SHEET
5. DIVIDEND & CAPITAL ALLOCATION
6. MANAGEMENT & COMPENSATION
7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
8. NARRATIVE VERDICT
Provide ONLY narrative reasoning — no invented numbers beyond what is given to you.

REALITY CHECKER MANDATE:
- If implied upside/downside is extreme (beyond +150% or beyond -50%), say so and urge caution.
- Treat news keyword signals as weak evidence only.
- Prefer Bear/Base/Bull ranges over any single target price when ranges are provided.
- State that composite scores are model scores, not calibrated probabilities.

NO BLIND AGREEMENT MANDATE:
- Quantitative baseline and forward catalysts can disagree; weigh both and explain.
- Do not upgrade a weak quantitative case purely because of optimistic language."""
    pred = metrics.get('predictive', {})
    news_titles = "; ".join([n['title'] for n in (metrics.get('recent_news') or [])[:5]]) or "No recent headlines found."
    turnaround_note = " TURNAROUND flagged." if metrics.get('is_turnaround') else ""
    order_book_note = (f" Forward catalyst signal(s) detected in recent news: {', '.join(metrics.get('order_book_hits', [])[:4])}."
                        if metrics.get('order_book_hits') else " No explicit order-book/guidance signal detected in recent news.")
    
    target_display = f"{metrics['currency']}{pred.get('target_price')}" if pred.get('verdict') != "DON'T BUY" else "N/A (Model Rejected due to strict veto)"
    
    pmt = (f"Target: {metrics['name']} ({ticker}). Sector: {metrics.get('sector')} "
           f"(profile: {metrics.get('sector_profile')}).{turnaround_note}{order_book_note} "
           f"Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. P/B: {metrics['pb_ratio']}. "
           f"EV/EBITDA: {metrics['ev_ebitda']}. Debt/Eq: {metrics['debt_to_equity']}. "
           f"Valuation model used: {pred.get('model_used')}. Forward growth assumption used in the model: "
           f"{pred.get('growth_used')}%. Quantitative Target Price: {target_display}. "
           f"System Verdict: {pred.get('verdict')} (composite score {pred.get('composite_score')}/100 — "
           f"fundamental {pred.get('fundamental_score')}, intrinsic {pred.get('intrinsic_score')}, "
           f"technical {pred.get('technical_score')}). Recent news headlines: {news_titles}")
    return client.models.generate_content(model='gemini-3.5-flash-lite', contents=pmt,
                                          config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2)).text


# ============================================================
# IPO FEATURE — DATA ENGINE (ipomarket.in primary + graceful fallbacks)
# ============================================================
from datetime import datetime, timedelta
from html import escape as _esc

_IPO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}


def ipo_ai_narrative(detail: dict, score, verdict, pros, cons, bucket: str = "current") -> str:
    try:
        if not _gemini_key():
            return "AI narrative unavailable (no GEMINI_API_KEY configured)."
        client = genai.Client(api_key=_gemini_key())
        if bucket == "current":
            sys = (
                "You are an IPO research synthesis layer. Write 4-6 paragraphs covering: "
                "business overview, financial health from provided RHP numbers, valuation context, "
                "key business-specific risks (ignore generic macro risks), and a final BUY or ABSTAIN "
                "recommendation consistent with the given score. Do not invent numbers."
            )
        elif bucket == "closed":
            sys = (
                "Summarize this closed IPO: issue outcome, subscription/GMP if given, and listing "
                "result if available. Do NOT give a BUY/ABSTAIN recommendation. Do not invent numbers."
            )
        else:
            sys = (
                "Summarize this upcoming IPO using available RHP-style facts. Do NOT invent GMP, "
                "subscription, or a BUY/ABSTAIN verdict. Flag missing data explicitly."
            )
        news_txt = "; ".join(n.get("title", "") for n in (detail.get("ipo_news") or [])[:4])
        prompt = (
            f"IPO: {detail.get('name')} ({detail.get('symbol')}). Bucket: {bucket}. "
            f"Offer: {detail.get('offer_type','N/A')}. Issue size: {detail.get('issue_size_str','N/A')}. "
            f"Revenue CAGR: {detail.get('revenue_cagr','N/A')}. "
            f"Profitable: {detail.get('is_profitable_latest','N/A')}. "
            f"GMP%: {detail.get('gmp_pct','N/A')}. Sub: {detail.get('subscription_total','N/A')}. "
            f"Score: {score}. Verdict: {verdict}. "
            f"Pros: {'; '.join(pros[:4]) or 'None'}. Cons: {'; '.join(cons[:4]) or 'None'}. "
            f"About: {(detail.get('about') or '')[:400]}. News: {news_txt or 'None'}."
        )
        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite", contents=prompt,
            config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2))
        return resp.text
    except Exception as e:
        return f"AI narrative unavailable: {e}"

