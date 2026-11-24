package com.cassette.workbench

import java.util.UUID
import java.util.Base64
import org.json.JSONObject

data class SshTarget(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val host: String,
    val user: String = "",
    val port: Int = 22,
    val platform: String = "",
    val hostKey: String = "",
    val remember: Boolean = false,
) {
    val destination get() = (if (user.isBlank()) "" else "$user@") + host + ":$port"
}

object SshTargetPolicy {
    private const val CODE_PREFIX = "workbench-ssh-v1:"
    private val hostKeyPattern = Regex("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}")

    // A verified computer/account keeps its pairing when its Wi-Fi address or
    // forwarded port changes. An IP alone must never establish this identity.
    fun sameComputer(first: SshTarget, second: SshTarget): Boolean =
        first.platform.isNotEmpty() && first.platform == second.platform &&
            first.user == second.user && first.user.isNotBlank() &&
            hostKeyPattern.matches(first.hostKey) && first.hostKey == second.hostKey

    fun connectionCode(input: String, remember: Boolean): SshTarget {
        val code = input.trim()
        require(code.length <= 2048 && code.startsWith(CODE_PREFIX)) { "请粘贴电脑工作台生成的连接码" }
        val data = runCatching {
            JSONObject(String(Base64.getDecoder().decode(code.removePrefix(CODE_PREFIX)), Charsets.UTF_8))
        }.getOrElse { throw IllegalArgumentException("连接码格式无效") }
        return fromConnectionInfo(data, remember).also { require(it.platform.isNotEmpty()) { "连接码缺少电脑信息" } }
    }

    fun fromConnectionInfo(data: JSONObject, remember: Boolean = false): SshTarget {
        val target = validate(data.getString("name"), data.getString("host"), data.optString("user"), data.optInt("port", 22).toString())
        val platform = data.optString("platform")
        if (platform.isEmpty()) return target
        val hostKey = data.optString("host_key")
        require(platform in listOf("macos", "windows") && hostKeyPattern.matches(hostKey) && target.user.isNotBlank()) {
            "连接码缺少电脑账号或主机密钥"
        }
        return target.copy(platform = platform, hostKey = hostKey, remember = data.optBoolean("remember") && remember)
    }

    fun validate(name: String, host: String, user: String, port: String): SshTarget {
        val cleanHost = host.trim().removeSurrounding("[", "]")
        val cleanUser = user.trim()
        require(name.trim().isNotEmpty() && name.trim().length <= 80) { "请填写电脑名称（最多 80 字）" }
        require(cleanHost.length in 1..253 && (
            cleanHost.matches(Regex("[A-Za-z0-9][A-Za-z0-9._-]*")) ||
            (cleanHost.contains(':') && cleanHost.matches(Regex("[0-9A-Fa-f:]+")))
        )) { "主机填写 IP、域名或 Termux SSH 配置中的别名，不包含 ssh:// 或用户名" }
        require(cleanUser.length <= 80 && (cleanUser.isEmpty() || cleanUser.matches(Regex("[\\p{L}\\p{N}_][\\p{L}\\p{N}_.@\\\\-]*")))) { "SSH 用户名无效" }
        val number = port.trim().toIntOrNull()
        require(number != null && number in 1..65535) { "SSH 端口应为 1–65535" }
        return SshTarget(name = name.trim(), host = cleanHost, user = cleanUser, port = number)
    }
}
