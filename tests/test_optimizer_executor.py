import unittest

from querylab.catalog.catalog import Catalog
from querylab.datagen.generate import DataConfig, generate_database
from querylab.executor.evaluator import PlanEvaluator
from querylab.optimizer.plan_generator import PlanGenerator
from querylab.parser.sql_parser import parse_sql
from querylab.report.compare import evaluate_query


SQL = (
    "SELECT c.region, SUM(o.amount) "
    "FROM customers c JOIN orders o ON c.id = o.customer_id "
    "WHERE c.tier = 'gold' GROUP BY c.region"
)


class OptimizerExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = generate_database(
            DataConfig(
                customer_rows=8,
                order_rows=24,
                product_rows=4,
                tuples_per_page=4,
                seed=7,
            )
        )

    def test_generator_includes_each_join_algorithm(self) -> None:
        catalog = Catalog(self.database)
        catalog.analyze()
        plans = PlanGenerator(self.database, catalog, buffer_frames=4).generate(
            parse_sql(SQL)
        )
        descriptions = [plan.describe() for plan in plans]

        self.assertTrue(any("HashJoin" in plan for plan in descriptions))
        self.assertTrue(any("NestedLoopJoin" in plan for plan in descriptions))
        self.assertTrue(any("BlockNestedLoopJoin" in plan for plan in descriptions))
        self.assertTrue(any("SortMergeJoin" in plan for plan in descriptions))

    def test_all_candidates_return_the_same_rows(self) -> None:
        report = evaluate_query(self.database, SQL, buffer_frames=4)

        self.assertGreater(len(report.candidates), 1)
        self.assertGreaterEqual(report.regret, 0)
        self.assertEqual(
            report.chosen.evaluation.rows,
            report.actual_best.evaluation.rows,
        )

    def test_sequential_scan_reads_every_table_page(self) -> None:
        query = parse_sql("SELECT c.id FROM customers c WHERE c.tier = 'gold'")
        catalog = Catalog(self.database)
        catalog.analyze()
        plans = PlanGenerator(self.database, catalog, buffer_frames=4).generate(query)
        sequential_plan = next(
            plan for plan in plans if "SeqScan" in plan.describe()
        )

        evaluation = PlanEvaluator(self.database, buffer_frames=4).evaluate(
            sequential_plan
        )
        scan_metric = next(
            metric for metric in evaluation.metrics if metric.label.startswith("SeqScan")
        )
        self.assertEqual(
            scan_metric.buffer_misses,
            self.database.tables["customers"].page_count,
        )

    def test_three_table_planning_skips_disconnected_intermediate_orders(self) -> None:
        sql = (
            "SELECT c.region, SUM(o.amount) "
            "FROM customers c "
            "JOIN orders o ON c.id = o.customer_id "
            "JOIN products p ON o.product_id = p.id "
            "WHERE p.category = 'books' GROUP BY c.region"
        )
        report = evaluate_query(
            self.database,
            sql,
            buffer_frames=4,
            execute_all=False,
        )

        self.assertEqual(len(report.candidates), 1)
        self.assertIn("products", report.chosen.plan.describe())


if __name__ == "__main__":
    unittest.main()
