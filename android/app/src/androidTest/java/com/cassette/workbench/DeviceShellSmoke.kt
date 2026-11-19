package com.cassette.workbench

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.os.Bundle
import android.util.Base64
import android.view.View
import android.view.ViewGroup
import android.view.accessibility.AccessibilityNodeInfo
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import androidx.lifecycle.ViewModelProvider
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Opt-in: only create disposable Shells; never send input to existing Agents. */
class DeviceShellSmoke(private val runner: Instrumentation) {
    fun run() {
        val result = Bundle()
        var model: WorkbenchModel? = null
        val created = mutableListOf<String>()
        var success = false
        try {
            val activity = runner.startActivitySync(Intent(runner.targetContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
            runner.runOnMainSync { model = ViewModelProvider(activity)[WorkbenchModel::class.java] }
            val active = model!!
            until { active.connected }
            check(active.localExecution) { "Select local execution before running this check" }
            runner.runOnMainSync {
                activity.getSystemService(InputMethodManager::class.java)
                    .hideSoftInputFromWindow(activity.window.decorView.windowToken, 0)
            }
            val original = active.sessions.map { it.id to it.bridgePort }
            fun launch(kind: String): String {
                val previous = active.sessions.map { it.id }.toSet()
                runner.runOnMainSync { active.launch(shell = true, shellKind = kind) }
                until { active.sessions.any { it.id !in previous } || (!active.busy && active.error.isNotBlank()) }
                check(active.error.isBlank()) { active.error }
                return active.sessions.single { it.id !in previous }.id.also { created += it }
            }
            fun output(id: String): String = active.sessions.first { it.id == id }.output
                .joinToString("") { Base64.decode(it.encoded, Base64.NO_WRAP).toString(Charsets.UTF_8) }
            val debian = launch("debian")
            check(active.termux.supportsRuntime()) { "New task did not upgrade the bridge" }
            runner.runOnMainSync {
                active.send(debian, "printf '\\nWB_DEBIAN '; id -un; printf 'WB_PROJECT '; pwd; test -z \"\${WORKBENCH_GATEWAY_KEY-}\${ANTHROPIC_API_KEY-}\" && printf 'WB_NO_KEY\\n'\r")
            }
            until { output(debian).contains("WB_DEBIAN workbench") && output(debian).contains("WB_NO_KEY\r\n") }
            val termux = launch("termux")
            runner.runOnMainSync { active.send(termux, "printf '\\nWB_TERMUX %s\\n' \"\$PREFIX\"\r") }
            until { output(termux).contains("WB_TERMUX /data/data/com.termux/files/usr") }
            check(!output(termux).contains("WB_DEBIAN workbench")) { "Shell output crossed sessions" }
            if (activity.resources.configuration.smallestScreenWidthDp >= 600) {
                runner.runOnMainSync { active.selectedSession = debian }
                until("split control") { node("分屏") != null || node("退出分屏") != null }
                if (node("分屏") != null) click("分屏")
                click("右侧：", prefix = true)
                click("Termux Shell")
                var panes = emptyList<TerminalWebView>()
                until("two ready terminal panes") {
                    runner.runOnMainSync { panes = terminals(activity.window.decorView) }
                    panes.size == 2 && panes.all { it.terminalReady }
                }
                val left = panes[0]
                val right = panes[1]
                until("expected Shells in both panes") { screen(left).contains("WB_DEBIAN workbench") && screen(right).contains("WB_TERMUX /data/data/com.termux/files/usr") }
                runner.runOnMainSync { left.showTerminalKeyboard() }
                until("IME visible with two usable panes") {
                    var visible = false
                    runner.runOnMainSync { visible = left.rootWindowInsets?.isVisible(android.view.WindowInsets.Type.ime()) == true }
                    visible && left.height >= 96 && right.height >= 96
                }
                commit(left, "printf 'WB_LEFT_IME\\n'")
                until("left IME input") { hasLine(left, "WB_LEFT_IME") }
                runner.runOnMainSync { right.showTerminalKeyboard() }
                commit(right, "printf 'WB_RIGHT_IME\\n'")
                until("right IME input") { hasLine(right, "WB_RIGHT_IME") }
                check(!output(termux).contains("WB_LEFT_IME") && !output(debian).contains("WB_RIGHT_IME")) { "Split IME input crossed panes" }
                // Cached terminal queries must not become fresh input when a pane is repainted.
                runner.runOnMainSync { active.send(termux, "stty -echo; printf 'WB_REPLAY_READY\\n'; IFS= read -r -t 2 -n 1 wb_probe; wb_rc=\$?; stty echo; test \"\$wb_rc\" -gt 128 && printf 'WB_REPLAY_SILENT\\n'\r") }
                until { hasLine(right, "WB_REPLAY_READY") }
                val query = Base64.encodeToString("\u001b]11;?\u0007\u001b[6n".toByteArray(), Base64.NO_WRAP)
                eval(right, "window.writeChunk('$query', true);true")
                until("cached queries remain silent") { hasLine(right, "WB_REPLAY_SILENT") }
                runner.runOnMainSync { activity.getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(right.windowToken, 0) }
            }
            check(original.all { (id, port) -> active.sessions.any { it.id == id && it.bridgePort == port } }) {
                "Existing terminal IDs or ports changed"
            }
            result.putString("stream", "PASS: Debian/Termux environments, clean Shell credentials, split panes remain visible with IME, separate pane input, silent cached queries, original terminals preserved; no model requests\n")
            success = true
        } catch (cause: Throwable) {
            result.putString("stream", "FAIL: ${cause.message}\n")
            val screenshot = java.io.File(runner.targetContext.getExternalFilesDir(null), "device-shell-failure.png")
            runCatching { screenshot.outputStream().use { runner.uiAutomation.takeScreenshot().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) } }
            model?.let { runner.sendStatus(0, Bundle().apply { putString("stream", "Shell check failed; screenshot: ${screenshot.absolutePath}\n") }) }
        } finally {
            model?.let { active ->
                created.forEach { id -> runner.runOnMainSync { active.close(id) } }
                runCatching { until { active.sessions.none { it.id in created } } }
            }
        }
        runner.finish(if (success) Activity.RESULT_OK else Activity.RESULT_CANCELED, result)
    }

    private fun terminals(view: View): List<TerminalWebView> = when (view) {
        is TerminalWebView -> listOf(view)
        is ViewGroup -> (0 until view.childCount).flatMap { terminals(view.getChildAt(it)) }
        else -> emptyList()
    }

    private fun node(text: String, prefix: Boolean = false): AccessibilityNodeInfo? {
        fun find(current: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            current ?: return null
            var match = current.takeIf { if (prefix) it.text?.startsWith(text) == true else it.text?.toString() == text }
            // Popups follow the underlying tabs in the accessibility tree.
            for (index in 0 until current.childCount) find(current.getChild(index))?.let { match = it }
            return match
        }
        return find(runner.uiAutomation.rootInActiveWindow)
    }

    private fun click(text: String, prefix: Boolean = false) {
        until("control $text") { node(text, prefix) != null }
        var target = node(text, prefix)
        while (target != null && !target.isClickable) target = target.parent
        check(target?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true) { "Could not click $text" }
        runner.waitForIdleSync()
        // Compose popup semantics arrive on a later frame than ACTION_CLICK.
        runCatching { runner.uiAutomation.waitForIdle(300, 3000) }
    }

    private fun commit(view: TerminalWebView, text: String) {
        runner.runOnMainSync {
            val connection = view.onCreateInputConnection(EditorInfo()) ?: error("Missing terminal input connection")
            check(connection.commitText(text, 1))
            connection.performEditorAction(EditorInfo.IME_ACTION_DONE)
        }
    }

    private fun screen(view: WebView) = eval(view, "Array.from({length:terminal.buffer.active.length},(_,i)=>terminal.buffer.active.getLine(i)?.translateToString(true)||'').join('\\n')")

    private fun hasLine(view: WebView, line: String) = eval(view, "Array.from({length:terminal.buffer.active.length},(_,i)=>terminal.buffer.active.getLine(i)?.translateToString(true)||'').some(line=>line.trim()===${org.json.JSONObject.quote(line)})") == "true"

    private fun eval(view: WebView, script: String): String {
        val done = CountDownLatch(1)
        var value = ""
        runner.runOnMainSync { view.evaluateJavascript(script) { value = it; done.countDown() } }
        check(done.await(5, TimeUnit.SECONDS)) { "Terminal JavaScript timed out" }
        return value
    }

    private fun until(description: String = "Shell verification", condition: () -> Boolean) {
        if (description != "Shell verification") runner.sendStatus(0, Bundle().apply { putString("stream", "Checking $description\n") })
        val deadline = System.currentTimeMillis() + 45000
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return
            Thread.sleep(150)
        }
        error("Timed out waiting for $description")
    }
}
