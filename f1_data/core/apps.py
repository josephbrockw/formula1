from __future__ import annotations

from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self) -> None:
        import fastf1
        from django.conf import settings

        fastf1.Cache.enable_cache(settings.FASTF1_CACHE_DIR)
