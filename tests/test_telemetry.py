import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.telemetry.tracer import MultiAgentTracer
from src.telemetry.metrics import PrometheusTelemetryExporter
from src.harness.resilience_circuit_breaker import HighScaleCircuitBreaker

class TestTelemetryHarness(unittest.TestCase):
    def setUp(self):
        self.tracer = MultiAgentTracer()
        self.exporter = PrometheusTelemetryExporter()
        self.cb = HighScaleCircuitBreaker(window_size=10, error_threshold_percent=2.0)

    def test_opentelemetry_w3c_traceparent_propagation(self):
        trace_id, traceparent = self.tracer.start_trace(tenant_id="tenant-sec-01", provider="openai", model="gpt-4o")
        
        # W3C traceparent format check: 00-{32 hex trace_id}-{16 hex span_id}-01
        parts = traceparent.split("-")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0], "00")
        self.assertEqual(parts[1], trace_id)
        self.assertEqual(parts[3], "01")

    def test_multi_agent_parent_child_spans(self):
        trace_id, _ = self.tracer.start_trace(tenant_id="tenant-sec-01", provider="openai", model="gpt-4o")
        
        parent_agent_span = self.tracer.create_span(trace_id, name="RootOrchestrator", kind="AGENT")
        sub_agent_span = self.tracer.create_span(trace_id, name="SecuritySubAgent", kind="AGENT", parent_span_id=parent_agent_span)
        tool_span = self.tracer.create_span(trace_id, name="QueryVectorDB", kind="TOOL", parent_span_id=sub_agent_span)

        self.tracer.end_span(trace_id, tool_span, status="OK")
        self.tracer.end_span(trace_id, sub_agent_span, status="OK")
        self.tracer.end_span(trace_id, parent_agent_span, status="OK")

        ctx = self.tracer.finalize_trace(trace_id, c_in=250, c_out=120, ttft_ms=50.0, total_latency_ms=450.0)
        self.assertEqual(len(ctx.spans), 4)  # Root + ParentAgent + SubAgent + Tool
        self.assertEqual(ctx.spans[3].parent_span_id, sub_agent_span)

    def test_prometheus_metric_emissions_and_counters(self):
        self.exporter.record_request(
            latency_ms=150.0,
            ttft_ms=30.0,
            prompt_tokens=200,
            completion_tokens=80,
            blocked_injection=True,
            attack_vector="QuantizedONNXVector",
            pii_count=3
        )

        summary = self.exporter.get_metrics_summary()
        counters = summary["counters"]
        histograms = summary["histograms"]

        self.assertEqual(counters["llm_guard_requests_total"], 1)
        self.assertEqual(counters["llm_guard_prompt_tokens_total"], 200)
        self.assertEqual(counters["llm_guard_completion_tokens_total"], 80)
        self.assertEqual(counters["llm_guard_attacks_blocked_total"], 1)
        self.assertEqual(counters["llm_guard_pii_redacted_total"], 3)
        self.assertEqual(histograms["llm_guard_ttft_seconds_avg"], 0.030)
        self.assertEqual(summary["attack_vectors_breakdown"]["QuantizedONNXVector"], 1)

    def test_error_budget_and_circuit_breaker_tripping(self):
        for _ in range(97):
            self.cb.record_request(success=True)
        for _ in range(3):
            self.cb.record_request(success=False)

        route, cached = self.cb.get_route("What is zero trust security?")
        self.assertEqual(route, "SECONDARY")

if __name__ == "__main__":
    unittest.main()
