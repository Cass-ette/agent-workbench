# Claude & Codex Agent 工作台

## Windows 下载与上手

Windows 朋友请从 [Windows 发布页](https://github.com/Cass-ette/claude-cchp-manager/releases/tag/windows-v0.1.3) 下载 `Agent-Workbench-Windows-x64-0.1.3-setup.exe`。普通使用无需安装 Rust、Visual Studio 或克隆源码；仍需安装准备使用的 Claude Code / Codex CLI，并提供自己的兼容网关地址和调用 Key。

- [Windows 中文快速上手与排错](windows/QUICKSTART.zh-CN.md)
- [Windows 源码、开发与构建说明](windows/README.md)
- 本项目采用 [MIT 许可证](LICENSE)，第三方组件保留各自许可证。
- 每个人使用自己的配置和凭据；`127.0.0.1` 指使用者当前的电脑，不会自动连接作者的网关。

## macOS

macOS SwiftUI terminal-first Agent workbench for Claude Code and Codex CLI. It keeps real coding sessions in independent embedded PTY tabs, while gateway and model settings stay in reusable task profiles. A private CCHP deployment is optional; each saved key profile can point at its own local or HTTPS gateway.

The repository now includes a no-secret CCHP deployment template under `deployment/cchp-local/`. It creates a separate local CCHP database and runtime key material for each user; it never packages the author's `.env`, Docker volumes, upstream provider keys, or downstream keys.

## Features

- Save multiple gateway keys and their gateway URLs separately; changing one profile does not retarget the others.
- Remember the selected profile, model, workspace, and session mode.
- After the first task starts, the setup area collapses into a compact task context bar so the running agents remain the focus; expand it only when creating the next task.
- Choose a recent or new working directory and start a new, continued, or resumed Claude session.
- Run the selected Claude or Codex process inside an interactive shell in the embedded SwiftTerm PTY. Ctrl+C stops the foreground Agent and returns to that tab's shell; closing the tab or using Stop ends the whole terminal process.
- Keep multiple independent PTY tabs running at once. Each captures its own client, key, model, workspace, session mode, and gateway.
- Share terminal font, palette, cursor, and scrollback settings across all PTY tabs through one editable `terminal.json` file in Application Support.
- Use the four-corner button in the terminal header to enter macOS full screen; the running PTY stays attached to the same agent process.
- Switch between Claude and Codex in one window. Codex gets a per-key isolated `CODEX_HOME`, provider config, and session history, so the regular `~/.codex` installation is not changed.
- Keep all manager keys and Claude settings in the existing private local config directory.
- Show input (including cache), output, cache reads/writes, and reasoning tokens from isolated local session logs. The status bar refreshes every 15 seconds on Mac (10 on Windows); click its summary for per-session estimates and the “网关售价” tab. When client logs omit USD amounts, calculate estimates from public catalog prices × the Key's disclosed group ratio, without a management token. Zero prices remain zero; hidden ratios, incomplete coverage, ranges, reference snapshots, and changed historic routes are labelled. The scanner makes no network requests and does not export conversation content or keys.
- Public price metadata refreshes while the app is running: check every five minutes, cache CCHP catalogs for six hours and direct DeepSeek official pricing for 24 hours, with a manual refresh button. DeepSeek official peak/off-peak prices are shown as a range when a request's applicable band is not known. CCHP gateway-published prices take precedence for gateway calls, including a DeepSeek official provider routed through CCHP. A failed fetch retains the last catalog; bundled, deployment-neutral references provide an offline fallback. The public source URL is configurable per Key and receives no credentials.
- These are current-price estimates of recorded historic tokens, not historic billing receipts or upstream procurement costs. Cache tokens are subtracted from ordinary input; reasoning is already part of output. Missing cache details use ordinary input pricing, cumulative-only differences use the last known model, and unknown tool/service-tier charges are excluded. Confirmed gateway prices are labelled “网关单价估算”; bundled or official reference prices and uncertain historic routes are labelled “参考估算”. Client-only amounts stay “客户端估算”, and mixed totals are labelled separately. Actual debit remains with the CCHP bill. No model inference is needed to query prices. Upstream procurement, discounts, and price synchronization belong to CCHP administration and are outside this app's downstream pricing workflow.
- Read CCHP account balances, spendable credit, published group ratio, and quota rules using the selected profile's existing LLM key. Gateway metadata refreshes at most once per minute, or on manual refresh. Other compatible inference gateways can still be used when they do not expose this optional CCHP account API.
- Optionally connect a control-plane URL and a read-only management token for actual gateway billing. The panel shows today's UTC spend and the latest 20 requests in the last 24 hours, explicitly scoped to that management user's keys, not to one local session. Pending or absent prices remain unknown; exact decimal amounts and genuine zero charges are preserved. This is an advanced optional account-wide connection; it is not required to read current-Key public selling prices and is not a per-session receipt.

Adding a key sends it to the local Python backend over stdin. The backend queries only `GET /v1/models` and stores the key and profile data in `~/.config/cchp-client/claude-test/` with restrictive permissions. It does not send an inference request. Keys, runtime configuration, and conversation data are not part of this repository.

Launching Claude may incur local CCHP balance and upstream charges. Resuming a session requires confirmation because its history is sent to the selected key and upstream. The app does not edit the user's regular Claude or Codex configuration.

To enable billing details, click the status-bar balance, expand “账户级账单连接（高级，可选）”, choose “连接只读账户账单…”, and enter the CCHP **control-plane** URL (for the default local deployment, port 8080) and a read-only `cch_auth_live_…` management token. Port 8081 is the inference gateway. This connection makes GET requests only, stores its credential in a private mode-600 file under the local client config, and refuses HTTP redirects. A connection is bound to its original profile and gateway; changing the gateway disables that saved connection. “断开并删除本机凭据” removes the saved credential and its previous-version backup. No management token or billing record is included in the application bundle.

Claude requires an Anthropic Messages-compatible gateway. Codex requires an OpenAI Responses-compatible gateway. The manager checks model IDs through `/v1/models`, but most gateways do not publish reliable tool-use or image capability metadata; model-list filters based on names are candidates only. Mark models you have verified as favorites for each key and client. Codex gets a `CODEX_HOME` isolated by profile, model, gateway, and workspace, and passes its key only to the child process. Its history is separate from the regular Codex desktop/TUI history.

## Build

Requirements: macOS 14+, Swift 6.2+, Python 3, and whichever CLI you intend to use (`~/.local/bin/claude` and/or `~/.local/bin/codex`). The build script produces a universal arm64/x86_64 application. The included Docker Compose template is one optional gateway deployment path.

Run `./build-app.command`. The app bundle is written to `build/CCHP Model Manager.app`. Run `./package-friend.command` to create a ZIP containing the app and a fresh local CCHP setup template. This ad-hoc signed build is not notarized; broad distribution requires an Apple Developer ID signature and notarization.

The app includes the macOS SwiftTerm source at commit `282c3c3a932b55c129628876b70f37d993af1615` (tag `1.99.3`) under its MIT license. The local target omits SwiftTerm's unrelated cross-platform package dependencies, which otherwise make SwiftPM fetch their complete Git histories on macOS. License and provenance are in `Vendor/SwiftTerm/UPSTREAM.md`.

## Friend deployment

Run `deployment/cchp-local/setup.command` on the friend's Mac after installing and starting Docker Desktop to deploy their own CCHP instance. The script generates a new `.env`, avoids occupied host ports, starts the official CCHP containers, and opens the local control panel. The friend then adds their own upstream provider keys in CCHP and creates a downstream LLM key for this manager. The manager must receive a gateway-issued downstream key, never an upstream provider key. Existing compatible gateways can be configured directly without deploying CCHP.

The related project [ding113/claude-code-hub](https://github.com/ding113/claude-code-hub) is a separate MIT-licensed CC Hub implementation. It is not the CCHP Plus image used by the deployment template. Its documented public endpoint is primarily OpenAI Chat Completions and it does not promise the Anthropic Messages plus OpenAI Responses translation that this manager currently relies on, so it should not be substituted without a protocol compatibility test.

## Android

Native Jetpack Compose implementation is under `android/`; the current development APK is 0.1.10. It targets local Termux execution, profiles, model selection, embedded PTY tabs, touch scrolling, and local Token/费用 summaries when CLI logs provide usage data. Full-screen applications with mouse tracking receive wheel events to scroll their own history; ordinary Shells retain local scrollback. Android Studio is not required when Android SDK/JDK 17 and Gradle are installed. Build with `cd android && ./gradlew :app:assembleDebug :app:testDebugUnitTest`. Model/account metadata and local usage are read-only; a task starts an actual CLI and can incur model usage charges. Termux and its CLI tools are user-installed; first-run Termux setup is explicit, never automatic. The Termux-to-app bridge binds only to loopback and requires a random local token. Remote Mac/Windows/host execution and full Linux VMs are future work; installing the APK does not imply those modes are available.
