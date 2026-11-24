@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.cassette.workbench

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.WindowManager
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AccountCircle
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.ViewModelProvider
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.util.Locale

class MainActivity : ComponentActivity() {
    private lateinit var model: WorkbenchModel
    private lateinit var updates: WorkbenchUpdates
    private var immersiveFullscreen = false
    fun setImmersiveFullscreen(enabled: Boolean) {
        immersiveFullscreen = enabled
        window.attributes = window.attributes.apply {
            layoutInDisplayCutoutMode = if (enabled) WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                else WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_DEFAULT
        }
        WindowCompat.getInsetsController(window, window.decorView).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            if (enabled) hide(WindowInsetsCompat.Type.systemBars()) else show(WindowInsetsCompat.Type.systemBars())
        }
    }
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && immersiveFullscreen) setImmersiveFullscreen(true)
    }
    private val termuxPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (::model.isInitialized) {
            model.environmentChanged()
            if (granted) model.connect()
        }
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        model = ViewModelProvider(this)[WorkbenchModel::class.java]
        updates = ViewModelProvider(this)[WorkbenchUpdates::class.java]
        setContent { WorkbenchApp(model, updates) { termuxPermission.launch(TERMUX_PERMISSION) } }
    }
    override fun onResume() { super.onResume(); if (::model.isInitialized) model.environmentChanged() }
    override fun onStart() { super.onStart(); model.setForeground(true); updates.setForeground(true) }
    override fun onStop() { updates.setForeground(false); model.setForeground(false); super.onStop() }
}

@Composable
fun WorkbenchApp(model: WorkbenchModel, updates: WorkbenchUpdates, requestTermuxPermission: () -> Unit) {
    val dark = isSystemInDarkTheme()
    val colors = if (dark) darkColorScheme(primary = Color(0xffc7c8ff), secondary = Color(0xffc4c4d6), surface = Color(0xff15161c), background = Color(0xff15161c))
        else lightColorScheme(primary = Color(0xff535684), secondary = Color(0xff5b5d70), surface = Color(0xfffaf9fc), background = Color(0xfffaf9fc))
    MaterialTheme(colorScheme = colors) {
        var page by rememberSaveable { mutableIntStateOf(0) }
        var fullscreen by rememberSaveable { mutableStateOf(false) }
        val activity = LocalContext.current as? MainActivity
        DisposableEffect(fullscreen, page, activity) {
            activity?.setImmersiveFullscreen(fullscreen && page == 0)
            onDispose { activity?.setImmersiveFullscreen(false) }
        }
        val terminalInput = page == 0 && model.terminalInputSession.isNotBlank() &&
            WindowInsets.ime.getBottom(LocalDensity.current) > 0
        BackHandler(fullscreen) { fullscreen = false }
        Scaffold(
            modifier = Modifier.fillMaxSize().imePadding(),
            contentWindowInsets = if (fullscreen && page == 0) WindowInsets(0, 0, 0, 0) else ScaffoldDefaults.contentWindowInsets,
            topBar = { if (!fullscreen && !terminalInput) TopAppBar(title = { Column {
                Text("Agent 工作台", fontWeight = FontWeight.SemiBold)
                val connection = if (model.bridgeReconnecting) "正在重连" else if (model.connected) "本地已连接" else "环境未连接"
                Text(if (BuildConfig.DEBUG) "开发预览 · $connection" else connection,
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } }, actions = {
                TextButton(onClick = { model.showAgentPermissions = true }, enabled = model.localExecution) { Text("本地权限") }
                TextButton(onClick = { model.showTools = true }, enabled = model.localExecution) { Text("本地工具") }
                TextButton(onClick = updates::open) { Text(updates.nextVersion?.let { "新版 $it" } ?: "检查更新") }
            }) },
            bottomBar = { if (!fullscreen && !terminalInput) NavigationBar {
                listOf("工作区", "调用配置", "运行环境").forEachIndexed { index, label ->
                    NavigationBarItem(selected = page == index, onClick = { page = index }, label = { Text(label) }, icon = {
                        Icon(when (index) { 0 -> Icons.Outlined.Home; 1 -> Icons.Outlined.AccountCircle; else -> Icons.Outlined.Settings }, label)
                    })
                }
            } },
        ) { padding ->
            Column(Modifier.fillMaxSize().padding(padding)) {
                if (model.error.isNotBlank()) Notice(model.error, true)
                else if (model.notice.isNotBlank() && !fullscreen && !terminalInput) Notice(model.notice, false)
                when (page) {
                    0 -> WorkspaceScreen(model, fullscreen, terminalInput, { fullscreen = !fullscreen }, { fullscreen = false; page = it })
                    1 -> ProfilesScreen(model)
                    else -> EnvironmentScreen(model, requestTermuxPermission) { page = 0 }
                }
            }
        }
        WorkbenchUpdateDialog(updates, model.sessions.count { it.exitCode == null }, model.busy || model.runtimeInstalling || model.vmBusy)
        if (model.showAgentPermissions) AgentPermissionsDialog(model)
        if (model.showTools) ToolBridgeDialog(model)
    }
}

@Composable private fun Notice(text: String, error: Boolean) {
    Text(text, style = MaterialTheme.typography.bodySmall,
        color = if (error) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.fillMaxWidth().background(if (error) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 18.dp, vertical = 9.dp))
}

@Composable private fun WorkspaceScreen(model: WorkbenchModel, fullscreen: Boolean, terminalInput: Boolean, toggleFullscreen: () -> Unit, changePage: (Int) -> Unit) {
    // TabRow subcomposes its contents later. Capture one immutable list so
    // removing the selected last tab cannot mix its old index with new tabs.
    val sessions = model.sessions
    val selectedSession = sessions.firstOrNull { it.id == model.selectedSession } ?: sessions.firstOrNull()
    var expanded by rememberSaveable { mutableStateOf(true) }
    var selectModel by remember { mutableStateOf(false) }
    var confirmResume by remember { mutableStateOf(false) }
    var showHistory by remember { mutableStateOf(false) }
    var allHistoryProjects by remember { mutableStateOf(false) }
    var confirmCompatibility by remember { mutableStateOf(false) }
    var closeTabPending by remember { mutableStateOf<TerminalSession?>(null) }
    var newTerminalMenu by rememberSaveable { mutableStateOf(false) }
    var splitEnabled by rememberSaveable { mutableStateOf(false) }
    var splitOtherSessionId by rememberSaveable { mutableStateOf("") }
    val tablet = LocalConfiguration.current.smallestScreenWidthDp >= 600
    val taskScroll = rememberScrollState()
    LaunchedEffect(sessions.size) { if (sessions.isNotEmpty()) expanded = false }
    LaunchedEffect(model.terminalRevealVersion) { if (model.terminalRevealVersion > 0) expanded = false }
    LaunchedEffect(sessions.map { it.id }, model.selectedSession) {
        val available = sessions.map { it.id }.filter { it != model.selectedSession }
        if (splitOtherSessionId !in available) splitOtherSessionId = available.firstOrNull().orEmpty()
        if (available.isEmpty()) splitEnabled = false
    }
    Column(Modifier.fillMaxSize()) {
        if (!fullscreen && !terminalInput) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(model.executionName, fontWeight = FontWeight.Medium)
                    Text(model.sshTarget?.destination ?: model.options.workspace, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if ((model.localExecution && model.runtimeEnvironment == "debian") || model.remoteWorkbench != null) TextButton(onClick = {
                    allHistoryProjects = true
                    showHistory = true
                    model.refreshHistory(allProjects = true)
                }, enabled = model.connected && !model.busy) { Text("恢复最近对话") }
                TextButton(onClick = { expanded = !expanded }) { Text(if (expanded) "收起配置" else "配置任务") }
            }
            if (expanded) Column(Modifier.fillMaxWidth().heightIn(max = 370.dp).verticalScroll(taskScroll).padding(horizontal = 18.dp)) {
                ChoiceField("执行位置", model.executionName, Modifier.fillMaxWidth(),
                    (if (model.localAgentSupported) listOf("local-debian" to "手机本地 · Debian") else listOf("local-shell" to "手机本地 · Shell")) +
                        (if (model.vmSupported) listOf("local-vm" to "手机本地 · Linux VM") else emptyList()) +
                        model.sshTargets.map { it.id to "SSH · ${it.name}" },
                    model::selectExecutionLocation)
                if (!model.localExecution) {
                    Spacer(Modifier.height(8.dp))
                    RemoteWorkbenchPanel(model) { all ->
                        allHistoryProjects = all; showHistory = true; model.refreshHistory(all)
                    }
                } else {
                Spacer(Modifier.height(8.dp))
                if (model.localAgentSupported || model.vmSupported) {
                    ChoiceField("本地运行环境", if (model.runtimeEnvironment == "vm") "Linux VM · Docker / 系统任务" else "Debian · 轻量 Agent",
                        Modifier.fillMaxWidth(), (if (model.localAgentSupported) listOf("debian" to "Debian · 轻量 Agent") else emptyList()) +
                            (if (model.vmSupported) listOf("vm" to "Linux VM · Docker / 系统任务") else emptyList()),
                        model::selectRuntimeEnvironment)
                } else Text("本地环境 · Shell（无适配的 Codex/Claude CLI）；可配置 SSH 电脑运行 Agent。", style = MaterialTheme.typography.bodySmall)
                if (!model.localAgentSupported) Text("Termux 架构 ${model.deviceArchitecture} 没有可用的本地 Codex/Claude CLI；工作台仍可运行 Shell，并通过 SSH 连接电脑执行 Agent。",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ChoiceField("调用配置", model.profile?.name ?: "添加调用配置", Modifier.weight(1f), model.profiles.map { it.id to it.name }, model::chooseProfile) { changePage(1) }
                    ChoiceField("客户端", if (model.options.client == "codex") "Codex" else "Claude", Modifier.weight(0.7f), listOf("codex" to "Codex", "claude" to "Claude"), { model.updateOptions(model.options.copy(client = it)) })
                }
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = { if (model.profile == null) changePage(1) else selectModel = true }, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(10.dp)) {
                    Text(model.options.model.ifBlank { "选择模型或手动填写 ID" }, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    Text("⌄")
                }
                OutlinedTextField(value = model.options.workspace, onValueChange = { model.updateOptions(model.options.copy(workspace = it)) }, label = { Text("手机项目目录") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().onFocusChanged { if (it.isFocused) model.terminalFocusChanged("", true) })
                if (model.options.client == "codex" && model.runtimeEnvironment == "debian") Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = model.options.codexCompatibility, onCheckedChange = { enabled ->
                        if (enabled) confirmCompatibility = true else model.updateOptions(model.options.copy(codexCompatibility = false))
                    })
                    Text("Codex 命令兼容模式（关闭 Linux 沙箱，保留按需审批）", style = MaterialTheme.typography.labelSmall)
                }
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    val inVm = model.runtimeEnvironment == "vm"
                    val modes = if (inVm) listOf("new" to "新会话", "continue" to "恢复 VM 当前配置的最近对话",
                        "resume" to "在 VM 终端选择对话") else
                        listOf("new" to "新会话", "continue" to "恢复最近对话", "resume" to "从历史创建分支")
                    ChoiceField("会话", modes.firstOrNull { it.first == model.options.mode }?.second ?: "新会话",
                        Modifier.weight(1f), modes, { mode ->
                            when {
                                inVm || mode == "new" -> model.updateOptions(model.options.copy(mode = mode))
                                mode == "continue" -> model.restoreRecentInProject()
                                else -> {
                                    allHistoryProjects = false
                                    showHistory = true
                                    model.refreshHistory()
                                }
                            }
                        })
                    Button(onClick = { if (model.options.mode == "new") model.launch() else confirmResume = true },
                        enabled = model.connected && !model.busy && model.localAgentSupported,
                        modifier = Modifier.height(56.dp)) {
                        Text(if (!inVm && model.options.mode == "resume") "启动分支" else "启动 Agent")
                    }
                }
                Text(if (model.runtimeEnvironment == "vm") "Agent 在完整 Linux VM 中执行，模型请求发往所选网关；VM 内可运行 Docker 和系统级任务。首次启动可能需要等待虚拟机就绪。"
                    else "Agent 在手机 Debian 中执行，模型请求发往所选网关。首次启动可能需要选择主题并确认 API Key。",
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 9.dp))
                }
            }
        }
        if (sessions.isEmpty()) {
            Column(Modifier.weight(1f).fillMaxWidth().padding(28.dp), verticalArrangement = Arrangement.Center) {
                Text("把工作留在终端里", fontSize = 25.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(12.dp))
                Text(if (model.localExecution) "先连接 Termux，即可在这里打开手机本地 Shell。配置网关和模型后，再启动 Claude 或 Codex。" else "连接电脑工作台后可接续运行中的会话，或用电脑的配置新建、恢复对话。电脑须启用手机接续并保持运行。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(22.dp))
                if (model.localExecution) {
                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                        if (model.connected && model.debianInstalled) {
                            Button(onClick = { model.launch(shell = true, shellKind = "debian") }) { Text("打开 Debian Shell") }
                        } else {
                            Button(onClick = { changePage(2) }) { Text("设置运行环境") }
                        }
                        if (model.connected && model.vmInstalled && model.vmSupported) {
                            OutlinedButton(onClick = { model.launch(shell = true, shellKind = "vm") }) { Text("打开 Linux VM Shell") }
                        }
                        if (model.connected) TextButton(onClick = { model.launch(shell = true, shellKind = "termux") }) { Text("Termux Shell") }
                    }
                } else {
                    Button(onClick = { if (model.connected) model.connectRemote() else changePage(2) }) { Text(if (!model.connected) "设置运行环境" else "连接电脑工作台") }
                }
            }
        } else {
            BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
                // IME height changes must not tear down the second terminal.
                val canSplit = tablet && maxWidth >= 760.dp && sessions.size >= 2
                Column(Modifier.fillMaxSize()) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("终端", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
                        Text(if (model.localExecution) usageCompact(model.usage) else "电脑用量见桌面工作台", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                        if (model.localExecution) {
                            Box {
                                TextButton(onClick = { newTerminalMenu = true }, enabled = model.connected && !model.busy) { Text("＋ 终端") }
                                DropdownMenu(expanded = newTerminalMenu, onDismissRequest = { newTerminalMenu = false }) {
                                    DropdownMenuItem(text = { Text("Debian Shell") }, onClick = {
                                        newTerminalMenu = false
                                        if (model.debianInstalled) model.launch(shell = true, shellKind = "debian")
                                        else { model.selectRuntimeEnvironment("debian"); changePage(2) }
                                    })
                                    DropdownMenuItem(text = { Text("Termux Shell") }, onClick = {
                                        newTerminalMenu = false
                                        model.launch(shell = true, shellKind = "termux")
                                    })
                                    if (model.vmSupported) DropdownMenuItem(text = { Text(if (model.vmInstalled) "Linux VM Shell · 支持 Docker" else "安装完整 Linux 虚拟机…") }, onClick = {
                                        newTerminalMenu = false
                                        if (model.vmInstalled) model.launch(shell = true, shellKind = "vm")
                                        else { model.selectRuntimeEnvironment("vm"); changePage(2) }
                                    })
                                }
                            }
                        } else {
                            TextButton(onClick = { expanded = true }, enabled = model.connected && !model.busy) { Text("电脑会话") }
                        }
                        if (canSplit) TextButton(onClick = {
                            if (!splitEnabled && splitOtherSessionId.isBlank()) {
                                splitOtherSessionId = sessions.firstOrNull { it.id != model.selectedSession }?.id.orEmpty()
                            }
                            splitEnabled = !splitEnabled
                        }) { Text(if (splitEnabled) "退出分屏" else "分屏") }
                        TextButton(onClick = toggleFullscreen) { Text(if (fullscreen) "退出全屏" else "全屏") }
                    }
                    if (splitEnabled && canSplit) {
                        var otherMenu by rememberSaveable { mutableStateOf(false) }
                        Row(Modifier.fillMaxWidth().padding(start = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text("左侧", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Spacer(Modifier.weight(1f))
                            Box {
                                TextButton(onClick = { otherMenu = true }) {
                                    Text("右侧：${sessions.firstOrNull { it.id == splitOtherSessionId }?.title ?: "选择终端"}", maxLines = 1, overflow = TextOverflow.Ellipsis)
                                }
                                DropdownMenu(expanded = otherMenu, onDismissRequest = { otherMenu = false }) {
                                    sessions.filter { it.id != model.selectedSession }.forEach { candidate ->
                                        DropdownMenuItem(text = { Text(candidate.title, maxLines = 1, overflow = TextOverflow.Ellipsis) }, onClick = {
                                            splitOtherSessionId = candidate.id
                                            otherMenu = false
                                        })
                                    }
                                }
                            }
                        }
                    }
                    TerminalSessionTabs(sessions, selectedSession?.id.orEmpty(), onSelect = { id ->
                        val previous = model.selectedSession
                        model.selectedSession = id
                        if (splitEnabled && splitOtherSessionId == id) splitOtherSessionId = previous
                    }, onClose = { closeTabPending = it }, onMove = model::moveSession)
                    val primary = selectedSession ?: sessions.first()
                    val secondary = sessions.firstOrNull { it.id == splitOtherSessionId && it.id != primary.id }
                    if (splitEnabled && canSplit && secondary != null) {
                        Row(Modifier.weight(1f).fillMaxWidth()) {
                            key(primary.id) {
                                TerminalPanel(model, primary, Modifier.weight(1f), showTitle = true)
                            }
                            Box(Modifier.width(1.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outlineVariant))
                            key(secondary.id) {
                                TerminalPanel(model, secondary, Modifier.weight(1f), showTitle = true)
                            }
                        }
                    } else {
                        key(primary.id) { TerminalPanel(model, primary, Modifier.weight(1f)) }
                    }
                }
            }
        }
    }
    closeTabPending?.let { session ->
        val login = session.client == "ssh-login"
        AlertDialog(
            onDismissRequest = { closeTabPending = null },
            title = { Text(if (login) "断开这台电脑？" else if (session.remoteTarget.isNotEmpty()) "断开此会话？" else "关闭此终端？") },
            text = { Text(when {
                login -> "将关闭平板上的电脑登录通道和此标签；电脑上运行中的任务会继续。"
                session.remoteTarget.isNotEmpty() -> "将移除平板上的会话标签；电脑上的任务会继续运行。"
                else -> "该标签中的 Agent 与 Shell 将停止。"
            }) },
            confirmButton = { TextButton(onClick = { closeTabPending = null; model.close(session.id) }) { Text(if (login || session.remoteTarget.isNotEmpty()) "断开" else "关闭终端") } },
            dismissButton = { TextButton(onClick = { closeTabPending = null }) { Text("取消") } },
        )
    }
    if (selectModel) ModelPicker(model, onDismiss = { selectModel = false })
    if (showHistory) HistoryPicker(model, allHistoryProjects, onDismiss = { showHistory = false }, onSelect = {
        showHistory = false
        if (allHistoryProjects) model.restoreOriginal(it)
        else {
            model.chooseHistory(it)
            expanded = true
        }
    })
    if (confirmCompatibility) AlertDialog(onDismissRequest = { confirmCompatibility = false },
        title = { Text("启用 Codex 命令兼容模式？") },
        text = { Text("部分安卓设备的 Debian/PRoot 无法运行 Codex Linux 沙箱，表现为能聊天但命令执行失败。启用后，Codex 将不使用这层文件与网络沙箱，并保留按需审批：是否请求确认由 Codex 决定，普通命令可以直接执行，并非逐条确认。它仍受 Android 的应用隔离限制，但可访问 Termux 及绑定给 Debian 的文件，范围不限于当前项目。仅用于你信任的任务。已运行终端保持原权限，之后新建 Codex 使用此选择，可随时关闭。") },
        confirmButton = { TextButton(onClick = { model.updateOptions(model.options.copy(codexCompatibility = true)); confirmCompatibility = false }) { Text("启用并记住") } },
        dismissButton = { TextButton(onClick = { confirmCompatibility = false }) { Text("保留沙箱") } })
    if (confirmResume) {
        val inVm = model.runtimeEnvironment == "vm"
        AlertDialog(onDismissRequest = { confirmResume = false },
            title = { Text(if (inVm) "从 Linux VM 历史恢复？" else "从历史创建分支？") },
            text = { Text(if (inVm) {
                "在 Linux VM 内查找当前项目和调用配置的会话。历史上下文会发往当前网关，可能产生费用。"
            } else model.selectedHistory?.let {
                "${it.title}\n调用配置：${model.profile?.name.orEmpty()}\n网关：${model.profile?.gateway.orEmpty()}\n模型：${model.options.model}\n\n新分支使用这套配置，保留原对话。"
            }.orEmpty()) },
            confirmButton = { TextButton(onClick = { confirmResume = false; model.launch() }) { Text(if (inVm) "打开 VM 终端" else "创建分支并启动") } },
            dismissButton = { TextButton(onClick = { confirmResume = false }) { Text("取消") } })
    }
}

@Composable private fun HistoryPicker(model: WorkbenchModel, allProjects: Boolean, onDismiss: () -> Unit, onSelect: (LocalConversation) -> Unit) {
    var query by remember { mutableStateOf("") }
    var showArchived by remember { mutableStateOf(false) }
    var directory by remember { mutableStateOf<List<String>?>(null) }
    fun parts(row: LocalConversation) = row.workspace.replace('\\', '/').split('/').filter(String::isNotBlank)
    val matches = model.history.filter { row ->
        row.archived == showArchived && listOf(row.title, row.preview, row.workspace, row.model, row.client)
            .any { it.contains(query.trim(), ignoreCase = true) }
    }
    val prefix = directory
    val rows = if (prefix == null) matches else matches.filter { parts(it) == prefix }
    val folders = if (prefix == null) emptyList() else matches.mapNotNull { row ->
        val path = parts(row)
        if (path.take(prefix.size) == prefix) path.getOrNull(prefix.size) else null
    }.distinct().sorted()
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(shape = RoundedCornerShape(20.dp), tonalElevation = 6.dp,
            modifier = Modifier.widthIn(max = 960.dp).fillMaxWidth(0.94f).fillMaxHeight(0.85f)) {
        Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(if (allProjects) "恢复最近对话" else "从当前项目的历史创建分支", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
                TextButton(onClick = { model.refreshHistory(allProjects) }, enabled = !model.historyLoading && model.historyArchiving.isEmpty()) { Text("刷新") }
                TextButton(onClick = onDismiss) { Text("关闭") }
            }
            Text((if (model.localExecution) "手机本地" else "电脑 · ${model.executionName}") + " · " +
                if (allProjects) "选择对话后用原配置恢复" else "${model.historyWorkspace}\n分支使用当前选择的配置和模型",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if(model.canArchiveHistory) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = !showArchived, onClick = { showArchived = false }, label = { Text("未归档") })
                FilterChip(selected = showArchived, onClick = { showArchived = true }, label = { Text("已归档") })
            } else Text("对话归档需要更新电脑工作台", style = MaterialTheme.typography.labelSmall)
            if (allProjects) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = prefix == null, onClick = { directory = null }, label = { Text("最近") })
                FilterChip(selected = prefix != null, onClick = { directory = emptyList() }, label = { Text("按目录") })
            }
            OutlinedTextField(query, { query = it; if (prefix != null) directory = emptyList() },
                label = { Text("搜索标题、内容、模型或项目") }, singleLine = true,
                modifier = Modifier.fillMaxWidth().onFocusChanged { if (it.isFocused) model.terminalFocusChanged("", true) })
            if (prefix != null && prefix.isNotEmpty()) {
                TextButton(onClick = { directory = prefix.dropLast(1) }) { Text("‹ 上一级") }
                Text(prefix.joinToString("/", prefix = "/"), maxLines = 1, overflow = TextOverflow.Ellipsis,
                    style = MaterialTheme.typography.labelSmall)
            }
            when {
                model.historyLoading -> LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 10.dp))
                model.historyError.isNotEmpty() -> Text(model.historyError, Modifier.padding(top = 10.dp))
                rows.isEmpty() && folders.isEmpty() -> Text(if (model.localExecution) "暂无匹配的本地对话" else "暂无匹配的电脑对话", Modifier.padding(top = 10.dp))
                else -> LazyColumn(Modifier.fillMaxWidth().weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (prefix != null) items(folders, key = { "folder:$it" }) { folder ->
                        TextButton(onClick = { directory = prefix + folder }, modifier = Modifier.fillMaxWidth()) {
                            Text(folder, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text("›")
                        }
                    }
                    items(rows, key = { it.reference }) { row ->
                        val profile = if (model.localExecution) model.profiles.firstOrNull { it.id == row.profileId }?.name ?: "来源配置未知"
                            else if (row.profileId == "global") "系统 CLI" else model.remoteWorkbench?.profiles?.firstOrNull { it.id == row.profileId }?.name ?: "电脑原配置"
                        Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(10.dp))
                            .clickable { onSelect(row) }.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(row.displayTitle, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Medium)
                            if (row.preview.isNotBlank() && row.preview != row.displayTitle) Text(row.preview, maxLines = 3, overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text("${if (row.client == "codex") "Codex" else "Claude"} · ${row.model.ifBlank { "模型未知" }} · $profile",
                                style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(row.workspace, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Text(java.text.DateFormat.getDateTimeInstance().format(java.util.Date(row.updatedAt * 1000)),
                                style = MaterialTheme.typography.labelSmall)
                            if(model.canArchiveHistory) TextButton(onClick = { model.archiveHistory(row) }, enabled = model.historyArchiving.isEmpty()) {
                                Text(if(row.archived) "取消归档" else "归档")
                            }
                        }
                    }
                }
            }
        }
        }
    }
}

@Composable internal fun ChoiceField(label: String, value: String, modifier: Modifier = Modifier, choices: List<Pair<String, String>>, choose: (String) -> Unit, empty: () -> Unit = {}) {
    var menu by remember { mutableStateOf(false) }
    Box(modifier) {
        OutlinedButton(onClick = { if (choices.isEmpty()) empty() else menu = true }, modifier = Modifier.fillMaxWidth().heightIn(min = 54.dp), shape = RoundedCornerShape(10.dp), contentPadding = PaddingValues(horizontal = 10.dp, vertical = 5.dp)) {
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.Start) {
                Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(value, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) { choices.forEach { (id, text) -> DropdownMenuItem(text = { Text(text) }, onClick = { menu = false; choose(id) }) } }
    }
}

@Composable private fun ModelPicker(model: WorkbenchModel, onDismiss: () -> Unit) {
    var query by remember { mutableStateOf("") }
    var favoritesOnly by remember { mutableStateOf(false) }
    val profile = model.profile
    val values = profile?.models.orEmpty().filter { it.contains(query, ignoreCase = true) && (!favoritesOnly || it in profile!!.favorites) }
        .sortedWith(compareByDescending<String> { it in profile!!.favorites }.thenBy { it })
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp).navigationBarsPadding()) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("选择模型", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
                IconButton(onClick = model::refreshModels, enabled = !model.loadingModels) { Icon(Icons.Outlined.Refresh, "刷新模型") }
            }
            OutlinedTextField(query, { query = it }, singleLine = true, label = { Text("搜索模型或输入模型 ID") }, modifier = Modifier.fillMaxWidth())
            Row(verticalAlignment = Alignment.CenterVertically) {
                FilterChip(selected = favoritesOnly, onClick = { favoritesOnly = !favoritesOnly }, label = { Text("收藏") })
                Text("${values.size} 个模型", style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(start = 12.dp))
                Spacer(Modifier.weight(1f))
                if (query.isNotBlank()) TextButton(onClick = { model.updateOptions(model.options.copy(model = query.trim())); onDismiss() }) { Text("使用此 ID") }
            }
            LazyColumn(Modifier.fillMaxWidth().heightIn(max = 420.dp)) {
                if (values.isEmpty()) item { Text(if (model.loadingModels) "正在读取模型…" else "暂无匹配；可刷新列表或手动填写模型 ID", modifier = Modifier.padding(vertical = 20.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
                items(values, key = { it }) { value ->
                    Row(Modifier.fillMaxWidth().clickable { model.updateOptions(model.options.copy(model = value)); onDismiss() }.padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(value, modifier = Modifier.weight(1f), maxLines = 2, fontSize = 14.sp)
                        IconButton(onClick = { model.favorite(value) }) { Icon(Icons.Outlined.Star, if (value in profile!!.favorites) "取消收藏 $value" else "收藏 $value", tint = if (value in profile!!.favorites) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant) }
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
        }
    }
}

@Composable private fun ProfilesScreen(model: WorkbenchModel) {
    var adding by remember { mutableStateOf(false) }
    var editingConnection by remember { mutableStateOf<KeyProfile?>(null) }
    var removing by remember { mutableStateOf<KeyProfile?>(null) }
    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("手机本地调用配置", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
            FilledTonalButton(onClick = { adding = true }) { Icon(Icons.Outlined.Add, null); Text("添加") }
        }
        Text("每把 Key 记住自己的网关和模型。密钥由 Android Keystore 加密保存在这部手机。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(14.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (model.profiles.isEmpty()) item { OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(20.dp)) { Text("还没有调用配置", fontWeight = FontWeight.Medium); Text("也可以先在工作区打开本地 Shell。", style = MaterialTheme.typography.bodySmall) } } }
            items(model.profiles, key = { it.id }) { profile ->
                Card(onClick = { model.chooseProfile(profile.id) }, colors = CardDefaults.cardColors(containerColor = if (model.options.profileId == profile.id) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surfaceVariant)) {
                    Column(Modifier.padding(16.dp).fillMaxWidth()) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(profile.name, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                            if (model.options.profileId == profile.id) Text("当前", style = MaterialTheme.typography.labelMedium)
                        }
                        Text(profile.gateway, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if(profile.modelCatalogUrl.isNotBlank()) Text("模型目录：" + profile.modelCatalogUrl, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("${profile.models.size} 个缓存模型 · ${profile.favorites.size} 个收藏", style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 8.dp))
                        Row {
                            TextButton(onClick = { model.chooseProfile(profile.id); model.refreshModels() }, enabled = !model.loadingModels) { Text("刷新模型") }
                            TextButton(onClick = { editingConnection = profile }, enabled = !model.busy) { Text("连接设置") }
                            Spacer(Modifier.weight(1f))
                            TextButton(onClick = { removing = profile }) { Text("删除", color = MaterialTheme.colorScheme.error) }
                        }
                    }
                }
            }
            if (model.account.isNotBlank()) item { Text(model.account, style = MaterialTheme.typography.bodySmall) }
            item { UsagePanel(model) }
            item { Spacer(Modifier.height(16.dp)) }
        }
    }
    if (adding) AddProfileDialog(model) { adding = false }
    editingConnection?.let { profile -> AddProfileDialog(model, profile) { editingConnection = null } }
    removing?.let { profile -> AlertDialog(onDismissRequest = { removing = null }, title = { Text("删除调用配置？") }, text = { Text("将删除本机保存的“${profile.name}”及加密 Key。已启动的终端继续使用启动时的配置。") },
        confirmButton = { TextButton(onClick = { model.removeProfile(profile.id); removing = null }) { Text("删除") } }, dismissButton = { TextButton(onClick = { removing = null }) { Text("取消") } }) }
}

@Composable private fun AddProfileDialog(model: WorkbenchModel, profile: KeyProfile? = null, dismiss: () -> Unit) {
    var name by remember { mutableStateOf(profile?.name.orEmpty()) }; var gateway by remember { mutableStateOf(profile?.gateway ?: "https://") }; var key by remember { mutableStateOf("") }
    var sharedCatalog by remember { mutableStateOf(profile?.modelCatalogUrl.isNullOrBlank()) }
    var catalogUrl by remember { mutableStateOf(profile?.modelCatalogUrl.orEmpty()) }
    var validate by remember { mutableStateOf(true) }
    var allowInsecureLan by remember { mutableStateOf(profile?.allowInsecureLan ?: false) }
    AlertDialog(onDismissRequest = { if (!model.busy) dismiss() }, title = { Text(if(profile == null) "添加调用配置" else "连接设置 · " + profile.name) }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(9.dp)) {
            if(profile == null) OutlinedTextField(name, { name = it }, label = { Text("配置名称") }, singleLine = true)
            OutlinedTextField(gateway, { gateway = it }, label = { Text("调用 API 基础地址（可含路径）") }, singleLine = true)
            Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(sharedCatalog, { sharedCatalog = it }); Text("模型目录与调用共用地址", style = MaterialTheme.typography.bodySmall) }
            if(!sharedCatalog) {
                OutlinedTextField(catalogUrl, { catalogUrl = it }, label = { Text("模型目录完整 URL") }, placeholder = { Text("https://服务商/api/paas/v4/models") }, singleLine = true)
                Text("只向此地址读取模型列表，使用同一把调用 Key；请填写服务商可信地址。推理仍使用调用地址。", style = MaterialTheme.typography.labelSmall)
            }
            if(profile == null) OutlinedTextField(key, { key = it }, label = { Text("网关签发的调用 Key") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
            else Text("保留已有 Key、收藏与对话历史。已运行终端继续使用原配置。", style = MaterialTheme.typography.labelSmall)
            Text("127.0.0.1 指这部手机。电脑上的本机网关需要另外提供可访问地址或安全隧道。", style = MaterialTheme.typography.labelSmall)
            Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(validate, { validate = it }); Text("保存时读取模型列表", style = MaterialTheme.typography.bodySmall) }
            Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(allowInsecureLan, { allowInsecureLan = it }); Text("允许可信局域网 HTTP（Key 会在局域网明文传输）", style = MaterialTheme.typography.bodySmall) }
            Text("可填写 /proxy/v1 等 API 前缀。Claude 需 Messages 接口，Codex 需 Responses 接口；仅支持 Chat Completions 的网关不能直接供 Codex 使用。读取列表只发送 GET，不调用模型。", style = MaterialTheme.typography.labelSmall)
            if (model.error.isNotBlank()) Text(model.error, color = MaterialTheme.colorScheme.error)
        }
    }, confirmButton = { TextButton(onClick = {
        val catalog = if(sharedCatalog) "" else catalogUrl
        if(profile == null) model.saveProfile(name, gateway, key, validate, allowInsecureLan, catalog) { key = ""; dismiss() }
        else model.updateProfileConnection(profile.id, gateway, catalog, validate, allowInsecureLan, dismiss)
    }, enabled = !model.busy && name.isNotBlank() && (profile != null || key.isNotBlank()) && (sharedCatalog || catalogUrl.isNotBlank())) { Text(if (model.busy) "保存中…" else "保存") } },
        dismissButton = { TextButton(onClick = { key = ""; dismiss() }, enabled = !model.busy) { Text("取消") } })
}

private val setupCommand = """
mkdir -p ~/.termux
grep -qxF 'allow-external-apps=true' ~/.termux/termux.properties 2>/dev/null || printf '\nallow-external-apps=true\n' >> ~/.termux/termux.properties
termux-reload-settings
""".trimIndent()

@Composable private fun EnvironmentScreen(model: WorkbenchModel, requestPermission: () -> Unit, changeToWorkspace: () -> Unit) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val refreshed = model.environmentRevision
    val version = model.termux.version()
    val termuxApk = termuxApkUrl(Build.SUPPORTED_ABIS)
    var addingComputer by remember { mutableStateOf(false) }
    var removingComputer by remember { mutableStateOf<SshTarget?>(null) }
    LaunchedEffect(model.connected) {
        if (model.connected && !model.runtimeEnvironmentsKnown && model.termux.permitted()) model.connect()
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("手机本地运行", style = MaterialTheme.typography.headlineSmall)
        Text("可分别保留 Debian 与完整 Linux VM，并在工作区为新 Agent 选择执行位置。Debian 开销较低；VM 可运行 Codex、Claude、Docker 和 Linux 系统任务。", color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("1 · 安装并打开 Termux", fontWeight = FontWeight.SemiBold)
            Text(if (version == null) "尚未检测到 Termux" else "已安装：$version", style = MaterialTheme.typography.bodyMedium)
            Text("候选推荐 v0.118.3 · 官方 GitHub 稳定版，下载链接按本机 ABI 匹配。已有环境先保留，主程序与插件保持同源。", style = MaterialTheme.typography.bodySmall)
            Row {
                TextButton(onClick = { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(termuxApk ?: TERMUX_RELEASE))) }) {
                    Text(if (termuxApk == null) "选择兼容版本" else "下载匹配版本")
                }
                TextButton(onClick = { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(TERMUX_RELEASE))) }) { Text("官方发布页") }
                if (version != null) TextButton(onClick = { context.packageManager.getLaunchIntentForPackage("com.termux")?.let(context::startActivity) }) { Text("打开 Termux") }
            }
        } }
        OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("2 · 首次允许工作台连接", fontWeight = FontWeight.SemiBold)
            Text("只需在 Termux 设置一次外部命令开关，再回到工作台授权。Python 和其他工具由下一步自动准备。已有开关无需重复执行。", style = MaterialTheme.typography.bodySmall)
            Text(setupCommand, fontFamily = FontFamily.Monospace, fontSize = 11.sp)
            TextButton(onClick = { clipboard.setText(AnnotatedString(setupCommand)); model.message("已复制环境准备命令") }) { Text("复制准备命令") }
        } }
        OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("3 · 选择要安装或检查的环境", fontWeight = FontWeight.SemiBold)
            Row(verticalAlignment = Alignment.Top) {
                RadioButton(selected = model.runtimeEnvironment == "debian", onClick = { model.selectRuntimeEnvironment("debian") })
                Column(Modifier.weight(1f).clickable { model.selectRuntimeEnvironment("debian") }.padding(top = 10.dp)) {
                    Text("Debian 轻量环境 · ${if (model.debianInstalled) "已安装" else "可选安装"}", fontWeight = FontWeight.Medium)
                    Text(if (model.localAgentSupported) "用于 Codex、Claude、Git、编译和普通测试。共享 Android 内核，开销较低；不能在其中启动 Docker，也不具备系统级 root 权限。"
                        else "Termux 架构 ${model.deviceArchitecture} 没有匹配的官方本地 Codex/Claude CLI；仍可打开 Shell，或通过 SSH 电脑运行 Agent。",
                        style = MaterialTheme.typography.bodySmall)
                }
            }
            HorizontalDivider()
            Row(verticalAlignment = Alignment.Top) {
                RadioButton(selected = model.runtimeEnvironment == "vm", onClick = { model.selectRuntimeEnvironment("vm") }, enabled = model.vmSupported)
                Column(Modifier.weight(1f).then(if (model.vmSupported) Modifier.clickable { model.selectRuntimeEnvironment("vm") } else Modifier).padding(top = 10.dp)) {
                    Text("完整 Linux 虚拟机 · ${if (model.vmInstalled) "已安装" else "可选安装"}", fontWeight = FontWeight.Medium)
                    Text("Alpine + Docker + Codex + Claude，独立 Linux 内核与虚拟机内 root。VM 中 Codex 可在虚拟机内安装依赖、管理 Docker 和系统文件；这不会获得 Android root。适合容器、数据库和系统测试；可用时启用 KVM，否则软件模拟较慢。运行时分配 1.5 GB 内存，12 GB 磁盘按实际写入占用。", style = MaterialTheme.typography.bodySmall)
                    Text(if (model.vmSupported) "支持 64 位 ARM64 与 x86_64。安装和拉取容器镜像需联网，已下载的镜像可离线运行。虚拟机按需启动。"
                        else "当前 Termux ABI (${model.vmArchitecture}) 不支持完整 Linux VM。可继续使用 Termux Shell，或通过 SSH 连接电脑运行完整环境。",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Text(if (model.termux.permitted()) "Termux 执行权限已授予" else "需要授予工作台 Termux 执行权限", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = requestPermission, enabled = version != null && !model.termux.permitted()) { Text("授权") }
                Button(onClick = model::installRuntime, enabled = version != null && model.termux.permitted() && !model.busy && !model.runtimeInstalling &&
                    (if (model.runtimeEnvironment == "vm") model.vmSupported else model.localAgentSupported)) {
                    Text(if (model.busy || model.runtimeInstalling) "准备中…" else if (model.runtimeEnvironment == "vm") "安装 / 检查 Linux VM" else "安装 / 检查 Debian")
                }
            }
            Text(model.runtimeStage, style = MaterialTheme.typography.bodyMedium)
            if (model.busy || model.runtimeInstalling) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (model.runtimeLog.isNotBlank()) {
                var showLog by remember { mutableStateOf(false) }
                TextButton(onClick = { showLog = !showLog }) { Text(if (showLog) "收起安装日志" else "查看安装日志") }
                if (showLog) Text(model.runtimeLog.takeLast(7000), fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                    modifier = Modifier.heightIn(max = 260.dp).verticalScroll(rememberScrollState()))
            }
            Text("仅处理所选环境，不删除另一套环境或现有项目，不调用模型。VM 安装可补齐 Codex、Claude；工作区可选择 Agent 在 Debian 或 Linux VM 中启动。已有终端保持原环境。", style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = model::connect, enabled = !model.busy) { Text("仅连接终端桥接") }
        } }
        if (model.vmInstalled) OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Linux 虚拟机 · ${when (model.vmState) { "running" -> "进程运行中"; "installing" -> "正在安装"; else -> "已停止" }}", fontWeight = FontWeight.SemiBold)
            Text(if (model.vmSupported) "Linux VM 可运行 Codex、Claude、Docker 和完整系统任务；${if (model.vmAcceleration == "kvm") "KVM 硬件加速可用" else "当前使用 QEMU 软件模拟"}。停止虚拟机会结束其中的 Agent、终端与容器进程，请先保存。"
                else "此 VM 磁盘与当前 Termux 架构不匹配；可继续保留，停止操作仍可用。", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (model.vmSupported) Button(onClick = { model.launch(shell = true, shellKind = "vm") { changeToWorkspace() } }, enabled = model.connected && !model.busy && !model.vmBusy && !model.runtimeInstalling) { Text("打开 Linux VM Shell") }
                OutlinedButton(onClick = { model.controlVirtualMachine(if (model.vmState == "running") "stop" else "start") }, enabled = model.connected && !model.busy && !model.vmBusy && !model.runtimeInstalling && (model.vmSupported || model.vmState == "running")) {
                    Text(if (model.vmBusy) "处理中…" else if (model.vmState == "running") "停止虚拟机" else "启动虚拟机")
                }
            }
            Text("Linux VM 中可用 /projects 访问手机项目目录；这里的 root 不会授予 Android 系统 root。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } }
        Text("桥接仅监听手机回环地址，最多同时运行 8 个终端。切后台暂停读取，回前台自动恢复原终端；临时连接失败会自动重试。Termux 被系统终止时任务仍会中断。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        HorizontalDivider()
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("SSH 电脑", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            TextButton(onClick = { addingComputer = true }) { Text("添加电脑") }
        }
        Text("在电脑工作台打开「手机连接」，复制连接码到这里。接续只支持局域网；两端一起更换 Wi-Fi 后会自动寻找原电脑，保留配对。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        model.sshTargets.forEach { target ->
            OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp)) {
                Text(target.name, fontWeight = FontWeight.Medium)
                Text(target.destination, style = MaterialTheme.typography.bodySmall)
                Row {
                    TextButton(enabled = !model.busy, onClick = {
                        model.updateOptions(model.options.copy(executionId = target.id))
                        model.launch(onOpened = changeToWorkspace)
                    }) { Text("连接") }
                    Spacer(Modifier.weight(1f))
                    TextButton(enabled = !model.busy, onClick = { removingComputer = target }) { Text("移除") }
                }
            } }
        }
        Text("每次连接会打开一个新的远程 Shell。需要在断网后继续运行的任务可在电脑上使用 tmux。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        HorizontalDivider()
        Text("环境选择建议", fontWeight = FontWeight.SemiBold)
        Text("Debian 启动快、耗电较低；Linux VM 提供独立内核、虚拟机内 root 和 Docker，但 QEMU 软件模拟更慢。SSH 可联网借用电脑或服务器。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text("Android ${BuildConfig.VERSION_NAME} · $refreshed", style = MaterialTheme.typography.labelSmall)
    }
    if (addingComputer) AddComputerDialog(model) { addingComputer = false }
    removingComputer?.let { target ->
        AlertDialog(
            onDismissRequest = { removingComputer = null },
            title = { Text("移除「${target.name}」？") },
            text = { Text(if (target.platform.isNotEmpty()) "会清除本机的连接信息和配对密钥。若要在电脑上撤销授权，可删除 SSH 授权列表中标记为 AgentWorkbench-Android-${target.id} 的这一条。"
                else "会移除本机保存的连接信息。") },
            confirmButton = { TextButton(onClick = { removingComputer = null; model.removeSshTarget(target.id) }) { Text("移除") } },
            dismissButton = { TextButton(onClick = { removingComputer = null }) { Text("取消") } },
        )
    }
}

@Composable private fun AddComputerDialog(model: WorkbenchModel, dismiss: () -> Unit) {
    val clipboard = LocalClipboardManager.current
    var manual by remember { mutableStateOf(false) }
    var connectionCode by remember { mutableStateOf("") }
    var rememberPhone by remember { mutableStateOf(true) }
    var name by remember { mutableStateOf("") }
    var host by remember { mutableStateOf("") }
    var user by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("22") }
    AlertDialog(onDismissRequest = dismiss, title = { Text("添加 SSH 电脑") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = !manual, onClick = { manual = false }, label = { Text("连接码") })
                FilterChip(selected = manual, onClick = { manual = true }, label = { Text("手动填写") })
            }
            if (manual) {
                OutlinedTextField(name, { name = it }, label = { Text("电脑名称") }, singleLine = true)
                OutlinedTextField(host, { host = it }, label = { Text("IP、域名或 SSH 别名") }, singleLine = true)
                OutlinedTextField(user, { user = it }, label = { Text("用户名（可留空使用 SSH 配置）") }, singleLine = true)
                OutlinedTextField(port, { port = it }, label = { Text("SSH 端口") }, singleLine = true)
            } else {
                Text("在 Mac 或 Windows 工作台打开「手机连接」，复制连接码。首次连接时在终端输入电脑账号密码。")
                TextButton(onClick = { clipboard.getText()?.text?.let { connectionCode = it } }) { Text("粘贴连接码") }
                OutlinedTextField(connectionCode, { connectionCode = it }, label = { Text("电脑连接码") }, maxLines = 3)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = rememberPhone, onCheckedChange = { rememberPhone = it })
                    Text("账号支持时记住此手机")
                }
            }
            if (model.error.isNotBlank()) Text(model.error, color = MaterialTheme.colorScheme.error)
        }
    }, confirmButton = { TextButton(onClick = {
        if (manual) model.saveSshTarget(name, host, user, port, dismiss)
        else model.importSshConnection(connectionCode, rememberPhone, dismiss)
    }) { Text("添加") } }, dismissButton = { TextButton(onClick = dismiss) { Text("取消") } })
}


private fun usageNumber(value: Long?): String = value?.toString() ?: "未提供"
private fun usageCompact(value: UsageSummary): String = if (value.inputTokens == null && value.outputTokens == null) "Token 未提供" else "Token ${usageNumber(value.inputTokens)} / ${usageNumber(value.outputTokens)}"

@Composable private fun TerminalPanel(model: WorkbenchModel, session: TerminalSession, modifier: Modifier = Modifier, showTitle: Boolean = false) {
    var closing by remember { mutableStateOf(false) }
    var terminating by remember { mutableStateOf(false) }
    var attachmentTarget by remember { mutableStateOf("") }
    val attachmentPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) model.uploadRemoteAttachment(attachmentTarget, uri)
    }
    var fontDelta by remember { mutableIntStateOf(0) }
    var keyboardRequest by remember { mutableIntStateOf(0) }
    var scrollRequest by remember { mutableIntStateOf(0) }
    var bottomRequest by remember { mutableIntStateOf(0) }
    var copyRequest by remember { mutableIntStateOf(0) }
    var pasteRequest by remember { mutableIntStateOf(0) }
    var copyText by remember { mutableStateOf<String?>(null) }
    val clipboard = LocalClipboardManager.current
    Column(modifier.fillMaxSize()) {
        if (session.remoteTarget.isNotEmpty()) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(session.input.control.label, style = MaterialTheme.typography.labelSmall, modifier = Modifier.weight(1f))
            if (!session.canWrite) TextButton(onClick = { model.attachRemote(session.remoteId, session.remoteTarget) }) { Text("接续并控制") }
            TextButton(onClick = { attachmentTarget = session.id; attachmentPicker.launch(arrayOf("*/*")) }, enabled = session.canWrite && !model.busy) { Text("传文件/图片") }
            TextButton(onClick = { terminating = true }, enabled = session.canWrite) { Text("结束电脑任务") }
        }
        if (session.input.error.isNotBlank()) Notice(session.input.error, true)
        if (session.error.isNotBlank()) Notice(session.error, true)
        if (showTitle) {
            TextButton(onClick = { keyboardRequest++ }, modifier = Modifier.fillMaxWidth(), contentPadding = PaddingValues(horizontal = 10.dp, vertical = 2.dp)) {
                Column(Modifier.fillMaxWidth()) {
                    Text(session.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(session.workspace, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { copyRequest++ }, contentPadding = PaddingValues(horizontal = 7.dp)) { Text("复制文本", fontSize = 11.sp) }
            TextButton(onClick = { pasteRequest++ }, enabled = session.canWrite, contentPadding = PaddingValues(horizontal = 7.dp)) { Text("粘贴", fontSize = 11.sp) }
            listOf("回车" to "\r", "↑" to "\u001b[A", "↓" to "\u001b[B", "←" to "\u001b[D", "→" to "\u001b[C", "Esc" to "\u001b", "Tab" to "\t", "Ctrl+C" to "\u0003").forEach { (title, value) ->
                TextButton(onClick = { model.send(session.id, value) }, enabled = session.canWrite, contentPadding = PaddingValues(horizontal = 7.dp), modifier = Modifier.height(38.dp).widthIn(min = 40.dp)) { Text(title, fontSize = 11.sp) }
            }
            TextButton(onClick = { scrollRequest-- }, contentPadding = PaddingValues(horizontal = 7.dp), modifier = Modifier.height(38.dp).widthIn(min = 40.dp)) { Text("上翻", fontSize = 11.sp) }
            TextButton(onClick = { scrollRequest++ }, contentPadding = PaddingValues(horizontal = 7.dp), modifier = Modifier.height(38.dp).widthIn(min = 40.dp)) { Text("下翻", fontSize = 11.sp) }
            TextButton(onClick = { bottomRequest++ }, contentPadding = PaddingValues(horizontal = 7.dp), modifier = Modifier.height(38.dp).widthIn(min = 48.dp)) { Text("底部", fontSize = 11.sp) }
            TextButton(onClick = { keyboardRequest++ }, enabled = session.canWrite, contentPadding = PaddingValues(2.dp), modifier = Modifier.widthIn(min = 40.dp)) { Text("键盘", fontSize = 11.sp) }
            Spacer(Modifier.width(8.dp))
            TextButton(onClick = { fontDelta++ }, contentPadding = PaddingValues(3.dp), modifier = Modifier.width(30.dp)) { Text("A+") }
            TextButton(onClick = { fontDelta-- }, contentPadding = PaddingValues(3.dp), modifier = Modifier.width(30.dp)) { Text("A−") }
            IconButton(onClick = { closing = true }, modifier = Modifier.width(34.dp)) { Icon(Icons.Outlined.Close, "关闭此终端") }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            TerminalSurface(model, session, fontDelta, keyboardRequest, scrollRequest, bottomRequest,
                copyRequest, pasteRequest, { copyText = it }, Modifier.fillMaxSize())
        }
        if (session.exitCode != null) Text("终端已退出：${session.exitCode}", style = MaterialTheme.typography.labelSmall)
    }
    copyText?.let { snapshot ->
        AlertDialog(onDismissRequest = { copyText = null }, title = { Text("复制终端文本") },
            text = {
                Column {
                    Text("长按文字可选择并复制。这里只显示此终端已加载的内容，复制不会向电脑发送命令。", style = MaterialTheme.typography.bodySmall)
                    Spacer(Modifier.height(8.dp))
                    SelectionContainer {
                        Text(snapshot.ifEmpty { "暂无可复制文本" }, fontFamily = FontFamily.Monospace,
                            modifier = Modifier.fillMaxWidth().heightIn(max = 360.dp).verticalScroll(rememberScrollState()))
                    }
                }
            },
            confirmButton = { TextButton(enabled = snapshot.isNotEmpty(), onClick = {
                clipboard.setText(AnnotatedString(snapshot)); model.message("已复制此终端文本"); copyText = null
            }) { Text("复制全部") } },
            dismissButton = { TextButton(onClick = { copyText = null }) { Text("关闭") } })
    }
    if (closing) AlertDialog(onDismissRequest = { closing = false }, title = { Text(if (session.remoteTarget.isEmpty()) "关闭此终端？" else "断开此会话？") },
        text = { Text(if (session.remoteTarget.isEmpty()) "该标签中的 Agent 与 Shell 将停止。关闭电脑登录标签只会断开 SSH，电脑任务继续。" else "移除手机标签，电脑上的任务继续运行，可以再次接续。") },
        confirmButton = { TextButton(onClick = { closing = false; model.close(session.id) }) { Text(if (session.remoteTarget.isEmpty()) "关闭终端" else "断开") } }, dismissButton = { TextButton(onClick = { closing = false }) { Text("取消") } })
    if (terminating) AlertDialog(onDismissRequest = { terminating = false }, title = { Text("结束电脑任务？") },
        text = { Text("这会停止电脑上的同一个 Agent 进程，电脑端也会看到任务结束。") },
        confirmButton = { TextButton(onClick = { terminating = false; model.terminateRemote(session.id) }) { Text("结束任务") } },
        dismissButton = { TextButton(onClick = { terminating = false }) { Text("取消") } })
}

private class TerminalCursor(var serial: Long = 0, var fontDelta: Int = 0, var scrollRequest: Int = 0, var bottomRequest: Int = 0)

@SuppressLint("SetJavaScriptEnabled")
@Composable private fun TerminalSurface(model: WorkbenchModel, session: TerminalSession, fontDelta: Int, keyboardRequest: Int, scrollRequest: Int, bottomRequest: Int,
    copyRequest: Int, pasteRequest: Int, onCopyText: (String) -> Unit, modifier: Modifier) {
    val context = LocalContext.current
    var ready by remember { mutableStateOf(false) }
    val cursor = remember { TerminalCursor() }
    val replayThrough = remember { session.after }
    val handler = remember { Handler(Looper.getMainLooper()) }
    val clipboard = LocalClipboardManager.current
    val browser = remember {
        TerminalWebView(context) { model.send(session.id, it) }.apply {
            setOnFocusChangeListener { _, focused -> model.terminalFocusChanged(session.id, focused) }
            setBackgroundColor(AndroidColor.rgb(22, 24, 31))
            settings.javaScriptEnabled = true
            settings.allowFileAccess = false; settings.allowContentAccess = false
            settings.domStorageEnabled = false; settings.textZoom = 100
            addJavascriptInterface(object {
                @JavascriptInterface fun input(value: String) { model.send(session.id, value) }
                @JavascriptInterface fun inputBytes(value: String) { model.sendBytes(session.id, Base64.decode(value, Base64.NO_WRAP)) }
                @JavascriptInterface fun resize(cols: Int, rows: Int) { model.resize(session.id, cols, rows) }
                @JavascriptInterface fun ready() { handler.post { terminalReady = true; ready = true } }
            }, "Workbench")
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?) = true
                override fun shouldInterceptRequest(view: WebView?, request: WebResourceRequest?): WebResourceResponse {
                    val uri = request?.url
                    val file = uri?.lastPathSegment
                    if (uri?.host != "workbench.local" || file !in setOf("terminal.html", "xterm.js", "addon-fit.js", "xterm.css")) {
                        return WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))
                    }
                    val mime = when { file!!.endsWith(".js") -> "application/javascript"; file.endsWith(".css") -> "text/css"; else -> "text/html" }
                    return WebResourceResponse(mime, "UTF-8", context.assets.open("terminal/$file"))
                }
            }
            loadUrl("https://workbench.local/terminal.html")
        }
    }
    SideEffect { browser.onCopyText = onCopyText }
    DisposableEffect(browser) { onDispose {
        model.terminalFocusChanged(session.id, false)
        browser.setOnFocusChangeListener(null)
        browser.onCopyText = null
        browser.removeJavascriptInterface("Workbench"); browser.destroy()
    } }
    LaunchedEffect(ready, session.canWrite) {
        if (ready && session.canWrite) {
            browser.requestFocus()
            browser.evaluateJavascript("window.focusTerminal()", null)
            context.getSystemService(android.view.inputmethod.InputMethodManager::class.java).restartInput(browser)
        }
    }
    LaunchedEffect(keyboardRequest) {
        if (keyboardRequest > 0) browser.showTerminalKeyboard()
    }
    LaunchedEffect(copyRequest) {
        if (copyRequest > 0) browser.requestCopyText()
    }
    LaunchedEffect(pasteRequest) {
        if (pasteRequest > 0 && session.canWrite) clipboard.getText()?.text?.let { browser.pasteText(it) }
    }
    Box(modifier) {
        AndroidView(factory = { browser }, modifier = Modifier.fillMaxSize(), update = { view ->
            view.inputEnabled = session.canWrite
            if (ready) {
                view.evaluateJavascript("window.setInputEnabled(${session.canWrite})", null)
                session.output.filter { it.serial > cursor.serial }.forEach { chunk ->
                    view.evaluateJavascript("window.writeChunk(${JSONObject.quote(chunk.encoded)}, ${chunk.replay || chunk.serial <= replayThrough})", null)
                    cursor.serial = chunk.serial
                }
                if (cursor.fontDelta != fontDelta) {
                    view.evaluateJavascript("window.changeFont(${fontDelta - cursor.fontDelta})", null)
                    cursor.fontDelta = fontDelta
                }
                if (cursor.scrollRequest != scrollRequest) {
                    view.evaluateJavascript("window.scrollByButton(${scrollRequest - cursor.scrollRequest}, ${JSONObject.quote(session.client)})", null)
                    cursor.scrollRequest = scrollRequest
                }
                if (cursor.bottomRequest != bottomRequest) {
                    view.evaluateJavascript("window.jumpToBottom(${JSONObject.quote(session.client)})", null)
                    cursor.bottomRequest = bottomRequest
                }
            }
        })
    }
}
