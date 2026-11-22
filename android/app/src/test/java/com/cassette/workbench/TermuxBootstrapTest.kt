package com.cassette.workbench

import java.io.ByteArrayInputStream
import java.util.Base64
import java.util.Random
import java.util.zip.ZipInputStream
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TermuxBootstrapTest {
    @Test fun oversizedSingleArgumentIsSplitAndAllAssetsSurvive() {
        // Incompressible input forces multiple argv entries even after compression.
        val bytes = ByteArray(132 * 1024).also(Random(0)::nextBytes)
        val args = bootstrapArguments("bootstrap", linkedMapOf("bridge.py" to bytes, "runtime.sh" to byteArrayOf(7)))
        assertEquals(listOf("--noprofile", "--norc", "-c", "bootstrap", "workbench-bootstrap"), args.take(5))
        val chunks = args.drop(5)
        assertTrue(chunks.size > 1)
        assertTrue(chunks.all { it.length <= BOOTSTRAP_CHUNK_SIZE })
        val decoded = Base64.getDecoder().decode(chunks.joinToString(""))
        ZipInputStream(ByteArrayInputStream(decoded)).use { zip ->
            assertEquals("bridge.py", zip.nextEntry.name)
            assertArrayEquals(bytes, zip.readBytes())
            assertEquals("runtime.sh", zip.nextEntry.name)
            assertArrayEquals(byteArrayOf(7), zip.readBytes())
        }
    }
}
