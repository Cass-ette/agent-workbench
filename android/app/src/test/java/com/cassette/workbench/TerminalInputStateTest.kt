package com.cassette.workbench

import org.junit.Assert.*
import org.junit.Test

class TerminalInputStateTest {
    @Test fun oldComputerCannotBeMistakenForDesktopTakeover() {
        val state = TerminalInputState().snapshot(false, "")
        assertEquals(RemoteControlState.ReadOnly, state.control)
        assertFalse(state.canWrite)
        assertEquals(RemoteControlState.Desktop, state.snapshot(false, "desktop").control)
        assertEquals(RemoteControlState.Expired, state.snapshot(false, "expired").control)
        assertEquals(RemoteControlState.OtherDevice, state.snapshot(false, "other_device").control)
    }

    @Test fun lateReadOnlyResponseCannotUndoExplicitReclaim() {
        val before = TerminalInputState().snapshot(false, "expired")
        val reclaimed = before.claimed()
        assertEquals(reclaimed, reclaimed.snapshot(false, "desktop", before.epoch))
        assertEquals(reclaimed, reclaimed.failed("当前为只读查看；请重新点接续并控制", before.epoch))
        assertTrue(reclaimed.canWrite)
    }

    @Test fun onlyActualInputFailureIsAnErrorAndItStaysWithItsSession() {
        val first = TerminalInputState().failed("无法连接服务")
        val other = TerminalInputState()
        assertTrue(first.error.contains("未送达"))
        assertTrue(other.error.isEmpty())
        val readOnly = first.failed("当前为只读查看；请重新点接续并控制")
        assertFalse(readOnly.canWrite)
        assertTrue(readOnly.error.isEmpty())
        assertTrue(first.snapshot(false, "expired").snapshot(true, "controlled").error.isEmpty())
    }

    @Test fun imeEnterUsesCarriageReturnAndMultilinePasteIsPreserved() {
        assertEquals("\r", terminalImeCommit("\n"))
        assertEquals("\r", terminalImeCommit("\r\n"))
        assertEquals("\r", terminalImeCommit("\r"))
        assertEquals("first\nsecond\n", terminalImeCommit("first\nsecond\n"))
    }
}
