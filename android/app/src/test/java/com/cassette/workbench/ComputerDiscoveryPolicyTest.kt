package com.cassette.workbench

import org.junit.Assert.*
import org.junit.Test

class ComputerDiscoveryPolicyTest {
    private val key = "ssh-ed25519 " + java.util.Base64.getEncoder().encodeToString(ByteArray(51) { it.toByte() })
    private val target = SshTarget(name = "Mac", host = "192.168.1.2", user = "tester", platform = "macos", hostKey = key)

    @Test fun samePinnedIdentityWorksAcrossNetworksWithoutTrustingNameOrAddress() {
        val fingerprint = ComputerDiscoveryPolicy.fingerprint(key)!!
        val attributes = mapOf("v" to "1", "key" to fingerprint, "platform" to "macos")
        val name = "wb-" + fingerprint.take(20)
        assertTrue(ComputerDiscoveryPolicy.matches(target, name, attributes))
        assertTrue(ComputerDiscoveryPolicy.matches(target.copy(host = "10.0.0.8"), name, attributes))
        assertFalse(ComputerDiscoveryPolicy.matches(target, name, attributes + ("key" to "0".repeat(64))))
        assertFalse(ComputerDiscoveryPolicy.matches(target, name, attributes + ("platform" to "windows")))
        assertFalse(ComputerDiscoveryPolicy.matches(target.copy(hostKey = ""), name, attributes))
        assertEquals(fingerprint, ComputerDiscoveryPolicy.fingerprint("$key comment"))
    }

    @Test fun onlyLanAddressCandidatesAreAccepted() {
        listOf("192.168.8.65", "10.0.0.1", "172.31.255.254", "fd12::1").forEach {
            assertTrue(it, ComputerDiscoveryPolicy.isLanAddress(it))
        }
        listOf("127.0.0.1", "0.0.0.0", "169.254.1.2", "172.32.1.2", "8.8.8.8", "100.64.0.1", "::1", "fe80::1", "example.com").forEach {
            assertFalse(it, ComputerDiscoveryPolicy.isLanAddress(it))
        }
    }
}
