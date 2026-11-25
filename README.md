# Claude & Codex Agent 工作台

首次启动可设置 Codex 和 Claude 的权限。推荐 Codex「项目内写入，允许联网」与 Claude「标准审批」。两者的高权限选项需要明确确认。「工具与插件」可分别接入私有 MCP、Skills 和 Claude 本地插件，桌面端可启用主屏幕读取。详见[权限与工具能力](docs/agent-permissions.zh-CN.md)和[工具接入说明](docs/tool-bridge.zh-CN.md)。

Mac / Windows 终端支持粘贴微信等截图工具复制的图片，保存为工作台私有 PNG 后交给当前对话，不自动提交。Mac 使用 ⌘V / Ctrl+V，Windows 使用 Ctrl+V；需要支持图片的模型和网关。详见[截图粘贴](docs/tool-bridge.zh-CN.md#微信等截图工具粘贴)。

Mac 模型列表读取失败时，会保留同一调用配置和网关上次的模型选择；点模型旁的刷新按钮可重试，点模型选择可查看具体错误。没有模型目录时可手动填写模型 ID；鉴权、网络等读取错误会明确显示，也可在添加时取消「保存时读取模型列表」，以未验证状态保存。已有终端继续使用启动时的配置。

Android 可通过 SSH 接续新版 Mac / Windows 工作台内的同一终端，使用电脑项目、配置和历史，或从手机传入文件/图片。电脑端在「手机连接」中启用；手机断开不结束电脑任务。工具与权限设置面板暂留在电脑端，实机验收边界见[手机接续电脑工作台](docs/remote-workbench.zh-CN.md)。

## 下载与上手

三端支持 Catppuccin 浅色、深色和跟随系统；切换不重启终端。Mac 项目标签可拖动排序或右键移动，详见[外观与项目标签](docs/appearance.zh-CN.md)。

从 [GitHub Releases](https://github.com/Cass-ette/claude-cchp-manager/releases) 下载客户端。Windows、macOS 和 Android 的默认安装包都不带 CCHP 服务端、Docker 或本地模型。

| 平台 | 下载 | 另外需要什么 |
| --- | --- | --- |
| Windows 10/11 x64 | Windows x64 安装程序（`*-setup.exe`） | WebView2（缺少时联网补装）和所用的 Claude Code 或 Codex CLI。 |
| macOS 14+（Apple Silicon / Intel） | macOS 通用 ZIP | Python 3 和所用的 CLI。 |
| Android 8+ | Android APK | Termux。用应用内的安装向导补齐 Debian 与 CLI。 |

支持应用内更新的版本可以在 macOS 左侧栏、Windows 窗口右上角或 Android 顶栏打开常驻的「检查更新」入口，发现新版时显示版本号。macOS 应用菜单也保留同一入口。查看更新说明后可以下载，等任务完成再安装。原地升级保留工作台配置、Key 和对话历史。也可以从发布页手动下载。发布流程见[三端更新与发布](docs/updates.zh-CN.md)。

**不用 CCHP 也能用。** 可以直连兼容的模型 API 或第三方网关，填写该服务的地址和它签发的调用 Key。支持自定义 API 前缀（例如 `/proxy/v1`、`/api/paas/v4`），不会重复拼接 `/v1`。裸域名仍默认使用 `/v1`；仅根路径 `/v1` 维持旧版的地址规范化，避免改变历史配置身份。模型列表可选：依次尝试 API 前缀下的 `/models`，未提供时再尝试 `/v1/models`；404/405/501 会提示手动填写 ID，401/403、网络和重定向错误仍显示。添加配置可取消读取列表，此时 Key 与模型尚未验证。启动不依赖模型目录或名单；实际鉴权、模型可用性和协议由 CLI 请求确认。Claude Code 需要 Anthropic Messages（`/v1/messages`），Codex 需要 OpenAI Responses（`/v1/responses`）。只提供 Chat Completions 的接口不能直接驱动这两个客户端。CCHP 专属的余额和账单功能需要网关提供对应接口；不影响普通模型调用。

**模型目录与调用地址可分开设置（三端）。** 默认勾选「模型目录与调用共用地址」，旧配置继续使用上述发现规则。服务商把目录和推理放在不同入口时，取消勾选并填写「模型目录完整 URL」（包含 `/models`，也支持服务商自己的目录路径）。这个地址只用于 GET 列表，使用同一把调用 Key；请填写可信服务商地址，远程需 HTTPS，重定向不会转发 Key。目录为空或不可用仍可手填模型 ID；目录列出了模型，也不表示该 Key 已有调用权限。

例如智谱的 Codex 调用地址为 `https://open.bigmodel.cn/api/v1`，独立模型目录为 `https://open.bigmodel.cn/api/paas/v4/models`。macOS 在左下网关编辑中修改，Windows 在配置旁的「连接设置」、Android 在配置卡片的「连接设置」修改；新增配置也有同样选项。重新勾选共用即可清除独立目录。修改保留配置 ID、Key、收藏与历史，后续启动使用新的调用地址，已运行的终端继续使用原配置。模型目录不会写入 CLI 推理路由，也不改变会话隔离身份；macOS 模型缓存同时区分目录来源。

**对话归档（三端）。** 历史窗口默认显示「未归档」，每条对话可「归档」或「取消归档」，切换到「已归档」后仍可搜索、按目录查找，以及按原配置恢复或从当前项目创建分支。「恢复最近对话」跳过已归档项。归档只写工作台自己的显示状态，不移动或删除原始日志，不改会话 ID、Key、模型与配置，不停止运行终端，也不影响用量统计。手机通过 SSH 浏览电脑历史时使用电脑端的归档状态；手机本地 Debian 历史单独保存。旧版电脑需要更新后才提供归档操作，完整虚拟机尚未开放的历史浏览能力不因此改变。

- [Windows 中文快速上手与排错](windows/QUICKSTART.zh-CN.md)
- [模型切换、思考强度滑条与会话内调整](docs/model-and-effort.zh-CN.md)
- [Windows 源码、开发与构建说明](windows/README.md)
- 本项目采用 [MIT 许可证](LICENSE)，第三方组件保留各自许可证。
- 每个人使用自己的配置和凭据；`127.0.0.1` 指使用者当前的电脑，不会自动连接作者的网关。
- [三端连接远程网关与迁移排错](docs/remote-gateway.zh-CN.md)：Mac、Windows、Android 均支持直接连接 HTTPS 网关。

## 运行边界与会话迁移

本项目的核心边界是“工作台内隔离，系统外保持原样”。在 macOS 和 Windows 上，选好客户端后，可以在“运行范围”切到“系统”，用同一个“启动”按钮在所选项目目录运行系统已有的 `claude` 或 `codex`。客户端沿用自己的登录和配置，普通终端里的会话独立运行。

局部会话与全局会话都支持显式的双向交接设计：局部会话可以通过临时 branch、独立配置目录或 memory handoff fork 到全局入口；全局会话也可以由用户明确选择后 fork/import 到工作台自己的分支和配置目录。交接复制的是所需的会话上下文，源会话和源配置保持不变，不共享会造成 writer lock 冲突的目录。CLI 是否提供完整的历史 fork 能力仍以实际版本为准，无法 fork 时应降级为显式记忆交接，而不是直接写入另一侧的配置目录。

Android 默认没有 root，因此不定义手机系统级“全局部署”或“全局命令”。工作台只能在自己的应用范围、Termux 用户空间、Debian/虚拟机或用户明确连接的远程主机中运行。连接新版 Mac / Windows 工作台后，手机可显示电脑的「系统 CLI」入口；它使用电脑上的 CLI、配置、项目和历史，不会把手机变成全局环境。其他主机继续通过普通 SSH 操作。

局部跨模型中转当前依赖 CCHP，尚未轻量化。后续可以按类似 CCHP 的协议中转方式让局部 Claude/Codex 调用原本不适配的模型，但 Android 是否能稳定运行该中转层仍需按 ABI、后台策略、网络和资源占用逐项验证，当前不承诺 Android 支持，也不把这项能力作为安装前置条件。

## macOS

macOS SwiftUI terminal-first Agent workbench for Claude Code and Codex CLI. It keeps real coding sessions in independent embedded PTY tabs, while gateway and model settings stay in reusable task profiles. A private CCHP deployment is optional; each saved key profile can point at its own local or HTTPS gateway.

The repository includes a separate [optional CCHP deployment template](deployment/cchp-local/README.md). It is not part of any client download.

## Features

- Save multiple gateway keys and their gateway URLs separately; remove custom key profiles from the sidebar when no longer needed. Removing a profile also deletes its local read-only billing token while preserving conversation history; the built-in free profile cannot be removed.
- Remember the selected profile, model, workspace, and session mode.
- After the first task starts, the setup area collapses into a compact task context bar so the running agents remain the focus; expand it only when creating the next task.
- Choose a recent or new working directory and start a new, continued, or resumed Claude session.
- Run the selected Claude or Codex process inside an interactive shell in the embedded SwiftTerm PTY. Ctrl+C stops the foreground Agent and returns to that tab's shell; closing the tab or using Stop ends the whole terminal process.
- Keep multiple independent PTY tabs running at once. Each captures its own client, key, model, workspace, session mode, and gateway.
- Share terminal font, palette, cursor, and scrollback settings across all PTY tabs through one editable `terminal.json` file in Application Support.
- Use the four-corner button in the terminal header to enter macOS full screen; the running PTY stays attached to the same agent process.
- Switch between Claude and Codex in one window. Codex gets a per-key isolated `CODEX_HOME`, provider config, and session history, so the regular `~/.codex` installation is not changed.
- **恢复最近对话（Mac / Windows）**：左侧可按最近时间或目录浏览本机的工作台和系统 CLI 历史。目录树只列出通往已有对话的目录。点击一条对话即在原项目用原配置打开。右侧同名入口只恢复当前项目、当前客户端的最新对话。原配置、Key 或项目目录无法使用时，应用会说明恢复失败。
- **从历史创建分支**：右侧列出当前项目的历史。选中来源，再选工作台配置和模型，确认后会创建独立会话，即使使用同一配置也会新建。原对话可以继续恢复。选择系统历史作为来源，可将上下文复制到工作台分支。确认界面会显示目标网关，启动后模型可能收取费用。Codex 目标需支持 Responses，Claude 目标需支持 Messages。
- **Android 历史**：Debian 可以浏览各项目的对话，也可以在当前项目恢复原对话或创建分支。Linux VM 使用虚拟机内当前配置的对话。SSH 使用远端 CLI 自己的历史。
- **手机连接（Mac / Windows）**：在桌面工作台复制连接码，在 Android「运行环境 → 添加电脑」粘贴。首次通过电脑账号登录，可由应用自动保存此手机的专用凭据。配对与换 Wi-Fi 后的自动找回仅支持两端所在的同一局域网；应用按已配对的 SSH 主机密钥验证电脑并更新地址，不提供公网中继或 Tailscale 接入。具体步骤见 [Android 连接电脑](android/README.md#连接电脑)。
- Keep all manager keys and Claude settings in the existing private local config directory.
- **用量小窗（三端）**：点击 macOS／Windows 状态栏用量摘要，或 Android 配置页的「查看用量与费用」。可组合筛选项目目录、配置档案、客户端、实际记录的模型、单个会话与 UTC 日期范围（全部、今天、近 7／30 天、自定义），查看输入／输出、缓存读写、推理 Token 和每日柱状图。项目按完整目录路径区分，默认只包含此目录；勾选「包含子目录」才合并后代目录。目录缺失显示「未关联项目」，配置身份不明确显示「未识别配置」，不按当前选中的 Key 猜测历史归属。
- **费用只取上游提供的信息**：日志明确保留上游 USD 金额时展示；只有上游明确单价时，按该次调用用量计算并标注。都没有则显示「费用未提供」，不套应用内参考单价、不把 CLI 的客户端估价当成上游实扣、不依赖管理 Token。费用缺失不是零；部分费用仅合计已知部分，零费用保留为零。标准 OpenAI／Anthropic usage 只保证各自定义的 Token 字段，不保证提供费用／单价；CLI 可能不保存服务商扩展字段，因此历史费用不能保证追溯。
- 扫描器按响应 ID 去重流式片段和会话副本；累计快照只计增量，恢复／分支继承记录不重复计费。同一会话的不同模型按各自记录分开筛选；没有记录模型时保持未知，无法归属到某次调用的累计差额不强归到末次模型或目录。缓存属于输入、推理属于输出，不重复叠加。缺时间戳记录只纳入「全部」，不强行归入某日。每台设备只读本机隔离日志，保存可重建的脱敏用量缓存，日志或配置变化后重新扫描；不输出对话正文、调用 Key 或 Key 指纹，不加入设备筛选。Android 本机统计不混入 SSH 电脑或未同步的 VM 日志。
- Read CCHP account balances, spendable credit, published group ratio, and quota rules using the selected profile's existing LLM key. Gateway metadata refreshes at most once per minute, or on manual refresh. Other compatible inference gateways can still be used when they do not expose this optional CCHP account API.
- Optionally connect a control-plane URL and a read-only management token for actual gateway billing. The panel shows today's UTC spend and the latest 20 requests in the last 24 hours, explicitly scoped to that management user's keys, not to one local session. Pending or absent prices remain unknown; exact decimal amounts and genuine zero charges are preserved. This is an advanced optional account-wide connection; it is not required to read current-Key public selling prices and is not a per-session receipt.

Adding a key sends it to the local Python backend over stdin. The backend queries only `GET /v1/models` and stores the key and profile data in `~/.config/cchp-client/claude-test/` with restrictive permissions. It does not send an inference request. Keys, runtime configuration, and conversation data are not part of this repository.

Launching Claude may incur local CCHP balance and upstream charges. Resuming a session requires confirmation because its history is sent to the selected key and upstream. The app does not edit the user's regular Claude or Codex configuration.

To enable billing details, click the status-bar balance, expand “账户级账单连接（高级，可选）”, choose “连接只读账户账单…”, and enter the CCHP **control-plane** URL (for the default local deployment, port 8080) and a read-only `cch_auth_live_…` management token. Port 8081 is the inference gateway. This connection makes GET requests only, stores its credential in a private mode-600 file under the local client config, and refuses HTTP redirects. A connection is bound to its original profile and gateway; changing the gateway disables that saved connection. “断开并删除本机凭据” removes the saved credential and its previous-version backup. No management token or billing record is included in the application bundle.

Claude requires an Anthropic Messages-compatible gateway. Codex requires an OpenAI Responses-compatible gateway. The manager accepts custom API base paths and optional model catalogs; a model ID can also be entered manually. Catalog availability does not prove protocol compatibility. Most gateways do not publish reliable tool-use or image capability metadata; model-list filters based on names are candidates only. Mark models you have verified as favorites for each key and client. Codex gets a `CODEX_HOME` isolated by profile, model, gateway, and workspace, and passes its key only to the child process. Its history is separate from the regular Codex desktop/TUI history.

## Build

Requirements: macOS 14+, Swift 6.2+, Python 3, and whichever CLI you intend to use (`~/.local/bin/claude` and/or `~/.local/bin/codex`). The build script produces a universal arm64/x86_64 application. The included Docker Compose template is one optional gateway deployment path.

For remote HTTPS metadata queries, the macOS app prefers Homebrew or Miniconda Python over the system Python, whose older LibreSSL may fail TLS negotiation even when a browser connects successfully. Set `CCHP_MANAGER_PYTHON` in the app's launch environment to choose a specific Python executable. This affects only the app's metadata backend, not the configured gateway or the Agent CLI. Windows uses Rustls and Android uses the system HTTPS implementation; neither depends on macOS Python.

Run `./build-app.command`. The app bundle is written to `build/CCHP Model Manager.app`. Run `./package-friend.command` to create a client-only ZIP. This ad-hoc signed build is not notarized; broad distribution requires an Apple Developer ID signature and notarization.

The app includes the macOS SwiftTerm source at commit `282c3c3a932b55c129628876b70f37d993af1615` (tag `1.99.3`) under its MIT license. The local target omits SwiftTerm's unrelated cross-platform package dependencies, which otherwise make SwiftPM fetch their complete Git histories on macOS. License and provenance are in `Vendor/SwiftTerm/UPSTREAM.md`.

## Optional self-hosted gateway

Run `deployment/cchp-local/setup.command` on the friend's Mac after installing and starting Docker Desktop to deploy their own CCHP instance. The script generates a new `.env`, avoids occupied host ports, starts the official CCHP containers, and opens the local control panel. The friend then adds their own upstream provider keys in CCHP and creates a downstream LLM key for this manager. The manager must receive a gateway-issued downstream key, never an upstream provider key. Existing compatible gateways can be configured directly without deploying CCHP.

The related project [ding113/claude-code-hub](https://github.com/ding113/claude-code-hub) is a separate MIT-licensed CC Hub implementation. It is not the CCHP Plus image used by the deployment template. Its documented public endpoint is primarily OpenAI Chat Completions and it does not promise the Anthropic Messages plus OpenAI Responses translation that this manager currently relies on, so it should not be substituted without a protocol compatibility test.

## Android

Native Jetpack Compose implementation is under `android/`. It targets local Termux execution, profiles, model selection, embedded PTY tabs, touch scrolling, and local Token/费用 summaries when CLI logs provide usage data. Debian Shell shares the Agents' development environment; an optional Alpine Linux VM provides a separate Docker-capable environment when the device ABI and resources allow it; Termux Shell remains available for host maintenance. Wide tablets can show two independent terminals side by side. Full-screen applications with mouse tracking receive wheel events to scroll their own history; ordinary Shells retain local scrollback. Android Studio is not required when Android SDK/JDK 17 and Gradle are installed. Build with `cd android && ./gradlew :app:assembleDebug :app:testDebugUnitTest`. Model/account metadata and local usage are read-only; a task starts an actual CLI and can incur model usage charges. Termux and its CLI tools are user-installed; first-run Termux setup is explicit, never automatic. The Termux-to-app bridge binds only to loopback and requires a random local token. SSH to a computer remains available when local ABI, battery, memory, or background limits make local Agent execution unsuitable. Device-specific VM boot, SSH, and real gateway/model end-to-end validation remain outstanding; installing the APK does not imply those modes work on every device.

## 测试版与更新

日常构建发布到 [GitHub Releases](https://github.com/Cass-ette/claude-cchp-manager/releases)，允许下载安装。客户端按版本号显示成熟度：`0.x.y` 是测试版，`1.0.0` 起是正式版；GitHub 的 Pre-release / Latest 标签不控制更新发现。macOS、Windows、Android 优先读取固定更新清单，失败时回退 Release 清单。升级保留原用户目录中的配置、Key 和历史，安装由用户确认，不会在检查或下载时重启终端。

旧客户端若无法发现测试版，请先从发布页手动覆盖安装一次。Android 必须使用与现有安装同签名的包。详见 [三端更新与发布](docs/updates.zh-CN.md)。
