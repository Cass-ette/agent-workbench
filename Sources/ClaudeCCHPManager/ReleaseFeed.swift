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
}

enum ReleaseFeed {
    // Published only after all three installers and their release manifest are public.
    static let manifestURL = URL(string: "https://raw.githubusercontent.com/Cass-ette/claude-cchp-manager/update-feed/update.json")!
    static let url = URL(string: "https://api.github.com/repos/Cass-ette/claude-cchp-manager/releases?per_page=20")!

    static func isPreview(_ version: String) -> Bool { version.split(separator: ".").first == "0" }

    static func load<T: Sendable>(read: @Sendable (URL) async throws -> Data,
                                  decode: @Sendable (Data) throws -> T) async throws -> T {
        do {
            return try decode(await read(manifestURL))
        } catch {
            try Task.checkCancellation()
            let primary = error.localizedDescription
            do {
                let releases = try JSONDecoder().decode([WorkbenchRelease].self, from: await read(url))
                guard let source = select(releases) else {
                    throw FeedFailure("发布页暂时没有完整更新包。")
                }
                return try decode(await read(source.url))
            } catch {
                try Task.checkCancellation()
                throw FeedFailure("固定更新清单：\(primary)\n备用发布列表：\(error.localizedDescription)")
            }
        }
    }

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
                return ReleaseManifestSource(url: asset.browser_download_url)
            }
        }
        return nil
    }
}

private struct FeedFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
