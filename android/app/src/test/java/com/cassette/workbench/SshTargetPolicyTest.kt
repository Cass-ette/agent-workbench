package com.cassette.workbench

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class SshTargetPolicyTest {
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
