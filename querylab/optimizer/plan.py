"""Physical query-plan nodes."""

from __future__ import annotations

from dataclasses import dataclass, field

from querylab.model import JoinCondition, Predicate, Query


@dataclass
class PlanNode:
    """One operator in a physical query plan."""

    # Every node receives a stable ID so equal-cost plans can be ordered
    # deterministically and execution metrics can be matched back to the node.
    node_id: str
    operation: str

    # Scan nodes have no children, joins have two, and result operators have
    # one. This uniform tree shape keeps traversal and reporting simple.
    children: list[PlanNode] = field(default_factory=list)

    # Scan-only fields identify the source and any predicate pushed into it.
    table_name: str | None = None
    table_alias: str | None = None
    predicates: list[Predicate] = field(default_factory=list)
    index_column: str | None = None

    # Join nodes use this condition to orient their left and right keys.
    join_condition: JoinCondition | None = None

    # The estimator fills these values from the leaves upward.
    estimated_rows: float = 0.0
    estimated_io: float = 0.0

    # The top project/aggregate node retains the logical query so it knows
    # which columns and aggregate expressions to return.
    query: Query | None = None

    def label(self) -> str:
        """Return a short operator name for tables and reports."""

        if self.operation == "seq_scan":
            return f"SeqScan({self.table_name} AS {self.table_alias})"
        if self.operation == "index_scan":
            return (
                f"IndexScan({self.table_name}.{self.index_column} "
                f"AS {self.table_alias})"
            )
        if self.operation.endswith("_join"):
            return self.operation.replace("_", " ").title().replace(" ", "")
        return self.operation.title()

    def describe(self) -> str:
        """Render this node and its children as one nested plan expression."""

        if not self.children:
            return self.label()
        child_text = ", ".join(child.describe() for child in self.children)
        return f"{self.label()}({child_text})"
