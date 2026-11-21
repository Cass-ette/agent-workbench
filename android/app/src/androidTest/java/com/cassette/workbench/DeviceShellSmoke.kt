package com.cassette.workbench

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.os.Bundle
import android.util.Base64
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.accessibility.AccessibilityNodeInfo
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import androidx.lifecycle.ViewModelProvider
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Opt-in: only create disposable Shells; never send input to existing Agents. */
class DeviceShellSmoke(private val runner: Instrumentation) {
    fun run(checkVm: Boolean, attachRunningVm: Boolean = false) {
        val result = Bundle()
        var model: WorkbenchModel? = null
        val created = mutableListOf<String>()
        var success = false
        try {
            check(!checkVm || !attachRunningVm) { "Choose either stopped-VM or running-VM verification" }
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
                var previous: Set<String>? = null
                until("bridge ready for $kind Shell") {
                    runner.runOnMainSync {
                        if (active.connected && !active.busy) {
                            previous = active.sessions.map { it.id }.toSet()
                            active.launch(shell = true, shellKind = kind)
                        }
                    }
                    previous != null
                }
                var opened: String? = null
                var launchError = ""
                until("$kind Shell opened") {
                    runner.runOnMainSync {
                        if (!active.busy) {
                            launchError = active.error
                            opened = active.sessions.singleOrNull { it.id !in previous.orEmpty() }?.id
                        }
                    }
                    check(launchError.isBlank()) { launchError }
                    opened != null
                }
                return checkNotNull(opened).also { created += it }
            }
            fun output(id: String): String = active.sessions.first { it.id == id }.output
                .joinToString("") { Base64.decode(it.encoded, Base64.NO_WRAP).toString(Charsets.UTF_8) }
            val debian = launch("debian")
            check(active.termux.supportsRuntime()) { "New task did not upgrade the bridge" }
            // Regression: remove a selected last tab while TabRow is subcomposing,
            // then restore fullscreen. Never close an original terminal.
            repeat(3) {
                val disposable = launch("termux")
                runner.waitForIdleSync()
                runner.runOnMainSync { active.close(disposable) }
                until("selected last tab removed") { active.sessions.none { it.id == disposable } }
                created.remove(disposable)
                runner.waitForIdleSync()
                runner.runOnMainSync { active.selectedSession = debian }
            }
            val runtime = active.termux.request("/runtime")
            val originalVm = runtime.getJSONObject("vm")
            runner.sendStatus(0, Bundle().apply {
                putString("stream", "Runtime: ABI=${runtime.optString("architecture")}; VM=${originalVm.optString("state")}; acceleration=${originalVm.optString("acceleration")}\n")
            })
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                fun barsVisible(visible: Boolean): Boolean {
                    var matches = false
                    runner.runOnMainSync {
                        val insets = activity.window.decorView.rootWindowInsets
                        matches = insets != null && insets.isVisible(WindowInsets.Type.statusBars()) == visible &&
                            insets.isVisible(WindowInsets.Type.navigationBars()) == visible
                    }
                    return matches
                }
                click("全屏")
                until("status and navigation bars are hidden") { barsVisible(false) }
                click("退出全屏")
                until("status and navigation bars are restored") { barsVisible(true) }
            }
            runner.runOnMainSync {
                active.send(debian, "printf '\\nWB_DEBIAN '; id -un; printf 'WB_PROJECT '; pwd; test -z \"\${WORKBENCH_GATEWAY_KEY-}\${ANTHROPIC_API_KEY-}\" && printf 'WB_NO_KEY\\n'\r")
            }
            until { output(debian).contains("WB_DEBIAN workbench") && output(debian).contains("WB_NO_KEY\r\n") }
            val termux = launch("termux")
            runner.runOnMainSync { active.send(termux, "printf '\\nWB_TERMUX %s\\n' \"\$PREFIX\"\r") }
            until { output(termux).contains("WB_TERMUX /data/data/com.termux/files/usr") }
            check(!output(termux).contains("WB_DEBIAN workbench")) { "Shell output crossed sessions" }
            if (checkVm || attachRunningVm) {
                until("installed VM metadata") { active.vmInstalled }
                check(originalVm.optString("state") == if (attachRunningVm) "running" else "stopped") {
                    "VM state does not match the explicitly selected verification mode"
                }
                val vm = launch("vm")
                until("VM login prompt", 240000) { output(vm).contains("workbench-linux:/projects") }
                runner.runOnMainSync {
                    active.send(vm, "printf '\\nWB_VM_ROOT '; id -u; test \"\$PWD\" = /projects && mountpoint -q /projects && printf 'WB_VM_PROJECT\\n'\r")
                }
                until("guest root and project mount") { output(vm).contains("WB_VM_ROOT 0") && output(vm).contains("WB_VM_PROJECT\r\n") }
                if (attachRunningVm) {
                    runner.runOnMainSync {
                        active.send(vm, "codex --version && claude --version && docker info --format '{{.ServerVersion}}'; printf 'WB_VM_CLIENTS_RESULT:%s\\n' \"\$?\"\r")
                    }
                    until("installed VM clients and Docker", 120000) { Regex("WB_VM_CLIENTS_RESULT:[0-9]+\\r\\n").containsMatchIn(output(vm)) }
                    val vmOutput = output(vm)
                    check(vmOutput.contains("WB_VM_CLIENTS_RESULT:0\r\n")) { "VM clients or Docker daemon are unavailable" }
                    check(vmOutput.contains("codex-cli 0.169.0") && vmOutput.contains("2.1.326")) { "VM client versions do not match the bundled installer" }
                    runner.sendStatus(0, Bundle().apply { putString("stream", "PASS: running VM root, project mount, Codex 0.169.0, Claude 2.1.326 and Docker daemon; VM kept running\n") })
                    val currentVm = active.termux.request("/runtime").getJSONObject("vm")
                    check(currentVm.optString("state") == "running" && currentVm.optLong("pid") == originalVm.optLong("pid")) { "Running VM process changed" }
                } else {
                    runner.runOnMainSync { active.controlVirtualMachine("stop") }
                    until("App stops VM", 120000) { !active.vmBusy && active.vmState == "stopped" && active.error.isBlank() }
                    runner.sendStatus(0, Bundle().apply { putString("stream", "PASS: Android system bars and local VM Shell, root, project mount and shutdown\n") })
                }
            }
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
        runner.finishAfterAutomationConnect(if (success) Activity.RESULT_OK else Activity.RESULT_CANCELED, result)
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

    private fun until(description: String = "Shell verification", timeout: Long = 45000, condition: () -> Boolean) {
        if (description != "Shell verification") runner.sendStatus(0, Bundle().apply { putString("stream", "Checking $description\n") })
        val deadline = System.currentTimeMillis() + timeout
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return
            Thread.sleep(150)
        }
        error("Timed out waiting for $description")
    }
}
