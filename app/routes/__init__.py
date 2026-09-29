"""
API routers package.
"""

from app.routes.auth import router as auth_router
from app.routes.market import router as market_router
from app.routes.market_v1 import router as market_v1_router

__all__ = ["auth_router", "market_router", "market_v1_router"]
