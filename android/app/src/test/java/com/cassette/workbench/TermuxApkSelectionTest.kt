package com.cassette.workbench

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TermuxApkSelectionTest {
    @Test fun selectsTheHighestPrioritySupportedDeviceAbi() {
        val url = termuxApkUrl(arrayOf("unknown", "arm64-v8a", "armeabi-v7a"))

        assertEquals(
            "https://github.com/termux/termux-app/releases/download/v0.118.3/termux-app_v0.118.3%2Bgithub-debug_arm64-v8a.apk",
            url,
        )
    }

    @Test fun supportsAllPublishedAndroidAbis() {
        mapOf(
            "arm64-v8a" to "arm64-v8a",
            "armeabi-v7a" to "armeabi-v7a",
            "x86_64" to "x86_64",
            "x86" to "x86",
        ).forEach { (abi, asset) ->
            assertEquals(asset, termuxApkUrl(arrayOf(abi))?.substringAfter("github-debug_")?.removeSuffix(".apk"))
        }
    }

    @Test fun unknownAbiFallsBackToOfficialReleasePage() {
        assertNull(termuxApkUrl(arrayOf("riscv64")))
    }
}
