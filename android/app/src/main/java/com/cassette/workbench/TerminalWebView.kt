package com.cassette.workbench

import android.content.Context
import android.text.InputType
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import org.json.JSONObject
import org.json.JSONTokener

/** Keep terminal rendering in xterm; Android IMEs submit text through a native connection. */
class TerminalWebView(context: Context, private val sendInput: (String) -> Unit) : WebView(context) {
    var terminalReady = false
    var inputEnabled = true
    var onCopyText: ((String) -> Unit)? = null
    private val keyboard get() = context.getSystemService(InputMethodManager::class.java)
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    private var downY = 0f
    private var downX = 0f
    private var lastY = 0f
    private var touchMoved = false
    private var copyOpened = false
    private val longPressCopy = Runnable {
        if (terminalReady && !touchMoved) {
            copyOpened = true
            val cancel = MotionEvent.obtain(0, 0, MotionEvent.ACTION_CANCEL, 0f, 0f, 0)
            super.onTouchEvent(cancel)
            cancel.recycle()
            requestCopyText()
        }
    }

    init {
        isFocusable = true
        isFocusableInTouchMode = true
    }

    fun showTerminalKeyboard() {
        if (!terminalReady || !inputEnabled) return
        requestFocus()
        evaluateJavascript("window.focusTerminal()", null)
        post { keyboard.showSoftInput(this, InputMethodManager.SHOW_IMPLICIT) }
    }

    fun requestCopyText() {
        if (!terminalReady) return
        keyboard.hideSoftInputFromWindow(windowToken, 0)
        evaluateJavascript("window.terminalCopyText()") { encoded ->
            val text = runCatching { JSONTokener(encoded).nextValue() as? String }.getOrNull()
            if (text != null) onCopyText?.invoke(text)
        }
    }

    fun pasteText(text: String) {
        if (terminalReady && inputEnabled) {
            evaluateJavascript("window.pasteTerminalText(${JSONObject.quote(text)})", null)
        }
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(longPressCopy)
        super.onDetachedFromWindow()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        // A connected mouse must not change finger gestures. Classify the
        // current event, leaving mouse selection/dragging and wheels to WebView.
        if (event.isFromSource(InputDevice.SOURCE_MOUSE) || event.getToolType(0) == MotionEvent.TOOL_TYPE_MOUSE) {
            return super.onTouchEvent(event)
        }
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                removeCallbacks(longPressCopy)
                downX = event.x
                downY = event.y
                lastY = event.y
                touchMoved = false
                copyOpened = false
                postDelayed(longPressCopy, ViewConfiguration.getLongPressTimeout().toLong())
                if (terminalReady) evaluateJavascript("window.beginTouchScroll()", null)
            }
            MotionEvent.ACTION_MOVE -> {
                if (copyOpened) return true
                val delta = event.y - lastY
                if (kotlin.math.abs(event.x - downX) > touchSlop || kotlin.math.abs(event.y - downY) > touchSlop) {
                    removeCallbacks(longPressCopy)
                }
                if (!touchMoved && kotlin.math.abs(event.y - downY) > touchSlop) {
                    touchMoved = true
                    // Stop WebView/xterm's default touch scroll before taking
                    // over, otherwise it can cancel our finger scroll.
                    val cancel = MotionEvent.obtain(event)
                    cancel.action = MotionEvent.ACTION_CANCEL
                    super.onTouchEvent(cancel)
                    cancel.recycle()
                }
                if (touchMoved) {
                    val cssPixels = delta / resources.displayMetrics.density
                    evaluateJavascript("window.scrollByTouch($cssPixels)", null)
                    lastY = event.y
                    return true
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                removeCallbacks(longPressCopy)
                if (copyOpened) { copyOpened = false; return true }
                if (touchMoved) {
                    touchMoved = false
                    return true
                }
            }
        }
        val handled = super.onTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_UP) {
            showTerminalKeyboard()
            touchMoved = false
        }
        return handled
    }

    override fun onCheckIsTextEditor() = inputEnabled && (terminalReady || super.onCheckIsTextEditor())

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection? {
        if (!inputEnabled) return null
        if (!terminalReady) return super.onCreateInputConnection(outAttrs)
        // Do not advertise a password field: MIUI routes that variation to its
        // secure keyboard and composing Chinese text becomes unavailable.
        outAttrs.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_NORMAL or
            InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS or InputType.TYPE_TEXT_FLAG_MULTI_LINE
        outAttrs.imeOptions = EditorInfo.IME_ACTION_NONE or EditorInfo.IME_FLAG_NO_EXTRACT_UI or
            EditorInfo.IME_FLAG_NO_FULLSCREEN
        outAttrs.initialSelStart = 0
        outAttrs.initialSelEnd = 0
        return object : BaseInputConnection(this, true) {
            // Composing text stays with the IME until it is committed. This avoids sending
            // pinyin candidates or corrected intermediate words to the Shell.
            private fun flushCommittedText() {
                val buffer = editable ?: return
                val text = buffer.toString()
                buffer.clear()
                removeComposingSpans(buffer)
                if (text.isNotEmpty() && inputEnabled) sendInput(terminalImeCommit(text))
            }

            override fun commitText(text: CharSequence?, newCursorPosition: Int): Boolean {
                super.commitText(text, newCursorPosition)
                flushCommittedText()
                return true
            }

            override fun finishComposingText(): Boolean {
                super.finishComposingText()
                flushCommittedText()
                return true
            }

            override fun deleteSurroundingText(beforeLength: Int, afterLength: Int): Boolean {
                if (!inputEnabled) return true
                if (!editable.isNullOrEmpty()) return super.deleteSurroundingText(beforeLength, afterLength)
                if (beforeLength > 0) sendInput("\u007f".repeat(beforeLength.coerceAtMost(1024)))
                if (afterLength > 0) sendInput("\u001b[3~".repeat(afterLength.coerceAtMost(1024)))
                return true
            }

            override fun deleteSurroundingTextInCodePoints(beforeLength: Int, afterLength: Int) =
                deleteSurroundingText(beforeLength, afterLength)

            override fun sendKeyEvent(event: KeyEvent): Boolean {
                if (!inputEnabled) return true
                if (event.keyCode == KeyEvent.KEYCODE_ENTER && event.hasNoModifiers()) {
                    if (event.action == KeyEvent.ACTION_DOWN) {
                        finishComposingText()
                        sendInput("\r")
                    }
                    return true
                }
                // Hardware keys, navigation and modifiers retain xterm's key mappings.
                return this@TerminalWebView.dispatchKeyEvent(event)
            }

            override fun performEditorAction(actionCode: Int): Boolean {
                finishComposingText()
                if (inputEnabled) sendInput("\r")
                return true
            }
        }
    }
}
