"""Streamlit UI components and Plotly charts."""
from __future__ import annotations
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta
from athenaeum.config import (
    GOLD, BG, CARD_BG, BORDER, GREEN, RED, ORANGE, MUTED, BLUE, PURPLE,
)
from athenaeum.utils.helpers import html_escape_fn, to_float, is_valid_metric, rating_color

# alias used in templates
html_escape = html_escape_fn

def render_checks(checks):
    if not checks:
        return "<div class='swf-check-na'>&#8213; Not enough data to run this checklist.</div>"
    html = ""
    for label, status, desc in checks:
        icon, cls = ("&#9989;", "swf-check-pass") if status else ("&#10060;", "swf-check-fail")
        html += f'<div style="padding:5px 0;"><span class="{cls}">{icon} <b>{html_escape(str(label))}</b></span><div class="swf-sub">{html_escape(str(desc))}</div></div>'
    return html

def price_history_chart(hist_df, currency):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Close'], mode='lines', line=dict(color=BLUE, width=1.5), fill='tozeroy', fillcolor='rgba(56,189,248,0.08)'))
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=20, l=10, r=10), xaxis=dict(showgrid=False), yaxis=dict(showgrid=False, title=currency))
    return fig

def fair_value_bar(price, fv, currency):
    fig = go.Figure()
    fig.add_trace(go.Bar(x=['Current Price'], y=[price], marker_color=BLUE, text=[f"{currency}{price:,.2f}"], textposition='auto'))
    fig.add_trace(go.Bar(x=['Modeled Fair Value'], y=[fv], marker_color=GREEN, text=[f"{currency}{fv:,.2f}"], textposition='auto'))
    diff_pct = round(((price - fv) / fv) * 100, 1) if fv else None
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=320, margin=dict(t=20, b=20, l=10, r=10), showlegend=False, yaxis=dict(showgrid=False))
    return fig, diff_pct

def projection_path_chart(hist_df, target_price):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Close'], mode='lines', line=dict(color=BLUE, width=2), name='Historical Price'))
    last_date, last_price = hist_df['Date'].iloc[-1], hist_df['Close'].iloc[-1]
    fig.add_trace(go.Scatter(x=[last_date, last_date + timedelta(days=365)], y=[last_price, target_price], mode='lines', line=dict(color=GOLD, width=2, dash='dot'), name='Illustrative Target'))
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=300, margin=dict(t=20, b=20, l=10, r=10), legend=dict(orientation="h", y=-0.2))
    return fig

def analysis_radar_chart(m, pred):
    categories = ['Fundamentals', 'Valuation', 'Momentum']
    values = [m.get('fundamental_score', 50), pred.get('intrinsic_score', 50), pred.get('technical_score', 50)]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=values + [values[0]], theta=categories + [categories[0]], fill='toself', fillcolor='rgba(234,179,8,0.35)', line=dict(color=GOLD, width=2)))
    fig.update_layout(polar=dict(bgcolor=BG, radialaxis=dict(visible=False, range=[0, 100]), angularaxis=dict(color=MUTED, gridcolor=BORDER)), showlegend=False, paper_bgcolor=BG, margin=dict(t=10, b=10, l=30, r=30), height=230)
    return fig

def ownership_donut(shareholding):
    colors = [BLUE, PURPLE, GOLD] if "Data Unavailable" not in shareholding else [MUTED]
    fig = go.Figure(data=[go.Pie(labels=list(shareholding.keys()), values=list(shareholding.values()), hole=.5, marker_colors=colors)])
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=240, margin=dict(t=10, b=10, l=10, r=10), legend=dict(orientation="h", y=-0.1))
    return fig

# --- ANGEL ONE COMPONENT: 52-WEEK RANGE BAR ---
def render_52week_range(current_price, low_52, high_52, currency="₹"):
    current_price = to_float(current_price)
    low_52 = to_float(low_52)
    high_52 = to_float(high_52)
    
    if current_price is None or low_52 is None or high_52 is None or high_52 <= low_52: return
    pct_position = max(0.0, min(100.0, ((current_price - low_52) / (high_52 - low_52)) * 100))
    fig = go.Figure()
    fig.add_trace(go.Bar(x=[100], y=["Range"], orientation="h", marker=dict(color="#1F1F1F"), hoverinfo="none"))
    fig.add_trace(go.Scatter(x=[pct_position], y=["Range"], mode="markers", marker=dict(color="#38BDF8", size=16, symbol="diamond"), name="Current Price"))
    fig.update_layout(height=50, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 100]), yaxis=dict(showgrid=False, zeroline=False, showticklabels=False), showlegend=False)
    st.markdown(f"<div style='color:{MUTED}; font-size:0.85em; text-align:center;'><b>52W Low:</b> {currency}{low_52:,.2f} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Current:</b> <span style='color:#E6E6E6;'>{currency}{current_price:,.2f}</span> &nbsp;&nbsp;|&nbsp;&nbsp; <b>52W High:</b> {currency}{high_52:,.2f}</div>", unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# --- ANGEL ONE COMPONENT: SMART SUMMARY CARDS ---
def render_price_summary_cards(df, current_price, low_52, high_52):
    if df is None or df.empty: return
    
    current_price = to_float(current_price)
    low_52 = to_float(low_52)
    high_52 = to_float(high_52)
    
    if current_price is None: return

    sma_20 = df["Close"].rolling(20).mean().iloc[-1]
    sma_50 = df["Close"].rolling(50).mean().iloc[-1]
    sma_200 = df["Close"].rolling(200).mean().iloc[-1]

    c1, c2, c3 = st.columns(3)
    if low_52 is not None and high_52 is not None:
        dist_from_low = ((current_price - low_52) / low_52) * 100
        with c1:
            if dist_from_low <= 5:
                st.info(f"📍 **Near 52W Low:** Just {dist_from_low:.1f}% above 52-week low.")
            else:
                st.info(f"📊 **52W Position:** {((current_price - low_52) / (high_52 - low_52)) * 100:.1f}% of 52-week range.")
    with c2:
        if pd.notna(sma_50) and pd.notna(sma_200):
            if current_price > sma_50 and current_price > sma_200:
                st.success("📈 **Bullish Trend:** Trading above 50-day & 200-day SMAs.")
            elif current_price < sma_50 and current_price < sma_200:
                st.error("📉 **Bearish Trend:** Trading below 50-day & 200-day SMAs.")
            else:
                st.warning("⚖️ **Mixed Trend:** Trading between 50-day & 200-day SMAs.")
    with c3:
        if "Volume" in df.columns and len(df) >= 6:
            vol_today = df["Volume"].iloc[-1]
            vol_avg_5d = df["Volume"].iloc[-6:-1].mean()
            vol_ratio = (vol_today / vol_avg_5d) if vol_avg_5d > 0 else 1.0
            if vol_ratio > 1.5:
                st.success(f"🔥 **High Volume:** Today's volume is {vol_ratio:.1f}x higher than 5-day avg.")
            else:
                st.info(f"💧 **Normal Volume:** Trading volume is steady ({vol_ratio:.1f}x 5-day avg).")

# --- PROFESSIONAL TRADER UPGRADE: TECHNICAL REGIME BADGES ---
def render_technical_regime_badges(signals):
    """Traffic-light pills for SMA Trend / Volume Ratio / Nifty-Relative Momentum.
    `signals` is the dict returned by technical.compute_technical_regime_badges()."""
    if not signals:
        return
    tone_color = {"positive": GREEN, "negative": RED, "neutral": GOLD, "na": MUTED}

    def _pill(title, sig):
        sig = sig or {}
        clr = tone_color.get(sig.get("tone"), MUTED)
        return (
            f"<div style='background:{CARD_BG}; border:1px solid {BORDER}; border-left:4px solid {clr}; "
            f"border-radius:8px; padding:10px 14px; flex:1; min-width:150px;'>"
            f"<div style='color:{MUTED}; font-size:0.72em; font-weight:700; text-transform:uppercase; letter-spacing:0.4px;'>"
            f"{html_escape(str(title))}</div>"
            f"<div style='display:flex; align-items:center; gap:6px; margin-top:4px;'>"
            f"<span style='width:8px; height:8px; border-radius:50%; background:{clr}; display:inline-block;'></span>"
            f"<span style='color:#FFFFFF; font-weight:800; font-size:1.05em;'>{html_escape(str(sig.get('label', 'N/A')))}</span>"
            f"</div>"
            f"<div style='color:{MUTED}; font-size:0.75em; margin-top:2px;'>{html_escape(str(sig.get('detail', '')))}</div>"
            f"</div>"
        )

    html = "<div style='display:flex; gap:12px; margin:2px 0 18px 0; flex-wrap:wrap;'>"
    html += _pill("SMA Trend", signals.get("sma_trend"))
    html += _pill("Volume Ratio (5D/20D)", signals.get("volume_ratio"))
    html += _pill("Nifty-Relative Momentum", signals.get("nifty_momentum"))
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

# --- ANGEL ONE COMPONENT: SCORECARD BADGES ---
def render_scorecard_badges(q_score, v_score, f_score):
    def get_badge(score, is_val=False):
        if score is None: return "N/A", "N/A", MUTED
        rating = max(1, min(5, round((score / 100) * 5)))
        if is_val:
            lbl = "VERY CHEAP" if rating==5 else "ATTRACTIVE" if rating==4 else "FAIR" if rating==3 else "EXPENSIVE" if rating==2 else "VERY EXPENSIVE"
        else:
            lbl = "EXCELLENT" if rating==5 else "GOOD" if rating==4 else "AVERAGE" if rating==3 else "WEAK" if rating==2 else "POOR"
        clr = GREEN if rating >= 4 else GOLD if rating == 3 else RED
        return rating, lbl, clr

    q_rat, q_lbl, q_clr = get_badge(q_score)
    v_rat, v_lbl, v_clr = get_badge(v_score, is_val=True)
    f_rat, f_lbl, f_clr = get_badge(f_score)

    st.markdown(f"""
    <div style="display: flex; gap: 15px; margin-bottom: 20px;">
        <div style="background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; padding:12px 18px; flex:1;">
            <div style="color:{MUTED}; font-size:0.8em; font-weight:600; text-transform:uppercase;">Quality</div>
            <div style="margin-top:5px; display:flex; align-items:center; gap:10px;">
                <span style="color:{q_clr}; border:1px solid {q_clr}; padding:2px 8px; border-radius:4px; font-weight:700; font-size:0.85em;">{q_lbl}</span>
                <span style="color:#E6E6E6; font-weight:700; font-size:1em;">{q_rat}/5</span>
            </div>
        </div>
        <div style="background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; padding:12px 18px; flex:1;">
            <div style="color:{MUTED}; font-size:0.8em; font-weight:600; text-transform:uppercase;">Valuation</div>
            <div style="margin-top:5px; display:flex; align-items:center; gap:10px;">
                <span style="color:{v_clr}; border:1px solid {v_clr}; padding:2px 8px; border-radius:4px; font-weight:700; font-size:0.85em;">{v_lbl}</span>
                <span style="color:#E6E6E6; font-weight:700; font-size:1em;">{v_rat}/5</span>
            </div>
        </div>
        <div style="background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; padding:12px 18px; flex:1;">
            <div style="color:{MUTED}; font-size:0.8em; font-weight:600; text-transform:uppercase;">Financial Health</div>
            <div style="margin-top:5px; display:flex; align-items:center; gap:10px;">
                <span style="color:{f_clr}; border:1px solid {f_clr}; padding:2px 8px; border-radius:4px; font-weight:700; font-size:0.85em;">{f_lbl}</span>
                <span style="color:#E6E6E6; font-weight:700; font-size:1em;">{f_rat}/5</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# --- ANGEL ONE COMPONENT: VALUATION SPECTRUM ---
def render_valuation_spectrum(current_price, fair_value, currency="₹"):
    if not fair_value or not current_price: return
    attractive_limit = round(fair_value * 0.85, 2)
    expensive_limit = round(fair_value * 1.50, 2)
    high_limit = round(fair_value * 2.50, 2)

    if current_price <= attractive_limit: pos = 15
    elif current_price >= high_limit: pos = 90
    else: pos = 15 + ((current_price - attractive_limit) / (high_limit - attractive_limit)) * 75

    fig = go.Figure()
    fig.add_trace(go.Bar(x=[100], y=["Valuation"], orientation="h", marker=dict(color=[pos], colorscale=[[0.0, GREEN], [0.4, GOLD], [1.0, RED]], showscale=False), hoverinfo="none"))
    fig.add_trace(go.Scatter(x=[pos], y=["Valuation"], mode="markers", marker=dict(color="#FFFFFF", size=18, symbol="triangle-up"), name="Current Price"))
    fig.update_layout(height=80, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 100]), yaxis=dict(showgrid=False, zeroline=False, showticklabels=False), showlegend=False)

    st.markdown(f"##### Valuation Zone — Current Price: **{currency}{current_price:,.2f}**")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1: st.markdown(f"<div style='color:{GREEN}; font-size:0.85em;'><b>Attractive:</b> Below {currency}{attractive_limit:,.2f}</div>", unsafe_allow_html=True)
    with col2: st.markdown(f"<div style='color:{GOLD}; font-size:0.85em; text-align:center;'><b>Fair/Exp:</b> {currency}{attractive_limit:,.2f} – {currency}{expensive_limit:,.2f}</div>", unsafe_allow_html=True)
    with col3: st.markdown(f"<div style='color:{RED}; font-size:0.85em; text-align:right;'><b>High:</b> Above {currency}{high_limit:,.2f}</div>", unsafe_allow_html=True)

# --- PROFESSIONAL TRADER UPGRADE: BEAR / BASE / BULL SCENARIO MATRIX ---
def render_scenario_matrix(bear_value, base_value, bull_value, current_price, currency="₹"):
    """Clean Bear / Base / Bull valuation matrix, each cell showing the modeled
    fair value and its implied upside/downside vs. the current price."""
    if base_value is None:
        return
    current_price = to_float(current_price)
    cols = [("BEAR", to_float(bear_value), RED), ("BASE", to_float(base_value), GOLD), ("BULL", to_float(bull_value), GREEN)]

    cells = ""
    for i, (label, val, clr) in enumerate(cols):
        border = f"border-left:1px solid {BORDER};" if i > 0 else ""
        if val is None:
            cells += (
                f"<div style='flex:1; text-align:center; padding:12px 8px; {border}'>"
                f"<div style='color:{MUTED}; font-size:0.75em; font-weight:700; letter-spacing:0.5px;'>{label}</div>"
                f"<div style='color:{MUTED}; font-size:1.1em; margin-top:6px;'>N/A</div></div>"
            )
            continue
        upside = ((val - current_price) / current_price * 100) if current_price else None
        upside_str = f"{'+' if upside is not None and upside >= 0 else ''}{upside:.1f}%" if upside is not None else ""
        cells += (
            f"<div style='flex:1; text-align:center; padding:12px 8px; {border}'>"
            f"<div style='color:{clr}; font-size:0.75em; font-weight:700; letter-spacing:0.5px;'>{label}</div>"
            f"<div style='color:#FFFFFF; font-size:1.25em; font-weight:800; margin-top:4px;'>{currency}{val:,.2f}</div>"
            f"<div style='color:{clr}; font-size:0.8em; margin-top:2px;'>{upside_str}</div>"
            f"</div>"
        )
    st.markdown(
        f"<div class='swf-card' style='padding:4px 0 10px 0;'>"
        f"<div style='color:{MUTED}; font-size:0.78em; font-weight:700; text-transform:uppercase; "
        f"letter-spacing:0.4px; padding:10px 16px 4px 16px;'>Scenario Matrix — Bear / Base / Bull</div>"
        f"<div style='display:flex;'>{cells}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

# --- ANGEL ONE COMPONENT: ANALYST CONSENSUS ---
def render_analyst_consensus(target, current, rec, currency="₹"):
    target = to_float(target)
    current = to_float(current)
    if not target or not current: return
    upside = ((target - current) / current) * 100
    color = GREEN if upside > 0 else RED
    st.markdown(f"""
    <div style='background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; padding:15px; margin-top:15px;'>
        <div style='color:{MUTED}; font-size:0.85em; font-weight:600; text-transform:uppercase;'>Analyst Consensus Target</div>
        <div style='display:flex; justify-content:space-between; align-items:flex-end; margin-top:8px;'>
            <div style='font-size:1.8em; font-weight:800;'>{currency}{target:,.2f}</div>
            <div style='color:{color}; font-weight:700; font-size:1.1em;'>{'+' if upside>0 else ''}{upside:.2f}% Expected</div>
        </div>
        <div style='color:{MUTED}; font-size:0.8em; margin-top:4px;'>Recommendation Mean: {rec or 'N/A'} (1=Strong Buy, 5=Sell)</div>
    </div>
    """, unsafe_allow_html=True)

# --- ANGEL ONE COMPONENT: HIGHLIGHTS CARD ---
def extract_highlights(metrics, cf_df):
    working, not_working = [], []
    if cf_df is not None and not cf_df.empty and 'Particulars' in cf_df.columns:
        ocf_rows = cf_df[cf_df['Particulars'] == 'Operating Cash Flow']
        if not ocf_rows.empty:
            ocf_val = ocf_rows['Amount (₹ Cr)'].iloc[0]
            if isinstance(ocf_val, (int, float)) and ocf_val > 0:
                working.append(f"Operating Cash Flow — Positive at ₹{round(ocf_val, 2):,.2f} Cr")
    
    ic = metrics.get('interest_coverage')
    if is_valid_metric(ic):
        if float(ic) > 10: working.append(f"Operating Profit to Interest — Strong coverage at {float(ic):.2f}x")
        elif float(ic) < 2.5: not_working.append(f"Interest Coverage — Low buffer at {float(ic):.2f}x EBIT")
        
    dte = metrics.get('debt_to_equity')
    if is_valid_metric(dte):
        if float(dte) < 0.2: working.append(f"Balance Sheet Strength — Virtually debt-free (D/E: {float(dte):.2f})")
        elif float(dte) > 1.5: not_working.append(f"Leverage Risk — High Debt-to-Equity at {float(dte):.2f}x")
        
    yoy = to_float(metrics.get('pat_yoy'))
    if yoy and yoy > 20: working.append(f"Strong Earnings Growth — PAT up {yoy:.2f}% YoY")
    elif yoy and yoy < 0: not_working.append(f"Earnings Contraction — PAT down {yoy:.2f}% YoY")
    
    return working, not_working

def render_highlights_card(working, not_working):
    st.markdown("### Key Drivers & Operational Highlights")
    col_pos, col_neg = st.columns(2)
    with col_pos:
        st.markdown(f"##### 🟢 What's Working Well?")
        if working:
            for w in working:
                st.markdown(
                    f"<div style='background:rgba(63,185,80,0.1); border-left:3px solid {GREEN}; "
                    f"padding:8px 12px; margin-bottom:8px; border-radius:4px; font-size:0.9em; color:#E6E6E6;'>"
                    f"• {html_escape(str(w))}</div>",
                    unsafe_allow_html=True)
        else:
            st.caption("No significant positive extremes detected.")
    with col_neg:
        st.markdown(f"##### 🔴 What's Not Working Well?")
        if not_working:
            for nw in not_working:
                st.markdown(
                    f"<div style='background:rgba(248,81,73,0.1); border-left:3px solid {RED}; "
                    f"padding:8px 12px; margin-bottom:8px; border-radius:4px; font-size:0.9em; color:#E6E6E6;'>"
                    f"• {html_escape(str(nw))}</div>",
                    unsafe_allow_html=True)
        else:
            st.caption("No major balance sheet red flags detected.")

# --- ANGEL ONE COMPONENT: CORPORATE EVENTS & MF ---
def render_corporate_events_and_mfs(cal_df, mf_df):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 📅 Corporate Events")
        if cal_df is not None and not cal_df.empty:
            mask = cal_df['Event'].astype(str).str.contains('High|Low|Average|Revenue', case=False, na=False)
            cal_df_clean = cal_df[~mask]
            st.dataframe(cal_df_clean, use_container_width=True, hide_index=True)
        else: st.caption("No upcoming corporate events found.")
    with c2:
        st.markdown("##### 🏦 Top Mutual Funds Invested")
        if mf_df is not None and not mf_df.empty:
            try:
                df_clean = mf_df[["Holder", "Shares", "% Out"]].rename(columns={"Holder": "Mutual Fund Scheme", "Shares": "Shares Held", "% Out": "% Stake"})
                df_clean["% Stake"] = df_clean["% Stake"].apply(lambda x: f"{x * 100:.2f}%" if pd.notna(x) else "N/A")
                st.dataframe(df_clean, use_container_width=True, hide_index=True)
            except Exception: st.dataframe(mf_df, use_container_width=True)
        else: st.caption("No Mutual Fund scheme data available.")

def custom_metric(label, value):
    st.markdown(
        f'<div style="background-color: {CARD_BG}; border: 1px solid {BORDER}; padding: 12px 15px; '
        f'border-radius: 8px; margin-bottom: 12px;">'
        f'<div style="font-size: 11px; color: {MUTED}; text-transform: uppercase; font-weight: 600; margin-bottom: 4px;">'
        f'{html_escape(str(label))}</div>'
        f'<div style="font-size: 20px; font-weight: 700; color: #FFFFFF;">{html_escape(str(value))}</div></div>',
        unsafe_allow_html=True)

def card(title, body_html):
    st.markdown(
        f'<div class="swf-card"><div class="swf-h">{html_escape(str(title))}</div>{body_html}</div>',
        unsafe_allow_html=True)


# ============================================================
# PHASE 2 — ELITE GRAPHICAL UI UPGRADES (additive Plotly charts)
# ============================================================

def render_valuation_bands_chart(ext_hist, current_price, pe_ratio, pb_ratio, currency="₹"):
    """Historical Valuation Bands: price line over the extended lookback window with
    a shaded ±1σ/±2σ band. The band is derived from the stock's own trailing P/E (or
    P/B, if EPS is unusable) held constant across history and applied to price — the
    standard simplification when a full historical-EPS time series isn't available.
    Labeled explicitly in the caption below the chart."""
    if ext_hist is None or ext_hist.empty or 'Close' not in ext_hist.columns:
        st.caption("Extended price history unavailable for this name.")
        return
    closes = ext_hist['Close'].dropna()
    if len(closes) < 30:
        st.caption("Not enough price history for a valuation band.")
        return
    dates = ext_hist['Date'] if 'Date' in ext_hist.columns else ext_hist.index

    basis, basis_label = None, None
    pe_f, pb_f, price_f = to_float(pe_ratio), to_float(pb_ratio), to_float(current_price)
    if pe_f and pe_f > 0 and price_f:
        basis, basis_label = price_f / pe_f, "P/E"
    elif pb_f and pb_f > 0 and price_f:
        basis, basis_label = price_f / pb_f, "P/B"

    fig = go.Figure()
    if basis:
        implied_multiple = closes / basis
        mean_m, std_m = float(implied_multiple.mean()), float(implied_multiple.std() or 0)
        upper2 = [(mean_m + 2 * std_m) * basis] * len(dates)
        upper1 = [(mean_m + 1 * std_m) * basis] * len(dates)
        lower1 = [max((mean_m - 1 * std_m) * basis, 0)] * len(dates)
        lower2 = [max((mean_m - 2 * std_m) * basis, 0)] * len(dates)
        fig.add_trace(go.Scatter(x=dates, y=upper2, line=dict(width=0), showlegend=False, hoverinfo='skip'))
        fig.add_trace(go.Scatter(x=dates, y=upper1, fill='tonexty', fillcolor='rgba(240,180,40,0.10)',
                                  line=dict(width=1, color=GOLD, dash='dot'), name=f'+1σ to +2σ avg {basis_label}'))
        fig.add_trace(go.Scatter(x=dates, y=lower1, fill=None, line=dict(width=1, color=GOLD, dash='dot'),
                                  name=f'Avg {basis_label} band'))
        fig.add_trace(go.Scatter(x=dates, y=lower2, fill='tonexty', fillcolor='rgba(240,180,40,0.10)',
                                  line=dict(width=1, color=GOLD, dash='dot'), showlegend=False))
    fig.add_trace(go.Scatter(x=dates, y=closes, mode='lines', name='Price', line=dict(color=BLUE, width=1.8)))
    fig.update_layout(template="plotly_dark", height=380, margin=dict(l=10, r=10, t=30, b=10),
                       paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                       legend=dict(orientation='h', y=1.15, font=dict(size=10)),
                       yaxis_title=f"Price ({currency})")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    if basis:
        st.caption(f"Band uses the current trailing {basis_label} applied across the lookback window (±1σ / ±2σ) — "
                    "a standard approximation when a full historical-fundamentals time series isn't available.")
    else:
        st.caption("P/E and P/B both unavailable for this name — showing price only.")


def render_seasonality_heatmap(ext_hist):
    """Seasonality Heatmap: monthly historical returns, Months (rows) vs Years (columns),
    green for positive months and red for negative ones."""
    if ext_hist is None or ext_hist.empty or 'Close' not in ext_hist.columns:
        st.caption("Extended price history unavailable for this name.")
        return
    df = ext_hist.copy()
    if 'Date' not in df.columns:
        df = df.reset_index().rename(columns={df.columns[0]: 'Date'})
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    monthly = df['Close'].resample('ME').last().dropna()
    monthly_ret = (monthly.pct_change() * 100).dropna()
    if len(monthly_ret) < 3:
        st.caption("Not enough monthly history to build a seasonality grid.")
        return
    grid_df = monthly_ret.to_frame('ret')
    grid_df['Year'] = grid_df.index.year
    grid_df['Month'] = grid_df.index.strftime('%b')
    month_order = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    grid = grid_df.pivot(index='Month', columns='Year', values='ret').reindex(month_order)
    z = grid.values
    text = [[f"{v:.1f}%" if pd.notna(v) else "" for v in row] for row in z]
    fig = go.Figure(data=go.Heatmap(
        z=z, x=[str(c) for c in grid.columns], y=list(grid.index),
        colorscale=[[0, RED], [0.5, '#2A2A2E'], [1, GREEN]], zmid=0,
        text=text, texttemplate="%{text}", textfont=dict(size=10),
        hovertemplate="%{y} %{x}: %{z:.2f}%<extra></extra>",
        colorbar=dict(title="Return %", tickfont=dict(size=9)),
    ))
    fig.update_layout(template="plotly_dark", height=380, margin=dict(l=10, r=10, t=30, b=10),
                       paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_peer_scatter_chart(peer_data, current_name, current_roe, current_pe, current_mcap):
    """Interactive Peer Scatter Plot: X=ROE, Y=P/E Ratio, bubble size=Market Cap.
    The analysed stock is highlighted in gold among its sector peers."""
    points = list(peer_data or [])
    self_roe, self_pe, self_mcap = to_float(current_roe), to_float(current_pe), to_float(current_mcap)
    if self_roe is not None and self_pe is not None and self_pe > 0 and self_mcap is not None:
        points = points + [{"ticker": "THIS STOCK", "name": current_name or "This stock",
                             "roe": self_roe, "pe": self_pe, "market_cap": self_mcap, "is_self": True}]
    if len(points) < 2:
        st.caption("Not enough peer data available to plot a comparison.")
        return
    fig = go.Figure()
    for p in points:
        is_self = p.get("is_self", False)
        size = max(18, min(70, (max(p["market_cap"], 1) / 1e9) ** 0.5 * 4))
        fig.add_trace(go.Scatter(
            x=[p["roe"]], y=[p["pe"]], mode="markers+text",
            marker=dict(size=size, color=GOLD if is_self else BLUE,
                        line=dict(width=2 if is_self else 1, color="#FFFFFF" if is_self else BORDER),
                        opacity=0.95 if is_self else 0.65),
            text=[str(p.get("name", p.get("ticker", "")))[:14]], textposition="top center",
            textfont=dict(size=9, color=MUTED), name=str(p.get("name", p.get("ticker", ""))), showlegend=False,
            hovertemplate=f"<b>{html_escape(str(p.get('name','')))}</b><br>ROE: %{{x:.1f}}%<br>"
                          f"P/E: %{{y:.1f}}x<br>Mkt Cap: ₹{p['market_cap']/1e7:,.0f} Cr<extra></extra>",
        ))
    fig.update_layout(template="plotly_dark", height=420, margin=dict(l=10, r=10, t=30, b=10),
                       paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                       xaxis_title="ROE (%)", yaxis_title="P/E Ratio (x)")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
