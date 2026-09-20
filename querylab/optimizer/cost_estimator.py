"""Estimate page I/O for physical plans."""

from __future__ import annotations

from math import ceil, log

from querylab.catalog.catalog import Catalog
from querylab.database import Database
from querylab.optimizer.plan import PlanNode
from querylab.optimizer.selectivity import estimate_conjunction


class CostEstimator:
    """Apply deliberately visible textbook I/O formulas."""

    def __init__(
        self,
        database: Database,
        catalog: Catalog,
        buffer_frames: int,
        tuples_per_page: int = 100,
    ) -> None:
        self.database = database
        self.catalog = catalog
        self.buffer_frames = buffer_frames
        self.tuples_per_page = tuples_per_page

    def estimate(self, node: PlanNode) -> PlanNode:
        for child in node.children:
            self.estimate(child)

        if node.operation in ("seq_scan", "index_scan"):
            self._estimate_scan(node)
        elif node.operation.endswith("_join"):
            self._estimate_join(node)
        elif node.children:
            node.estimated_rows = node.children[0].estimated_rows
            node.estimated_io = node.children[0].estimated_io

        return node

    def _estimate_scan(self, node: PlanNode) -> None:
        if node.table_name is None:
            raise ValueError("scan node is missing table_name")

        table_stats = self.catalog.table(node.table_name)
        selectivity = estimate_conjunction(node.predicates, table_stats.columns)
        node.estimated_rows = table_stats.row_count * selectivity

        if node.operation == "seq_scan":
            node.estimated_io = float(table_stats.page_count)
            return

        if node.index_column is None:
            raise ValueError("index scan is missing index_column")

        index = self.database.get_index(node.table_name, node.index_column)
        if index is None:
            raise ValueError("index scan refers to an index that does not exist")

        if index.clustered:
            data_pages = ceil(selectivity * table_stats.page_count)
        else:
            data_pages = ceil(selectivity * table_stats.row_count)

        node.estimated_io = float(index.height + data_pages)

    def _estimate_join(self, node: PlanNode) -> None:
        left, right = node.children
        left_pages = max(1, ceil(left.estimated_rows / self.tuples_per_page))
        right_pages = max(1, ceil(right.estimated_rows / self.tuples_per_page))

        if node.join_condition is None:
            raise ValueError("join node is missing its join condition")

        left_distinct = self._join_distinct_count(node, node.join_condition.left)
        right_distinct = self._join_distinct_count(node, node.join_condition.right)
        node.estimated_rows = (
            left.estimated_rows
            * right.estimated_rows
            / max(1, left_distinct, right_distinct)
        )

        child_io = left.estimated_io + right.estimated_io

        if node.operation == "nested_loop_join":
            repeated_inner_reads = max(0, ceil(left.estimated_rows) - 1) * right_pages
            node.estimated_io = child_io + repeated_inner_reads
        elif node.operation == "block_nested_loop_join":
            usable_frames = max(1, self.buffer_frames - 2)
            passes = ceil(left_pages / usable_frames)
            node.estimated_io = child_io + max(0, passes - 1) * right_pages
        elif node.operation == "hash_join":
            smaller_pages = min(left_pages, right_pages)
            if smaller_pages <= max(1, self.buffer_frames - 2):
                node.estimated_io = child_io
            else:
                node.estimated_io = child_io + 2 * (left_pages + right_pages)
        elif node.operation == "sort_merge_join":
            sort_io = self._external_sort_io(left_pages)
            sort_io += self._external_sort_io(right_pages)
            node.estimated_io = child_io + sort_io
        else:
            raise ValueError(f"unsupported join operation: {node.operation}")

    def _external_sort_io(self, page_count: int) -> int:
        if page_count <= self.buffer_frames:
            return 0

        initial_runs = ceil(page_count / self.buffer_frames)
        merge_fan_in = max(2, self.buffer_frames - 1)
        merge_passes = ceil(log(initial_runs, merge_fan_in))

        # Each pass writes and then rereads every temporary page.
        return 2 * page_count * (1 + merge_passes)

    def _join_distinct_count(self, node: PlanNode, column: object) -> int:
        table_alias = getattr(column, "table_alias")
        column_name = getattr(column, "name")

        for leaf in self._scan_leaves(node):
            if leaf.table_alias == table_alias and leaf.table_name is not None:
                stats = self.catalog.table(leaf.table_name)
                return stats.columns[column_name].distinct_count

        # Unqualified columns are allowed when exactly one table contains it.
        matches: list[int] = []
        for table_name in self.database.tables:
            stats = self.catalog.table(table_name)
            if column_name in stats.columns:
                matches.append(stats.columns[column_name].distinct_count)

        if len(matches) == 1:
            return matches[0]

        raise KeyError(f"no statistics found for join column: {column_name}")

    def _scan_leaves(self, node: PlanNode) -> list[PlanNode]:
        if node.operation in ("seq_scan", "index_scan"):
            return [node]

        leaves: list[PlanNode] = []
        for child in node.children:
            leaves.extend(self._scan_leaves(child))
        return leaves
