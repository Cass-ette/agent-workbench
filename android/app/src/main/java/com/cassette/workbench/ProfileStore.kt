package com.cassette.workbench

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class KeyProfile(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val gateway: String,
    val models: List<String> = emptyList(),
    val favorites: Set<String> = emptySet(),
    val allowInsecureLan: Boolean = false,
) {
    fun json() = JSONObject().put("id", id).put("name", name).put("gateway", gateway)
        .put("models", JSONArray(models)).put("favorites", JSONArray(favorites.toList())).put("allowInsecureLan", allowInsecureLan)
}

data class TaskOptions(
    val profileId: String = "", val client: String = "codex", val model: String = "",
    val workspace: String = "~/projects", val mode: String = "new",
    val executionId: String = "local",
    val codexCompatibility: Boolean = false,
)

class ProfileStore(context: Context) {
    private val prefs = context.getSharedPreferences("workbench", Context.MODE_PRIVATE)
    private val secrets = context.getSharedPreferences("credentials", Context.MODE_PRIVATE)
    private val alias = "agent-workbench-aes-v1"

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true).build())
        }.generateKey()
    }

    @Synchronized fun saveSecret(id: String, value: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        cipher.updateAAD(id.toByteArray())
        val encrypted = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val data = JSONObject().put("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .put("data", Base64.encodeToString(encrypted, Base64.NO_WRAP)).toString()
        check(secrets.edit().putString(id, data).commit()) { "无法保存加密密钥" }
    }

    @Synchronized fun secret(id: String): String {
        val raw = secrets.getString(id, null) ?: error("请先填写调用 Key")
        try {
            val data = JSONObject(raw)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(data.getString("iv"), Base64.NO_WRAP)))
            cipher.updateAAD(id.toByteArray())
            return String(cipher.doFinal(Base64.decode(data.getString("data"), Base64.NO_WRAP)), Charsets.UTF_8)
        } catch (_: Exception) { error("密钥无法解密，请重新填写；原配置仍保留") }
    }

    fun profiles(): List<KeyProfile> = try {
        val values = JSONArray(prefs.getString("profiles", "[]"))
        (0 until values.length()).map { index ->
            val row = values.getJSONObject(index)
            KeyProfile(row.getString("id"), row.getString("name"), row.getString("gateway"),
                row.optJSONArray("models").strings(), row.optJSONArray("favorites").strings().toSet(), row.optBoolean("allowInsecureLan", false))
        }
    } catch (_: Exception) { emptyList() }

    fun saveProfiles(profiles: List<KeyProfile>) {
        check(prefs.edit().putString("profiles", JSONArray(profiles.map { it.json() }).toString()).commit()) { "无法保存调用配置" }
    }

    fun options(): TaskOptions {
        val data = try { JSONObject(prefs.getString("options", "{}") ?: "{}") } catch (_: Exception) { JSONObject() }
        return TaskOptions(data.optString("profile"), data.optString("client", "codex"), data.optString("model"),
            data.optString("workspace", "~/projects"), data.optString("mode", "new"), data.optString("execution", "local"),
            data.optBoolean("codex_compatibility", false))
    }

    fun saveOptions(options: TaskOptions) {
        prefs.edit().putString("options", JSONObject().put("profile", options.profileId).put("client", options.client)
            .put("model", options.model).put("workspace", options.workspace).put("mode", options.mode)
            .put("execution", options.executionId).put("codex_compatibility", options.codexCompatibility).toString()).apply()
    }

    fun sshTargets(): List<SshTarget> = try {
        val rows = JSONArray(prefs.getString("ssh_targets", "[]"))
        (0 until rows.length()).mapNotNull { index -> runCatching {
            val row = rows.getJSONObject(index)
            SshTargetPolicy.validate(row.getString("name"), row.getString("host"), row.optString("user"), row.optInt("port", 22).toString())
                .copy(id = row.getString("id"))
        }.getOrNull() }
    } catch (_: Exception) { emptyList() }

    fun saveSshTargets(targets: List<SshTarget>) {
        val rows = JSONArray(targets.map { JSONObject().put("id", it.id).put("name", it.name)
            .put("host", it.host).put("user", it.user).put("port", it.port) })
        check(prefs.edit().putString("ssh_targets", rows.toString()).commit()) { "无法保存电脑配置" }
    }

    fun removeSecret(id: String) { secrets.edit().remove(id).apply() }
    fun bridgePort() = prefs.getInt("bridge_port", 0)
    fun saveBridgePort(port: Int) { prefs.edit().putInt("bridge_port", port).apply() }
    fun bootstrapOperation() = prefs.getString("bootstrap_operation", "").orEmpty()
    fun saveBootstrapOperation(id: String) { prefs.edit().putString("bootstrap_operation", id).remove("bootstrap_error").apply() }
    fun bootstrapError() = prefs.getString("bootstrap_error", "").orEmpty()
    fun saveBootstrapError(text: String) { prefs.edit().putString("bootstrap_error", text.take(1000)).apply() }
    fun sessionRows(): JSONArray = try { JSONArray(prefs.getString("sessions", "[]")) } catch (_: Exception) { JSONArray() }
    fun saveSessions(rows: JSONArray) { prefs.edit().putString("sessions", rows.toString()).apply() }
}

fun JSONArray?.strings(): List<String> = if (this == null) emptyList() else (0 until length()).mapNotNull {
    optString(it).takeIf(String::isNotBlank)
}
