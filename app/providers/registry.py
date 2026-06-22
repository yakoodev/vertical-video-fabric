from __future__ import annotations

from app.providers.base import Provider
from app.providers.instagram import InstagramProvider
from app.providers.mock import MockProvider
from app.providers.tiktok import TikTokProvider
from app.providers.youtube import YouTubeProvider
from app.settings import settings


def get_provider(platform: str) -> Provider:
    platform = platform.lower()
    if settings.provider_mode == "mock":
        return MockProvider(platform)
    if platform == "youtube":
        return YouTubeProvider()
    if platform == "tiktok":
        return TikTokProvider()
    if platform == "instagram":
        return InstagramProvider()
    raise ValueError(f"unsupported provider: {platform}")

