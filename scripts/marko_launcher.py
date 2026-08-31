from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import tkinter as tk
import traceback
import winsound
from pathlib import Path
from tkinter import messagebox, ttk
from uuid import uuid4

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.config import load_settings
from app.services.inbox import CATEGORIES, InboxItem, InboxService
from app.services.local_process import LocalProcessManager
from app.services.runtime import DEVICE_UPLOAD_DIR, process_assistant_turn, process_text_assistant_turn
from app.services.windows_audio import WindowsWaveRecorder


COLORS = {
    "paper": "#e8dfd0", "panel": "#d9cfbf", "panel_2": "#cfc5b5",
    "ink": "#161616", "muted": "#706c65", "accent": "#146c62", "bad": "#a8322a",
}
MAX_HISTORY = 4
LOG_PATH = REPO_ROOT / "data" / "marko_launcher.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class MarkoInbox(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.inbox = InboxService(self.settings)
        self.process_manager = LocalProcessManager(self.settings.local_process)
        self.recorder: WindowsWaveRecorder | None = None
        self.history: list[dict[str, str]] = []
        self.submit_in_progress = False
        self.queue_in_progress = False
        self.icon_state = "stopped"
        self.animation_index = 0

        self.title(f"{self.settings.assistant.name}'s Inbox")
        icon_path = REPO_ROOT / "webAssets" / "qq2.ico"
        if icon_path.is_file():
            self.iconbitmap(default=str(icon_path))
        self.geometry("1160x680")
        self.minsize(980, 620)
        self.configure(bg=COLORS["paper"])
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self.refresh_recent()
        self.after(250, self.drain_queue)
        self.after(120, self._animate_icon)
        self.after(5000, self._poll_queue)

    def _panel(self, parent: tk.Misc, color: str, padx: int = 12, pady: int = 10) -> tk.Frame:
        return tk.Frame(parent, bg=color, padx=padx, pady=pady)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure("TButton", font=("Trebuchet MS", 10), padding=(10, 6))
        root = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=COLORS["paper"], bd=0, sashwidth=10)
        root.pack(fill="both", expand=True, padx=18, pady=16)
        root.add(self._build_inbox_column(root), minsize=270)
        root.add(self._build_icon_column(root), minsize=360)
        root.add(self._build_chat_column(root), minsize=280)

    def _build_inbox_column(self, parent: tk.Misc) -> tk.Frame:
        column = tk.Frame(parent, bg=COLORS["paper"])
        column.grid_columnconfigure(0, weight=1)
        column.grid_rowconfigure(3, weight=1)
        tk.Label(column, text="Inbox", bg=COLORS["paper"], fg=COLORS["ink"], font=("Trebuchet MS", 25, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 12))
        entry_panel = self._panel(column, COLORS["panel_2"])
        entry_panel.grid(row=1, column=0, sticky="ew")
        entry_panel.grid_columnconfigure(0, weight=1)
        self.inbox_input = tk.Text(entry_panel, height=5, wrap="word", bd=0, bg=COLORS["panel_2"], fg=COLORS["ink"], font=("Trebuchet MS", 15))
        self.inbox_input.grid(row=0, column=0, sticky="ew")
        self.inbox_input.bind("<Return>", self._capture_on_enter)
        actions = tk.Frame(column, bg=COLORS["paper"])
        actions.grid(row=2, column=0, sticky="ew", pady=(6, 10))
        actions.grid_columnconfigure(0, weight=1)
        self.inbox_status = tk.StringVar(value="Ready")
        tk.Label(actions, textvariable=self.inbox_status, bg=COLORS["paper"], fg=COLORS["muted"], font=("Trebuchet MS", 10)).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Undo", command=self.undo_last, width=8).grid(row=0, column=1)
        ttk.Button(actions, text="Enter", command=self.capture_current, width=9).grid(row=0, column=2, padx=(8, 0))
        recent_panel = self._panel(column, COLORS["panel_2"], 14, 12)
        recent_panel.grid(row=3, column=0, sticky="nsew", pady=(0, 12))
        recent_panel.grid_columnconfigure(0, weight=1)
        tk.Label(recent_panel, text="Last 5", bg=COLORS["panel_2"], fg=COLORS["ink"], font=("Trebuchet MS", 17)).grid(row=0, column=0, sticky="w")
        self.recent_frame = tk.Frame(recent_panel, bg=COLORS["panel_2"])
        self.recent_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.recent_frame.grid_columnconfigure(0, weight=1)
        model_panel = self._panel(column, COLORS["panel_2"], 14, 8)
        model_panel.grid(row=4, column=0, sticky="ew")
        model_panel.grid_columnconfigure(0, weight=1)
        self.model_status = tk.StringVar(value="Local AI")
        tk.Label(model_panel, textvariable=self.model_status, bg=COLORS["panel_2"], fg=COLORS["ink"], font=("Trebuchet MS", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Button(model_panel, text="Start", command=self.start_local_process, width=7).grid(row=0, column=1)
        ttk.Button(model_panel, text="Restart", command=self.restart_local_process, width=8).grid(row=0, column=2, padx=(6, 0))
        self.process_log = tk.Text(model_panel, height=4, state="disabled", wrap="word", bd=0, bg=COLORS["panel_2"], fg=COLORS["muted"], font=("Consolas", 8))
        self.process_log.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        return column

    def _build_icon_column(self, parent: tk.Misc) -> tk.Frame:
        column = tk.Frame(parent, bg=COLORS["paper"])
        column.grid_rowconfigure(0, weight=1)
        column.grid_columnconfigure(0, weight=1)
        self.icon_images = self._load_images()
        self.icon_canvas = tk.Canvas(column, width=340, height=340, bg=COLORS["paper"], highlightthickness=0, cursor="hand2")
        self.icon_canvas.grid(row=0, column=0, sticky="nsew")
        self.icon_id = self.icon_canvas.create_image(170, 170, image=self.icon_images["base"], anchor="center")
        self.icon_canvas.bind("<Button-1>", self._icon_clicked)
        self.icon_canvas.bind("<Configure>", lambda event: self.icon_canvas.coords(self.icon_id, event.width / 2, event.height / 2))
        return column

    def _build_chat_column(self, parent: tk.Misc) -> tk.Frame:
        column = tk.Frame(parent, bg=COLORS["paper"])
        column.grid_columnconfigure(0, weight=1)
        column.grid_rowconfigure(1, weight=1)
        tk.Label(column, text=self.settings.llm.model or "Local AI", bg=COLORS["paper"], fg=COLORS["ink"], font=("Trebuchet MS", 15, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        panel = self._panel(column, COLORS["panel"], 16, 12)
        panel.grid(row=1, column=0, sticky="nsew")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self.chat_log = tk.Text(panel, state="disabled", wrap="word", bd=0, bg=COLORS["panel"], fg=COLORS["ink"], font=("Trebuchet MS", 11))
        self.chat_log.grid(row=0, column=0, sticky="nsew")
        self.chat_input = tk.Text(panel, height=4, wrap="word", bd=0, bg=COLORS["panel_2"], fg=COLORS["ink"], font=("Trebuchet MS", 12))
        self.chat_input.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.chat_input.bind("<Return>", self._chat_on_enter)
        ttk.Button(panel, text="Enter", command=self.send_chat).grid(row=2, column=0, sticky="e", pady=(6, 0))
        return column

    def _load_images(self) -> dict[str, object]:
        base = tk.PhotoImage(file=str(REPO_ROOT / "qq4.png")).subsample(3, 3)
        spins = [tk.PhotoImage(file=str(path)) for path in sorted((REPO_ROOT / "webAssets" / "qq4_spin_smooth").glob("frame-*.png"))]
        pulses = [tk.PhotoImage(file=str(path)) for path in sorted((REPO_ROOT / "webAssets" / "qq4_pulse").glob("frame-*.png"))]
        if pulses:
            base = pulses[0]
        return {"base": base, "spin": spins or [base], "pulse": pulses or [base]}

    def _capture_on_enter(self, event: tk.Event) -> str:
        if event.state & 0x0001:
            return ""
        self.capture_current()
        return "break"

    def capture_current(self) -> None:
        if self.submit_in_progress:
            return
        text = self.inbox_input.get("1.0", "end").strip()
        if not text:
            return
        self.submit_in_progress = True
        self.inbox_input.delete("1.0", "end")
        self.inbox_status.set("Classifying")
        threading.Thread(target=self._capture_worker, args=(text,), daemon=True).start()

    def _capture_worker(self, text: str) -> None:
        try:
            self._ensure_backend()
            item = self.inbox.capture_and_publish(text)
            self.after(0, self._capture_done, item)
        except Exception as exc:
            self.after(0, self._capture_failed, str(exc))

    def _capture_done(self, item: InboxItem) -> None:
        self.submit_in_progress = False
        self.refresh_recent()
        self.inbox_status.set(item.status.replace("_", " ").title())
        if item.status in {"needs_review", "failed"}:
            self.show_review(item)

    def _capture_failed(self, message: str) -> None:
        self.submit_in_progress = False
        self.inbox_status.set("Error")
        messagebox.showerror("Inbox", message)

    def refresh_recent(self) -> None:
        for child in self.recent_frame.winfo_children():
            child.destroy()
        for row, item in enumerate(self.inbox.list_recent(5)):
            text = item.original_text.strip().replace("\n", " ")
            frame = tk.Frame(self.recent_frame, bg="#f4eee4", padx=8, pady=7)
            frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            frame.grid_columnconfigure(0, weight=1)
            tk.Label(frame, text=text[:82], bg="#f4eee4", fg=COLORS["ink"], anchor="w", justify="left", wraplength=270, font=("Trebuchet MS", 10, "bold")).grid(row=0, column=0, sticky="ew")
            meta = " | ".join(value for value in [item.status, item.category, *item.topic_tags[:2]] if value)
            tk.Label(frame, text=meta, bg="#f4eee4", fg=COLORS["muted"], anchor="w", font=("Trebuchet MS", 8)).grid(row=1, column=0, sticky="ew")

    def show_review(self, item: InboxItem) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Review Inbox item")
        dialog.configure(bg=COLORS["paper"])
        dialog.transient(self)
        dialog.grab_set()
        category = tk.StringVar(value=item.category or guess_category(item.original_text))
        topics = tk.StringVar(value=", ".join(item.topic_tags or [guess_tag(item.original_text)]))
        contexts = tk.StringVar(value=", ".join(item.context_tags))
        error = tk.StringVar(value=item.error_message or "")
        frame = self._panel(dialog, COLORS["panel"], 18, 18)
        frame.pack(padx=14, pady=14, fill="both", expand=True)
        tk.Label(frame, text=item.original_text, bg=COLORS["panel"], fg=COLORS["ink"], wraplength=520, justify="left").grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        for row, (label, variable) in enumerate((("Category", category), ("Topic tags", topics), ("Context tags", contexts)), start=1):
            tk.Label(frame, text=label, bg=COLORS["panel"], fg=COLORS["muted"]).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            if row == 1:
                ttk.Combobox(frame, textvariable=variable, values=CATEGORIES, state="readonly").grid(row=row, column=1, sticky="ew", pady=4)
            else:
                ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=4)
        tk.Label(frame, textvariable=error, bg=COLORS["panel"], fg=COLORS["bad"], wraplength=520).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Button(frame, text="Publish", command=lambda: self._publish_review(dialog, item.id, category.get(), topics.get(), contexts.get(), error)).grid(row=5, column=1, sticky="e", pady=(12, 0))
        frame.grid_columnconfigure(1, weight=1)

    def _publish_review(self, dialog: tk.Toplevel, item_id: str, category: str, topics: str, contexts: str, error: tk.StringVar) -> None:
        try:
            self.inbox.review_and_publish(item_id, category, split_tags(topics), split_tags(contexts))
        except Exception as exc:
            error.set(str(exc))
            return
        dialog.destroy()
        self.inbox_status.set("Published")
        self.refresh_recent()
        self.after(0, self.drain_queue)

    def undo_last(self) -> None:
        try:
            result = self.inbox.undo_last_publish()
            self.inbox_status.set(result.message)
            self.refresh_recent()
        except Exception as exc:
            messagebox.showerror("Undo", str(exc))

    def drain_queue(self) -> None:
        if self.queue_in_progress or self.submit_in_progress or self.inbox.count_unpublished() == 0:
            return
        self.queue_in_progress = True
        threading.Thread(target=self._drain_worker, daemon=True).start()

    def _drain_worker(self) -> None:
        try:
            self._ensure_backend()
            result = self.inbox.drain_unpublished_queue()
            self.after(0, self._drain_done, result)
        except Exception as exc:
            self.after(0, self._drain_failed, str(exc))

    def _drain_done(self, result) -> None:
        self.queue_in_progress = False
        self.refresh_recent()
        self.inbox_status.set(result.message)
        if result.blocked_item:
            self.show_review(result.blocked_item)

    def _drain_failed(self, message: str) -> None:
        self.queue_in_progress = False
        self.inbox_status.set("Queue waiting")
        logging.warning("Queue drain failed: %s", message)

    def _poll_queue(self) -> None:
        self.drain_queue()
        self.after(5000, self._poll_queue)

    def _chat_on_enter(self, event: tk.Event) -> str:
        if event.state & 0x0001:
            return ""
        self.send_chat()
        return "break"

    def send_chat(self) -> None:
        text = self.chat_input.get("1.0", "end").strip()
        if not text or self.icon_state in {"thinking", "starting"}:
            return
        self.chat_input.delete("1.0", "end")
        self._append_chat("You", text)
        self._set_icon_state("thinking")
        threading.Thread(target=self._chat_worker, args=(text,), daemon=True).start()

    def _chat_worker(self, text: str) -> None:
        try:
            self._ensure_backend()
            data = asyncio.run(process_text_assistant_turn(transcript=text, history=self.history[-MAX_HISTORY:]))
            self.history.extend(({"role": "user", "content": data["transcript"]}, {"role": "assistant", "content": data["reply_text"]}))
            del self.history[:-MAX_HISTORY]
            self.after(0, self._append_chat, self.settings.assistant.name, data["reply_text"])
            self.after(0, self._set_icon_state, "running")
        except Exception as exc:
            logging.error("Text turn failed\n%s", traceback.format_exc())
            self.after(0, self._voice_failed, str(exc))

    def _icon_clicked(self, _event: tk.Event) -> None:
        if self.icon_state == "listening":
            self.stop_voice_recording()
        elif self.icon_state not in {"thinking", "starting"}:
            self.start_voice_recording()

    def start_voice_recording(self) -> None:
        try:
            self._ensure_backend()
            self.recorder = WindowsWaveRecorder()
            self.recorder.start()
            self._set_icon_state("listening")
            self._append_chat(self.settings.assistant.name, "Listening…")
        except Exception as exc:
            self._voice_failed(str(exc))

    def stop_voice_recording(self) -> None:
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        self._set_icon_state("thinking")
        output = DEVICE_UPLOAD_DIR / f"{uuid4()}.wav"
        threading.Thread(target=self._voice_worker, args=(recorder, output), daemon=True).start()

    def _voice_worker(self, recorder: WindowsWaveRecorder, output: Path) -> None:
        try:
            audio = recorder.stop_to_file(output)
            data = asyncio.run(process_assistant_turn(audio_path=audio, history=self.history[-MAX_HISTORY:], synthesize_audio=True, external_tts=True))
            self.history.extend(({"role": "user", "content": data["transcript"]}, {"role": "assistant", "content": data["reply_text"]}))
            del self.history[:-MAX_HISTORY]
            self.after(0, self._append_chat, "You", data["transcript"])
            self.after(0, self._append_chat, self.settings.assistant.name, data["reply_text"])
            if data["response_audio_path"]:
                winsound.PlaySound(str(data["response_audio_path"]), winsound.SND_FILENAME)
            self.after(0, self._set_icon_state, "running")
        except Exception as exc:
            logging.error("Voice turn failed\n%s", traceback.format_exc())
            self.after(0, self._voice_failed, str(exc))

    def _voice_failed(self, message: str) -> None:
        self._set_icon_state("running" if self.backend_available() else "stopped")
        self._append_chat("Error", message)

    def _append_chat(self, speaker: str, text: str) -> None:
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", f"{speaker}: {text.strip()}\n\n")
        self.chat_log.see("end")
        self.chat_log.configure(state="disabled")

    def backend_available(self) -> bool:
        endpoint = "/api/tags" if self.settings.llm.provider == "ollama" else "/v1/models"
        try:
            return httpx.get(self.settings.llm.base_url + endpoint, timeout=0.7).status_code < 500
        except httpx.HTTPError:
            return False

    def _ensure_backend(self) -> None:
        if self.backend_available():
            return
        self.process_manager.start()
        deadline = time.monotonic() + self.settings.local_process.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.backend_available():
                return
            if not self.process_manager.running:
                break
            time.sleep(0.25)
        raise RuntimeError("The configured local AI did not become ready.")

    def start_local_process(self) -> None:
        try:
            process = self.process_manager.start()
            self.model_status.set("Local AI starting")
            threading.Thread(target=self._read_process_output, args=(process,), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Local AI", str(exc))

    def restart_local_process(self) -> None:
        try:
            process = self.process_manager.restart()
            self.model_status.set("Local AI restarting")
            threading.Thread(target=self._read_process_output, args=(process,), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Local AI", str(exc))

    def _read_process_output(self, process) -> None:
        if process.stdout:
            for line in process.stdout:
                self.after(0, self._append_process_log, line.rstrip())
        self.after(0, self.model_status.set, "Local AI stopped")

    def _append_process_log(self, text: str) -> None:
        self.process_log.configure(state="normal")
        self.process_log.insert("end", text + "\n")
        self.process_log.see("end")
        self.process_log.configure(state="disabled")

    def _set_icon_state(self, state: str) -> None:
        self.icon_state = state
        self.animation_index = 0

    def _animate_icon(self) -> None:
        frames = self.icon_images["spin"] if self.icon_state in {"thinking", "starting"} else self.icon_images["pulse"]
        image = frames[self.animation_index % len(frames)]
        self.icon_canvas.itemconfigure(self.icon_id, image=image)
        self.animation_index += 1
        self.after(65 if self.icon_state in {"thinking", "starting"} else 95, self._animate_icon)

    def on_close(self) -> None:
        if self.recorder:
            self.recorder.abort()
        if self.process_manager.running and messagebox.askyesno("Exit", "Stop the local AI process started by this app?"):
            self.process_manager.stop()
        self.destroy()


def split_tags(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def guess_category(text: str) -> str:
    lowered = text.lower()
    if "?" in lowered:
        return "question"
    if "need to" in lowered or "todo" in lowered:
        return "task"
    if "idea" in lowered:
        return "idea"
    return "other"


def guess_tag(text: str) -> str:
    words = [word for word in "".join(character if character.isalnum() else " " for character in text.lower()).split() if len(word) >= 4]
    return words[0] if words else "note"


def main() -> None:
    os.chdir(REPO_ROOT)
    MarkoInbox().mainloop()


if __name__ == "__main__":
    main()
