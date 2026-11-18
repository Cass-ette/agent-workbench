package com.cassette.workbench

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.os.SystemClock

/** Opt-in metadata check using the app's HTTPS stack and an existing on-device key. */
class DeviceGatewaySmoke(private val runner: Instrumentation) {
    fun run(args: Bundle) {
        val result = Bundle()
        try {
            val store = ProfileStore(runner.targetContext)
            val profileId = args.getString("profile_id") ?: store.options().profileId
            val profile = store.profiles().firstOrNull { it.id == profileId }
                ?: error("No saved profile selected")
            val gateway = GatewayPolicy.normalize(
                args.getString("gateway_url") ?: profile.gateway,
                profile.allowInsecureLan,
            )
            val start = SystemClock.elapsedRealtime()
            val models = GatewayClient.models(gateway, store.secret(profile.id))
            check(models.isNotEmpty()) { "Gateway returned no models" }
            result.putString("gateway", gateway)
            result.putInt("model_count", models.size)
            result.putLong("elapsed_ms", SystemClock.elapsedRealtime() - start)
            result.putString("stream", "PASS: gateway metadata; saved profile unchanged; no inference request\n")
            runner.finish(Activity.RESULT_OK, result)
        } catch (cause: Exception) {
            result.putString("error_type", cause.javaClass.simpleName)
            result.putString("error_stack", cause.stackTrace.take(8).joinToString("\n"))
            result.putString("stream", "FAIL: gateway metadata check; saved profile unchanged\n")
            runner.finish(Activity.RESULT_CANCELED, result)
        }
    }
}
