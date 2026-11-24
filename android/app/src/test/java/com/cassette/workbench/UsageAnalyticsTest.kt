package com.cassette.workbench

import org.junit.Assert.*
import org.junit.Test

class UsageAnalyticsTest {
    @Test fun projectFiltersUsePathBoundariesAndExplicitChildren() {
        assertTrue(usageProjectMatches("/a/project", "/a/project/"))
        assertFalse(usageProjectMatches("/b/project", "/a/project"))
        assertFalse(usageProjectMatches("/a/project/sub", "/a/project"))
        assertTrue(usageProjectMatches("/a/project/sub", "/a/project", true))
        assertFalse(usageProjectMatches("/a/project-other", "/a/project", true))
        assertTrue(usageProjectMatches("", "__unknown"))
        assertFalse(usageProjectMatches("", "/", true))
    }

    @Test fun projectProfileModelAndDateFiltersComposeWithoutReassigningMissingValues() {
        val rows = listOf(
            UsageDay("2026-11-21", 100, 10, null, null, null, null, 1, "first"),
            UsageDay("2026-11-22", 200, 20, 0, 0, null, 0.0, 0, "last"),
            UsageDay("", 3, 0, null, null, null, null, 1, "first"),
        )
        val sessions = listOf(
            UsageConversation("s", "claude", "last", "/a/project", rows, "p", "配置"),
            UsageConversation("other", "codex", "first", "/b/project", rows, "other"),
        )
        val filtered = filterUsageDays(sessions, "", "2026-11-21", "2026-11-21", "/a/project", "p", "claude", "first")
        assertEquals(1, filtered.size)
        assertEquals(100L, filtered.single().input)
        assertNull(filtered.single().cost)
        val zero = filterUsageDays(sessions, "s", "2026-11-22", "2026-11-22")
        assertEquals(0.0, zero.single().cost!!, 0.0)
        assertEquals(3, filterUsageDays(sessions, "s", "", "").size)
    }

    @Test fun canonicalCallsKeepTheirProjectProfileAndUnknownModel() {
        val session = UsageConversation("s", "codex", "last", "/last", listOf(
            UsageDay("2026-11-21", 100, 10, null, null, null, 0.0, model = "first", projectPath = "/a", profileId = "a"),
            UsageDay("2026-11-22", 200, 20, null, null, null, null, model = "", projectPath = "", profileId = ""),
        ), "last-profile")
        assertEquals(100L, filterUsageDays(listOf(session), "s", "", "", "/a", "a", "codex", "first").single().input)
        assertEquals(200L, filterUsageDays(listOf(session), "s", "", "", "__unknown", "__unknown").single().input)
        assertTrue(filterUsageDays(listOf(session), "s", "", "", model = "last").isEmpty())
    }
}
