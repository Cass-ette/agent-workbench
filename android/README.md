# Agent 工作台 Android 0.1.19

安卓原生工作台开发预览版：在手机或平板本地运行 Agent，Termux 承载后台进程，默认使用 Debian Linux 用户空间。模型来自用户选择的 API 网关。

APK 不带 Termux、Debian、CLI、Docker、CCHP 或本地模型。运行环境由下方向导单独安装；首次下载及解包占用另计。可直接填写兼容模型 API 或网关的地址和调用 Key，无须 CCHP。更新时需使用与已装版本相同的 APK 签名；遇到签名冲突时先保留已装版本，卸载会清除工作台数据。

## Android 的全局边界

Android 版本默认不要求 root，也不能把没有 root 的设备描述成拥有系统级“全局 Claude/Codex”。工作台只能管理自己的应用范围、Termux 用户空间、Debian/虚拟机环境和用户明确连接的远程主机；不能注册系统 PATH、修改其他应用的配置、替换系统命令或接管外部终端。因此 Android 的本地模式是局部模式，不能承诺桌面上的全局部署体验。

首次启动显示 Codex / Claude「权限设置」，顶部「权限」可重新打开。Codex 推荐为 Debian 项目保留沙箱并允许联网；已启用的 Debian 兼容模式和 VM 内系统任务保持其明确说明的权限范围。Claude 默认保留标准审批，提高权限须确认。SSH 沿用远端权限。详见[权限与工具能力](../docs/agent-permissions.zh-CN.md)。

顶部「工具」按 Debian / Linux VM 分别配置工作台 MCP、Skills 和 Claude 插件，组件路径属于所选环境，确认保存后加载到之后启动的会话。本机手机截屏尚未接入；SSH 工具由电脑端配置。详见[工具接入](../docs/tool-bridge.zh-CN.md)。

需要接近全局体验时，使用本页后面的 SSH 连接电脑或其他 Linux 主机，在远端工作区运行远端已有的 CLI。远端的配置、项目、历史和凭据留在远端，手机只负责连接和终端交互；这不要求手机 root，也不会把远端配置复制进 Android 应用。

局部会话与远端/全局会话的记忆交接按双向 fork 设计：局部可以显式交接到远端，全局或远端也可以由用户明确选择后 fork/import 回工作台自己的私有分支。源会话保持不变，不共享配置目录，也不把状态静默写回另一侧。当前设备和 CLI 的实际 fork 能力不足时，使用显式 memory handoff；不能把两个环境的历史目录直接混用。

局部跨模型中转仍依赖 CCHP，尚未轻量化。Android 是否能运行未来的轻量中转层要单独验证 ABI、后台存活、网络和资源占用；当前 Android 版本不把它列为已支持功能或安装前置条件。

## 首次使用

1. 安装并打开官方 GitHub Termux v0.118.3。工作台会按 Android ABI 打开对应官方 APK；也可从[其他架构与版本说明](https://github.com/termux/termux-app/releases/tag/v0.118.3)选择。已有 Termux 先保留，主程序与插件保持同源，不自动卸载环境。
2. 在工作台“运行环境”复制首次连接命令，在 Termux 粘贴一次：

   ```sh
   mkdir -p ~/.termux
   grep -qxF 'allow-external-apps=true' ~/.termux/termux.properties 2>/dev/null || printf '\nallow-external-apps=true\n' >> ~/.termux/termux.properties
   termux-reload-settings
   ```

3. 回工作台点“授权”，在“运行环境”选装 **Debian 轻量环境**、**完整 Linux 虚拟机**，也可以分别安装两套。Debian 安装会检查并补齐 Python、bash、Git、OpenSSH、PRoot-Distro、Debian、Node.js、Codex 和 Claude。已有环境和项目会保留。可展开安装日志，失败后可重试。安装不调用模型。
4. 在“调用配置”添加网关根地址与该网关签发的调用 Key，保留“保存时读取模型列表”。这是元数据查询，不发起推理。CCHP 的下游调用 Key 应搭配 CCHP 地址，上游 Key 应搭配它所属的上游地址。
5. 回“工作区”，选择调用配置、模型、Codex 或 Claude、手机项目目录与会话方式，点“启动 Agent”。无需手动进入 Debian 或输入启动命令。Claude 首次可能要求选择主题、确认使用自定义 API Key；项目访问仍由 CLI 提示确认。

如果模型列表提示 HTTP 401/403，先核对地址与 Key 是否属于同一网关、Key 是否仍有效；不要将管理 Token 当成调用 Key。工作台对模型网关发送 Bearer 与 `x-api-key` 兼容头，对本机桥接只发送其独立的 Bearer 凭据。

网关地址中的 `127.0.0.1` 指手机本身。电脑上仅绑定回环地址的网关，需要用户提供可访问的 HTTPS 地址或隧道；连接同一 Wi-Fi 不会自动建立转发。应用提供可信局域网 HTTP 例外，需要用户显式开启。

## 通用部署约定

- Android 8.0 及以上共用同一份 APK 和安装逻辑。按设备 ABI 引导到官方 Termux 安装包；用户只完成系统要求的安装、首次外部命令开关和授权，不要求自行判断 CPU、寻找镜像或拼装启动命令。
- 基础连接自动补齐 Python、Git 和 OpenSSH。运行环境能力以 Termux 进程的实际位数判断，避免把 64 位内核上的 32 位 Termux 误判成可安装 64 位客户端。
- ARM64 与 x86_64 分别选择对应的 QEMU、Linux 镜像及校验值；KVM 可用时启用，否则使用 TCG。没有可用本地 CLI 的 ABI 保留 Shell 和 SSH，并提前显示限制，不让用户进入注定失败的 Agent 安装。
- Debian 和 VM 可并存。环境选择只影响之后新建的终端；项目通过同一 Termux 项目目录共享，调用配置沿用工作台已有配置。两套系统的 CLI 历史各自保留，不把不同系统的进程或会话伪装成可直接迁移。
- 桥接脚本按 APK 构建号与内容摘要保存在独立版本目录。新桥接只使用自身版本的安装器与 Agent runner；旧终端继续使用原脚本，安装状态、项目、调用配置和 VM 磁盘留在持久目录。
- APK 升级补齐新版桥接和缺失依赖，不卸载 Termux、不重置 Debian、不格式化来源不明的虚拟盘。旧 VM 缺少架构字段时按已发布的 ARM64 格式识别，架构不匹配时保留原盘并阻止启动；旧终端继续使用原桥接端口。
- ADB 仅用于开发验证，不属于用户部署步骤。新增适配必须落在随 APK 分发的安装器和能力探测中，不能以单独修补某台设备作为交付。

这里的通用逻辑不代表所有 Android 版本、ABI 和厂商后台策略都已经实测。当前本地 Agent/VM 面向 ARM64、x86_64；其他设备提供可用路径并明确显示硬件和客户端限制。

## 日常操作

- 保存的配置、模型缓存、收藏和最近项目在本机保留，模型列表可手动刷新、搜索或填写 ID。
- 同时最多 8 个终端；每次启动固定自己的客户端、模型、网关、项目和会话方式。
- Debian Agent 与 Debian Shell 都在 PRoot-Distro Debian 的普通用户 `workbench` 下运行，并绑定同一手机项目目录；Linux VM Agent 与 VM Shell 则在独立 Alpine 虚拟机中运行。Termux Shell 单独保留给主机维护。新建终端菜单可选 Debian Shell、Linux VM Shell 或 Termux Shell，首次打开工作区也默认直达 Debian Shell。
- 平板宽屏可将两个已有终端并排显示，分别保留各自的模型、项目、会话和 PTY；点对应窗格或标题输入，右侧菜单可选择另一个终端，手机窄屏继续使用标签切换。键盘弹出后暂时收起应用标题、配置摘要和底部导航，为双窗格保留输入区域，收起键盘后恢复。当前支持左右两格，上下布局尚未实现。分屏只改变显示，不会合并终端或共享会话状态。
- Ctrl+C 交给前台 CLI 处理，退出 Agent 后可继续使用同一终端；“关闭标签”才停止该终端进程。
- 原生 Android 输入法处理中文组词、退格与回车。终端“全屏”会隐藏 Android 状态栏、导航栏和工作台导航，可按返回或点“退出全屏”恢复。系统栏也可从屏幕边缘滑动临时呼出。
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

## 完整 Linux 虚拟机

Debian 使用 PRoot，共享 Android 内核，适合日常 Agent、Git、编译和普通测试。Docker 任务使用单独选装的 Alpine ARM64 或 x86_64 虚拟机，架构由 Termux 自动探测并匹配。QEMU 可使用时自动启用 KVM，否则回退到软件模拟；VM 分配 1.5 GiB 内存与 12 GiB 稀疏虚拟盘，安装需联网。Docker 镜像可另行拉取，已缓存的镜像可以离线运行。官方 Codex/Claude 本地 CLI 与完整 VM 目前面向 64 位 ARM64、x86_64；其他 ABI 仍可使用工作台 Shell，并通过 SSH 连接电脑运行 Agent。

在「运行环境」可启动、停止虚拟机，或点击「打开 Linux VM Shell」进入工作区。工作区的新终端菜单也可打开 VM Shell，需要时会启动虚拟机。VM Shell 提供虚拟机内的 root 权限和 Docker，`/projects` 可读写 Termux 的 `~/projects`。选择 Linux VM 后，Codex 和 Claude 在虚拟机里运行，可恢复虚拟机内当前配置的对话。关闭 VM Shell 后虚拟机仍可继续处理任务，用完可在「运行环境」停止。

Debian 的「浏览各项目历史」按最近时间或目录查看对话。项目内的「恢复最近对话」用原配置打开当前目录和客户端的最近一条。「从历史创建分支」使用当前选定的配置创建独立会话。

## 连接电脑

1. 在 Mac 的「系统设置 → 通用 → 共享」开启远程登录，或在 Windows 启用 OpenSSH 服务器。电脑需保持在线。
2. 在电脑工作台点「手机连接 → 复制连接码」。同一网络可选局域网地址。异地可填写两端已加入的 Tailscale 网络里的电脑地址，也可以使用手机已能访问的公网地址。
3. 在 Android 点「运行环境 → 添加电脑 → 粘贴连接码」，保存后点「连接」。首次在内嵌终端输入电脑账号密码。连接码会让手机核对电脑身份。

新版电脑工作台可在「手机连接」启用接续。Android 选择该电脑后点「连接电脑工作台」，登录后在配置任务中选择运行会话、新建会话或电脑历史。支持电脑的工作台配置 / 系统 CLI 和分段附件传输；手机标签的断开不会停止电脑任务。详细步骤、控制权与当前限制见[手机接续电脑工作台](../docs/remote-workbench.zh-CN.md)。

勾选「账号支持时记住此手机」后，应用会生成手机专用的 SSH 密钥，通过首次登录登记，并验证后续连接能否自动登录。Windows 默认的管理员账号用密码登录，普通账号支持记住手机。配对失败时，可在终端按回车改用密码。移除保存的电脑会清除手机里的密钥，并显示对应的电脑端授权条目标记。

「手动填写」支持连接其他 SSH 服务器，或使用 Termux OpenSSH 的别名与 `ProxyJump`。手动连接时请核对主机指纹。每次连接会打开新的远程 Shell，使用电脑上的 CLI、项目、配置和历史，可用电脑上的 tmux 保留断网后的任务。

## 构建与验证

需要 Android SDK 34、JDK 17：

```sh
./gradlew :app:assembleDebug :app:assembleDebugAndroidTest :app:testDebugUnitTest
python3 -m unittest discover -s tests -v
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

2026-11-20，0.1.14 已验证：

- Python 桥接/安装器 16 项、Android 单元测试、应用与测试 APK 构建通过。
- Xiaomi `22081281AC` 无线安装保留已有终端；临时 Debian Shell 以普通用户 `workbench` 进入项目目录，Termux Shell 使用外层环境，两者不携带模型凭据。
- 实机左右分屏在软键盘弹出后仍各有可用终端区域；通过两个窗格各自的原生输入连接执行不同固定输出命令，确认输入与输出没有串线。只关闭测试新建的临时终端，原有终端 ID 与桥接端口保留。
- 缓存重绘及应用重新打开后的首批历史输出不会回送旧终端查询；实机测试重放颜色和光标位置查询，确认没有作为新输入进入 Shell。检查没有调用外部模型。Android 系统分屏已声明允许，系统级多窗口及上下布局尚未实机验收。

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

`-e device_shell_smoke true` 在已经配置好本地执行的设备上创建临时 Debian/Termux Shell，验证运行用户、项目目录、凭据清理、桥接升级及旧终端保留；平板还验证左右窗格、键盘显示期间的可用高度、两侧输入隔离及缓存查询重放。检查后只关闭自己创建的临时终端，不向已有 Agent 输入，也不发起模型请求。

额外传入 `-e device_vm_smoke true` 会启动一台已安装且已停止的虚拟机，检查 VM Shell 及项目挂载后将虚拟机关机。Android 11 以上也会核对全屏切换前后的系统栏可见性。

改为 `-e device_vm_attach_smoke true` 则要求 VM 已在运行，只附加临时 Shell，检查虚拟机内 root、项目挂载、Codex/Claude 版本与 Docker 服务，并核对原 VM PID 不变；不会启动或停止 VM。这两种 VM 测试不能同时启用。部分厂商系统需先用 `adb shell am start -W -n com.cassette.workbench/.MainActivity` 将应用带到前台，再运行 instrumentation。
