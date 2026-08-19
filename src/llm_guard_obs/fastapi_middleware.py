import time
import json
import logging
from typing import Callable, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.middleware.guardrail_filter import GuardrailFilter
from src.telemetry.tracer import MultiAgentTracer
from src.evaluator.judge import LLMJudgeEvaluator
from src.llm_guard_obs.metrics import PrometheusMetricsExporter

logger = logging.getLogger("LLMGuardObs.FastAPIMiddleware")

async def _async_body_generator(chunks):
    for chunk in chunks:
        yield chunk

class LLMGuardMiddleware(BaseHTTPMiddleware):
    """
    Drop-in FastAPI / ASGI Middleware for llm-guard-obs.
    Usage:
        from fastapi import FastAPI
        from llm_guard_obs import LLMGuardMiddleware

        app = FastAPI()
        app.add_middleware(LLMGuardMiddleware)
    """

    def __init__(
        self,
        app,
        guardrail_filter: Optional[GuardrailFilter] = None,
        tracer: Optional[MultiAgentTracer] = None,
        metrics: Optional[PrometheusMetricsExporter] = None
    ):
        super().__init__(app)
        self.guardrail_filter = guardrail_filter or GuardrailFilter()
        self.tracer = tracer or MultiAgentTracer()
        self.metrics = metrics or PrometheusMetricsExporter()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith("/v1/chat/completions") or request.method != "POST":
            return await call_next(request)

        start_time = time.time()
        tenant_id = request.headers.get("X-Tenant-ID", "tenant-default")
        body = await request.json()
        messages = body.get("messages", [])
        model = body.get("model", "gpt-4o")

        prompt = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                prompt = m.get("content", "")
                break

        trace_id, traceparent = self.tracer.start_trace(tenant_id=tenant_id, provider="openai", model=model)

        # Ingress Guardrail Check
        guard_span = self.tracer.create_span(trace_id, name="GuardrailIngress", kind="GUARDRAIL")
        is_valid, sanitized_prompt, pii_map, ing_ms, err = self.guardrail_filter.filter_ingress(prompt)

        if not is_valid:
            self.tracer.end_span(trace_id, guard_span, status="ERROR", error=err)
            self.tracer.finalize_trace(trace_id, c_in=len(prompt.split()), c_out=0, ttft_ms=ing_ms, total_latency_ms=ing_ms)
            self.metrics.record_request(ing_ms, prompt_tokens=len(prompt.split()), completion_tokens=0, blocked_injection=True)
            return JSONResponse(
                status_code=400,
                content={"error": "Security Policy Violation", "details": err, "trace_id": trace_id},
                headers={"traceparent": traceparent}
            )

        self.tracer.end_span(trace_id, guard_span, status="OK")

        # LLM Call Forwarding
        llm_span = self.tracer.create_span(trace_id, name="LLMCompletion", kind="LLM")
        response = await call_next(request)
        self.tracer.end_span(trace_id, llm_span, status="OK")

        # Egress Processing
        resp_body = []
        async for chunk in response.body_iterator:
            resp_body.append(chunk)

        response.body_iterator = _async_body_generator(resp_body)
        raw_text = b"".join(resp_body).decode()

        try:
            resp_json = json.loads(raw_text)
            gen_text = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception:
            gen_text = raw_text

        is_out_valid, final_text, eg_ms, out_err = self.guardrail_filter.filter_egress(gen_text, pii_map)
        total_ms = (time.time() - start_time) * 1000

        c_in = len(prompt.split()) * 2
        c_out = max(1, len(final_text.split()) * 2)
        self.tracer.finalize_trace(trace_id, c_in=c_in, c_out=c_out, ttft_ms=ing_ms + 5.0, total_latency_ms=total_ms)
        self.metrics.record_request(total_ms, prompt_tokens=c_in, completion_tokens=c_out, pii_count=len(pii_map))

        response.headers["traceparent"] = traceparent
        return response
