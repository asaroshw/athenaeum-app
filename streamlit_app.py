# ============================================================
# 7. APP STATE
# ============================================================
if 'report_data' not in st.session_state: st.session_state.report_data = None
if 'active_section' not in st.session_state: st.session_state.active_section = "Company Overview"

SECTIONS = ["Company Overview", "1. Valuation", "2. Future Growth", "3. Past Performance",
            "4. Financial Health", "5. Dividend", "6. Management", "7. Ownership", "8. Other Information"]

# ============================================================
# 8. TOP BAR & DATA GENERATION
# ============================================================
st.markdown("<div class='swf-topbar'><div>🐂 <b>ASW STOCK IDEAS</b> &nbsp;|&nbsp; Financial Intelligence Dashboard</div></div>", unsafe_allow_html=True)

col_input, col_btn = st.columns([4, 1])
with col_input: 
    stock_input = st.text_input("Search Ticker:", label_visibility="collapsed", placeholder="Enter a company or ticker (e.g. Reliance, HBL, Tata Motors)...")
with col_btn: 
    generate_clicked = st.button("Generate Terminal Dossier", type="primary", use_container_width=True)

if generate_clicked:
    if stock_input.strip():
        with st.spinner('Compiling data cascade and institutional narrative...'):
            try:
                resolved_ticker = resolve_name_to_ticker(stock_input)
                m = fetch_stock_data(resolved_ticker, stock_input)
                final_ticker = m.pop('working_ticker', resolved_ticker)
                ai_text = generate_comprehensive_report(m, final_ticker)
                
                rating_match = re.search(r'DYNAMIC_RATING:\s*(.*)', ai_text)
                rating = rating_match.group(1).strip().upper() if rating_match else "EVALUATED"
                raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', ai_text)
                sections_list = [s.strip() for s in re.split(r'\n(?=[0-9]\.\s[A-Z&]+)', raw_ai_text) if s.strip()]
                
                st.session_state.report_data = {"metrics": m, "ai_text": ai_text, "narrative_sections": sections_list, "ticker": final_ticker, "rating": rating}
                st.session_state.active_section = "Company Overview"
            except Exception as e:
                st.error(f"Error compiling dossier: {e}")

# ============================================================
# 9. SIDEBAR NAVIGATION (Moved below data generation)
# ============================================================
with st.sidebar:
    if st.session_state.report_data:
        m0 = st.session_state.report_data['metrics']
        t0 = st.session_state.report_data['ticker']
        name0 = g(m0, 'name', t0)
        mcap_val = g(m0, 'market_cap')
        mcap_str = fmt_num(mcap_val, prefix=g(m0, 'currency', '') + ' ') if mcap_val != "N/A" else ""

        st.markdown(f"""
        <div class="swf-company-mini">
            <div style="display:flex; align-items:center; gap:10px;">
                <div class="swf-avatar">{name0[0] if name0 else '?'}</div>
                <div>
                    <div style="font-weight:700;">{name0}</div>
                    <div style="color:{MUTED}; font-size:0.8em;">{t0} Stock Report</div>
                </div>
            </div>
            {"<div style='color:" + MUTED + "; font-size:0.85em; margin-top:6px;'>Market Cap: " + mcap_str + "</div>" if mcap_str else ""}
        </div>
        """, unsafe_allow_html=True)
        st.session_state.active_section = st.radio("Navigate", SECTIONS, index=SECTIONS.index(st.session_state.active_section), label_visibility="collapsed")
    else:
        st.markdown(f'<div style="color:{MUTED}; padding:10px;">Generate a report to unlock section navigation.</div>', unsafe_allow_html=True)

# ============================================================
# 10. MAIN CONTENT RENDERING
# ============================================================
if st.session_state.report_data:
    d = st.session_state.report_data
    m = d['metrics']
    ticker = d['ticker']
    narrative = d.get('narrative_sections', [])
    current_rating = d.get('rating', 'EVALUATED')

    def narrative_for(idx):
        return narrative[idx] if idx < len(narrative) else "Detailed qualitative breakdown unavailable for this section."

    val_checks = valuation_checks(m)
    past_checks = past_performance_checks(m)
    health_checks = financial_health_checks(m)
    div_checks = dividend_checks(m)

    scores = {"Value": score_from_checks(val_checks), "Future": 50, "Past": score_from_checks(past_checks), "Health": score_from_checks(health_checks), "Dividend": score_from_checks(div_checks)}

    currency = g(m, 'currency', '')
    mcap_val = g(m, 'market_cap')
    mcap_display = fmt_num(mcap_val, prefix=currency + ' ') if mcap_val != "N/A" else ""

    rc = GREEN if "BUY" in current_rating else ORANGE if "OBSERVE" in current_rating else RED

    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        sector_str = f"Stocks / {g(m, 'industry')}" if g(m, 'industry') != "N/A" else "Stock Overview"
        st.markdown(f"""
        <div class="swf-card">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                    <div style="color:{MUTED}; font-size:0.85em;">{sector_str}</div>
                    <div style="font-size:1.4em; font-weight:800;">{g(m,'name',ticker)}</div>
                    <div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report {"| Market Cap: " + mcap_display if mcap_display else ""}</div>
                    <span class="swf-badge" style="margin-top:8px; display:inline-block;">Rating: <span style="color:{rc};">{current_rating}</span></span>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:1.6em; font-weight:800;">{currency} {g(m,'price')}</div>
                    <div style="color:{MUTED}; font-size:0.85em;">Current Price</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        hist_df = m.get('history')
        if hist_df is not None and len(hist_df) > 0:
            st.plotly_chart(price_history_chart(hist_df, m.get('fair_value'), currency), use_container_width=True, config={'displayModeBar': False})
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Analysis Summary</div>', unsafe_allow_html=True)
        st.plotly_chart(analysis_radar_chart(scores), use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")

    section = st.session_state.active_section

    # ---------- INDIVIDUAL SECTIONS ----------
    if section == "Company Overview":
        pe_disp = g(m, 'pe_ratio')
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current Price", f"{currency} {g(m,'price')}")
        c1.metric("P/E Ratio", f"{pe_disp}x" if pe_disp != "N/A" else "N/A")
        c2.metric("PEG Ratio", f"{g(m,'peg_ratio')}")
        c2.metric("14-Day RSI", f"{g(m,'rsi')}")
        c3.metric("ROE", f"{g(m,'roe')}")
        c3.metric("Dividend Yield", f"{g(m,'dividend_yield')}")
        c4.metric("PAT Growth (YoY)", f"{g(m,'pat_yoy')}")
        c4.metric("PAT Growth (QoQ)", f"{g(m,'pat_qoq')}")

        summary = m.get('business_summary')
        meta_items = []
        if m.get('employees'): meta_items.append(f"Employees: {m['employees']:,}")
        if g(m, 'sector') != "N/A": meta_items.append(f"Sector: {g(m, 'sector')}")
        if g(m, 'industry') != "N/A": meta_items.append(f"Industry: {g(m, 'industry')}")

        meta_html = f"<div class='swf-sub' style='margin-left:0; margin-top:8px;'>{' | '.join(meta_items)}</div>" if meta_items else ""

        if summary:
            st.markdown("### About the Company")
            card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{summary}</p>{meta_html}")
        elif meta_html:
            st.markdown("### About the Company")
            card("Overview", meta_html)

    elif section == "1. Valuation":
        st.markdown(f"### 1. Valuation — Score {score_from_checks(val_checks)}/100")
        card("Valuation Checklist", render_checks(val_checks))
        if m.get('fair_value') and g(m, 'price', None) is not None:
            fig, diff_pct = fair_value_bar(g(m, 'price'), m['fair_value'], currency)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            status_word = "overvalued" if diff_pct and diff_pct > 0 else "undervalued"
            st.caption(f"Price is approximately {abs(diff_pct)}% {status_word} vs the simplified fair value estimate.")
        card("Narrative — Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(0)}</p>")

    elif section == "2. Future Growth":
        st.markdown("### 2. Future Growth & Outlook")
        if m.get('target_mean_price') and m.get('num_analysts'):
            card("Analyst Coverage", f"<div class='swf-sub' style='margin-left:0;'>Average 12-month analyst target: <b>{currency} {m['target_mean_price']}</b> based on {m['num_analysts']} analyst(s).</div>")
        else: card("Analyst Coverage", "<div class='swf-check-na'>&#8213; Insufficient analyst coverage to forecast growth for this stock.</div>")
        card("Narrative — Future Growth & Outlook", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(1)}</p>")

    elif section == "3. Past Performance":
        st.markdown(f"### 3. Past Performance — Score {score_from_checks(past_checks)}/100")
        card("Past Performance Checklist", render_checks(past_checks))
        yoy_val, qoq_val, roe_val, roa_val = to_float(g(m, 'pat_yoy', None)) or 0, to_float(g(m, 'pat_qoq', None)) or 0, to_float(g(m, 'roe', None)) or 0, to_float(g(m, 'roce_roa', None)) or 0
        p1, p2 = st.columns(2)
        with p1:
            fig = go.Figure(data=[go.Bar(x=['PAT YoY', 'PAT QoQ'], y=[yoy_val, qoq_val], marker_color=[GREEN, BLUE], text=[f"{yoy_val}%", f"{qoq_val}%"], textposition='auto')])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Earnings Momentum")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        with p2:
            fig = go.Figure(data=[go.Bar(x=['ROE', 'ROA/ROCE'], y=[roe_val, roa_val], marker_color=[GOLD, '#a855f7'], text=[f"{roe_val}%", f"{roa_val}%"], textposition='auto')])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Profitability Returns")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        card("Narrative — Past Performance & Earnings Quality", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(2)}</p>")

    elif section == "4. Financial Health":
        st.markdown(f"### 4. Financial Health — Score {score_from_checks(health_checks)}/100")
        card("Financial Health Checklist", render_checks(health_checks))
        tm = balance_sheet_treemap(m)
        if tm: st.plotly_chart(tm, use_container_width=True, config={'displayModeBar': False})
        card("Narrative — Financial Health & Balance Sheet", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(3)}</p>")

    elif section == "5. Dividend":
        st.markdown(f"### 5. Dividend — Score {score_from_checks(div_checks)}/100")
        card("Dividend Checklist", render_checks(div_checks))
        card("Narrative — Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(4)}</p>")

    elif section == "6. Management":
        st.markdown("### 6. Management")
        officers = m.get('company_officers') or []
        if officers:
            rows = [{"Name": o.get('name', 'N/A'), "Title": o.get('title', 'N/A'), "Age": o.get('age', 'N/A'), "Total Pay": fmt_num(o.get('totalPay'), prefix=currency + ' ') if o.get('totalPay') else "N/A"} for o in officers[:8]]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        card("Narrative — Management & Compensation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(5)}</p>")

    elif section == "7. Ownership":
        st.markdown("### 7. Ownership")
        shareholding = m.get('shareholding') or {}
        if shareholding: st.plotly_chart(ownership_bar(shareholding), use_container_width=True, config={'displayModeBar': False})
        card("Narrative — Ownership Structure & Insider Sentiment", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(6)}</p>")

    elif section == "8. Other Information":
        st.markdown("### 8. Other Information")
        oc1, oc2 = st.columns(2)
        with oc1:
            info_lines = []
            if g(m, 'exchange') != "N/A": info_lines.append(f"Exchange: {g(m,'exchange')}")
            info_lines.append(f"Ticker: {ticker}")
            if m.get('employees'): info_lines.append(f"Employees: {m['employees']:,}")
            if m.get('website'): info_lines.append(f"Website: <a href='{m['website']}' style='color:{BLUE};'>{m['website']}</a>")
            card("Key Information", f"<div class='swf-sub' style='margin-left:0;'>{'<br>'.join(info_lines)}</div>")
        with oc2:
            news_val = g(m, 'recent_news', '')
            headlines = news_val.split(" | ") if news_val and news_val != "N/A" else []
            news_html = "".join([f"<div class='swf-sub' style='margin-left:0; padding:4px 0; border-bottom:1px solid {BORDER};'>{h}</div>" for h in headlines]) if headlines else "<div class='swf-check-na'>No recent news available.</div>"
            card("Recent News & Updates", news_html)
            
        styled_verdict = narrative_for(7)
        styled_verdict = re.sub(r'(?i)\bSTRONG BUY\b', f'<span style="color:{GREEN}; font-weight:bold;">STRONG BUY</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)(?<!STRONG )\bBUY\b', f'<span style="color:{GREEN}; font-weight:bold;">BUY</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)\bOBSERVE\b', f'<span style="color:{ORANGE}; font-weight:bold;">OBSERVE</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)\bSELL\b', f'<span style="color:{RED}; font-weight:bold;">SELL</span>', styled_verdict)
        card("Narrative — Summary Verdict & Key Risks", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{styled_verdict}</p>")

    st.markdown("---")

    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, m, d['ai_text'], ticker, current_rating)
    pdf_buffer.seek(0)

    st.download_button(
        label="📥 Download Official PDF Dossier",
        data=pdf_buffer,
        file_name=f"{ticker}_ASW_Stock_Ideas_Dossier.pdf",
        mime="application/pdf",
        type="primary"
    )
