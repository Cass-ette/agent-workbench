package com.cassette.workbench

import android.app.Activity
import android.app.Instrumentation
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.webkit.WebView
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.Lifecycle
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Emulator-only test against a loopback PTY fixture; never uses model APIs or real keys. */
class TerminalInputInstrumentation : Instrumentation() {
    private lateinit var args: Bundle
    override fun onCreate(arguments: Bundle?) { args = arguments ?: Bundle(); start() }

    override fun onStart() {
        if (args.getString("device_smoke") == "true") {
            DeviceRuntimeSmoke(this).run()
            return
        }
        val result = Bundle()
        var activity: Activity? = null
        var model: WorkbenchModel? = null
        var browser: WebView? = null
        var resultCode = Activity.RESULT_CANCELED
        val prefs = targetContext.getSharedPreferences("workbench", Context.MODE_PRIVATE)
        val credentials = targetContext.getSharedPreferences("credentials", Context.MODE_PRIVATE)
        val originalPrefs = prefs.all.toMap()
        val originalCredentials = credentials.all.toMap()
        try {
            check(Build.MODEL.startsWith("sdk_gphone")) { "Run only on the test emulator" }
            val store = ProfileStore(targetContext)
            store.saveBridgePort(args.getString("bridge_port", "19887").toInt())
            store.saveSecret("bridge", "workbench-ui-fixture-token-only-00000000")
            store.saveSessions(org.json.JSONArray())
            store.saveOptions(TaskOptions(workspace = args.getString("workspace") ?: error("Missing fixture directory")))
            activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            val active = activity as MainActivity
            runOnMainSync { model = ViewModelProvider(active)[WorkbenchModel::class.java] }
            until("bridge connected") { model!!.connected }
            val gatewayModels = GatewayClient.models("http://127.0.0.1:${args.getString("bridge_port", "19887")}/test/gateway", "metadata-test-key")
            check(gatewayModels == listOf("synthetic-model")) { "Gateway authentication regression" }
            runOnMainSync { model!!.launch(shell = true) }
            until("terminal created") { model!!.sessions.isNotEmpty() }
            until("WebView attached") { runOnMainSync { browser = findWebView(active.window.decorView) }; browser != null }
            val view = browser!!
            until("terminal ready") { eval(view, "typeof window.writeChunk === 'function'") == "true" }
            check(eval(view, "terminal.rows > 2 && terminal.cols > 10") == "true") { "Terminal has no usable size" }
            until("shell prompt") { eval(view, "terminal.buffer.active.length > 0 && Array.from({length: terminal.rows}, (_,i)=>terminal.buffer.active.getLine(i)?.translateToString(true)||'').join('\\n').includes('WB_TEST')") == "true" }
            val sessionId = model!!.sessions.single().id
            val state = model!!.termux.request("/test/state")
            val pid = state.getJSONObject("sessions").getJSONObject(sessionId).getLong("pid")
            val creates = state.getInt("creates")
            // A pending command keeps running while the UI is stopped.
            runOnMainSync { model!!.send(sessionId, "sleep 2; printf '\\102\\107\\137\\117\\113\\n'\r") }
            sendKeyDownUpSync(KeyEvent.KEYCODE_HOME)
            until("Activity stopped") { !active.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED) }
            Thread.sleep(400) // Drain any request already in flight at onStop.
            val beforeBackground = model!!.termux.request("/test/state").getInt("reads")
            Thread.sleep(2400)
            check(model!!.termux.request("/test/state").getInt("reads") == beforeBackground) { "App kept polling while backgrounded" }
            runOnMainSync { active.startActivity(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)) }
            until("reconnected on foreground") { model!!.connected && !model!!.bridgeReconnecting }
            until("background command output restored") { hasLine(view, "BG_OK") }
            check(model!!.sessions.single().id == sessionId) { "Resume replaced the original session" }
            check(model!!.termux.request("/test/state").getJSONObject("sessions").getJSONObject(sessionId).getLong("pid") == pid) { "Resume replaced the Shell process" }
            // An ordinary read failure plus a failed health check must recover without a tap.
            model!!.termux.request("/test/state", "POST", org.json.JSONObject().put("reads", 1).put("health", 1))
            until("temporary disconnection") { !model!!.connected && model!!.bridgeReconnecting }
            until("automatic bridge recovery") { model!!.connected && !model!!.bridgeReconnecting }
            check(model!!.sessions.single().id == sessionId) { "Transient failure removed the session" }
            check(model!!.termux.request("/test/state").getInt("creates") == creates) { "Reconnection created a duplicate terminal" }
            val xy = IntArray(2)
            runOnMainSync { view.getLocationOnScreen(xy) }
            val now = android.os.SystemClock.uptimeMillis()
            sendPointerSync(MotionEvent.obtain(now, now, MotionEvent.ACTION_DOWN, (xy[0] + 30).toFloat(), (xy[1] + 30).toFloat(), 0))
            sendPointerSync(MotionEvent.obtain(now, now + 50, MotionEvent.ACTION_UP, (xy[0] + 30).toFloat(), (xy[1] + 30).toFloat(), 0))
            waitForIdleSync()
            var committed = false
            runOnMainSync {
                val connection = view.onCreateInputConnection(EditorInfo())
                check(connection != null) { "WebView did not expose an input connection" }
                committed = connection.commitText("printf '\\127\\102\\137\\111\\116\\120\\125\\124\\137\\117\\113\\n'", 1)
            }
            runOnMainSync {
                val connection = view.onCreateInputConnection(EditorInfo())!!
                connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER))
                connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER))
            }
            check(committed) { "IME rejected committed text" }
            until("IME input executed by Shell and echoed") {
                eval(view, "Array.from({length: terminal.buffer.active.length}, (_,i)=>terminal.buffer.active.getLine(i)?.translateToString(true)||'').some(line=>line.trim()==='WB_INPUT_OK')") == "true"
            }
            runOnMainSync {
                val connection = view.onCreateInputConnection(EditorInfo())!!
                connection.commitText("printf '", 1)
                connection.setComposingText("zhong", 1)
                connection.setComposingText("中文", 1)
                connection.commitText("中文", 1)
                connection.finishComposingText()
                connection.commitText("\\n'", 1)
                connection.performEditorAction(EditorInfo.IME_ACTION_DONE)
            }
            until("Chinese composition committed once") { hasLine(view, "中文") }
            runOnMainSync {
                val connection = view.onCreateInputConnection(EditorInfo())!!
                connection.commitText("printf 'DELETE_OX", 1)
                connection.deleteSurroundingText(1, 0)
                connection.commitText("K\\n'", 1)
                connection.performEditorAction(EditorInfo.IME_ACTION_DONE)
            }
            until("IME backspace removes one character") { hasLine(view, "DELETE_OK") }
            verifyScrolling(model!!, view, sessionId)
            val screenshot = java.io.File(targetContext.getExternalFilesDir(null), "terminal-input-test.png")
            screenshot.outputStream().use { uiAutomation.takeScreenshot().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
            runOnMainSync { model!!.close(model!!.sessions.single().id) }
            until("last terminal removed") { model!!.sessions.isEmpty() }
            check(store.sessionRows().length() == 0) { "Closed terminal was persisted" }
            result.putString("stream", "PASS: background recovery, IME input, Shell scroll without PTY input, full-screen TUI button/touch/edge scrolling, SGR and binary mouse reports, fragmented UTF-8, last-tab close\n")
            result.putString("result", "PASS")
            resultCode = Activity.RESULT_OK
        } catch (cause: Throwable) {
            browser?.let { view -> result.putString("terminal_debug", eval(view, "JSON.stringify({active:document.activeElement?.className,value:document.querySelector('textarea')?.value,rows:terminal.rows,lines:Array.from({length:terminal.buffer.active.length},(_,i)=>terminal.buffer.active.getLine(i)?.translateToString(true)||'')})")) }
            result.putString("session_id", model?.sessions?.firstOrNull()?.id ?: "")
            result.putString("stream", "FAIL: ${cause.message}\n${cause.stackTraceToString()}")
            result.putString("result", "FAIL")
        } finally {
            model?.sessions?.toList().orEmpty().forEach { session -> runCatching { model!!.termux.request("/sessions/${session.id}", "DELETE") } }
            runOnMainSync { activity?.finish() }
            waitForIdleSync()
            restore(prefs, originalPrefs)
            restore(credentials, originalCredentials)
        }
        finish(resultCode, result)
    }

    private fun restore(prefs: android.content.SharedPreferences, values: Map<String, *>) {
        val editor = prefs.edit().clear()
        values.forEach { (key, value) -> when (value) {
            is String -> editor.putString(key, value)
            is Int -> editor.putInt(key, value)
            is Long -> editor.putLong(key, value)
            is Boolean -> editor.putBoolean(key, value)
            is Float -> editor.putFloat(key, value)
            is Set<*> -> @Suppress("UNCHECKED_CAST") editor.putStringSet(key, value as Set<String>)
        } }
        editor.commit()
    }
    private fun verifyScrolling(model: WorkbenchModel, view: WebView, id: String) {
        runOnMainSync { targetContext.getSystemService(android.view.inputmethod.InputMethodManager::class.java).hideSoftInputFromWindow(view.windowToken, 0) }
        runOnMainSync { model.send(id, "for ((i=0;i<100;i++)); do printf 'SCROLL_%s\\n' \"\$i\"; done\r") }
        until("Shell scrollback filled") { hasLine(view, "SCROLL_99") && eval(view, "terminal.buffer.active.baseY > terminal.rows") == "true" }
        val inputs = model.termux.request("/test/state").getInt("inputs")
        val before = eval(view, "terminal.buffer.active.viewportY").toInt()
        scrollButton(view, true)
        until("up button scrolls Shell") { eval(view, "terminal.buffer.active.viewportY").toInt() < before }
        check(model.termux.request("/test/state").getInt("inputs") == inputs) { "Shell scrolling sent command history keys" }
        eval(view, "window.jumpToBottom('claude');window.jumpToBottom('codex');window.jumpToBottom('shell');true")
        until("jump to bottom moves Shell viewport") { eval(view, "terminal.buffer.active.viewportY === terminal.buffer.active.baseY") == "true" }
        check(model.termux.request("/test/state").getInt("inputs") == inputs) { "Jump to bottom sent a shortcut to a Shell" }
        eval(view, "window.scrollByButton(-1);true")
        until("restore scrolled Shell before down button") { eval(view, "terminal.buffer.active.viewportY").toInt() < before }
        scrollButton(view, false)
        until("down button scrolls Shell") { eval(view, "terminal.buffer.active.viewportY").toInt() == before }
        eval(view, "terminal.scrollToLine(Math.floor(terminal.buffer.active.baseY/2));true")
        val middle = eval(view, "terminal.buffer.active.viewportY").toInt()
        dragInView(view, "40", "50", 80.0)
        until("finger down scrolls Shell toward older lines") { eval(view, "terminal.buffer.active.viewportY").toInt() < middle }
        val fingerDown = eval(view, "terminal.buffer.active.viewportY").toInt()
        dragInView(view, "40", "150", -80.0)
        until("finger up scrolls Shell toward newer lines") { eval(view, "terminal.buffer.active.viewportY").toInt() > fingerDown }
        check(model.termux.request("/test/state").getInt("inputs") == inputs) { "Finger scrolling sent Shell input" }
        eval(view, "terminal.scrollToBottom();true")
        val python = model.termux.request("/test/state").getString("python")
        val script = "${args.getString("workspace")}/scroll_tui.py"
        runOnMainSync { model.send(id, "'${python.replace("'", "'\\''")}' '${script.replace("'", "'\\''")}'\r") }
        until("full-screen TUI ready") { tuiTop(view) == 80 && eval(view, "terminal.buffer.active.type === 'alternate' && terminal.modes.mouseTrackingMode === 'any'") == "true" }
        eval(view, "window.jumpToBottom('claude');true")
        until("Claude bottom uses Ctrl+End") { tuiTop(view) == 100 }
        runOnMainSync { model.send(id, "r") }
        until("reset after Claude bottom") { tuiTop(view) == 80 }
        scrollButton(view, true)
        until("up button scrolls TUI history") { tuiTop(view) in 0..79 }
        val up = tuiTop(view)
        scrollButton(view, false)
        until("down button scrolls TUI history") { tuiTop(view) > up }
        // The absolute xterm offset stays zero; the foreground app actually redraws.
        check(eval(view, "terminal.buffer.active.baseY === 0 && terminal.buffer.active.viewportY === 0") == "true")
        runOnMainSync { model.send(id, "r") }
        until("reset TUI position") { tuiTop(view) == 80 }
        dragInView(view, "terminal.element.querySelector('.xterm-screen').getBoundingClientRect().left + 40", "terminal.element.querySelector('.xterm-screen').getBoundingClientRect().top + 40", 100.0)
        until("finger down scrolls TUI toward older lines") { tuiTop(view) in 0..79 }
        runOnMainSync { model.send(id, "r") }
        until("reset before finger up") { tuiTop(view) == 80 }
        dragInView(view, "40", "150", -80.0)
        until("finger up scrolls TUI toward newer lines") { tuiTop(view) > 80 }
        runOnMainSync { model.send(id, "r") }
        until("reset before mouse wheel") { tuiTop(view) == 80 }
        mouseWheel(view, 1f)
        until("mouse wheel up retains older-history direction") { tuiTop(view) in 0..79 }
        val mouseUp = tuiTop(view)
        mouseWheel(view, -1f)
        until("mouse wheel down retains newer-history direction") { tuiTop(view) > mouseUp }
        eval(view, "window.touchScrollCalls=0;const originalTouchScroll=window.scrollByTouch;window.scrollByTouch=p=>{window.touchScrollCalls++;originalTouchScroll(p)};true")
        dragInView(view, "40", "50", 80.0, mouse = true)
        check(eval(view, "window.touchScrollCalls") == "0") { "Mouse drag was misclassified as finger scroll" }
        runOnMainSync { model.send(id, "r") }
        until("reset before right-edge drag") { tuiTop(view) == 80 }
        dragInView(view, "window.innerWidth - 10", "50", 60.0)
        until("right edge is available for content scrolling") { tuiTop(view) in 0..79 }
        runOnMainSync { model.send(id, "br") }
        until("legacy mouse mode ready") { tuiTop(view) == 80 }
        scrollButton(view, true)
        until("binary mouse reports reach TUI") { tuiTop(view) in 0..79 }
        check(eval(view, "terminal.buffer.active.getLine(0).translateToString(true).includes('UNEXPECTED=0')") == "true") { "Scrolling sent unexpected input" }
        runOnMainSync { model.send(id, "cr") }
        until("Codex transcript fixture ready") { tuiTop(view) == 80 && eval(view, "terminal.modes.mouseTrackingMode === 'none' && terminal.buffer.active.getLine(0).translateToString(true).includes('T R A N S C R I P T')") == "true" }
        scrollButton(view, true, "codex")
        until("up button scrolls Codex transcript without mouse tracking") { tuiTop(view) < 80 }
        val codexUp = tuiTop(view)
        scrollButton(view, false, "codex")
        until("down button scrolls Codex transcript without mouse tracking") { tuiTop(view) > codexUp }
        runOnMainSync { model.send(id, "g") }
        until("generic full-screen TUI ready without mouse tracking") { tuiTop(view) == 80 && eval(view, "terminal.modes.mouseTrackingMode === 'none' && terminal.buffer.active.getLine(0).translateToString(true).includes('TUI_TOP=80')") == "true" }
        scrollButton(view, true)
        until("up button uses PageUp for generic full-screen TUI") { tuiTop(view) < 80 }
        val genericUp = tuiTop(view)
        scrollButton(view, false)
        until("down button uses PageDown for generic full-screen TUI") { tuiTop(view) > genericUp }
        runOnMainSync { model.send(id, "r") }
        until("generic full-screen TUI reset") { tuiTop(view) == 80 }
        runOnMainSync { model.send(id, "c") }
        until("Codex transcript mode restored") { tuiTop(view) == 80 && eval(view, "terminal.modes.mouseTrackingMode === 'none' && terminal.buffer.active.getLine(0).translateToString(true).includes('T R A N S C R I P T')") == "true" }
        eval(view, "window.jumpToBottom('codex');true")
        until("Codex transcript bottom uses End") { tuiTop(view) == 100 }
        check(eval(view, "terminal.buffer.active.getLine(1).translateToString(true).includes('UNEXPECTED=0')") == "true") { "Codex received the wrong bottom shortcut" }
        runOnMainSync { model.send(id, "q") }
        until("TUI exits back to Shell") { eval(view, "terminal.buffer.active.type === 'normal' && terminal.modes.mouseTrackingMode === 'none'") == "true" }
        eval(view, "window.writeChunk(btoa('SPLIT_'+String.fromCharCode(0xe7)));window.writeChunk(btoa(String.fromCharCode(0x95,0x8c)+'_END'));true")
        until("UTF-8 byte split survives output chunks") { eval(view, "Array.from({length:terminal.buffer.active.length},(_,i)=>terminal.buffer.active.getLine(i)?.translateToString(true)||'').some(line=>line.includes('SPLIT_界_END'))") == "true" }
    }
    private fun tuiTop(view: WebView): Int = eval(view, "Number(Array.from({length:3},(_,i)=>terminal.buffer.active.getLine(i)?.translateToString(true)||'').join(' ').match(/TUI_TOP=(\\d+)/)?.[1]??-1)").toInt()
    private fun scrollButton(view: WebView, up: Boolean, client: String = "shell") {
        eval(view, "window.scrollByButton(${if (up) -1 else 1}, ${org.json.JSONObject.quote(client)});true")
    }
    private fun dragInView(view: WebView, xExpression: String, yExpression: String, dy: Double, mouse: Boolean = false) {
        val xy = IntArray(2)
        runOnMainSync { view.getLocationOnScreen(xy) }
        val scale = view.width / eval(view, "window.innerWidth").toDouble()
        val x = (xy[0] + eval(view, xExpression).toDouble()*scale).toFloat()
        val y = (xy[1] + eval(view, yExpression).toDouble()*scale).toFloat()
        val now = android.os.SystemClock.uptimeMillis()
        fun event(action: Int, whenMs: Long, eventY: Float): MotionEvent {
            val pointer = MotionEvent.PointerProperties().apply { id=0; toolType=if (mouse) MotionEvent.TOOL_TYPE_MOUSE else MotionEvent.TOOL_TYPE_FINGER }
            val coords = MotionEvent.PointerCoords().apply { this.x=x; this.y=eventY; pressure=1f; size=1f }
            return MotionEvent.obtain(now, whenMs, action, 1, arrayOf(pointer), arrayOf(coords), 0,
                if (mouse && action != MotionEvent.ACTION_UP) MotionEvent.BUTTON_PRIMARY else 0,
                1f, 1f, 0, 0, if (mouse) android.view.InputDevice.SOURCE_MOUSE else android.view.InputDevice.SOURCE_TOUCHSCREEN, 0)
        }
        sendPointerSync(event(MotionEvent.ACTION_DOWN, now, y))
        for (step in 1..8) sendPointerSync(event(MotionEvent.ACTION_MOVE, now+step*30L, y+(dy*scale*step/8).toFloat()))
        sendPointerSync(event(MotionEvent.ACTION_UP, now+270, y+(dy*scale).toFloat()))
    }
    private fun mouseWheel(view: WebView, vertical: Float) {
        val now=android.os.SystemClock.uptimeMillis()
        val pointer=MotionEvent.PointerProperties().apply { id=0; toolType=MotionEvent.TOOL_TYPE_MOUSE }
        val coords=MotionEvent.PointerCoords().apply { x=view.width/2f; y=view.height/2f; setAxisValue(MotionEvent.AXIS_VSCROLL,vertical) }
        val event=MotionEvent.obtain(now,now,MotionEvent.ACTION_SCROLL,1,arrayOf(pointer),arrayOf(coords),0,0,1f,1f,0,0,android.view.InputDevice.SOURCE_MOUSE,0)
        runOnMainSync { view.dispatchGenericMotionEvent(event) }
        event.recycle()
    }
    private fun findWebView(view: View): WebView? {
        if (view is WebView) return view
        if (view is ViewGroup) for (i in 0 until view.childCount) findWebView(view.getChildAt(i))?.let { return it }
        return null
    }
    private fun eval(view: WebView, expression: String): String {
        val latch = CountDownLatch(1)
        var value = ""
        runOnMainSync { view.evaluateJavascript(expression) { value = it; latch.countDown() } }
        check(latch.await(3, TimeUnit.SECONDS)) { "WebView JavaScript timed out" }
        return value
    }
    private fun hasLine(view: WebView, line: String) = eval(view,
        "Array.from({length:terminal.buffer.active.length},(_,i)=>terminal.buffer.active.getLine(i)?.translateToString(true)||'').some(line=>line.trim()===${org.json.JSONObject.quote(line)})") == "true"
    private fun until(label: String, condition: () -> Boolean) {
        val end = android.os.SystemClock.uptimeMillis() + 12000
        do { if (condition()) return; Thread.sleep(100) } while (android.os.SystemClock.uptimeMillis() < end)
        error("Timed out waiting for $label")
    }
}
