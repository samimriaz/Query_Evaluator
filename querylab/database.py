"""Database container shared by the catalog, optimizer, and executor."""

from __future__ import annotations

from querylab.model import Row
from querylab.storage.index import BTreeIndex
from querylab.storage.table import Table


class Database:
    """Own tables and their optional indexes."""

    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}
        self.indexes: dict[tuple[str, str], BTreeIndex] = {}

    def add_table(self, table: Table) -> None:
        if table.name in self.tables:
            raise ValueError(f"table already exists: {table.name}")
        self.tables[table.name] = table

    def create_index(
        self,
        table_name: str,
        column: str,
        clustered: bool = False,
    ) -> BTreeIndex:
        table = self.tables[table_name]
        index = BTreeIndex(table, column, clustered)
        self.indexes[(table_name, column)] = index
        return index

    def get_index(self, table_name: str, column: str) -> BTreeIndex | None:
        return self.indexes.get((table_name, column))

    def insert_rows(self, table_name: str, rows: list[Row]) -> None:
        """Insert rows and rebuild indexes while catalog stats remain untouched."""

        table = self.tables[table_name]
        indexed_columns = [
            (column, index.clustered)
            for (indexed_table, column), index in self.indexes.items()
            if indexed_table == table_name
        ]

        table.insert_rows(rows)

        for column, clustered in indexed_columns:
            self.indexes[(table_name, column)] = BTreeIndex(
                table,
                column,
                clustered,
            )
