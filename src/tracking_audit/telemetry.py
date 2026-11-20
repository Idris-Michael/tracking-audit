"""JSON logs (structlog) and OpenTelemetry traces. Tracing is on only when an OTLP endpoint is set,
for example the Jaeger container in docker-compose."""

from __future__ import annotations

import logging

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from structlog.types import EventDict, WrappedLogger


def _add_trace_ids(_logger: WrappedLogger, _method: str, event: EventDict) -> EventDict:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event["trace_id"] = f"{ctx.trace_id:032x}"
    return event


def setup(service_name: str, otlp_endpoint: str | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_trace_ids,
            structlog.processors.JSONRenderer(),
        ],
    )
    if otlp_endpoint:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(provider)
