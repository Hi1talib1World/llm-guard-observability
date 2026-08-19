import time
import logging
from collections import deque
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("LLMGuard.CircuitBreaker")

class SemanticCache:
    """In-memory Vector Semantic Cache for low-latency emergency fallback."""

    def __init__(self, distance_threshold: float = 0.08):
        self.distance_threshold = distance_threshold
        self.cache: Dict[str, str] = {}

    def _simple_vector_sim(self, s1: str, s2: str) -> float:
        w1 = set(s1.lower().split())
        w2 = set(s2.lower().split())
        if not w1 or not w2:
            return 1.0
        jaccard = len(w1.intersection(w2)) / len(w1.union(w2))
        return 1.0 - jaccard

    def get(self, query: str) -> Optional[str]:
        for cached_q, cached_resp in self.cache.items():
            dist = self._simple_vector_sim(query, cached_q)
            if dist <= self.distance_threshold:
                logger.info(f"[SEMANTIC CACHE HIT] Distance={dist:.4f}")
                return cached_resp
        return None

    def put(self, query: str, response: str):
        self.cache[query] = response


class HighScaleCircuitBreaker:
    """Sliding window Circuit Breaker with 2% Error Threshold."""

    def __init__(self, window_size: int = 100, error_threshold_percent: float = 2.0, cool_off_seconds: float = 15.0):
        self.window_size = window_size
        self.error_threshold_percent = error_threshold_percent
        self.cool_off_seconds = cool_off_seconds
        self.rolling_window: deque = deque(maxlen=window_size)
        self.state = "CLOSED"
        self.last_state_change = time.time()
        self.semantic_cache = SemanticCache()

    def record_request(self, success: bool):
        self.rolling_window.append(1 if success else 0)
        self._evaluate_state()

    def _evaluate_state(self):
        if len(self.rolling_window) < 10:
            return
        failures = self.rolling_window.count(0)
        total = len(self.rolling_window)
        error_rate = (failures / total) * 100.0

        if self.state == "CLOSED" and error_rate > self.error_threshold_percent:
            self.state = "OPEN"
            self.last_state_change = time.time()
            logger.warning(f"[CIRCUIT BREAKER OPEN] Error rate {error_rate:.2f}% > threshold {self.error_threshold_percent}%")
        elif self.state == "OPEN" and time.time() - self.last_state_change > self.cool_off_seconds:
            self.state = "HALF-OPEN"
        elif self.state == "HALF-OPEN" and error_rate <= self.error_threshold_percent:
            self.state = "CLOSED"

    def get_route(self, query: str) -> Tuple[str, Optional[str]]:
        self._evaluate_state()
        if self.state in ("CLOSED", "HALF-OPEN"):
            return "PRIMARY", None
        cached = self.semantic_cache.get(query)
        if cached:
            return "CACHE", cached
        return "SECONDARY", None
