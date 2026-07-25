def resolve_name_to_ticker(stock_input):
    stock_str = str(stock_input).strip()
    if stock_str.isdigit(): 
        return stock_str + '.BO'
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            for q in res.json().get('quotes', []):
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'): 
                    return sym
    except Exception: 
        pass
    upper_input = stock_str.upper().replace(" ", "")
    return upper_input if upper_input.endswith(('.NS', '.BO')) else upper_input + '.NS'

@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty: 
        raise ValueError(f"Could not find '{raw_input}'.")
    info = stock.info

    # Data Cascade Usage Example
    pe_raw = fetch_metric_cascade(info.get("trailingPE", "N/A"), resolved_ticker, "pe_ratio")
    dy_raw = fetch_metric_cascade(info.get("dividendYield", "N/A"), resolved_ticker, "dividend_yield")
    
    if dy_raw != "N/A" and isinstance(dy_raw, (int, float)): 
        dy_raw = round(float(dy_raw) * 100, 2)

    pat_qoq, pat_yoy = "N/A", "N/A"
    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            net_inc = q_fin.loc['Net Income'].dropna()
            if len(net_inc) >= 2 and net_inc.iloc[1] != 0: 
                pat_qoq = round(((net_inc.iloc[0] - net_inc.iloc[1]) / abs(net_inc.iloc[1])) * 100, 2)
            if len(net_inc) >= 5 and net_inc.iloc[4] != 0: 
                pat_yoy = round(((net_inc.iloc[0] - net_inc.iloc[4]) / abs(net_inc.iloc[4])) * 100, 2)
    except Exception: 
        pass

    roe_val = info.get("returnOnEquity")
    roa_val = info.get("returnOnAssets")
    de_val = info.get("debtToEquity")

    metrics = {
        "name": info.get("longName", resolved_ticker),
        "price": info.get("currentPrice", round(hist['Close'].iloc[-1], 2)),
        "pe_ratio": pe_raw,
        "peg_ratio": info.get("pegRatio", "N/A"),
        "roe": f"{round(roe_val * 100, 2)}%" if isinstance(roe_val, (int, float)) else "N/A",
        "roce_roa": f"{round(roa_val * 100, 2)}%" if isinstance(roa_val, (int, float)) else "N/A",
        "dividend_yield": f"{dy_raw}%" if dy_raw != "N/A" else "N/A",
        "pat_qoq": f"{pat_qoq}%" if pat_qoq != "N/A" else "N/A",
        "pat_yoy": f"{pat_yoy}%" if pat_yoy != "N/A" else "N/A",
        "debt_to_equity": round(de_val / 100, 2) if isinstance(de_val, (int, float)) else "N/A",
        "market_cap": info.get("marketCap", "N/A"),
        "industry": info.get("industry", "N/A"),
        "sector": info.get("sector", "N/A"),
        "currency": info.get("currency", "INR"),
        "working_ticker": resolved_ticker,  # <--- RE-ADDED THIS KEY HERE
        "history": hist.reset_index()[["Date", "Close"]]
    }
    
    # Fair Value Generation
    pe_num = to_float(metrics['pe_ratio'])
    growth_num = to_float(pat_yoy)
    if metrics['price'] and pe_num and pe_num > 0:
        fair_pe = min(max(growth_num, 8), 40) if growth_num else 15
        metrics['fair_value'] = round((metrics['price'] / pe_num) * fair_pe, 2)
    else:
        metrics['fair_value'] = None

    return metrics
