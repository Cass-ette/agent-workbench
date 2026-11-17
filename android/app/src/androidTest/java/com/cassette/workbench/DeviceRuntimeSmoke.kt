package com.cassette.workbench

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.os.Bundle
import androidx.lifecycle.ViewModelProvider

/** Explicit opt-in device check: install dependencies, query versions, no model request. */
class DeviceRuntimeSmoke(private val runner: Instrumentation) {
    fun run() {
        val result = Bundle()
        try {
            val activity = runner.startActivitySync(Intent(runner.targetContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
            lateinit var model: WorkbenchModel
            runner.runOnMainSync { model = ViewModelProvider(activity)[WorkbenchModel::class.java] }
            val original = model.sessions.map { it.id to it.bridgePort }
            runner.runOnMainSync { model.installRuntime() }
            val deadline = System.currentTimeMillis() + 15 * 60 * 1000
            var lastStage = ""
            while (System.currentTimeMillis() < deadline) {
                if (model.runtimeStage != lastStage) {
                    lastStage = model.runtimeStage
                    runner.sendStatus(0, Bundle().apply { putString("stage", lastStage) })
                }
                if (!model.busy && model.runtimeState == "ready") break
                check(model.runtimeState !in setOf("failed", "interrupted")) { model.runtimeStage + "\n" + model.runtimeLog.takeLast(1800) }
                if (!model.busy && model.error.isNotBlank()) error(model.error)
                Thread.sleep(500)
            }
            check(model.runtimeState == "ready") { "Runtime check timed out" }
            check(original.all { old -> model.sessions.any { it.id == old.first && it.bridgePort == old.second } }) { "Existing terminals changed" }
            check(model.termux.request("/health").optString("version") == BuildConfig.VERSION_NAME)
            val profiles = ProfileStore(runner.targetContext).profiles()
            runner.sendStatus(0, Bundle().apply { putInt("saved_profile_count", profiles.size) })
            model.profile?.let { selected ->
                val models = GatewayClient.models(selected.gateway, ProfileStore(runner.targetContext).secret(selected.id))
                runner.sendStatus(0, Bundle().apply { putInt("real_gateway_model_count", models.size) })
            }
            result.putString("stream", "PASS: device dependency setup and non-root CLI versions; existing terminals preserved; no model requests\n")
            runner.finish(Activity.RESULT_OK, result)
        } catch (cause: Throwable) {
            result.putString("stream", "FAIL: ${cause.message}\n")
            runner.finish(Activity.RESULT_CANCELED, result)
        }
    }
}
