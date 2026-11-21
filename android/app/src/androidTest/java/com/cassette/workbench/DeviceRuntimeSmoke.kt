package com.cassette.workbench

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.os.Bundle
import androidx.lifecycle.ViewModelProvider

/** Explicit opt-in device check: install dependencies, query versions, no model request. */
class DeviceRuntimeSmoke(private val runner: Instrumentation) {
    fun run(environment: String? = null) {
        val result = Bundle()
        var resultCode = Activity.RESULT_CANCELED
        var active: WorkbenchModel? = null
        var originalEnvironment: String? = null
        try {
            val activity = runner.startActivitySync(Intent(runner.targetContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
            lateinit var model: WorkbenchModel
            runner.runOnMainSync { model = ViewModelProvider(activity)[WorkbenchModel::class.java] }
            active = model
            originalEnvironment = model.runtimeEnvironment
            val original = model.sessions.map { it.id to it.bridgePort }
            val vmBefore = if (environment == "vm") model.termux.request("/runtime").optJSONObject("vm") else null
            runner.runOnMainSync {
                if (environment != null) model.selectRuntimeEnvironment(environment)
                model.installRuntime()
            }
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
            if (environment != "vm") model.profile?.let { selected ->
                val models = GatewayClient.models(selected.gateway, ProfileStore(runner.targetContext).secret(selected.id))
                runner.sendStatus(0, Bundle().apply { putInt("real_gateway_model_count", models.size) })
            }
            if (environment == "vm") {
                val vm = model.termux.request("/runtime").getJSONObject("vm")
                check(vm.optJSONArray("clients").strings().containsAll(listOf("codex", "claude"))) { "VM clients were not verified" }
                if (vmBefore?.optString("state") == "running") {
                    check(vm.optString("state") == "running" && vm.optLong("pid") == vmBefore.optLong("pid")) { "Existing VM was stopped or replaced" }
                }
            }
            result.putString("stream", "PASS: ${environment ?: "default"} one-click dependency setup and CLI versions; existing terminals preserved; no model requests\n")
            resultCode = Activity.RESULT_OK
        } catch (cause: Throwable) {
            result.putString("stream", "FAIL: ${cause.message}\n")
        } finally {
            val previous = originalEnvironment
            if (previous != null) runner.runOnMainSync { active?.selectRuntimeEnvironment(previous) }
        }
        runner.finishAfterAutomationConnect(resultCode, result)
    }
}
