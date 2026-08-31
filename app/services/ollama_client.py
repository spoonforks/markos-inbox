from __future__ import annotations

import base64
from typing import Iterable

import httpx

from app.services.config import AppSettings
from app.services.memory import memory_lines
from app.services.tools import ToolContext


class OllamaAssistantService:
    MAX_PROMPT_HISTORY_ITEMS = 4

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def _provider(self) -> str:
        provider = self.settings.llm.provider.strip().lower()
        if provider in {"ollama", "openai"}:
            return provider
        return "ollama"

    def _endpoint(self) -> str:
        if self._provider() == "openai":
            return "/v1/chat/completions"
        return "/api/chat"

    def _response_text(self, data: dict[str, object]) -> str:
        message = data.get("message", {})
        if isinstance(message, dict):
            content = str(message.get("content", "")).strip()
            if content:
                return content

        choices = data.get("choices", [])
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                choice_message = first_choice.get("message", {})
                if isinstance(choice_message, dict):
                    content = choice_message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                    if isinstance(content, list):
                        text_parts = []
                        for item in content:
                            if not isinstance(item, dict):
                                continue
                            if item.get("type") == "text":
                                text = str(item.get("text", "")).strip()
                                if text:
                                    text_parts.append(text)
                        if text_parts:
                            return "\n".join(text_parts)

        return ""

    def _build_payload(
        self,
        *,
        messages: list[dict[str, object]],
    ) -> dict[str, object]:
        if self._provider() == "openai":
            return {
                "model": self.settings.llm.model,
                "messages": messages,
                "temperature": self.settings.llm.temperature,
                "stream": False,
            }

        return {
            "model": self.settings.llm.model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": self.settings.llm.temperature,
                "num_ctx": self.settings.llm.num_ctx,
            },
        }

    def build_system_prompt(self, memories: Iterable[dict[str, str]]) -> str:
        injected_memories = memory_lines(memories, self.settings.memory.inject_limit)
        memory_block = "\n".join(injected_memories) if injected_memories else "- No saved facts yet."
        operating_rules = [
            f"- Your name is {self.settings.assistant.name}.",
            "- You are running locally for a single user.",
            "- Be concise but complete.",
            "- If the user asks you to remember a fact, acknowledge it naturally.",
            "- Do not claim to have remembered new facts unless explicitly told to remember them.",
        ]

        if self.settings.tools.obsidian.enabled:
            operating_rules.append("- Treat the Obsidian vault as authoritative personal context when fresh Obsidian tool results are provided.")
            if self.settings.tools.obsidian.write_requires_explicit_instruction:
                operating_rules.append(
                    "- Treat the Obsidian vault as read-only unless the user explicitly asks you to create, save, append, capture, or update a note."
                )
            if self.settings.tools.obsidian.task_note_title:
                operating_rules.append(
                    f"- If the user asks for a task suggestion by priority, use the configured Obsidian task note titled {self.settings.tools.obsidian.task_note_title} when available."
                )
        operating_rules_block = "\n".join(operating_rules)

        return (
            f"{self.settings.assistant.personality.strip()}\n\n"
            "Operating rules:\n"
            f"{operating_rules_block}\n\n"
            "Saved user facts:\n"
            f"{memory_block}"
        )

    async def generate_reply(
        self,
        *,
        transcript: str,
        memories: list[dict[str, str]],
        history: list[dict[str, str]],
        tool_contexts: list[ToolContext] | None = None,
        user_images: list[bytes] | None = None,
    ) -> str:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": self.build_system_prompt(memories)}
        ]
        if tool_contexts:
            tool_block = "\n\n".join(
                f"Tool: {context.tool_name}\n{context.content}" for context in tool_contexts
            )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Fresh tool results are available below. "
                        "Use them as authoritative factual context for this reply.\n\n"
                        f"{tool_block}"
                    ),
                }
            )
        messages.extend(history[-self.MAX_PROMPT_HISTORY_ITEMS :])
        user_message: dict[str, object] = {"role": "user", "content": transcript}
        if user_images and self._provider() != "openai":
            user_message["images"] = [
                base64.b64encode(image_bytes).decode("utf-8") for image_bytes in user_images
            ]
        messages.append(user_message)

        payload = self._build_payload(messages=messages)

        async with httpx.AsyncClient(timeout=self.settings.llm.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.llm.base_url}{self._endpoint()}",
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        content = self._response_text(data)
        if not content:
            raise RuntimeError("LLM backend returned an empty response.")
        return content
