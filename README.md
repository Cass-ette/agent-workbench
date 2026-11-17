# Claude & Codex Agent 工作台

## Windows 下载与上手

Windows 朋友请从 [Windows 发布页](https://github.com/Cass-ette/claude-cchp-manager/releases/tag/windows-v0.1.3) 下载 `Agent-Workbench-Windows-x64-0.1.3-setup.exe`。普通使用无需安装 Rust、Visual Studio 或克隆源码；仍需安装准备使用的 Claude Code / Codex CLI，并提供自己的兼容网关地址和调用 Key。

- [Windows 中文快速上手与排错](windows/QUICKSTART.zh-CN.md)
- [Windows 源码、开发与构建说明](windows/README.md)
- 本项目采用 [MIT 许可证](LICENSE)，第三方组件保留各自许可证。
- 每个人使用自己的配置和凭据；`127.0.0.1` 指使用者当前的电脑，不会自动连接作者的网关。

## macOS

macOS SwiftUI manager for the isolated Claude Code entry at `127.0.0.1:8081`.

The repository now includes a no-secret CCHP deployment template under `deployment/cchp-local/`. It creates a separate local CCHP database and runtime key material for each user; it never packages the author's `.env`, Docker volumes, upstream provider keys, or downstream keys.

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

Requirements: macOS 14+, Swift 6.2+, Python 3, Claude Code at `~/.local/bin/claude`, and the local CCHP service at `127.0.0.1:8081`. The build script produces a universal arm64/x86_64 application.

Run `./build-app.command`. The app bundle is written to `build/CCHP Model Manager.app`. Run `./package-friend.command` to create a ZIP containing the app and a fresh local CCHP setup template. This ad-hoc signed build is not notarized; broad distribution requires an Apple Developer ID signature and notarization.

The app includes the macOS SwiftTerm source at commit `282c3c3a932b55c129628876b70f37d993af1615` (tag `1.99.3`) under its MIT license. The local target omits SwiftTerm's unrelated cross-platform package dependencies, which otherwise make SwiftPM fetch their complete Git histories on macOS. License and provenance are in `Vendor/SwiftTerm/UPSTREAM.md`.

## Friend deployment

Run `deployment/cchp-local/setup.command` on the friend's Mac after installing and starting Docker Desktop. The script generates a new `.env`, starts the official CCHP containers, and opens the local control panel. The friend then adds their own upstream provider keys in CCHP and creates a downstream LLM key for this manager. The manager must receive the downstream key, never the upstream key.

The related project [ding113/claude-code-hub](https://github.com/ding113/claude-code-hub) is a separate MIT-licensed CC Hub implementation. It is not the CCHP Plus image used by the deployment template. Its documented public endpoint is primarily OpenAI Chat Completions and it does not promise the Anthropic Messages plus OpenAI Responses translation that this manager currently relies on, so it should not be substituted without a protocol compatibility test.
