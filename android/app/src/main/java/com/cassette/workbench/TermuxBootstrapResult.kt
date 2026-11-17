package com.cassette.workbench

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Only the explicit one-use PendingIntent supplied to Termux can reach this. */
class TermuxBootstrapResult : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val store = ProfileStore(context)
        if (intent.action != "com.cassette.workbench.BOOTSTRAP.${store.bootstrapOperation()}") return
        val result = intent.getBundleExtra("result") ?: return
        if (result.getInt("exitCode", -1) != 0 || result.getInt("err", -1) != -1) {
            val detail = listOf(result.getString("errmsg"), result.getString("stderr"), result.getString("stdout"))
                .filterNotNull().joinToString("\n").takeLast(900)
            store.saveBootstrapError("Termux 准备未完成：$detail")
        }
    }
}
