"""Athenaeum Financial Intelligence — Streamlit entrypoint."""
from __future__ import annotations
import logging
import re
import base64
import pandas as pd
import streamlit as st

from athenaeum.config import (
    GOLD, BG, CARD_BG, CARD_BG_HOVER, BORDER, BORDER_STRONG, GREEN, GREEN_SOFT,
    RED, RED_SOFT, ORANGE, ORANGE_SOFT, MUTED, MUTED_SOFT, BLUE, PURPLE, TEXT,
    ACCENT, ACCENT_SOFT,
)
from athenaeum.utils.helpers import html_escape_fn as html_escape, style_verdict_text, to_float, compute_risk_reward
from athenaeum.data.equity import resolve_name_to_ticker, fetch_stock_data, fetch_extended_price_history, fetch_peer_comparison_data
from athenaeum.data.snapshot_store import save_snapshot, get_snapshot_history
from athenaeum.data.ipo import (
    fetch_ipo_list_categorized, fetch_ipo_detail, score_ipo,
    _render_ipo_list_rows, _render_ipo_detail_view,
)
from athenaeum.models.technical import compute_technical_regime_badges
from athenaeum.ui.components import (
    custom_metric, card, render_checks, render_scorecard_badges, verdict_pill, status_pill,
    price_history_chart, fair_value_bar, analysis_radar_chart, projection_path_chart,
    render_52week_range, render_price_summary_cards, render_valuation_spectrum, 
    render_analyst_consensus, extract_highlights, render_highlights_card, 
    render_corporate_events_and_mfs, ownership_donut,
    render_technical_regime_badges, render_scenario_matrix,
    render_valuation_bands_chart, render_seasonality_heatmap, render_peer_scatter_chart,
)
from athenaeum.ai.reports import generate_comprehensive_report
from athenaeum.models.fundamentals import valuation_checks, past_performance_checks, financial_health_checks, dividend_checks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("athenaeum")

st.set_page_config(
    page_title="Athenaeum Financial Intelligence",
    page_icon="📊",
    layout="wide",
)

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

    /* ---------- Hide Streamlit chrome — make it read as a native app, not a script ---------- */
    #MainMenu {{ visibility: hidden; height: 0; }}
    header[data-testid="stHeader"] {{ display: none; }}
    div[data-testid="stToolbar"] {{ visibility: hidden; height: 0; position: fixed; }}
    div[data-testid="stDecoration"] {{ display: none; }}
    div[data-testid="stStatusWidget"] {{ visibility: hidden; height: 0; }}
    footer {{ visibility: hidden; height: 0; }}
    #stDecoration {{ display: none; }}
    .block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1200px; }}

    /* ---------- Base ---------- */
    html, body, [class*="st-"], .stApp, div, span, p, table, th, td, label {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }}
    .stApp {{ background-color: {BG}; color: {TEXT}; }}
    /* Prices, percentages, scores — tabular figures so digits line up in
       columns instead of each number claiming a slightly different width. */
    .swf-num, .swf-card, [data-testid="stMetricValue"], .stDataFrame {{
        font-variant-numeric: tabular-nums;
    }}

    /* ---------- Header ---------- */
    .swf-title-container {{ text-align: center; padding: 4px 0 22px 0; border-bottom: 1px solid {BORDER}; margin-bottom: 22px; }}
    .swf-title {{ font-size: 1.7em; font-weight: 800; color: {TEXT}; letter-spacing: -0.01em; }}
    .swf-eyebrow {{ color: {MUTED}; font-size: 0.72em; font-weight: 600; letter-spacing: 0.09em; text-transform: uppercase; }}

    /* ---------- Cards ---------- */
    .swf-card {{
        background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 12px;
        padding: 18px 20px; margin-bottom: 16px; transition: border-color 0.15s ease;
    }}
    .swf-card:hover {{ border-color: {BORDER_STRONG}; }}
    .swf-h {{ color:{BLUE}; font-weight:700; font-size:1.02em; margin-bottom:6px; }}
    .swf-sub {{ color:{MUTED}; font-size:0.85em; margin-left:0px; }}
    .swf-check-pass {{ color: {GREEN}; }}
    .swf-check-fail {{ color: {RED}; }}
    .swf-check-na {{ color: {MUTED}; }}

    /* Section titles: a small colored eyebrow rule instead of a flat top
       border — encodes "new section" the same way throughout, and gives
       the numbered sections (1. Valuation, 2. Future Growth, ...) a
       consistent structural marker since they genuinely are an ordered
       walkthrough of the analysis. */
    .swf-section-title {{
        font-size: 1.35em; font-weight: 800; color: {TEXT}; margin-top: 8px;
        padding-top: 16px; border-top: 2px solid {ACCENT}; letter-spacing: -0.01em;
    }}

    /* ---------- Pill badges ---------- */
    .swf-badge {{
        display: inline-flex; align-items: center; gap: 6px;
        background: {CARD_BG}; border: 1px solid {BORDER}; color: {TEXT};
        padding: 5px 13px; border-radius: 999px; font-weight: 600; font-size: 0.82em;
        letter-spacing: 0.01em;
    }}
    .swf-pill {{
        display: inline-flex; align-items: center; gap: 6px;
        padding: 4px 12px; border-radius: 999px; font-weight: 700; font-size: 0.78em;
        letter-spacing: 0.03em; text-transform: uppercase; white-space: nowrap;
    }}
    .swf-pill-dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }}
    .swf-pill-green  {{ background: {GREEN_SOFT};  color: {GREEN}; }}
    .swf-pill-red    {{ background: {RED_SOFT};    color: {RED}; }}
    .swf-pill-orange {{ background: {ORANGE_SOFT}; color: {ORANGE}; }}
    .swf-pill-blue   {{ background: {BLUE}1F;      color: {BLUE}; }}
    .swf-pill-muted  {{ background: {MUTED_SOFT};  color: {MUTED}; }}
    .swf-pill-accent {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}

    /* ---------- Metric cards (custom_metric) ---------- */
    .swf-metric {{
        background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px;
        padding: 13px 16px; margin-bottom: 12px; transition: border-color 0.15s ease;
    }}
    .swf-metric:hover {{ border-color: {BORDER_STRONG}; }}
    .swf-metric-label {{
        font-size: 0.70em; color: {MUTED}; text-transform: uppercase;
        font-weight: 600; letter-spacing: 0.07em; margin-bottom: 5px;
    }}
    .swf-metric-value {{ font-size: 1.28em; font-weight: 700; color: {TEXT}; font-variant-numeric: tabular-nums; }}

    /* ---------- Streamlit native widgets, restyled to match ---------- */
    .stButton>button[kind="primary"], .stButton>button[kind="primaryFormSubmit"] {{
        background-color: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 9px;
        font-weight: 700; color: #0A0B0D; transition: filter 0.15s ease;
    }}
    .stButton>button[kind="primary"]:hover {{ filter: brightness(1.08); border-color: {ACCENT}; }}
    .stButton>button[kind="secondary"] {{
        background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 9px; color: {TEXT};
    }}
    .stTextInput>div>div>input, .stTextArea textarea {{
        background-color: {CARD_BG} !important; border: 1px solid {BORDER} !important;
        border-radius: 9px !important; color: {TEXT} !important;
    }}
    .stTextInput>div>div>input:focus {{ border-color: {ACCENT} !important; box-shadow: 0 0 0 1px {ACCENT} !important; }}
    div[data-baseweb="tab-list"] {{ border-bottom: 1px solid {BORDER}; gap: 4px; }}
    button[data-baseweb="tab"] {{ color: {MUTED}; font-weight: 600; }}
    button[data-baseweb="tab"][aria-selected="true"] {{ color: {TEXT}; }}
    div[data-baseweb="tab-highlight"] {{ background-color: {ACCENT} !important; height: 2px; }}
    .streamlit-expanderHeader {{
        background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; font-weight: 600;
    }}
    div[data-testid="stMetric"] {{
        background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 12px 16px;
    }}
    hr {{ border-color: {BORDER}; }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# APP STATE & HEADER
# ============================================================
if 'report_data'  not in st.session_state: st.session_state.report_data  = None
if 'app_mode'     not in st.session_state: st.session_state.app_mode     = 'equity'
if 'selected_ipo' not in st.session_state: st.session_state.selected_ipo = None
if 'ipo_detail'   not in st.session_state: st.session_state.ipo_detail   = None

def get_base64_image(image_path):
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode('utf-8')
    except Exception:
        return None

logo_b64 = get_base64_image("Logo.png")

if logo_b64:
    st.markdown(f'''
    <div class="swf-title-container" style="display: flex; align-items: center; justify-content: center; gap: 14px; padding: 10px 0 20px 0;">
        <img src="data:image/png;base64,{logo_b64}" style="height: 48px; filter: invert(1); vertical-align: middle;">
        <div class="swf-title" style="letter-spacing: 1.5px;">ATHENAEUM FINANCIAL INTELLIGENCE</div>
    </div>
    ''', unsafe_allow_html=True)
else:
    st.markdown('<div class="swf-title-container"><div class="swf-title">ATHENAEUM FINANCIAL INTELLIGENCE</div></div>', unsafe_allow_html=True)

m1, m2, m3 = st.columns([1, 1, 3])
with m1:
    if st.button("📈  Equity Analysis", use_container_width=True,
                  type="primary" if st.session_state.app_mode == "equity" else "secondary"):
        st.session_state.app_mode    = "equity"
        st.session_state.report_data = None
        st.rerun()
with m2:
    if st.button("🚀  IPO Analysis", use_container_width=True,
                  type="primary" if st.session_state.app_mode == "ipo" else "secondary"):
        st.session_state.app_mode   = "ipo"
        st.session_state.selected_ipo = None
        st.session_state.ipo_detail   = None
        st.rerun()
st.markdown("---")

# ============================================================
# EQUITY MODE
# ============================================================
stock_input, generate_clicked = "", False
if st.session_state.app_mode == "equity":
    col_input, col_btn = st.columns([4, 1])
    with col_input:
        stock_input = st.text_input("Enter Stock Name or Ticker:", label_visibility="collapsed",
                                     placeholder="Search a company or ticker (e.g. RELIANCE, Tata Motors)...")
    with col_btn:
        generate_clicked = st.button("Analyse", type="primary", use_container_width=True)

if generate_clicked and stock_input.strip() and st.session_state.app_mode == 'equity':
    with st.spinner('Compiling metrics, applying sector normalization, and running the composite models...'):
        try:
            rt = resolve_name_to_ticker(stock_input)
            metrics = fetch_stock_data(rt, stock_input)
            
            if metrics is not None and isinstance(metrics, dict):
                final_ticker = metrics.pop('working_ticker', rt)
            else:
                st.error("Error: Could not retrieve valid data for this stock ticker.")
                st.stop()

            # Point-in-time logging of THIS app's own verdict — see
            # data/snapshot_store.py's module docstring for exactly what this
            # does and does not cover. Best-effort: never blocks the page on
            # a storage failure.
            save_snapshot(final_ticker, metrics, metrics.get("predictive"))

            ai_text = generate_comprehensive_report(metrics, final_ticker)
            raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', ai_text)
            sections_list = [s.strip() for s in re.split(r'\n+(?=\d+\.\s+(?:VALUATION|FUTURE GROWTH|PAST PERFORMANCE|FINANCIAL HEALTH|DIVIDEND|MANAGEMENT|OWNERSHIP STRUCTURE|NARRATIVE VERDICT))', raw_ai_text, flags=re.IGNORECASE) if s.strip()]
            if len(sections_list) > 8: sections_list = sections_list[-8:]

            st.session_state.report_data = {"metrics": metrics, "ai_text": ai_text, "narrative_sections": sections_list, "ticker": final_ticker}
        except Exception as e:
            st.error(f"Error: {e}")

if st.session_state.report_data and st.session_state.app_mode == 'equity':
    data = st.session_state.report_data
    m = data['metrics']
    ticker = data['ticker']
    narrative = data['narrative_sections']
    def narrative_for(idx): return re.sub(r'^(?:\*\*|__)?\d+\.\s+[A-Z&\s]+(?:\*\*|__)?\n+', '', narrative[idx], flags=re.IGNORECASE).strip() if idx < len(narrative) else "Detailed qualitative breakdown unavailable."

    pred = m.get('predictive', {})
    current_rating = pred.get('verdict', 'OBSERVE')
    currency = m.get('currency', '₹')

    val_checks = m.get('valuation_checks') or valuation_checks(m)
    past_checks = m.get('past_checks') or past_performance_checks(m)
    health_checks = m.get('health_checks') or financial_health_checks(m)
    div_checks = dividend_checks(m)

    # ---------------- Header ----------------
    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        turnaround_badge = f' {status_pill("Turnaround", tone="orange")}' if m.get('is_turnaround') else ''
        price_str = f"{currency}{m['price']}" if m.get('price') is not None else "N/A"
        st.markdown(f'<div class="swf-card"><div style="display:flex; justify-content:space-between; align-items:flex-start;"><div><div class="swf-eyebrow">Stocks / {html_escape(m.get("industry","N/A"))}</div><div style="font-size:1.4em; font-weight:800; margin-top:2px;">{html_escape(m.get("name", "N/A"))}</div><div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report</div><div style="margin-top:10px;">{verdict_pill(current_rating)}{turnaround_badge}</div></div><div style="text-align:right;"><div class="swf-num" style="font-size:1.6em; font-weight:800;">{price_str}</div></div></div></div>', unsafe_allow_html=True)

        past_snapshots = get_snapshot_history(ticker, limit=10)
        if len(past_snapshots) > 1:
            with st.expander(f"🕓 Verdict history for {ticker} ({len(past_snapshots)} logged runs)"):
                st.caption("Logged locally by this app each time it analyses this ticker — its own past "
                           "outputs, not a restated/point-in-time record of the underlying financial data. "
                           "Too new to show performance yet; see data/snapshot_store.py for scope.")
                hist_rows = [{"As of (UTC)": s["as_of_utc"], "Verdict": s["verdict"],
                              "Composite": s["composite_score"], "Target": s["target_price"],
                              "Price then": s["current_price"], "Model": s["model_used"]}
                             for s in past_snapshots]
                st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)

        try:
            render_scorecard_badges(m.get('q_score'), m.get('v_score'), m.get('f_score'))
        except Exception: pass
        
        render_price_summary_cards(m.get('history'), m.get('price'), to_float(m.get('fifty_two_low')), to_float(m.get('fifty_two_high')))
        try:
            render_technical_regime_badges(compute_technical_regime_badges(m.get('history'), m.get('price')))
        except Exception: pass
        
        hist_df = m.get('history')
        if hist_df is not None and not hist_df.empty: st.plotly_chart(price_history_chart(hist_df, currency), use_container_width=True, config={'displayModeBar': False})
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Composite Score Radar</div>', unsafe_allow_html=True)
        try:
            st.plotly_chart(analysis_radar_chart(m, pred), use_container_width=True, config={'displayModeBar': False})
        except Exception: pass
        st.markdown('</div>', unsafe_allow_html=True)

    # ---------------- Recent News & Catalysts ----------------
    news_items = m.get('recent_news', [])
    if news_items:
        news_html = "<ul style='padding-left: 20px; margin-bottom: 0;'>"
        for item in news_items[:5]:
            news_html += f"<li style='margin-bottom: 8px;'><a href='{html_escape(item['link'], quote=True)}' target='_blank' style='color:{BLUE}; text-decoration:none;'>{html_escape(item['title'])}</a></li>"
        news_html += "</ul>"
    else:
        news_html = "<div class='swf-sub'>No recent news found for this stock.</div>"
        
    card("Recent News & Market Catalysts", news_html)
    st.markdown("---")

    # ---------------- Company Overview ----------------
    st.markdown('<div class="swf-section-title">Company Overview</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1: 
        custom_metric("Current Price", f"{currency}{m['price']}" if m.get('price') is not None else "N/A")
        custom_metric("P/E Ratio", f"{m['pe_ratio']}x" if m.get('pe_ratio') not in ["N/A", None] else "N/A")
    with c2: 
        custom_metric("P/BV Ratio", f"{m['pb_ratio']}x" if m.get('pb_ratio') not in ["N/A", None] else "N/A")
        custom_metric("ROE", f"{m['roe']}")
    with c3: 
        custom_metric("EV/EBITDA", f"{m['ev_ebitda']}x" if "N/A" not in str(m.get('ev_ebitda', 'N/A')) else str(m.get('ev_ebitda', 'N/A')))
        custom_metric("PAT Growth (YoY)", f"{m['pat_yoy']}")
    with c4: 
        custom_metric("Debt-to-Equity", f"{m['debt_to_equity']}")
        custom_metric("EBITDA Margin", f"{m.get('ebitda_margin', 'N/A')}")
    
    render_52week_range(m.get('price'), to_float(m.get('fifty_two_low')), to_float(m.get('fifty_two_high')), currency)
    
    card("Overview", f"<p style='color:{TEXT_BODY}; font-size:0.9em; line-height:1.5em;'>{html_escape(str(m.get('business_summary', 'Business summary not available.')))}</p><div class='swf-sub'>Sector: {m.get('sector', 'N/A')} | Industry: {m.get('industry', 'N/A')}</div>")
    st.markdown("---")

    # ---------------- 1. Valuation ----------------
    st.markdown('<div class="swf-section-title">1. Valuation</div>', unsafe_allow_html=True)
    card("Valuation Checklist", render_checks(val_checks))
    st.markdown("##### Fair Value Estimate")
    if pred.get('base_value') and m.get('price'):
        try:
            fig, diff_pct = fair_value_bar(m['price'], pred['base_value'], currency)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            render_valuation_spectrum(m['price'], pred['base_value'], currency)
            st.caption(f"Price is approx {abs(diff_pct)}% {'overvalued' if diff_pct > 0 else 'undervalued'} vs the modeled {pred.get('model_used','valuation')} fair value (growth assumption used: {pred.get('growth_used','N/A')}%).")
        except Exception: pass

    try:
        render_scenario_matrix(pred.get('bear_value'), pred.get('base_value'), pred.get('bull_value'), m.get('price'), currency)
    except Exception: pass

    render_analyst_consensus(m.get('target_mean_price'), m.get('price'), m.get('recommendation_mean'), currency)
    card("Valuation & Fair Value", f"<p style='color:{TEXT_BODY}; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(0))}</p>")

    # ---------------- Elite Graphical UI: advanced institutional charts ----------------
    # Wrapped in an expander (closed by default) so the extra fetches these need
    # (5-year price history, sector peer snapshot) only run if the user opens it —
    # the page's initial load stays fast either way.
    with st.expander("📊 Advanced Analytics — Valuation Bands · Seasonality · Peer Comparison", expanded=False):
        adv_tab1, adv_tab2, adv_tab3 = st.tabs(["Historical Valuation Bands", "Seasonality Heatmap", "Peer Comparison"])
        working_ticker = ticker
        with adv_tab1:
            try:
                ext_hist = fetch_extended_price_history(working_ticker)
                render_valuation_bands_chart(ext_hist, m.get('price'), m.get('pe_ratio'), m.get('pb_ratio'), currency)
            except Exception: st.caption("Valuation band chart unavailable.")
        with adv_tab2:
            try:
                ext_hist = fetch_extended_price_history(working_ticker)
                render_seasonality_heatmap(ext_hist)
            except Exception: st.caption("Seasonality heatmap unavailable.")
        with adv_tab3:
            try:
                roe_val = float(str(m.get('roe', '')).replace('%', '').strip())
            except Exception:
                roe_val = None
            try:
                peer_data = fetch_peer_comparison_data(m.get('sector_profile'), exclude_ticker=working_ticker)
                render_peer_scatter_chart(peer_data, m.get('name'), roe_val, m.get('pe_ratio'), m.get('market_cap'))
            except Exception: st.caption("Peer comparison unavailable.")

    st.markdown("---")

    # ---------------- 2. Future Growth ----------------
    st.markdown('<div class="swf-section-title">2. Future Growth &amp; Outlook</div>', unsafe_allow_html=True)
    fg1, fg2, fg3 = st.columns(3)
    
    target_display = f"{currency}{pred.get('target_price')}" if current_rating != "DON'T BUY" else "N/A (Rejected Model)"
    with fg1: custom_metric(f"Modeled Target ({pred.get('model_used','DCF')})", target_display)
    with fg2: custom_metric("Est. Time Horizon", pred.get('time_horizon', 'N/A'))
    with fg3: custom_metric("Growth Assumption Used", f"{pred.get('growth_used','N/A')}%")

    # Cost of capital & capital efficiency — previously computed nowhere in
    # the app (no WACC existed anywhere; Ke itself was used internally but
    # never shown). The FCF DCF above still discounts at Ke, not WACC — see
    # the caption below for why — but WACC and the ROIC-WACC spread are
    # real, independently useful cross-checks in their own right.
    if pred.get('wacc_pct') is not None or pred.get('roic_pct') is not None:
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            custom_metric("WACC", f"{pred['wacc_pct']}%" if pred.get('wacc_pct') is not None else "N/A")
        with cc2:
            custom_metric("ROIC", f"{pred['roic_pct']}%" if pred.get('roic_pct') is not None else "N/A")
        with cc3:
            ep = pred.get('economic_profit_pct')
            ep_display = f"{'+' if ep is not None and ep >= 0 else ''}{ep} pp" if ep is not None else "N/A"
            custom_metric("Economic Profit (ROIC − WACC)", ep_display)
        st.caption(f"Cost of equity (Ke) used in the target-price model above: {pred.get('audit',{}).get('ke','N/A')}%. "
                   "The DCF model discounts Free Cash Flow (Operating Cash Flow − Capex, a levered/equity-side proxy) "
                   "at Ke rather than WACC — that pairing is conceptually consistent for this FCF definition. WACC is "
                   "shown here as an independent capital-cost benchmark and for the ROIC spread, not as this model's "
                   "discount rate.")
    
    if pred.get('base_value') and m.get('history') is not None:
        try:
            st.plotly_chart(projection_path_chart(m['history'], pred['base_value']), use_container_width=True, config={'displayModeBar': False})
        except Exception: pass
        
    card("Future Growth & Outlook Narrative", f"<p style='color:{TEXT_BODY}; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(1))}</p>")
    st.markdown("---")

    # ---------------- 3. Past Performance ----------------
    st.markdown('<div class="swf-section-title">3. Past Performance</div>', unsafe_allow_html=True)
    card("Past Performance Checklist", render_checks(past_checks))
    pp1, pp2 = st.columns(2)
    with pp1: custom_metric("Operating Margin (OPM)", f"{m.get('operating_margin')}%" if m.get('operating_margin') is not None else "N/A")
    with pp2: custom_metric("Multi-Year Revenue CAGR", f"{m.get('revenue_cagr')}%" if m.get('revenue_cagr') is not None else "N/A")
    
    if m.get('pnl_df') is not None and not m['pnl_df'].empty: 
        st.markdown("##### Profit & Loss (Cr)")
        st.dataframe(m['pnl_df'], use_container_width=True, hide_index=True)
    
    working, not_working = extract_highlights(m, m.get('cf_df'))
    render_highlights_card(working, not_working)
    card("Past Performance & Earnings Quality", f"<p style='color:{TEXT_BODY}; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(2))}</p>")
    st.markdown("---")

    # ---------------- 4. Financial Health ----------------
    st.markdown('<div class="swf-section-title">4. Financial Health</div>', unsafe_allow_html=True)
    card("Financial Health Checklist", render_checks(health_checks))
    if m.get('is_financial_sector'):
        st.caption("Note: Capital Adequacy Ratio and NPA (asset quality) figures are not available from this data source and are not shown or estimated. The Net Interest Margin above is an approximation.")
    tab_bs, tab_cf = st.tabs(["Balance Sheet", "Cash Flows"])
    with tab_bs:
        if m.get('bs_df') is not None and not m['bs_df'].empty: st.dataframe(m['bs_df'], use_container_width=True, hide_index=True)
    with tab_cf:
        if m.get('cf_df') is not None and not m['cf_df'].empty: st.dataframe(m['cf_df'], use_container_width=True, hide_index=True)
    card("Financial Health & Balance Sheet", f"<p style='color:{TEXT_BODY}; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(3))}</p>")
    st.markdown("---")

    # ---------------- 5. Dividend ----------------
    st.markdown('<div class="swf-section-title">5. Dividend</div>', unsafe_allow_html=True)
    card("Dividend Checklist", render_checks(div_checks))
    card("Dividend & Capital Allocation", f"<p style='color:{TEXT_BODY}; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(4))}</p>")
    st.markdown("---")

    # ---------------- 6. Management ----------------
    st.markdown('<div class="swf-section-title">6. Management &amp; Leadership</div>', unsafe_allow_html=True)
    if m.get('company_officers'): 
        st.dataframe(pd.DataFrame([{"Name": o.get('name', 'N/A'), "Position": o.get('title', 'N/A')} for o in m['company_officers']]), use_container_width=True, hide_index=True)
    card("Management & Compensation", f"<p style='color:{TEXT_BODY}; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(5))}</p>")
    st.markdown("---")

    # ---------------- 7. Ownership ----------------
    st.markdown('<div class="swf-section-title">7. Ownership Structure</div>', unsafe_allow_html=True)
    if m.get('shareholding'):
        st.plotly_chart(ownership_donut(m['shareholding']), use_container_width=True, config={'displayModeBar': False})
        if "Data Unavailable" not in m['shareholding']:
            st.caption("‘Promoters’ here reflects the data source's insider-holding figure, which approximates but is not identical to official promoter-group disclosure (e.g. does not capture pledging).")
    render_corporate_events_and_mfs(m.get('calendar'), m.get('mutual_funds'))
    card("Ownership Analysis", f"<p style='color:{TEXT_BODY}; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(6))}</p>")
    st.markdown("---")

    # ---------------- 8. Verdict ----------------
    st.markdown('<div class="swf-section-title">8. Verdict &amp; Summary</div>', unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:1.05em; margin-bottom:8px; display:flex; align-items:center; gap:10px;'><b>Composite System Verdict:</b> {verdict_pill(current_rating)}</div>", unsafe_allow_html=True)
    _dc = m.get('data_completeness')
    if _dc is not None:
        _dc_pct = round(float(_dc) * 100 if float(_dc) <= 1 else float(_dc), 0)
        _dc_color = GREEN if _dc_pct >= 75 else (GOLD if _dc_pct >= 45 else RED)
        st.markdown(f"<div style='font-size:0.78em; color:{MUTED}; margin-bottom:14px;'>Fundamental data completeness: <span style='color:{_dc_color}; font-weight:600;'>{_dc_pct:.0f}%</span> — treat the score's precision accordingly.</div>", unsafe_allow_html=True)

    if current_rating in ["BUY", "STRONG BUY"]:
        rr_ratio, _entry_mid = compute_risk_reward(pred.get('entry_low'), pred.get('entry_high'), pred.get('target_price'), pred.get('stop_loss'))
        rr_line = ""
        if rr_ratio is not None:
            rr_color = GREEN if rr_ratio >= 2 else (GOLD if rr_ratio >= 1 else RED)
            rr_line = f"<br><b>Risk / Reward:</b> <span style='color:{rr_color}; font-weight:800;'>1 : {rr_ratio:.2f}</span>"
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Recommended Entry:</b> {pred.get('entry_range')}<br><b>Horizon:</b> {pred.get('time_horizon')}<br><b>Target:</b> {currency}{pred.get('target_price')}<br><b>Stop Loss:</b> {currency}{pred.get('stop_loss')}{rr_line}</div>", unsafe_allow_html=True)
    elif current_rating == "OBSERVE":
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Target ({pred.get('model_used','DCF')}):</b> {currency}{pred.get('target_price')}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px; color:{RED};'><b>Rejected Valuation Baseline:</b> {currency}{pred.get('target_price')} (Do Not Trade)</div>", unsafe_allow_html=True)

    # --- RECOMMENDED SECTOR ALTERNATIVE ---
    if current_rating in ["DON'T BUY", "OBSERVE"] and m.get('best_alternative'):
        alt = m['best_alternative']
        upside_line = (
            f"<div>Upside to Target: <span style='color:{GREEN};font-weight:700;'>+{alt['upside_pct']}%</span></div>"
            if alt.get('upside_pct') is not None else ""
        )
        st.markdown(
            f"""
            <div style="background-color: rgba(56, 189, 248, 0.1); border: 1px solid {BLUE}; border-radius: 8px; padding: 15px; margin-bottom: 20px;">
                <div style="color: {BLUE}; font-weight: 700; font-size: 1.1em; margin-bottom: 5px;">💡 Recommended Sector Alternative</div>
                <div style="font-size: 0.9em; color: {TEXT}; margin-bottom: 8px;">
                    This stock scored poorly. Run through the same analysis engine, this sector peer independently comes back <span style="color:{GREEN};font-weight:700;">STRONG BUY</span> right now:
                </div>
                <div style="display: flex; gap: 20px; font-weight: 600; flex-wrap: wrap;">
                    <div>Stock: <span style="color: {GOLD};">{alt['name']} ({alt['ticker']})</span></div>
                    <div>Price: {currency}{alt['price']}</div>
                    <div>P/E: {f"{alt['pe']}x" if alt['pe'] != "N/A" else "N/A"}</div>
                    <div>P/B: {f"{alt['pb']}x" if alt['pb'] != "N/A" else "N/A"}</div>
                    <div>Score: <span style="color:{GREEN};">{alt.get('composite_score')}/100</span></div>
                    {upside_line}
                </div>
                <div style="font-size: 0.78em; color: #8b949e; margin-top: 8px;">
                    Verdict recomputed live at scan time — search it directly to confirm before acting, as prices and data move.
                </div>
            </div>
            """, unsafe_allow_html=True
        )

    # --- PROS AND CONS CARDS ---
    all_checks = val_checks + past_checks + health_checks + div_checks
    pros = [c for c in all_checks if c[1] is True]
    cons = [c for c in all_checks if c[1] is False]

    def render_pro_con_list(items, is_pro=True):
        if not items: return "<div class='swf-sub'>None identified based on current data.</div>"
        html = "<ul style='padding-left: 20px; margin-bottom: 0; font-size: 0.9em;'>"
        for label, _, desc in items:
            if is_pro:
                html += f"<li style='margin-bottom: 8px; color: {TEXT};'><b>{html_escape(str(label))}</b><br><span style='color: {MUTED}; font-size: 0.85em;'>{html_escape(str(desc))}</span></li>"
            else:
                html += f"<li style='margin-bottom: 8px; color: {TEXT};'><b style='color: {RED};'>Failed:</b> {html_escape(str(label))}<br><span style='color: {MUTED}; font-size: 0.85em;'>{html_escape(str(desc))}</span></li>"
        html += "</ul>"
        return html

    pc1, pc2 = st.columns(2)
    with pc1: card("✅ Quantitative Strengths", render_pro_con_list(pros, is_pro=True))
    with pc2: card("⚠️ Quantitative Weaknesses", render_pro_con_list(cons, is_pro=False))

    card("Narrative Summary", f"<p style='color:{TEXT_BODY}; font-size:0.9em; line-height:1.6em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(7))}</p>")
    st.caption("This report combines sector-normalized checklists, a sector-aware intrinsic valuation model, an ATR/volume-profile risk model, a trend-based time estimate, and a lightweight news/catalyst scan.")

# ============================================================
# IPO MODE (Unchanged)
# ============================================================
if st.session_state.app_mode == "ipo":
    st.info("🚀 **IPO Analysis Mode** — Live Indian IPOs from public aggregators. Current issues can receive a BUY/ABSTAIN screen.")
    if st.session_state.selected_ipo and st.session_state.ipo_detail:
        _render_ipo_detail_view()
    else:
        with st.spinner("Fetching live IPO lists..."):
            cats = fetch_ipo_list_categorized()
        _total_ipos = len(cats.get('current') or []) + len(cats.get('closed') or []) + len(cats.get('upcoming') or [])
        if _total_ipos < 5:
            _health = cats.get('source_health') or {}
            _dead = [k for k, v in _health.items() if v == 0]
            if _dead:
                st.caption(f"⚠️ Fewer IPOs than usual came back — these sources returned nothing this fetch: {', '.join(_dead)}. This can be a temporary block or rate limit; it should recover within 15 minutes, or try again.")
        tab_cur, tab_closed, tab_up = st.tabs([
            f"Current ({len(cats.get('current') or [])})",
            f"Closed ({len(cats.get('closed') or [])})",
            f"Upcoming ({len(cats.get('upcoming') or [])})",
        ])
        with tab_cur: _render_ipo_list_rows(cats.get("current") or [], "current")
        with tab_closed: _render_ipo_list_rows(cats.get("closed") or [], "closed")
        with tab_up: _render_ipo_list_rows(cats.get("upcoming") or [], "upcoming")
