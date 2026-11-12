"""Optional cross-run metrics for agent turns.

The collector is process-local and disabled by default. When enabled it powers
a Prometheus-compatible /metrics endpoint and, if the OpenTelemetry SDK happens
to be installed, mirrors the same counters into OTel instruments.
"""
from __future__ import annotations

import threading
from collections import Counter
from typing import Any

from app.config import settings

_LOCK = threading.Lock()
_COUNTERS: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
_DURATION_SECONDS = 0.0
_DURATION_COUNT = 0
_OTEL_INSTRUMENTS: dict[str, Any] | None = None


def _add(name: str, value: float, **labels: Any) -> None:
    key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
    _COUNTERS[key] += value


def reset() -> None:
    global _DURATION_SECONDS, _DURATION_COUNT, _OTEL_INSTRUMENTS
    with _LOCK:
        _COUNTERS.clear()
        _DURATION_SECONDS = 0.0
        _DURATION_COUNT = 0
        _OTEL_INSTRUMENTS = None


def _otel_instruments() -> dict[str, Any] | None:
    global _OTEL_INSTRUMENTS
    if _OTEL_INSTRUMENTS is not None:
        return _OTEL_INSTRUMENTS
    try:
        from opentelemetry import metrics as otel_metrics
    except Exception:  # noqa: BLE001 — OTel is optional
        _OTEL_INSTRUMENTS = {}
        return _OTEL_INSTRUMENTS
    meter = otel_metrics.get_meter(
        str(getattr(settings, "otel_service_name", "shopping-agent"))
    )
    _OTEL_INSTRUMENTS = {
        "turns": meter.create_counter("shopping_agent.turns"),
        "tools": meter.create_counter("shopping_agent.tool.calls"),
        "excluded": meter.create_counter("shopping_agent.inventory.excluded"),
        "tokens": meter.create_counter("shopping_agent.llm.tokens"),
    }
    return _OTEL_INSTRUMENTS


def record_turn(summary: dict[str, Any]) -> None:
    if not settings.metrics_enabled:
        return
    global _DURATION_SECONDS, _DURATION_COUNT
    status = str(summary.get("status") or "unknown")
    with _LOCK:
        _add("shopping_agent_turns_total", 1, status=status)
        _DURATION_SECONDS += float(summary.get("duration_ms") or 0) / 1000.0
        _DURATION_COUNT += 1
        llm = summary.get("llm") or {}
        _add("shopping_agent_llm_calls_total", float(llm.get("calls") or 0))
        _add(
            "shopping_agent_llm_internal_calls_total",
            float(llm.get("internal_calls") or 0),
        )
        _add(
            "shopping_agent_llm_internal_tokens_total",
            float(llm.get("internal_tokens") or 0),
        )
        for kind in ("prompt_tokens", "completion_tokens", "total_tokens"):
            _add(
                "shopping_agent_llm_tokens_total",
                float(llm.get(kind) or 0),
                kind=kind,
            )
        for key, count in (summary.get("tool_status") or {}).items():
            tool, _, tool_status = str(key).partition(":")
            _add(
                "shopping_agent_tool_calls_total",
                float(count or 0),
                tool=tool,
                status=tool_status or "unknown",
            )
        for reason, count in (
            (summary.get("inventory") or {}).get("excluded") or {}
        ).items():
            _add(
                "shopping_agent_excluded_total",
                float(count or 0),
                reason=str(reason),
            )
        for code, count in (summary.get("error_codes") or {}).items():
            _add(
                "shopping_agent_errors_total",
                float(count or 0),
                code=str(code),
            )
        _add(
            "shopping_agent_recommendations_total",
            float(summary.get("recommendations") or 0),
        )

    instruments = _otel_instruments() if getattr(settings, "otel_enabled", False) else {}
    if instruments:
        instruments["turns"].add(1, {"status": status})
        for key, count in (summary.get("tool_status") or {}).items():
            tool, _, tool_status = str(key).partition(":")
            instruments["tools"].add(
                int(count or 0),
                {"tool": tool, "status": tool_status or "unknown"},
            )
        for reason, count in (
            (summary.get("inventory") or {}).get("excluded") or {}
        ).items():
            instruments["excluded"].add(
                int(count or 0), {"reason": str(reason)}
            )


def render_prometheus() -> str:
    lines: list[str] = []
    with _LOCK:
        for (name, labels), value in sorted(_COUNTERS.items()):
            value_text = (
                str(int(value))
                if float(value).is_integer()
                else str(float(value))
            )
            if not labels:
                lines.append(f"{name} {value_text}")
                continue
            label_text = ",".join(f'{key}="{val}"' for key, val in labels)
            lines.append(f"{name}{{{label_text}}} {value_text}")
        lines.append(
            f"shopping_agent_turn_duration_seconds_sum {_DURATION_SECONDS:.3f}"
        )
        lines.append(
            f"shopping_agent_turn_duration_seconds_count {_DURATION_COUNT}"
        )
    return "\n".join(lines) + "\n"
