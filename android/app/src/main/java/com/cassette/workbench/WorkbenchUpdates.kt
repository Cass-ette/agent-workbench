package com.cassette.workbench

import android.app.Application
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.lifecycle.AndroidViewModel
import org.json.JSONObject
import org.json.JSONArray
import java.io.File
import java.io.InputStream
import java.net.URI
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.HttpsURLConnection
import kotlin.math.min

private const val RELEASE_PAGE = "https://github.com/Cass-ette/claude-cchp-manager/releases"
private const val RELEASE_FEED = "https://api.github.com/repos/Cass-ette/claude-cchp-manager/releases?per_page=20"
private const val APK_MIME = "application/vnd.android.package-archive"
private val CHECK_INTERVAL = 86_400_000L
private val RETRY_INTERVAL = 1_800_000L

internal data class AndroidUpdate(
    val version: String,
    val build: Long,
    val url: String,
    val size: Long,
    val sha256: String,
    val notes: String,
    val channel: String = "stable",
) {
    fun samePackage(other: AndroidUpdate?) = other != null && copy(notes = "") == other.copy(notes = "")

    fun cache() = JSONObject()
        .put("version", version).put("build", build).put("url", url).put("size", size)
        .put("sha256", sha256).put("notes", notes).put("channel", channel).toString()

    companion object {
        fun decode(data: JSONObject, notes: String) = AndroidUpdate(
            data.getString("version"), data.getLong("build"), data.getString("url"),
            data.getLong("size"), data.getString("sha256"), notes, data.optString("channel", "stable"),
        ).also {
            val uri = URI(it.url)
            require(it.channel in listOf("stable", "preview") && it.version.matches(Regex("[0-9]+\\.[0-9]+\\.[0-9]+")) && it.build > 0 &&
                it.size in 1..1_073_741_824 && it.sha256.matches(Regex("[0-9a-f]{64}")) &&
                uri.scheme == "https" && uri.host == "github.com" && uri.rawUserInfo == null &&
                uri.port == -1 && uri.rawQuery == null && uri.rawFragment == null &&
                uri.path.startsWith("/Cass-ette/claude-cchp-manager/releases/download/") && uri.path.endsWith(".apk")
            ) { "安装包信息无效。" }
        }
    }
}

internal data class AndroidUpdateCandidate(
    val index: Int,
    val platform: String,
    val channel: String,
    val arch: String,
    val version: String,
    val signing: String,
)

internal fun selectAndroidUpdatePackage(
    packages: List<AndroidUpdateCandidate>,
    architectures: List<String>,
    channel: String,
    signing: String,
): AndroidUpdateCandidate? {
    val candidates = packages.filter {
        it.platform == "android" && it.channel == channel && it.signing == signing
    }
    return (architectures + "universal").firstNotNullOfOrNull { architecture ->
        val matches = candidates.filter { it.arch == architecture }
        require(matches.size <= 1) { "发布清单有重复的安装包。" }
        matches.firstOrNull()
    }
}

internal data class WorkbenchReleaseAsset(val name: String, val url: String)
internal data class WorkbenchRelease(
    val draft: Boolean, val prerelease: Boolean, val publishedAt: String?, val assets: List<WorkbenchReleaseAsset>,
)
internal data class ReleaseManifestSource(val url: String, val channel: String)

internal fun selectReleaseManifest(releases: List<WorkbenchRelease>): ReleaseManifestSource? {
    for (release in releases.filter { !it.draft && it.publishedAt != null }.sortedByDescending { it.publishedAt }) {
        val asset = release.assets.filter { it.name == "update.json" }.singleOrNull() ?: continue
        val uri = runCatching { URI(asset.url) }.getOrNull() ?: continue
        if (uri.scheme == "https" && uri.host == "github.com" && uri.rawUserInfo == null && uri.port == -1 &&
            uri.rawQuery == null && uri.rawFragment == null &&
            uri.path.startsWith("/Cass-ette/claude-cchp-manager/releases/download/") && uri.path.endsWith("/update.json")) {
            return ReleaseManifestSource(asset.url, if (release.prerelease) "preview" else "stable")
        }
    }
    return null
}

private object AndroidUpdateService {
    fun lookup(): AndroidUpdate? {
        val releases = JSONArray(readDocument(RELEASE_FEED))
        val source = selectReleaseManifest((0 until releases.length()).map { index ->
            val release = releases.getJSONObject(index)
            val assets = release.getJSONArray("assets")
            WorkbenchRelease(release.getBoolean("draft"), release.getBoolean("prerelease"),
                if (release.isNull("published_at")) null else release.getString("published_at"),
                (0 until assets.length()).map { i ->
                    val asset = assets.getJSONObject(i)
                    WorkbenchReleaseAsset(asset.getString("name"), asset.getString("browser_download_url"))
                })
        }) ?: error("发布页暂时没有完整更新包，可以稍后重试或手动下载。")
        val document = JSONObject(readDocument(source.url))
        check(document.getInt("schema") == 1) { "无法识别发布清单版本。" }
        val packages = document.getJSONArray("packages")
        val signing = if (BuildConfig.DEBUG) "development" else "release"
        val packageInfo = selectAndroidUpdatePackage(
            (0 until packages.length()).map { index ->
                val item = packages.getJSONObject(index)
                AndroidUpdateCandidate(index, item.optString("platform"), item.optString("channel"),
                    item.optString("arch"), item.optString("version"), item.optString("signing"))
            },
            Build.SUPPORTED_ABIS.toList(),
            source.channel,
            signing,
        )?.let { packages.getJSONObject(it.index) }
            ?: error(if (BuildConfig.DEBUG) "此开发预览版暂无匹配的更新包。" else "发布清单没有适用于这台设备的安装包。")
        return AndroidUpdate.decode(packageInfo, document.optString("notes")).takeIf { it.build > BuildConfig.VERSION_CODE }
    }

    private fun readDocument(address: String): String = read(address) { input ->
        val bytes = input.readNBytesCompat(512 * 1024 + 1)
        require(bytes.size <= 512 * 1024) { "发布清单过大。" }
        bytes.toString(Charsets.UTF_8)
    }

    fun download(context: Context, update: AndroidUpdate, cancel: AtomicBoolean, progress: (Float) -> Unit): File {
        val dir = File(context.cacheDir, "workbench-updates")
        check(dir.isDirectory || dir.mkdirs()) { "无法创建更新目录。" }
        val path = File.createTempFile("workbench-", ".apk", dir)
        try {
            val hash = MessageDigest.getInstance("SHA-256")
            var count = 0L
            var lastPercent = -1
            path.outputStream().buffered().use { output ->
                read(update.url) { input ->
                    val buffer = ByteArray(64 * 1024)
                    while (!cancel.get()) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        count += read
                        check(count <= update.size) { "安装包大小与发布清单不符。" }
                        output.write(buffer, 0, read)
                        hash.update(buffer, 0, read)
                        val percent = min(99, (100 * count / update.size).toInt())
                        if (percent > lastPercent) {
                            lastPercent = percent
                            progress(percent / 100f)
                        }
                    }
                }
            }
            check(!cancel.get()) { "下载已取消。" }
            check(count == update.size && hash.digest().hex() == update.sha256) { "安装包校验失败，请重新下载。" }
            verifyApk(context, path, update)
            return path
        } catch (error: Exception) {
            path.delete()
            throw error
        }
    }

    @Suppress("DEPRECATION")
    private fun verifyApk(context: Context, file: File, update: AndroidUpdate) {
        val pm = context.packageManager
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val installed = pm.getPackageInfo(context.packageName, flags)
        val apk = pm.getPackageArchiveInfo(file.absolutePath, flags) ?: error("无法读取 APK。")
        val versionCode = if (Build.VERSION.SDK_INT >= 28) apk.longVersionCode else apk.versionCode.toLong()
        check(apk.packageName == context.packageName && versionCode == update.build && apk.versionName == update.version) {
            "APK 的应用或版本与发布清单不符。"
        }
        val old = if (Build.VERSION.SDK_INT >= 28) installed.signingInfo?.apkContentsSigners else installed.signatures
        val new = if (Build.VERSION.SDK_INT >= 28) apk.signingInfo?.apkContentsSigners else apk.signatures
        check(!old.isNullOrEmpty() && !new.isNullOrEmpty() &&
            old.map { it.toCharsString() }.toSet() == new.map { it.toCharsString() }.toSet()
        ) { "安装包签名与当前版本不同。请使用同一来源发布的 APK。" }
    }

    private fun <T> read(address: String, consume: (InputStream) -> T): T {
        val connection = URL(address).openConnection() as HttpsURLConnection
        connection.connectTimeout = 15_000
        connection.readTimeout = 30_000
        connection.useCaches = false
        connection.setRequestProperty("User-Agent", "Agent-Workbench")
        try {
            check(connection.responseCode == 200) { "无法从 GitHub 读取更新（HTTP ${connection.responseCode}）。" }
            return connection.inputStream.buffered().use(consume)
        } finally {
            connection.disconnect()
        }
    }

    private fun InputStream.readNBytesCompat(limit: Int): ByteArray {
        val output = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (output.size() < limit) {
            val read = read(buffer, 0, min(buffer.size, limit - output.size()))
            if (read < 0) break
            output.write(buffer, 0, read)
        }
        return output.toByteArray()
    }

    private fun ByteArray.hex() = joinToString("") { "%02x".format(it.toInt() and 0xff) }
}

class WorkbenchUpdates(private val application: Application) : AndroidViewModel(application) {
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val prefs = application.getSharedPreferences("app-updates", Context.MODE_PRIVATE)
    private val key = "version-${BuildConfig.VERSION_CODE}-${BuildConfig.BUILD_TYPE}"
    private var selected by mutableStateOf(prefs.getString("$key.available", null)?.let { cached ->
        runCatching { JSONObject(cached).let { AndroidUpdate.decode(it, it.getString("notes")) } }.getOrNull()
    })
    private var staged by mutableStateOf<File?>(null)
    private var cancel: AtomicBoolean? = null
    private var cleared = false
    private var showCheckResult = false
    var checking by mutableStateOf(false); private set
    var downloading by mutableStateOf(false); private set
    var status by mutableStateOf(""); private set
    var progress by mutableFloatStateOf(0f); private set
    var show by mutableStateOf(false)
    val ready get() = staged != null
    val nextVersion get() = selected?.version
    val notes get() = selected?.notes.orEmpty()
    val isPreview get() = selected?.channel == "preview"

    private val checkLoop = object : Runnable {
        override fun run() {
            check()
            main.postDelayed(this, 900_000)
        }
    }

    fun setForeground(value: Boolean) {
        main.removeCallbacks(checkLoop)
        if (value) main.post(checkLoop)
    }

    fun open() {
        show = true
        if (selected == null) check(manual = true)
    }

    fun check(manual: Boolean = false) {
        if (checking) {
            if (manual) {
                showCheckResult = true
                status = "正在检查更新…"
            }
            return
        }
        if (downloading || ready) return
        if (!manual && prefs.getLong("$key.next", 0) > System.currentTimeMillis()) return
        checking = true
        showCheckResult = manual
        if (manual) status = "正在检查更新…"
        worker.execute {
            val result = runCatching { AndroidUpdateService.lookup() }
            main.post {
                if (cleared) return@post
                checking = false
                if (result.isSuccess) save(result.getOrNull())
                prefs.edit().putLong("$key.next", System.currentTimeMillis() + if (result.isSuccess) CHECK_INTERVAL else RETRY_INTERVAL).apply()
                if (showCheckResult) status = result.fold({ if (it == null) "当前已是最新版本。" else "" }, { message(it) })
            }
        }
    }

    fun download() {
        val expected = selected ?: return
        if (checking || downloading || ready) return
        downloading = true
        progress = 0f
        status = "正在下载并校验更新…"
        val abort = AtomicBoolean(false)
        cancel = abort
        worker.execute {
            val result = runCatching {
                val release = AndroidUpdateService.lookup()
                if (!expected.samePackage(release)) {
                    main.post { if (!cleared) save(release) }
                    error("可用版本已变化，请查看最新说明。")
                }
                check(!abort.get()) { "下载已取消。" }
                AndroidUpdateService.download(application, expected, abort) { fraction ->
                    main.post { if (!cleared && downloading) progress = fraction }
                }
            }
            main.post {
                val file = result.getOrNull()
                if (cleared) {
                    file?.delete()
                    return@post
                }
                downloading = false
                cancel = null
                if (abort.get()) {
                    file?.delete()
                    status = "下载已取消。"
                } else if (file != null) {
                    staged = file
                    status = "安装包已校验，可以安装。"
                } else status = message(result.exceptionOrNull())
            }
        }
    }

    fun cancelDownload() {
        cancel?.set(true)
        status = "正在取消下载…"
    }

    fun install(context: Context) {
        val file = staged ?: return
        if (!file.isFile) {
            staged = null
            status = "安装包已被系统清理，请重新下载。"
            return
        }
        try {
            if (!context.packageManager.canRequestPackageInstalls()) {
                status = "请允许从工作台安装应用，再回来点「安装更新」。"
                context.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}")))
                return
            }
            val uri = FileProvider.getUriForFile(context, context.packageName + ".updates", file)
            context.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, APK_MIME)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
        } catch (_: ActivityNotFoundException) {
            status = "无法打开系统安装界面，可从发布页手动安装。"
        } catch (_: SecurityException) {
            status = "系统未允许工作台安装应用，可从发布页手动安装。"
        }
    }

    private fun save(result: AndroidUpdate?) {
        selected = result
        prefs.edit().putString("$key.available", result?.cache()).apply()
    }

    private fun message(error: Throwable?) = when (error) {
        is java.io.IOException -> "更新连接或文件读取失败，可以重试。"
        else -> error?.localizedMessage ?: "更新失败，可以重试。"
    }

    override fun onCleared() {
        cleared = true
        setForeground(false)
        cancel?.set(true)
        worker.shutdownNow()
    }
}

@Composable
internal fun WorkbenchUpdateDialog(updates: WorkbenchUpdates, running: Int, busy: Boolean) {
    val context = LocalContext.current
    var confirming by remember { mutableStateOf(false) }
    if (!updates.show) return
    AlertDialog(
        onDismissRequest = { updates.show = false },
        title = { Text("工作台更新") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (updates.isPreview) Text("测试版 · 可下载安装，尚未确认为正式版")
                Text("当前版本 ${BuildConfig.VERSION_NAME}" + (updates.nextVersion?.let { " · 新版本 $it" } ?: ""))
                if (updates.nextVersion != null) Text(updates.notes.ifBlank { "此版本暂无更新说明。" })
                if (updates.checking) LinearProgressIndicator(Modifier.fillMaxWidth())
                if (updates.downloading) LinearProgressIndicator(progress = { updates.progress }, modifier = Modifier.fillMaxWidth())
                if (updates.ready && running > 0) Text("还有 $running 个终端会话。系统安装期间会关闭工作台界面，建议等任务完成。")
                if (updates.ready && busy) Text("当前正在准备运行环境，请完成后再安装。")
                if (updates.status.isNotBlank()) Text(updates.status)
                TextButton(onClick = {
                    try { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(RELEASE_PAGE))) }
                    catch (_: ActivityNotFoundException) { }
                }) { Text("打开发布页") }
            }
        },
        dismissButton = {
            if (updates.downloading) TextButton(onClick = updates::cancelDownload) { Text("取消下载") }
            else TextButton(onClick = { updates.show = false }) { Text("稍后") }
        },
        confirmButton = {
            if (updates.ready) Button(enabled = !busy, onClick = {
                if (running > 0) confirming = true else updates.install(context)
            }) { Text("安装更新") }
            else if (updates.nextVersion != null) {
                Row {
                    TextButton(enabled = !updates.checking && !updates.downloading, onClick = { updates.check(manual = true) }) { Text("重新检查") }
                    Button(enabled = !updates.checking && !updates.downloading, onClick = updates::download) { Text("下载更新") }
                }
            } else Button(enabled = !updates.checking, onClick = { updates.check(manual = true) }) { Text("检查更新") }
        },
    )
    if (confirming) AlertDialog(
        onDismissRequest = { confirming = false },
        title = { Text("现在安装更新？") },
        text = { Text("系统安装时会关闭工作台界面。你也可以稍后再装。") },
        dismissButton = { TextButton(onClick = { confirming = false }) { Text("稍后") } },
        confirmButton = { Button(enabled = !busy, onClick = { confirming = false; updates.install(context) }) { Text("继续安装") } },
    )
}
