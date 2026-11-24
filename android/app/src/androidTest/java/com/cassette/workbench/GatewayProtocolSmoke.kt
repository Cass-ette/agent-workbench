package com.cassette.workbench

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.ServerSocket
import java.nio.charset.StandardCharsets
import java.util.concurrent.atomic.AtomicReference

/** Exercises an x-api-key -> Bearer-only metadata retry against a local fake gateway. */
class GatewayProtocolSmoke(private val runner: Instrumentation) {
    fun run() {
        val result = Bundle()
        val server = ServerSocket(0, 2, InetAddress.getByName("127.0.0.1"))
        server.soTimeout = 5000
        val serverFailure = AtomicReference<Throwable?>()
        val thread = Thread {
            try {
                repeat(2) { index ->
                    server.accept().use { socket ->
                        socket.soTimeout = 5000
                        val input = BufferedReader(InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII))
                        val requestLine = input.readLine()
                        val headers = mutableMapOf<String, String>()
                        while (true) {
                            val line = input.readLine() ?: error("Request headers ended unexpectedly")
                            if (line.isEmpty()) break
                            val split = line.indexOf(':')
                            if (split > 0) headers[line.substring(0, split).lowercase()] = line.substring(split + 1).trim()
                        }
                        check(requestLine?.startsWith("GET /proxy/v1/models") == true) { "Wrong gateway API prefix" }
                        check(headers["authorization"] == "Bearer fixture-key") { "Bearer key is missing" }
                        val body: String
                        val status: String
                        if (index == 0) {
                            check(requestLine.contains("?limit=1000")) { "First request should be the normal catalog request" }
                            check(headers["x-api-key"] == "fixture-key") { "First request should try x-api-key" }
                            status = "401 Unauthorized"; body = "{}"
                        } else {
                            check(!requestLine.contains("?")) { "Bearer retry must omit the catalog query" }
                            check(headers["x-api-key"] == null) { "Bearer retry should not send the rejected x-api-key header" }
                            status = "200 OK"; body = "{\"data\":[{\"id\":\"fixture-model\"}]}"
                        }
                        val output = BufferedWriter(OutputStreamWriter(socket.getOutputStream(), StandardCharsets.US_ASCII))
                        output.write("HTTP/1.1 $status\r\nContent-Type: application/json\r\nContent-Length: ${body.toByteArray().size}\r\nConnection: close\r\n\r\n$body")
                        output.flush()
                    }
                }
            } catch (cause: Throwable) { serverFailure.set(cause) }
        }
        try {
            thread.start()
            val models = GatewayClient.models("http://127.0.0.1:${server.localPort}/proxy/v1", "fixture-key")
            thread.join(6000)
            serverFailure.get()?.let { throw it }
            check(!thread.isAlive) { "Fake gateway did not receive both requests" }
            check(models == listOf("fixture-model")) { "Unexpected model list" }
            result.putString("stream", "PASS: generic API prefix and Bearer-only model catalog; no inference sent\n")
            runner.finish(Activity.RESULT_OK, result)
        } catch (cause: Throwable) {
            result.putString("error_type", cause.javaClass.simpleName)
            result.putString("error", cause.message.orEmpty())
            result.putString("stream", "FAIL: generic API prefix / Bearer retry fixture\n")
            runner.finish(Activity.RESULT_CANCELED, result)
        } finally {
            runCatching { server.close() }
            runCatching { thread.join(1000) }
        }
    }
}
