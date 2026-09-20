"""Execute candidate plans, verify results, and calculate regret."""

from __future__ import annotations

from dataclasses import dataclass

from querylab.catalog.catalog import Catalog
from querylab.database import Database
from querylab.executor.evaluator import EvaluationResult, PlanEvaluator
from querylab.model import Row
from querylab.optimizer.plan import PlanNode
from querylab.optimizer.plan_generator import PlanGenerator
from querylab.parser.sql_parser import parse_sql


@dataclass
class CandidateResult:
    """One candidate plan and its measured execution."""

    plan: PlanNode
    evaluation: EvaluationResult


@dataclass
class QueryReport:
    """Estimated and actual outcomes for a complete candidate set."""

    sql: str
    candidates: list[CandidateResult]
    chosen: CandidateResult
    actual_best: CandidateResult
    regret: float


def _canonical_rows(rows: list[Row]) -> tuple[str, ...]:
    """Normalize row order so equivalent plans can be compared."""

    normalized = [repr(sorted(row.items())) for row in rows]
    return tuple(sorted(normalized))


def evaluate_query(
    database: Database,
    sql: str,
    buffer_frames: int = 16,
    execute_all: bool = True,
    catalog: Catalog | None = None,
) -> QueryReport:
    """Optimize, execute, verify, and compare a SQL query."""

    query = parse_sql(sql)
    if catalog is None:
        catalog = Catalog(database)
        catalog.analyze()
    elif catalog.database is not database:
        raise ValueError("catalog belongs to a different database")

    plans = PlanGenerator(database, catalog, buffer_frames).generate(query)

    if not plans:
        raise RuntimeError("optimizer generated no candidate plans")

    plans_to_execute = plans if execute_all else plans[:1]
    evaluator = PlanEvaluator(database, buffer_frames)
    candidates: list[CandidateResult] = []
    expected_rows: tuple[str, ...] | None = None

    for plan in plans_to_execute:
        evaluation = evaluator.evaluate(plan)
        actual_rows = _canonical_rows(evaluation.rows)

        if expected_rows is None:
            expected_rows = actual_rows
        elif actual_rows != expected_rows:
            raise RuntimeError(
                f"candidate {plan.node_id} returned a different result"
            )

        candidates.append(CandidateResult(plan, evaluation))

    chosen = candidates[0]
    actual_best = min(
        candidates,
        key=lambda candidate: (candidate.evaluation.actual_io, candidate.plan.node_id),
    )

    best_io = actual_best.evaluation.actual_io
    if best_io == 0:
        regret = 0.0 if chosen.evaluation.actual_io == 0 else float("inf")
    else:
        regret = (chosen.evaluation.actual_io - best_io) / best_io

    return QueryReport(sql, candidates, chosen, actual_best, regret)
