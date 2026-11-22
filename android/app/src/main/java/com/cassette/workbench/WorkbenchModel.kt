package com.cassette.workbench

import android.app.Application
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Base64
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.ScheduledFuture

data class OutputChunk(val serial: Long, val encoded: String, val replay: Boolean = false)
data class LocalConversation(
    val id: String, val reference: String, val client: String, val title: String,
    val workspace: String, val model: String, val profileId: String, val updatedAt: Long,
    val preview: String = "",
    val configuredModel: String = model,
    val gateway: String = "",
)
data class UsageSummary(
    val inputTokens: Long? = null,
    val outputTokens: Long? = null,
    val cachedInputTokens: Long? = null,
    val cacheCreationInputTokens: Long? = null,
    val reasoningOutputTokens: Long? = null,
    val requests: Int = 0,
    val estimatedCost: Double? = null,
    val pricingStatus: String = "unavailable",
    val note: String = "网关/CLI 未返回费用；这里仅显示本地 Token 记录",
)

private fun JSONObject.longOrNull(name: String): Long? =
    if (!has(name) || isNull(name)) null else optLong(name).takeIf { it >= 0 }

data class TerminalSession(
    val id: String, val title: String, val workspace: String, val client: String,
    val output: List<OutputChunk> = emptyList(), val after: Long = 0,
    val exitCode: Int? = null, val error: String = "",
    val bridgePort: Int = 0,
    val restoredOutputPending: Boolean = false,
)

class WorkbenchModel(application: Application) : AndroidViewModel(application) {
    private val store = ProfileStore(application)
    val termux = TermuxConnection(application, store)
    private val worker = Executors.newFixedThreadPool(3)
    private val inputWorker = Executors.newSingleThreadExecutor()
    private val timer = Executors.newSingleThreadScheduledExecutor()
    private val main = Handler(Looper.getMainLooper())
    private var polling = false
    @Volatile private var foreground = false
    private var connectionGeneration = 0
    private var pollTask: ScheduledFuture<*>? = null
    private var needsProbe = true
    private var probeAt = 0L
    private var probeFailures = 0
    private var autoBootstrapAttempted = false
    private var bridgeIssue = ""
    private val terminalBridgeRetryAt = mutableMapOf<Int, Long>()
    var profiles by mutableStateOf(store.profiles()); private set
    var sshTargets by mutableStateOf(store.sshTargets()); private set
    var options by mutableStateOf(store.options()); private set
    var showAgentPermissions by mutableStateOf(!options.permissionsReviewed)
    var showTools by mutableStateOf(false)
    var toolsBusy by mutableStateOf(false); private set
    var toolsMessage by mutableStateOf(""); private set

    fun manageTools(client: String, environment: String, payload: JSONObject?, completed: (JSONObject) -> Unit) {
        if (!connected || toolsBusy) { toolsMessage = "请先连接 Termux，或等待当前工具操作完成"; return }
        toolsBusy = true
        toolsMessage = "正在读取所选环境；虚拟机未运行时需要启动"
        val requestPayload = payload?.let { JSONObject(it.toString()).put("client", client).put("environment", environment) }
        fun performRequest(allowRecovery: Boolean) { worker.execute {
            try {
                if (!termux.supportsRuntime()) {
                    check(allowRecovery) { "桥接升级后仍不兼容，请重新连接 Termux" }
                    main.post { connectInternal(false, onFailed = { reason -> toolsBusy = false; toolsMessage = reason }) { performRequest(false) } }
                    return@execute
                }
                val body = requestPayload
                val path = if (body == null) "/tools?client=$client&environment=$environment" else "/tools"
                val result = termux.request(path, if (body == null) "GET" else "POST", body, 60000)
                main.post { toolsBusy = false; toolsMessage = if (body == null) "配置已载入；连接和模型能力尚未验证" else "已保存到私有目录；只影响后续会话"; completed(result) }
            } catch (failure: Exception) { main.post { toolsBusy = false; toolsMessage = failure.message ?: "工具操作失败" } }
        } }
        performRequest(true)
    }

    fun saveAgentPermissions(codex: String, claude: String) {
        require(codex in listOf("restricted", "standard", "full") && claude in listOf("standard", "full"))
        updateOptions(options.copy(codexPermission = codex, claudePermission = claude, permissionsReviewed = true))
        showAgentPermissions = false
        notice = "已保存权限设置；之后新建、恢复和分支的本地会话使用此设置"
    }
    var history by mutableStateOf(emptyList<LocalConversation>()); private set
    var historyLoading by mutableStateOf(false); private set
    var historyError by mutableStateOf(""); private set
    var selectedHistory by mutableStateOf<LocalConversation?>(null); private set
    var sessions by mutableStateOf(emptyList<TerminalSession>()); private set
    var selectedSession by mutableStateOf("")
    var busy by mutableStateOf(false); private set
    var loadingModels by mutableStateOf(false); private set
    var connected by mutableStateOf(false); private set
    var bridgeReconnecting by mutableStateOf(false); private set
    var notice by mutableStateOf(""); private set
    var error by mutableStateOf(""); private set
    var account by mutableStateOf(""); private set
    var usage by mutableStateOf(UsageSummary()); private set
    var usageLoading by mutableStateOf(false); private set
    var environmentRevision by mutableStateOf(0); private set
    var runtimeStage by mutableStateOf("尚未检查本地 Agent 环境"); private set
    var runtimeLog by mutableStateOf(""); private set
    var runtimeState by mutableStateOf("idle"); private set
    var runtimeEnvironment by mutableStateOf(store.runtimeEnvironment()); private set
    var runtimeEnvironmentsKnown by mutableStateOf(false); private set
    var debianInstalled by mutableStateOf(false); private set
    var localAgentSupported by mutableStateOf(true); private set
    var deviceArchitecture by mutableStateOf(""); private set
    var vmInstalled by mutableStateOf(false); private set
    var vmSupported by mutableStateOf(true); private set
    var vmArchitecture by mutableStateOf(""); private set
    var vmAcceleration by mutableStateOf("tcg"); private set
    var vmState by mutableStateOf("stopped"); private set
    var vmBusy by mutableStateOf(false); private set
    private var runtimePollAt = 0L
    private var usagePollAt = 0L
    private var runtimePolling = false
    val runtimeInstalling get() = runtimeState == "running"
    val profile get() = profiles.firstOrNull { it.id == options.profileId }
    val sshTarget get() = sshTargets.firstOrNull { it.id == options.executionId }
    val localExecution get() = options.executionId == "local"
    val executionName get() = if (localExecution) {
        if (!localAgentSupported) "手机本地 · Shell" else "手机本地 · " + if (runtimeEnvironment == "vm") "Linux VM" else "Debian"
    } else sshTarget?.name ?: "请选择电脑"

    init {
        sessions = store.sessionRows().let { rows -> (0 until rows.length()).mapNotNull { index ->
            runCatching { rows.getJSONObject(index).let { TerminalSession(it.getString("id"), it.getString("title"), it.getString("workspace"), it.getString("client"), bridgePort = it.optInt("bridge_port", termux.currentPort), restoredOutputPending = true) } }.getOrNull()
        } }
        selectedSession = sessions.firstOrNull()?.id.orEmpty()
    }

    fun setForeground(value: Boolean) {
        if (foreground == value) return
        foreground = value
        if (value) autoBootstrapAttempted = false
        connectionGeneration++
        needsProbe = true
        probeAt = 0L
        bridgeReconnecting = value && termux.configured
        pollTask?.cancel(false)
        pollTask = if (value) timer.scheduleWithFixedDelay({ main.post { poll() } }, 0, 250, TimeUnit.MILLISECONDS) else null
    }

    private fun bridgeRecovered() {
        val wasRecovering = bridgeReconnecting || !connected
        connected = true
        bridgeReconnecting = false
        needsProbe = false
        probeFailures = 0
        probeAt = 0L
        if (error == bridgeIssue || error.startsWith("终端输入未送达：")) error = ""
        bridgeIssue = ""
        if (wasRecovering) notice = if (sessions.isEmpty()) "本地桥接已连接" else "已恢复原终端连接"
        if (wasRecovering) refreshUsage()
    }

    private fun bridgeUnavailable() {
        connected = false
        needsProbe = true
        bridgeReconnecting = true
        probeFailures++
        probeAt = SystemClock.elapsedRealtime() + when (probeFailures) { 1 -> 1000L; 2 -> 2000L; 3 -> 4000L; else -> 8000L }
        if (probeFailures >= 3) {
            bridgeIssue = "暂时无法连接 Termux 桥接，正在自动重试；原终端标签已保留。若 Termux 已被系统终止，任务无法自动恢复。"
            if (error.isBlank() || error == bridgeIssue) error = bridgeIssue
            // After a reboot the saved endpoint may be gone. Start the bridge
            // once per foreground visit; never replace a task or kill Termux.
            if (!autoBootstrapAttempted && !busy && termux.permitted() && termux.version() != null) {
                autoBootstrapAttempted = true
                connectInternal(false)
            }
        }
    }

    fun message(value: String) { notice = value; error = "" }
    fun environmentChanged() { environmentRevision++ }
    fun updateOptions(value: TaskOptions) {
        val workspaceChanged = value.workspace != options.workspace
        val next = if (value.mode == "resume" && (value.executionId != "local"
                || (runtimeEnvironment != "vm" && (value.client != selectedHistory?.client || workspaceChanged))))
            value.copy(mode = "new") else value
        if (next.mode != "resume") selectedHistory = null
        if (workspaceChanged) history = emptyList()
        options = next
        store.saveOptions(if (next.mode == "resume") next.copy(mode = "new") else next)
    }

    private fun readHistory(directory: String?, client: String? = null): List<LocalConversation> {
        val query = if (directory == null) "scope=all" else
            "workspace=" + java.net.URLEncoder.encode(directory, Charsets.UTF_8.name())
        val rows = termux.request("/history?$query" + (client?.let { "&client=$it" } ?: ""), timeout = 15000)
            .getJSONArray("sessions")
        return (0 until rows.length()).map { index ->
            val row = rows.getJSONObject(index)
            LocalConversation(row.getString("id"), row.getString("history_ref"), row.getString("client"),
                row.getString("title"), row.getString("workspace"), row.getString("model"),
                row.getString("profile_id"), row.getLong("updated_at"), row.optString("preview"),
                row.optString("configured_model", row.getString("model")), row.optString("gateway"))
        }
    }

    fun refreshHistory(allProjects: Boolean = false) {
        if (historyLoading) return
        historyLoading = true
        history = emptyList()
        historyError = ""
        val directory = if (allProjects) null else options.workspace
        worker.execute {
            val result = runCatching { readHistory(directory) }
            main.post {
                result.onSuccess { rows -> if (directory == null || options.workspace == directory) history = rows }
                    .onFailure { historyError = "无法读取本地历史，请在运行环境页重新连接 Termux" }
                historyLoading = false
            }
        }
    }

    fun restoreRecentInProject() {
        if (!connected || busy || !localExecution || runtimeEnvironment != "debian") return
        val snapshot = options
        if (snapshot.workspace.isBlank()) { error = "请先选择项目目录"; return }
        busy = true
        worker.execute {
            val result = runCatching { readHistory(snapshot.workspace, snapshot.client).firstOrNull() }
            main.post {
                busy = false
                if (!localExecution || runtimeEnvironment != "debian" || options.workspace != snapshot.workspace || options.client != snapshot.client) return@post
                result.onSuccess { entry ->
                    if (entry == null) error = "当前项目暂无该客户端的对话可恢复"
                    else restoreOriginal(entry)
                }.onFailure { error = safeError(it as? Exception ?: Exception()) }
            }
        }
    }

    fun restoreOriginal(entry: LocalConversation) {
        launch(original = entry)
    }

    fun chooseHistory(entry: LocalConversation) {
        selectedHistory = entry
        updateOptions(options.copy(executionId = "local", client = entry.client, mode = "resume"))
    }
    fun saveSshTarget(name: String, host: String, user: String, port: String, done: () -> Unit) {
        runCatching { saveComputer(SshTargetPolicy.validate(name, host, user, port), done) }
            .onFailure { error = it.message ?: "无法保存电脑" }
    }

    fun importSshConnection(code: String, remember: Boolean, done: () -> Unit) {
        runCatching { saveComputer(SshTargetPolicy.connectionCode(code, remember), done) }
            .onFailure { error = it.message ?: "无法读取连接码" }
    }

    private fun saveComputer(info: SshTarget, done: () -> Unit) {
        val existing = sshTargets.firstOrNull { info.platform.isNotEmpty() && it.host == info.host &&
            it.user == info.user && it.port == info.port && it.hostKey == info.hostKey }
        val target = if (existing == null) info else info.copy(id = existing.id)
        val next = sshTargets.filterNot { it.id == target.id } + target
        store.saveSshTargets(next)
        sshTargets = next
        updateOptions(options.copy(executionId = target.id))
        message(if (target.platform.isEmpty()) "电脑已保存，首次连接请核对主机指纹" else "电脑已添加，可回工作区连接")
        done()
    }
    fun removeSshTarget(id: String) {
        val target = sshTargets.firstOrNull { it.id == id } ?: return
        if (busy) return
        if (target.platform.isNotEmpty()) {
            busy = true
            error = ""
            worker.execute {
                try {
                    if (!termux.supportsRuntime()) {
                        main.post { busy = false; connectInternal(false) { removeSshTarget(id) } }
                        return@execute
                    }
                    termux.request("/ssh-pairing/$id", "DELETE")
                    main.post { busy = false; removeComputer(id) }
                } catch (failure: Exception) {
                    main.post { busy = false; error = failure.message ?: "无法删除手机配对" }
                }
            }
        } else {
            removeComputer(id)
        }
    }

    private fun removeComputer(id: String) {
        runCatching {
            val next = sshTargets.filterNot { it.id == id }
            store.saveSshTargets(next); sshTargets = next
            if (options.executionId == id) updateOptions(options.copy(executionId = "local"))
            message("电脑已移除")
        }.onFailure { error = "电脑配置删除失败" }
    }
    fun chooseProfile(id: String) {
        val next = profiles.firstOrNull { it.id == id } ?: return
        updateOptions(options.copy(profileId = id, model = next.models.firstOrNull { it == options.model } ?: next.models.firstOrNull().orEmpty()))
        account = ""
    }

    fun saveProfile(name: String, address: String, key: String, validate: Boolean, allowInsecureLan: Boolean, done: () -> Unit) {
        if (busy) return
        busy = true; error = ""
        worker.execute {
            try {
                require(name.isNotBlank() && name.length <= 80 && key.isNotBlank() && !key.contains('\n')) { "请填写名称和完整调用 Key" }
                val gateway = GatewayPolicy.normalize(address, allowInsecureLan)
                val models = if (validate) GatewayClient.models(gateway, key.trim()) else emptyList()
                val created = KeyProfile(name = name.trim(), gateway = gateway, models = models, allowInsecureLan = allowInsecureLan)
                store.saveSecret(created.id, key.trim())
                main.post {
                    try {
                        val next = profiles + created
                        store.saveProfiles(next); profiles = next
                        updateOptions(options.copy(profileId = created.id, model = models.firstOrNull().orEmpty()))
                        notice = if (validate) "配置已保存，读取到 ${models.size} 个模型" else "配置已保存，等待连接网关"
                        done()
                    } catch (_: Exception) { store.removeSecret(created.id); error = "配置写入失败，未添加调用配置" }
                    busy = false
                }
            } catch (cause: Exception) { main.post { busy = false; error = safeError(cause) } }
        }
    }

    fun removeProfile(id: String) {
        val next = profiles.filterNot { it.id == id }
        runCatching { store.saveProfiles(next) }.onFailure { error = "无法删除调用配置"; return }
        profiles = next; store.removeSecret(id)
        if (options.profileId == id) updateOptions(options.copy(profileId = next.firstOrNull()?.id.orEmpty(), model = ""))
    }

    fun favorite(model: String) {
        val selected = profile ?: return
        val next = selected.copy(favorites = if (model in selected.favorites) selected.favorites - model else selected.favorites + model)
        profiles = profiles.map { if (it.id == next.id) next else it }; store.saveProfiles(profiles)
    }

    fun refreshModels() {
        val selected = profile ?: return
        if (loadingModels) return
        loadingModels = true; error = ""
        worker.execute {
            try {
                val key = store.secret(selected.id)
                val models = GatewayClient.models(selected.gateway, key)
                val balance = runCatching {
                    val response = GatewayClient.json(selected.gateway + "/v1/cchp/account", key)
                    val value = response.optJSONObject("balance")?.opt("spendable")
                    if (value == null || value == JSONObject.NULL) "账户接口未返回可消费额度" else "网关可消费额度：$value ${response.optString("currency", "USD") }"
                }.getOrDefault("此网关未提供可读取的账户信息")
                main.post {
                    profiles = profiles.map { if (it.id == selected.id) it.copy(models = models) else it }; store.saveProfiles(profiles)
                    if (options.profileId == selected.id) { account = balance; if (options.model.isBlank() && models.isNotEmpty()) updateOptions(options.copy(model = models[0])) }
                    notice = if (models.isEmpty()) "网关未返回模型；已保留手动填写的模型" else "已刷新 ${models.size} 个模型"
                    loadingModels = false
                }
            } catch (cause: Exception) { main.post { loadingModels = false; error = safeError(cause) + "；保留已缓存列表" } }
        }
    }

    fun refreshUsage() {
        if (!connected || usageLoading) return
        usageLoading = true
        worker.execute {
            val result = runCatching { termux.request("/usage", timeout = 8000) }
            main.post {
                result.onSuccess { data ->
                    usage = UsageSummary(
                        inputTokens = data.longOrNull("input_tokens"),
                        outputTokens = data.longOrNull("output_tokens"),
                        cachedInputTokens = data.longOrNull("cached_input_tokens"),
                        cacheCreationInputTokens = data.longOrNull("cache_creation_input_tokens"),
                        reasoningOutputTokens = data.longOrNull("reasoning_output_tokens"),
                        requests = data.optInt("requests", 0).coerceAtLeast(0),
                        estimatedCost = if (data.has("estimated_cost") && !data.isNull("estimated_cost")) data.optDouble("estimated_cost").takeIf { it >= 0 } else null,
                        pricingStatus = data.optString("pricing_status", "unavailable"),
                        note = data.optString("note", "网关/CLI 未返回费用；这里仅显示本地 Token 记录"),
                    )
                }
                usageLoading = false
            }
        }
    }

    fun connect() = connectInternal(false)
    fun installRuntime() = connectInternal(true)
    fun selectRuntimeEnvironment(value: String) {
        if (value !in listOf("debian", "vm")) return
        if (value == "debian" && !localAgentSupported) {
            runtimeEnvironment = value
            store.saveRuntimeEnvironment(value)
            return
        }
        if (value == "vm" && !vmSupported) {
            error = "此设备的 Termux 架构暂不支持完整 Linux VM；可使用 Debian Shell 或 SSH 电脑"
            return
        }
        if (runtimeEnvironment != value) {
            runtimeEnvironment = value
            updateOptions(options.copy(mode = "new"))
        }
        store.saveRuntimeEnvironment(value)
    }

    fun selectExecutionLocation(value: String) {
        when (value) {
            "local-debian" -> { updateOptions(options.copy(executionId = "local")); selectRuntimeEnvironment("debian") }
            "local-shell" -> { updateOptions(options.copy(executionId = "local")); selectRuntimeEnvironment("debian") }
            "local-vm" -> if (vmSupported) {
                updateOptions(options.copy(executionId = "local")); selectRuntimeEnvironment("vm")
            } else error = "此设备的 Termux 架构暂不支持完整 Linux VM；可使用 Debian Shell 或 SSH 电脑"
            else -> updateOptions(options.copy(executionId = value))
        }
    }

    fun controlVirtualMachine(action: String) {
        if (!connected || vmBusy || action !in listOf("start", "stop")) return
        if (action == "start" && !vmSupported) {
            error = "此设备的 Termux 架构暂不支持完整 Linux VM"
            return
        }
        vmBusy = true
        error = ""
        worker.execute {
            val result = runCatching { termux.request("/vm", "POST", JSONObject().put("action", action), 120000) }
            main.post {
                vmBusy = false
                result.onSuccess {
                    vmState = it.optString("state", "stopped")
                    vmAcceleration = it.optString("acceleration", "tcg")
                    notice = if (action == "start") "Linux 虚拟机进程已启动，可打开 Linux VM Shell 等待系统就绪" else "Linux 虚拟机已正常停止"
                }.onFailure { error = it.message ?: "Linux 虚拟机操作失败" }
            }
        }
    }

    private fun applyRuntime(data: JSONObject) {
        runtimeState = data.optString("state", "idle")
        runtimeStage = data.optString("stage")
        runtimeLog = data.optString("log")
        debianInstalled = data.optBoolean("debian_installed")
        deviceArchitecture = data.optString("architecture")
        localAgentSupported = data.optBoolean("local_agent_supported")
        data.optJSONObject("vm")?.let {
            runtimeEnvironmentsKnown = true
            vmInstalled = it.optBoolean("installed")
            vmSupported = it.optBoolean("supported")
            vmArchitecture = it.optString("architecture")
            vmState = it.optString("state", "stopped")
            vmAcceleration = it.optString("acceleration", "tcg")
            if (!vmSupported && runtimeEnvironment == "vm") {
                runtimeEnvironment = "debian"
                store.saveRuntimeEnvironment("debian")
                error = "已切换到 Debian：当前 Termux 架构不支持完整 Linux VM；也可通过 SSH 使用电脑环境"
            }
        }
    }

    private fun connectInternal(install: Boolean, onFailed: ((String) -> Unit)? = null, onConnected: (() -> Unit)? = null) {
        if (busy) { onFailed?.invoke("正在执行其他连接操作，请完成后重试工具设置"); return }
        val selectedEnvironment = runtimeEnvironment
        connectionGeneration++
        busy = true; error = ""; notice = "正在连接 Termux 本地桥接…"
        worker.execute {
            val alive = termux.ping()
            val compatible = alive && termux.supportsRuntime()
            if (compatible) {
                try {
                    val data = termux.request("/runtime", if (install) "POST" else "GET",
                        if (install) JSONObject().put("clients", JSONArray(listOf("codex", "claude"))).put("environment", selectedEnvironment) else null, 5000)
                    main.post { applyRuntime(data); bridgeRecovered(); busy = false; notice = "本地桥接已连接"; onConnected?.invoke() }
                } catch (cause: Exception) { main.post { busy = false; error = safeError(cause); onFailed?.invoke(error) } }
                return@execute
            }
            main.post {
                try {
                    runtimeStage = "准备基础桥接、Git 与 SSH；首次可能需要下载依赖"
                    saveSessions()
                    termux.startHost(upgrade = alive)
                    worker.execute {
                        var ready = false
                        var failure = ""
                        for (attempt in 0 until 300) {
                            if (termux.ping()) { ready = true; break }
                            failure = termux.bootstrapError()
                            if (failure.isNotBlank()) break
                            Thread.sleep(1000)
                        }
                        val data = if (ready) runCatching {
                            termux.request("/runtime", if (install) "POST" else "GET",
                                if (install) JSONObject().put("clients", JSONArray(listOf("codex", "claude"))).put("environment", selectedEnvironment) else null, 5000)
                        }.getOrNull() else null
                        main.post {
                            busy = false
                            if (ready) bridgeRecovered() else bridgeUnavailable()
                            if (data != null) applyRuntime(data)
                            if (!ready) error = failure.ifBlank { "准备仍未完成；请检查 Termux 网络与外部命令开关，再点重试。已有文件与任务已保留。" }
                            if (ready && data != null) onConnected?.invoke()
                            else onFailed?.invoke(error.ifBlank { "桥接未就绪，请重新连接后重试工具设置" })
                        }
                    }
                } catch (cause: Exception) { busy = false; error = safeError(cause); onFailed?.invoke(error) }
            }
        }
    }

    fun launch(shell: Boolean = false, shellKind: String = "termux", original: LocalConversation? = null, onOpened: () -> Unit = {}) {
        if (localExecution && !shell && !options.permissionsReviewed) { showAgentPermissions = true; return }
        if (!connected || busy) { error = "请先在环境页连接 Termux"; return }
        if (original != null && (!localExecution || runtimeEnvironment != "debian")) return
        val snapshot = if (original == null) options else options.copy(
            mode = "resume", workspace = original.workspace, model = original.configuredModel,
        )
        val selectedEnvironment = runtimeEnvironment
        val vmEnvironment = localExecution && !shell && selectedEnvironment == "vm"
        val source = original ?: selectedHistory.takeIf { !shell && snapshot.mode == "resume" && localExecution && !vmEnvironment }
        val selected = if (original == null) profile else profiles.firstOrNull { it.id == original.profileId }
        if (original != null && (selected == null || original.configuredModel.isBlank()
                    || original.gateway != selected.gateway)) {
            error = "原调用配置、Key 或网关不可用，可从当前项目的历史创建分支"
            return
        }
        val vmShell = shell && shellKind == "vm"
        val remote = sshTarget.takeUnless { vmShell }
        if (!localExecution && !vmShell && remote == null) { error = "请先选择 SSH 电脑"; return }
        if (localExecution && !shell && (selected == null || snapshot.model.isBlank())) { error = "请先选择调用配置和模型"; return }
        if (localExecution && !shell && !localAgentSupported) { error = "本地 Codex/Claude 需要 64 位 ARM64 或 x86_64；请通过 SSH 电脑运行"; return }
        if (vmEnvironment && !vmSupported) { error = "此设备的 Termux 架构暂不支持完整 Linux VM；可使用 Debian 或 SSH"; return }
        if (vmEnvironment && !vmInstalled) { error = "请先在运行环境页安装 / 检查完整 Linux 虚拟机"; return }
        if (localExecution && !shell && !vmEnvironment && snapshot.mode == "resume" && source == null) { error = "请先选择要恢复的对话"; return }
        busy = true; error = ""
        worker.execute {
            try {
                // Upgrade only the endpoint for new tasks. Existing terminals
                // retain their original bridge port and credential.
                if (!termux.supportsRuntime()) {
                    main.post { busy = false; connectInternal(false) { launch(shell, shellKind, original, onOpened) } }
                    return@execute
                }
                val client = if (remote != null) "ssh" else if (shell) {
                    when (shellKind) { "debian" -> "debian-shell"; "vm" -> "vm-shell"; else -> "shell" }
                } else source?.client ?: snapshot.client
                val body = JSONObject().put("client", client)
                    .put("mode", if (remote == null) snapshot.mode else "new").put("workspace", snapshot.workspace)
                    .put("codex_compatibility", snapshot.codexCompatibility)
                    .put("permission_mode", if (client == "codex") snapshot.codexPermission else snapshot.claudePermission)
                    .put("environment", if (remote == null && !shell) selectedEnvironment else "debian")
                if (source != null) body.put("history_ref", source.reference).put("history_id", source.id)
                    .put("history_action", if (original == null) "fork" else "original")
                if (remote != null) body.put("ssh", JSONObject().put("host", remote.host).put("user", remote.user)
                    .put("port", remote.port).put("id", remote.id).put("platform", remote.platform)
                    .put("host_key", remote.hostKey).put("remember", remote.remember))
                else if (!shell && selected != null) body.put("profile_id", selected.id).put("model", snapshot.model)
                    .put("gateway", selected.gateway).put("key", store.secret(selected.id))
                val result = termux.request("/sessions", "POST", body, if (source != null) 15000 else 4000)
                val title = if (remote != null) "SSH · ${remote.name}" else when (client) {
                    "debian-shell" -> "Debian Shell"
                    "vm-shell" -> "Linux VM Shell"
                    "shell" -> "Termux Shell"
                    else -> "${if (client == "codex") "Codex" else "Claude"} · ${snapshot.model}" +
                        " · " + when {
                            client == "claude" -> if (snapshot.claudePermission == "full") "跳过工具确认" else "标准审批"
                            vmEnvironment -> "VM 内完整访问"
                            snapshot.codexCompatibility || snapshot.codexPermission == "full" -> "完整访问 · 按需审批"
                            snapshot.codexPermission == "standard" -> "项目内写入 · 允许联网"
                            else -> "项目内写入 · 限制联网"
                        }
                }
                val terminal = TerminalSession(result.getString("id"), title, remote?.destination ?: snapshot.workspace, client, bridgePort = termux.currentPort)
                main.post {
                    if (original == null && source != null && selectedHistory == source) updateOptions(options.copy(mode = "new"))
                    sessions = sessions + terminal; selectedSession = terminal.id; saveSessions(); busy = false
                    notice = when {
                        remote?.platform?.isNotEmpty() == true -> "正在打开 SSH。首次连接时请在终端登录电脑账号"
                        remote != null -> "正在打开 SSH，请在终端核对主机指纹并登录"
                        original != null -> "已用原配置恢复「${original.title}」"
                        source != null -> "已从「${source.title}」创建分支"
                        else -> "已创建独立终端；Ctrl+C 保留当前 Shell"
                    }
                    onOpened()
                }
            } catch (cause: Exception) { main.post { busy = false; error = safeError(cause) } }
        }
    }

    fun send(id: String, value: String) {
        sendBytes(id, value.toByteArray(Charsets.UTF_8))
    }
    fun sendBytes(id: String, value: ByteArray) {
        if (value.isEmpty()) return
        inputWorker.execute {
            try {
                termux.request("/sessions/$id/input", "POST", JSONObject().put("data", Base64.encodeToString(value, Base64.NO_WRAP)), bridgePort = sessions.firstOrNull { it.id == id }?.bridgePort ?: termux.currentPort)
            } catch (cause: Exception) {
                main.post {
                    if (sessions.any { it.id == id }) error = "终端输入未送达：${safeError(cause)}"
                }
            }
        }
    }
    fun resize(id: String, cols: Int, rows: Int) {
        val port = sessions.firstOrNull { it.id == id }?.bridgePort ?: termux.currentPort
        worker.execute { runCatching { termux.request("/sessions/$id/resize", "POST", JSONObject().put("cols", cols).put("rows", rows), bridgePort = port) } }
    }
    fun close(id: String) {
        worker.execute {
            val failure = runCatching { termux.request("/sessions/$id", "DELETE", bridgePort = sessions.firstOrNull { it.id == id }?.bridgePort ?: termux.currentPort) }.exceptionOrNull()
            main.post {
                sessions = sessions.filterNot { it.id == id }
                if (selectedSession == id) selectedSession = sessions.lastOrNull()?.id.orEmpty()
                saveSessions()
                if (failure != null) notice = "已移除终端标签；后台进程未确认停止，请检查 Termux"
            }
        }
    }
    private fun saveSessions() {
        store.saveSessions(JSONArray(sessions.map { JSONObject().put("id", it.id).put("title", it.title).put("workspace", it.workspace).put("client", it.client).put("bridge_port", it.bridgePort) }))
    }
    private fun poll() {
        if (!foreground || polling || busy || !termux.configured) return
        if (connected && !runtimePolling && SystemClock.elapsedRealtime() >= runtimePollAt) {
            runtimePollAt = SystemClock.elapsedRealtime() + 1500
            runtimePolling = true
            worker.execute {
                val data = runCatching { termux.request("/runtime") }.getOrNull()
                main.post { if (data != null) applyRuntime(data); runtimePolling = false }
            }
        }
        if (connected && SystemClock.elapsedRealtime() >= usagePollAt && !usageLoading) {
            usagePollAt = SystemClock.elapsedRealtime() + 5000
            refreshUsage()
        }
        val now = SystemClock.elapsedRealtime()
        if (now < probeAt) return
        val generation = connectionGeneration
        val checking = needsProbe || !connected
        val currentBridge = termux.currentPort
        val snapshot = if (checking) emptyList() else sessions.filter {
            it.exitCode == null && it.error.isBlank() && now >= (terminalBridgeRetryAt[it.bridgePort] ?: 0L)
        }.sortedBy { it.bridgePort != currentBridge }
        if (!checking && snapshot.isEmpty()) return
        polling = true
        worker.execute {
            try {
                if (checking) {
                    val alive = termux.ping()
                    main.post {
                        if (foreground && generation == connectionGeneration) {
                            if (alive) bridgeRecovered() else bridgeUnavailable()
                        }
                    }
                } else {
                    val unavailablePorts = mutableSetOf<Int>()
                    for (session in snapshot) {
                        if (!foreground) break
                        if (session.bridgePort in unavailablePorts) continue
                        try {
                            val data = termux.request("/sessions/${session.id}?after=${session.after}", bridgePort = session.bridgePort)
                            main.post {
                                if (!foreground || generation != connectionGeneration) return@post
                                sessions = sessions.map { current ->
                                    if (current.id != session.id) current else {
                                        val encoded = data.optString("data")
                                        val next = if (encoded.isBlank()) current.output else (current.output + OutputChunk(data.getLong("after"), encoded, current.restoredOutputPending)).takeLast(80)
                                        current.copy(after = data.getLong("after"), output = next, restoredOutputPending = false, exitCode = if (data.isNull("exit_code")) null else data.getInt("exit_code"))
                                    }
                                }
                                if (!data.isNull("exit_code")) {
                                    sessions = sessions.filterNot { it.id == session.id }
                                    if (selectedSession == session.id) selectedSession = sessions.lastOrNull()?.id.orEmpty()
                                    saveSessions()
                                }
                                probeFailures = 0
                            }
                        } catch (cause: Exception) {
                            val gone = cause.message?.contains("接口不存在") == true || cause.message?.contains("终端不存在") == true
                            if (!gone) unavailablePorts += session.bridgePort
                            main.post {
                                if (!foreground || generation != connectionGeneration) return@post
                                if (sessions.none { it.id == session.id }) return@post
                                if (gone) {
                                    sessions = sessions.filterNot { it.id == session.id }
                                    if (selectedSession == session.id) selectedSession = sessions.lastOrNull()?.id.orEmpty()
                                    saveSessions()
                                    notice = "原终端进程已不存在；已移除失效标签，可以重新启动 Agent。"
                                } else {
                                    // A legacy terminal can outlive its bridge. Keep other ports
                                    // polling and only let a failed health probe disconnect the app.
                                    terminalBridgeRetryAt[session.bridgePort] = SystemClock.elapsedRealtime() + 4000L
                                    if (session.bridgePort == termux.currentPort) needsProbe = true
                                    else if (session.id == selectedSession) notice = "此终端暂时无法连接，正在重试；其他终端可继续使用。"
                                }
                            }
                        }
                    }
                }
            } finally { main.post { polling = false } }
        }
    }
    override fun onCleared() { timer.shutdownNow(); worker.shutdownNow(); inputWorker.shutdownNow() }
    private fun safeError(cause: Exception): String = when (cause) {
        is java.net.SocketTimeoutException -> "连接超时，请检查网关或本地桥接"
        is java.net.ConnectException -> "无法连接服务，请确认地址与服务状态"
        is java.net.UnknownHostException -> "无法解析服务地址"
        is javax.net.ssl.SSLException -> "HTTPS 证书或连接校验失败"
        is IllegalArgumentException, is IllegalStateException, is SecurityException -> cause.message?.take(180) ?: "操作失败"
        else -> "操作未完成，请检查地址、权限与环境"
    }
}
