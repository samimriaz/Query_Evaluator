"""Command-line interface for QueryLab."""

from __future__ import annotations

import argparse

from querylab.datagen.generate import DataConfig, generate_database
from querylab.experiments import render_experiments, run_layout_experiment
from querylab.report.compare import evaluate_query
from querylab.report.render import render_report

DEFAULT_QUERY = (
    "SELECT c.region, SUM(o.amount) "
    "FROM customers c JOIN orders o ON c.id = o.customer_id "
    "WHERE c.tier = 'gold' GROUP BY c.region"
)


def _add_data_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--customers", type=int, default=100)
    parser.add_argument("--orders", type=int, default=2_000)
    parser.add_argument("--products", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skew", type=float, default=0.0)
    parser.add_argument("--correlation", type=float, default=0.8)
    parser.add_argument("--buffer-frames", type=int, default=16)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="querylab",
        description="Estimate and execute query plans using page I/O cost.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Generate data and compare candidate plans.",
    )
    evaluate_parser.add_argument("sql", nargs="?", default=DEFAULT_QUERY)
    evaluate_parser.add_argument(
        "--chosen-only",
        action="store_true",
        help="Execute only the optimizer's chosen plan.",
    )
    _add_data_arguments(evaluate_parser)

    experiment_parser = subparsers.add_parser(
        "experiment",
        help="Compare layouts with filtered and unfiltered queries.",
    )
    _add_data_arguments(experiment_parser)
    return parser


def _data_config(arguments: argparse.Namespace) -> DataConfig:
    return DataConfig(
        customer_rows=arguments.customers,
        order_rows=arguments.orders,
        product_rows=arguments.products,
        seed=arguments.seed,
        skew=arguments.skew,
        correlation=arguments.correlation,
    )


def main(arguments: list[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(arguments)

    if options.command == "evaluate":
        database = generate_database(_data_config(options))
        report = evaluate_query(
            database,
            options.sql,
            options.buffer_frames,
            execute_all=not options.chosen_only,
        )
        print(render_report(report))
        return 0

    if options.command == "experiment":
        results = run_layout_experiment(
            customer_rows=options.customers,
            order_rows=options.orders,
            product_rows=options.products,
            buffer_frames=options.buffer_frames,
            seed=options.seed,
        )
        print(render_experiments(results))
        return 0

    parser.error(f"unknown command: {options.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
