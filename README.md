# CCHP Model Manager

macOS SwiftUI manager for the isolated Claude Code entry at `127.0.0.1:8081`.

## Features

- Save multiple local CCHP downstream keys and load the model allowlist for the selected key.
- Remember the selected profile, model, workspace, and session mode.
- Choose a recent or new working directory and start a new, continued, or resumed Claude session.
- Run the selected Claude or Codex process directly in the embedded SwiftTerm PTY. The app does not type launch commands into an existing agent session.
- Use the four-corner button in the terminal header to enter macOS full screen; the running PTY stays attached to the same agent process.
- Switch between Claude and Codex in one window. Codex gets a per-key isolated `CODEX_HOME`, provider config, and session history, so the regular `~/.codex` installation is not changed.
- Keep all manager keys and Claude settings in the existing private local config directory.

Adding a key sends it to the local Python backend over stdin. The backend queries only `GET /v1/models` and stores the key in the existing `~/.config/cchp-client/claude-test/` directory with restrictive permissions. It does not send an inference request. Keys, runtime configuration, and conversation data are not part of this repository.

Launching Claude may incur local CCHP balance and upstream charges. Resuming a session requires confirmation because its history is sent to the selected key and upstream. The app does not edit the user's regular Claude or Codex configuration.

Codex launch uses the local CCHP Responses-compatible endpoint and passes `CCH_API_KEY` only to the child process. Codex model names are read from the selected key's `/v1/models` allowlist. The isolated Codex session history is separate from the regular Codex desktop/TUI history; this prevents provider-mismatch resume errors.

## Build

Requirements: macOS 14+, Swift 6.2+, Python 3, Claude Code at `~/.local/bin/claude`, and the local CCHP service at `127.0.0.1:8081`.

Run `./build-app.command`. The app bundle is written to `build/CCHP Model Manager.app`.

The app includes the macOS SwiftTerm source at commit `282c3c3a932b55c129628876b70f37d993af1615` (tag `1.99.3`) under its MIT license. The local target omits SwiftTerm's unrelated cross-platform package dependencies, which otherwise make SwiftPM fetch their complete Git histories on macOS. License and provenance are in `Vendor/SwiftTerm/UPSTREAM.md`.
