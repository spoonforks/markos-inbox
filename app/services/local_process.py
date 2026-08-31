from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.services.config import LocalProcessSettings, REPO_ROOT


class LocalProcessManager:
    """Owns only the optional process it starts itself."""

    def __init__(self, settings: LocalProcessSettings) -> None:
        self.settings = settings
        self.process: subprocess.Popen[str] | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> subprocess.Popen[str]:
        if self.running:
            return self.process  # type: ignore[return-value]
        if not self.settings.enabled:
            raise RuntimeError("Optional local process launch is disabled in config/assistant.yaml.")
        if not self.settings.command:
            raise RuntimeError("local_process.command is empty.")
        working_directory = (
            Path(self.settings.working_directory).expanduser().resolve()
            if self.settings.working_directory
            else REPO_ROOT
        )
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            self.settings.command,
            cwd=working_directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            shell=False,
            creationflags=creationflags,
        )
        return self.process

    def stop(self) -> bool:
        process = self.process
        if process is None:
            return False
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.process = None
        return True

    def restart(self) -> subprocess.Popen[str]:
        self.stop()
        return self.start()
