"""A readable parser for the documented QueryLab SQL subset."""

from __future__ import annotations

import re

from querylab.model import (
    ColumnRef,
    JoinCondition,
    Predicate,
    Query,
    Scalar,
    SelectItem,
    TableRef,
)


class SQLParseError(ValueError):
    """Raised when SQL is outside the supported subset."""


def _column_ref(text: str) -> ColumnRef:
    pieces = text.strip().split(".")
    if len(pieces) == 1:
        return ColumnRef(pieces[0])
    if len(pieces) == 2:
        return ColumnRef(pieces[1], pieces[0])
    raise SQLParseError(f"invalid column reference: {text}")


def _literal(text: str) -> Scalar:
    value = text.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]

    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError as error:
            raise SQLParseError(f"invalid literal: {text}") from error


def _select_item(text: str) -> SelectItem:
    item = text.strip()
    aggregate_match = re.fullmatch(
        r"(COUNT|SUM|AVG)\s*\(\s*([A-Za-z_][\w.]*)\s*\)"
        r"(?:\s+AS\s+([A-Za-z_]\w*))?",
        item,
        re.IGNORECASE,
    )
    if aggregate_match:
        aggregate, column, output_name = aggregate_match.groups()
        return SelectItem(
            column=_column_ref(column),
            aggregate=aggregate.upper(),
            output_name=output_name,
        )

    alias_match = re.fullmatch(
        r"([A-Za-z_][\w.]*)(?:\s+AS\s+([A-Za-z_]\w*))?",
        item,
        re.IGNORECASE,
    )
    if not alias_match:
        raise SQLParseError(f"unsupported SELECT item: {text}")

    column, output_name = alias_match.groups()
    return SelectItem(_column_ref(column), output_name=output_name)


def _parse_from(from_text: str) -> tuple[list[TableRef], list[JoinCondition]]:
    first_join = re.search(r"\s+JOIN\s+", from_text, re.IGNORECASE)
    first_table_text = from_text[: first_join.start()] if first_join else from_text
    table_parts = first_table_text.strip().split()

    if len(table_parts) not in (1, 2):
        raise SQLParseError("FROM must contain a table and optional alias")

    tables = [TableRef(table_parts[0], table_parts[-1])]
    joins: list[JoinCondition] = []
    remaining = from_text[first_join.start() :] if first_join else ""

    join_pattern = re.compile(
        r"\s+JOIN\s+([A-Za-z_]\w*)"
        r"(?:\s+([A-Za-z_]\w*))?"
        r"\s+ON\s+([A-Za-z_][\w.]*)\s*=\s*([A-Za-z_][\w.]*)",
        re.IGNORECASE,
    )
    position = 0

    while position < len(remaining):
        match = join_pattern.match(remaining, position)
        if not match:
            raise SQLParseError(f"unsupported JOIN clause: {remaining[position:]}")

        table_name, alias, left, right = match.groups()
        tables.append(TableRef(table_name, alias or table_name))
        joins.append(JoinCondition(_column_ref(left), _column_ref(right)))
        position = match.end()

    return tables, joins


def _parse_predicates(where_text: str) -> list[Predicate]:
    if re.search(r"\s+OR\s+", where_text, re.IGNORECASE):
        raise SQLParseError("OR predicates are deferred")

    predicates: list[Predicate] = []
    for expression in re.split(r"\s+AND\s+", where_text, flags=re.IGNORECASE):
        match = re.fullmatch(
            r"([A-Za-z_][\w.]*)\s*(<=|>=|=|<|>)\s*(.+)",
            expression.strip(),
        )
        if not match:
            raise SQLParseError(f"unsupported predicate: {expression}")

        column, operator, value = match.groups()
        predicates.append(Predicate(_column_ref(column), operator, _literal(value)))

    return predicates


def parse_sql(sql: str) -> Query:
    """Parse one SELECT statement into QueryLab's logical representation."""

    normalized = " ".join(sql.strip().rstrip(";").split())
    if not normalized.upper().startswith("SELECT "):
        raise SQLParseError("only SELECT statements are supported")

    if re.search(r"\b(NULL|DISTINCT|LEFT|RIGHT|FULL|OUTER|OVER)\b", normalized, re.I):
        raise SQLParseError("query uses a deferred SQL feature")

    match = re.fullmatch(
        r"SELECT\s+(.+?)\s+FROM\s+(.+?)"
        r"(?:\s+WHERE\s+(.+?))?"
        r"(?:\s+GROUP\s+BY\s+(.+))?",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        raise SQLParseError("query does not match the supported SELECT syntax")

    select_text, from_text, where_text, group_text = match.groups()
    tables, joins = _parse_from(from_text)

    if len(tables) > 3:
        raise SQLParseError("at most three tables are supported")

    select_items = [_select_item(item) for item in select_text.split(",")]
    predicates = _parse_predicates(where_text) if where_text else []
    group_by = []
    if group_text:
        group_by = [_column_ref(item) for item in group_text.split(",")]

    return Query(
        tables=tables,
        select_items=select_items,
        predicates=predicates,
        joins=joins,
        group_by=group_by,
    )
