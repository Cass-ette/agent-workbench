package com.cassette.workbench

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

class SessionOrderTest {
    @Test fun draggedTabMovesBeforeOrAfterItsTarget() {
        val ids = listOf("a", "b", "c")
        assertEquals(listOf("b", "c", "a"), reorderSessionIds(ids, "a", "c", true))
        assertEquals(listOf("c", "a", "b"), reorderSessionIds(ids, "c", "a", false))
        assertEquals(listOf("a", "b", "c"), ids)
    }

    @Test fun invalidAndSameTabDropsDoNotChangeOrder() {
        val ids = listOf("a", "b")
        assertSame(ids, reorderSessionIds(ids, "missing", "b", false))
        assertSame(ids, reorderSessionIds(ids, "a", "missing", false))
        assertSame(ids, reorderSessionIds(ids, "a", "a", true))
    }
}
