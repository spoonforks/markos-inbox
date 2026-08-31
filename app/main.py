from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.services.config import REPO_ROOT, load_settings
from app.services.memory import extract_explicit_memory
from app.services.ollama_client import OllamaAssistantService
from app.services.stt import SpeechToTextService
from app.services.inbox import InboxItem, InboxService
from app.services.tools import ToolRouter
from app.services.tts import PiperTextToSpeechService
from app.storage.files import AssistantDataStore


SETTINGS = load_settings()
DATA_DIR = REPO_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
RESPONSE_DIR = DATA_DIR / "responses"
TEMPLATES = Jinja2Templates(directory=str(REPO_ROOT / "app" / "templates"))
MAX_HISTORY_ITEMS = 4
MAX_GENERATED_FILES = 2

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESPONSE_DIR.mkdir(parents=True, exist_ok=True)

store = AssistantDataStore(DATA_DIR, max_memories=SETTINGS.memory.max_items)
stt_service = SpeechToTextService(SETTINGS.stt)
ollama_service = OllamaAssistantService(SETTINGS)
inbox_service = InboxService(SETTINGS)
tool_router = ToolRouter(SETTINGS)
tts_service = PiperTextToSpeechService(SETTINGS.tts, RESPONSE_DIR)

app = FastAPI(title="Marko's Inbox")
app.mount("/static", StaticFiles(directory=str(REPO_ROOT / "app" / "static")), name="static")
app.mount("/webAssets", StaticFiles(directory=str(REPO_ROOT / "webAssets")), name="web_assets")
app.mount("/generated/responses", StaticFiles(directory=str(RESPONSE_DIR)), name="responses")


class InboxCaptureRequest(BaseModel):
    text: str
    client_id: str | None = None
    captured_at: str | None = None


def build_public_origin(request: Request) -> str:
    if SETTINGS.server.public_base_url:
        return SETTINGS.server.public_base_url
    return str(request.base_url).rstrip("/")


def guess_extension(filename: str | None, content_type: str | None) -> str:
    if filename:
        suffix = Path(filename).suffix
        if suffix:
            return suffix.lower()

    mapping = {
        "audio/webm": ".webm",
        "audio/webm;codecs=opus": ".webm",
        "audio/ogg": ".ogg",
        "audio/ogg;codecs=opus": ".ogg",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
    }
    return mapping.get((content_type or "").lower(), ".bin")


def sanitize_history(raw_history: str) -> list[dict[str, str]]:
    if not raw_history.strip():
        return []

    try:
        parsed = json.loads(raw_history)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid history payload.") from exc

    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail="History payload must be a list.")

    cleaned: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            cleaned.append({"role": role, "content": content})
    return cleaned[-MAX_HISTORY_ITEMS:]


def prune_old_files(directory: Path, keep: int) -> None:
    files = [path for path in directory.iterdir() if path.is_file()]
    if len(files) <= keep:
        return

    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        path.unlink(missing_ok=True)


def serialize_capture_item(item: InboxItem) -> dict[str, str]:
    return {
        "id": item.id,
        "original_text": item.original_text,
        "captured_at": item.captured_at,
    }


INBOX_SYNC_TOKEN = os.environ.get("MARKO_INBOX_SYNC_TOKEN", "").strip()
if len(INBOX_SYNC_TOKEN) < 32:
    raise RuntimeError("MARKO_INBOX_SYNC_TOKEN must contain at least 32 characters.")


def require_inbox_auth(authorization: str | None = Header(default=None)) -> None:
    candidate = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if candidate and secrets.compare_digest(candidate, INBOX_SYNC_TOKEN):
        return
    raise HTTPException(status_code=401, detail="Inbox unlock token required.")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {"assistant_name": SETTINGS.assistant.name},
    )


@app.get("/capture", response_class=HTMLResponse)
async def capture_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "capture.html",
        {"assistant_name": SETTINGS.assistant.name},
    )


@app.get("/capture-sw.js")
async def capture_service_worker() -> FileResponse:
    return FileResponse(
        REPO_ROOT / "app" / "static" / "js" / "capture-sw.js",
        media_type="application/javascript",
    )


@app.get("/capture.webmanifest")
async def capture_manifest() -> JSONResponse:
    return JSONResponse(
        {
            "name": f"{SETTINGS.assistant.name} Inbox",
            "short_name": "Marko Inbox",
            "display": "standalone",
            "start_url": "/capture",
            "scope": "/",
            "background_color": "#e8dfd0",
            "theme_color": "#e8dfd0",
            "icons": [
                {
                    "src": "/webAssets/qq2.png",
                    "sizes": "512x512",
                    "type": "image/png",
                }
            ],
        }
    )


@app.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    return {
        "assistant_name": SETTINGS.assistant.name,
        "public_origin": build_public_origin(request),
        "memory_count": len(store.list_memories()),
        "https_configured": bool(SETTINGS.server.ssl_certfile and SETTINGS.server.ssl_keyfile),
        "tts_configured": bool(SETTINGS.tts.model_path),
        "model": SETTINGS.llm.model,
    }


@app.get("/api/inbox/unpublished")
async def unpublished_items(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_inbox_auth(authorization)
    items = await run_in_threadpool(inbox_service.list_unpublished)
    serialized = [serialize_capture_item(item) for item in items]
    return {"items": serialized, "count": len(serialized)}


@app.post("/api/inbox/unpublished")
async def create_unpublished_item(
    payload: InboxCaptureRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_inbox_auth(authorization)
    item = await run_in_threadpool(
        inbox_service.capture_unpublished,
        payload.text,
        "marko-mobile",
        item_id=payload.client_id,
        captured_at=payload.captured_at,
    )
    count = await run_in_threadpool(inbox_service.count_unpublished)
    return {
        "item": serialize_capture_item(item),
        "count": count,
        "message": "Queued inbox item.",
    }


@app.post("/api/inbox/unpublished/undo")
async def undo_unpublished_item(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_inbox_auth(authorization)
    result = await run_in_threadpool(inbox_service.undo_last_unpublished)
    count = await run_in_threadpool(inbox_service.count_unpublished)
    return {
        "item": serialize_capture_item(result.item) if result.item else None,
        "count": count,
        "message": result.message,
    }


@app.post("/api/chat")
async def chat(
    request: Request,
    audio: UploadFile = File(...),
    image: UploadFile | None = File(None),
    history: str = Form("[]"),
) -> dict[str, Any]:
    raw_audio = await audio.read()
    if not raw_audio:
        raise HTTPException(status_code=400, detail="Uploaded audio file was empty.")

    upload_id = str(uuid4())
    extension = guess_extension(audio.filename, audio.content_type)
    upload_path = UPLOAD_DIR / f"{upload_id}{extension}"
    upload_path.write_bytes(raw_audio)
    prune_old_files(UPLOAD_DIR, MAX_GENERATED_FILES)
    image_bytes = await image.read() if image is not None else b""

    try:
        transcript = await run_in_threadpool(stt_service.transcribe, upload_path)
        prior_history = sanitize_history(history)

        memory_record = None
        memory_fact = extract_explicit_memory(transcript)
        if memory_fact:
            memory_record = store.append_memory(memory_fact, transcript)

        memories = store.list_memories()
        tool_contexts = await tool_router.collect_contexts(transcript)
        reply_text = await ollama_service.generate_reply(
            transcript=transcript,
            memories=memories,
            history=prior_history,
            tool_contexts=tool_contexts,
            user_images=[image_bytes] if image_bytes else None,
        )
        response_audio_path = await run_in_threadpool(tts_service.synthesize, reply_text)
        prune_old_files(RESPONSE_DIR, MAX_GENERATED_FILES)
        store.append_exchange(
            transcript=transcript,
            reply_text=reply_text,
            memory_saved=memory_record["fact"] if memory_record else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    clean_reply_text = reply_text.strip()

    audio_url = f"{build_public_origin(request)}/generated/responses/{response_audio_path.name}"
    return {
        "transcript": transcript,
        "reply_text": clean_reply_text,
        "audio_url": audio_url,
        "memory_saved": memory_record["fact"] if memory_record else None,
        "needs_photo": False,
    }
