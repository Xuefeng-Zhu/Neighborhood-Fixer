"""Allowlist-only OpenTelemetry export to structured CloudWatch stdout.

A custom exporter deliberately excludes prompts, tool arguments/results, images,
identity, arbitrary exception text and model reasoning from every span.
"""

import json
import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

ALLOWED = {"nf.operation", "nf.outcome", "nf.model_turns", "nf.tool_count"}


class SanitizedCloudWatchExporter(SpanExporter):
    def export(self, spans):
        for span in spans:
            attributes = {
                k: v for k, v in (span.attributes or {}).items() if k in ALLOWED
            }
            if not attributes:
                continue
            print(
                json.dumps(
                    {
                        "event": "agent_operation",
                        "trace_id": format(span.context.trace_id, "032x"),
                        "span_id": format(span.context.span_id, "016x"),
                        "duration_ms": round(
                            (span.end_time - span.start_time) / 1_000_000, 2
                        ),
                        "attributes": attributes,
                    }
                ),
                flush=True,
            )
        return SpanExportResult.SUCCESS


def configure():
    # Strands debug logging may include model text; do not enable it for residents.
    logging.getLogger("strands").setLevel(logging.CRITICAL)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SanitizedCloudWatchExporter()))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("neighborhood-fixer")
