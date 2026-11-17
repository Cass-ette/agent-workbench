package com.cassette.workbench

import android.content.Context
import android.text.InputType
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView

/** Keep terminal rendering in xterm; Android IMEs submit text through a native connection. */
class TerminalWebView(context: Context, private val sendInput: (String) -> Unit) : WebView(context) {
    var terminalReady = false
    private val keyboard get() = context.getSystemService(InputMethodManager::class.java)
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    private var downY = 0f
    private var lastY = 0f
    private var touchMoved = false
    private var touchingScrollbar = false

    init {
        isFocusable = true
        isFocusableInTouchMode = true
    }

    fun showTerminalKeyboard() {
        if (!terminalReady) return
        requestFocus()
        evaluateJavascript("window.focusTerminal()", null)
        post { keyboard.showSoftInput(this, InputMethodManager.SHOW_IMPLICIT) }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downY = event.y
                lastY = event.y
                touchMoved = false
                touchingScrollbar = event.x >= width - 36 * resources.displayMetrics.density
            }
            MotionEvent.ACTION_MOVE -> {
                val delta = event.y - lastY
                if (!touchMoved && kotlin.math.abs(event.y - downY) > touchSlop) touchMoved = true
                if (!touchingScrollbar && touchMoved && kotlin.math.abs(delta) > 0.5f) {
                    // xterm owns its viewport inside the WebView, so WebView's native
                    // scrolling cannot move it. Forward a drag as line scrolling.
                    evaluateJavascript("window.scrollByTouch(${delta.toInt()})", null)
                    lastY = event.y
                }
            }
        }
        val handled = super.onTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_UP) {
            if (!touchMoved && !touchingScrollbar) showTerminalKeyboard()
            touchMoved = false
        }
        return handled
    }

    override fun onCheckIsTextEditor() = terminalReady || super.onCheckIsTextEditor()

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection? {
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
                if (text.isNotEmpty()) sendInput(text)
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
                if (!editable.isNullOrEmpty()) return super.deleteSurroundingText(beforeLength, afterLength)
                if (beforeLength > 0) sendInput("\u007f".repeat(beforeLength.coerceAtMost(1024)))
                if (afterLength > 0) sendInput("\u001b[3~".repeat(afterLength.coerceAtMost(1024)))
                return true
            }

            override fun deleteSurroundingTextInCodePoints(beforeLength: Int, afterLength: Int) =
                deleteSurroundingText(beforeLength, afterLength)

            override fun sendKeyEvent(event: KeyEvent): Boolean {
                // Hardware keys, navigation and modifiers retain xterm's key mappings.
                return this@TerminalWebView.dispatchKeyEvent(event)
            }

            override fun performEditorAction(actionCode: Int): Boolean {
                finishComposingText()
                sendInput("\r")
                return true
            }
        }
    }
}
