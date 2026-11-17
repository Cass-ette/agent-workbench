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
        require(uri.path in listOf("", "/", "/v1", "/v1/")) { "只填写网关根地址或 /v1" }
        val local = uri.host.lowercase() in setOf("localhost", "127.0.0.1", "[::1]", "::1")
        val privateAddress = runCatching { InetAddress.getByName(uri.host).let { it.isSiteLocalAddress || it.isLinkLocalAddress } }.getOrDefault(false)
        require(uri.scheme == "https" || local || (allowEmulator && privateAddress)) { "远程网关必须使用 HTTPS；可信局域网 HTTP 需显式允许" }
        require(uri.port == -1 || uri.port in 1..65535) { "端口无效" }
        return URI(uri.scheme, null, uri.host, uri.port, null, null, null).toASCIIString().trimEnd('/')
    }
}
