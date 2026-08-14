"""Athenaeum Financial Intelligence — Streamlit entrypoint."""
from __future__ import annotations
import logging
import re
import base64
import pandas as pd
import streamlit as st

from athenaeum.config import (
    GOLD, BG, CARD_BG, BORDER, GREEN, RED, ORANGE, MUTED, BLUE, PURPLE,
)
from athenaeum.utils.helpers import html_escape_fn as html_escape, rating_color, style_verdict_text, to_float
from athenaeum.data.equity import resolve_name_to_ticker, fetch_stock_data
from athenaeum.data.ipo import (
    fetch_ipo_list_categorized, fetch_ipo_detail, score_ipo,
    _render_ipo_list_rows, _render_ipo_detail_view,
)
from athenaeum.ui.components import (
    custom_metric, card, render_checks, render_scorecard_badges,
    price_history_chart, fair_value_bar, analysis_radar_chart, projection_path_chart,
    render_52week_range, render_price_summary_cards, render_valuation_spectrum, 
    render_analyst_consensus, extract_highlights, render_highlights_card, 
    render_corporate_events_and_mfs, ownership_donut
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
    .swf-section-title {{ font-size: 1.6em; font-weight: 800; color: #FFFFFF; margin-top: 10px; padding-top: 14px; border-top: 2px solid {BORDER}; }}
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
    rc = rating_color(current_rating)
    currency = m.get('currency', '₹')

    val_checks = m.get('valuation_checks') or valuation_checks(m)
    past_checks = m.get('past_checks') or past_performance_checks(m)
    health_checks = m.get('health_checks') or financial_health_checks(m)
    div_checks = dividend_checks(m)

    # ---------------- Header ----------------
    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        turnaround_badge = ' <span class="swf-badge" style="margin-left:6px; color:#F97316;">TURNAROUND</span>' if m.get('is_turnaround') else ''
        price_str = f"{currency}{m['price']}" if m.get('price') is not None else "N/A"
        st.markdown(f'<div class="swf-card"><div style="display:flex; justify-content:space-between; align-items:flex-start;"><div><div style="color:{MUTED}; font-size:0.85em;">Stocks / {m.get("industry","N/A")}</div><div style="font-size:1.4em; font-weight:800;">{html_escape(m.get("name", "N/A"))}</div><div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report</div><span class="swf-badge" style="margin-top:8px; display:inline-block;">Verdict: <span style="color:{rc};">{current_rating}</span></span>{turnaround_badge}</div><div style="text-align:right;"><div style="font-size:1.6em; font-weight:800;">{price_str}</div></div></div></div>', unsafe_allow_html=True)
        
        try:
            render_scorecard_badges(m.get('q_score'), m.get('v_score'), m.get('f_score'))
        except Exception: pass
        
        render_price_summary_cards(m.get('history'), m.get('price'), to_float(m.get('fifty_two_low')), to_float(m.get('fifty_two_high')))
        
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
    
    card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{html_escape(str(m.get('business_summary', 'Business summary not available.')))}</p><div class='swf-sub'>Sector: {m.get('sector', 'N/A')} | Industry: {m.get('industry', 'N/A')}</div>")
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
    
    render_analyst_consensus(m.get('target_mean_price'), m.get('price'), m.get('recommendation_mean'), currency)
    card("Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(0))}</p>")
    st.markdown("---")

    # ---------------- 2. Future Growth ----------------
    st.markdown('<div class="swf-section-title">2. Future Growth &amp; Outlook</div>', unsafe_allow_html=True)
    fg1, fg2, fg3 = st.columns(3)
    
    target_display = f"{currency}{pred.get('target_price')}" if current_rating != "DON'T BUY" else "N/A (Rejected Model)"
    with fg1: custom_metric(f"Modeled Target ({pred.get('model_used','DCF')})", target_display)
    with fg2: custom_metric("Est. Time Horizon", pred.get('time_horizon', 'N/A'))
    with fg3: custom_metric("Growth Assumption Used", f"{pred.get('growth_used','N/A')}%")
    
    if pred.get('base_value') and m.get('history') is not None:
        try:
            st.plotly_chart(projection_path_chart(m['history'], pred['base_value']), use_container_width=True, config={'displayModeBar': False})
        except Exception: pass
        
    card("Future Growth & Outlook Narrative", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(1))}</p>")
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
    card("Past Performance & Earnings Quality", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(2))}</p>")
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
    card("Financial Health & Balance Sheet", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(3))}</p>")
    st.markdown("---")

    # ---------------- 5. Dividend ----------------
    st.markdown('<div class="swf-section-title">5. Dividend</div>', unsafe_allow_html=True)
    card("Dividend Checklist", render_checks(div_checks))
    card("Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(4))}</p>")
    st.markdown("---")

    # ---------------- 6. Management ----------------
    st.markdown('<div class="swf-section-title">6. Management &amp; Leadership</div>', unsafe_allow_html=True)
    if m.get('company_officers'): 
        st.dataframe(pd.DataFrame([{"Name": o.get('name', 'N/A'), "Position": o.get('title', 'N/A')} for o in m['company_officers']]), use_container_width=True, hide_index=True)
    card("Management & Compensation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(5))}</p>")
    st.markdown("---")

    # ---------------- 7. Ownership ----------------
    st.markdown('<div class="swf-section-title">7. Ownership Structure</div>', unsafe_allow_html=True)
    if m.get('shareholding'):
        st.plotly_chart(ownership_donut(m['shareholding']), use_container_width=True, config={'displayModeBar': False})
    render_corporate_events_and_mfs(m.get('calendar'), m.get('mutual_funds'))
    card("Ownership Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(6))}</p>")
    st.markdown("---")

    # ---------------- 8. Verdict ----------------
    st.markdown('<div class="swf-section-title">8. Verdict &amp; Summary</div>', unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:1.15em; margin-bottom:14px;'><b>Composite System Verdict:</b> <span style='color:{rc}; font-weight:bold;'>{current_rating}</span></div>", unsafe_allow_html=True)

    if current_rating in ["BUY", "STRONG BUY"]:
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Recommended Entry:</b> {pred.get('entry_range')}<br><b>Horizon:</b> {pred.get('time_horizon')}<br><b>Target:</b> {currency}{pred.get('target_price')}<br><b>Stop Loss:</b> {currency}{pred.get('stop_loss')}</div>", unsafe_allow_html=True)
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
                <div style="font-size: 0.9em; color: #E6E6E6; margin-bottom: 8px;">
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
                html += f"<li style='margin-bottom: 8px; color: #E6E6E6;'><b>{html_escape(str(label))}</b><br><span style='color: {MUTED}; font-size: 0.85em;'>{html_escape(str(desc))}</span></li>"
            else:
                html += f"<li style='margin-bottom: 8px; color: #E6E6E6;'><b style='color: {RED};'>Failed:</b> {html_escape(str(label))}<br><span style='color: {MUTED}; font-size: 0.85em;'>{html_escape(str(desc))}</span></li>"
        html += "</ul>"
        return html

    pc1, pc2 = st.columns(2)
    with pc1: card("✅ Quantitative Strengths", render_pro_con_list(pros, is_pro=True))
    with pc2: card("⚠️ Quantitative Weaknesses", render_pro_con_list(cons, is_pro=False))

    card("Narrative Summary", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.6em; white-space:pre-wrap;'>{style_verdict_text(narrative_for(7))}</p>")
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
        tab_cur, tab_closed, tab_up = st.tabs([
            f"Current ({len(cats.get('current') or [])})",
            f"Closed ({len(cats.get('closed') or [])})",
            f"Upcoming ({len(cats.get('upcoming') or [])})",
        ])
        with tab_cur: _render_ipo_list_rows(cats.get("current") or [], "current")
        with tab_closed: _render_ipo_list_rows(cats.get("closed") or [], "closed")
        with tab_up: _render_ipo_list_rows(cats.get("upcoming") or [], "upcoming")
