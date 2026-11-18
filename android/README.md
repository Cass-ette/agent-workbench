# Agent 工作台 Android 0.1.13

安卓原生工作台开发预览版：在手机或平板本地运行 Agent，Termux 承载后台进程，默认使用 Debian Linux 用户空间。模型来自用户选择的 API 网关。

## 首次使用

1. 安装并打开官方 GitHub [Termux v0.118.3 ARM64 APK](https://github.com/termux/termux-app/releases/download/v0.118.3/termux-app_v0.118.3%2Bgithub-debug_arm64-v8a.apk)。大多数近期手机、平板使用 ARM64；[其他架构与版本说明](https://github.com/termux/termux-app/releases/tag/v0.118.3)。已有 Termux 先保留，主程序与插件保持同源，不自动卸载环境。
2. 在工作台“运行环境”复制首次连接命令，在 Termux 粘贴一次：

   ```sh
   mkdir -p ~/.termux
   grep -qxF 'allow-external-apps=true' ~/.termux/termux.properties 2>/dev/null || printf '\nallow-external-apps=true\n' >> ~/.termux/termux.properties
   termux-reload-settings
   ```

3. 回工作台点“授权”，再点“一键安装并检查”。工作台自动检查并补齐 Python、bash、Git、OpenSSH、PRoot-Distro、Debian、Node.js、Codex 和 Claude。已有可用组件会复用，不清空项目，不自动重装损坏的 Debian。可展开日志查看阶段，失败后可重试。安装只下载工具和查询版本，不调用模型。
4. 在“调用配置”添加网关根地址与该网关签发的调用 Key，保留“保存时读取模型列表”。这是元数据查询，不发起推理。CCHP 的下游调用 Key 应搭配 CCHP 地址，上游 Key 应搭配它所属的上游地址。
5. 回“工作区”，选择调用配置、模型、Codex 或 Claude、手机项目目录与会话方式，点“启动 Agent”。无需手动进入 Debian 或输入启动命令。Claude 首次可能要求选择主题、确认使用自定义 API Key；项目访问仍由 CLI 提示确认。

如果模型列表提示 HTTP 401/403，先核对地址与 Key 是否属于同一网关、Key 是否仍有效；不要将管理 Token 当成调用 Key。工作台对模型网关发送 Bearer 与 `x-api-key` 兼容头，对本机桥接只发送其独立的 Bearer 凭据。

网关地址中的 `127.0.0.1` 指手机本身。电脑上仅绑定回环地址的网关，需要用户提供可访问的 HTTPS 地址或隧道；连接同一 Wi-Fi 不会自动建立转发。应用提供可信局域网 HTTP 例外，需要用户显式开启。

## 日常操作

- 保存的配置、模型缓存、收藏和最近项目在本机保留，模型列表可手动刷新、搜索或填写 ID。
- 同时最多 8 个终端；每次启动固定自己的客户端、模型、网关、项目和会话方式。
- Agent 在 Debian 中以普通用户 `workbench` 运行，手机项目目录绑定到 Debian 内相同路径。普通“Shell”入口目前打开 Termux Shell；Agent 退出后保留 Debian Shell。
- Ctrl+C 交给前台 CLI 处理，退出 Agent 后可继续使用同一终端；“关闭标签”才停止该终端进程。
- 原生 Android 输入法处理中文组词、退格与回车；支持终端全屏、物理按键和字体大小。
- 终端通过工具栏上下按钮及手指滑动浏览内容；右侧不再显示滚动条，也不保留其占位或触摸区域。拖动终端内容时不会误弹键盘。普通 Shell 滚动终端历史；Claude 等启用鼠标滚动的全屏程序收到原生滚轮事件，由程序滚动自己的对话历史。
- 手指直接拖动内容采用系统自然方向：上滑查看更晚内容，下滑查看更早内容。按每次事件的来源区分触摸与鼠标，鼠标滚轮保留原方向，鼠标拖动保留原行为；连接鼠标后手指仍使用触摸方向。上下按钮方向保持不变。
- 工具栏的“底部”按当前界面处理：Claude 的全屏会话使用 `Ctrl+End`，Codex 的会话记录页使用 `End`，Codex 普通视图与 Shell 只跳到终端历史底部。Codex 记录页须匹配已验证的标题及 `home/end to jump` 提示；不把 `End` 发进普通输入草稿。窄屏可横向滑动工具栏。
- “调用配置”页显示本地 Agent 日志中能读取到的输入/输出 Token、请求数和客户端费用估算；网关或 CLI 未提供金额时显示“未提供”，实际扣费仍以网关账单为准。
- 切后台暂停输出读取，Termux 进程继续运行；回前台恢复原标签与积压输出，短暂连接失败自动重试。保存过的桥接若已停止，前台会尝试重新启动桥接；系统已杀死的任务不能恢复，会提示移除失效标签。
- 更新运行环境会为新会话启动新版桥接，旧会话继续使用原端口与认证。不会为了更新而杀死旧任务。

Claude 使用固定版本支持的 `--bare` API Key 模式，避免普通首次启动对官方服务的连接检查。它会关闭自动记忆、自动插件同步和部分自动发现；工作台用 `--add-dir` 显式提供项目上下文，并固定各模型档位到所选模型，不偷偷切换其他模型。API/模型功能兼容性仍由实际网关决定。

在已测试的平板上，Codex 的 Linux 沙箱会让命令以退出码 182 失败。工作区提供默认关闭的“Codex 命令兼容模式”：阅读范围说明并确认后，可关闭这层沙箱，保留 `on-request` 按需审批；是否请求确认由模型决定，普通命令可能直接运行，不是逐条确认。此模式可以访问 Termux/已绑定的文件，不等同于只允许访问项目，也不是完整虚拟机。普通启动保持沙箱，不会静默放宽权限。已对比验证同一条固定输出命令：沙箱模式失败，兼容模式成功。

## 凭据与文件

- 调用 Key 使用 Android Keystore 加密保存，不放进 CLI 命令行、普通模型配置或安装日志。
- PRoot 会清理父进程环境。启动时通过 Termux 私有目录中权限 600 的一次性 JSON 交接所需环境，Debian 读取后立即删除；启动失败也尝试清理未消费文件。设备异常终止可能留下私有启动文件，因此不能将这套机制描述成“完全不落盘”。
- Agent 退出后清除模型凭据环境，再返回普通 Shell。桥接只监听 `127.0.0.1`，使用独立随机凭据。
- 项目当前放在 Termux 私有目录，例如 `~/projects/my-project`。系统文件选择器导入、ZIP 导入与导出尚待实现，不能直接把 Android 的 content URI 当成 Linux 路径。

## 连接电脑

“运行环境”可保存 SSH 电脑，支持用户名、端口、OpenSSH 别名及 `ProxyJump`。目标电脑需启用 SSH Server，手机须能访问其地址。首次连接在内嵌终端核对主机指纹并登录；私钥、密码和 `known_hosts` 由 Termux OpenSSH 管理。

SSH 打开电脑上的新 Shell，不接管桌面工作台已有终端，也不自动转交手机模型 Key。远程 Agent 使用电脑自身的 CLI、项目和配置；断网后的任务保留可使用电脑上的 tmux 等工具。

## 构建与验证

需要 Android SDK 34、JDK 17：

```sh
./gradlew :app:assembleDebug :app:assembleDebugAndroidTest :app:testDebugUnitTest
python3 -m unittest -v tests/test_runtime_installer.py tests/test_termux_bridge.py
bash -n app/src/main/assets/runtime_setup.sh
```

APK：`app/build/outputs/apk/debug/app-debug.apk`。

2026-11-18 已验证：

- Python 桥接/安装器 15 项测试、Android 单元测试及 APK 构建通过。
- API 34 模拟器通过真实 PTY、中文 IME/退格/回车、后台停止读取、恢复相同 PID/会话、短暂故障自动恢复且不重复创建终端、关闭最后一个标签测试。全屏 PTY 测试程序覆盖按钮、原生触摸及右侧边缘、SGR 与二进制鼠标编码；普通 Shell 滚动不发送输入，跨输出块的 UTF-8 保持完整。另验证模型网关认证头与本机桥接认证互相隔离。
- 小米平板 5 Pro 12.4、官方 Termux 0.118.3、PRoot-Distro 5.10.3、已有 Debian trixie：工作台自动补齐缺失工具并验证普通用户下的 Node 22.23.5、Codex 0.169.0、Claude 2.1.326；升级桥接保留原终端标签。
- 平板真实 Codex/Claude 经一次性环境交接，向仅在设备回环地址运行的模拟网关发送 Responses/Messages 请求，正确携带测试模型及测试认证，完成后返回 Debian Shell，凭据环境已清除。该测试没有使用真实 Key，也没有调用外部模型。
- Codex 交互输入界面及 Claude API 模式的首次主题/Key 确认界面已在平板启动。最终启动参数下，Codex 兼容模式的真实交互会话经模拟网关完成 `exec_command` 固定输出命令，退出码 0；未调用外部模型。
- 0.1.9 在 Xiaomi `22081281AC` 实机安装验证：在用户已有的 Claude · Kimi-Dev-72B 会话确认 alternate screen 与鼠标模式；工具栏上下按钮、内容拖动及右侧滑块能滚动对话。更新保留原会话，没有发起新的模型推理。普通小米中文输入法可弹出。
- 0.1.10 的模拟器回归验证普通 Shell/全屏程序的手指上下滑方向、模拟鼠标滚轮方向、鼠标拖动不会进入触摸滚动逻辑，右侧滑块维持原方向；平板原 Kimi 会话已试过反向触摸滚动。未用实体鼠标进行实机测试。
- 0.1.11 的 PTY 回归覆盖 Claude `Ctrl+End`、Codex 会话记录页 `End`，并确认普通 Shell 的“底部”不发送输入。平板原 Kimi 会话点原生“底部”按钮后，`Jump to bottom` 提示消失并回到最新内容。Codex 快捷键根据本机 0.169.0 的实际记录页核对；非默认键位映射尚未验收。
- 0.1.12 移除右侧滚动条与占位。平板检查确认没有可见滚动条、左右内边距均为 6px；回归保留上下按钮、“底部”、手指方向与右侧边缘滑动覆盖。
- 0.1.13 保留自然手指滑动和鼠标事件方向；全屏界面未启用鼠标跟踪时，上下按钮向 Codex 记录页发送上下键，其他 TUI 使用 PageUp/PageDown，避免只滚动 xterm 的空白 alternate buffer。

尚未完成：只有全新 Termux 的完整首次下载验收、真实网关与真实模型端到端对话、各厂商长期后台存活、远程 SSH 链路及项目导入/导出。PRoot 共享 Android 内核，不是完整虚拟机；不能将这里的结果当作官方 Android 支持承诺。

### 模拟器输入回归

测试只在模拟器运行，使用真实本地 Shell 与假网关，不使用真实 Key：

```sh
python3 tests/terminal_pty_fixture.py --workspace /tmp/workbench-terminal-test --port 19887
# 另一个终端：
adb -s emulator-5554 install -r app/build/outputs/apk/debug/app-debug.apk
adb -s emulator-5554 install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb -s emulator-5554 reverse tcp:19887 tcp:19887
adb -s emulator-5554 shell am instrument -w -r -e bridge_port 19887 -e workspace /tmp/workbench-terminal-test com.cassette.workbench.test/com.cassette.workbench.TerminalInputInstrumentation
```

`-e device_smoke true` 是另行明确选择的真机环境补齐检查，会下载缺失工具并查询版本；不要把它当作无副作用的模拟器测试。
