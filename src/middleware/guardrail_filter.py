import re
import time
import logging
from typing import Tuple, Dict, Optional
from src.middleware.pii_redactor import PIIRedactor

logger = logging.getLogger("LLMGuard.Middleware")

class GuardrailFilter:
    """Sub-15ms Real-Time Guardrail Filter for Prompt Injections, System Leaks, and Toxicity."""

    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(all\s+)?(previous|above)\s+instructions", re.IGNORECASE),
        re.compile(r"disregard\s+(the\s+)?system\s+prompt", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.IGNORECASE),
        re.compile(r"dan\s+mode\s+enabled", re.IGNORECASE),
        re.compile(r"override\s+system\s+rules", re.IGNORECASE),
        re.compile(r"reveal\s+(your\s+)?system\s+prompt", re.IGNORECASE)
    ]

    LEAK_PHRASES = [
        "system prompt:",
        "internal instructions:",
        "my core programming dictates",
        "confidential system prompt"
    ]

    TOXIC_TERMS = ["malicious_payload", "exploit_code", "hate_speech_keyword"]

    def __init__(self, system_prompt: str = "You are an enterprise AI assistant."):
        self.system_prompt = system_prompt.lower()
        self.pii_redactor = PIIRedactor()

    def filter_ingress(self, prompt: str) -> Tuple[bool, str, Dict[str, str], float, Optional[str]]:
        """Scans input prompt for jailbreaks and performs PII redaction."""
        t0 = time.perf_counter()

        for pattern in self.INJECTION_PATTERNS:
            if pattern.search(prompt):
                latency_ms = (time.perf_counter() - t0) * 1000
                return False, prompt, {}, latency_ms, f"Prompt Injection Blocked: '{pattern.pattern}'"

        if prompt.count("```") > 4 or prompt.count("system:") > 2:
            latency_ms = (time.perf_counter() - t0) * 1000
            return False, prompt, {}, latency_ms, "Delimiter Exploitation Blocked"

        sanitized, pii_map = self.pii_redactor.redact(prompt)
        latency_ms = (time.perf_counter() - t0) * 1000
        return True, sanitized, pii_map, latency_ms, None

    def filter_egress(self, response_text: str, pii_map: Dict[str, str]) -> Tuple[bool, str, float, Optional[str]]:
        """Scans output response for system leaks, toxicity, and restores PII."""
        t0 = time.perf_counter()
        lowered = response_text.lower()

        if self.system_prompt and self.system_prompt in lowered:
            latency_ms = (time.perf_counter() - t0) * 1000
            return False, "", latency_ms, "System Prompt Leak Blocked"

        for kw in self.LEAK_PHRASES:
            if kw in lowered:
                latency_ms = (time.perf_counter() - t0) * 1000
                return False, "", latency_ms, f"System Prompt Leak Blocked: '{kw}'"

        for toxic in self.TOXIC_TERMS:
            if toxic in lowered:
                latency_ms = (time.perf_counter() - t0) * 1000
                return False, "", latency_ms, f"Toxic Output Blocked: '{toxic}'"

        restored = self.pii_redactor.restore(response_text, pii_map)
        latency_ms = (time.perf_counter() - t0) * 1000
        return True, restored, latency_ms, None
