@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.cassette.workbench

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.ViewModelProvider
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.util.Locale

class MainActivity : ComponentActivity() {
    private lateinit var model: WorkbenchModel
    private val termuxPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (::model.isInitialized) {
            model.environmentChanged()
            if (granted) model.connect()
        }
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        model = ViewModelProvider(this)[WorkbenchModel::class.java]
        setContent { WorkbenchApp(model) { termuxPermission.launch(TERMUX_PERMISSION) } }
    }
    override fun onResume() { super.onResume(); if (::model.isInitialized) model.environmentChanged() }
    override fun onStart() { super.onStart(); if (::model.isInitialized) model.setForeground(true) }
    override fun onStop() { if (::model.isInitialized) model.setForeground(false); super.onStop() }
}

@Composable
fun WorkbenchApp(model: WorkbenchModel, requestTermuxPermission: () -> Unit) {
    val dark = isSystemInDarkTheme()
    val colors = if (dark) darkColorScheme(primary = Color(0xffc7c8ff), secondary = Color(0xffc4c4d6), surface = Color(0xff15161c), background = Color(0xff15161c))
        else lightColorScheme(primary = Color(0xff535684), secondary = Color(0xff5b5d70), surface = Color(0xfffaf9fc), background = Color(0xfffaf9fc))
    MaterialTheme(colorScheme = colors) {
        var page by rememberSaveable { mutableIntStateOf(0) }
        var fullscreen by rememberSaveable { mutableStateOf(false) }
        BackHandler(fullscreen) { fullscreen = false }
        Scaffold(
            topBar = { if (!fullscreen) TopAppBar(title = { Column {
                Text("Agent 工作台", fontWeight = FontWeight.SemiBold)
                Text("ANDROID ${BuildConfig.VERSION_NAME} · 开发预览版", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } }, actions = { Text(if (model.bridgeReconnecting) "正在重连" else if (model.connected) "本地已连接" else "环境未连接", style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(end = 18.dp)) }) },
            bottomBar = { if (!fullscreen) NavigationBar {
                listOf("工作区", "调用配置", "运行环境").forEachIndexed { index, label ->
                    NavigationBarItem(selected = page == index, onClick = { page = index }, label = { Text(label) }, icon = {
                        Icon(when (index) { 0 -> Icons.Outlined.Home; 1 -> Icons.Outlined.AccountCircle; else -> Icons.Outlined.Settings }, label)
                    })
                }
            } },
        ) { padding ->
            Column(Modifier.fillMaxSize().padding(padding)) {
                if (model.error.isNotBlank()) Notice(model.error, true)
                else if (model.notice.isNotBlank() && !fullscreen) Notice(model.notice, false)
                when (page) {
                    0 -> WorkspaceScreen(model, fullscreen, { fullscreen = !fullscreen }, { page = it })
                    1 -> ProfilesScreen(model)
                    else -> EnvironmentScreen(model, requestTermuxPermission)
                }
            }
        }
    }
}

@Composable private fun Notice(text: String, error: Boolean) {
    Text(text, style = MaterialTheme.typography.bodySmall,
        color = if (error) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.fillMaxWidth().background(if (error) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 18.dp, vertical = 9.dp))
}

@Composable private fun WorkspaceScreen(model: WorkbenchModel, fullscreen: Boolean, toggleFullscreen: () -> Unit, changePage: (Int) -> Unit) {
    var expanded by rememberSaveable { mutableStateOf(true) }
    var selectModel by remember { mutableStateOf(false) }
    var confirmResume by remember { mutableStateOf(false) }
    var showHistory by remember { mutableStateOf(false) }
    var confirmCompatibility by remember { mutableStateOf(false) }
    LaunchedEffect(model.sessions.size) { if (model.sessions.isNotEmpty()) expanded = false }
    Column(Modifier.fillMaxSize()) {
        if (!fullscreen) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(model.executionName, fontWeight = FontWeight.Medium)
                    Text(model.sshTarget?.destination ?: model.options.workspace, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (model.localExecution) TextButton(onClick = {
                    showHistory = true
                    model.refreshHistory()
                }) { Text("对话历史") }
                TextButton(onClick = { expanded = !expanded }) { Text(if (expanded) "收起配置" else "配置任务") }
            }
            if (expanded) Column(Modifier.fillMaxWidth().heightIn(max = 370.dp).verticalScroll(rememberScrollState()).padding(horizontal = 18.dp)) {
                ChoiceField("执行位置", model.executionName, Modifier.fillMaxWidth(),
                    listOf("local" to "手机本地 · Debian") + model.sshTargets.map { it.id to "SSH · ${it.name}" },
                    { model.updateOptions(model.options.copy(executionId = it)) })
                if (!model.localExecution) {
                    Spacer(Modifier.height(8.dp))
                    Text("通过手机的 SSH 连接电脑，登录后使用电脑上的 CLI、项目和调用配置。手机保存的模型 Key 不会发送给电脑。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        TextButton(onClick = { changePage(2) }) { Text("管理电脑") }
                        Spacer(Modifier.weight(1f))
                        Button(onClick = { model.launch(shell = true) }, enabled = model.connected && !model.busy) { Text("连接 SSH") }
                    }
                } else {
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
                OutlinedTextField(value = model.options.workspace, onValueChange = { model.updateOptions(model.options.copy(workspace = it)) }, label = { Text("手机项目目录") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                if (model.options.client == "codex") Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = model.options.codexCompatibility, onCheckedChange = { enabled ->
                        if (enabled) confirmCompatibility = true else model.updateOptions(model.options.copy(codexCompatibility = false))
                    })
                    Text("Codex 命令兼容模式（关闭 Linux 沙箱，保留按需审批）", style = MaterialTheme.typography.labelSmall)
                }
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    ChoiceField("会话", model.selectedHistory?.title ?: modeName(model.options.mode), Modifier.weight(1f),
                        listOf("new" to "新会话", "continue" to "继续最近会话", "resume" to "选择历史会话"), {
                            if (it == "resume") { showHistory = true; model.refreshHistory() }
                            else model.updateOptions(model.options.copy(mode = it))
                        })
                    Button(onClick = { if (model.options.mode == "new") model.launch() else confirmResume = true }, enabled = model.connected && !model.busy, modifier = Modifier.height(56.dp)) { Text(if (model.busy) "处理中…" else "启动 Agent") }
                }
                Text("Agent 在手机 Debian 中执行，模型请求发往所选网关。首次启动可能需要选择主题并确认 API Key。", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 9.dp))
                }
            }
        }
        if (model.sessions.isEmpty()) {
            Column(Modifier.weight(1f).fillMaxWidth().padding(28.dp), verticalArrangement = Arrangement.Center) {
                Text("把工作留在终端里", fontSize = 25.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(12.dp))
                Text(if (model.localExecution) "先连接 Termux，即可在这里打开手机本地 Shell。配置网关和模型后，再启动 Claude 或 Codex。" else "Termux 提供 SSH 客户端，任务和文件留在你连接的电脑。已有桌面终端标签页暂不支持直接接续。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(22.dp))
                Button(onClick = { if (model.connected) model.launch(shell = true) else changePage(2) }) { Text(if (!model.connected) "设置运行环境" else if (model.localExecution) "打开本地终端" else "连接 SSH") }
            }
        } else {
            Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("终端", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
                Text(usageCompact(model.usage), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                TextButton(onClick = { model.launch(shell = true) }, enabled = model.connected && !model.busy) { Text(if (model.localExecution) "＋ Shell" else "＋ SSH") }
                TextButton(onClick = toggleFullscreen) { Text(if (fullscreen) "退出全屏" else "全屏") }
            }
            ScrollableTabRow(selectedTabIndex = model.sessions.indexOfFirst { it.id == model.selectedSession }.coerceAtLeast(0), edgePadding = 4.dp) {
                model.sessions.forEach { session ->
                    Tab(selected = model.selectedSession == session.id, onClick = { model.selectedSession = session.id }, text = { Text(session.title, maxLines = 1, overflow = TextOverflow.Ellipsis) })
                }
            }
            val session = model.sessions.firstOrNull { it.id == model.selectedSession } ?: model.sessions.first()
            key(session.id) { TerminalPanel(model, session, Modifier.weight(1f)) }
        }
    }
    if (selectModel) ModelPicker(model, onDismiss = { selectModel = false })
    if (showHistory) HistoryPicker(model, onDismiss = { showHistory = false }, onSelect = {
        model.chooseHistory(it)
        expanded = true
        showHistory = false
    })
    if (confirmCompatibility) AlertDialog(onDismissRequest = { confirmCompatibility = false },
        title = { Text("启用 Codex 命令兼容模式？") },
        text = { Text("部分安卓设备的 Debian/PRoot 无法运行 Codex Linux 沙箱，表现为能聊天但命令执行失败。启用后，Codex 将不使用这层文件与网络沙箱，并保留按需审批：是否请求确认由 Codex 决定，普通命令可以直接执行，并非逐条确认。它仍受 Android 的应用隔离限制，但可访问 Termux 及绑定给 Debian 的文件，范围不限于当前项目。仅用于你信任的任务。已运行终端保持原权限，之后新建 Codex 使用此选择，可随时关闭。") },
        confirmButton = { TextButton(onClick = { model.updateOptions(model.options.copy(codexCompatibility = true)); confirmCompatibility = false }) { Text("启用并记住") } },
        dismissButton = { TextButton(onClick = { confirmCompatibility = false }) { Text("保留沙箱") } })
    if (confirmResume) AlertDialog(onDismissRequest = { confirmResume = false }, title = { Text("恢复历史会话？") },
        text = { Text(model.selectedHistory?.let {
            "${it.title}\n配置：${model.profile?.name.orEmpty()}\n网关：${model.profile?.gateway.orEmpty()}\n模型：${model.options.model}\n\n历史上下文会发送到这个网关，可能产生费用。原会话会保留。"
        } ?: "历史上下文会发送给当前调用 Key 和所选模型，可能产生费用。") },
        confirmButton = { TextButton(onClick = { confirmResume = false; model.launch() }) { Text("恢复并启动") } },
        dismissButton = { TextButton(onClick = { confirmResume = false }) { Text("取消") } })
}

@Composable private fun HistoryPicker(model: WorkbenchModel, onDismiss: () -> Unit, onSelect: (LocalConversation) -> Unit) {
    var query by remember { mutableStateOf("") }
    val rows = model.history.filter { row ->
        listOf(row.title, row.workspace, row.model, row.client).any { it.contains(query.trim(), ignoreCase = true) }
    }
    AlertDialog(onDismissRequest = onDismiss, title = { Text("本地对话历史") }, text = {
        Column {
            OutlinedTextField(query, { query = it }, label = { Text("搜索标题、模型或项目") }, singleLine = true)
            when {
                model.historyLoading -> LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 10.dp))
                model.historyError.isNotEmpty() -> Text(model.historyError, Modifier.padding(top = 10.dp))
                rows.isEmpty() -> Text("暂无匹配的本地对话", Modifier.padding(top = 10.dp))
                else -> LazyColumn(Modifier.heightIn(max = 300.dp)) {
                    items(rows, key = { it.reference }) { row ->
                        val profile = model.profiles.firstOrNull { it.id == row.profileId }?.name ?: "来源配置未知"
                        Column(Modifier.fillMaxWidth().clickable { onSelect(row) }.padding(vertical = 10.dp)) {
                            Text(row.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text("${if (row.client == "codex") "Codex" else "Claude"} · ${row.model.ifBlank { "模型未知" }} · $profile",
                                style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(row.workspace, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(java.text.DateFormat.getDateTimeInstance().format(java.util.Date(row.updatedAt * 1000)),
                                style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
        }
    }, confirmButton = { TextButton(onClick = onDismiss) { Text("关闭") } })
}

@Composable private fun ChoiceField(label: String, value: String, modifier: Modifier = Modifier, choices: List<Pair<String, String>>, choose: (String) -> Unit, empty: () -> Unit = {}) {
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
    var removing by remember { mutableStateOf<KeyProfile?>(null) }
    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("调用配置", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
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
                        Text("${profile.models.size} 个缓存模型 · ${profile.favorites.size} 个收藏", style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 8.dp))
                        Row {
                            TextButton(onClick = { model.chooseProfile(profile.id); model.refreshModels() }, enabled = !model.loadingModels) { Text("刷新模型") }
                            Spacer(Modifier.weight(1f))
                            TextButton(onClick = { removing = profile }) { Text("删除", color = MaterialTheme.colorScheme.error) }
                        }
                    }
                }
            }
            if (model.account.isNotBlank()) item { Text(model.account, style = MaterialTheme.typography.bodySmall) }
            item {
                Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("本机用量", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                            TextButton(onClick = model::refreshUsage, enabled = model.connected && !model.usageLoading) { Text(if (model.usageLoading) "读取中…" else "刷新") }
                        }
                        Text("输入 ${usageNumber(model.usage.inputTokens)} · 输出 ${usageNumber(model.usage.outputTokens)} · 请求 ${model.usage.requests}", style = MaterialTheme.typography.bodySmall)
                        Text(if (model.usage.estimatedCost != null) "费用：客户端估算 $${String.format(Locale.US, "%.4f", model.usage.estimatedCost)}" else "费用：未提供", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
                        Text(model.usage.note, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            item { Spacer(Modifier.height(16.dp)) }
        }
    }
    if (adding) AddProfileDialog(model) { adding = false }
    removing?.let { profile -> AlertDialog(onDismissRequest = { removing = null }, title = { Text("删除调用配置？") }, text = { Text("将删除本机保存的“${profile.name}”及加密 Key。已启动的终端继续使用启动时的配置。") },
        confirmButton = { TextButton(onClick = { model.removeProfile(profile.id); removing = null }) { Text("删除") } }, dismissButton = { TextButton(onClick = { removing = null }) { Text("取消") } }) }
}

@Composable private fun AddProfileDialog(model: WorkbenchModel, dismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }; var gateway by remember { mutableStateOf("https://") }; var key by remember { mutableStateOf("") }
    var validate by remember { mutableStateOf(true) }
    var allowInsecureLan by remember { mutableStateOf(false) }
    AlertDialog(onDismissRequest = { if (!model.busy) dismiss() }, title = { Text("添加调用配置") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(9.dp)) {
            OutlinedTextField(name, { name = it }, label = { Text("配置名称") }, singleLine = true)
            OutlinedTextField(gateway, { gateway = it }, label = { Text("网关根地址") }, singleLine = true)
            OutlinedTextField(key, { key = it }, label = { Text("网关签发的调用 Key") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
            Text("127.0.0.1 指这部手机。电脑上的本机网关需要另外提供可访问地址或安全隧道。", style = MaterialTheme.typography.labelSmall)
            Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(validate, { validate = it }); Text("保存时读取模型列表", style = MaterialTheme.typography.bodySmall) }
            Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(allowInsecureLan, { allowInsecureLan = it }); Text("允许可信局域网 HTTP（Key 会在局域网明文传输）", style = MaterialTheme.typography.bodySmall) }
            Text("读取列表只发送 GET，不调用模型。", style = MaterialTheme.typography.labelSmall)
            if (model.error.isNotBlank()) Text(model.error, color = MaterialTheme.colorScheme.error)
        }
    }, confirmButton = { TextButton(onClick = { model.saveProfile(name, gateway, key, validate, allowInsecureLan) { key = ""; dismiss() } }, enabled = !model.busy && name.isNotBlank() && key.isNotBlank()) { Text(if (model.busy) "保存中…" else "保存") } },
        dismissButton = { TextButton(onClick = { key = ""; dismiss() }, enabled = !model.busy) { Text("取消") } })
}

private val setupCommand = """
mkdir -p ~/.termux
grep -qxF 'allow-external-apps=true' ~/.termux/termux.properties 2>/dev/null || printf '\nallow-external-apps=true\n' >> ~/.termux/termux.properties
termux-reload-settings
""".trimIndent()

@Composable private fun EnvironmentScreen(model: WorkbenchModel, requestPermission: () -> Unit) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val refreshed = model.environmentRevision
    val version = model.termux.version()
    var addingComputer by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("手机本地运行", style = MaterialTheme.typography.headlineSmall)
        Text("首次完成 Termux 授权后，在这里自动准备 Debian、Node.js、Codex 和 Claude；已有组件会复用，安装进度会保留。", color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("1 · 安装并打开 Termux", fontWeight = FontWeight.SemiBold)
            Text(if (version == null) "尚未检测到 Termux" else "已安装：$version", style = MaterialTheme.typography.bodyMedium)
            Text("候选推荐 v0.118.3 · 官方 GitHub 稳定版。已有环境先保留，主程序与插件保持同源。", style = MaterialTheme.typography.bodySmall)
            Row {
                TextButton(onClick = { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(TERMUX_ARM64_APK))) }) { Text("直接下载 ARM64") }
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
            Text("3 · 自动安装本地 Agent", fontWeight = FontWeight.SemiBold)
            Text(if (model.termux.permitted()) "Termux 执行权限已授予" else "需要授予工作台 Termux 执行权限", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = requestPermission, enabled = version != null && !model.termux.permitted()) { Text("授权") }
                Button(onClick = model::installRuntime, enabled = version != null && model.termux.permitted() && !model.busy && !model.runtimeInstalling) { Text(if (model.busy || model.runtimeInstalling) "准备中…" else if (model.runtimeState == "ready") "检查并补齐" else "一键安装并检查") }
            }
            Text(model.runtimeStage, style = MaterialTheme.typography.bodyMedium)
            if (model.busy || model.runtimeInstalling) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (model.runtimeLog.isNotBlank()) {
                var showLog by remember { mutableStateOf(false) }
                TextButton(onClick = { showLog = !showLog }) { Text(if (showLog) "收起安装日志" else "查看安装日志") }
                if (showLog) Text(model.runtimeLog.takeLast(7000), fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                    modifier = Modifier.heightIn(max = 260.dp).verticalScroll(rememberScrollState()))
            }
            Text("只下载缺失依赖和检查版本，不调用模型。完成后在工作区选择项目和调用配置，直接启动。", style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = model::connect, enabled = !model.busy) { Text("仅连接终端桥接") }
        } }
        Text("桥接仅监听手机回环地址，最多同时运行 8 个终端。切后台暂停读取，回前台自动恢复原终端；临时连接失败会自动重试。Termux 被系统终止时任务仍会中断。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        HorizontalDivider()
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("SSH 电脑", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            TextButton(onClick = { addingComputer = true }) { Text("添加电脑") }
        }
        Text("通过 Termux 的 OpenSSH 连接 Mac、Windows 或服务器。支持 SSH 配置别名和 ProxyJump；密码、私钥与主机指纹由 Termux 管理。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        model.sshTargets.forEach { target ->
            OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp)) {
                Text(target.name, fontWeight = FontWeight.Medium)
                Text(target.destination, style = MaterialTheme.typography.bodySmall)
                Row {
                    TextButton(onClick = { model.updateOptions(model.options.copy(executionId = target.id)); model.message("已选择 ${target.name}，可回工作区连接 SSH") }) { Text("选择") }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { model.removeSshTarget(target.id) }) { Text("删除配置") }
                }
            } }
        }
        Text("首次连接请核对电脑上的 SSH 主机指纹。这里打开新的远程 Shell，不接管桌面已打开的标签页；网络断开后远程任务是否保留取决于电脑端的会话管理。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        HorizontalDivider()
        Text("后续执行环境", fontWeight = FontWeight.SemiBold)
        Text("本地默认使用 Debian Linux 用户空间；它共享安卓内核，并非完整虚拟机。完整虚拟机与桌面会话接续另行开发。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text("Android ${BuildConfig.VERSION_NAME} · $refreshed", style = MaterialTheme.typography.labelSmall)
    }
    if (addingComputer) AddComputerDialog(model) { addingComputer = false }
}

@Composable private fun AddComputerDialog(model: WorkbenchModel, dismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var host by remember { mutableStateOf("") }
    var user by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("22") }
    AlertDialog(onDismissRequest = dismiss, title = { Text("添加 SSH 电脑") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(name, { name = it }, label = { Text("电脑名称") }, singleLine = true)
            OutlinedTextField(host, { host = it }, label = { Text("IP、域名或 SSH 别名") }, singleLine = true)
            OutlinedTextField(user, { user = it }, label = { Text("用户名（可留空使用 SSH 配置）") }, singleLine = true)
            OutlinedTextField(port, { port = it }, label = { Text("SSH 端口") }, singleLine = true)
            Text("需先在电脑启用 SSH，并确保手机可达。SSH 别名、跳板、私钥请在 Termux 的 ~/.ssh/config 配置；不会复制电脑已有的凭据。", style = MaterialTheme.typography.bodySmall)
            if (model.error.isNotBlank()) Text(model.error, color = MaterialTheme.colorScheme.error)
        }
    }, confirmButton = { TextButton(onClick = { model.saveSshTarget(name, host, user, port, dismiss) }) { Text("保存") } },
        dismissButton = { TextButton(onClick = dismiss) { Text("取消") } })
}

private fun modeName(value: String) = when (value) { "continue" -> "继续最近会话"; "resume" -> "选择历史会话"; else -> "新会话" }

private fun usageNumber(value: Long?): String = value?.toString() ?: "未提供"
private fun usageCompact(value: UsageSummary): String = if (value.inputTokens == null && value.outputTokens == null) "Token 未提供" else "Token ${usageNumber(value.inputTokens)} / ${usageNumber(value.outputTokens)}"

@Composable private fun TerminalPanel(model: WorkbenchModel, session: TerminalSession, modifier: Modifier = Modifier) {
    var closing by remember { mutableStateOf(false) }
    var fontDelta by remember { mutableIntStateOf(0) }
    var keyboardRequest by remember { mutableIntStateOf(0) }
    var scrollRequest by remember { mutableIntStateOf(0) }
    var bottomRequest by remember { mutableIntStateOf(0) }
    Column(modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            listOf("Esc" to "\u001b", "Tab" to "\t", "Ctrl+C" to "\u0003").forEach { (title, value) ->
                TextButton(onClick = { model.send(session.id, value) }, contentPadding = PaddingValues(horizontal = 7.dp), modifier = Modifier.height(38.dp).widthIn(min = 40.dp)) { Text(title, fontSize = 11.sp) }
            }
            TextButton(onClick = { scrollRequest-- }, contentPadding = PaddingValues(horizontal = 7.dp), modifier = Modifier.height(38.dp).widthIn(min = 40.dp)) { Text("↑", fontSize = 11.sp) }
            TextButton(onClick = { scrollRequest++ }, contentPadding = PaddingValues(horizontal = 7.dp), modifier = Modifier.height(38.dp).widthIn(min = 40.dp)) { Text("↓", fontSize = 11.sp) }
            TextButton(onClick = { bottomRequest++ }, contentPadding = PaddingValues(horizontal = 7.dp), modifier = Modifier.height(38.dp).widthIn(min = 48.dp)) { Text("底部", fontSize = 11.sp) }
            TextButton(onClick = { keyboardRequest++ }, contentPadding = PaddingValues(2.dp), modifier = Modifier.widthIn(min = 40.dp)) { Text("键盘", fontSize = 11.sp) }
            Spacer(Modifier.width(8.dp))
            TextButton(onClick = { fontDelta++ }, contentPadding = PaddingValues(3.dp), modifier = Modifier.width(30.dp)) { Text("A+") }
            TextButton(onClick = { fontDelta-- }, contentPadding = PaddingValues(3.dp), modifier = Modifier.width(30.dp)) { Text("A−") }
            IconButton(onClick = { closing = true }, modifier = Modifier.width(34.dp)) { Icon(Icons.Outlined.Close, "关闭此终端") }
        }
        Box(Modifier.weight(1f).fillMaxWidth().heightIn(min = 220.dp)) {
            TerminalSurface(model, session, fontDelta, keyboardRequest, scrollRequest, bottomRequest, Modifier.fillMaxSize())
        }
        if (session.exitCode != null) Text("终端已退出：${session.exitCode}", style = MaterialTheme.typography.labelSmall)
    }
    if (closing) AlertDialog(onDismissRequest = { closing = false }, title = { Text("结束此终端？") }, text = { Text("该标签中的 Agent 与 Shell 将一起停止。其他终端不受影响。") },
        confirmButton = { TextButton(onClick = { closing = false; model.close(session.id) }) { Text("结束终端") } }, dismissButton = { TextButton(onClick = { closing = false }) { Text("取消") } })
}

private class TerminalCursor(var serial: Long = 0, var fontDelta: Int = 0, var scrollRequest: Int = 0, var bottomRequest: Int = 0)

@SuppressLint("SetJavaScriptEnabled")
@Composable private fun TerminalSurface(model: WorkbenchModel, session: TerminalSession, fontDelta: Int, keyboardRequest: Int, scrollRequest: Int, bottomRequest: Int, modifier: Modifier) {
    val context = LocalContext.current
    var ready by remember { mutableStateOf(false) }
    val cursor = remember { TerminalCursor() }
    val handler = remember { Handler(Looper.getMainLooper()) }
    val browser = remember {
        TerminalWebView(context) { model.send(session.id, it) }.apply {
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
    DisposableEffect(browser) { onDispose { browser.removeJavascriptInterface("Workbench"); browser.destroy() } }
    LaunchedEffect(ready) {
        if (ready) {
            browser.requestFocus()
            browser.evaluateJavascript("window.focusTerminal()", null)
            context.getSystemService(android.view.inputmethod.InputMethodManager::class.java).restartInput(browser)
        }
    }
    LaunchedEffect(keyboardRequest) {
        if (keyboardRequest > 0) browser.showTerminalKeyboard()
    }
    Box(modifier) {
        AndroidView(factory = { browser }, modifier = Modifier.fillMaxSize(), update = { view ->
            if (ready) {
                session.output.filter { it.serial > cursor.serial }.forEach { chunk ->
                    view.evaluateJavascript("window.writeChunk(${JSONObject.quote(chunk.encoded)})", null)
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
