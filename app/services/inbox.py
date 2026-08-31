from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

import httpx

from app.services.config import AppSettings, REPO_ROOT, resolve_path


CATEGORIES = [
    "task", "goal", "idea", "inspiration", "reflection", "question",
    "memory", "project_note", "resource", "reminder", "other",
]
CLASSIFICATION_VERSION = "marko.inbox.classification.v1"
PROMPT_TEMPLATE = """Classify one inbox item and return JSON only.
Required keys: category, topic_tags, context_tags, confidence.
Allowed categories: task, goal, idea, inspiration, reflection, question,
memory, project_note, resource, reminder, other.
Use one to three short lowercase tags total. Confidence must be 0 to 1.
Do not add markdown or explanation."""


@dataclass(slots=True)
class Classification:
    category: str
    topic_tags: list[str]
    context_tags: list[str]
    confidence: float
    classification_model: str
    classification_version: str = CLASSIFICATION_VERSION


@dataclass(slots=True)
class InboxItem:
    id: str
    source: str
    captured_at: str
    received_at: str
    original_text: str
    status: str
    category: str | None
    topic_tags: list[str]
    context_tags: list[str]
    confidence: float | None
    vault_path: str | None
    classification_model: str | None
    classification_version: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class UndoResult:
    item: InboxItem | None
    deleted_vault_note: bool
    message: str


@dataclass(slots=True)
class QueueDrainResult:
    status: str
    processed_count: int
    pending_count: int
    blocked_item: InboxItem | None
    message: str


class InboxService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.db_path = resolve_path(settings.inbox.db_path) or (REPO_ROOT / "data" / "inbox.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._create_schema()

    def capture_and_publish(self, text: str) -> InboxItem:
        item = self.capture_unpublished(text, source="marko-native")
        return self._process(item, mark_transport_failures=True)

    def capture_unpublished(
        self,
        text: str,
        source: str = "marko-mobile",
        *,
        item_id: str | None = None,
        captured_at: str | None = None,
    ) -> InboxItem:
        original_text = text.strip()
        if not original_text:
            raise ValueError("Inbox item cannot be empty.")
        now = utc_now_iso()
        item_id = item_id or str(uuid4())
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO inbox_items (
                     id, source, captured_at, received_at, original_text, status,
                     created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'captured', ?, ?)""",
                (item_id, source, captured_at or now, now, original_text, now, now),
            )
            db.commit()
        item = self.get_item(item_id)
        if item is None:
            raise RuntimeError("Queued inbox item could not be loaded.")
        return item

    def review_and_publish(
        self,
        item_id: str,
        category: str,
        topic_tags: list[str],
        context_tags: list[str],
    ) -> InboxItem:
        item = self.get_item(item_id)
        if item is None:
            raise ValueError("Inbox item not found.")
        classification = Classification(
            category=category.strip(),
            topic_tags=normalize_tags(topic_tags),
            context_tags=normalize_tags(context_tags),
            confidence=1.0,
            classification_model=item.classification_model or "manual-review",
        )
        validate_classification(classification)
        note_path = self._publish(item, classification)
        self._update_classification(item.id, "published", classification, str(note_path))
        result = self.get_item(item.id)
        if result is None:
            raise RuntimeError("Published inbox item could not be loaded.")
        return result

    def list_recent(self, limit: int = 5) -> list[InboxItem]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM inbox_items WHERE status != 'undone' ORDER BY captured_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [hydrate(row) for row in rows]

    def list_unpublished(self, limit: int = 50) -> list[InboxItem]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM inbox_items WHERE status = 'captured' ORDER BY captured_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [hydrate(row) for row in rows]

    def count_unpublished(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) FROM inbox_items WHERE status = 'captured'").fetchone()
        return int(row[0]) if row else 0

    def get_item(self, item_id: str) -> InboxItem | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        return hydrate(row) if row else None

    def undo_last_publish(self) -> UndoResult:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM inbox_items WHERE status = 'published' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return UndoResult(None, False, "Nothing to undo.")
        item = hydrate(row)
        deleted = False
        if item.vault_path:
            note = Path(item.vault_path)
            if self._is_inside_vault(note) and note.is_file():
                note.unlink()
                deleted = True
        self._mark_undone(item.id)
        return UndoResult(self.get_item(item.id), deleted, "Undid last published item.")

    def undo_last_unpublished(self) -> UndoResult:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM inbox_items WHERE status = 'captured' ORDER BY captured_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return UndoResult(None, False, "Nothing to undo.")
        item = hydrate(row)
        self._mark_undone(item.id)
        return UndoResult(self.get_item(item.id), False, "Removed latest unpublished item.")

    def drain_unpublished_queue(self) -> QueueDrainResult:
        processed = 0
        while True:
            item = self._next_unpublished()
            if item is None:
                return QueueDrainResult("completed", processed, 0, None, "No unpublished items.")
            try:
                result = self._process(item, mark_transport_failures=False)
            except httpx.HTTPError as exc:
                return QueueDrainResult("backend_unavailable", processed, self.count_unpublished(), None, str(exc))
            if result.status == "published":
                processed += 1
                continue
            return QueueDrainResult("blocked", processed, self.count_unpublished(), result, result.error_message or result.status)

    def _process(self, item: InboxItem, *, mark_transport_failures: bool) -> InboxItem:
        try:
            classification = self._classify(item.original_text)
        except httpx.HTTPError as exc:
            if not mark_transport_failures:
                raise
            self._mark_failed(item.id, "classification_unavailable", str(exc))
            return self.get_item(item.id) or item
        except Exception as exc:
            self._mark_failed(item.id, "classification_failed", str(exc))
            return self.get_item(item.id) or item

        if classification.confidence < self.settings.inbox.confidence_threshold:
            self._update_classification(item.id, "needs_review", classification, None)
        else:
            try:
                note_path = self._publish(item, classification)
            except Exception as exc:
                self._mark_failed(item.id, "publish_failed", str(exc))
                return self.get_item(item.id) or item
            self._update_classification(item.id, "published", classification, str(note_path))
        return self.get_item(item.id) or item

    def _classify(self, original_text: str) -> Classification:
        messages = [
            {"role": "system", "content": PROMPT_TEMPLATE},
            {"role": "user", "content": f"Inbox item:\n{original_text}"},
        ]
        if self.settings.llm.provider == "openai":
            endpoint = f"{self.settings.llm.base_url}/v1/chat/completions"
            payload: dict[str, Any] = {
                "model": self.settings.llm.model, "messages": messages,
                "temperature": 0.1, "stream": False,
            }
        else:
            endpoint = f"{self.settings.llm.base_url}/api/chat"
            payload = {
                "model": self.settings.llm.model, "messages": messages, "stream": False,
                "options": {"temperature": 0.1, "num_ctx": self.settings.llm.num_ctx},
            }
        with httpx.Client(timeout=self.settings.llm.request_timeout_seconds) as client:
            response = client.post(endpoint, json=payload)
            response.raise_for_status()
        data = response.json()
        content = (
            str(data["choices"][0]["message"]["content"])
            if self.settings.llm.provider == "openai"
            else str((data.get("message") or {}).get("content") or "")
        )
        parsed = parse_json_object(content)
        tags = normalize_tags(parsed.get("topic_tags")) + normalize_tags(parsed.get("context_tags"))
        if not tags:
            tags = [derive_fallback_tag(original_text)]
        topics = normalize_tags(parsed.get("topic_tags")) or tags[:1]
        contexts = [tag for tag in normalize_tags(parsed.get("context_tags")) if tag not in topics]
        topics, contexts = trim_tags(topics, contexts)
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        classification = Classification(
            category=str(parsed.get("category", "")),
            topic_tags=topics,
            context_tags=contexts,
            confidence=confidence,
            classification_model=self.settings.llm.model,
        )
        validate_classification(classification)
        return classification

    def _publish(self, item: InboxItem, classification: Classification) -> Path:
        vault = self._vault_root()
        destination = vault / self.settings.tools.obsidian.inbox_folder / classification.category
        destination.mkdir(parents=True, exist_ok=True)
        title = title_from_item(item.original_text)
        note = destination / f"{safe_filename(title)} - {item.id[:8]}.md"
        if not note.exists():
            tag_lines = "\n".join(f"  - {json.dumps(tag)}" for tag in [*classification.topic_tags, *classification.context_tags])
            note.write_text(
                "---\n"
                f"id: {json.dumps(item.id)}\n"
                f"captured_at: {json.dumps(item.captured_at)}\n"
                f"category: {json.dumps(classification.category)}\n"
                f"confidence: {classification.confidence}\n"
                f"tags:\n{tag_lines}\n"
                "---\n\n"
                f"# {title}\n\n{item.original_text.strip()}\n",
                encoding="utf-8",
            )
        return note

    def _vault_root(self) -> Path:
        if not self.settings.tools.obsidian.enabled:
            raise RuntimeError("Enable tools.obsidian before publishing Inbox items.")
        raw = self.settings.tools.obsidian.vault_path
        if not raw:
            raise RuntimeError("Set tools.obsidian.vault_path in config/assistant.yaml.")
        vault = Path(raw).expanduser().resolve()
        if not vault.is_dir():
            raise RuntimeError(f"Obsidian vault path does not exist: {vault}")
        return vault

    def _is_inside_vault(self, path: Path) -> bool:
        try:
            vault = self._vault_root()
            resolved = path.resolve()
            return resolved == vault or vault in resolved.parents
        except RuntimeError:
            return False

    def _next_unpublished(self) -> InboxItem | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM inbox_items WHERE status = 'captured' ORDER BY captured_at ASC LIMIT 1"
            ).fetchone()
        return hydrate(row) if row else None

    def _update_classification(self, item_id: str, status: str, value: Classification, vault_path: str | None) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """UPDATE inbox_items SET status=?, category=?, topic_tags_json=?,
                   context_tags_json=?, confidence=?, vault_path=COALESCE(?, vault_path),
                   classification_model=?, classification_version=?, error_code=NULL,
                   error_message=NULL, updated_at=? WHERE id=?""",
                (
                    status, value.category, json.dumps(value.topic_tags), json.dumps(value.context_tags),
                    value.confidence, vault_path, value.classification_model,
                    value.classification_version, utc_now_iso(), item_id,
                ),
            )
            db.commit()

    def _mark_failed(self, item_id: str, code: str, message: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE inbox_items SET status='failed', error_code=?, error_message=?, updated_at=? WHERE id=?",
                (code, message, utc_now_iso(), item_id),
            )
            db.commit()

    def _mark_undone(self, item_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE inbox_items SET status='undone', updated_at=? WHERE id=?", (utc_now_iso(), item_id))
            db.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
        finally:
            connection.close()

    def _create_schema(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS inbox_items (
                     id TEXT PRIMARY KEY,
                     source TEXT NOT NULL,
                     captured_at TEXT NOT NULL,
                     received_at TEXT NOT NULL,
                     original_text TEXT NOT NULL,
                     status TEXT NOT NULL,
                     category TEXT,
                     topic_tags_json TEXT NOT NULL DEFAULT '[]',
                     context_tags_json TEXT NOT NULL DEFAULT '[]',
                     confidence REAL,
                     vault_path TEXT,
                     classification_model TEXT,
                     classification_version TEXT,
                     error_code TEXT,
                     error_message TEXT,
                     created_at TEXT NOT NULL,
                     updated_at TEXT NOT NULL
                   );
                   CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_items(status);
                   CREATE INDEX IF NOT EXISTS idx_inbox_captured_at ON inbox_items(captured_at);"""
            )
            db.commit()


def hydrate(row: sqlite3.Row) -> InboxItem:
    return InboxItem(
        id=row["id"], source=row["source"], captured_at=row["captured_at"], received_at=row["received_at"],
        original_text=row["original_text"], status=row["status"], category=row["category"],
        topic_tags=json.loads(row["topic_tags_json"] or "[]"),
        context_tags=json.loads(row["context_tags_json"] or "[]"), confidence=row["confidence"],
        vault_path=row["vault_path"], classification_model=row["classification_model"],
        classification_version=row["classification_version"], error_code=row["error_code"],
        error_message=row["error_message"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("Classifier did not return a JSON object.")
    return json.loads(text[start : end + 1])


def normalize_tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        tag = re.sub(r"[^a-z0-9_-]+", "-", str(value).strip().lower()).strip("-")
        if tag and tag not in result:
            result.append(tag)
    return result


def trim_tags(topics: list[str], contexts: list[str]) -> tuple[list[str], list[str]]:
    combined = [("topic", value) for value in topics] + [("context", value) for value in contexts]
    kept = combined[:3]
    return ([v for kind, v in kept if kind == "topic"], [v for kind, v in kept if kind == "context"])


def validate_classification(value: Classification) -> None:
    tags = [*value.topic_tags, *value.context_tags]
    if value.category not in CATEGORIES:
        raise RuntimeError("Classifier returned an unknown category.")
    if not 1 <= len(tags) <= 3:
        raise RuntimeError("Classifier must return one to three tags.")
    if not 0 <= value.confidence <= 1:
        raise RuntimeError("Classifier confidence must be between zero and one.")


def derive_fallback_tag(text: str) -> str:
    words = re.findall(r"[a-z0-9]{4,}", text.lower())
    return words[0] if words else "note"


def title_from_item(value: str) -> str:
    cleaned = re.sub(r"^idea:\s*", "", value.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned)[:72] or "Untitled Item"


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    return re.sub(r"\s+", " ", cleaned).strip()[:72] or "Untitled Item"
