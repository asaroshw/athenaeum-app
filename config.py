"""Shared constants and theme tokens."""
GOLD = "#EAB308"
BG = "#000000"
CARD_BG = "#0D0D0D"
BORDER = "#1F1F1F"
GREEN = "#3FB950"
RED = "#F85149"
ORANGE = "#F97316"
MUTED = "#8B949E"
BLUE = "#38BDF8"
PURPLE = "#A855F7"

EQUITY_RISK_PREMIUM = 0.055
TERMINAL_GROWTH_PCT = 5.0

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
GROWTH_PCT_PATTERN = None  # set in analysis.sentiment to avoid circular import of re at load

SECTOR_PEERS = {
    # populated in data.peers if needed — kept in models.sector for runtime
}
