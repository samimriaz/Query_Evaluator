"""Textbook selectivity estimates with optional histograms."""

from __future__ import annotations

from querylab.catalog.stats import ColumnStats
from querylab.model import Predicate


def estimate_predicate(predicate: Predicate, stats: ColumnStats) -> float:
    """Estimate the fraction of rows accepted by one predicate."""

    if predicate.operator == "=":
        # A histogram captures uneven value frequencies. Without one, assume
        # every distinct value appears equally often.
        if stats.histogram is not None:
            return stats.histogram.estimate_equality(predicate.value)
        return 1.0 / max(1, stats.distinct_count)

    # Min/max interpolation only makes sense for ordered numeric values.
    # Use a neutral fallback when this small estimator lacks usable data.
    if not isinstance(predicate.value, (int, float)):
        return 0.5
    if not isinstance(stats.minimum, (int, float)):
        return 0.5
    if not isinstance(stats.maximum, (int, float)):
        return 0.5

    value_range = stats.maximum - stats.minimum
    if value_range <= 0:
        return 1.0

    # Position the literal inside the observed range. Clamping handles values
    # below the minimum or above the maximum.
    fraction_below = (predicate.value - stats.minimum) / value_range
    fraction_below = max(0.0, min(1.0, fraction_below))

    if predicate.operator in ("<", "<="):
        return fraction_below
    if predicate.operator in (">", ">="):
        return 1.0 - fraction_below

    raise ValueError(f"unsupported predicate operator: {predicate.operator}")


def estimate_conjunction(
    predicates: list[Predicate],
    column_stats: dict[str, ColumnStats],
) -> float:
    """Multiply selectivities using the textbook independence assumption."""

    selectivity = 1.0
    for predicate in predicates:
        stats = column_stats[predicate.column.name]

        # This multiplication intentionally assumes predicates are
        # independent. Correlated columns make this estimate inaccurate and
        # are one of the behaviors QueryLab is designed to expose.
        selectivity *= estimate_predicate(predicate, stats)
    return selectivity
