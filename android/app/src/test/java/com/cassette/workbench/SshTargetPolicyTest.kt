package com.cassette.workbench

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test

class SshTargetPolicyTest {
    @Test fun changedNetworkKeepsPairingOnlyForTheVerifiedComputerAndAccount() {
        val old = SshTarget(name = "Mac", host = "192.168.8.65", user = "tester", platform = "macos",
            hostKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI" + "A".repeat(43))
        assertTrue(SshTargetPolicy.sameComputer(old, old.copy(host = "100.64.1.2", port = 2222)))
        assertFalse(SshTargetPolicy.sameComputer(old, old.copy(user = "other")))
        assertFalse(SshTargetPolicy.sameComputer(old, old.copy(hostKey = old.hostKey.dropLast(1) + "B")))
        assertFalse(SshTargetPolicy.sameComputer(old.copy(platform = "", hostKey = ""), old.copy(platform = "", hostKey = "")))
    }
    @Test fun acceptsIpAndSshConfigAlias() {
        assertEquals("ASUS@100.105.10.85:22", SshTargetPolicy.validate("Windows", "100.105.10.85", "ASUS", "22").destination)
        assertEquals("laptop:22", SshTargetPolicy.validate("Windows", "laptop", "", "22").destination)
        assertEquals("user@2001:db8::4:2222", SshTargetPolicy.validate("Server", "2001:db8::4", "user", "2222").destination)
    }

    @Test fun rejectsOptionsAndShellMetacharacters() {
        assertThrows(IllegalArgumentException::class.java) { SshTargetPolicy.validate("Windows", "-oProxyCommand=evil", "", "22") }
        assertThrows(IllegalArgumentException::class.java) { SshTargetPolicy.validate("Windows", "laptop", "user;id", "22") }
        assertThrows(IllegalArgumentException::class.java) { SshTargetPolicy.validate("Windows", "laptop", "", "65536") }
    }
}
