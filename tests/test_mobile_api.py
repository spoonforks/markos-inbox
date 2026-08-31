from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import INBOX_SYNC_TOKEN, app


client = TestClient(app)
AUTH = {"Authorization": f"Bearer {INBOX_SYNC_TOKEN}"}


def test_mobile_api_rejects_missing_and_query_string_tokens() -> None:
    assert client.get("/api/inbox/unpublished").status_code == 401
    assert client.get(f"/api/inbox/unpublished?token={INBOX_SYNC_TOKEN}").status_code == 401
    assert client.get("/api/inbox/unpublished", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_mobile_capture_and_undo() -> None:
    item_id = str(uuid4())
    created = client.post(
        "/api/inbox/unpublished",
        headers=AUTH,
        json={"text": "Temporary test item", "client_id": item_id},
    )
    assert created.status_code == 200
    assert created.json()["item"]["id"] == item_id

    listed = client.get("/api/inbox/unpublished", headers=AUTH)
    assert listed.status_code == 200
    assert item_id in {item["id"] for item in listed.json()["items"]}

    undone = client.post("/api/inbox/unpublished/undo", headers=AUTH)
    assert undone.status_code == 200
