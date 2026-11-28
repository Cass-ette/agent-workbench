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
    static let stableManifestURL = URL(string: "https://raw.githubusercontent.com/Cass-ette/agent-workbench/update-feed/stable.json")!
    static let previewManifestURL = URL(string: "https://raw.githubusercontent.com/Cass-ette/agent-workbench/update-feed/preview.json")!
    static let manifestURL = stableManifestURL
    static let url = URL(string: "https://api.github.com/repos/Cass-ette/agent-workbench/releases?per_page=20")!

    static func isPreview(_ version: String) -> Bool { version.split(separator: ".").first == "0" }

    static func load<T: Sendable>(channel: String = "stable",
                                  read: @Sendable (URL) async throws -> Data,
                                  decode: @Sendable (Data) throws -> T) async throws -> T {
        let manifestURL = channel == "preview" ? previewManifestURL : stableManifestURL
        do {
            return try decode(await read(manifestURL))
        } catch {
            try Task.checkCancellation()
            let primary = error.localizedDescription
            do {
                let releases = try JSONDecoder().decode([WorkbenchRelease].self, from: await read(url))
                let sources = sources(releases)
                guard !sources.isEmpty else {
                    throw FeedFailure("发布页暂时没有完整更新包。")
                }
                var lastError: Error?
                for source in sources {
                    do {
                        return try decode(await read(source.url))
                    } catch {
                        lastError = error
                    }
                }
                throw lastError ?? FeedFailure("发布页暂时没有适用于当前通道的更新包。")
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
        trustedDownloadPaths.contains { url.path.hasPrefix($0) }
    }

    // The repository was renamed; older releases keep the previous path.
    private static let trustedDownloadPaths = [
        "/Cass-ette/agent-workbench/releases/download/",
        "/Cass-ette/claude-cchp-manager/releases/download/",
    ]

    static func select(_ releases: [WorkbenchRelease]) -> ReleaseManifestSource? {
        sources(releases).first
    }

    private static func sources(_ releases: [WorkbenchRelease]) -> [ReleaseManifestSource] {
        releases.filter { !$0.draft && $0.published_at != nil }
            .sorted { $0.published_at! > $1.published_at! }
            .compactMap { release in
                let assets = release.assets.filter { $0.name == "update.json" }
                guard assets.count == 1, let asset = assets.first, trusted(asset.browser_download_url),
                      asset.browser_download_url.lastPathComponent == "update.json" else { return nil }
                return ReleaseManifestSource(url: asset.browser_download_url)
            }
    }

}

private struct FeedFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
