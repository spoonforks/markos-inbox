from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from app.services.config import LocalProcessSettings, load_settings
from app.services.local_process import LocalProcessManager
from app.services.ollama_client import OllamaAssistantService


def test_personality_is_present_in_system_prompt() -> None:
    settings = load_settings()
    settings.assistant.personality = "Speak like a precise garden designer."
    prompt = OllamaAssistantService(settings).build_system_prompt([])
    assert "precise garden designer" in prompt
    assert settings.assistant.name in prompt


def test_process_manager_owns_only_its_child(monkeypatch, tmp_path: Path) -> None:
    child = Mock()
    child.poll.return_value = None
    popen = Mock(return_value=child)
    monkeypatch.setattr("app.services.local_process.subprocess.Popen", popen)
    manager = LocalProcessManager(
        LocalProcessSettings(True, ["model-server", "--local"], str(tmp_path), 5)
    )

    manager.start()
    _, kwargs = popen.call_args
    assert kwargs["shell"] is False
    assert manager.stop() is True
    child.terminate.assert_called_once()

    fresh_manager = LocalProcessManager(manager.settings)
    assert fresh_manager.stop() is False
    assert child.terminate.call_count == 1
