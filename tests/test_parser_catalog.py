import json
import tempfile
import unittest
from pathlib import Path

from querylab.catalog.catalog import Catalog
from querylab.datagen.generate import DataConfig, generate_database
from querylab.parser.sql_parser import SQLParseError, parse_sql


class ParserAndCatalogTests(unittest.TestCase):
    def test_parser_reads_join_filter_and_aggregate(self) -> None:
        query = parse_sql(
            "SELECT c.region, SUM(o.amount) "
            "FROM customers c JOIN orders o ON c.id = o.customer_id "
            "WHERE c.tier = 'gold' GROUP BY c.region"
        )

        self.assertEqual([table.alias for table in query.tables], ["c", "o"])
        self.assertEqual(len(query.joins), 1)
        self.assertEqual(query.predicates[0].value, "gold")
        self.assertEqual(query.select_items[1].aggregate, "SUM")

    def test_parser_rejects_deferred_features(self) -> None:
        with self.assertRaises(SQLParseError):
            parse_sql("SELECT DISTINCT c.region FROM customers c")

    def test_frozen_catalog_keeps_old_statistics_after_insert(self) -> None:
        database = generate_database(
            DataConfig(customer_rows=4, order_rows=8, product_rows=2)
        )
        catalog = Catalog(database)
        catalog.analyze()
        catalog.freeze()
        database.insert_rows(
            "customers",
            [{"id": 5, "region": "north", "tier": "gold", "signup_year": 2026}],
        )

        self.assertEqual(database.tables["customers"].row_count, 5)
        self.assertEqual(catalog.table("customers").row_count, 4)
        with self.assertRaises(RuntimeError):
            catalog.analyze()

    def test_catalog_snapshot_round_trip(self) -> None:
        database = generate_database(
            DataConfig(customer_rows=8, order_rows=24, product_rows=4)
        )
        catalog = Catalog(database)
        catalog.analyze()

        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot_path = Path(temporary_directory) / "catalog.json"
            catalog.save(snapshot_path)
            loaded = Catalog.load(database, snapshot_path)
            document = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(loaded.table_stats, catalog.table_stats)
        self.assertTrue(loaded.frozen)
        self.assertEqual(document["schema_version"], 1)
        self.assertIn("indexes", document["tables"]["customers"])


if __name__ == "__main__":
    unittest.main()
