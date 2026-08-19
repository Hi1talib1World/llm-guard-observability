import time
import logging
from typing import Dict, Any, Optional
from src.middleware.guardrail_filter import GuardrailFilter
from src.telemetry.tracer import MultiAgentTracer
from src.llm_guard_obs.metrics import PrometheusMetricsExporter

logger = logging.getLogger("LLMGuardObs.LlamaIndexHandler")

class LLMGuardLlamaIndexHandler:
    """
    LlamaIndex BaseEventHandler / Callback integration for llm-guard-obs.
    Usage:
        from llm_guard_obs import LLMGuardLlamaIndexHandler
        from llama_index.core import Settings

        handler = LLMGuardLlamaIndexHandler()
        Settings.callback_manager.add_handler(handler)
    """

    def __init__(
        self,
        guardrail_filter: Optional[GuardrailFilter] = None,
        tracer: Optional[MultiAgentTracer] = None,
        metrics: Optional[PrometheusMetricsExporter] = None
    ):
        self.guardrail_filter = guardrail_filter or GuardrailFilter()
        self.tracer = tracer or MultiAgentTracer()
        self.metrics = metrics or PrometheusMetricsExporter()
        self.active_events: Dict[str, Dict[str, Any]] = {}

    def on_event_start(self, event_type: str, payload: Optional[Dict[str, Any]] = None, event_id: str = "llamaindex-event") -> str:
        prompt = ""
        if payload and "query_str" in payload:
            prompt = payload["query_str"]
        elif payload and "messages" in payload:
            prompt = str(payload["messages"][-1])

        t0 = time.time()
        is_valid, sanitized, pii_map, ing_ms, err = self.guardrail_filter.filter_ingress(prompt)
        if not is_valid:
            logger.warning(f"[LLAMAINDEX GUARD BLOCK] {err}")
            raise ValueError(f"LLMGuard Security Violation: {err}")

        trace_id, traceparent = self.tracer.start_trace(tenant_id="tenant-llamaindex", provider="openai", model="gpt-4o")
        span_id = self.tracer.create_span(trace_id, name=f"LlamaIndexEvent:{event_type}", kind="RAG")

        self.active_events[event_id] = {
            "trace_id": trace_id,
            "span_id": span_id,
            "start_time": t0,
            "prompt": prompt,
            "pii_map": pii_map,
            "ing_ms": ing_ms
        }
        return event_id

    def on_event_end(self, event_id: str, payload: Optional[Dict[str, Any]] = None) -> None:
        event_info = self.active_events.pop(event_id, None)
        if not event_info:
            return

        total_ms = (time.time() - event_info["start_time"]) * 1000
        response_text = str(payload.get("response")) if payload and "response" in payload else "Completed"

        is_out_valid, final_text, eg_ms, out_err = self.guardrail_filter.filter_egress(response_text, event_info["pii_map"])
        if not is_out_valid:
            self.tracer.end_span(event_info["trace_id"], event_info["span_id"], status="ERROR", error=out_err)
            raise ValueError(f"LLMGuard Safety Violation: {out_err}")

        self.tracer.end_span(event_info["trace_id"], event_info["span_id"], status="OK")
        c_in = len(event_info["prompt"].split()) * 2
        c_out = max(1, len(final_text.split()) * 2)

        self.tracer.finalize_trace(event_info["trace_id"], c_in=c_in, c_out=c_out, ttft_ms=event_info["ing_ms"] + 5.0, total_latency_ms=total_ms)
        self.metrics.record_request(total_ms, prompt_tokens=c_in, completion_tokens=c_out)
