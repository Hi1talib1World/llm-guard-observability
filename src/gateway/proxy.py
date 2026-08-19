import sys
import time
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.middleware.guardrail_filter import GuardrailFilter
from src.telemetry.tracer import MultiAgentTracer
from src.evaluator.judge import LLMJudgeEvaluator
from src.harness.resilience_circuit_breaker import HighScaleCircuitBreaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("LLMGuard.Proxy")

guardrail_filter = GuardrailFilter()
tracer = MultiAgentTracer()
judge_evaluator = LLMJudgeEvaluator(sample_rate=0.10)
circuit_breaker = HighScaleCircuitBreaker(window_size=100, error_threshold_percent=2.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(judge_evaluator.start_worker())
    logger.info("LLM Guard & Observability Gateway Proxy server running.")
    yield
    judge_evaluator.is_running = False
    worker_task.cancel()

app = FastAPI(
    title="LLM Guard & Observability Harness 🛡️📊",
    version="1.0.0",
    lifespan=lifespan
)

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    t_start = time.time()
    tenant_id = request.headers.get("X-Tenant-ID", "tenant-default")
    body = await request.json()

    messages = body.get("messages", [])
    model = body.get("model", "gpt-4o")
    context = body.get("context")

    prompt = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            prompt = m.get("content", "")
            break

    trace_id, traceparent = tracer.start_trace(tenant_id=tenant_id, provider="openai", model=model)

    # Ingress Guardrail
    guard_span = tracer.create_span(trace_id, name="GuardrailIngress", kind="GUARDRAIL")
    is_valid, sanitized_prompt, pii_map, ing_ms, err = guardrail_filter.filter_ingress(prompt)

    if not is_valid:
        tracer.end_span(trace_id, guard_span, status="ERROR", error=err)
        tracer.finalize_trace(trace_id, c_in=len(prompt.split()), c_out=0, ttft_ms=ing_ms, total_latency_ms=ing_ms)
        return JSONResponse(
            status_code=400,
            content={"error": "Security Policy Violation", "details": err, "trace_id": trace_id},
            headers={"traceparent": traceparent}
        )

    tracer.end_span(trace_id, guard_span, status="OK")

    # Circuit Breaker Route
    route_type, cached = circuit_breaker.get_route(sanitized_prompt)
    provider_used = "openai"

    if route_type == "CACHE" and cached:
        raw_response = cached
        provider_used = "local_semantic_cache"
    else:
        provider_used = "openai" if route_type == "PRIMARY" else "anthropic"
        llm_span = tracer.create_span(trace_id, name=f"LLMInference:{provider_used}", kind="LLM")
        
        simulated_success = not (body.get("induce_failure", False))
        circuit_breaker.record_request(simulated_success)

        if not simulated_success:
            provider_used = "anthropic"
            raw_response = f"[{provider_used}/claude-3-5-sonnet] Fallback response for: {sanitized_prompt[:30]}..."
            tracer.end_span(trace_id, llm_span, status="OK")
        else:
            raw_response = f"[{provider_used}/{model}] Processed response for: {sanitized_prompt[:30]}..."
            tracer.end_span(trace_id, llm_span, status="OK")

        circuit_breaker.semantic_cache.put(sanitized_prompt, raw_response)

    # Egress Guardrail
    egress_span = tracer.create_span(trace_id, name="GuardrailEgress", kind="GUARDRAIL")
    is_out_valid, final_text, eg_ms, out_err = guardrail_filter.filter_egress(raw_response, pii_map)

    if not is_out_valid:
        tracer.end_span(trace_id, egress_span, status="ERROR", error=out_err)
        return JSONResponse(
            status_code=422,
            content={"error": "Output Policy Violation", "details": out_err, "trace_id": trace_id},
            headers={"traceparent": traceparent}
        )

    tracer.end_span(trace_id, egress_span, status="OK")

    total_ms = (time.time() - t_start) * 1000
    c_in = len(prompt.split()) * 2
    c_out = len(final_text.split()) * 2
    tracer.finalize_trace(trace_id, c_in=c_in, c_out=c_out, ttft_ms=ing_ms + 10.0, total_latency_ms=total_ms)

    await judge_evaluator.enqueue(trace_id, prompt, final_text, context)

    return JSONResponse(
        content={
            "id": f"chatcmpl-{trace_id[:8]}",
            "choices": [{"message": {"role": "assistant", "content": final_text}}],
            "provider_used": provider_used,
            "guardrail_ingress_ms": round(ing_ms, 2),
            "guardrail_egress_ms": round(eg_ms, 2),
            "total_latency_ms": round(total_ms, 2)
        },
        headers={"traceparent": traceparent}
    )

@app.get("/v1/telemetry/spans")
async def get_telemetry_spans():
    return {"traces": [t.__dict__ for t in tracer.traces.values()]}

@app.get("/v1/evaluations/stats")
async def get_evaluations_stats():
    return judge_evaluator.get_stats()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.gateway.proxy:app", host="0.0.0.0", port=8000, reload=False)
