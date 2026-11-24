package com.cassette.workbench

import org.junit.Assert.*
import org.junit.Test

class ReasoningEffortTest {
    @Test fun nativeCommandsKeepModelAndDoNotSubmit() {
        ReasoningEffort.levels.drop(1).forEach { level ->
            assertEquals("/model --reasoning=$level", ReasoningEffort.command("codex", level))
            assertEquals("/effort $level", ReasoningEffort.command("claude", level))
            assertFalse(ReasoningEffort.command("codex", level)!!.any(Char::isISOControl))
        }
        assertNull(ReasoningEffort.command("codex", "high\n/model other"))
        assertNull(ReasoningEffort.command("ssh", "high"))
        assertEquals("/effort", ReasoningEffort.command("claude", "default"))
    }
    @Test fun preferencesDoNotLeakBetweenClientsProfilesOrModels() {
        val key = ReasoningEffort.key("codex", "p", "glm")
        assertNotEquals(key, ReasoningEffort.key("claude", "p", "glm"))
        assertNotEquals(key, ReasoningEffort.key("codex", "q", "glm"))
        assertNotEquals(key, ReasoningEffort.key("codex", "p", "other"))
        assertNotEquals(ReasoningEffort.key("codex", "a|b", "c"), ReasoningEffort.key("codex", "a", "b|c"))
    }
}
