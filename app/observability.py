"""Lightweight observability, latency tracking, and OpenTelemetry span helpers."""

import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

try:
    from opentelemetry import trace

    _TRACER = trace.get_tracer("policydesk")
except Exception:
    _TRACER = None


def new_request_id() -> str:
    """Generate a compact correlation ID for request tracing."""
    return f"req_{uuid.uuid4().hex[:12]}"


def estimate_tokens(text: str) -> int:
    """Estimate token count using a lightweight character/word heuristic."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@contextmanager
def trace_node(node_name: str, state: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Measure node latency and emit an optional OpenTelemetry span."""
    start = time.perf_counter()
    metrics = dict(state.get("metrics") or {})
    latencies = dict(metrics.get("node_latencies_ms") or {})

    if _TRACER is not None:
        with _TRACER.start_as_current_span(f"policydesk.node.{node_name}"):
            yield metrics
    else:
        yield metrics

    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 3)
    latencies[node_name] = elapsed_ms
    metrics["node_latencies_ms"] = latencies
    metrics["total_latency_ms"] = round(sum(latencies.values()), 3)
