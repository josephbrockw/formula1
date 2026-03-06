from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from core.models import Event, Season
from predictions.models import (
    BacktestRun,
    FantasyConstructorPrice,
    FantasyDriverPrice,
    FantasyDriverScore,
    LineupRecommendation,
    MyLineup,
    RacePrediction,
)


# ---------------------------------------------------------------------------
# Season Dashboard
# ---------------------------------------------------------------------------


def season_dashboard(request: HttpRequest) -> HttpResponse:
    seasons = list(
        Season.objects.filter(event__mylineup__isnull=False)
        .values_list("year", flat=True)
        .distinct()
        .order_by("-year")
    )

    try:
        year = int(request.GET.get("year", seasons[0] if seasons else 2025))
    except (ValueError, TypeError):
        year = seasons[0] if seasons else 2025

    my_lineups = list(
        MyLineup.objects.filter(event__season__year=year)
        .select_related(
            "event",
            "driver_1", "driver_2", "driver_3", "driver_4", "driver_5",
            "drs_boost_driver", "constructor_1", "constructor_2",
        )
        .order_by("event__round_number")
    )

    # Best ML rec per event: latest by created_at (SQLite-safe; no DISTINCT ON)
    recs: dict[int, LineupRecommendation] = {}
    for r in LineupRecommendation.objects.filter(event__season__year=year).order_by("-created_at"):
        if r.event_id not in recs:
            recs[r.event_id] = r

    rows = []
    prev: MyLineup | None = None
    for ml in my_lineups:
        n_transfers = _count_transfers(prev, ml)
        rec = recs.get(ml.event_id)
        my_pts = ml.actual_points
        ml_predicted = rec.predicted_points if rec else None
        ml_actual = rec.actual_points if rec else None
        oracle = rec.oracle_actual_points if rec else None
        beat_ml = my_pts is not None and ml_actual is not None and my_pts > ml_actual
        left_on_table = (oracle - my_pts) if (oracle is not None and my_pts is not None) else None
        rows.append({
            "event": ml.event,
            "my_points": my_pts,
            "ml_predicted": ml_predicted,
            "ml_actual": ml_actual,
            "oracle": oracle,
            "left_on_table": left_on_table,
            "n_transfers": n_transfers,
            "beat_ml": beat_ml,
        })
        prev = ml

    my_total = sum(r["my_points"] for r in rows if r["my_points"] is not None)
    ml_actual_total = sum(r["ml_actual"] for r in rows if r["ml_actual"] is not None)
    oracle_total = sum(r["oracle"] for r in rows if r["oracle"] is not None)
    races_beat = sum(1 for r in rows if r["beat_ml"])

    context = {
        "seasons": seasons,
        "selected_year": year,
        "rows": rows,
        "my_total": my_total,
        "ml_actual_total": ml_actual_total,
        "oracle_total": oracle_total,
        "vs_ml": my_total - ml_actual_total,
        "vs_oracle": my_total - oracle_total,
        "races_beat": races_beat,
    }

    if request.headers.get("HX-Request"):
        return render(request, "predictions/partials/season_table.html", context)
    return render(request, "predictions/season_dashboard.html", context)


# ---------------------------------------------------------------------------
# Next Race
# ---------------------------------------------------------------------------


def next_race_redirect(request: HttpRequest) -> HttpResponse:
    from datetime import date, timedelta

    today = date.today()
    # Race weekends run Thursday–Sunday. event_date is always the race day (Sunday).
    # "Active or upcoming" = event_date is no more than 3 days in the past.
    weekend_start = today - timedelta(days=3)

    # Try the current/next event in the most recent season with scheduled events.
    event = (
        Event.objects.select_related("season")
        .filter(event_date__gte=weekend_start)
        .order_by("event_date")
        .first()
    )

    if event is None:
        # Season ended — show the most recent event we have
        event = Event.objects.select_related("season").order_by("-event_date").first()

    if event is None:
        return render(request, "predictions/next_race.html", {"no_data": True})

    return redirect("next_race", year=event.season.year, round_number=event.round_number)


def next_race(request: HttpRequest, year: int, round_number: int) -> HttpResponse:
    try:
        event = Event.objects.select_related("season", "circuit").get(
            season__year=year, round_number=round_number
        )
    except Event.DoesNotExist:
        raise Http404

    predictions = list(
        RacePrediction.objects.filter(event=event)
        .select_related("driver", "driver__team")
        .order_by("-predicted_fantasy_points")
    )

    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=event).values_list("driver_id", "price")
    )
    constructor_prices = dict(
        FantasyConstructorPrice.objects.filter(event=event).values_list("team_id", "price")
    )

    prediction_rows = [
        {
            "driver": p.driver,
            "predicted_pts": p.predicted_fantasy_points,
            "lower": p.confidence_lower,
            "upper": p.confidence_upper,
            "price": driver_prices.get(p.driver_id),
        }
        for p in predictions
    ]

    # Current team = most recent MyLineup submitted before this event
    current_lineup = (
        MyLineup.objects
        .filter(event__season__year=year, event__event_date__lt=event.event_date)
        .select_related(
            "driver_1", "driver_2", "driver_3", "driver_4", "driver_5",
            "drs_boost_driver", "constructor_1", "constructor_2",
        )
        .order_by("-event__event_date")
        .first()
    )

    rec = (
        LineupRecommendation.objects
        .filter(event=event)
        .select_related(
            "driver_1", "driver_2", "driver_3", "driver_4", "driver_5",
            "drs_boost_driver", "constructor_1", "constructor_2",
        )
        .order_by("-created_at")
        .first()
    )

    context: dict = {
        "event": event,
        "prediction_rows": prediction_rows,
        "has_predictions": bool(prediction_rows),
        "current_lineup": current_lineup,
        "rec": rec,
    }

    if current_lineup:
        context["current_drivers"] = [
            current_lineup.driver_1, current_lineup.driver_2, current_lineup.driver_3,
            current_lineup.driver_4, current_lineup.driver_5,
        ]
        context["current_constructors"] = [current_lineup.constructor_1, current_lineup.constructor_2]

    if rec:
        rec_drivers = [rec.driver_1, rec.driver_2, rec.driver_3, rec.driver_4, rec.driver_5]
        rec_constructors = [rec.constructor_1, rec.constructor_2]
        context["rec_drivers"] = rec_drivers
        context["rec_constructors"] = rec_constructors
        context["rec_constructor_prices"] = [
            constructor_prices.get(t.id) for t in rec_constructors
        ]

        if current_lineup:
            curr_driver_ids = {d.id for d in context["current_drivers"]}
            rec_driver_ids = {d.id for d in rec_drivers}
            curr_team_ids = {t.id for t in context["current_constructors"]}
            rec_team_ids = {t.id for t in rec_constructors}
            context["dropped_drivers"] = [d for d in context["current_drivers"] if d.id not in rec_driver_ids]
            context["added_drivers"] = [d for d in rec_drivers if d.id not in curr_driver_ids]
            context["dropped_teams"] = [t for t in context["current_constructors"] if t.id not in rec_team_ids]
            context["added_teams"] = [t for t in rec_constructors if t.id not in curr_team_ids]
            context["n_transfers"] = len(context["dropped_drivers"]) + len(context["dropped_teams"])

    return render(request, "predictions/next_race.html", context)


# ---------------------------------------------------------------------------
# Backtest Explorer
# ---------------------------------------------------------------------------


def backtest_explorer(request: HttpRequest) -> HttpResponse:
    runs = BacktestRun.objects.order_by("-created_at")
    selected_id = request.GET.get("run_id")
    race_results = []
    selected_run = None
    if selected_id:
        selected_run = BacktestRun.objects.filter(id=selected_id).first()
        if selected_run:
            race_results = list(
                selected_run.race_results
                .select_related("event", "event__season")
                .order_by("event__event_date")
            )
    context = {"runs": runs, "selected_run": selected_run, "race_results": race_results}
    if request.headers.get("HX-Request"):
        return render(request, "predictions/partials/backtest_run_detail.html", context)
    return render(request, "predictions/backtest.html", context)


# ---------------------------------------------------------------------------
# Driver Deep-Dive
# ---------------------------------------------------------------------------


def driver_detail(request: HttpRequest, year: int, driver_code: str) -> HttpResponse:
    # Stub: implemented in Step 5
    return render(request, "predictions/driver_detail.html", {})


# ---------------------------------------------------------------------------
# Price Trajectory
# ---------------------------------------------------------------------------


def price_trajectory(request: HttpRequest, year: int) -> HttpResponse:
    years = list(
        FantasyDriverPrice.objects.values_list("event__season__year", flat=True)
        .distinct().order_by("event__season__year")
    )

    all_prices = list(
        FantasyDriverPrice.objects.filter(event__season__year=year)
        .select_related("driver", "event")
        .order_by("event__round_number", "driver__code")
    )

    # Ordered events and drivers from the price records themselves
    events: dict = {}
    drivers: dict = {}
    for p in all_prices:
        events.setdefault(p.event_id, p.event)
        drivers.setdefault(p.driver_id, p.driver)

    event_list = list(events.values())
    driver_list = sorted(drivers.values(), key=lambda d: d.code)
    price_lookup = {(p.driver_id, p.event_id): p for p in all_prices}

    rows = []
    for driver in driver_list:
        cells = [price_lookup.get((driver.id, e.id)) for e in event_list]
        prices_present = [c for c in cells if c is not None]
        start = float(prices_present[0].price) if prices_present else None
        end = float(prices_present[-1].price) if prices_present else None
        net = round(end - start, 1) if start is not None and end is not None else None
        rows.append({"driver": driver, "cells": cells, "start": start, "end": end, "net": net})

    rows.sort(key=lambda r: r["net"] or 0, reverse=True)

    context = {
        "years": years,
        "selected_year": year,
        "events": event_list,
        "rows": rows,
    }
    return render(request, "predictions/price_trajectory.html", context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_transfers(prev: MyLineup | None, current: MyLineup) -> int:
    if prev is None:
        return 0
    prev_drivers = {prev.driver_1_id, prev.driver_2_id, prev.driver_3_id, prev.driver_4_id, prev.driver_5_id}
    curr_drivers = {current.driver_1_id, current.driver_2_id, current.driver_3_id, current.driver_4_id, current.driver_5_id}
    prev_teams = {prev.constructor_1_id, prev.constructor_2_id}
    curr_teams = {current.constructor_1_id, current.constructor_2_id}
    return len(curr_drivers - prev_drivers) + len(curr_teams - prev_teams)
