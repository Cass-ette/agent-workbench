import Foundation
import XCTest
@testable import AgentWorkbench

private final class URLRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [URL] = []
    func append(_ url: URL) { lock.lock(); defer { lock.unlock() }; storage.append(url) }
    var values: [URL] { lock.lock(); defer { lock.unlock() }; return storage }
}

final class ReleaseFeedTests: XCTestCase {
    let address = "https://github.com/Cass-ette/claude-cchp-manager/releases/download/test/update.json"

    func release(preview: Bool, published: String, draft: Bool = false, address: String? = nil) -> WorkbenchRelease {
        WorkbenchRelease(draft: draft, prerelease: preview, published_at: published,
                         assets: [.init(name: "update.json", browser_download_url: URL(string: address ?? self.address)!)])
    }

    func testSelectsNewestPublishedManifestIncludingPreviews() {
        let stable = release(preview: false, published: "2026-01-01")
        let preview = release(preview: true, published: "2026-02-01")
        XCTAssertEqual(ReleaseFeed.select([stable, preview])?.url.absoluteString, address)
        XCTAssertEqual(ReleaseFeed.select([preview, stable])?.url.absoluteString, address)
        XCTAssertEqual(ReleaseFeed.select([stable, release(preview: true, published: "2026-03-01", draft: true)])?.url.absoluteString, address)
    }

    func testVersionControlsPreviewStatus() {
        XCTAssertTrue(ReleaseFeed.isPreview("0.1.16"))
        XCTAssertFalse(ReleaseFeed.isPreview("1.0.0"))
    }

    func testFixedManifestDoesNotNeedTheAPI() async throws {
        let reads = URLRecorder()
        let value = try await ReleaseFeed.load(read: { url in
            reads.append(url)
            if url != ReleaseFeed.manifestURL { throw NSError(domain: "HTTP 403", code: 403) }
            return Data("16".utf8)
        }, decode: { try JSONDecoder().decode(Int.self, from: $0) })
        XCTAssertEqual(value, 16)
        XCTAssertEqual(reads.values, [ReleaseFeed.manifestURL])
    }

    func testInvalidPrimaryFallsBackWithoutUsingTheReleaseLabel() async throws {
        let reads = URLRecorder()
        let manifestAddress = address
        let releaseJSON = Data(("[{\"draft\":false,\"prerelease\":false,\"published_at\":\"2026-01-01\",\"assets\":[{\"name\":\"update.json\",\"browser_download_url\":\"" + manifestAddress + "\"}]}]").utf8)
        let value = try await ReleaseFeed.load(read: { url in
            reads.append(url)
            if url == ReleaseFeed.manifestURL { return Data("broken JSON".utf8) }
            return url == ReleaseFeed.url ? releaseJSON : Data("16".utf8)
        }, decode: { try JSONDecoder().decode(Int.self, from: $0) })
        XCTAssertEqual(value, 16)
        XCTAssertEqual(reads.values, [ReleaseFeed.manifestURL, ReleaseFeed.url, URL(string: manifestAddress)!])
    }

    func testBothFailuresAreVisible() async {
        let manifestURL = ReleaseFeed.manifestURL
        do {
            let _: Int = try await ReleaseFeed.load(read: { url in
                throw NSError(domain: "update", code: 1, userInfo: [NSLocalizedDescriptionKey:
                    url == manifestURL ? "HTTP 404" : "HTTP 403"])
            }, decode: { try JSONDecoder().decode(Int.self, from: $0) })
            XCTFail("Must report failure instead of claiming the app is up to date")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("HTTP 404"))
            XCTAssertTrue(error.localizedDescription.contains("HTTP 403"))
        }
    }

    func testAcceptsPackagesPublishedAfterTheRepositoryRename() {
        let renamed = address.replacingOccurrences(of: "claude-cchp-manager", with: "agent-workbench")
        XCTAssertEqual(ReleaseFeed.select([release(preview: true, published: "2026-01-01", address: renamed)])?.url.absoluteString, renamed)
    }

    func testRejectsUntrustedOrMissingManifest() {
        for bad in [address.replacingOccurrences(of: "github.com", with: "example.com"), address + "?extra=1",
                    address.replacingOccurrences(of: "Cass-ette/", with: "other/"), address + ".zip"] {
            XCTAssertNil(ReleaseFeed.select([release(preview: true, published: "2026-01-01", address: bad)]))
        }
        XCTAssertNil(ReleaseFeed.select([WorkbenchRelease(draft: false, prerelease: false, published_at: "2026-01-01", assets: [])]))
    }
}
