# Agent Workbench for Windows

Windows desktop client for running Claude Code or Codex CLI in independent PowerShell terminal tabs. Each task keeps its own model, project directory, client, and session mode.

## Requirements

For normal use, download the Windows x64 installer from the [Windows release](https://github.com/Cass-ette/claude-cchp-manager/releases/tag/windows-v0.1.3) and follow the [Chinese quick start](QUICKSTART.zh-CN.md). Rust, Node.js and Visual Studio below are build requirements, not requirements of the installed desktop app itself. The selected CLI may have its own runtime requirements.

- Windows 10/11 with the WebView2 Evergreen Runtime.
- Node.js LTS and Rust with the MSVC toolchain.
- Visual Studio Build Tools with the Desktop development with C++ workload to build installers.
- The selected agent CLI (`claude` or `codex`) installed and available on `PATH`.
- A compatible local gateway or HTTPS gateway with a downstream API key.

## Development

```powershell
npm ci
npm run tauri:dev
```

Run the interface regression tests with `npm test`. They use synthetic model lists and mocked desktop APIs; they do not read saved keys, contact a gateway, or call a model.

Build a Windows installer with:

```powershell
npm run tauri:build
```

Tauri writes the installer under `src-tauri/target/release/bundle/`.

## Local data

- Gateway names, addresses, selected model, and last project directory are stored in the app's configuration directory.
- Downstream API keys are stored in Windows Credential Manager and are not written to the configuration file.
- Codex settings and conversations are isolated under the app configuration directory, one persistent `CODEX_HOME` per gateway profile.
- Claude Code uses a separate `CLAUDE_CONFIG_DIR` per gateway profile.
- Adding a profile only requests `/v1/models`; it does not make an inference call.
- The status bar scans isolated Claude/Codex logs every 10 seconds. Click the summary for per-session input (including cache), output, cache reads/writes, and reasoning tokens, or refresh manually. Totals include restored history; missing fields appear as `—`. Public CCHP catalog prices × the current Key's disclosed group ratio provide “网关单价估算” without a management token. Official/bundled references and uncertain historic routes are labelled “参考估算”; explicit `total_cost_usd` records remain client estimates, and mixed totals are labelled separately. Unknown prices show “待匹配单价”, never a fabricated zero; actual charges remain with the CCHP bill. The scanner makes no network requests and emits no conversation content or keys.

For a local CCHP gateway, enter its downstream address (for example `http://127.0.0.1:8081`) and the CCHP-issued downstream key. Never enter an upstream provider key in this app.

## 首次使用

1. 点击左栏的 `＋`，输入配置名称、网关地址及该网关签发的下游调用 Key。
2. 点击“校验并保存”，读取模型列表。此步骤不会调用模型。
3. 选择 Claude Code 或 Codex、模型、Windows 本机项目目录，并把会话方式设为“新会话”。
4. 点击“启动终端”，在内嵌终端中使用所选客户端。启动后的实际模型请求可能产生上游费用。

`127.0.0.1` 始终指运行应用的这台电脑。如果网关在另一台电脑，需要填写 Windows 能访问的网关地址，或先建立 SSH 转发，再填写转发在 Windows 上监听的地址。SSH 转发断开或网关停止后，请求也会中断。

## Windows 凭据存储验证

0.1.1 显式启用 `keyring` 的 `windows-native` 后端，修复首版未持久保存调用 Key 的问题。

在已登录的 Windows 桌面终端中执行 `cargo test --manifest-path src-tauri/Cargo.toml`。凭据测试只创建随机测试值，验证重新打开凭据后仍可读取，并在结束时删除测试凭据，不读取实际调用 Key，也不调用模型。通过公钥 SSH 登录的会话可能无法访问 Windows 凭据管理器并返回错误 1312；此时应在交互式桌面会话中运行该测试。

## 模型选择修复（0.1.2）

修复切换模型或手动编辑项目目录时发生的界面崩溃。模型搜索不再截断前 160 项，改变搜索条件也会保留当前所选模型。状态栏会定时刷新本机隔离会话的 Token 历史累计，点击可看会话明细，缺失 Token 字段显示“—”。价格功能现已按下述 CCHP 下游口径展示估算。

## 模型列表恢复（0.1.3）

已有调用配置时，网络或凭据错误会在模型控件下方显示具体原因；网关返回空列表时显示“网关未返回可用模型”。这些情况都会保留已保存的模型，并可用右侧刷新按钮重新读取，无须重新添加配置。刷新失败也会保留本次已经读取成功的列表。只有尚无配置时才显示“先添加调用配置”。

## 网关售价与费用估算

“网关售价”显示当前调用 Key 所连接的 CCHP 公开目录基础价 × 公开分组倍率，倍率仅乘一次。经 CCHP 转发的 DeepSeek 也优先采用该网关售价。查看公开售价无需管理 Token。应用运行时每 5 分钟检查，公开目录缓存 6 小时，也可手动刷新或在高级设置指定该网关的公开目录地址。

本地 Token 按该售价计算的金额标为“网关单价估算”；官方或内置参考表、倍率未知、历史路由无法归属等结果标为“参考估算”。客户端日志自带金额保留为“客户端估算”，不同来源合计单独标注。真实零价格保留为零，缺价显示待匹配。当前价格重估历史不等于历史实扣，最终以 CCHP 账单为准。

上游价格同步、进货成本与优惠配置由 CCHP 管理端负责，工作台只负责下游售价、用量和账单口径的统一。Windows 安装包由本仓库 GitHub Actions 的 Windows runner 构建，构建日志及对应源码提交公开可查。
