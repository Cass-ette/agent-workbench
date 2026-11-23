package com.cassette.workbench

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import org.json.JSONObject
import java.net.ServerSocket
import java.security.SecureRandom
import java.security.MessageDigest
import java.util.UUID

const val TERMUX_PERMISSION = "com.termux.permission.RUN_COMMAND"
const val TERMUX_RELEASE = "https://github.com/termux/termux-app/releases/tag/v0.118.3"
private const val TERMUX_APK_PREFIX = "https://github.com/termux/termux-app/releases/download/v0.118.3/termux-app_v0.118.3%2Bgithub-debug_"
private val TERMUX_APK_ARCHES = mapOf(
    "arm64-v8a" to "arm64-v8a",
    "armeabi-v7a" to "armeabi-v7a",
    "x86_64" to "x86_64",
    "x86" to "x86",
)

fun termuxApkUrl(supportedAbis: Array<String>): String? {
    val abi = supportedAbis.firstOrNull { it in TERMUX_APK_ARCHES } ?: return null
    return TERMUX_APK_PREFIX + TERMUX_APK_ARCHES.getValue(abi) + ".apk"
}

class TermuxConnection(private val context: Context, private val store: ProfileStore) {
    private var port = store.bridgePort()
    private var token = if (port == 0) "" else runCatching { store.secret("bridge") }.getOrDefault("")
    val connectedAddress get() = "http://127.0.0.1:$port"
    val configured get() = port > 0 && token.isNotBlank()
    val currentPort get() = port

    @Suppress("DEPRECATION")
    fun version(): String? = try { context.packageManager.getPackageInfo("com.termux", 0).versionName } catch (_: Exception) { null }
    fun permitted() = context.checkSelfPermission(TERMUX_PERMISSION) == PackageManager.PERMISSION_GRANTED

    fun ping(): Boolean = port > 0 && token.isNotEmpty() && try {
        request("/health", timeout = 1500).optString("service") == "agent-workbench-termux"
    } catch (_: Exception) { false }

    fun supportsRuntime() = try {
        val health = request("/health")
        health.optString("version") == BuildConfig.VERSION_NAME && health.optInt("build") == BuildConfig.VERSION_CODE &&
            health.optJSONArray("capabilities").strings().containsAll(listOf("debian-runtime", "debian-shell", "linux-vm", "vm-agent", "versioned-runtime", "history", "history-actions", "ssh-pairing", "tool-bridge", "remote-workbench", "remote-connection-state"))
    } catch (_: Exception) { false }

    fun startHost(upgrade: Boolean = false) {
        check(version() != null) { "请先安装官方 Termux v0.118.3" }
        check(permitted()) { "请先授权工作台执行 Termux 命令" }
        if (upgrade && port > 0 && token.isNotBlank()) {
            store.saveSecret("bridge:$port", token)
            port = 0
        }
        if (port == 0 || token.isBlank()) {
            port = ServerSocket(0).use { it.localPort }
            token = ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
            store.saveSecret("bridge", token)
            store.saveSecret("bridge:$port", token)
            store.saveBridgePort(port)
        }
        val files = listOf("termux_bridge.py", "remote_workbench.py", "debian_runner.py", "runtime_setup.sh", "workbench_runtime.py", "linux_vm.py", "session_history.py", "tool_bridge.py")
        val assets = files.associateWith { name -> context.assets.open(name).use { it.readBytes() } }
        val digest = MessageDigest.getInstance("SHA-256")
        assets.forEach { (name, bytes) -> digest.update(name.toByteArray(Charsets.UTF_8)); digest.update(bytes) }
        val revision = digest.digest().joinToString("") { "%02x".format(it) }.take(16)
        // Keep old bundles for their still-running terminals. Projects, VM
        // disks, install status and API profiles remain outside this directory.
        val root = "/data/data/com.termux/files/home/.local/share/agent-workbench/runtime/releases/${BuildConfig.VERSION_CODE}-$revision"
        val operation = UUID.randomUUID().toString()
        val script = compileBootstrapScript(root, operation, port, token, BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE)
        val arguments = bootstrapArguments(script, assets)
        store.saveBootstrapOperation(operation)
        val callback = PendingIntent.getBroadcast(context, 0,
            Intent(context, TermuxBootstrapResult::class.java).setAction("com.cassette.workbench.BOOTSTRAP.$operation"),
            PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_CANCEL_CURRENT or PendingIntent.FLAG_MUTABLE)
        val intent = Intent("com.termux.RUN_COMMAND").apply {
            setClassName("com.termux", "com.termux.app.RunCommandService")
            putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash")
            putExtra("com.termux.RUN_COMMAND_ARGUMENTS", arguments)
            putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home")
            putExtra("com.termux.RUN_COMMAND_BACKGROUND", true)
            putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", callback)
            putExtra("com.termux.RUN_COMMAND_COMMAND_LABEL", "Agent 工作台终端桥接")
            putExtra("com.termux.RUN_COMMAND_COMMAND_DESCRIPTION", "仅本机回环端口，承载工作台内嵌终端")
        }
        context.startService(intent)
    }

    fun bootstrapError() = store.bootstrapError()

    fun request(path: String, method: String = "GET", body: JSONObject? = null, timeout: Int = 1800, bridgePort: Int = port): JSONObject {
        check(port > 0 && token.isNotBlank()) { "请先连接 Termux" }
        val credential = if (bridgePort == port) token else store.secret("bridge:$bridgePort")
        return GatewayClient.json("http://127.0.0.1:$bridgePort$path", credential, method, body, timeout, bridge = true)
    }
}
