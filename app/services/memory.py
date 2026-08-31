from __future__ import annotations

import re
from typing import Iterable


EXPLICIT_MEMORY_PATTERNS = [
    re.compile(r"\bremember that (?P<fact>.+)", re.IGNORECASE),
    re.compile(r"\bremember this[:\s]+(?P<fact>.+)", re.IGNORECASE),
    re.compile(r"\bplease remember(?: that)? (?P<fact>.+)", re.IGNORECASE),
    re.compile(r"\bstore this fact[:\s]+(?P<fact>.+)", re.IGNORECASE),
    re.compile(r"\bsave this fact[:\s]+(?P<fact>.+)", re.IGNORECASE),
    re.compile(r"\bcommit this to memory[:\s]+(?P<fact>.+)", re.IGNORECASE),
]


def extract_explicit_memory(transcript: str) -> str | None:
    cleaned = transcript.strip().strip("\"'")
    if not cleaned:
        return None

    for pattern in EXPLICIT_MEMORY_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            fact = match.group("fact").strip(" .")
            return fact or None
    return None


def memory_lines(memories: Iterable[dict[str, str]], limit: int) -> list[str]:
    items = list(memories)[-limit:]
    return [f"- {item['fact']}" for item in items if item.get("fact")]
