package com.cassette.workbench

import java.util.UUID

data class SshTarget(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val host: String,
    val user: String = "",
    val port: Int = 22,
) {
    val destination get() = (if (user.isBlank()) "" else "$user@") + host + ":$port"
}

object SshTargetPolicy {
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
