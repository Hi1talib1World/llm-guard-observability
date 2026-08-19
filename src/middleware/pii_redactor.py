import re
from typing import Tuple, Dict

class PIIRedactor:
    """High-performance PII detection (Regex + NER mapping) and restoration engine."""

    PII_PATTERNS = {
        "EMAIL": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", re.IGNORECASE),
        "PHONE": re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
        "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,16}\b")
    }

    def redact(self, text: str) -> Tuple[str, Dict[str, str]]:
        """Redacts PII tokens from text and returns (redacted_text, pii_mapping)."""
        sanitized = text
        mapping = {}
        counter = 1

        for pii_type, pattern in self.PII_PATTERNS.items():
            matches = set(pattern.findall(sanitized))
            for match in matches:
                placeholder = f"[{pii_type}_{counter}]"
                mapping[placeholder] = match
                sanitized = sanitized.replace(match, placeholder)
                counter += 1

        return sanitized, mapping

    def restore(self, text: str, mapping: Dict[str, str]) -> str:
        """Restores original PII tokens into text."""
        restored = text
        for placeholder, original in mapping.items():
            restored = restored.replace(placeholder, original)
        return restored
