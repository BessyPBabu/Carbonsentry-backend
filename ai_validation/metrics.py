import logging
from prometheus_client import Counter, Histogram, Gauge, REGISTRY

logger = logging.getLogger(__name__)


def _safe_metric(factory, *args, **kwargs):
    name = args[0]
    try:
        return factory(*args, **kwargs)
    except ValueError:
        return REGISTRY._names_to_collectors.get(name)


validation_counter = _safe_metric(
    Counter,
    "carbonsentry_validations_total",
    "Total document validations by outcome",
    ["status"],
)

validation_duration = _safe_metric(
    Histogram,
    "carbonsentry_validation_duration_seconds",
    "End-to-end validation pipeline duration",
    buckets=[1, 2, 5, 10, 20, 30, 60, 120, 300],
)

gemini_call_counter = _safe_metric(
    Counter,
    "carbonsentry_gemini_calls_total",
    "Gemini API calls per step and outcome",
    ["step", "success"],
)

confidence_histogram = _safe_metric(
    Histogram,
    "carbonsentry_confidence_score",
    "AI document confidence score distribution",
    buckets=[10, 20, 30, 40, 50, 55, 60, 70, 80, 90, 95, 100],
)

active_validations = _safe_metric(
    Gauge,
    "carbonsentry_active_validations",
    "Validations currently being processed",
)

manual_review_queue_size = _safe_metric(
    Gauge,
    "carbonsentry_manual_review_queue_size",
    "Pending items in the manual review queue",
)