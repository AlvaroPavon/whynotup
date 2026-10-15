"""Best-effort, deterministic report redaction. Never a certification."""
from __future__ import annotations

import re
from typing import Any, Iterable


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self.secrets = sorted({s for s in secrets if s}, key=lambda s: (-len(s), s))
        self.replacements: dict[str, str] = {}

    def _mask(self, value: str) -> str:
        if value.startswith("[REDACTED_"):
            return value
        if value not in self.replacements:
            self.replacements[value] = f"[REDACTED_{len(self.replacements) + 1}]"
        return self.replacements[value]

    def text(self, text: str) -> str:
        text = re.sub(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", lambda m: self._mask(m.group()), text, flags=re.S)
        for secret in self.secrets:
            pattern = re.escape(secret)
            if len(secret) <= 3:
                pattern = r"(?<![A-Za-z0-9_])" + pattern + r"(?![A-Za-z0-9_])"
            text = re.sub(pattern, lambda m: self._mask(secret), text)
        text = re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)([^/\s@]+)@", lambda m: m.group(1) + self._mask(m.group(2)) + "@", text)
        text = re.sub(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/-]+)", lambda m: m.group(1) + self._mask(m.group(2)), text)
        text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{12,})\b", lambda m: self._mask(m.group()), text)
        text = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", lambda m: self._mask(m.group()), text)
        assignment = r'''(?i)(\b[A-Za-z0-9_-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|authorization)[A-Za-z0-9_-]*["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;]+)'''
        text = re.sub(assignment, lambda m: m.group(1) + self._mask(m.group(2)), text)
        return text

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [self.sanitize(item) for item in value]
        if isinstance(value, dict):
            return {self.text(str(key)): self.sanitize(item) for key, item in value.items()}
        return value
