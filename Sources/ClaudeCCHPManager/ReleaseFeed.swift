import Foundation

struct WorkbenchRelease: Decodable {
    struct Asset: Decodable {
        let name: String
        let browser_download_url: URL
    }
    let draft: Bool
    let prerelease: Bool
    let published_at: String?
    let assets: [Asset]
}

struct ReleaseManifestSource: Equatable {
    let url: URL
    let channel: String
}

enum ReleaseFeed {
    static let url = URL(string: "https://api.github.com/repos/Cass-ette/claude-cchp-manager/releases?per_page=20")!

    static func trusted(_ url: URL) -> Bool {
        url.scheme == "https" && url.host == "github.com" &&
        url.user == nil && url.password == nil && url.port == nil &&
        url.query == nil && url.fragment == nil &&
        url.path.hasPrefix("/Cass-ette/claude-cchp-manager/releases/download/")
    }

    static func select(_ releases: [WorkbenchRelease]) -> ReleaseManifestSource? {
        for release in releases.filter({ !$0.draft && $0.published_at != nil })
            .sorted(by: { $0.published_at! > $1.published_at! }) {
            let assets = release.assets.filter { $0.name == "update.json" }
            if assets.count == 1, let asset = assets.first, trusted(asset.browser_download_url),
               asset.browser_download_url.lastPathComponent == "update.json" {
                return ReleaseManifestSource(url: asset.browser_download_url, channel: release.prerelease ? "preview" : "stable")
            }
        }
        return nil
    }
}
