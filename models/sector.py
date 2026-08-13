import re
"""Sector classification."""
from athenaeum.config import (
    FINANCIAL_SECTOR_KEYWORDS, CAPEX_INTENSIVE_KEYWORDS,
    MATERIALS_KEYWORDS, CYCLICAL_KEYWORDS,
)

def is_financial_sector(sector, industry):
    text = f"{sector or ''} {industry or ''}".lower()
    return any(kw in text for kw in FINANCIAL_SECTOR_KEYWORDS)


def classify_sector_profile(sector, industry):
    if is_financial_sector(sector, industry):
        return "financial"
    text = f"{sector or ''} {industry or ''}".lower()
    if any(kw in text for kw in MATERIALS_KEYWORDS):
        return "materials"
    if any(kw in text for kw in CAPEX_INTENSIVE_KEYWORDS):
        return "capex_intensive"
    if any(kw in text for kw in CYCLICAL_KEYWORDS):
        return "cyclical"
    return "standard"

STANDARD_REVENUE_KEYS = ['Total Revenue', 'Operating Revenue']
BANK_REVENUE_KEYS = ['Total Revenue', 'Total Operating Income', 'Interest Income',
                      'Total Interest Income', 'Operating Revenue']
INTEREST_INCOME_KEYS = ['Interest Income', 'Total Interest Income']

# ============================================================
# 4. QUALITATIVE SIGNAL SCANNER
# ============================================================
CATALYST_KEYWORDS = ['acqui', 'profit', 'surge', 'turnaround', 'wins', ' win ', 'order book',
                      'expansion', 'partnership', 'record revenue', 'upgrade', 'beat estimates',
                      'demerger', 'stake sale', 'contract']
RISK_KEYWORDS = ['fraud', 'resign', 'default', 'probe', 'raid', 'downgrade', 'scam',
                  'investigation', 'lawsuit', 'bankruptcy', 'insolvency', 'delisting']
ORDER_BOOK_KEYWORDS = ['order book', 'order win', 'wins order', 'contract win', 'crore order',
                        'export order', 'multi-year contract', 'l1 bidder', 'lowest bidder',
                        'capex expansion', 'capacity expansion', 'new plant', 'guidance']
GROWTH_PCT_PATTERN = re.compile(r'(\d{1,2})\s*%\s*(?:growth|guidance)|(?:growth|guidance).{0,25}?(\d{1,2})\s*%', re.IGNORECASE)

