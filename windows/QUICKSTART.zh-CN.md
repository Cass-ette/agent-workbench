# Windows 快速上手

Agent 工作台把 Claude Code / Codex CLI 放在同一窗口的独立终端标签页中，让每个任务记住模型、网关和项目。这里的模型仍通过 API 调用，工作台本身不提供免费模型额度。

有可用的网关地址和调用 Key，就不需要在 Windows 上安装 Docker、CCHP 或本地模型。安装包约 2.5 MB；WebView2 和所选 CLI 的磁盘占用另计。

工作台主程序约 7 MB。以 Windows x64 的 npm 发行包为例，Claude Code 2.1.328 的平台包解包约 380 MB，Codex 0.170.0 约 519 MB。只装要用的一个即可；这些数值不含 Node.js、下载缓存、临时文件和会话记录，其他安装方式也可能不同。Windows 11 通常已有 WebView2；缺少时需要另留安装空间。

## 下载安装

1. 打开 [GitHub Releases](https://github.com/Cass-ette/claude-cchp-manager/releases)，下载 `Agent-Workbench-Windows-x64-*-setup.exe`，在 Windows 10/11 x64 上运行安装。
2. 安装包需要 Microsoft WebView2；系统缺少它时，安装程序会联网安装。此版本未做商业代码签名，首次运行可能出现发布者确认，请核对下载来源及发布页提供的 SHA-256。
3. 提前安装准备使用的客户端，安装后重新打开工作台，让它读取更新后的 PATH：
   - [Claude Code 官方安装说明](https://code.claude.com/docs/en/setup)。
   - [Codex CLI 官方安装说明](https://developers.openai.com/codex/cli/)。
4. 普通使用无需 Rust、Visual Studio 或源码构建。源码 ZIP 不能当作 Windows 安装包运行。

## 第一次启动任务

1. 点击左栏 `＋` 添加调用配置，填写名称、自己的网关根地址和该网关签发的调用 Key。
2. 点击“校验并保存”，工作台查询 `/v1/models`，不会发起模型推理。
3. 选择客户端和“工作台”，再选模型和 Windows 本机项目文件夹。第一次使用选“新会话”。
4. 点击“启动”，在窗口内与 Agent 交互。每个终端可以选择不同的模型、客户端和项目。
5. 以后直接使用保存的调用配置；不必每次重新输入 Key。Ctrl+C 停止前台程序，关闭终端标签则结束该标签中的进程。

Claude 需要支持 Anthropic Messages 的网关，Codex 需要支持 OpenAI Responses 的网关。模型出现在列表中只代表 Key 可见，不保证它能驱动 Agent 工具。

如果平时已在 PowerShell 使用 `claude` 或 `codex`，可以跳过添加调用配置。选好客户端和“系统”，选择项目目录，点击同一个“启动”按钮。客户端会沿用系统已有的登录和配置。

## 网关是自己的

工作台可以直连兼容的模型 API 或 HTTPS 网关，不依赖 CCHP，也不依赖作者的电脑。填写该服务的地址和它签发的调用 Key。使用 CCHP 时才填 CCHP 地址和它签发的下游 Key。仅提供 Chat Completions 的服务不能直接驱动 Claude Code 或 Codex；请确认它支持上述对应协议及模型列表。

`http://127.0.0.1:8081` 只是本机网关地址的示例，不是公共服务。若网关在另一台电脑，请填写可访问的 HTTPS 地址，或先建立 SSH 隧道再填 Windows 本机转发地址。停止隧道后连接也会停止。

仓库中的 `deployment/cchp-local/setup.command` 是 macOS 部署脚本，不能直接在 Windows 双击使用。Windows 用户可以连接已有网关；自行部署网关需按网关自身的 Windows/Docker 文档操作。

## 配置与会话

- 调用 Key 保存在 Windows 凭据管理器中，普通配置文件不保存明文 Key。
- 普通配置、每把 Key 隔离的 Claude/Codex 会话位于 Windows 应用配置目录（通常为 `%APPDATA%\com.cassette.agentworkbench`）。
- 工作台不覆盖日常 `~/.claude`、`~/.codex` 配置；旧客户端会话不会自动迁入。
- 用量和金额展示有不同来源。按当前单价算出的金额是估算，实际扣款以网关账单为准；模型是否免费取决于你自己的 Key 和网关定价。

## 常见问题

| 现象 | 先检查什么 |
| --- | --- |
| `claude` 或 `codex` 无法识别 | 在新开的 PowerShell 中运行相应 `--version`；确认 CLI 已安装、PATH 生效，再重开工作台。 |
| 模型列表连接失败 | 检查网关地址、网关或 SSH 隧道是否运行；不要把别人的 `127.0.0.1` 当作远程地址。 |
| 401 / 403 | 在应用里核对当前网关的调用 Key、权限和有效期。 |
| 没有可选模型 | 先添加调用配置；已有配置时看具体错误并点刷新，不必反复重建。 |
| 选了模型但启动失败 | 检查该网关和模型是否支持所选客户端所需协议及工具。 |
| 旧对话找不到 | 工作台使用隔离会话，先确认调用配置与会话方式是否与创建时一致。 |

## 让 AI 帮忙理解或排查

可以把下面这段发给 AI：

> 请阅读公开仓库 https://github.com/Cass-ette/claude-cchp-manager ，重点看 windows/QUICKSTART.zh-CN.md、windows/README.md、windows/src/App.tsx 和 windows/src-tauri/src/lib.rs。我在 Windows 使用已发布的安装包。请全程用简体中文，先检查 CLI 是否安装、PATH、网关可达性和所需 API 协议，再一步步指导我。不修改原有 Claude/Codex 配置，不索取或输出完整 API Key；密钥让我在应用里填写。连通性检查先用版本和模型列表，发起付费推理前告诉我。

源码入口：React 页面在 `windows/src/`；Rust 终端、凭据和隔离启动逻辑在 `windows/src-tauri/src/lib.rs`；本地 Token 读取在 `usage.rs`；公开价目与估算在 `pricing.rs`。Windows 客户端不依赖 macOS Python 后端；编译时使用仓库 `backend/` 中的参考价格 JSON 以及 `tests/fixtures/` 中的测试样本。
