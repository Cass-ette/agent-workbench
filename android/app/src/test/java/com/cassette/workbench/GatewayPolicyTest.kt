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
        listOf("https://key@example.com", "https://example.com?key=x", "file:///tmp/x").forEach {
            assertThrows(IllegalArgumentException::class.java) { GatewayPolicy.normalize(it) }
        }
        assertThrows(IllegalArgumentException::class.java) { GatewayPolicy.normalize("http://192.168.1.2:8081") }
        assertEquals("http://192.168.1.2:8081", GatewayPolicy.normalize("http://192.168.1.2:8081", true))
    }
    @Test fun preservesGenericApiPrefixesWithoutDuplicatingV1() {
        for (path in listOf("/api/paas/v4", "/proxy/v1", "/anthropic", "/v2%2Ftenant/v1")) {
            val base = "https://gateway.test$path"
            assertEquals(base, GatewayPolicy.normalize("$base/"))
            assertEquals(base, GatewayPolicy.apiBase(base))
        }
        assertEquals("https://gateway.test/v1", GatewayPolicy.apiBase("https://gateway.test"))
        assertEquals(listOf("https://gateway.test/proxy/v1/models"), GatewayPolicy.modelUrls("https://gateway.test/proxy/v1"))
        assertEquals(listOf("https://gateway.test/custom/models", "https://gateway.test/custom/v1/models"), GatewayPolicy.modelUrls("https://gateway.test/custom"))
        for (url in listOf("https://gateway.test:0/api", "https://gateway.test:99999/api", "https://gateway.test/api#fragment")) {
            assertThrows(IllegalArgumentException::class.java) { GatewayPolicy.normalize(url) }
        }
    }



}
