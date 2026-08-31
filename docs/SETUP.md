# Setup

## Local AI

Run Ollama or another OpenAI-compatible local server. In
`config/assistant.yaml`, choose `provider: ollama` for `/api/chat`, or
`provider: openai` for `/v1/chat/completions`, then set its base URL and model.
The app does not download or bundle a model.

The optional `local_process` section is disabled by default. If enabled,
`command` must be a YAML list such as `["ollama", "serve"]`; no shell command
string is accepted. `working_directory` and `startup_timeout_seconds` control
launch behavior. Start, restart, and exit can terminate only the child process
created by this app. An independently started server is never killed.

## Voice

Install FFmpeg so `faster-whisper` can decode recordings. The first STT run may
download the selected Whisper model; model caches are not part of this repo.
For spoken replies, install Piper and download your own matching `.onnx` and
`.onnx.json` voice files into the ignored `voices/` directory, then configure
their paths. Leave `tts.model_path` blank if you do not need speech output.

## Obsidian and Inbox

Set `tools.obsidian.vault_path` to an existing vault. Published notes are placed
under `<vault>/<inbox_folder>/<category>/`. A low-confidence classification is
held for review in the desktop app. Undo removes the database record logically;
for a published note it deletes only the exact note created inside the configured
vault.

The app starts with a new ignored `data/inbox.sqlite`. It deliberately has no
legacy database import or migration path.

## Mobile synchronization

`MARKO_INBOX_SYNC_TOKEN` is required at server startup and must be at least 32
characters. The three synchronization endpoints require a bearer token:

- `GET /api/inbox/unpublished`
- `POST /api/inbox/unpublished`
- `POST /api/inbox/unpublished/undo`

Offline entries remain in local storage until an authenticated synchronization
succeeds. Rotate the token by replacing it in `.env`, restarting the server, and
entering the new value on each mobile browser when prompted.
