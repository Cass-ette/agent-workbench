package com.cassette.workbench

import android.app.Instrumentation
import android.os.Bundle

internal fun Instrumentation.finishAfterAutomationConnect(resultCode: Int, results: Bundle) {
    val deadline = android.os.SystemClock.uptimeMillis() + 5000
    while (true) {
        try {
            finish(resultCode, results)
            return
        } catch (failure: IllegalStateException) {
            if (failure.message?.contains("while connecting UiAutomation") != true ||
                android.os.SystemClock.uptimeMillis() >= deadline
            ) throw failure
            Thread.sleep(100)
        }
    }
}
