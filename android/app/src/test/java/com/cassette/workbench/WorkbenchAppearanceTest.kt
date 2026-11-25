package com.cassette.workbench

import org.junit.Assert.*
import org.junit.Test

class WorkbenchAppearanceTest {
    @Test fun explicitChoiceOverridesSystemAndUnknownSettingsFollowSystem() {
        assertEquals(WorkbenchPalette.light, AppearanceMode.LIGHT.palette(true))
        assertEquals(WorkbenchPalette.dark, AppearanceMode.DARK.palette(false))
        assertEquals(WorkbenchPalette.light, AppearanceMode.SYSTEM.palette(false))
        assertEquals(WorkbenchPalette.dark, AppearanceMode.SYSTEM.palette(true))
        assertEquals(AppearanceMode.SYSTEM, AppearanceMode.parse("corrupt"))
        assertEquals(16, WorkbenchPalette.dark.ansi.size)
    }
}
