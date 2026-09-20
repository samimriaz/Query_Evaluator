"""Plain-text rendering for query comparison reports."""

from __future__ import annotations

from querylab.report.compare import QueryReport


def render_report(report: QueryReport, candidate_limit: int = 20) -> str:
    """Render the most useful optimizer and evaluator measurements."""

    chosen = report.chosen
    lines = [
        f"Query: {report.sql}",
        f"Chosen plan: {chosen.plan.describe()}",
        "",
        "Operator                         Est rows   Act rows   Est I/O   Act I/O",
        "-----------------------------------------------------------------------",
    ]

    metric_by_id = {
        metric.node_id: metric for metric in chosen.evaluation.metrics
    }

    def add_operator(node: object, depth: int = 0) -> None:
        plan_node = node
        for child in plan_node.children:
            add_operator(child, depth + 1)

        metric = metric_by_id[plan_node.node_id]
        label = f"{'  ' * depth}{plan_node.label()}"
        lines.append(
            f"{label:<32}"
            f"{plan_node.estimated_rows:>10.0f}"
            f"{metric.rows_out:>11}"
            f"{plan_node.estimated_io:>10.0f}"
            f"{metric.actual_io:>10}"
        )

    add_operator(chosen.plan)
    lines.extend(
        [
            "",
            "Candidate plans:",
            "Rank  Estimated I/O  Actual I/O  Plan",
            "----  -------------  ----------  ----",
        ]
    )

    ranked = sorted(
        report.candidates,
        key=lambda candidate: (
            candidate.evaluation.actual_io,
            candidate.plan.node_id,
        ),
    )
    for rank, candidate in enumerate(ranked[:candidate_limit], start=1):
        marker = " [chosen]" if candidate is chosen else ""
        lines.append(
            f"{rank:>4}  {candidate.plan.estimated_io:>13.0f}"
            f"  {candidate.evaluation.actual_io:>10}"
            f"  {candidate.plan.describe()}{marker}"
        )

    if len(ranked) > candidate_limit:
        lines.append(f"... {len(ranked) - candidate_limit} more candidates")

    lines.append("")
    lines.append(f"Regret: {report.regret:.1%}")
    return "\n".join(lines)

