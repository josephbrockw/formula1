from __future__ import annotations

from django.contrib import admin

from core.models import (
    Circuit,
    CollectionRun,
    Driver,
    Event,
    Lap,
    Season,
    Session,
    SessionCollectionStatus,
    SessionResult,
    Team,
    WeatherSample,
)

admin.site.register(Season)
admin.site.register(Circuit)
admin.site.register(Team)
admin.site.register(Driver)
admin.site.register(Event)
admin.site.register(Session)
admin.site.register(SessionResult)
admin.site.register(Lap)
admin.site.register(WeatherSample)
admin.site.register(CollectionRun)
admin.site.register(SessionCollectionStatus)
