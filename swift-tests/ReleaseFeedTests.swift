import Foundation
import XCTest
@testable import ClaudeCCHPManager

final class ReleaseFeedTests: XCTestCase {
    let address = "https://github.com/Cass-ette/claude-cchp-manager/releases/download/test/update.json"

    func release(preview: Bool, published: String, draft: Bool = false, address: String? = nil) -> WorkbenchRelease {
        WorkbenchRelease(draft: draft, prerelease: preview, published_at: published,
                         assets: [.init(name: "update.json", browser_download_url: URL(string: address ?? self.address)!)])
    }

    func testSelectsNewestPublishedManifestIncludingPreviews() {
        let stable = release(preview: false, published: "2026-01-01")
        let preview = release(preview: true, published: "2026-02-01")
        XCTAssertEqual(ReleaseFeed.select([stable, preview])?.channel, "preview")
        XCTAssertEqual(ReleaseFeed.select([preview, stable])?.url.absoluteString, address)
        XCTAssertEqual(ReleaseFeed.select([stable, release(preview: true, published: "2026-03-01", draft: true)])?.channel, "stable")
    }

    func testRejectsUntrustedOrMissingManifest() {
        for bad in [address.replacingOccurrences(of: "github.com", with: "example.com"), address + "?extra=1",
                    address.replacingOccurrences(of: "Cass-ette/", with: "other/"), address + ".zip"] {
            XCTAssertNil(ReleaseFeed.select([release(preview: true, published: "2026-01-01", address: bad)]))
        }
        XCTAssertNil(ReleaseFeed.select([WorkbenchRelease(draft: false, prerelease: false, published_at: "2026-01-01", assets: [])]))
    }
}
