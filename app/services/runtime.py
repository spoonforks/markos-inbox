from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.services.config import REPO_ROOT, load_settings
from app.services.memory import extract_explicit_memory
from app.services.ollama_client import OllamaAssistantService
from app.services.stt import SpeechToTextService
from app.services.tools import ToolRouter
from app.services.tts import PiperTextToSpeechService
from app.storage.files import AssistantDataStore


DATA_DIR = REPO_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DEVICE_UPLOAD_DIR = DATA_DIR / "device_uploads"
RESPONSE_DIR = DATA_DIR / "responses"
MAX_GENERATED_FILES = 2
MAX_DEVICE_UPLOADS = 8

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DEVICE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESPONSE_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS = load_settings()
store = AssistantDataStore(DATA_DIR, max_memories=SETTINGS.memory.max_items)
stt_service = SpeechToTextService(SETTINGS.stt)
ollama_service = OllamaAssistantService(SETTINGS)
tool_router = ToolRouter(SETTINGS)
tts_service = PiperTextToSpeechService(SETTINGS.tts, RESPONSE_DIR)


def prune_old_files(directory: Path, keep: int) -> None:
    files = [path for path in directory.iterdir() if path.is_file()]
    if len(files) <= keep:
        return

    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        path.unlink(missing_ok=True)


async def process_assistant_turn(
    *,
    audio_path: Path,
    history: list[dict[str, str]],
    image_bytes: bytes = b"",
    synthesize_audio: bool = True,
    external_tts: bool = False,
) -> dict[str, Any]:
    try:
        transcript = await run_in_threadpool(stt_service.transcribe, audio_path)

        memory_record = None
        memory_fact = extract_explicit_memory(transcript)
        if memory_fact:
            memory_record = store.append_memory(memory_fact, transcript)

        memories = store.list_memories()
        tool_contexts = await tool_router.collect_contexts(transcript)
        reply_text = await ollama_service.generate_reply(
            transcript=transcript,
            memories=memories,
            history=history,
            tool_contexts=tool_contexts,
            user_images=[image_bytes] if image_bytes else None,
        )
        response_audio_path = None
        if synthesize_audio:
            synthesize = tts_service.synthesize_subprocess if external_tts else tts_service.synthesize
            response_audio_path = await run_in_threadpool(synthesize, reply_text)
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

    return {
        "transcript": transcript,
        "reply_text": reply_text.strip(),
        "response_audio_path": response_audio_path,
        "memory_saved": memory_record["fact"] if memory_record else None,
    }


async def process_text_assistant_turn(
    *,
    transcript: str,
    history: list[dict[str, str]],
) -> dict[str, Any]:
    transcript = transcript.strip()
    if not transcript:
        raise RuntimeError("Text entry was empty.")

    try:
        memory_record = None
        memory_fact = extract_explicit_memory(transcript)
        if memory_fact:
            memory_record = store.append_memory(memory_fact, transcript)

        memories = store.list_memories()
        tool_contexts = await tool_router.collect_contexts(transcript)
        reply_text = await ollama_service.generate_reply(
            transcript=transcript,
            memories=memories,
            history=history,
            tool_contexts=tool_contexts,
        )
        store.append_exchange(
            transcript=transcript,
            reply_text=reply_text,
            memory_saved=memory_record["fact"] if memory_record else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "transcript": transcript,
        "reply_text": reply_text.strip(),
        "response_audio_path": None,
        "memory_saved": memory_record["fact"] if memory_record else None,
    }
