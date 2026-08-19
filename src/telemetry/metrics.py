import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("LLMGuard.Telemetry.Metrics")

class PrometheusTelemetryExporter:
    """Prometheus Counters & Histograms for Token Economy, TTFT, and Latency."""

    def __init__(self):
        # Prometheus Counters
        self.prompt_tokens_total: int = 0      # C_prompt
        self.completion_tokens_total: int = 0  # C_completion
        self.attacks_blocked_total: int = 0
        self.pii_redacted_total: int = 0
        self.requests_total: int = 0

        # Prometheus Histograms (Buckets in seconds)
        self.ttft_seconds: List[float] = []
        self.latency_seconds: List[float] = []
        self.blocked_attack_vectors: Dict[str, int] = {}

    def record_request(
        self,
        latency_ms: float,
        ttft_ms: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        blocked_injection: bool = False,
        attack_vector: Optional[str] = None,
        pii_count: int = 0
    ):
        """Records Prometheus metric counters and histograms."""
        self.requests_total += 1
        self.prompt_tokens_total += prompt_tokens
        self.completion_tokens_total += completion_tokens
        self.pii_redacted_total += pii_count

        if blocked_injection:
            self.attacks_blocked_total += 1
            vec = attack_vector or "GenericInjection"
            self.blocked_attack_vectors[vec] = self.blocked_attack_vectors.get(vec, 0) + 1

        self.ttft_seconds.append(ttft_ms / 1000.0)
        self.latency_seconds.append(latency_ms / 1000.0)

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Returns structured Prometheus counters and histograms summary."""
        lat_sec = sorted(self.latency_seconds)
        ttft_sec = sorted(self.ttft_seconds)
        n = len(lat_sec)

        p95_lat = lat_sec[int(n * 0.95)] if n > 0 else 0.0
        p99_lat = lat_sec[int(n * 0.99)] if n > 0 else 0.0
        avg_ttft = sum(ttft_sec) / n if n > 0 else 0.0

        return {
            "counters": {
                "llm_guard_requests_total": self.requests_total,
                "llm_guard_prompt_tokens_total": self.prompt_tokens_total,
                "llm_guard_completion_tokens_total": self.completion_tokens_total,
                "llm_guard_attacks_blocked_total": self.attacks_blocked_total,
                "llm_guard_pii_redacted_total": self.pii_redacted_total
            },
            "histograms": {
                "llm_guard_ttft_seconds_avg": round(avg_ttft, 4),
                "llm_guard_latency_p95_seconds": round(p95_lat, 4),
                "llm_guard_latency_p99_seconds": round(p99_lat, 4)
            },
            "attack_vectors_breakdown": self.blocked_attack_vectors
        }

    # Backward compatibility alias
    def record_event(self, *args, **kwargs):
        self.record_request(*args, **kwargs)

    def get_summary(self) -> Dict[str, Any]:
        summary = self.get_metrics_summary()
        c = summary["counters"]
        h = summary["histograms"]
        return {
            "llm_guard_requests_total": c["llm_guard_requests_total"],
            "llm_guard_prompt_tokens_total": c["llm_guard_prompt_tokens_total"],
            "llm_guard_completion_tokens_total": c["llm_guard_completion_tokens_total"],
            "llm_guard_injections_blocked_total": c["llm_guard_attacks_blocked_total"],
            "llm_guard_pii_redacted_total": c["llm_guard_pii_redacted_total"],
            "llm_guard_latency_p95_ms": round(h["llm_guard_latency_p95_seconds"] * 1000, 2),
            "llm_guard_latency_p99_ms": round(h["llm_guard_latency_p99_seconds"] * 1000, 2)
        }
