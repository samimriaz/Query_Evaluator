"""Enumerate access paths, left-deep join orders, and join algorithms."""

from __future__ import annotations

from itertools import count, permutations, product

from querylab.catalog.catalog import Catalog
from querylab.database import Database
from querylab.model import JoinCondition, Query, TableRef
from querylab.optimizer.cost_estimator import CostEstimator
from querylab.optimizer.plan import PlanNode

JOIN_OPERATIONS = (
    "hash_join",
    "block_nested_loop_join",
    "sort_merge_join",
    "nested_loop_join",
)


class PlanGenerator:
    """Generate and rank a small, exhaustive physical-plan space."""

    def __init__(
        self,
        database: Database,
        catalog: Catalog,
        buffer_frames: int,
    ) -> None:
        self.database = database
        self.catalog = catalog
        self.estimator = CostEstimator(database, catalog, buffer_frames)
        self._ids = count(1)

    def generate(self, query: Query) -> list[PlanNode]:
        candidates: list[PlanNode] = []

        for table_order in permutations(query.tables):
            access_options = [self._access_paths(query, table) for table in table_order]

            for access_nodes in product(*access_options):
                join_count = max(0, len(access_nodes) - 1)
                algorithm_choices = product(JOIN_OPERATIONS, repeat=join_count)

                for algorithms in algorithm_choices:
                    root = self._build_join_tree(
                        query,
                        list(access_nodes),
                        list(algorithms),
                    )
                    if root is None:
                        continue
                    root = self._add_result_operator(query, root)
                    self.estimator.estimate(root)
                    candidates.append(root)

        candidates.sort(key=lambda plan: (plan.estimated_io, plan.node_id))
        return candidates

    def _access_paths(self, query: Query, table: TableRef) -> list[PlanNode]:
        predicates = [
            predicate
            for predicate in query.predicates
            if predicate.column.table_alias in (None, table.alias)
        ]
        paths = [
            PlanNode(
                node_id=self._next_id(),
                operation="seq_scan",
                table_name=table.name,
                table_alias=table.alias,
                predicates=predicates,
            )
        ]

        for predicate in predicates:
            if predicate.operator != "=":
                continue
            if self.database.get_index(table.name, predicate.column.name) is None:
                continue

            paths.append(
                PlanNode(
                    node_id=self._next_id(),
                    operation="index_scan",
                    table_name=table.name,
                    table_alias=table.alias,
                    predicates=predicates,
                    index_column=predicate.column.name,
                )
            )

        return paths

    def _build_join_tree(
        self,
        query: Query,
        access_nodes: list[PlanNode],
        algorithms: list[str],
    ) -> PlanNode | None:
        root = access_nodes[0]
        joined_aliases = {root.table_alias}

        for next_node, algorithm in zip(access_nodes[1:], algorithms):
            condition = self._find_join_condition(
                query.joins,
                joined_aliases,
                next_node.table_alias,
            )
            if condition is None:
                return None
            root = PlanNode(
                node_id=self._next_id(),
                operation=algorithm,
                children=[root, next_node],
                join_condition=condition,
            )
            joined_aliases.add(next_node.table_alias)

        return root

    def _find_join_condition(
        self,
        conditions: list[JoinCondition],
        joined_aliases: set[str | None],
        next_alias: str | None,
    ) -> JoinCondition | None:
        for condition in conditions:
            aliases = {
                condition.left.table_alias,
                condition.right.table_alias,
            }
            if next_alias in aliases and aliases.intersection(joined_aliases):
                return condition

        return None

    def _add_result_operator(self, query: Query, root: PlanNode) -> PlanNode:
        has_aggregate = any(item.aggregate for item in query.select_items)
        operation = "aggregate" if has_aggregate or query.group_by else "project"
        return PlanNode(
            node_id=self._next_id(),
            operation=operation,
            children=[root],
            query=query,
        )

    def _next_id(self) -> str:
        return f"op-{next(self._ids):05d}"
