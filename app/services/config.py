from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def find_repo_root() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        for candidate in (executable_dir, *executable_dir.parents):
            if (candidate / "config" / "assistant.yaml").exists():
                return candidate
        return executable_dir
    return Path(__file__).resolve().parents[2]


REPO_ROOT = find_repo_root()
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "assistant.yaml"


@dataclass(slots=True)
class AssistantSettings:
    name: str
    personality: str


@dataclass(slots=True)
class LLMSettings:
    provider: str
    base_url: str
    model: str
    temperature: float
    num_ctx: int
    request_timeout_seconds: int


@dataclass(slots=True)
class LocalProcessSettings:
    enabled: bool
    command: list[str]
    working_directory: str
    startup_timeout_seconds: int


@dataclass(slots=True)
class STTSettings:
    model_size: str
    device: str
    compute_type: str
    language: str


@dataclass(slots=True)
class TTSSettings:
    binary_path: str
    model_path: str
    config_path: str
    speaker: int | None


@dataclass(slots=True)
class ServerSettings:
    host: str
    port: int
    public_base_url: str
    ssl_certfile: str
    ssl_keyfile: str


@dataclass(slots=True)
class MemorySettings:
    max_items: int
    inject_limit: int


@dataclass(slots=True)
class WeatherToolSettings:
    enabled: bool
    default_location: str
    geocoding_country_code: str


@dataclass(slots=True)
class ObsidianToolSettings:
    enabled: bool
    vault_path: str
    inbox_folder: str
    daily_notes_folder: str
    search_result_limit: int
    write_requires_explicit_instruction: bool
    task_note_title: str


@dataclass(slots=True)
class ToolSettings:
    weather: WeatherToolSettings
    obsidian: ObsidianToolSettings


@dataclass(slots=True)
class InboxSettings:
    db_path: str
    confidence_threshold: float


@dataclass(slots=True)
class AppSettings:
    assistant: AssistantSettings
    llm: LLMSettings
    local_process: LocalProcessSettings
    stt: STTSettings
    tts: TTSSettings
    server: ServerSettings
    memory: MemorySettings
    tools: ToolSettings
    inbox: InboxSettings
    config_path: Path


def _nested(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key, {})
    return value if isinstance(value, dict) else {}


def resolve_path(path_value: str) -> Path | None:
    if not path_value:
        return None
    candidate = Path(path_value).expanduser()
    return candidate if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()


def configured_path() -> Path:
    override = os.environ.get("MARKO_CONFIG", "").strip()
    return Path(override).expanduser().resolve() if override else DEFAULT_CONFIG_PATH


def load_settings(config_path: Path | None = None) -> AppSettings:
    config_path = config_path or configured_path()
    if not config_path.exists():
        raise RuntimeError(
            f"Missing configuration: {config_path}. Copy config/assistant.example.yaml "
            "to config/assistant.yaml and customize it."
        )
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    assistant = _nested(raw, "assistant")
    llm = _nested(raw, "llm")
    local_process = _nested(raw, "local_process")
    stt = _nested(raw, "stt")
    tts = _nested(raw, "tts")
    server = _nested(raw, "server")
    memory = _nested(raw, "memory")
    tools = _nested(raw, "tools")
    weather = _nested(tools, "weather")
    obsidian = _nested(tools, "obsidian")
    inbox = _nested(raw, "inbox")

    provider = str(llm.get("provider", "ollama")).strip().lower()
    if provider not in {"ollama", "openai"}:
        raise ValueError("llm.provider must be 'ollama' or 'openai'.")
    raw_command = local_process.get("command", [])
    if not isinstance(raw_command, list) or not all(isinstance(item, str) for item in raw_command):
        raise ValueError("local_process.command must be a YAML list of arguments.")

    return AppSettings(
        assistant=AssistantSettings(
            name=str(assistant.get("name", "Marko")).strip() or "Marko",
            personality=str(assistant.get("personality", "")).strip(),
        ),
        llm=LLMSettings(
            provider=provider,
            base_url=str(llm.get("base_url", "http://127.0.0.1:11434")).rstrip("/"),
            model=str(llm.get("model", "")).strip(),
            temperature=float(llm.get("temperature", 0.7)),
            num_ctx=int(llm.get("num_ctx", 8192)),
            request_timeout_seconds=int(llm.get("request_timeout_seconds", 180)),
        ),
        local_process=LocalProcessSettings(
            enabled=bool(local_process.get("enabled", False)),
            command=[item for item in raw_command if item],
            working_directory=str(local_process.get("working_directory", "")).strip(),
            startup_timeout_seconds=int(local_process.get("startup_timeout_seconds", 30)),
        ),
        stt=STTSettings(
            model_size=str(stt.get("model_size", "base")),
            device=str(stt.get("device", "auto")),
            compute_type=str(stt.get("compute_type", "auto")),
            language=str(stt.get("language", "en")),
        ),
        tts=TTSSettings(
            binary_path=str(tts.get("binary_path", "piper")),
            model_path=str(tts.get("model_path", "")),
            config_path=str(tts.get("config_path", "")),
            speaker=tts.get("speaker"),
        ),
        server=ServerSettings(
            host=str(server.get("host", "127.0.0.1")),
            port=int(server.get("port", 8000)),
            public_base_url=str(server.get("public_base_url", "")).rstrip("/"),
            ssl_certfile=str(server.get("ssl_certfile", "")),
            ssl_keyfile=str(server.get("ssl_keyfile", "")),
        ),
        memory=MemorySettings(
            max_items=int(memory.get("max_items", 100)),
            inject_limit=int(memory.get("inject_limit", 20)),
        ),
        tools=ToolSettings(
            weather=WeatherToolSettings(
                enabled=bool(weather.get("enabled", False)),
                default_location=str(weather.get("default_location", "")).strip(),
                geocoding_country_code=str(weather.get("geocoding_country_code", "")).strip(),
            ),
            obsidian=ObsidianToolSettings(
                enabled=bool(obsidian.get("enabled", True)),
                vault_path=str(obsidian.get("vault_path", "")).strip(),
                inbox_folder=str(obsidian.get("inbox_folder", "Inbox")).strip(),
                daily_notes_folder=str(obsidian.get("daily_notes_folder", "Daily")).strip(),
                search_result_limit=int(obsidian.get("search_result_limit", 5)),
                write_requires_explicit_instruction=bool(obsidian.get("write_requires_explicit_instruction", True)),
                task_note_title=str(obsidian.get("task_note_title", "Tasks")).strip(),
            ),
        ),
        inbox=InboxSettings(
            db_path=str(inbox.get("db_path", "data/inbox.sqlite")).strip(),
            confidence_threshold=float(inbox.get("confidence_threshold", 0.68)),
        ),
        config_path=config_path,
    )
