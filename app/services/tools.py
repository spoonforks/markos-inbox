from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.services.config import AppSettings


WEATHER_TRIGGER_PATTERN = re.compile(
    r"\b(weather|forecast|temperature|temp|rain|wind|chance of rain)\b",
    re.IGNORECASE,
)
LOCATION_PATTERN = re.compile(
    r"\b(?:in|for|at)\s+(?P<location>[A-Za-z][A-Za-z\s,\-']{1,80})$",
    re.IGNORECASE,
)
MULTISPACE_PATTERN = re.compile(r"\s+")
CREATE_NOTE_TITLE_PATTERN = re.compile(
    r"\b(?:create|make)\s+(?:a\s+)?note\s+(?:called|titled|named)\s+(?P<title>[^:]+?)(?:\s*:\s*(?P<content>.+))?$",
    re.IGNORECASE,
)
SEARCH_NOTES_PATTERN = re.compile(
    r"\b(?:search|find|look(?:ing)?(?:\s+up)?|check)\b.*\b(?:notes|vault|obsidian)\b",
    re.IGNORECASE,
)
ASK_NOTES_PATTERN = re.compile(
    r"\b(?:what|anything|do)\b.*\b(?:notes|vault|obsidian)\b.*\b(?:about|on|for)\b",
    re.IGNORECASE,
)
CREATE_NOTE_PATTERN = re.compile(r"\b(?:create|make|start)\b.*\b(?:note|page)\b", re.IGNORECASE)
DAILY_NOTE_PATTERN = re.compile(
    r"\b(?:daily note|today'?s note|journal|log today)\b",
    re.IGNORECASE,
)
CAPTURE_PATTERN = re.compile(
    r"\b(?:write this down|note this|save this|capture this|put this in obsidian|add this to obsidian)\b",
    re.IGNORECASE,
)
APPEND_PATTERN = re.compile(
    r"\b(?:add|append|put|save|write)\b",
    re.IGNORECASE,
)
TASK_QUERY_PATTERN = re.compile(r"\b(?:task|tasks|priority|priorities)\b", re.IGNORECASE)
TASK_REQUEST_PATTERN = re.compile(
    r"\b(?:give|find|pick|provide|suggest|show|what(?:'s| is)?)\b.*\b(?:task|tasks)\b|\b(?:task|tasks)\b.*\b(?:today|priority|priorities)\b",
    re.IGNORECASE,
)
TASK_PRIORITY_PATTERN = re.compile(r"\b(low|medium|high)\b", re.IGNORECASE)
MARKDOWN_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*$")
PLAIN_PRIORITY_HEADING_PATTERN = re.compile(
    r"^\s*(?P<priority>low|medium|high)(?:\s+priority(?:\s+tasks?)?)?\s*:?\s*$",
    re.IGNORECASE,
)
TASK_ITEM_PATTERN = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(?:\[(?P<done>[ xX])\]\s+)?(?P<item>.+?)\s*$"
)


@dataclass(slots=True)
class ToolContext:
    tool_name: str
    content: str


@dataclass(slots=True)
class ObsidianSearchResult:
    path: Path
    snippet: str


@dataclass(slots=True)
class TaskNoteEntry:
    priority: str
    text: str


@dataclass(slots=True)
class WeatherReport:
    location_label: str
    forecast_date: str
    high_c: float
    low_c: float
    wind_kmh: float
    rain_chance_percent: int

    def as_prompt_block(self) -> str:
        return (
            f"Weather tool result for {self.location_label} on {self.forecast_date}:\n"
            "When answering, mention the high, low, max wind speed, and chance of rain using metric units.\n"
            f"- High: {self.high_c:.1f} C\n"
            f"- Low: {self.low_c:.1f} C\n"
            f"- Max wind speed: {self.wind_kmh:.1f} km/h\n"
            f"- Chance of rain: {self.rain_chance_percent}%"
        )


class WeatherToolService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def should_handle(self, transcript: str) -> bool:
        return bool(WEATHER_TRIGGER_PATTERN.search(transcript))

    def extract_location_query(self, transcript: str) -> str:
        cleaned = transcript.strip().rstrip("?.! ")
        match = LOCATION_PATTERN.search(cleaned)
        if match:
            return match.group("location").strip(" ,")
        return self.settings.tools.weather.default_location

    async def lookup_forecast(self, transcript: str) -> ToolContext | None:
        if not self.settings.tools.weather.enabled or not self.should_handle(transcript):
            return None

        location_query = self.extract_location_query(transcript)
        if not location_query:
            raise RuntimeError(
                "Weather tool needs a location. Set tools.weather.default_location in config/assistant.yaml "
                "or ask for weather in a specific place."
            )

        location = await self._geocode_location(location_query)
        report = await self._fetch_today_forecast(location)
        return ToolContext(tool_name="weather", content=report.as_prompt_block())

    async def _geocode_location(self, query: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": query,
            "count": 1,
            "language": "en",
            "format": "json",
        }
        if self.settings.tools.weather.geocoding_country_code:
            params["countryCode"] = self.settings.tools.weather.geocoding_country_code

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params=params,
            )
            response.raise_for_status()

        payload = response.json()
        results = payload.get("results") or []
        if not results:
            raise RuntimeError(f"Could not find a weather location for '{query}'.")
        return results[0]

    async def _fetch_today_forecast(self, location: dict[str, Any]) -> WeatherReport:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "daily": ",".join(
                        [
                            "temperature_2m_max",
                            "temperature_2m_min",
                            "precipitation_probability_max",
                            "wind_speed_10m_max",
                        ]
                    ),
                    "forecast_days": 1,
                    "timezone": "auto",
                    "temperature_unit": "celsius",
                    "windspeed_unit": "kmh",
                    "precipitation_unit": "mm",
                },
            )
            response.raise_for_status()

        payload = response.json()
        daily = payload.get("daily") or {}
        dates = daily.get("time") or []
        if not dates:
            raise RuntimeError("Weather forecast response did not include daily data.")

        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        rain_chances = daily.get("precipitation_probability_max") or []
        wind_speeds = daily.get("wind_speed_10m_max") or []
        if not all([highs, lows, rain_chances, wind_speeds]):
            raise RuntimeError("Weather forecast response was missing one or more required values.")

        location_label = self._format_location_label(location)
        forecast_date = self._format_date(dates[0])
        return WeatherReport(
            location_label=location_label,
            forecast_date=forecast_date,
            high_c=float(highs[0]),
            low_c=float(lows[0]),
            wind_kmh=float(wind_speeds[0]),
            rain_chance_percent=int(round(float(rain_chances[0]))),
        )

    @staticmethod
    def _format_location_label(location: dict[str, Any]) -> str:
        parts = [location.get("name"), location.get("admin1"), location.get("country")]
        return ", ".join(part for part in parts if part)

    @staticmethod
    def _format_date(date_str: str) -> str:
        try:
            return datetime.fromisoformat(date_str).strftime("%Y-%m-%d")
        except ValueError:
            return date_str


class ObsidianToolService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    async def handle(self, transcript: str) -> ToolContext | None:
        if not self.settings.tools.obsidian.enabled:
            return None

        action = self._detect_action(transcript)
        if action == "task":
            return self._task_lookup(transcript)
        if action == "search":
            return self._search_notes(transcript)
        if action == "create":
            return self._create_note(transcript)
        if action == "daily":
            return self._append_daily_note(transcript)
        if action == "capture":
            return self._quick_capture(transcript)
        return None

    def _detect_action(self, transcript: str) -> str | None:
        lowered = transcript.strip().lower()
        if CREATE_NOTE_TITLE_PATTERN.search(transcript):
            return "create"
        if DAILY_NOTE_PATTERN.search(lowered) and APPEND_PATTERN.search(lowered):
            return "daily"
        if self._looks_like_task_request(lowered):
            return "task"
        if SEARCH_NOTES_PATTERN.search(lowered) or ASK_NOTES_PATTERN.search(lowered):
            return "search"
        if CREATE_NOTE_PATTERN.search(lowered):
            return "create"
        if CAPTURE_PATTERN.search(lowered):
            return "capture"
        return None

    def _vault_root(self) -> Path:
        raw_path = self.settings.tools.obsidian.vault_path
        if not raw_path:
            raise RuntimeError(
                "Obsidian tool is enabled but tools.obsidian.vault_path is empty in config/assistant.yaml."
            )

        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_dir():
            raise RuntimeError(f"Obsidian vault path does not exist: {path}")
        return path

    def _search_notes(self, transcript: str) -> ToolContext:
        query = self._extract_search_query(transcript)
        if not query:
            raise RuntimeError("Obsidian note search needs a search phrase.")

        vault_root = self._vault_root()
        matches = self._find_note_matches(vault_root, query)
        if not matches:
            return ToolContext(
                tool_name="obsidian",
                content=f"Obsidian search found no notes matching '{query}'.",
            )

        lines = [f"Obsidian search results for '{query}':"]
        for match in matches:
            relative_path = match.path.relative_to(vault_root)
            lines.append(f"- {relative_path}: {match.snippet}")
        return ToolContext(tool_name="obsidian", content="\n".join(lines))

    def _task_lookup(self, transcript: str) -> ToolContext:
        vault_root = self._vault_root()
        task_note_title = self.settings.tools.obsidian.task_note_title
        if not task_note_title:
            raise RuntimeError(
                "Obsidian task lookup is enabled but tools.obsidian.task_note_title is empty in config/assistant.yaml."
            )

        task_note_path = self._find_note_by_title(vault_root, task_note_title)
        if task_note_path is None:
            return ToolContext(
                tool_name="obsidian",
                content=f"Could not find an Obsidian task note titled '{task_note_title}'.",
            )

        task_entries = self._extract_task_entries(task_note_path)
        requested_priority = self._extract_task_priority(transcript)
        if requested_priority:
            filtered_entries = [
                entry.text for entry in task_entries if entry.priority == requested_priority
            ]
        else:
            filtered_entries = [entry.text for entry in task_entries]

        relative_path = task_note_path.relative_to(vault_root)
        if not filtered_entries:
            priority_label = (
                f"{requested_priority} priority " if requested_priority is not None else ""
            )
            return ToolContext(
                tool_name="obsidian",
                content=(
                    f"Read Obsidian note {relative_path}, but found no open {priority_label}tasks."
                ),
            )

        header = f"Open tasks from Obsidian note {relative_path}:"
        if requested_priority:
            header += f" requested priority={requested_priority}."
        lines = [header]
        for item in filtered_entries[: self.settings.tools.obsidian.search_result_limit]:
            lines.append(f"- {item}")
        lines.append("Use these tasks as the source of truth for any task suggestion in this reply.")
        return ToolContext(tool_name="obsidian", content="\n".join(lines))

    def _create_note(self, transcript: str) -> ToolContext:
        match = CREATE_NOTE_TITLE_PATTERN.search(transcript.strip())
        if not match:
            raise RuntimeError("Could not parse the requested note title.")

        title = self._sanitize_title(match.group("title") or "")
        content = (match.group("content") or "").strip()
        if not title:
            raise RuntimeError("Could not create an Obsidian note without a title.")

        vault_root = self._vault_root()
        inbox_dir = self._resolve_folder(vault_root, self.settings.tools.obsidian.inbox_folder)
        inbox_dir.mkdir(parents=True, exist_ok=True)
        note_path = self._unique_note_path(inbox_dir, title)
        body = content if content else f"# {title}\n"
        if not body.endswith("\n"):
            body += "\n"
        note_path.write_text(body, encoding="utf-8")

        relative_path = note_path.relative_to(vault_root)
        return ToolContext(
            tool_name="obsidian",
            content=f"Created Obsidian note at {relative_path}.",
        )

    def _append_daily_note(self, transcript: str) -> ToolContext:
        content = self._extract_daily_note_content(transcript)
        if not content:
            raise RuntimeError("Daily note append needs content to write.")

        vault_root = self._vault_root()
        daily_dir = self._resolve_folder(vault_root, self.settings.tools.obsidian.daily_notes_folder)
        daily_dir.mkdir(parents=True, exist_ok=True)
        daily_path = daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"- {timestamp} {content.strip()}\n"
        with daily_path.open("a", encoding="utf-8") as handle:
            handle.write(entry)

        relative_path = daily_path.relative_to(vault_root)
        return ToolContext(
            tool_name="obsidian",
            content=f"Appended to Obsidian daily note {relative_path}: {content.strip()}",
        )

    def _quick_capture(self, transcript: str) -> ToolContext:
        content = self._extract_capture_content(transcript)
        if not content:
            raise RuntimeError("Quick capture needs content after the trigger phrase.")

        vault_root = self._vault_root()
        inbox_dir = self._resolve_folder(vault_root, self.settings.tools.obsidian.inbox_folder)
        inbox_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        title = self._sanitize_title("Quick Capture " + timestamp)
        note_path = self._unique_note_path(inbox_dir, title)
        note_body = f"# Quick Capture\n\n{content.strip()}\n"
        note_path.write_text(note_body, encoding="utf-8")

        relative_path = note_path.relative_to(vault_root)
        return ToolContext(
            tool_name="obsidian",
            content=f"Saved quick capture to Obsidian note {relative_path}.",
        )

    def _find_note_matches(self, vault_root: Path, query: str) -> list[ObsidianSearchResult]:
        query_lower = query.lower()
        results: list[ObsidianSearchResult] = []
        for path in vault_root.rglob("*.md"):
            if ".obsidian" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="ignore")
            lowered = text.lower()
            if query_lower not in lowered:
                continue

            snippet = self._extract_snippet(text, query_lower)
            results.append(ObsidianSearchResult(path=path, snippet=snippet))
            if len(results) >= self.settings.tools.obsidian.search_result_limit:
                break
        return results

    def _find_note_by_title(self, vault_root: Path, title: str) -> Path | None:
        target = title.strip().lower()
        if not target:
            return None

        partial_match: Path | None = None
        for path in vault_root.rglob("*.md"):
            if ".obsidian" in path.parts:
                continue
            stem = path.stem.strip().lower()
            if stem == target:
                return path
            if partial_match is None and target in stem:
                partial_match = path
        return partial_match

    def _extract_task_entries(self, note_path: Path) -> list[TaskNoteEntry]:
        try:
            text = note_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = note_path.read_text(encoding="utf-8", errors="ignore")

        entries: list[TaskNoteEntry] = []
        current_priority: str | None = None
        for raw_line in text.splitlines():
            heading_match = MARKDOWN_HEADING_PATTERN.match(raw_line)
            if heading_match:
                current_priority = self._priority_from_heading(heading_match.group("title"))
                continue

            plain_heading_match = PLAIN_PRIORITY_HEADING_PATTERN.match(raw_line)
            if plain_heading_match:
                current_priority = plain_heading_match.group("priority").lower()
                continue

            item_match = TASK_ITEM_PATTERN.match(raw_line)
            if not item_match or current_priority is None:
                continue
            if (item_match.group("done") or "").lower() == "x":
                continue

            item_text = MULTISPACE_PATTERN.sub(" ", item_match.group("item")).strip()
            if item_text:
                entries.append(TaskNoteEntry(priority=current_priority, text=item_text))
        return entries

    @staticmethod
    def _extract_snippet(text: str, query_lower: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if query_lower in line.lower():
                collapsed = MULTISPACE_PATTERN.sub(" ", line)
                return collapsed[:220]
        return MULTISPACE_PATTERN.sub(" ", text.strip())[:220]

    @staticmethod
    def _sanitize_title(title: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*]', "", title).strip().strip(".")
        cleaned = MULTISPACE_PATTERN.sub(" ", cleaned)
        return cleaned[:120]

    @staticmethod
    def _resolve_folder(vault_root: Path, folder_name: str) -> Path:
        folder = (vault_root / folder_name).resolve()
        if vault_root.resolve() not in folder.parents and folder != vault_root.resolve():
            raise RuntimeError("Configured Obsidian folder resolves outside the vault.")
        return folder

    @staticmethod
    def _unique_note_path(folder: Path, title: str) -> Path:
        candidate = folder / f"{title}.md"
        if not candidate.exists():
            return candidate
        stamp = datetime.now().strftime("%H%M%S")
        return folder / f"{title} {stamp}.md"

    @staticmethod
    def _extract_query(transcript: str, prefixes: list[str]) -> str:
        lowered = transcript.strip().lower()
        original = transcript.strip()
        for prefix in prefixes:
            index = lowered.find(prefix)
            if index != -1:
                extracted = original[index + len(prefix) :].strip(" :")
                if extracted.lower().startswith("that "):
                    extracted = extracted[5:]
                return extracted.strip()
        return ""

    def _extract_search_query(self, transcript: str) -> str:
        cleaned = transcript.strip().rstrip("?.! ")
        lowered = cleaned.lower()
        marker_patterns = [
            r"\b(?:about|on|for)\b",
        ]
        for marker in marker_patterns:
            match = re.search(marker, lowered)
            if match:
                query = cleaned[match.end() :].strip(" :")
                if query:
                    return query

        cleanup = lowered
        cleanup = re.sub(r"\b(search|find|look(?:ing)?(?:\s+up)?|check)\b", "", cleanup)
        cleanup = re.sub(r"\b(my|the|any|all)\b", "", cleanup)
        cleanup = re.sub(r"\b(notes|note|vault|obsidian)\b", "", cleanup)
        cleanup = MULTISPACE_PATTERN.sub(" ", cleanup).strip(" ?!.,:")
        return cleanup

    def _extract_daily_note_content(self, transcript: str) -> str:
        cleaned = transcript.strip()
        lowered = cleaned.lower()
        match = re.search(r"\b(?:daily note|today'?s note|journal)\b", lowered)
        if match:
            remainder = cleaned[match.end() :].strip(" :,-")
            remainder = re.sub(r"^(with|saying|that)\s+", "", remainder, flags=re.IGNORECASE)
            return remainder.strip()
        return ""

    def _extract_capture_content(self, transcript: str) -> str:
        cleaned = transcript.strip()
        patterns = [
            r"^write this down[:\s-]*",
            r"^note this[:\s-]*",
            r"^save this(?: to obsidian| in obsidian)?[:\s-]*",
            r"^capture this[:\s-]*",
            r"^put this in obsidian[:\s-]*",
            r"^add this to obsidian[:\s-]*",
        ]
        for pattern in patterns:
            updated = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
            if updated != cleaned:
                return updated.strip()
        return ""

    def _looks_like_task_request(self, transcript: str) -> bool:
        return bool(TASK_QUERY_PATTERN.search(transcript) and TASK_REQUEST_PATTERN.search(transcript))

    @staticmethod
    def _extract_task_priority(transcript: str) -> str | None:
        match = TASK_PRIORITY_PATTERN.search(transcript)
        if not match:
            return None
        return match.group(1).lower()

    @staticmethod
    def _priority_from_heading(heading: str) -> str | None:
        lowered = heading.strip().lower()
        for priority in ("low", "medium", "high"):
            if priority in lowered:
                return priority
        return None


class ToolRouter:
    def __init__(self, settings: AppSettings) -> None:
        self.weather = WeatherToolService(settings)
        self.obsidian = ObsidianToolService(settings)

    async def collect_contexts(self, transcript: str) -> list[ToolContext]:
        contexts: list[ToolContext] = []
        weather_context = await self.weather.lookup_forecast(transcript)
        if weather_context is not None:
            contexts.append(weather_context)
        obsidian_context = await self.obsidian.handle(transcript)
        if obsidian_context is not None:
            contexts.append(obsidian_context)
        return contexts
