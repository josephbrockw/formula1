from predictions.predictors.xgboost.shared import _RACE_POSITION_BASE_POINTS


def position_to_fantasy_points(position: int) -> float:
    """Return base fantasy points for a finishing position (no bonuses)."""
    return _RACE_POSITION_BASE_POINTS.get(position, 0.0)
