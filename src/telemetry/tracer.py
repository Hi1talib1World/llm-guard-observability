import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger("LLMGuard.Telemetry")

@dataclass
class OTelSpan:
    span_id: str
    parent_span_id: Optional[str]
    name: str
    kind: str
    start_time: float
    end_time: Optional[float] = None
    status: str = "OK"
    error: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

@dataclass
class TraceContext:
    trace_id: str
    tenant_id: str
    root_span_id: str
    provider: str
    model: str
    c_in: int = 0
    c_out: int = 0
    total_cost_usd: float = 0.0
    ttft_ms: float = 0.0
    tpot_ms: float = 0.0
    total_latency_ms: float = 0.0
    spans: List[OTelSpan] = field(default_factory=list)
    traceparent: str = ""

class MultiAgentTracer:
    """OpenTelemetry Tracer supporting W3C traceparent headers and multi-agent spans."""

    PRICING = {
        "openai/gpt-4o": (0.0025, 0.0100),
        "anthropic/claude-3-5-sonnet": (0.0030, 0.0150),
        "local/llama-3-8b": (0.0001, 0.0001)
    }

    def __init__(self):
        self.traces: Dict[str, TraceContext] = {}

    def start_trace(self, tenant_id: str, provider: str, model: str) -> Tuple[str, str]:
        trace_id = uuid.uuid4().hex
        root_span_id = uuid.uuid4().hex[:16]
        traceparent = f"00-{trace_id}-{root_span_id}-01"

        ctx = TraceContext(
            trace_id=trace_id,
            tenant_id=tenant_id,
            root_span_id=root_span_id,
            provider=provider,
            model=model,
            traceparent=traceparent
        )
        ctx.spans.append(OTelSpan(span_id=root_span_id, parent_span_id=None, name="RootAgent", kind="AGENT", start_time=time.time()))
        self.traces[trace_id] = ctx
        return trace_id, traceparent

    def create_span(self, trace_id: str, name: str, kind: str, parent_span_id: Optional[str] = None) -> str:
        ctx = self.traces.get(trace_id)
        if not ctx:
            return ""

        span_id = uuid.uuid4().hex[:16]
        parent = parent_span_id or ctx.root_span_id
        ctx.spans.append(OTelSpan(span_id=span_id, parent_span_id=parent, name=name, kind=kind, start_time=time.time()))
        return span_id

    def end_span(self, trace_id: str, span_id: str, status: str = "OK", error: Optional[str] = None):
        ctx = self.traces.get(trace_id)
        if not ctx:
            return
        for s in ctx.spans:
            if s.span_id == span_id:
                s.end_time = time.time()
                s.status = status
                s.error = error
                break

    def finalize_trace(self, trace_id: str, c_in: int, c_out: int, ttft_ms: float, total_latency_ms: float) -> TraceContext:
        ctx = self.traces.get(trace_id)
        if not ctx:
            return TraceContext(trace_id=trace_id, tenant_id="unknown", root_span_id="", provider="", model="")

        ctx.c_in = c_in
        ctx.c_out = c_out
        ctx.ttft_ms = round(ttft_ms, 2)
        ctx.total_latency_ms = round(total_latency_ms, 2)
        ctx.tpot_ms = round((total_latency_ms - ttft_ms) / max(1, c_out), 2)

        rates = self.PRICING.get(f"{ctx.provider}/{ctx.model}", (0.002, 0.005))
        ctx.total_cost_usd = round(((c_in / 1000.0) * rates[0]) + ((c_out / 1000.0) * rates[1]), 6)
        self.end_span(trace_id, ctx.root_span_id, status="OK")

        logger.info(
            f"[OTEL TRACE FINALIZE] TraceID={ctx.trace_id} | TraceParent={ctx.traceparent} | "
            f"Spans={len(ctx.spans)} | Cost=${ctx.total_cost_usd:.6f} | Latency={ctx.total_latency_ms}ms"
        )
        return ctx
