from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx

from app.services.config import AppSettings, load_settings
from app.services.inbox import Classification, InboxService


def settings_for(tmp_path: Path) -> AppSettings:
    settings = load_settings()
    vault = tmp_path / "vault"
    vault.mkdir()
    settings.inbox.db_path = str(tmp_path / "inbox.sqlite")
    settings.tools.obsidian.enabled = True
    settings.tools.obsidian.vault_path = str(vault)
    settings.tools.obsidian.inbox_folder = "Inbox"
    return settings


def result(confidence: float) -> Classification:
    return Classification(
        category="idea",
        topic_tags=["making"],
        context_tags=[],
        confidence=confidence,
        classification_model="test-model",
    )


def test_capture_publish_and_undo(tmp_path: Path) -> None:
    service = InboxService(settings_for(tmp_path))
    service._classify = lambda _text: result(0.95)  # type: ignore[method-assign]

    item = service.capture_and_publish("Build a small paper prototype")

    assert item.status == "published"
    assert item.vault_path and Path(item.vault_path).is_file()
    undo = service.undo_last_publish()
    assert undo.deleted_vault_note is True
    assert not Path(item.vault_path).exists()
    assert service.get_item(item.id).status == "undone"  # type: ignore[union-attr]


def test_low_confidence_review_then_publish(tmp_path: Path) -> None:
    service = InboxService(settings_for(tmp_path))
    service._classify = lambda _text: result(0.2)  # type: ignore[method-assign]

    item = service.capture_and_publish("Maybe explore a new material")
    assert item.status == "needs_review"

    reviewed = service.review_and_publish(item.id, "project_note", ["materials"], [])
    assert reviewed.status == "published"
    assert reviewed.category == "project_note"


def test_queue_is_idempotent_and_pauses_when_backend_is_offline(tmp_path: Path) -> None:
    service = InboxService(settings_for(tmp_path))
    first = service.capture_unpublished("First", item_id="same-client-id")
    duplicate = service.capture_unpublished("Changed", item_id="same-client-id")
    assert duplicate.original_text == first.original_text

    def offline(_text: str) -> Classification:
        raise httpx.ConnectError("offline")

    service._classify = offline  # type: ignore[method-assign]
    outcome = service.drain_unpublished_queue()
    assert outcome.status == "backend_unavailable"
    assert outcome.pending_count == 1


def test_database_contains_only_inbox_schema(tmp_path: Path) -> None:
    service = InboxService(settings_for(tmp_path))
    with sqlite3.connect(service.db_path) as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables == {"inbox_items"}
