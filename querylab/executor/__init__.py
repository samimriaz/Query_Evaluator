"""Physical plan execution and instrumentation."""

from querylab.executor.evaluator import EvaluationResult, PlanEvaluator
from querylab.executor.metrics import OperatorMetrics

__all__ = ["EvaluationResult", "OperatorMetrics", "PlanEvaluator"]

