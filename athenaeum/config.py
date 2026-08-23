"""Shared constants and theme tokens."""
# Refined dark palette (Angel One–inspired structural cues — clean cards,
# pill status indicators, a warm brand accent — adapted to a dark
# professional-terminal surface rather than Angel One's own light retail
# theme; the existing app was already dark, and dark suits the "Tier-1
# institutional terminal" positioning better than a wholesale light-theme
# rewrite would). BG and the old CARD_BG used to sit within a few RGB values
# of each other (#000000 vs #0D0D0D) — cards had almost no visual presence
# against the page. The gap between BG and CARD_BG below is the fix; the
# rest is refinement of the same names so every existing call site (equity.py,
# ipo.py, ui/components.py) picks up the new look with no code changes.
GOLD = "#EAB308"
BG = "#0A0B0D"
CARD_BG = "#14161B"
CARD_BG_HOVER = "#1B1E25"
BORDER = "#262A33"
BORDER_STRONG = "#363B47"
GREEN = "#22C55E"
GREEN_SOFT = "rgba(34, 197, 94, 0.13)"
RED = "#EF4444"
RED_SOFT = "rgba(239, 68, 68, 0.13)"
ORANGE = "#F59E0B"
ORANGE_SOFT = "rgba(245, 158, 11, 0.13)"
MUTED = "#8D94A3"
MUTED_SOFT = "rgba(141, 148, 163, 0.13)"
BLUE = "#38BDF8"
BLUE_SOFT = "rgba(56, 189, 248, 0.13)"
PURPLE = "#A855F7"
TEXT = "#F2F4F7"
# Narrative/body-copy text — the AI-generated prose sections use a slightly
# dimmer register than headline numbers/labels, which is a real, deliberate
# reading-comfort choice for long-form text — but it was previously a bare
# hex literal (#c9d1d9) repeated ~15 times across streamlit_app.py and
# ipo.py rather than a named token, alongside #FFFFFF and #E6E6E6 used
# elsewhere for what was semantically the same "primary readable text" —
# three different near-white shades with no distinction in intent. Now one
# token (TEXT) for UI text/numbers, one (TEXT_BODY) for narrative prose.
TEXT_BODY = "#C9D1D9"
# Warm orange-red brand accent for primary actions / active states — the one
# place this design spends its "boldness" (buttons, active tabs, the verdict
# pill's accent dot), used sparingly everywhere else.
ACCENT = "#FF6B35"
ACCENT_SOFT = "rgba(255, 107, 53, 0.14)"

EQUITY_RISK_PREMIUM = 0.055
TERMINAL_GROWTH_PCT = 5.0

# Single source of truth for the Gemini model used across the app (AI narratives +
# LLM-assisted news materiality scoring). Previously hardcoded independently in
# ai/reports.py ("gemini-3.5-flash-lite") and analysis/sentiment.py
# ("gemini-2.0-flash", a model Google has since retired) — the two call sites had
# silently drifted onto different models. Update this one constant when Google
# ships a new generation; both call sites now read from it.
GEMINI_MODEL = "gemini-3.5-flash-lite"

FINANCIAL_SECTOR_KEYWORDS = [
    "financial services", "bank", "nbfc", "insurance", "capital markets",
    "credit services", "diversified financials", "asset management",
    "mortgage finance", "consumer finance", "shadow banking",
]
CAPEX_INTENSIVE_KEYWORDS = [
    "industrial", "engineering", "infrastructure", "construction", "capital goods",
    "electrical equipment", "machinery", "railroad", "defense", "aerospace",
    "building products", "specialty industrial"
]
MATERIALS_KEYWORDS = ["steel", "metals", "mining", "materials", "chemicals", "cement", "iron", "pipes", "tubes"]
CYCLICAL_KEYWORDS = ["auto", "automobile", "tire", "tyre"]

STANDARD_REVENUE_KEYS = ["Total Revenue", "Operating Revenue"]
BANK_REVENUE_KEYS = [
    "Total Revenue", "Total Operating Income", "Interest Income",
    "Total Interest Income", "Operating Revenue",
]
INTEREST_INCOME_KEYS = ["Interest Income", "Total Interest Income"]

CATALYST_KEYWORDS = [
    "acqui", "profit", "surge", "turnaround", "wins", " win ", "order book",
    "expansion", "partnership", "record revenue", "upgrade", "beat estimates",
    "demerger", "stake sale", "contract",
]
RISK_KEYWORDS = [
    "fraud", "resign", "default", "probe", "raid", "downgrade", "scam",
    "investigation", "lawsuit", "bankruptcy", "insolvency", "delisting",
]
ORDER_BOOK_KEYWORDS = [
    "order book", "order win", "wins order", "contract win", "crore order",
    "new order", "secures order", "bagged order", "order inflow",
]
# GROWTH_PCT_PATTERN is defined and compiled in athenaeum.analysis.sentiment (avoids circular import)

# SECTOR_PEERS is defined and populated in athenaeum.data.equity (not here)
# to keep peer ticker lists close to their usage context.
