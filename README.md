# Marko's Inbox

A local-first Inbox and assistant for capturing loose ideas, classifying them
with your own local model, reviewing uncertain results, and publishing notes to
an Obsidian vault. It includes a Windows desktop interface and an installable,
offline-capable mobile Inbox.

This repository contains no original conversations, memories, databases,
recordings, model weights, voice models, secrets, certificates, or personal
configuration. Runtime data is created under the ignored `data/` directory.

## What is included

- Native Windows Inbox with capture, five recent items, classification review,
  publish, and undo.
- Text and push-to-talk chat with the configured local assistant.
- Robot icon, smooth spin, and pulse animations.
- Mobile PWA with offline capture and authenticated synchronization.
- Ollama and OpenAI-compatible local endpoints.
- Optional model-server launch using an argument list and `shell=False`.

The desktop recorder uses Windows audio APIs. The FastAPI server and PWA can
run separately on any Python platform supported by the dependencies.

## Quick start

1. Install Python 3.11 or newer, FFmpeg, a local model server, and optionally
   Piper plus a compatible `.onnx` voice model.
2. Create and activate a virtual environment, then install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Copy `config/assistant.example.yaml` to the ignored
   `config/assistant.yaml` and edit the endpoint, model, voice paths, and
   Obsidian vault path.
4. Copy `.env.example` to the ignored `.env`. Generate a sync token, for
   example with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
5. Run the native app with `python scripts/marko_launcher.py`, or run only the
   server/PWA with `python scripts/run_server.py` and open
   `http://127.0.0.1:8000/capture`.

See [Setup](docs/SETUP.md), [Personality](docs/PERSONALITY.md), and
[Architecture and security](docs/ARCHITECTURE.md).

## Private mobile access

Keep the default localhost bind and expose it privately to devices on your
Tailscale network:

```powershell
tailscale serve --bg 8000
```

Open the HTTPS URL Tailscale reports on the phone, install the `/capture` page,
and enter the same sync token when prompted. The token is stored only in the
browser's local storage and sent only in the Authorization header. API replies
and credentials are never put in the service-worker cache.

For explicit LAN access, set `server.host` to `0.0.0.0`, allow the port through
your firewall, and use HTTPS if the browser needs PWA installation or microphone
features. LAN binding exposes the service to that network and is not the default.

## License

The application code and included robot artwork are released under the MIT
License. See `LICENSE`.
