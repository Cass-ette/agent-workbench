# Claude & Codex Agent 工作台

## 下载与上手

从 [GitHub Releases](https://github.com/Cass-ette/claude-cchp-manager/releases) 下载客户端。Windows、macOS 和 Android 的默认安装包都不带 CCHP 服务端、Docker 或本地模型。

| 平台 | 下载 | 另外需要什么 |
| --- | --- | --- |
| Windows 10/11 x64 | Windows x64 安装程序（`*-setup.exe`） | WebView2（缺少时联网补装）和所用的 Claude Code 或 Codex CLI。 |
| macOS 14+（Apple Silicon / Intel） | macOS 通用 ZIP | Python 3 和所用的 CLI。 |
| Android 8+ | Android APK | Termux。用应用内的安装向导补齐 Debian 与 CLI。 |

支持应用内更新的版本可以在 macOS 的「Agent 工作台」菜单、Windows 窗口右上角或 Android 顶栏打开「检查更新」。查看更新说明后可以下载，等任务完成再安装。原地升级保留工作台配置、Key 和对话历史。也可以从发布页手动下载。发布流程见[三端更新与发布](docs/updates.zh-CN.md)。

**不用 CCHP 也能用。** 可以直连兼容的模型 API 或第三方网关，填写该服务的地址和它签发的调用 Key。工作台通过 `/v1/models` 读取列表；Claude Code 需要 Anthropic Messages（`/v1/messages`），Codex 需要 OpenAI Responses（`/v1/responses`）。只提供 Chat Completions 的接口不能直接驱动这两个客户端。CCHP 专属的余额和账单功能需要网关提供对应接口；不影响普通模型调用。

- [Windows 中文快速上手与排错](windows/QUICKSTART.zh-CN.md)
- [Windows 源码、开发与构建说明](windows/README.md)
- 本项目采用 [MIT 许可证](LICENSE)，第三方组件保留各自许可证。
- 每个人使用自己的配置和凭据；`127.0.0.1` 指使用者当前的电脑，不会自动连接作者的网关。
- [三端连接远程网关与迁移排错](docs/remote-gateway.zh-CN.md)：Mac、Windows、Android 均支持直接连接 HTTPS 网关。

## 运行边界与会话迁移

本项目的核心边界是“工作台内隔离，系统外保持原样”。在 macOS 和 Windows 上，选好客户端后，可以在“运行范围”切到“系统”，用同一个“启动”按钮在所选项目目录运行系统已有的 `claude` 或 `codex`。客户端沿用自己的登录和配置，普通终端里的会话独立运行。

局部会话与全局会话都支持显式的双向交接设计：局部会话可以通过临时 branch、独立配置目录或 memory handoff fork 到全局入口；全局会话也可以由用户明确选择后 fork/import 到工作台自己的分支和配置目录。交接复制的是所需的会话上下文，源会话和源配置保持不变，不共享会造成 writer lock 冲突的目录。CLI 是否提供完整的历史 fork 能力仍以实际版本为准，无法 fork 时应降级为显式记忆交接，而不是直接写入另一侧的配置目录。

Android 默认没有 root，因此不定义“全局部署”或“全局命令”这一模式。工作台只能在自己的应用范围、Termux 用户空间、Debian/虚拟机或用户明确连接的远程主机中运行；不能注册系统级 PATH、修改其他应用的配置、接管系统终端，也不能把 Android 上的局部 CLI 假装成全局 CLI。需要接近桌面全局体验时，使用 SSH 连接 Mac、Windows 或 Linux 主机，在远端工作区启动远端已有的 Claude/Codex；远端使用自己的 CLI、配置、项目和历史，手机工作台只提供终端入口和连接管理。

局部跨模型中转当前依赖 CCHP，尚未轻量化。后续可以按类似 CCHP 的协议中转方式让局部 Claude/Codex 调用原本不适配的模型，但 Android 是否能稳定运行该中转层仍需按 ABI、后台策略、网络和资源占用逐项验证，当前不承诺 Android 支持，也不把这项能力作为安装前置条件。

## macOS

macOS SwiftUI terminal-first Agent workbench for Claude Code and Codex CLI. It keeps real coding sessions in independent embedded PTY tabs, while gateway and model settings stay in reusable task profiles. A private CCHP deployment is optional; each saved key profile can point at its own local or HTTPS gateway.

The repository includes a separate [optional CCHP deployment template](deployment/cchp-local/README.md). It is not part of any client download.

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
- **对话历史（Mac / Windows / Android）**：打开「对话历史」，按标题、项目或模型找一条工作台会话，再选择目标调用配置和模型。恢复配置与历史记录的 Key profile、模型及已知网关一致时，直接续接原会话，不会多建分支；更换 Key、模型或已知网关时，会先复制到工作台私有目录并 fork，原记录保持可恢复。全局导入记录始终 fork。确认框会显示目标网关；继续时会把所需上下文发往这个网关，可能产生费用。旧记录缺少可靠来源信息时会保守 fork 一次；生成的分支带有配置标记，之后相同配置可直接续接。Codex 目标需支持 Responses，Claude 目标需支持 Messages。
- 默认只读取工作台自己的本地历史；外部 CLI、官方桌面端和 SSH 远端不会被静默导入。用户点击“导入系统历史”后，Mac/Windows 会按只读方式扫描系统级 Claude/Codex 历史，并把选中的会话复制到工作台私有分支；源历史和配置保持不变。Codex 用于恢复的父历史副本保留在工作台私有目录中，不会重复显示；Claude 的临时副本随终端进程正常结束清理。后续继续某个分支时，请从「对话历史」选中它。「继续最近会话」仍只看常规启动所用的目录。Claude 恢复需要原项目路径；首次启动分支时，CLI 可能要求确认 API Key 和项目目录。
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

For remote HTTPS metadata queries, the macOS app prefers Homebrew or Miniconda Python over the system Python, whose older LibreSSL may fail TLS negotiation even when a browser connects successfully. Set `CCHP_MANAGER_PYTHON` in the app's launch environment to choose a specific Python executable. This affects only the app's metadata backend, not the configured gateway or the Agent CLI. Windows uses Rustls and Android uses the system HTTPS implementation; neither depends on macOS Python.

Run `./build-app.command`. The app bundle is written to `build/CCHP Model Manager.app`. Run `./package-friend.command` to create a client-only ZIP. This ad-hoc signed build is not notarized; broad distribution requires an Apple Developer ID signature and notarization.

The app includes the macOS SwiftTerm source at commit `282c3c3a932b55c129628876b70f37d993af1615` (tag `1.99.3`) under its MIT license. The local target omits SwiftTerm's unrelated cross-platform package dependencies, which otherwise make SwiftPM fetch their complete Git histories on macOS. License and provenance are in `Vendor/SwiftTerm/UPSTREAM.md`.

## Optional self-hosted gateway

Run `deployment/cchp-local/setup.command` on the friend's Mac after installing and starting Docker Desktop to deploy their own CCHP instance. The script generates a new `.env`, avoids occupied host ports, starts the official CCHP containers, and opens the local control panel. The friend then adds their own upstream provider keys in CCHP and creates a downstream LLM key for this manager. The manager must receive a gateway-issued downstream key, never an upstream provider key. Existing compatible gateways can be configured directly without deploying CCHP.

The related project [ding113/claude-code-hub](https://github.com/ding113/claude-code-hub) is a separate MIT-licensed CC Hub implementation. It is not the CCHP Plus image used by the deployment template. Its documented public endpoint is primarily OpenAI Chat Completions and it does not promise the Anthropic Messages plus OpenAI Responses translation that this manager currently relies on, so it should not be substituted without a protocol compatibility test.

## Android

Native Jetpack Compose implementation is under `android/`; the current development APK is 0.1.19. It targets local Termux execution, profiles, model selection, embedded PTY tabs, touch scrolling, and local Token/费用 summaries when CLI logs provide usage data. Debian Shell shares the Agents' development environment; an optional Alpine Linux VM provides a separate Docker-capable environment when the device ABI and resources allow it; Termux Shell remains available for host maintenance. Wide tablets can show two independent terminals side by side. Full-screen applications with mouse tracking receive wheel events to scroll their own history; ordinary Shells retain local scrollback. Android Studio is not required when Android SDK/JDK 17 and Gradle are installed. Build with `cd android && ./gradlew :app:assembleDebug :app:testDebugUnitTest`. Model/account metadata and local usage are read-only; a task starts an actual CLI and can incur model usage charges. Termux and its CLI tools are user-installed; first-run Termux setup is explicit, never automatic. The Termux-to-app bridge binds only to loopback and requires a random local token. SSH to a computer remains available when local ABI, battery, memory, or background limits make local Agent execution unsuitable. Device-specific VM boot, SSH, and real gateway/model end-to-end validation remain outstanding; installing the APK does not imply those modes work on every device.

## 测试版与更新

日常构建默认发布为 [GitHub 测试版（Pre-release）](https://github.com/Cass-ette/claude-cchp-manager/releases)，允许下载安装；只有维护者明确确认后才发布正式版。macOS、Windows、Android 的应用内更新会发现带完整清单的最新发布，并明确显示测试版标记。升级保留原用户目录中的配置、Key 和历史，安装由用户确认，不会在检查或下载时重启终端。

旧客户端若无法发现测试版，请先从发布页手动覆盖安装一次。Android 必须使用与现有安装同签名的包。详见 [三端更新与发布](docs/updates.zh-CN.md)。
