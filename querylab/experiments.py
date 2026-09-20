"""Repeatable experiments for physical layouts, filters, and cache sizes."""

from __future__ import annotations

from dataclasses import dataclass

from querylab.datagen.generate import DataConfig, generate_database
from querylab.report.compare import QueryReport, evaluate_query


@dataclass(frozen=True)
class ExperimentResult:
    """One named configuration and its query report."""

    name: str
    page_counts: dict[str, int]
    sql: str
    report: QueryReport


def run_layout_experiment(
    customer_rows: int = 100,
    order_rows: int = 2_000,
    product_rows: int = 50,
    buffer_frames: int = 16,
    seed: int = 42,
) -> list[ExperimentResult]:
    """Compare filtered and unfiltered queries under two page layouts."""

    layouts = {
        "customers-wide": {"customers": 50, "orders": 100, "products": 5},
        "orders-wide": {"customers": 10, "orders": 200, "products": 5},
    }
    queries = {
        "filtered": (
            "SELECT c.region, SUM(o.amount) "
            "FROM customers c JOIN orders o ON c.id = o.customer_id "
            "WHERE c.tier = 'gold' GROUP BY c.region"
        ),
        "unfiltered": (
            "SELECT c.region, SUM(o.amount) "
            "FROM customers c JOIN orders o ON c.id = o.customer_id "
            "GROUP BY c.region"
        ),
    }
    results: list[ExperimentResult] = []

    for layout_name, page_counts in layouts.items():
        config = DataConfig(
            customer_rows=customer_rows,
            order_rows=order_rows,
            product_rows=product_rows,
            page_counts=page_counts,
            seed=seed,
        )
        database = generate_database(config)

        for query_name, sql in queries.items():
            report = evaluate_query(database, sql, buffer_frames)
            results.append(
                ExperimentResult(
                    name=f"{layout_name}/{query_name}",
                    page_counts=page_counts,
                    sql=sql,
                    report=report,
                )
            )

    return results


def render_experiments(results: list[ExperimentResult]) -> str:
    lines = [
        "Experiment                    Customers  Orders  Est I/O  Act I/O  Regret",
        "--------------------------------------------------------------------------",
    ]

    for result in results:
        lines.append(
            f"{result.name:<29}"
            f"{result.page_counts['customers']:>10}"
            f"{result.page_counts['orders']:>8}"
            f"{result.report.chosen.plan.estimated_io:>9.0f}"
            f"{result.report.chosen.evaluation.actual_io:>9}"
            f"{result.report.regret:>8.1%}"
        )

    return "\n".join(lines)

