package com.cassette.workbench

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object GatewayClient {
    fun json(url: String, key: String, method: String = "GET", body: JSONObject? = null, timeout: Int = 12000, bridge: Boolean = false): JSONObject {
        val destination = URL(url)
        val connection = (if (bridge) destination.openConnection(java.net.Proxy.NO_PROXY) else destination.openConnection()) as HttpURLConnection
        connection.instanceFollowRedirects = false
        connection.requestMethod = method
        connection.connectTimeout = timeout
        connection.readTimeout = timeout
        // CCHP/AutoBits Anthropic-compatible gateways authenticate downstream
        // keys with x-api-key. The local Termux bridge keeps its own Bearer
        // token; this header is only for the configured model gateway.
        connection.setRequestProperty("Authorization", "Bearer $key")
        if (!bridge) {
            connection.setRequestProperty("x-api-key", key)
            connection.setRequestProperty("anthropic-version", "2023-06-01")
        }
        connection.setRequestProperty("Accept", "application/json")
        connection.setRequestProperty("User-Agent", "AgentWorkbench-Android/${BuildConfig.VERSION_NAME}")
        try {
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            if (status !in 200..299) {
                // Never display arbitrary upstream error bodies: they can echo credentials.
                val localConflict = if (bridge && status == 409) runCatching {
                    connection.errorStream.use { input ->
                        JSONObject(String(input.readBytesBounded(4096), Charsets.UTF_8)).optString("error").take(180)
                    }
                }.getOrNull()?.takeIf(String::isNotBlank) else null
                error(localConflict ?: when (status) {
                    401, 403 -> "凭据被拒绝（HTTP $status）"
                    404 -> "接口不存在（HTTP 404）"
                    in 300..399 -> "服务要求重定向，已阻止转发调用 Key"
                    else -> "请求失败（HTTP $status）"
                })
            }
            val bytes = connection.inputStream.use { it.readBytesBounded(2 * 1024 * 1024) }
            return JSONObject(String(bytes, Charsets.UTF_8))
        } finally { connection.disconnect() }
    }

    fun models(gateway: String, key: String): List<String> {
        val data = json("$gateway/v1/models?limit=1000", key)
        val rows = data.optJSONArray("data") ?: data.optJSONArray("models") ?: error("网关没有返回模型列表")
        return (0 until rows.length()).mapNotNull { index ->
            rows.optJSONObject(index)?.optString("id")?.takeIf(String::isNotBlank)
        }.distinct().sorted()
    }
}

private fun java.io.InputStream.readBytesBounded(limit: Int): ByteArray {
    val output = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    while (true) {
        val count = read(buffer)
        if (count < 0) break
        require(output.size() + count <= limit) { "服务返回的数据过大" }
        output.write(buffer, 0, count)
    }
    return output.toByteArray()
}
