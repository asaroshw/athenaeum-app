"""Athenaeum Financial Intelligence — Streamlit entrypoint."""
from __future__ import annotations
import logging
import streamlit as st

from athenaeum.config import (
    GOLD, BG, CARD_BG, BORDER, GREEN, RED, ORANGE, MUTED, BLUE, PURPLE,
)
from athenaeum.utils.helpers import html_escape_fn as html_escape, rating_color, style_verdict_text
from athenaeum.data.equity import resolve_name_to_ticker, fetch_stock_data
from athenaeum.data.ipo import (
    fetch_ipo_list_categorized, fetch_ipo_detail, score_ipo,
    _render_ipo_list_rows, _render_ipo_detail_view,
)
from athenaeum.ui.components import (
    custom_metric, card, render_checks, render_scorecard_badges,
    price_history_chart, fair_value_bar, analysis_radar_chart, 
    render_52week_range, render_price_summary_cards, render_valuation_spectrum, 
    render_analyst_consensus, extract_highlights, render_highlights_card, 
    render_corporate_events_and_mfs, ownership_donut
)
from athenaeum.ai.reports import generate_comprehensive_report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("athenaeum")

st.set_page_config(
    page_title="Athenaeum Financial Intelligence",
    page_icon="📊",
    layout="wide",
)

# Session defaults
if "app_mode" not in st.session_state:
    st.session_state.app_mode = "equity"
if "selected_ipo" not in st.session_state:
    st.session_state.selected_ipo = None
if "ipo_detail" not in st.session_state:
    st.session_state.ipo_detail = None
if "ipo_bucket" not in st.session_state:
    st.session_state.ipo_bucket = None

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="st-"], .stApp, div, span, p, table, th, td, label {{ font-family: 'Inter', sans-serif !important; }}
    .stApp {{ background-color: {BG}; color: #E6E6E6; }}
    .swf-title-container {{ text-align: center; padding: 10px 0 20px 0; border-bottom: 1px solid {BORDER}; margin-bottom: 20px; }}
    .swf-title {{ font-size: 1.85em; font-weight: 800; color: #FFFFFF; letter-spacing: 0.5px; }}
    .swf-card {{ background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; }}
    .swf-h {{ color:{BLUE}; font-weight:700; font-size:1.05em; margin-bottom:6px; }}
    .swf-sub {{ color:{MUTED}; font-size:0.85em; margin-left:0px; }}
    .swf-check-pass {{ color: {GREEN}; }}
    .swf-check-fail {{ color: {RED}; }}
    .swf-check-na {{ color: {MUTED}; }}
    .swf-badge {{ background:{CARD_BG}; border:1px solid {BORDER}; padding:5px 12px; border-radius:6px; font-weight:700; font-size:0.85em; }}
    .swf-tag {{ background:#1c2333; border:1px solid {BORDER}; color:{MUTED}; padding:3px 9px; border-radius:5px; font-size:0.78em; margin-right:6px; display:inline-block; }}
    .swf-section-title {{ font-size: 1.6em; font-weight: 800; color: #FFFFFF; margin-top: 10px; padding-top: 14px; border-top: 2px solid {BORDER}; }}

    @media print {{
        section[data-testid="stSidebar"], header[data-testid="stHeader"], #MainMenu, footer,
        div[data-testid="stTextInput"], div[data-testid="stButton"], .stSpinner {{ display: none !important; }}
        .stApp {{ background-color: #ffffff !important; }}
        body, .stApp, p, div, span, td, th, li {{ color: #111111 !important; }}
        .swf-card {{ background-color: #ffffff !important; border: 1px solid #ccc !important; break-inside: avoid; }}
        .swf-check-pass {{ color: #15803d !important; }}
        .swf-check-fail {{ color: #b91c1c !important; }}
        .swf-h, .swf-section-title {{ color: #1e40af !important; }}
        .swf-title {{ color: #111111 !important; }}
    }}
</style>
""", unsafe_allow_html=True)

# MODE SELECTOR + EQUITY UI
# ============================================================
st.markdown('<div class="swf-title-container"><div class="swf-title">ATHENAEUM FINANCIAL INTELLIGENCE</div></div>', unsafe_allow_html=True)

c_eq, c_ipo = st.columns(2)
with c_eq:
    if st.button("📈 Equity Research", use_container_width=True,
                 type="primary" if st.session_state.app_mode == "equity" else "secondary"):
        st.session_state.app_mode = "equity"
        st.rerun()
with c_ipo:
    if st.button("🚀 IPO Analysis", use_container_width=True,
                 type="primary" if st.session_state.app_mode == "ipo" else "secondary"):
        st.session_state.app_mode = "ipo"
        st.rerun()

if st.session_state.app_mode == "equity":
    st.info("Equity mode — listed stocks via FMP/yfinance. Verdicts are research screens, not advice.")
    stock_input = st.text_input("Stock name or ticker (e.g. RELIANCE, TCS.NS)", "")
    run = st.button("Analyse Equity", type="primary")
    if run and stock_input.strip():
        with st.spinner("Fetching & modelling..."):
            resolved = resolve_name_to_ticker(stock_input.strip())
            if not resolved:
                st.error("Could not resolve ticker.")
            else:
                try:
                    metrics = fetch_stock_data(resolved, stock_input.strip())
                except Exception as e:
                    st.error(f"Data fetch failed: {e}")
                    metrics = None
                if metrics:
                    st.session_state["last_equity_metrics"] = metrics
    metrics = st.session_state.get("last_equity_metrics")
    if metrics:
        pred = metrics.get("predictive") or {}
        currency = metrics.get("currency") or "₹"
        name = metrics.get("name") or "—"
        price = metrics.get("price")
        vc = rating_color(pred.get("verdict", "OBSERVE"))
        st.markdown(f"""
        <div class="swf-card">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <div style="color:{MUTED};font-size:0.85em;">Equity Research</div>
              <div style="font-size:1.5em;font-weight:800;">{html_escape(str(name))}</div>
              <div style="color:{MUTED};">{html_escape(str(metrics.get('sector','')))} · {html_escape(str(metrics.get('industry','')))}</div>
            </div>
            <div style="text-align:right;">
              <div style="font-size:2em;font-weight:900;color:{vc};">{html_escape(str(pred.get('verdict','—')))}</div>
              <div style="color:{MUTED};">Composite {pred.get('composite_score','—')}/100</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            custom_metric("Price", f"{currency}{price:,.2f}" if price else "N/A")
        with m2:
            custom_metric("Base Fair Value", f"{currency}{pred.get('base_value'):,.2f}" if pred.get("base_value") else "N/A")
        with m3:
            custom_metric("Bear / Bull",
                (f"{currency}{pred.get('bear_value'):,.0f} / {currency}{pred.get('bull_value'):,.0f}"
                 if pred.get("bear_value") and pred.get("bull_value") else "N/A"))
        with m4:
            custom_metric("Valuation Confidence", pred.get("confidence") or "N/A")

        # Range and Summary Cards
        render_52week_range(price, metrics.get("fifty_two_low"), metrics.get("fifty_two_high"), currency)
        if metrics.get("history") is not None:
            render_price_summary_cards(metrics["history"], price, metrics.get("fifty_two_low"), metrics.get("fifty_two_high"))

        # Valuation Spectrum & Analyst Consensus
        c_spec, c_analyst = st.columns([2, 1])
        with c_spec:
            render_valuation_spectrum(price, pred.get("base_value"), currency)
        with c_analyst:
            render_analyst_consensus(metrics.get("target_mean_price"), price, metrics.get("recommendation_mean"), currency)

        if pred.get("valuation_models"):
            st.markdown("##### Valuation models")
            st.dataframe(
                [{"Model": k, "Value": v} for k, v in pred["valuation_models"].items()],
                use_container_width=True, hide_index=True,
            )

        audit = pred.get("audit") or metrics.get("audit") or {}
        with st.expander("Why this verdict? (audit trail)", expanded=False):
            st.markdown(
                f"- **Growth used:** {audit.get('growth_used', pred.get('growth_used'))}% "
                f"({audit.get('growth_source', pred.get('growth_source'))})\n"
                f"- **Ke / RFR:** {audit.get('ke', pred.get('discount_rate'))}% / source `{audit.get('rfr_source', metrics.get('rfr_source'))}`\n"
                f"- **Near-term / terminal g:** {audit.get('near_term_growth')}% / {audit.get('terminal_growth')}%\n"
                f"- **Model:** {audit.get('model_used', pred.get('model_used'))}\n"
                f"- **Scores:** fundamental={audit.get('fundamental_score', pred.get('fundamental_score'))}, "
                f"intrinsic={audit.get('intrinsic_score', pred.get('intrinsic_score'))}, "
                f"technical={audit.get('technical_score', pred.get('technical_score'))}\n"
                f"- **Valuation CV / confidence:** {audit.get('valuation_cv')} / {audit.get('valuation_confidence', pred.get('confidence'))}\n"
                f"- **Data completeness:** {metrics.get('data_completeness')}%\n"
            )
            # Fix: Single execution loop for audit notes
            notes_list = audit.get("notes") or pred.get("note") or []
            if isinstance(notes_list, str):
                notes_list = [n.strip() for n in notes_list.split(". ") if n.strip()]
            for note in notes_list:
                st.caption(f"• {html_escape(str(note))}")

        warns = metrics.get("warnings") or []
        if warns:
            with st.expander(f"Data-quality warnings ({len(warns)})"):
                for w in warns:
                    st.warning(str(w))

        # Scorecards, Checks, and Radar Chart
        st.markdown("---")
        col_sc, col_radar = st.columns([2, 1])
        with col_sc:
            try:
                render_scorecard_badges(metrics.get("q_score"), metrics.get("v_score"), metrics.get("f_score"))
            except Exception:
                pass
            if metrics.get("valuation_checks"):
                card("Valuation checklist", render_checks(metrics["valuation_checks"]) if callable(render_checks) else "")
        with col_radar:
            try:
                st.plotly_chart(analysis_radar_chart(metrics, pred), use_container_width=True)
            except Exception:
                pass

        # Highlights & Drivers
        working, not_working = extract_highlights(metrics, metrics.get("cf_df"))
        render_highlights_card(working, not_working)

        # Ownership, Events, MFs
        st.markdown("---")
        c_own, c_ev = st.columns([1, 2])
        with c_own:
            st.markdown("##### 🥧 Shareholding Pattern")
            if metrics.get("shareholding"):
                st.plotly_chart(ownership_donut(metrics["shareholding"]), use_container_width=True)
        with c_ev:
            render_corporate_events_and_mfs(metrics.get("calendar"), metrics.get("mutual_funds"))

        # Charts
        st.markdown("---")
        try:
            if metrics.get("history") is not None:
                st.plotly_chart(price_history_chart(metrics["history"], currency), use_container_width=True)
        except Exception as e:
            st.caption(f"Chart unavailable: {e}")
        if pred.get("base_value") and price:
            try:
                st.plotly_chart(fair_value_bar(price, pred["base_value"], currency), use_container_width=True)
            except Exception:
                pass

        with st.spinner("AI synthesis..."):
            try:
                report = generate_comprehensive_report(metrics, metrics.get("working_ticker") or "")
                card("AI Research Synthesis", f"<p style='color:#c9d1d9;line-height:1.6;white-space:pre-wrap;'>{style_verdict_text(report)}</p>")
            except Exception as e:
                st.caption(f"AI report unavailable: {e}")

        st.caption(
            "Not financial advice. Simplified FCF DCF / residual income are cross-checks. "
            "Scores are uncalibrated heuristics. Always read primary filings."
        )


# ============================================================
# IPO MODE — FULL UI (3-tab: Current / Closed / Upcoming)
# ============================================================
if st.session_state.app_mode == "ipo":
    if "ipo_bucket" not in st.session_state:
        st.session_state.ipo_bucket = None

    st.info(
        "🚀 **IPO Analysis Mode** — Live Indian IPOs from public aggregators. "
        "Current issues can receive a BUY/ABSTAIN screen. Closed & Upcoming show facts only. "
        "Not financial advice."
    )

    if st.session_state.selected_ipo and st.session_state.ipo_detail:
        _render_ipo_detail_view()
    else:
        with st.spinner("Fetching live IPO lists..."):
            cats = fetch_ipo_list_categorized()

        tab_cur, tab_closed, tab_up = st.tabs([
            f"Current ({len(cats.get('current') or [])})",
            f"Closed ({len(cats.get('closed') or [])})",
            f"Upcoming ({len(cats.get('upcoming') or [])})",
        ])
        with tab_cur:
            st.caption("Open for subscription — full screen with GMP, subscription, score & BUY/ABSTAIN.")
            _render_ipo_list_rows(cats.get("current") or [], "current")
        with tab_closed:
            st.caption("Closed / listed — shows listing gains or listing date. No algorithmic verdict.")
            _render_ipo_list_rows(cats.get("closed") or [], "closed")
        with tab_up:
            st.caption("Upcoming — price band & issue facts only. No GMP / subscription / verdict.")
            _render_ipo_list_rows(cats.get("upcoming") or [], "upcoming")

        st.caption(
            f"Source refresh: {cats.get('fetched_at', 'n/a')} · "
            f"{cats.get('sources_note', 'Hybrid IPO sources')}. "
            "GMP is unofficial (aggregator / news / AI extraction)."
        )
