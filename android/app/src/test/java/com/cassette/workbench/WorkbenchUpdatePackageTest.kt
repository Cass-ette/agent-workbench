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
            packages, listOf("x86_64", "arm64-v8a"), "release",
        )?.version)
        assertEquals("2.0.0", selectAndroidUpdatePackage(
            packages, listOf("arm64-v8a"), "development",
        )?.version)
        assertNull(selectAndroidUpdatePackage(packages, listOf("x86_64"), "development"))
    }

    @Test fun releaseSignedPreviewDoesNotMatchDevelopmentClient() {
        val packages = listOf(AndroidUpdateCandidate(0, "android", "preview", "universal", "2.0.0", "release"))
        assertNull(selectAndroidUpdatePackage(packages, listOf("arm64-v8a"), "development"))
        assertEquals("2.0.0", selectAndroidUpdatePackage(packages, listOf("arm64-v8a"), "release")?.version)
    }

    @Test fun selectsPublishedPreviewAndRejectsUntrustedOrDraftManifests() {
        val url = "https://github.com/Cass-ette/claude-cchp-manager/releases/download/test/update.json"
        val stable = WorkbenchRelease(false, false, "2026-01-01", listOf(WorkbenchReleaseAsset("update.json", url)))
        val preview = stable.copy(prerelease = true, publishedAt = "2026-02-01")
        assertEquals(url, selectReleaseManifest(listOf(stable, preview))?.url)
        assertEquals(url, selectReleaseManifest(listOf(stable, preview.copy(draft = true)))?.url)
        for (bad in listOf(url.replace("github.com", "example.com"), url + "?extra=1", url.replace("Cass-ette/", "other/"))) {
            assertNull(selectReleaseManifest(listOf(preview.copy(assets = listOf(WorkbenchReleaseAsset("update.json", bad))))))
        }
        assertNull(selectReleaseManifest(listOf(preview.copy(assets = emptyList()))))
    }

    @Test fun fixedManifestWorksWhenApiWouldBeRateLimited() {
        val reads = mutableListOf<String>()
        val result = loadWorkbenchUpdate(read = { url ->
            reads.add(url)
            check(url == UPDATE_MANIFEST) { "HTTP 403" }
            "16"
        }, parse = String::toInt, selectFallback = { error("Should not request API") })
        assertEquals(16, result)
        assertEquals(listOf(UPDATE_MANIFEST), reads)
    }

    @Test fun invalidPrimaryFallsBackAndFailuresKeepTheirDetails() {
        val reads = mutableListOf<String>()
        val fallback = "https://github.com/Cass-ette/claude-cchp-manager/releases/download/test/update.json"
        assertEquals(16, loadWorkbenchUpdate(read = { url ->
            reads.add(url)
            when (url) {
                UPDATE_MANIFEST -> "broken"
                RELEASE_FEED -> "release list"
                else -> "16"
            }
        }, parse = String::toInt, selectFallback = { fallback }))
        assertEquals(listOf(UPDATE_MANIFEST, RELEASE_FEED, fallback), reads)
        val error = assertThrows(IllegalStateException::class.java) {
            loadWorkbenchUpdate<Int>(read = { url ->
                error(if (url == UPDATE_MANIFEST) "HTTP 404" else "HTTP 403")
            }, parse = String::toInt, selectFallback = { fallback })
        }
        org.junit.Assert.assertTrue(error.message!!.contains("HTTP 404"))
        org.junit.Assert.assertTrue(error.message!!.contains("HTTP 403"))
    }

    @Test fun packageAndPreviewStatusIgnoreGithubAndManifestLabels() {
        val packageInfo = AndroidUpdateCandidate(0, "android", "stable", "universal", "0.1.32", "development")
        assertEquals(packageInfo, selectAndroidUpdatePackage(listOf(packageInfo), listOf("arm64-v8a"), "development"))
        org.junit.Assert.assertTrue(isPreviewVersion(packageInfo.version))
        org.junit.Assert.assertFalse(isPreviewVersion("1.0.0"))
    }

    @Test fun rejectsDuplicatePackagesForThePreferredArchitecture() {
        val packages = listOf(
            pkg(0, "stable", "arm64-v8a", "1.1.0"),
            pkg(1, "stable", "arm64-v8a", "1.2.0"),
        )

        assertThrows(IllegalArgumentException::class.java) {
            selectAndroidUpdatePackage(packages, listOf("arm64-v8a"), "release")
        }
    }
}
