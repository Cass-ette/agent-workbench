package com.cassette.workbench

import org.junit.Assert.*
import org.junit.Test

class GatewayPolicyTest {
    @Test fun normalizesLocalAndRemoteRoots() {
        assertEquals("http://127.0.0.1:8081", GatewayPolicy.normalize(" http://127.0.0.1:8081/v1/ "))
        assertEquals("https://gateway.example.com", GatewayPolicy.normalize("https://gateway.example.com/v1"))
        assertEquals("http://[::1]:8081", GatewayPolicy.normalize("http://[::1]:8081"))
    }
    @Test fun refusesCredentialsAndRemoteCleartext() {
        listOf("https://key@example.com", "https://example.com?key=x", "https://example.com/api", "file:///tmp/x").forEach {
            assertThrows(IllegalArgumentException::class.java) { GatewayPolicy.normalize(it) }
        }
        assertThrows(IllegalArgumentException::class.java) { GatewayPolicy.normalize("http://192.168.1.2:8081") }
        assertEquals("http://192.168.1.2:8081", GatewayPolicy.normalize("http://192.168.1.2:8081", true))
    }
}
