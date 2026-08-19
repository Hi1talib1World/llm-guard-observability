import sys
import unittest
import asyncio
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.middleware.guardrail_filter import GuardrailFilter
from src.telemetry.tracer import MultiAgentTracer
from src.evaluator.judge import LLMJudgeEvaluator
from src.harness.resilience_circuit_breaker import HighScaleCircuitBreaker
from src.gateway.proxy import app

class TestHighScaleAIGateway(unittest.TestCase):
    def setUp(self):
        self.guard = GuardrailFilter()
        self.tracer = MultiAgentTracer()
        self.judge = LLMJudgeEvaluator(sample_rate=1.0)
        self.cb = HighScaleCircuitBreaker(window_size=10, error_threshold_percent=2.0)
        self.client = TestClient(app)

    def test_guardrail_latency_benchmark_under_15ms(self):
        prompt = "Contact security officer at sec.lead@enterprise.com or 555-987-6543 regarding risk assessment."
        is_valid, sanitized, pii_map, ing_ms, _ = self.guard.filter_ingress(prompt)
        self.assertTrue(is_valid)
        self.assertLess(ing_ms, 15.0, f"Ingress guardrail latency ({ing_ms:.2f}ms) exceeded 15ms target budget!")

        is_eg_valid, restored, eg_ms, _ = self.guard.filter_egress("Redacted response [EMAIL_1]", pii_map)
        self.assertTrue(is_eg_valid)
        self.assertLess(eg_ms, 15.0, f"Egress guardrail latency ({eg_ms:.2f}ms) exceeded 15ms target budget!")

    def test_multi_agent_otel_spans(self):
        trace_id, traceparent = self.tracer.start_trace(tenant_id="tenant-99", provider="openai", model="gpt-4o")
        self.assertTrue(traceparent.startswith("00-"))

        subagent_span = self.tracer.create_span(trace_id, name="ResearcherSubAgent", kind="AGENT")
        tool_span = self.tracer.create_span(trace_id, name="ExecuteVectorSearch", kind="TOOL", parent_span_id=subagent_span)
        
        self.tracer.end_span(trace_id, tool_span, status="OK")
        self.tracer.end_span(trace_id, subagent_span, status="OK")

        ctx = self.tracer.finalize_trace(trace_id, c_in=150, c_out=75, ttft_ms=45.0, total_latency_ms=300.0)
        self.assertEqual(len(ctx.spans), 3)
        self.assertEqual(ctx.spans[2].parent_span_id, subagent_span)

    def test_realtime_judge_structured_json(self):
        async def run_eval():
            await self.judge.enqueue(
                trace_id="test-trace-101",
                prompt="Explain quantum entanglement physics.",
                response="Quantum entanglement occurs when particles remain connected.",
                context="Quantum physics describes entanglement between particle states."
            )
            item = await self.judge.queue.get()
            await self.judge._evaluate(item)

        asyncio.run(run_eval())
        stats = self.judge.get_stats()
        self.assertEqual(stats["samples"], 1)
        self.assertGreater(stats["avg_faithfulness"], 0.0)
        self.assertGreater(stats["avg_relevance"], 0.0)

    def test_circuit_breaker_2_percent_error_threshold_failover(self):
        for _ in range(97):
            self.cb.record_request(success=True)
        for _ in range(3):
            self.cb.record_request(success=False)

        route, cached = self.cb.get_route("What is cloud security?")
        self.assertEqual(route, "SECONDARY")

    def test_gateway_api_endpoint_integration(self):
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Provide corporate cloud migration guidance."}]
        }
        res = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("traceparent", res.headers)
        self.assertLess(data["guardrail_ingress_ms"], 15.0)

if __name__ == "__main__":
    unittest.main()
