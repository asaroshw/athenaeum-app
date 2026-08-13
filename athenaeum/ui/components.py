# --- ANGEL ONE COMPONENT: 52-WEEK RANGE BAR ---
def render_52week_range(current_price, low_52, high_52, currency="₹"):
    current_price = to_float(current_price)
    low_52 = to_float(low_52)
    high_52 = to_float(high_52)
    
    if current_price is None or low_52 is None or high_52 is None or high_52 <= low_52: return
    pct_position = ((current_price - low_52) / (high_52 - low_52)) * 100
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

# ... (Keep the render_scorecard_badges and render_valuation_spectrum functions exactly as they are) ...

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
