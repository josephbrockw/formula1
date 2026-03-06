from __future__ import annotations

from django.urls import path

from predictions import views

urlpatterns = [
    path("", views.season_dashboard, name="season_dashboard"),
    path("race/next/", views.next_race_redirect, name="next_race_redirect"),
    path("race/<int:year>/<int:round_number>/", views.next_race, name="next_race"),
    path("backtest/", views.backtest_explorer, name="backtest_explorer"),
    path("driver/<int:year>/<str:driver_code>/", views.driver_detail, name="driver_detail"),
    path("prices/<int:year>/", views.price_trajectory, name="price_trajectory"),
]
