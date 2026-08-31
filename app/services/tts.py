from __future__ import annotations

import re
import subprocess
import threading
import wave
from pathlib import Path
from uuid import uuid4

from app.services.config import TTSSettings, resolve_path


TTS_REMOVE_CHARS = str.maketrans("", "", "*\"")
TTS_DASH_CHARS = str.maketrans({char: " " for char in "-\u2010\u2011\u2012\u2013\u2014\u2015"})
WHITESPACE_PATTERN = re.compile(r"\s+")


def sanitize_tts_text(text: str) -> str:
    cleaned = text.translate(TTS_DASH_CHARS).translate(TTS_REMOVE_CHARS)
    return WHITESPACE_PATTERN.sub(" ", cleaned).strip()


class PiperTextToSpeechService:
    def __init__(self, settings: TTSSettings, output_dir: Path) -> None:
        self.settings = settings
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._voice = None
        self._syn_config = None
        self._voice_lock = threading.Lock()

    def _resolve_model_path(self) -> Path:
        model_path = resolve_path(self.settings.model_path)
        if model_path is None or not model_path.exists():
            raise RuntimeError(
                "Piper model_path is not configured. Update config/assistant.yaml."
            )
        return model_path

    def _resolve_config_path(self) -> Path | None:
        config_path = resolve_path(self.settings.config_path)
        if config_path is not None and config_path.exists():
            return config_path
        return None

    def _load_python_voice(self):
        if self._voice is not None:
            return self._voice, self._syn_config

        with self._voice_lock:
            if self._voice is not None:
                return self._voice, self._syn_config

            try:
                from piper import PiperVoice, SynthesisConfig
            except ImportError:
                return None, None

            model_path = self._resolve_model_path()
            config_path = self._resolve_config_path()

            try:
                voice = PiperVoice.load(
                    model_path=str(model_path),
                    config_path=str(config_path) if config_path else None,
                )
            except Exception:
                return None, None

            self._voice = voice
            self._syn_config = SynthesisConfig(speaker_id=self.settings.speaker)
            return self._voice, self._syn_config

    def _synthesize_with_python_voice(self, text: str) -> Path | None:
        voice, syn_config = self._load_python_voice()
        if voice is None:
            return None

        output_path = self.output_dir / f"{uuid4()}.wav"
        with wave.open(str(output_path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        return output_path

    def synthesize(self, text: str) -> Path:
        text = sanitize_tts_text(text)
        if not text:
            raise RuntimeError("Cannot synthesize empty text.")

        python_output = self._synthesize_with_python_voice(text)
        if python_output is not None:
            return python_output

        return self.synthesize_subprocess(text)

    def synthesize_subprocess(self, text: str) -> Path:
        text = sanitize_tts_text(text)
        if not text:
            raise RuntimeError("Cannot synthesize empty text.")

        raw_binary = self.settings.binary_path.strip()
        if not raw_binary:
            raise RuntimeError("Piper binary_path is empty. Update config/assistant.yaml.")

        binary = raw_binary
        if "/" in raw_binary or "\\" in raw_binary or raw_binary.lower().endswith(".exe"):
            resolved_binary = resolve_path(raw_binary)
            binary = str(resolved_binary) if resolved_binary is not None else raw_binary

        model_path = self._resolve_model_path()

        output_path = self.output_dir / f"{uuid4()}.wav"
        command = [
            binary,
            "--model",
            str(model_path),
            "--output_file",
            str(output_path),
        ]

        config_path = self._resolve_config_path()
        if config_path:
            command.extend(["--config", str(config_path)])
        if self.settings.speaker is not None:
            command.extend(["--speaker", str(self.settings.speaker)])

        try:
            subprocess.run(
                command,
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Piper executable was not found. Update tts.binary_path or add Piper to PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"Piper synthesis failed: {stderr or exc}") from exc

        return output_path
