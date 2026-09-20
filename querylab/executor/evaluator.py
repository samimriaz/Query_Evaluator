"""Execute physical plans and meter their actual page I/O."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from querylab.database import Database
from querylab.executor.external_sort import external_merge_sort
from querylab.executor.metrics import OperatorMetrics
from querylab.model import ColumnRef, Predicate, Query, Row, Scalar, SelectItem
from querylab.optimizer.plan import PlanNode
from querylab.storage.buffer_pool import BufferPool


@dataclass
class EvaluationResult:
    """Rows and measurements produced by one plan execution."""

    rows: list[Row]
    metrics: list[OperatorMetrics]
    actual_io: int


class PlanEvaluator:
    """Run one physical plan against a shared per-plan buffer pool."""

    def __init__(
        self,
        database: Database,
        buffer_frames: int,
        tuples_per_page: int = 100,
    ) -> None:
        self.database = database
        self.buffer_frames = buffer_frames
        self.tuples_per_page = tuples_per_page

    def evaluate(self, plan: PlanNode) -> EvaluationResult:
        # Every candidate starts cold so execution order cannot make one plan
        # appear cheaper by inheriting another plan's cached pages.
        buffer_pool: BufferPool[object] = BufferPool(self.buffer_frames)
        metrics: list[OperatorMetrics] = []
        rows = self._execute(plan, buffer_pool, metrics)
        return EvaluationResult(rows, metrics, buffer_pool.metrics.actual_io)

    def _execute(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
        all_metrics: list[OperatorMetrics],
    ) -> list[Row]:
        # This dispatcher keeps the plan tree visible: each operator executes
        # its children, performs its own algorithm, and records its own work.
        if node.operation == "seq_scan":
            rows, metric = self._sequential_scan(node, buffer_pool)
        elif node.operation == "index_scan":
            rows, metric = self._index_scan(node, buffer_pool)
        elif node.operation == "nested_loop_join":
            rows, metric = self._nested_loop_join(node, buffer_pool, all_metrics)
        elif node.operation == "block_nested_loop_join":
            rows, metric = self._block_nested_loop_join(
                node,
                buffer_pool,
                all_metrics,
            )
        elif node.operation == "hash_join":
            rows, metric = self._hash_join(node, buffer_pool, all_metrics)
        elif node.operation == "sort_merge_join":
            rows, metric = self._sort_merge_join(node, buffer_pool, all_metrics)
        elif node.operation in ("project", "aggregate"):
            rows, metric = self._produce_result(node, buffer_pool, all_metrics)
        else:
            raise ValueError(f"unsupported physical operator: {node.operation}")

        all_metrics.append(metric)
        return rows

    def _sequential_scan(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
    ) -> tuple[list[Row], OperatorMetrics]:
        table_name, table_alias = self._scan_identity(node)
        table = self.database.tables[table_name]
        metric = OperatorMetrics(node.node_id, node.label())
        misses_before = buffer_pool.metrics.buffer_misses
        rows: list[Row] = []

        # A sequential scan must request every page. Predicates are evaluated
        # here so rejected rows do not flow into the parent operator.
        for page_number in range(table.page_count):
            page = table.fetch_page(page_number, buffer_pool)
            for source_row in page.rows:
                qualified_row = self._qualify_row(source_row, table_alias)
                if self._matches_all(qualified_row, node.predicates, metric):
                    rows.append(qualified_row)

        metric.rows_in = table.row_count
        metric.rows_out = len(rows)
        metric.buffer_misses = buffer_pool.metrics.buffer_misses - misses_before
        return rows, metric

    def _index_scan(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
    ) -> tuple[list[Row], OperatorMetrics]:
        table_name, table_alias = self._scan_identity(node)
        if node.index_column is None:
            raise ValueError("index scan is missing index_column")

        table = self.database.tables[table_name]
        index = self.database.get_index(table_name, node.index_column)
        if index is None:
            raise ValueError(f"index does not exist: {table_name}.{node.index_column}")

        index_predicate = next(
            predicate
            for predicate in node.predicates
            if predicate.column.name == node.index_column
            and predicate.operator == "="
        )
        metric = OperatorMetrics(node.node_id, node.label())
        misses_before = buffer_pool.metrics.buffer_misses

        # First traverse the index to get physical (page, row) locations.
        locations = index.equality_search(index_predicate.value, buffer_pool)
        rows: list[Row] = []

        # Then fetch the referenced data pages. Repeated locations on one page
        # become buffer hits, which is why actual unclustered-index I/O can be
        # lower than its conservative estimate.
        for page_number, row_offset in locations:
            page = table.fetch_page(page_number, buffer_pool)
            qualified_row = self._qualify_row(page.rows[row_offset], table_alias)
            if self._matches_all(qualified_row, node.predicates, metric):
                rows.append(qualified_row)

        metric.rows_in = len(locations)
        metric.rows_out = len(rows)
        metric.buffer_misses = buffer_pool.metrics.buffer_misses - misses_before
        return rows, metric

    def _nested_loop_join(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
        all_metrics: list[OperatorMetrics],
    ) -> tuple[list[Row], OperatorMetrics]:
        left_rows = self._execute(node.children[0], buffer_pool, all_metrics)
        right_rows = self._execute(node.children[1], buffer_pool, all_metrics)
        metric, misses_before, writes_before = self._join_metric(node, buffer_pool)
        output: list[Row] = []

        # This direct formulation intentionally exposes the two nested loops.
        for left_row in left_rows:
            for right_row in right_rows:
                metric.comparisons += 1
                if self._join_keys_match(node, left_row, right_row):
                    output.append(left_row | right_row)

        # The inner input is read once per outer row. Its first read happened
        # while producing right_rows, so only the remaining passes are added.
        self._rescan_leaf_pages(
            node.children[1],
            max(0, len(left_rows) - 1),
            buffer_pool,
        )
        metric.rows_in = len(left_rows) + len(right_rows)
        metric.rows_out = len(output)
        metric.buffer_misses = buffer_pool.metrics.buffer_misses - misses_before
        metric.page_writes = buffer_pool.metrics.page_writes - writes_before
        return output, metric

    def _block_nested_loop_join(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
        all_metrics: list[OperatorMetrics],
    ) -> tuple[list[Row], OperatorMetrics]:
        left_rows = self._execute(node.children[0], buffer_pool, all_metrics)
        right_rows = self._execute(node.children[1], buffer_pool, all_metrics)
        metric, misses_before, writes_before = self._join_metric(node, buffer_pool)
        output: list[Row] = []

        usable_frames = max(1, self.buffer_frames - 2)
        rows_per_block = usable_frames * self.tuples_per_page

        # Keep one left-side block in memory while scanning all right rows.
        blocks = [
            left_rows[start : start + rows_per_block]
            for start in range(0, len(left_rows), rows_per_block)
        ]

        for block in blocks:
            for right_row in right_rows:
                for left_row in block:
                    metric.comparisons += 1
                    if self._join_keys_match(node, left_row, right_row):
                        output.append(left_row | right_row)

        self._rescan_leaf_pages(
            node.children[1],
            max(0, len(blocks) - 1),
            buffer_pool,
        )
        metric.rows_in = len(left_rows) + len(right_rows)
        metric.rows_out = len(output)
        metric.buffer_misses = buffer_pool.metrics.buffer_misses - misses_before
        metric.page_writes = buffer_pool.metrics.page_writes - writes_before
        return output, metric

    def _hash_join(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
        all_metrics: list[OperatorMetrics],
    ) -> tuple[list[Row], OperatorMetrics]:
        left_rows = self._execute(node.children[0], buffer_pool, all_metrics)
        right_rows = self._execute(node.children[1], buffer_pool, all_metrics)
        metric, misses_before, writes_before = self._join_metric(node, buffer_pool)

        # Build the hash table from the smaller input.
        if len(left_rows) <= len(right_rows):
            build_rows = left_rows
            probe_rows = right_rows
            build_ref, probe_ref = self._oriented_join_refs(node, left_rows, right_rows)
            build_is_left = True
        else:
            build_rows = right_rows
            probe_rows = left_rows
            probe_ref, build_ref = self._oriented_join_refs(node, left_rows, right_rows)
            build_is_left = False

        build_pages = ceil(len(build_rows) / self.tuples_per_page)
        if build_pages > max(1, self.buffer_frames - 2):
            # Grace hash join: partition both inputs when one build hash table
            # would exceed the available frames.
            output = self._partitioned_hash_join(
                node,
                build_rows,
                probe_rows,
                build_ref,
                probe_ref,
                build_is_left,
                metric,
                buffer_pool,
            )
        else:
            # One-pass hash join: build and probe entirely in memory.
            output = self._hash_join_partition(
                build_rows,
                probe_rows,
                build_ref,
                probe_ref,
                build_is_left,
                metric,
            )

        metric.rows_in = len(left_rows) + len(right_rows)
        metric.rows_out = len(output)
        metric.buffer_misses = buffer_pool.metrics.buffer_misses - misses_before
        metric.page_writes = buffer_pool.metrics.page_writes - writes_before
        return output, metric

    def _sort_merge_join(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
        all_metrics: list[OperatorMetrics],
    ) -> tuple[list[Row], OperatorMetrics]:
        left_rows = self._execute(node.children[0], buffer_pool, all_metrics)
        right_rows = self._execute(node.children[1], buffer_pool, all_metrics)
        metric, misses_before, writes_before = self._join_metric(node, buffer_pool)
        left_ref, right_ref = self._oriented_join_refs(node, left_rows, right_rows)

        # Sort both inputs on their correctly oriented join keys.
        sorted_left = external_merge_sort(
            left_rows,
            lambda row: self._column_value(row, left_ref),
            buffer_pool,
            self.buffer_frames,
            self.tuples_per_page,
            f"{node.node_id}:left",
        )
        sorted_right = external_merge_sort(
            right_rows,
            lambda row: self._column_value(row, right_ref),
            buffer_pool,
            self.buffer_frames,
            self.tuples_per_page,
            f"{node.node_id}:right",
        )

        output: list[Row] = []
        left_position = 0
        right_position = 0

        # Advance the side with the smaller key. Equal keys are handled as
        # groups so duplicate keys produce every valid row combination.
        while left_position < len(sorted_left) and right_position < len(sorted_right):
            left_key = self._column_value(sorted_left[left_position], left_ref)
            right_key = self._column_value(sorted_right[right_position], right_ref)
            metric.comparisons += 1

            if left_key < right_key:
                left_position += 1
                continue
            if left_key > right_key:
                right_position += 1
                continue

            left_group_end = left_position
            while (
                left_group_end < len(sorted_left)
                and self._column_value(sorted_left[left_group_end], left_ref) == left_key
            ):
                left_group_end += 1

            right_group_end = right_position
            while (
                right_group_end < len(sorted_right)
                and self._column_value(sorted_right[right_group_end], right_ref) == right_key
            ):
                right_group_end += 1

            for left_row in sorted_left[left_position:left_group_end]:
                for right_row in sorted_right[right_position:right_group_end]:
                    output.append(left_row | right_row)

            left_position = left_group_end
            right_position = right_group_end

        metric.rows_in = len(left_rows) + len(right_rows)
        metric.rows_out = len(output)
        metric.buffer_misses = buffer_pool.metrics.buffer_misses - misses_before
        metric.page_writes = buffer_pool.metrics.page_writes - writes_before
        return output, metric

    def _produce_result(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
        all_metrics: list[OperatorMetrics],
    ) -> tuple[list[Row], OperatorMetrics]:
        input_rows = self._execute(node.children[0], buffer_pool, all_metrics)
        metric = OperatorMetrics(node.node_id, node.label(), rows_in=len(input_rows))
        query = node.query
        if query is None:
            raise ValueError("result operator is missing its query")

        if node.operation == "project":
            rows = [self._project_row(row, query.select_items) for row in input_rows]
        else:
            rows = self._aggregate_rows(input_rows, query)

        metric.rows_out = len(rows)
        return rows, metric

    def _aggregate_rows(self, rows: list[Row], query: Query) -> list[Row]:
        groups: dict[tuple[Scalar, ...], list[Row]] = {}

        # Build one bucket per GROUP BY key. An empty key creates one global
        # aggregate group for queries without GROUP BY.
        for row in rows:
            group_key = tuple(
                self._column_value(row, column) for column in query.group_by
            )
            groups.setdefault(group_key, []).append(row)

        if not query.group_by and not groups:
            groups[()] = []

        output: list[Row] = []
        for group_rows in groups.values():
            result: Row = {}

            for item in query.select_items:
                output_name = self._output_name(item)
                if item.aggregate is None:
                    if not group_rows:
                        raise ValueError("cannot project a column from an empty group")
                    result[output_name] = self._column_value(group_rows[0], item.column)
                else:
                    result[output_name] = self._aggregate_value(item, group_rows)

            output.append(result)

        return output

    def _aggregate_value(self, item: SelectItem, rows: list[Row]) -> Scalar:
        if item.aggregate == "COUNT":
            return len(rows)

        values = [self._column_value(row, item.column) for row in rows]
        if not all(isinstance(value, (int, float)) for value in values):
            raise TypeError(f"{item.aggregate} requires a numeric column")

        numeric_values = [float(value) for value in values]
        if item.aggregate == "SUM":
            return round(sum(numeric_values), 2)
        if item.aggregate == "AVG":
            if not numeric_values:
                return 0.0
            return round(sum(numeric_values) / len(numeric_values), 2)
        raise ValueError(f"unsupported aggregate: {item.aggregate}")

    def _project_row(self, row: Row, items: list[SelectItem]) -> Row:
        result: Row = {}
        for item in items:
            result[self._output_name(item)] = self._column_value(row, item.column)
        return result

    def _output_name(self, item: SelectItem) -> str:
        if item.output_name:
            return item.output_name
        if item.aggregate:
            return f"{item.aggregate.lower()}_{item.column.name}"
        return item.column.name

    def _matches_all(
        self,
        row: Row,
        predicates: list[Predicate],
        metric: OperatorMetrics,
    ) -> bool:
        for predicate in predicates:
            metric.predicate_evaluations += 1
            actual = self._column_value(row, predicate.column)
            expected = predicate.value

            if predicate.operator == "=" and actual != expected:
                return False
            if predicate.operator == "<" and not actual < expected:
                return False
            if predicate.operator == "<=" and not actual <= expected:
                return False
            if predicate.operator == ">" and not actual > expected:
                return False
            if predicate.operator == ">=" and not actual >= expected:
                return False

        return True

    def _join_keys_match(
        self,
        node: PlanNode,
        left_row: Row,
        right_row: Row,
    ) -> bool:
        left_ref, right_ref = self._oriented_join_refs(node, [left_row], [right_row])
        return (
            self._column_value(left_row, left_ref)
            == self._column_value(right_row, right_ref)
        )

    def _oriented_join_refs(
        self,
        node: PlanNode,
        left_rows: list[Row],
        right_rows: list[Row],
    ) -> tuple[ColumnRef, ColumnRef]:
        condition = node.join_condition
        if condition is None:
            raise ValueError("join is missing its condition")
        if not left_rows or not right_rows:
            return condition.left, condition.right

        left_key = condition.left.qualified_name
        if left_key in left_rows[0]:
            return condition.left, condition.right
        return condition.right, condition.left

    def _partitioned_hash_join(
        self,
        node: PlanNode,
        build_rows: list[Row],
        probe_rows: list[Row],
        build_ref: ColumnRef,
        probe_ref: ColumnRef,
        build_is_left: bool,
        metric: OperatorMetrics,
        buffer_pool: BufferPool[object],
    ) -> list[Row]:
        """Partition spilled inputs, then build and probe one partition at a time."""

        memory_capacity_pages = max(1, self.buffer_frames - 2)
        build_pages = ceil(len(build_rows) / self.tuples_per_page)
        partition_count = max(2, ceil(build_pages / memory_capacity_pages))
        build_partitions: list[list[Row]] = [[] for _ in range(partition_count)]
        probe_partitions: list[list[Row]] = [[] for _ in range(partition_count)]

        # The same hash-to-partition rule guarantees that equal keys from both
        # inputs land in matching partitions.
        for row in build_rows:
            key = self._column_value(row, build_ref)
            partition_number = hash(key) % partition_count
            build_partitions[partition_number].append(row)
            metric.hash_operations += 1

        for row in probe_rows:
            key = self._column_value(row, probe_ref)
            partition_number = hash(key) % partition_count
            probe_partitions[partition_number].append(row)
            metric.hash_operations += 1

        all_partitions = build_partitions + probe_partitions
        total_pages = sum(
            ceil(len(partition) / self.tuples_per_page)
            for partition in all_partitions
            if partition
        )

        # Partitioning writes both inputs to temporary storage.
        for _ in range(total_pages):
            buffer_pool.write_temporary_page()

        # Each temporary partition is read back for its local build/probe pass.
        for page_number in range(total_pages):
            page_key = f"temp:{node.node_id}:hash-partition:{page_number}"
            buffer_pool.fetch(page_key, lambda: page_key)

        output: list[Row] = []

        # Each build partition now fits in memory, so it can use a one-pass
        # hash join against the corresponding probe partition.
        for build_partition, probe_partition in zip(
            build_partitions,
            probe_partitions,
        ):
            output.extend(
                self._hash_join_partition(
                    build_partition,
                    probe_partition,
                    build_ref,
                    probe_ref,
                    build_is_left,
                    metric,
                )
            )
        return output

    def _hash_join_partition(
        self,
        build_rows: list[Row],
        probe_rows: list[Row],
        build_ref: ColumnRef,
        probe_ref: ColumnRef,
        build_is_left: bool,
        metric: OperatorMetrics,
    ) -> list[Row]:
        """Build a hash table for one partition and probe it."""

        hash_table: dict[Scalar, list[Row]] = {}

        # Build phase: retain every row for a key so duplicate join keys work.
        for build_row in build_rows:
            key = self._column_value(build_row, build_ref)
            hash_table.setdefault(key, []).append(build_row)
            metric.hash_operations += 1

        output: list[Row] = []

        # Probe phase: one lookup finds all build rows with the same key.
        for probe_row in probe_rows:
            key = self._column_value(probe_row, probe_ref)
            metric.hash_operations += 1

            for build_row in hash_table.get(key, []):
                if build_is_left:
                    output.append(build_row | probe_row)
                else:
                    output.append(probe_row | build_row)

        return output

    def _rescan_leaf_pages(
        self,
        node: PlanNode,
        pass_count: int,
        buffer_pool: BufferPool[object],
    ) -> None:
        if pass_count <= 0:
            return

        if node.operation in ("seq_scan", "index_scan"):
            if node.table_name is None:
                raise ValueError("scan is missing table_name")
            table = self.database.tables[node.table_name]

            # Re-request pages in scan order instead of incrementing a counter
            # directly. The buffer pool can therefore turn repeated reads into
            # hits exactly as it would during execution.
            for _ in range(pass_count):
                for page_number in range(table.page_count):
                    table.fetch_page(page_number, buffer_pool)
            return

        for child in node.children:
            self._rescan_leaf_pages(child, pass_count, buffer_pool)

    def _join_metric(
        self,
        node: PlanNode,
        buffer_pool: BufferPool[object],
    ) -> tuple[OperatorMetrics, int, int]:
        metric = OperatorMetrics(node.node_id, node.label())
        return (
            metric,
            buffer_pool.metrics.buffer_misses,
            buffer_pool.metrics.page_writes,
        )

    def _scan_identity(self, node: PlanNode) -> tuple[str, str]:
        if node.table_name is None or node.table_alias is None:
            raise ValueError("scan is missing table identity")
        return node.table_name, node.table_alias

    def _qualify_row(self, row: Row, alias: str) -> Row:
        return {f"{alias}.{column}": value for column, value in row.items()}

    def _column_value(self, row: Row, column: ColumnRef) -> Scalar:
        if column.table_alias:
            return row[column.qualified_name]

        matching_keys = [
            key for key in row if key == column.name or key.endswith(f".{column.name}")
        ]
        if len(matching_keys) != 1:
            raise KeyError(f"ambiguous or missing column: {column.name}")
        return row[matching_keys[0]]
