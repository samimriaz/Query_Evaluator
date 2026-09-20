"""Physical query-plan nodes."""

from __future__ import annotations

from dataclasses import dataclass, field

from querylab.model import JoinCondition, Predicate, Query


@dataclass
class PlanNode:
    """One operator in a physical query plan."""

    node_id: str
    operation: str
    children: list[PlanNode] = field(default_factory=list)
    table_name: str | None = None
    table_alias: str | None = None
    predicates: list[Predicate] = field(default_factory=list)
    index_column: str | None = None
    join_condition: JoinCondition | None = None
    estimated_rows: float = 0.0
    estimated_io: float = 0.0
    query: Query | None = None

    def label(self) -> str:
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
        if not self.children:
            return self.label()
        child_text = ", ".join(child.describe() for child in self.children)
        return f"{self.label()}({child_text})"

