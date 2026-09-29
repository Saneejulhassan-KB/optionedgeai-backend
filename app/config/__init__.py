"""
Config package public exports.

Other modules should prefer:
    from app.config import get_settings
instead of importing the settings module path directly.
"""

from app.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
