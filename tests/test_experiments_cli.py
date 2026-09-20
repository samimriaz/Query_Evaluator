import io
import unittest
from contextlib import redirect_stdout

from querylab.cli import main
from querylab.experiments import render_experiments, run_layout_experiment


class ExperimentAndCliTests(unittest.TestCase):
    def test_layout_experiment_covers_two_layouts_and_filter_modes(self) -> None:
        results = run_layout_experiment(
            customer_rows=8,
            order_rows=24,
            product_rows=4,
            buffer_frames=4,
        )

        self.assertEqual(len(results), 4)
        rendered = render_experiments(results)
        self.assertIn("customers-wide/filtered", rendered)
        self.assertIn("orders-wide/unfiltered", rendered)

    def test_cli_can_evaluate_only_the_chosen_plan(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "evaluate",
                    "--customers",
                    "8",
                    "--orders",
                    "24",
                    "--products",
                    "4",
                    "--chosen-only",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Chosen plan:", output.getvalue())
        self.assertIn("Regret:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
