package com.cassette.workbench

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class WorkbenchUpdatePackageTest {
    private fun pkg(index: Int, channel: String, arch: String, version: String) =
        AndroidUpdateCandidate(index, "android", channel, arch, version, if (channel == "preview") "development" else "release")

    @Test fun selectsThePreferredAbiAndRequiresCompatibleSigning() {
        val packages = listOf(
            pkg(0, "stable", "universal", "1.0.0"),
            pkg(1, "preview", "arm64-v8a", "2.0.0"),
            pkg(2, "stable", "arm64-v8a", "1.1.0"),
        )

        assertEquals("1.1.0", selectAndroidUpdatePackage(
            packages, listOf("x86_64", "arm64-v8a"), "stable", "release",
        )?.version)
        assertEquals("2.0.0", selectAndroidUpdatePackage(
            packages, listOf("arm64-v8a"), "preview", "development",
        )?.version)
        assertNull(selectAndroidUpdatePackage(packages, listOf("x86_64"), "preview", "development"))
    }

    @Test fun releaseSignedPreviewDoesNotMatchDevelopmentClient() {
        val packages = listOf(AndroidUpdateCandidate(0, "android", "preview", "universal", "2.0.0", "release"))
        assertNull(selectAndroidUpdatePackage(packages, listOf("arm64-v8a"), "preview", "development"))
        assertEquals("2.0.0", selectAndroidUpdatePackage(packages, listOf("arm64-v8a"), "preview", "release")?.version)
    }

    @Test fun selectsPublishedPreviewAndRejectsUntrustedOrDraftManifests() {
        val url = "https://github.com/Cass-ette/claude-cchp-manager/releases/download/test/update.json"
        val stable = WorkbenchRelease(false, false, "2026-01-01", listOf(WorkbenchReleaseAsset("update.json", url)))
        val preview = stable.copy(prerelease = true, publishedAt = "2026-02-01")
        assertEquals("preview", selectReleaseManifest(listOf(stable, preview))?.channel)
        assertEquals("stable", selectReleaseManifest(listOf(stable, preview.copy(draft = true)))?.channel)
        for (bad in listOf(url.replace("github.com", "example.com"), url + "?extra=1", url.replace("Cass-ette/", "other/"))) {
            assertNull(selectReleaseManifest(listOf(preview.copy(assets = listOf(WorkbenchReleaseAsset("update.json", bad))))))
        }
        assertNull(selectReleaseManifest(listOf(preview.copy(assets = emptyList()))))
    }

    @Test fun rejectsDuplicatePackagesForThePreferredArchitecture() {
        val packages = listOf(
            pkg(0, "stable", "arm64-v8a", "1.1.0"),
            pkg(1, "stable", "arm64-v8a", "1.2.0"),
        )

        assertThrows(IllegalArgumentException::class.java) {
            selectAndroidUpdatePackage(packages, listOf("arm64-v8a"), "stable", "release")
        }
    }
}
