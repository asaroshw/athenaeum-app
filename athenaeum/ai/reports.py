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

def _algorithmic_fallback_report(metrics, ticker):
    """Deterministic, template-based 8-section narrative used when GEMINI_API_KEY is
    not configured (or google-genai isn't installed). Mirrors the AI report's section
    structure using only numbers the pipeline has already computed — nothing invented."""
    pred = metrics.get('predictive', {}) or {}
    cur = metrics.get('currency', '₹')
    verdict = pred.get('verdict', 'N/A')
    composite, fundamental = pred.get('composite_score'), pred.get('fundamental_score')
    intrinsic, technical = pred.get('intrinsic_score'), pred.get('technical_score')
    pe, pb, ev_ebitda, de = metrics.get('pe_ratio'), metrics.get('pb_ratio'), metrics.get('ev_ebitda'), metrics.get('debt_to_equity')
    model_used, growth = pred.get('model_used', 'DCF'), pred.get('growth_used')
    bear, base, bull = pred.get('bear_value'), pred.get('base_value'), pred.get('bull_value')
    news_titles = [n.get('title', '') for n in (metrics.get('recent_news') or [])[:5] if n.get('title')]
    order_hits = metrics.get('order_book_hits') or []

    def sec(n, title, body):
        return f"**{n}. {title}**\n{body}\n"

    val_body = (f"The model applies a {model_used} approach with a {growth}% forward growth assumption, "
                f"producing a base-case fair value of {cur}{base}." if base is not None
                else "A fair value estimate is not currently available for this name.")
    if bear is not None and bull is not None:
        val_body += f" Scenario range — Bear {cur}{bear} / Base {cur}{base} / Bull {cur}{bull}."
    val_body += f" Trading multiples: P/E {pe if pe is not None else 'N/A'}, P/B {pb if pb is not None else 'N/A'}, EV/EBITDA {ev_ebitda if ev_ebitda is not None else 'N/A'}."

    growth_body = (f"Forward growth assumption used in the model: {growth}%. " if growth is not None else "")
    growth_body += (f"Recent news carries possible forward catalyst signal(s): {', '.join(order_hits[:4])}."
                     if order_hits else "No explicit order-book or forward-guidance signal was detected in recent news.")

    perf_body = ("Flagged as a potential turnaround situation by the model." if metrics.get('is_turnaround')
                 else "No turnaround flag raised by the model.")
    perf_body += f" Fundamental sub-score: {fundamental if fundamental is not None else 'N/A'}/100."

    fin_body = f"Debt/Equity: {de if de is not None else 'N/A'}."
    try:
        fin_body += " Leverage appears elevated." if de is not None and float(de) > 1 else " Leverage appears moderate to low." if de is not None else ""
    except Exception:
        pass

    verdict_body = f"System Verdict: {verdict}"
    if composite is not None:
        verdict_body += f" (composite score {composite}/100 — fundamental {fundamental}, intrinsic {intrinsic}, technical {technical})."
    if news_titles:
        verdict_body += f" Recent headlines: {'; '.join(news_titles)}."
    verdict_body += (" This is a template-generated summary of the quantitative model's own outputs, not an "
                      "independent AI-authored analysis — configure GEMINI_API_KEY for full narrative synthesis.")

    return "\n".join([
        sec(1, "VALUATION & FAIR VALUE", val_body),
        sec(2, "FUTURE GROWTH & OUTLOOK", growth_body),
        sec(3, "PAST PERFORMANCE & EARNINGS QUALITY", perf_body),
        sec(4, "FINANCIAL HEALTH & BALANCE SHEET", fin_body),
        sec(5, "DIVIDEND & CAPITAL ALLOCATION",
            "Dividend and capital-allocation detail is not modeled quantitatively in this pipeline; "
            "see the Corporate Events panel above for any declared dividends."),
        sec(6, "MANAGEMENT & COMPENSATION",
            "Management-quality and compensation data are outside this model's quantitative scope."),
        sec(7, "OWNERSHIP STRUCTURE & INSIDER SENTIMENT",
            "See the Shareholding panel above for the latest promoter/institutional/public split and mutual-fund ownership."),
        sec(8, "NARRATIVE VERDICT", verdict_body),
    ])


def generate_comprehensive_report(metrics, ticker):
    if not _gemini_key() or genai is None:
        return _algorithmic_fallback_report(metrics, ticker)
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


def _algorithmic_fallback_ipo_narrative(detail: dict, score, verdict, pros, cons, bucket: str = "current") -> str:
    """Deterministic, template-based IPO narrative used when GEMINI_API_KEY is not
    configured. Mirrors the AI narrative's Key Strengths / Key Risks / Should You
    Invest? structure using only numbers already scraped/computed — nothing invented."""
    name = detail.get("name") or "This IPO"
    news_titles = [n.get("title", "") for n in (detail.get("ipo_news") or [])[:4] if n.get("title")]
    vm = detail.get("valuation_matrix") or {}
    fins = detail.get("financials") or []
    latest_fin = fins[-1] if fins else {}

    lines = [f"**{name}** — algorithmic summary (GEMINI_API_KEY not configured; showing the model's own data, not AI-authored analysis)."]
    lines.append(f"Offer: {detail.get('offer_type', 'N/A')}. Issue size: {detail.get('issue_size_str', 'N/A')}. "
                 f"Price band: {detail.get('price_band_str', 'N/A')}.")
    if latest_fin:
        lines.append(f"Latest reported year ({latest_fin.get('year', 'N/A')}): revenue "
                     f"₹{latest_fin.get('revenue_cr', 'N/A')} Cr, PAT ₹{latest_fin.get('pat_cr', 'N/A')} Cr.")
    if detail.get("revenue_cagr") is not None:
        lines.append(f"Revenue CAGR across reported years: {detail['revenue_cagr']}%.")
    if vm.get("pre") or vm.get("post"):
        pre_pe = vm.get("pre", {}).get("pe")
        post_pe = vm.get("post", {}).get("pe")
        lines.append(f"Valuation: pre-IPO P/E {pre_pe if pre_pe is not None else 'N/A'}x, "
                     f"post-IPO (diluted) P/E {post_pe if post_pe is not None else 'N/A'}x.")
    if bucket != "upcoming" and detail.get("gmp_pct") is not None:
        lines.append(f"Grey market premium: {detail['gmp_pct']}%. Subscription: "
                     f"{detail.get('subscription_total', 'N/A')}x overall.")

    lines.append("\n**Key Strengths:** " + ("; ".join(pros[:4]) if pros else "None flagged by the model."))
    lines.append("**Key Risks:** " + ("; ".join(cons[:4]) if cons else "None flagged by the model."))
    if news_titles:
        lines.append("**Recent news:** " + "; ".join(news_titles))

    if bucket == "current":
        lines.append(f"\n**Should You Invest?** Model verdict: **{verdict}** (score {score}/100). "
                     "This is a template-generated readout of the quantitative score, not independent judgment.")
    elif bucket == "closed":
        lines.append("\nThis IPO has closed for bidding — figures above reflect the latest data captured; "
                     "no BUY/ABSTAIN verdict is given for a closed issue.")
    else:
        lines.append("\nThis IPO has not yet opened — GMP, subscription, and a verdict aren't available yet.")
    return "\n".join(lines)


def ipo_ai_narrative(detail: dict, score, verdict, pros, cons, bucket: str = "current") -> str:
    if not _gemini_key() or genai is None:
        return _algorithmic_fallback_ipo_narrative(detail, score, verdict, pros, cons, bucket)
    try:
        client = genai.Client(api_key=_gemini_key())
        if bucket == "current":
            sys = (
                "You are an IPO research synthesis layer producing a Tier-1 institutional-style note. "
                "You are given: issue structure (size, fresh/OFS split, price band, dates), 3-year financials "
                "(revenue, EBITDA, PAT, assets, net worth, borrowings), margin ratios (ROE, ROCE, PAT margin, "
                "debt/equity), a pre-IPO vs. post-IPO valuation matrix (EPS, P/E, market cap — post-IPO figures "
                "are already diluted for the fresh issue), GMP and subscription data, and recent news headlines. "
                "Use the news to give real-world context for the numbers — say plainly whether it supports or "
                "challenges the quantitative picture, don't just restate headlines. "
                "Structure your output in exactly these sections, in this order:\n"
                "**Key Strengths** — 3-5 bullet points, specific to this business and its numbers.\n"
                "**Key Risks** — 3-5 bullet points, specific business risks (not generic market risk).\n"
                "**Should You Invest?** — a direct verdict consistent with the given model score, weighing "
                "valuation (is post-IPO dilution reasonable given growth/margins?), financial trend, and news "
                "context together.\n"
                "Do not invent numbers beyond what is given to you."
            )
        elif bucket == "closed":
            sys = (
                "You are an IPO research synthesis layer. This IPO has closed for bidding. Using the issue "
                "structure, 3-year financials, valuation matrix, GMP, and subscription data given, summarize: "
                "**Key Strengths**, **Key Risks**, and the issue outcome (subscription/GMP and listing result if "
                "available). Weave in any recent news for context. Do NOT give a BUY/ABSTAIN recommendation for "
                "a closed issue — there is nothing left to decide. Do not invent numbers."
            )
        else:
            sys = (
                "You are an IPO research synthesis layer. This IPO has not yet opened. Using the issue structure "
                "and any 3-year financials/valuation-matrix data given, summarize **Key Strengths** and **Key "
                "Risks** from the business fundamentals alone. Do NOT invent GMP, subscription, or a BUY/ABSTAIN "
                "verdict — flag explicitly that these aren't available yet. Weave in any recent news for context."
            )

        news_txt = "; ".join(n.get("title", "") for n in (detail.get("ipo_news") or [])[:4]) or "None found."
        fins = detail.get("financials") or []
        fin_txt = "; ".join(
            f"{f.get('year','?')}: revenue ₹{f.get('revenue_cr','N/A')}Cr, EBITDA ₹{f.get('ebitda_cr','N/A')}Cr, "
            f"PAT ₹{f.get('pat_cr','N/A')}Cr, assets ₹{f.get('assets_cr','N/A')}Cr, net worth ₹{f.get('net_worth_cr','N/A')}Cr, "
            f"borrowings ₹{f.get('borrowings_cr','N/A')}Cr"
            for f in fins[-3:]
        ) or "Not available."
        vm = detail.get("valuation_matrix") or {}
        vm_txt = (f"Pre-IPO: EPS ₹{vm['pre']['eps']}, P/E {vm['pre']['pe']}x, market cap ₹{vm['pre']['market_cap_cr']}Cr. "
                  f"Post-IPO (diluted): EPS ₹{vm['post']['eps']}, P/E {vm['post']['pe']}x, market cap ₹{vm['post']['market_cap_cr']}Cr."
                  if vm.get("pre") and vm.get("post") else "Not available.")
        roe_txt = (f"ROE by year: {detail['roe_by_year']}. ROCE by year: {detail.get('roce_by_year','N/A')}."
                   if detail.get("roe_by_year") else "Not available.")

        prompt = (
            f"IPO: {detail.get('name')} ({detail.get('symbol')}). Bucket: {bucket}.\n"
            f"ISSUE STRUCTURE — Offer: {detail.get('offer_type','N/A')}. Total issue: {detail.get('issue_size_str','N/A')}. "
            f"Fresh issue: {detail.get('fresh_issue_str','N/A')}. OFS: {detail.get('ofs_str','N/A')}. "
            f"Price band: {detail.get('price_band_str','N/A')}. Lot size: {detail.get('lot_size','N/A')}. "
            f"Open/Close/Allotment/Listing: {detail.get('open_date_str','N/A')} / {detail.get('close_date_str','N/A')} / "
            f"{detail.get('allotment_date_str','N/A')} / {detail.get('listing_date_str','N/A')}.\n"
            f"3-YEAR FINANCIALS (₹ Cr) — {fin_txt}\n"
            f"MARGIN RATIOS — {roe_txt}\n"
            f"VALUATION MATRIX — {vm_txt}\n"
            f"Revenue CAGR: {detail.get('revenue_cagr','N/A')}%. Profitable in latest year: {detail.get('is_profitable_latest','N/A')}.\n"
            f"GMP%: {detail.get('gmp_pct','N/A')}. Subscription (overall): {detail.get('subscription_total','N/A')}x "
            f"(QIB {detail.get('subscription_qib','N/A')}x / NII {detail.get('subscription_nii','N/A')}x / "
            f"Retail {detail.get('subscription_retail','N/A')}x).\n"
            f"Model score: {score}. Model verdict: {verdict}. "
            f"Model-flagged pros: {'; '.join(pros[:4]) or 'None'}. Model-flagged cons: {'; '.join(cons[:4]) or 'None'}.\n"
            f"About: {(detail.get('about') or '')[:400]}.\n"
            f"Recent news headlines: {news_txt}"
        )
        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite", contents=prompt,
            config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2))
        return resp.text
    except Exception as e:
        return _algorithmic_fallback_ipo_narrative(detail, score, verdict, pros, cons, bucket)

