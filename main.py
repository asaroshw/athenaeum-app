def resolve_name_to_ticker(stock_input: str) -> str:
    """Converts company names or ticker inputs to Yahoo Finance tickers (.NS / .BO)."""
    if not stock_input:
        raise ValueError("No input provided")

    stock_str = str(stock_input).strip()
    
    # Direct numeric BSE script code check (e.g., 500325)
    if stock_str.isdigit():
        return stock_str + '.BO'
    
    # Clean up common text additions
    clean_input = re.sub(r'(?i)\s+(ltd|limited|inc|corp|industries|share|stock)$', '', stock_str).strip()
    
    # Check if user already typed a explicit symbol with suffix
    upper_input = clean_input.upper().replace(" ", "")
    if upper_input.endswith(('.NS', '.BO')):
        return upper_input

    # Try Yahoo Finance search query with custom headers
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(clean_input)}"
        res = requests.get(url, headers=headers, timeout=5)
        
        if res.status_code == 200:
            quotes = res.json().get('quotes', [])
            for q in quotes:
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'):
                    return sym
            if quotes and 'symbol' in quotes[0]:
                return quotes[0]['symbol'].upper()
    except Exception:
        pass

    # Fallback default: Append .NS for Indian Stock Exchange lookup
    return upper_input + '.NS'
