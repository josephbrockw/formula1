from __future__ import annotations

from django.contrib import admin

from predictions.models import (
    FantasyConstructorPrice,
    FantasyConstructorScore,
    FantasyDriverPrice,
    FantasyDriverScore,
    LineupRecommendation,
    MyLineup,
    RacePrediction,
    ScoringRule,
)

admin.site.register(FantasyDriverPrice)
admin.site.register(FantasyConstructorPrice)
admin.site.register(FantasyDriverScore)
admin.site.register(FantasyConstructorScore)
admin.site.register(ScoringRule)
admin.site.register(RacePrediction)
admin.site.register(LineupRecommendation)
admin.site.register(MyLineup)
