"""Sector classification."""
import re
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
