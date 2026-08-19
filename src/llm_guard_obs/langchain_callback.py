import time
import logging
from typing import Dict, Any, List, Optional
from src.middleware.guardrail_filter import GuardrailFilter
from src.telemetry.tracer import MultiAgentTracer
from src.llm_guard_obs.metrics import PrometheusMetricsExporter

logger = logging.getLogger("LLMGuardObs.LangChainCallback")

class LLMGuardLangChainCallback:
    """
    LangChain BaseCallbackHandler compatible integration for llm-guard-obs.
    Usage:
        from llm_guard_obs import LLMGuardLangChainCallback
        from langchain_openai import ChatOpenAI

        handler = LLMGuardLangChainCallback()
        llm = ChatOpenAI(callbacks=[handler])
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
        self.active_runs: Dict[str, Dict[str, Any]] = {}

    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> Any:
        run_id = str(kwargs.get("run_id", "langchain-run"))
        prompt = prompts[0] if prompts else ""
        t0 = time.time()

        # Ingress Guardrail Filter
        is_valid, sanitized, pii_map, ing_ms, err = self.guardrail_filter.filter_ingress(prompt)
        if not is_valid:
            logger.warning(f"[LANGCHAIN GUARD BLOCK] {err}")
            raise ValueError(f"LLMGuard Security Violation: {err}")

        trace_id, traceparent = self.tracer.start_trace(tenant_id="tenant-langchain", provider="openai", model="gpt-4o")
        span_id = self.tracer.create_span(trace_id, name="LangChainLLMRun", kind="LLM")

        self.active_runs[run_id] = {
            "trace_id": trace_id,
            "span_id": span_id,
            "start_time": t0,
            "prompt": prompt,
            "pii_map": pii_map,
            "ing_ms": ing_ms
        }
        return traceparent

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id", "langchain-run"))
        run_info = self.active_runs.pop(run_id, None)
        if not run_info:
            return

        total_ms = (time.time() - run_info["start_time"]) * 1000
        raw_text = str(response)
        
        # Egress Guardrail Filter
        is_out_valid, final_text, eg_ms, out_err = self.guardrail_filter.filter_egress(raw_text, run_info["pii_map"])
        if not is_out_valid:
            logger.warning(f"[LANGCHAIN OUTPUT BLOCK] {out_err}")
            self.tracer.end_span(run_info["trace_id"], run_info["span_id"], status="ERROR", error=out_err)
            raise ValueError(f"LLMGuard Safety Violation: {out_err}")

        self.tracer.end_span(run_info["trace_id"], run_info["span_id"], status="OK")
        c_in = len(run_info["prompt"].split()) * 2
        c_out = max(1, len(final_text.split()) * 2)

        self.tracer.finalize_trace(run_info["trace_id"], c_in=c_in, c_out=c_out, ttft_ms=run_info["ing_ms"] + 10.0, total_latency_ms=total_ms)
        self.metrics.record_request(total_ms, prompt_tokens=c_in, completion_tokens=c_out)

    def on_llm_error(self, error: Exception, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id", "langchain-run"))
        run_info = self.active_runs.pop(run_id, None)
        if run_info:
            self.tracer.end_span(run_info["trace_id"], run_info["span_id"], status="ERROR", error=str(error))
