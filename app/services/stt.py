from __future__ import annotations

import os
import re
from pathlib import Path

from app.services.config import REPO_ROOT, STTSettings


HF_CACHE_DIR = REPO_ROOT / "data" / "huggingface"
MARKO_NAME_PATTERN = re.compile(r"\bmarco(?=(?:'s)?\b)(?!\s+polo\b)", re.IGNORECASE)


def normalize_transcript(text: str) -> str:
    def replace_marko(match: re.Match[str]) -> str:
        value = match.group(0)
        if value.isupper():
            return "MARKO"
        if value.islower():
            return "marko"
        return "Marko"

    return MARKO_NAME_PATTERN.sub(replace_marko, text)


def _candidate_windows_dll_dirs() -> list[Path]:
    candidates: list[Path] = []

    cuda_env = os.environ.get("CUDA_PATH")
    if cuda_env:
        candidates.append(Path(cuda_env) / "bin")

    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    cuda_root = program_files / "NVIDIA GPU Computing Toolkit" / "CUDA"
    if cuda_root.exists():
        for child in sorted(cuda_root.iterdir(), reverse=True):
            if child.is_dir():
                candidates.append(child / "bin")

    cudnn_roots = [
        program_files / "NVIDIA" / "CUDNN",
        program_files / "NVIDIA" / "cuDNN",
    ]
    for root in cudnn_roots:
        if root.exists():
            for child in sorted(root.rglob("bin"), reverse=True):
                if child.is_dir():
                    candidates.append(child)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen and path.exists():
            unique.append(path)
            seen.add(key)
    return unique


def _prepare_windows_gpu_dll_paths() -> None:
    if os.name != "nt":
        return

    dll_dirs = _candidate_windows_dll_dirs()
    if not dll_dirs:
        return

    for dll_dir in dll_dirs:
        try:
            os.add_dll_directory(str(dll_dir))
        except (AttributeError, FileNotFoundError, OSError):
            continue

    path_entries = os.environ.get("PATH", "").split(";")
    existing = {entry.lower() for entry in path_entries if entry}
    prepend = [str(dll_dir) for dll_dir in dll_dirs if str(dll_dir).lower() not in existing]
    if prepend:
        os.environ["PATH"] = ";".join(prepend + path_entries)


class SpeechToTextService:
    def __init__(self, settings: STTSettings) -> None:
        self.settings = settings
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR / "hub"))
        _prepare_windows_gpu_dll_paths()

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                f"Speech-to-text dependencies are incomplete: {exc}. "
                "Run `pip install -r requirements.txt`."
            ) from exc

        device = self.settings.device if self.settings.device != "auto" else "auto"
        compute_type = (
            self.settings.compute_type if self.settings.compute_type != "auto" else "auto"
        )

        try:
            self._model = WhisperModel(
                self.settings.model_size,
                device=device,
                compute_type=compute_type,
            )
        except Exception as exc:
            message = str(exc)
            cuda_missing = "cublas64_12.dll" in message or "cuda" in message.lower()
            if not cuda_missing:
                raise

            self._model = WhisperModel(
                self.settings.model_size,
                device="cpu",
                compute_type="int8",
            )
        return self._model

    def transcribe(self, audio_path: Path) -> str:
        model = self._load_model()
        segments, _ = model.transcribe(
            str(audio_path),
            language=self.settings.language or None,
            vad_filter=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise RuntimeError("No speech was detected in the uploaded audio.")
        return normalize_transcript(text)
