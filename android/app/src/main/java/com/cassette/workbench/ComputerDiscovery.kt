package com.cassette.workbench

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.Handler
import android.os.Looper
import java.net.InetAddress
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

internal data class ComputerAddress(val host: String, val port: Int)

internal object ComputerDiscoveryPolicy {
    const val serviceType = "_agent-wb._tcp."

    fun fingerprint(publicKey: String): String? {
        val parts = publicKey.trim().split(Regex("\\s+"))
        if (parts.size < 2 || parts[0] != "ssh-ed25519") return null
        val blob = runCatching { java.util.Base64.getDecoder().decode(parts[1]) }.getOrNull() ?: return null
        if (blob.size != 51) return null
        return MessageDigest.getInstance("SHA-256").digest(parts.take(2).joinToString(" ").toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    fun matches(target: SshTarget, name: String, attributes: Map<String, String>): Boolean {
        val key = fingerprint(target.hostKey) ?: return false
        return target.platform in listOf("macos", "windows") && target.user.isNotBlank() &&
            name.startsWith("wb-" + key.take(20)) && attributes["v"] == "1" &&
            attributes["key"] == key && attributes["platform"] == target.platform
    }

    fun isLanAddress(host: String): Boolean {
        if (!host.matches(Regex("[0-9A-Fa-f:.]+"))) return false
        val bytes = runCatching { InetAddress.getByName(host).address }.getOrNull() ?: return false
        val first = bytes[0].toInt() and 255
        return if (bytes.size == 4) first == 10 ||
            (first == 172 && (bytes[1].toInt() and 255) in 16..31) ||
            (first == 192 && (bytes[1].toInt() and 255) == 168)
        else bytes.size == 16 && first and 254 == 252
    }
}

/** A bounded foreground search through Android's system DNS-SD implementation. */
internal class ComputerDiscovery(context: Context) {
    private val nsd = context.getSystemService(NsdManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var cancelSearch: (() -> Unit)? = null

    fun cancel() { cancelSearch?.invoke() }

    @Suppress("DEPRECATION")
    fun find(target: SshTarget, timeoutMillis: Long = 6000): List<ComputerAddress> {
        val key = ComputerDiscoveryPolicy.fingerprint(target.hostKey) ?: return emptyList()
        val manager = nsd ?: return emptyList()
        val active = AtomicBoolean(true)
        val done = CountDownLatch(1)
        val found = CopyOnWriteArrayList<ComputerAddress>()
        val queue = ArrayDeque<NsdServiceInfo>()
        var resolving: NsdManager.ResolveListener? = null
        var started = false
        fun resolveNext() {
            if (!active.get() || resolving != null || queue.isEmpty()) return
            val info = queue.removeFirst()
            val resolver = object : NsdManager.ResolveListener {
                override fun onResolveFailed(service: NsdServiceInfo, error: Int) { main.post { resolving = null; resolveNext() } }
                override fun onServiceResolved(service: NsdServiceInfo) { main.post {
                    if (!active.get()) return@post
                    val attrs = service.attributes.mapNotNull { (key, value) -> value?.let { key to String(it, Charsets.UTF_8) } }.toMap()
                    if (ComputerDiscoveryPolicy.matches(target, service.serviceName, attrs) && service.port in 1..65535) {
                        val addresses = if (Build.VERSION.SDK_INT >= 34) service.hostAddresses else listOfNotNull(service.host)
                        addresses.mapNotNull { it.hostAddress }.filter(ComputerDiscoveryPolicy::isLanAddress)
                            .sortedBy { it.contains(':') }.take(4).forEach { found.addIfAbsent(ComputerAddress(it, service.port)) }
                        if (found.isNotEmpty()) done.countDown()
                    }
                    resolving = null
                    resolveNext()
                } }
            }
            resolving = resolver
            runCatching { manager.resolveService(info, resolver) }.onFailure { resolving = null; resolveNext() }
        }
        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(type: String) { main.post {
                started = true
                if (!active.get()) runCatching { manager.stopServiceDiscovery(this) }
            } }
            override fun onServiceFound(info: NsdServiceInfo) { main.post {
                if (active.get() && info.serviceName.startsWith("wb-" + key.take(20)) && queue.size < 4) {
                    queue.addLast(info); resolveNext()
                }
            } }
            override fun onServiceLost(info: NsdServiceInfo) = Unit
            override fun onDiscoveryStopped(type: String) = Unit
            override fun onStartDiscoveryFailed(type: String, error: Int) { done.countDown() }
            override fun onStopDiscoveryFailed(type: String, error: Int) = Unit
        }
        val cancel = {
            active.set(false)
            done.countDown()
            main.post {
                if (started) runCatching { manager.stopServiceDiscovery(listener) }
                if (Build.VERSION.SDK_INT >= 34) resolving?.let { runCatching { manager.stopServiceResolution(it) } }
            }
            Unit
        }
        cancelSearch = cancel
        main.post {
            if (active.get()) runCatching { manager.discoverServices(ComputerDiscoveryPolicy.serviceType, NsdManager.PROTOCOL_DNS_SD, listener) }
                .onFailure { done.countDown() }
        }
        try {
            done.await(timeoutMillis, TimeUnit.MILLISECONDS)
            return if (active.get()) found.toList() else emptyList()
        } finally { cancel(); if (cancelSearch === cancel) cancelSearch = null }
    }
}
