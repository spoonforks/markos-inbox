from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonStore:
    def __init__(self, path: Path, default_value: list[Any] | dict[str, Any]) -> None:
        self.path = path
        self.default_value = default_value
        self._lock = Lock()
        self._ensure_exists()

    def _ensure_exists(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", encoding="utf-8") as file:
                json.dump(self.default_value, file, indent=2)

    def read(self) -> Any:
        with self._lock:
            with self.path.open("r", encoding="utf-8") as file:
                return json.load(file)

    def write(self, value: Any) -> None:
        with self._lock:
            with self.path.open("w", encoding="utf-8") as file:
                json.dump(value, file, indent=2, ensure_ascii=False)


class AssistantDataStore:
    def __init__(self, data_dir: Path, max_memories: int = 100) -> None:
        self.data_dir = data_dir
        self.max_memories = max_memories
        self.memory_store = JsonStore(data_dir / "memory.json", [])
        self.conversation_log_store = JsonStore(data_dir / "conversation_log.json", [])

    def list_memories(self) -> list[dict[str, Any]]:
        data = self.memory_store.read()
        return data if isinstance(data, list) else []

    def append_memory(self, fact: str, source_text: str) -> dict[str, Any]:
        memories = self.list_memories()
        record = {
            "id": str(uuid4()),
            "fact": fact.strip(),
            "source_text": source_text.strip(),
            "created_at": utc_now_iso(),
        }
        memories.append(record)
        memories = memories[-self.max_memories :]
        self.memory_store.write(memories)
        return record

    def append_exchange(
        self,
        *,
        transcript: str,
        reply_text: str,
        memory_saved: str | None,
    ) -> None:
        entries = self.conversation_log_store.read()
        if not isinstance(entries, list):
            entries = []

        entries.append(
            {
                "id": str(uuid4()),
                "timestamp": utc_now_iso(),
                "transcript": transcript.strip(),
                "reply_text": reply_text.strip(),
                "memory_saved": memory_saved,
            }
        )
        self.conversation_log_store.write(entries[-250:])
