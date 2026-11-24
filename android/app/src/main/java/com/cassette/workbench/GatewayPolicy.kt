package com.cassette.workbench

import java.net.InetAddress
import java.net.URI

object GatewayPolicy {
    fun normalize(input: String, allowEmulator: Boolean = false): String {
        val uri = try { URI(input.trim()) } catch (_: Exception) {
            throw IllegalArgumentException("请填写完整的网关地址")
        }
        require(uri.scheme in setOf("http", "https") && !uri.host.isNullOrBlank()) { "网关必须是 HTTP(S) 地址" }
        require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null) { "地址不能包含凭据或查询参数" }
        require(input.trim().none { it.isWhitespace() || it.isISOControl() } && !input.contains('\\')) { "网关地址格式无效" }
        val local = uri.host.lowercase() in setOf("localhost", "127.0.0.1", "[::1]", "::1")
        val privateAddress = runCatching { InetAddress.getByName(uri.host).let { it.isSiteLocalAddress || it.isLinkLocalAddress } }.getOrDefault(false)
        require(uri.scheme == "https" || local || (allowEmulator && privateAddress)) { "远程网关必须使用 HTTPS；可信局域网 HTTP 需显式允许" }
        require(uri.port == -1 || uri.port in 1..65535) { "端口无效" }
        val path = uri.rawPath.orEmpty().trimEnd('/')
        // Preserve the legacy identity for a bare /v1 address.
        val base = uri.toASCIIString().trimEnd('/')
        return if (path == "/v1") base.removeSuffix("/v1") else base
    }
    fun apiBase(gateway: String): String {
        val base = gateway.trimEnd('/')
        return if (URI(base).path.isNullOrEmpty()) "$base/v1" else base
    }

    fun modelUrls(gateway: String): List<String> {
        val base = gateway.trimEnd('/')
        return listOf(apiBase(base) + "/models") +
            if (!URI(base).path.isNullOrEmpty() && !base.endsWith("/v1")) listOf("$base/v1/models") else emptyList()
    }
}
