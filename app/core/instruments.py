"""
Primary instrument keys used by OptionEdgeAI Home screens.

These match Upstox instrument_key values and Flutter IndexKeys.
"""

# India VIX + three index cards (Phase 5.1 first live universe)
INSTRUMENT_INDIA_VIX: str = "NSE_INDEX|India VIX"
INSTRUMENT_NIFTY: str = "NSE_INDEX|Nifty 50"
INSTRUMENT_BANK_NIFTY: str = "NSE_INDEX|Nifty Bank"
INSTRUMENT_SENSEX: str = "BSE_INDEX|SENSEX"

PRIMARY_QUOTE_KEYS: tuple[str, ...] = (
    INSTRUMENT_INDIA_VIX,
    INSTRUMENT_NIFTY,
    INSTRUMENT_BANK_NIFTY,
    INSTRUMENT_SENSEX,
)

# Safety limit for one quote request (Upstox allows up to 500)
MAX_QUOTE_KEYS: int = 50
