package com.cassette.workbench

import android.app.Application
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
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
    val archived: Boolean = false,
    val projectId: String? = null,
    val projectPath: String = "",
    val customTitle: Boolean = false,
) {
    val displayTitle get() = if (title.trimStart().startsWith("# AGENTS.md instructions")) preview.ifBlank { "未命名对话" } else title
}
data class HistoryProject(
    val id: String,
    val name: String,
    val parentId: String? = null,
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
    val sessions: List<UsageConversation> = emptyList(),
    val scopeNote: String = "",
)

private fun JSONObject.longOrNull(name: String): Long? =
    if (!has(name) || isNull(name)) null else optLong(name).takeIf { it >= 0 }

data class TerminalSession(
    val id: String, val title: String, val workspace: String, val client: String,
    val output: List<OutputChunk> = emptyList(), val after: Long = 0,
    val exitCode: Int? = null, val error: String = "",
    val bridgePort: Int = 0,
    val restoredOutputPending: Boolean = false,
    val remoteTarget: String = "",
    val remoteId: String = "",
    val sshTargetId: String = "",
    val input: TerminalInputState = TerminalInputState(),
) {
    val canWrite get() = input.canWrite
}

class WorkbenchModel(application: Application) : AndroidViewModel(application) {
    var remoteWorkbench by mutableStateOf<RemoteWorkbench?>(null); private set
    var remoteMessage by mutableStateOf(""); private set
    var remoteConnecting by mutableStateOf(false); private set
    var terminalInputSession by mutableStateOf(""); private set
    var terminalRevealVersion by mutableStateOf(0); private set
    fun terminalFocusChanged(id: String, focused: Boolean) {
        if (focused) terminalInputSession = id
        else if (terminalInputSession == id) terminalInputSession = ""
    }
    private var remoteLoginId = ""
    var remoteProfile by mutableStateOf("")
    var remoteModel by mutableStateOf("")
    var remoteClient by mutableStateOf("codex")
    var remoteWorkspace by mutableStateOf("")
    var remoteScope by mutableStateOf("workbench")
    var remoteModels by mutableStateOf(emptyList<String>()); private set
    private var remoteTarget = ""
    private var remoteRefreshing = false
    private var remotePollAt = 0L
    private var remoteWantedId = ""
    private var remoteRetryAt = 0L
    private var forceRemoteReconnect = false
    private var remoteAutoRetry = false
    @Volatile private var remoteConnectionEpoch = 0
    private var remoteOpeningEpoch = 0
    private var networkSignature = ""
    val historyWorkspace get() = if (localExecution) options.workspace else remoteWorkspace

    private fun remoteRequest(target: String, body: JSONObject): JSONObject =
        termux.request("/remote/$target/rpc", "POST", body, 70000)

    private fun sshBody(target: SshTarget) = JSONObject().put("host", target.host).put("user", target.user)
        .put("port", target.port).put("id", target.id).put("platform", target.platform)
        .put("host_key", target.hostKey).put("remember", target.remember)

    fun connectRemote() {
        val target = sshTarget ?: run { error = "请先在运行环境中添加并选择电脑"; return }
        if (busy) { notice = "正在准备连接，请等待当前操作完成"; return }
        remoteWantedId = target.id
        remoteAutoRetry = true
        if (!connected) { connectInternal(false) { connectRemote() }; return }
        val replaceConnection = forceRemoteReconnect
        forceRemoteReconnect = false
        if (!replaceConnection && remoteWorkbench != null && remoteTarget == target.id) { refreshRemote(); return }
        if (!replaceConnection && remoteConnecting && remoteLoginId.isNotEmpty() && sessions.any { it.id == remoteLoginId && it.exitCode == null }) {
            selectedSession = remoteLoginId; notice = "电脑登录终端已打开，请在终端完成登录"; return
        }
        if (target.platform.isBlank()) { error = "手动 SSH 主机请先使用普通 SSH；工作台接续需要电脑生成的连接码"; return }
        busy = true; error = ""; remoteMessage = "正在准备 ${target.name} 的 SSH 登录…"
        val epoch = ++remoteConnectionEpoch
        remoteOpeningEpoch = epoch
        remoteRetryAt = SystemClock.elapsedRealtime() + 30000
        worker.execute {
            try {
                if (!termux.supportsRuntime()) {
                    main.post { remoteOpeningEpoch = 0; busy = false; connectInternal(false) { connectRemote() } }; return@execute
                }
                val resolved = resolveRemoteAddress(target, epoch)
                check(remoteConnectionEpoch == epoch && foreground && sshTarget?.id == target.id) { "连接已取消" }
                val value = termux.request("/remote/connect", "POST", JSONObject().put("ssh", sshBody(resolved))
                    .put("replace_connection", replaceConnection), 15000)
                main.post {
                    if (remoteConnectionEpoch != epoch || sshTarget?.id != target.id) return@post
                    if (resolved != target) {
                        val next = sshTargets.map { if (it.id == target.id) resolved else it }
                        store.saveSshTargets(next)
                        sshTargets = next
                        notice = "已核对电脑身份并自动更新局域网地址，原配对保留"
                    }
                    val id = value.getString("id")
                    val duplicateSessions = duplicateLoginSessions(resolved, id)
                    sessions = upsertLoginSession(resolved, id, duplicateSessions)
                    remoteConnecting = true; remoteLoginId = id
                    selectedSession = id; saveSessions(); remoteMessage = "正在连接电脑；如需密码，请在下方电脑登录终端输入"; refreshRemote()
                    duplicateSessions.forEach { stale ->
                        worker.execute { runCatching { termux.request("/sessions/${stale.id}", "DELETE", bridgePort = stale.bridgePort) } }
                    }
                }
            } catch (cause: Exception) { main.post {
                if (remoteConnectionEpoch == epoch && sshTarget?.id == target.id) {
                    remoteWorkbench = null; remoteConnecting = false; remoteMessage = safeError(cause)
                }
            } } finally { main.post { if (remoteOpeningEpoch == epoch) { remoteOpeningEpoch = 0; busy = false } } }
        }
    }

    private fun resolveRemoteAddress(target: SshTarget, epoch: Int): SshTarget {
        fun verify(host: String, port: Int): JSONObject = termux.request("/remote/verify-address", "POST",
            JSONObject().put("ssh", sshBody(target)).put("host", host).put("port", port), 14000)
        val current = verify(target.host, target.port)
        if (current.optBoolean("verified")) return target.copy(host = current.getString("host"))
        main.post { if (remoteConnectionEpoch == epoch) remoteMessage = "正在局域网自动寻找 ${target.name} 的新地址…" }
        for (candidate in computerDiscovery.find(target)) {
            check(remoteConnectionEpoch == epoch && foreground) { "连接已取消" }
            val checked = verify(candidate.host, candidate.port)
            if (checked.optBoolean("verified")) return target.copy(host = checked.getString("host"), port = candidate.port)
        }
        throw IllegalStateException(if (current.optString("reason") == "identity")
            "原地址的电脑身份已变化，局域网内尚未找到原电脑；原配对保留，请勿向未知主机输入密码"
            else "尚未在局域网找到已配对电脑；请让两端连接同一 Wi-Fi，保持电脑工作台与 SSH 开启，将在前台自动重试")
    }

    fun refreshRemote() {
        val target = sshTarget ?: return
        if (!connected || remoteRefreshing) return
        val epoch = remoteConnectionEpoch
        remoteRefreshing = true
        worker.execute {
            try {
                val status = termux.request("/remote/${target.id}")
                val result = if (status.optBoolean("connected")) RemoteWorkbench.parse(remoteRequest(target.id, JSONObject().put("op", "state"))) else null
                main.post {
                    if (remoteConnectionEpoch != epoch || sshTarget?.id != target.id) return@post
                    if (remoteOpeningEpoch != 0) return@post
                    val loginId = status.optString("login_id")
                    if (loginId.isNotEmpty()) {
                        val duplicates = duplicateLoginSessions(target, loginId)
                        val next = upsertLoginSession(target, loginId, duplicates)
                        if (next.map { it.id } != sessions.map { it.id } || next != sessions) {
                            sessions = next
                            saveSessions()
                            duplicates.forEach { stale ->
                                worker.execute { runCatching { termux.request("/sessions/${stale.id}", "DELETE", bridgePort = stale.bridgePort) } }
                            }
                        }
                    }
                    if (result != null && (remoteWorkbench == null || remoteTarget != target.id)) {
                        remoteProfile = result.profile; remoteWorkspace = result.workspace; remoteClient = result.client; remoteModel = result.model
                        remoteModels = emptyList(); selectedHistory = null
                    }
                    remoteTarget = target.id; remoteWorkbench = result
                    remoteLoginId = loginId
                    remoteConnecting = status.optString("phase") == "connecting"
                    remoteAutoRetry = result != null || status.optBoolean("retryable") || status.optString("phase") == "idle"
                    remoteMessage = if (result == null) status.optString("error").ifBlank {
                        if (remoteConnecting) "正在连接电脑；请查看下方电脑登录终端，按提示输入密码" else "尚未连接电脑工作台"
                    } else "已连接 ${target.name} · ${result.platform}"
                    if (result != null && notice == "正在打开 SSH。首次连接时请在终端登录电脑账号") notice = remoteMessage
                    if (result == null && !remoteConnecting && remoteWantedId == target.id && foreground && !busy &&
                        (status.optBoolean("retryable") || status.optString("phase") == "idle") &&
                        SystemClock.elapsedRealtime() >= remoteRetryAt) connectRemote()
                }
            } catch (cause: Exception) { main.post { if (remoteConnectionEpoch == epoch && sshTarget?.id == target.id) { remoteWorkbench = null; remoteConnecting = false; remoteMessage = safeError(cause) } } }
            finally { main.post { remoteRefreshing = false } }
        }
    }

    private fun duplicateLoginSessions(target: SshTarget, keepId: String): List<TerminalSession> {
        val title = "电脑登录 · ${target.name}"
        return sessions.filter { session ->
            session.client == "ssh-login" && session.id != keepId &&
                (session.sshTargetId == target.id ||
                    (session.sshTargetId.isEmpty() && session.title == title &&
                        session.workspace == target.destination))
        }
    }

    private fun loginSession(target: SshTarget, id: String): TerminalSession {
        val title = "电脑登录 · ${target.name}"
        val existing = sessions.firstOrNull { it.id == id }
        return existing?.copy(title = title, workspace = target.destination, sshTargetId = target.id,
            bridgePort = termux.currentPort)
            ?: TerminalSession(id, title, target.destination, "ssh-login", bridgePort = termux.currentPort,
                sshTargetId = target.id)
    }

    private fun upsertLoginSession(target: SshTarget, id: String, duplicates: List<TerminalSession>): List<TerminalSession> {
        val duplicateIds = duplicates.mapTo(mutableSetOf()) { it.id }
        val existed = sessions.any { it.id == id }
        val login = loginSession(target, id)
        val next = sessions.mapNotNull { session ->
            when {
                session.id == id -> login
                session.id in duplicateIds -> null
                else -> session
            }
        }.toMutableList()
        if (!existed) next += login
        return next
    }

    fun selectRemoteProfile(id: String) { remoteProfile = id; remoteModel = ""; remoteModels = emptyList(); loadRemoteModels() }
    fun loadRemoteModels() {
        val target = sshTarget?.id ?: return
        val profile = remoteProfile
        if (busy) return
        busy = true
        worker.execute {
            try {
                val models = remoteRequest(target, JSONObject().put("op", "models").put("profile_id", profile)).getJSONArray("models").strings()
                main.post { if (sshTarget?.id == target && remoteProfile == profile) { remoteModels = models; if (remoteModel !in models) remoteModel = models.firstOrNull().orEmpty() } }
            } catch (cause: Exception) { main.post { error = safeError(cause) } }
            finally { main.post { busy = false } }
        }
    }

    fun launchRemote(action: String = "", source: LocalConversation? = null) {
        val target = sshTarget?.id ?: return
        if (busy || remoteWorkbench == null) return
        val body = JSONObject().put("op", "launch").put("client", source?.client ?: remoteClient)
            .put("workspace", source?.workspace ?: remoteWorkspace).put("scope", remoteScope)
            .put("model", remoteModel).put("profile_id", remoteProfile).put("history_action", action)
        if (remoteScope == "workbench" && remoteWorkbench?.capabilities?.contains("reasoning-effort") == true) {
            // Original history uses its own configuration. A recent-history lookup
            // is resolved on the computer, so never apply an unrelated UI selection.
            val effort = when {
                action == "recent" -> null
                action == "original" && source != null -> reasoningPreferences[ReasoningEffort.key(
                    source.client, "ssh:$target:${source.profileId}", source.configuredModel)]
                else -> remoteStartupReasoningEffort
            }
            if (effort != null) body.put("reasoning_effort", effort)
        }
        if (source != null) body.put("history_ref", source.reference)
        busy = true; error = ""
        worker.execute {
            try {
                val value = remoteRequest(target, body)
                attachRemoteInWorker(target, value.getString("id"), when (action) {
                    "original", "recent" -> "已用电脑原配置恢复对话"
                    "fork" -> "已用当前电脑配置创建独立分支"
                    else -> "已在电脑新建会话"
                })
                main.post { selectedHistory = null }
            } catch (cause: Exception) { main.post { error = safeError(cause) } }
            finally { main.post { busy = false; refreshRemote() } }
        }
    }

    fun attachRemote(id: String, computer: String? = null) {
        val target = computer ?: sshTarget?.id ?: return
        if (busy) return
        busy = true; error = ""
        worker.execute {
            try { attachRemoteInWorker(target, id) }
            catch (cause: Exception) { main.post { error = safeError(cause) } }
            finally { main.post { busy = false } }
        }
    }
    private fun attachRemoteInWorker(target: String, id: String, resultMessage: String = "已接续同一个电脑进程") {
        val row = termux.request("/remote/$target/attach", "POST", JSONObject().put("session_id", id), 70000)
        val session = TerminalSession(row.getString("id"), row.getString("title"), row.getString("workspace"), row.optString("client", "codex"),
            bridgePort = termux.currentPort, remoteTarget = target, remoteId = row.getString("remote_id"))
        main.post {
            val old = sessions.firstOrNull { it.remoteTarget == target && it.remoteId == session.remoteId }
            sessions = sessions.filterNot { it.remoteTarget == target && it.remoteId == session.remoteId } + (old?.copy(id = session.id, input = old.input.claimed()) ?: session)
            selectedSession = session.id; saveSessions()
            terminalRevealVersion++
            notice = "$resultMessage · ${session.workspace}；关闭手机标签只会断开"
        }
    }

    fun terminateRemote(id: String) {
        val session = sessions.firstOrNull { it.id == id && it.remoteTarget.isNotEmpty() } ?: return
        worker.execute {
            try { termux.request("/sessions/$id/terminate", "POST", JSONObject(), 70000, session.bridgePort); main.post { close(id); refreshRemote() } }
            catch (cause: Exception) { main.post { error = safeError(cause) } }
        }
    }

    fun uploadRemoteAttachment(id: String, uri: android.net.Uri) {
        val session = sessions.firstOrNull { it.id == id && it.remoteTarget.isNotEmpty() } ?: return
        if (selectedSession != id || busy) return
        val uploadId = java.util.UUID.randomUUID().toString()
        busy = true; error = ""; notice = "正在传输附件到电脑"
        worker.execute {
            var pasted = false
            try {
                val resolver = getApplication<Application>().contentResolver
                val name = resolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                    if (it.moveToFirst()) it.getString(0) else "attachment"
                } ?: "attachment"
                val bytes = resolver.openInputStream(uri)?.use { input ->
                    val output = java.io.ByteArrayOutputStream()
                    val buffer = ByteArray(65536)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        require(output.size() + count <= 20 * 1024 * 1024) { "附件不能超过 20 MB" }
                        output.write(buffer, 0, count)
                    }
                    output.toByteArray()
                } ?: throw IllegalStateException("无法读取所选文件")
                require(bytes.isNotEmpty()) { "附件为空" }
                var offset = 0
                while (offset < bytes.size) {
                    check(selectedSession == id && sessions.any { it.id == id }) { "目标会话已切换，取消插入附件" }
                    val end = minOf(offset + 131072, bytes.size)
                    val result = remoteRequest(session.remoteTarget, JSONObject().put("op", "attachment").put("session_id", session.remoteId)
                        .put("upload_id", uploadId).put("name", name).put("size", bytes.size).put("offset", offset)
                        .put("data", Base64.encodeToString(bytes.copyOfRange(offset, end), Base64.NO_WRAP)))
                    check(result.getInt("received") == end) { "电脑接收的附件长度不一致" }
                    offset = end
                }
                check(selectedSession == id && sessions.any { it.id == id }) { "目标会话已切换，取消插入附件" }
                remoteRequest(session.remoteTarget, JSONObject().put("op", "attachment-paste").put("session_id", session.remoteId).put("upload_id", uploadId))
                pasted = true
                main.post { notice = "附件已传到电脑并插入当前会话，未自动发送消息" }
            } catch (cause: Exception) { main.post { error = safeError(cause) } }
            finally {
                main.post { busy = false }
                if (!pasted) runCatching { remoteRequest(session.remoteTarget, JSONObject().put("op", "attachment-cancel").put("session_id", session.remoteId).put("upload_id", uploadId)) }
            }
        }
    }
    private val store = ProfileStore(application)
    internal var appearanceMode by mutableStateOf(store.appearance()); private set
    internal fun setAppearance(value: AppearanceMode) {
        runCatching { store.saveAppearance(value) }.onSuccess { appearanceMode = value }.onFailure { error = "无法保存外观设置" }
    }
    val termux = TermuxConnection(application, store)
    private val worker = Executors.newFixedThreadPool(3)
    private val inputWorker = Executors.newSingleThreadExecutor()
    private val timer = Executors.newSingleThreadScheduledExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val computerDiscovery = ComputerDiscovery(application)
    private val connectivity = application.getSystemService(ConnectivityManager::class.java)
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onLinkPropertiesChanged(network: Network, properties: LinkProperties) { main.post {
            val addresses = properties.linkAddresses.mapNotNull { it.address.hostAddress }
                .filter(ComputerDiscoveryPolicy::isLanAddress).sorted()
            if (addresses.isEmpty()) return@post
            val signature = "${network.networkHandle}:${addresses.joinToString(",")}"
            if (networkSignature.isNotEmpty() && networkSignature != signature) {
                remoteConnectionEpoch++
                computerDiscovery.cancel()
                forceRemoteReconnect = true
                remoteRetryAt = 0L
            }
            networkSignature = signature
        } }
        override fun onLost(network: Network) { main.post {
            if (networkSignature.startsWith("${network.networkHandle}:")) {
                remoteConnectionEpoch++; computerDiscovery.cancel(); forceRemoteReconnect = true
            }
        } }
    }
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
        require(codex in listOf("restricted", "standard", "full", "dangerous") && claude in listOf("standard", "full", "dangerous"))
        updateOptions(options.copy(codexPermission = codex, claudePermission = claude, permissionsReviewed = true))
        showAgentPermissions = false
        notice = "已保存权限设置；之后新建、恢复和分支的本地会话使用此设置"
    }
    var history by mutableStateOf(emptyList<LocalConversation>()); private set
    var historyProjects by mutableStateOf(emptyList<HistoryProject>()); private set
    private var reasoningPreferences by mutableStateOf(store.loadReasoningPreferences())
    private fun reasoningFor(client: String, profile: String, model: String) = reasoningPreferences[ReasoningEffort.key(client, profile, model)] ?: "default"
    val startupReasoningEffort get() = reasoningFor(options.client, options.profileId, options.model)
    val remoteStartupReasoningEffort get() = reasoningFor(remoteClient, "ssh:${sshTarget?.id}:$remoteProfile", remoteModel)
    fun setStartupReasoningEffort(value: String) = saveReasoning(options.client, options.profileId, options.model, value)
    fun setRemoteStartupReasoningEffort(value: String) = saveReasoning(remoteClient, "ssh:${sshTarget?.id}:$remoteProfile", remoteModel, value)
    private fun saveReasoning(client: String, profile: String, model: String, value: String) {
        if(value !in ReasoningEffort.levels) return
        val next = reasoningPreferences + (ReasoningEffort.key(client, profile, model) to value)
        runCatching { store.saveReasoningPreferences(next) }.onSuccess { reasoningPreferences = next }.onFailure { error = "无法保存思考强度设置" }
    }
    fun insertReasoningCommand(id: String, value: String): Boolean {
        val session = sessions.firstOrNull { it.id == id } ?: return false
        val command = ReasoningEffort.command(session.client, value)
        if(selectedSession != id || !session.canWrite || session.exitCode != null || command == null) {
            error = "目标终端已切换、结束或没有输入控制权"; return false
        }
        send(id, "\u001b[200~$command\u001b[201~")
        notice = "已填入 $command；确认输入框内容后按回车，生效情况由 CLI 确认"
        return true
    }
    var historyLoading by mutableStateOf(false); private set
    var historyError by mutableStateOf(""); private set
    var historyArchiving by mutableStateOf(""); private set
    var historyMutating by mutableStateOf(""); private set
    val canArchiveHistory get() = localExecution || remoteWorkbench?.capabilities?.contains("history-archive") == true
    val canManageHistoryProjects get() = localExecution || remoteWorkbench?.capabilities?.contains("history-projects") == true
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
    var usageError by mutableStateOf(""); private set
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
            runCatching { rows.getJSONObject(index).let { TerminalSession(it.getString("id"), it.getString("title"), it.getString("workspace"), it.getString("client"), bridgePort = it.optInt("bridge_port", termux.currentPort), restoredOutputPending = true,
                remoteTarget = it.optString("remote_target"), remoteId = it.optString("remote_id"), sshTargetId = it.optString("ssh_target_id"),
                input = TerminalInputState(if (it.optString("remote_target").isEmpty()) RemoteControlState.Controlled else RemoteControlState.Checking)) } }.getOrNull()
        } }
        selectedSession = sessions.firstOrNull()?.id.orEmpty()
        if (!localExecution && sessions.any { it.client == "ssh-login" }) remoteWantedId = options.executionId
        runCatching { connectivity?.registerDefaultNetworkCallback(networkCallback) }
    }

    fun setForeground(value: Boolean) {
        if (foreground == value) return
        foreground = value
        if (!value) sessions = sessions.map {
            if (it.remoteTarget.isEmpty()) it else it.copy(input = TerminalInputState(RemoteControlState.Checking, epoch = it.input.epoch + 1))
        }
        if (!value) { computerDiscovery.cancel(); remoteConnectionEpoch++ }
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

    private fun readHistory(directory: String?, client: String? = null, includeArchived: Boolean = false): List<LocalConversation> {
        val query = if (directory == null) "scope=all" else
            "workspace=" + java.net.URLEncoder.encode(directory, Charsets.UTF_8.name())
        val rows = termux.request("/history?$query" + (client?.let { "&client=$it" } ?: "") + (if(includeArchived) "&archive=all" else ""), timeout = 15000)
            .getJSONArray("sessions")
        return (0 until rows.length()).map { index ->
            val row = rows.getJSONObject(index)
            LocalConversation(row.getString("id"), row.getString("history_ref"), row.getString("client"),
                row.getString("title"), row.getString("workspace"), row.getString("model"),
                row.getString("profile_id"), row.getLong("updated_at"), row.optString("preview"),
                row.optString("configured_model", row.getString("model")), row.optString("gateway"), row.optBoolean("archived"),
                row.optString("project_id").takeIf { it.isNotBlank() }, row.optString("project_path"), row.optBoolean("custom_title"))
        }
    }

    fun refreshHistory(allProjects: Boolean = false) {
        if (historyLoading) return
        historyLoading = true
        history = emptyList()
        historyError = ""
        val remote = sshTarget?.id
        val directory = if (allProjects) null else historyWorkspace
        worker.execute {
            val result = runCatching { if (remote == null) readHistory(directory, includeArchived = true) else {
                val value = remoteRequest(remote, JSONObject().put("op", "history").put("workspace", directory ?: "").put("include_archived", true))
                val rows = value.getJSONArray("sessions")
                (0 until rows.length()).map { index -> val row = rows.getJSONObject(index)
                    LocalConversation(row.getString("id"), row.getString("history_ref"), row.getString("client"), row.getString("title"),
                        row.getString("workspace"), row.getString("model"), row.getString("profile_id"), row.getLong("updated_at"),
                        row.optString("preview"), row.optString("configured_model", row.getString("model")), row.optString("gateway"), row.optBoolean("archived"),
                        row.optString("project_id").takeIf { it.isNotBlank() }, row.optString("project_path"), row.optBoolean("custom_title"))
                }
            } }
            main.post {
                result.onSuccess { rows -> if (sshTarget?.id == remote && (directory == null || historyWorkspace == directory)) history = rows }
                    .onFailure { historyError = if (remote == null) "无法读取本地历史，请在运行环境页重新连接 Termux" else it.message ?: "无法读取电脑历史" }
                historyLoading = false
            }
        }
    }

    private fun parseHistoryProjects(value: JSONObject): List<HistoryProject> {
        val rows = value.optJSONArray("projects") ?: JSONArray()
        return (0 until rows.length()).mapNotNull { index ->
            rows.optJSONObject(index)?.let { row ->
                val id = row.optString("id")
                val name = row.optString("name")
                if (id.isBlank() || name.isBlank()) null else HistoryProject(id, name, row.optString("parent_id").takeIf { it.isNotBlank() })
            }
        }
    }

    fun refreshHistoryProjects() {
        if (!canManageHistoryProjects) return
        val remote = sshTarget?.id
        worker.execute {
            val result = runCatching {
                if (remote == null) parseHistoryProjects(termux.request("/history/projects"))
                else parseHistoryProjects(remoteRequest(remote, JSONObject().put("op", "history-projects")))
            }
            main.post {
                if (sshTarget?.id == remote) result.onSuccess { historyProjects = it }
                    .onFailure { historyError = it.message ?: "无法读取历史项目" }
            }
        }
    }

    private fun projectPath(projectId: String?): String {
        if (projectId == null) return ""
        val projects = historyProjects.associateBy { it.id }
        val names = mutableListOf<String>()
        val seen = mutableSetOf<String>()
        var current = projectId
        while (current != null && seen.add(current)) {
            val project = projects[current] ?: break
            names += project.name
            current = project.parentId
        }
        return names.asReversed().joinToString(" / ")
    }

    fun renameHistory(entry: LocalConversation, title: String) {
        val clean = title.trim()
        if (clean.isBlank()) { historyError = "会话名称不能为空"; return }
        if (historyMutating.isNotEmpty()) return
        historyMutating = entry.reference
        worker.execute {
            val result = runCatching {
                val payload = JSONObject().put("history_ref", entry.reference).put("title", clean)
                if (sshTarget?.id == null) termux.request("/history/rename", "POST", payload)
                else remoteRequest(sshTarget!!.id, payload.put("op", "history-rename"))
            }
            main.post {
                result.onSuccess {
                    history = history.map { if (it.reference == entry.reference) it.copy(title = clean, customTitle = true) else it }
                    notice = "已重命名会话"
                }.onFailure { historyError = it.message ?: "会话重命名失败" }
                historyMutating = ""
            }
        }
    }

    fun assignHistoryProject(entry: LocalConversation, projectId: String?) {
        if (!canManageHistoryProjects || historyMutating.isNotEmpty()) return
        historyMutating = entry.reference
        worker.execute {
            val result = runCatching {
                val payload = JSONObject().put("history_ref", entry.reference)
                    .put("project_id", projectId ?: JSONObject.NULL)
                if (sshTarget?.id == null) termux.request("/history/project", "POST", payload.put("action", "assign"))
                else remoteRequest(sshTarget!!.id, payload.put("op", "history-project-assign"))
            }
            main.post {
                result.onSuccess {
                    history = history.map { if (it.reference == entry.reference) it.copy(projectId = projectId, projectPath = projectPath(projectId)) else it }
                    notice = if (projectId == null) "已移出项目" else "已归类到项目"
                }.onFailure { historyError = it.message ?: "会话归类失败" }
                historyMutating = ""
            }
        }
    }

    fun createHistoryProject(name: String, parentId: String? = null) {
        val clean = name.trim()
        if (clean.isBlank()) { historyError = "项目名称不能为空"; return }
        if (!canManageHistoryProjects || historyMutating.isNotEmpty()) return
        historyMutating = "project-create"
        worker.execute {
            val result = runCatching {
                val payload = JSONObject().put("name", clean).put("parent_id", parentId ?: JSONObject.NULL)
                val value = if (sshTarget?.id == null) termux.request("/history/project", "POST", payload.put("action", "create"))
                    else remoteRequest(sshTarget!!.id, payload.put("op", "history-project-create"))
                val project = value.optJSONObject("project") ?: value
                HistoryProject(project.getString("id"), project.getString("name"), project.optString("parent_id").takeIf { it.isNotBlank() })
            }
            main.post {
                result.onSuccess { historyProjects = historyProjects + it; notice = "已创建项目" }
                    .onFailure { historyError = it.message ?: "创建项目失败" }
                historyMutating = ""
            }
        }
    }

    fun renameHistoryProject(project: HistoryProject, name: String) {
        val clean = name.trim()
        if (clean.isBlank()) { historyError = "项目名称不能为空"; return }
        if (!canManageHistoryProjects || historyMutating.isNotEmpty()) return
        historyMutating = project.id
        worker.execute {
            val result = runCatching {
                val payload = JSONObject().put("project_id", project.id).put("name", clean)
                val value = if (sshTarget?.id == null) termux.request("/history/project", "POST", payload.put("action", "rename"))
                    else remoteRequest(sshTarget!!.id, payload.put("op", "history-project-rename"))
                val row = value.optJSONObject("project") ?: value
                HistoryProject(row.getString("id"), row.getString("name"), row.optString("parent_id").takeIf { it.isNotBlank() })
            }
            main.post {
                result.onSuccess { updated ->
                    historyProjects = historyProjects.map { if (it.id == updated.id) updated else it }
                    history = history.map { if (it.projectId == updated.id) it.copy(projectPath = projectPath(updated.id)) else it }
                    notice = "已重命名项目"
                }.onFailure { historyError = it.message ?: "项目重命名失败" }
                historyMutating = ""
            }
        }
    }

    fun deleteHistoryProject(project: HistoryProject) {
        if (!canManageHistoryProjects || historyMutating.isNotEmpty()) return
        historyMutating = project.id
        worker.execute {
            val result = runCatching {
                val payload = JSONObject().put("project_id", project.id)
                if (sshTarget?.id == null) termux.request("/history/project", "POST", payload.put("action", "delete"))
                else remoteRequest(sshTarget!!.id, payload.put("op", "history-project-delete"))
            }
            main.post {
                result.onSuccess {
                    historyProjects = historyProjects.filterNot { it.id == project.id }
                    history = history.map { if (it.projectId == project.id) it.copy(projectId = null, projectPath = "") else it }
                    notice = "已删除项目"
                }.onFailure { historyError = it.message ?: "删除项目失败" }
                historyMutating = ""
            }
        }
    }

    fun archiveHistory(entry: LocalConversation) {
        if(historyArchiving.isNotEmpty() || historyLoading || !canArchiveHistory) return
        historyArchiving = entry.reference
        historyError = ""
        val remote = sshTarget?.id
        val archived = !entry.archived
        worker.execute {
            val result = runCatching {
                val payload = JSONObject().put("history_ref", entry.reference).put("history_id", entry.id)
                    .put("client", entry.client).put("archived", archived)
                if(remote == null) termux.request("/history/archive", "POST", payload)
                else remoteRequest(remote, payload.put("op", "history-archive"))
            }
            main.post {
                if(sshTarget?.id == remote) result.onSuccess {
                    history = history.map { if(it.reference == entry.reference) it.copy(archived = archived) else it }
                    notice = if(archived) "已归档；可在“已归档”中查找和取消归档" else "已取消归档"
                }.onFailure { historyError = it.message ?: "归档失败，请确认已更新运行环境与电脑工作台" }
                historyArchiving = ""
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
        if (localExecution) launch(original = entry) else launchRemote("original", entry)
    }

    fun chooseHistory(entry: LocalConversation) {
        selectedHistory = entry
        if (!localExecution) { remoteClient = entry.client; return }
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
        val existing = sshTargets.firstOrNull { SshTargetPolicy.sameComputer(it, info) }
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

    fun saveProfile(name: String, address: String, key: String, validate: Boolean, allowInsecureLan: Boolean, modelCatalogUrl: String = "", done: () -> Unit) {
        if (busy) return
        busy = true; error = ""
        worker.execute {
            try {
                require(name.isNotBlank() && name.length <= 80 && key.isNotBlank() && key.length <= 4096 && key.trim().none { it.isWhitespace() || it.isISOControl() }) { "请填写名称和完整调用 Key" }
                val gateway = GatewayPolicy.normalize(address, allowInsecureLan)
                val catalogUrl = GatewayPolicy.normalizeCatalogUrl(modelCatalogUrl, allowInsecureLan)
                var catalogNotice = if (validate) "" else "已跳过模型列表读取；Key 和模型尚未验证，请手动填写模型 ID"
                val models = if (validate) try { GatewayClient.models(gateway, key.trim(), catalogUrl, allowInsecureLan) }
                    catch (cause: ModelCatalogUnavailable) { catalogNotice = cause.message.orEmpty(); emptyList() } else emptyList()
                val created = KeyProfile(name = name.trim(), gateway = gateway, models = models, allowInsecureLan = allowInsecureLan, modelCatalogUrl = catalogUrl)
                store.saveSecret(created.id, key.trim())
                main.post {
                    try {
                        val next = profiles + created
                        store.saveProfiles(next); profiles = next
                        updateOptions(options.copy(profileId = created.id, model = models.firstOrNull().orEmpty()))
                        notice = "配置已保存；" + (catalogNotice.ifBlank { if (models.isEmpty()) "模型列表为空，请手动填写模型 ID" else "读取到 ${models.size} 个模型" })
                        done()
                    } catch (_: Exception) { store.removeSecret(created.id); error = "配置写入失败，未添加调用配置" }
                    busy = false
                }
            } catch (cause: Exception) { main.post { busy = false; error = safeError(cause) } }
        }
    }

    fun updateProfileConnection(id: String, address: String, modelCatalogUrl: String, validate: Boolean, allowInsecureLan: Boolean, done: () -> Unit) {
        if(busy) return
        val previous = profiles.find { it.id == id } ?: return
        busy = true; error = ""
        worker.execute {
            try {
                val gateway = GatewayPolicy.normalize(address, allowInsecureLan)
                val catalogUrl = GatewayPolicy.normalizeCatalogUrl(modelCatalogUrl, allowInsecureLan)
                var catalogNotice = if(validate) "" else "已跳过模型列表读取；连接尚未验证"
                val models = if(validate) try { GatewayClient.models(gateway, store.secret(id), catalogUrl, allowInsecureLan) }
                    catch(cause: ModelCatalogUnavailable) { catalogNotice = cause.message.orEmpty(); emptyList() } else emptyList()
                main.post {
                    try {
                        val current = profiles.find { it.id == id } ?: throw IllegalStateException("调用配置已删除")
                        check(current.gateway == previous.gateway && current.modelCatalogUrl == previous.modelCatalogUrl) { "连接设置已更改，请重新打开后保存" }
                        val updated = current.copy(gateway = gateway, modelCatalogUrl = catalogUrl, models = models, allowInsecureLan = allowInsecureLan)
                        val next = profiles.map { if(it.id == id) updated else it }
                        store.saveProfiles(next); profiles = next
                        if(options.profileId == id) account = ""
                        notice = "连接设置已保存；已运行终端保留原配置。" + catalogNotice
                        done()
                    } catch(cause: Exception) { error = safeError(cause) }
                    busy = false
                }
            } catch(cause: Exception) { main.post { busy = false; error = safeError(cause) } }
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
                val models = GatewayClient.models(selected.gateway, key, selected.modelCatalogUrl, selected.allowInsecureLan)
                val balance = runCatching {
                    val response = GatewayClient.json(selected.gateway + "/v1/cchp/account", key)
                    val value = response.optJSONObject("balance")?.opt("spendable")
                    if (value == null || value == JSONObject.NULL) "账户接口未返回可消费额度" else "网关可消费额度：$value ${response.optString("currency", "USD") }"
                }.getOrDefault("此网关未提供可读取的账户信息")
                main.post {
                    val current = profiles.find { it.id == selected.id }
                    if(current == null || current.gateway != selected.gateway || current.modelCatalogUrl != selected.modelCatalogUrl) { loadingModels = false; return@post }
                    profiles = profiles.map { if (it.id == selected.id) it.copy(models = models) else it }; store.saveProfiles(profiles)
                    if (options.profileId == selected.id) { account = balance; if (options.model.isBlank() && models.isNotEmpty()) updateOptions(options.copy(model = models[0])) }
                    notice = if (models.isEmpty()) "网关未返回模型；已保留手动填写的模型" else "已刷新 ${models.size} 个模型"
                    loadingModels = false
                }
            } catch (cause: ModelCatalogUnavailable) { main.post {
                loadingModels = false; notice = cause.message.orEmpty() + "；保留已缓存列表和模型选择"
            } } catch (cause: Exception) { main.post { loadingModels = false; error = safeError(cause) + "；保留已缓存列表" } }
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
                        sessions = parseUsageConversations(data).map { s -> s.copy(profileName = profiles.find { it.id == s.profileId }?.name ?: s.profileName, days = s.days.map { r -> r.copy(profileName = profiles.find { it.id == (r.profileId ?: s.profileId) }?.name ?: r.profileName) }) },
                        scopeNote = data.optString("scope_note"),
                    )
                    usageError = ""
                }
                result.onFailure { usageError = "用量读取失败，保留上次结果：" + safeError(it as? Exception ?: Exception("读取异常")) }
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
        remoteWantedId = ""; remoteAutoRetry = false; remoteConnectionEpoch++; computerDiscovery.cancel()
        remoteWorkbench = null; remoteMessage = ""; remoteModels = emptyList(); selectedHistory = null
        remoteConnecting = false; remoteLoginId = ""
        when (value) {
            "local-debian" -> { updateOptions(options.copy(executionId = "local")); selectRuntimeEnvironment("debian") }
            "local-shell" -> { updateOptions(options.copy(executionId = "local")); selectRuntimeEnvironment("debian") }
            "local-vm" -> if (vmSupported) {
                updateOptions(options.copy(executionId = "local")); selectRuntimeEnvironment("vm")
            } else error = "此设备的 Termux 架构暂不支持完整 Linux VM；可使用 Debian Shell 或 SSH 电脑"
            else -> updateOptions(options.copy(executionId = value))
        }
        if (!localExecution) refreshRemote()
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
                    .put("reasoning_effort", reasoningFor(client, selected.id, snapshot.model))
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
        val session = sessions.firstOrNull { it.id == id } ?: return
        // Read-only is an expected state, not an application-wide error.
        if (!session.canWrite) return
        inputWorker.execute {
            try {
                termux.request("/sessions/$id/input", "POST", JSONObject().put("data", Base64.encodeToString(value, Base64.NO_WRAP)), bridgePort = session.bridgePort)
                main.post { sessions = sessions.map { if (it.id == id && it.input.epoch == session.input.epoch) it.copy(input = it.input.copy(error = "")) else it } }
            } catch (cause: Exception) {
                main.post {
                    sessions = sessions.map { if (it.id == id) it.copy(input = it.input.failed(safeError(cause), session.input.epoch)) else it }
                }
            }
        }
    }
    fun resize(id: String, cols: Int, rows: Int) {
        val port = sessions.firstOrNull { it.id == id }?.bridgePort ?: termux.currentPort
        worker.execute { runCatching { termux.request("/sessions/$id/resize", "POST", JSONObject().put("cols", cols).put("rows", rows), bridgePort = port) } }
    }
    fun moveSession(id: String, targetIndex: Int) {
        val from = sessions.indexOfFirst { it.id == id }
        if (from < 0 || sessions.isEmpty()) return
        val destination = targetIndex.coerceIn(0, sessions.lastIndex)
        if (from == destination) return
        val next = sessions.toMutableList()
        val item = next.removeAt(from)
        next.add(destination, item)
        sessions = next
        saveSessions()
    }
    fun moveSession(id: String, targetId: String, afterTarget: Boolean) {
        val reordered = reorderSessionIds(sessions.map { it.id }, id, targetId, afterTarget)
        if (reordered == sessions.map { it.id }) return
        sessions = reordered.mapNotNull { target -> sessions.firstOrNull { it.id == target } }
        saveSessions()
    }
    fun close(id: String) {
        val closingSession = sessions.firstOrNull { it.id == id }
        worker.execute {
            val failure = runCatching { termux.request("/sessions/$id", "DELETE", bridgePort = sessions.firstOrNull { it.id == id }?.bridgePort ?: termux.currentPort) }.exceptionOrNull()
            main.post {
                sessions = sessions.filterNot { it.id == id }
                if (selectedSession == id) selectedSession = sessions.lastOrNull()?.id.orEmpty()
                if (closingSession?.client == "ssh-login" && remoteLoginId == id) {
                    remoteWantedId = ""; remoteAutoRetry = false; remoteConnectionEpoch++; computerDiscovery.cancel()
                    remoteLoginId = ""; remoteConnecting = false; remoteWorkbench = null; remoteTarget = ""
                    remoteMessage = "已断开电脑登录；电脑上的任务继续运行"
                }
                saveSessions()
                if (failure != null) notice = "已移除终端标签；后台进程未确认停止，请检查 Termux"
            }
        }
    }

    private fun saveSessions() {
        store.saveSessions(JSONArray(sessions.map { JSONObject().put("id", it.id).put("title", it.title).put("workspace", it.workspace).put("client", it.client).put("bridge_port", it.bridgePort)
            .put("remote_target", it.remoteTarget).put("remote_id", it.remoteId).put("ssh_target_id", it.sshTargetId) }))
    }
    private fun poll() {
        if (!foreground || polling || busy || !termux.configured) return
        if (!localExecution && connected && remoteWantedId == options.executionId &&
            (forceRemoteReconnect || (remoteAutoRetry && remoteWorkbench == null && !remoteConnecting)) &&
            SystemClock.elapsedRealtime() >= remoteRetryAt) { connectRemote(); return }
        if (!localExecution && connected && SystemClock.elapsedRealtime() >= remotePollAt) {
            remotePollAt = SystemClock.elapsedRealtime() + 2500; refreshRemote()
        }
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
                                        val next = if (encoded.isBlank()) current.output else (current.output + OutputChunk(data.getLong("after"), encoded, current.restoredOutputPending || current.remoteTarget.isNotEmpty())).takeLast(80)
                                        current.copy(after = data.getLong("after"), output = next, restoredOutputPending = false,
                                            error = if (current.client == "ssh-login") data.optString("error") else current.error,
                                            input = current.input.snapshot(data.optBoolean("can_write", current.remoteTarget.isEmpty()), data.optString("control_state"), session.input.epoch),
                                            exitCode = if (data.isNull("exit_code")) null else data.getInt("exit_code"))
                                    }
                                }
                                if (!data.isNull("exit_code")) {
                                    if (session.client == "ssh-login") {
                                        // Retained old login tabs must not mark a new connection
                                        // failed or overwrite feedback in another terminal.
                                        if (session.id == remoteLoginId) remoteConnecting = false
                                    } else {
                                        sessions = sessions.filterNot { it.id == session.id }
                                        if (selectedSession == session.id) selectedSession = sessions.lastOrNull()?.id.orEmpty()
                                    }
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
                                    sessions = sessions.map { if (it.id == session.id && it.remoteTarget.isNotEmpty() && it.input.epoch == session.input.epoch) it.copy(input = it.input.copy(control = RemoteControlState.Unavailable, error = "")) else it }
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
    override fun onCleared() {
        computerDiscovery.cancel()
        runCatching { connectivity?.unregisterNetworkCallback(networkCallback) }
        timer.shutdownNow(); worker.shutdownNow(); inputWorker.shutdownNow()
    }
    private fun safeError(cause: Exception): String = when (cause) {
        is java.net.SocketTimeoutException -> "连接超时，请检查网关或本地桥接"
        is java.net.ConnectException -> "无法连接服务，请确认地址与服务状态"
        is java.net.UnknownHostException -> "无法解析服务地址"
        is javax.net.ssl.SSLException -> "HTTPS 证书或连接校验失败"
        is IllegalArgumentException, is IllegalStateException, is SecurityException -> cause.message?.take(180) ?: "操作失败"
        else -> "操作未完成，请检查地址、权限与环境"
    }
}
