import AppKit
import CryptoKit
import SwiftUI
import os

private let releasePage = URL(string: "https://github.com/Cass-ette/agent-workbench/releases")!

private enum MacUpdateSource {
    #if WORKBENCH_LOCAL_UPDATE_TEST
    static let directory = Bundle.main.bundleURL.deletingLastPathComponent().appending(path: "Update Feed")
    static let manifest = directory.appending(path: "update.json")
    static let bundleIdentifier = "com.cassette.agentworkbench.updatetest"
    static let channel = "local-test"
    static let architecture = "local"

    static func accepts(_ url: URL) -> Bool {
        url.isFileURL && url.host?.isEmpty != false &&
            url.resolvingSymlinksInPath().deletingLastPathComponent().path == directory.resolvingSymlinksInPath().path
    }
    #else
    static let bundleIdentifier = "com.cassette.agentworkbench"
    // The first public builds used this bundle identity even after the UI was
    // renamed. Keep it valid as an installed source so those builds can move
    // to the current product without a manual reinstall.
    static let legacyBundleIdentifiers = ["com.cassette.cchpmodelmanager"]
    static let architecture = "universal"

    static func accepts(_ url: URL) -> Bool {
        ReleaseFeed.trusted(url)
    }
    #endif

    static func acceptsInstalledBundle(_ identifier: String?) -> Bool {
        #if WORKBENCH_LOCAL_UPDATE_TEST
        return identifier == bundleIdentifier
        #else
        return identifier == bundleIdentifier || legacyBundleIdentifiers.contains(identifier ?? "")
        #endif
    }
}

private struct UpdateManifest: Decodable {
    let schema: Int
    let notes: String
    let packages: [UpdatePackage]
}

private struct UpdatePackage: Codable, Equatable, Sendable {
    let platform: String
    let arch: String
    let channel: String
    let version: String
    let url: URL
    let size: Int64
    let sha256: String

    var valid: Bool {
        MacUpdateSource.accepts(url) &&
        url.pathExtension == "zip" && size > 0 && size <= 1_073_741_824 &&
        sha256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
    }
}

private struct AvailableUpdate: Codable, Equatable {
    let package: UpdatePackage
    let notes: String
}

private struct UpdateFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

private struct MacUpdateService: Sendable {
    // Numeric versions work with all three platform installers; 0.x denotes preview.
    static func version(_ value: String) -> [Int]? {
        let parts = value.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3, parts.allSatisfy({ !$0.isEmpty && $0.utf8.allSatisfy { (48...57).contains($0) } }) else { return nil }
        let numbers = parts.compactMap { Int($0) }
        return numbers.count == 3 ? numbers : nil
    }

    static func lookup(current: String, channel: String) async throws -> AvailableUpdate? {
        let limit = 512 * 1024
        #if WORKBENCH_LOCAL_UPDATE_TEST
        let file = try FileHandle(forReadingFrom: MacUpdateSource.manifest)
        defer { try? file.close() }
        let data = try file.read(upToCount: limit + 1) ?? Data()
        return try parse(data, current: current, channel: MacUpdateSource.channel)
        #else
        return try await ReleaseFeed.load(channel: channel, read: readRemote) { data in
            try parse(data, current: current, channel: channel)
        }
        #endif
    }

    private static func parse(_ data: Data, current: String, channel: String? = nil) throws -> AvailableUpdate? {
        guard data.count <= 512 * 1024 else { throw UpdateFailure("发布清单过大。") }
        let manifest = try JSONDecoder().decode(UpdateManifest.self, from: data)
        guard manifest.schema == 1, let installed = version(current) else { throw UpdateFailure("无法识别更新清单或当前版本。") }
        let packages = manifest.packages.filter {
            $0.platform == "macos" && (channel == nil || $0.channel == channel) && $0.arch == MacUpdateSource.architecture
        }
        guard packages.count <= 1, let package = packages.first, package.valid,
              let latest = version(package.version) else { throw UpdateFailure("发布清单没有适用于这台 Mac 的安装包。") }
        let baseline = channel == "preview" && installed.first != 0 ? [0, 0, 0] : installed
        return baseline.lexicographicallyPrecedes(latest) ? AvailableUpdate(package: package, notes: manifest.notes) : nil
    }

    private static func readRemote(_ url: URL) async throws -> Data {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
        request.setValue("Agent-Workbench", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await URLSession.shared.data(for: request)
        if let status = (response as? HTTPURLResponse)?.statusCode, status != 200 {
            let hint = [403, 429].contains(status) ? "，请求受限，请稍后重试" : ""
            throw UpdateFailure("无法读取更新（HTTP \(status)\(hint)）。")
        }
        guard response.url?.scheme == "https" else {
            throw UpdateFailure("暂时无法读取发布清单。可以重试或打开发布页。")
        }
        guard data.count <= 512 * 1024 else { throw UpdateFailure("发布清单过大。") }
        return data
    }

    static func download(_ package: UpdatePackage, progress: @escaping @Sendable (Double) -> Void) async throws -> URL {
        let archive = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + ".zip")
        do {
            #if WORKBENCH_LOCAL_UPDATE_TEST
            try verify(package.url, package: package)
            try FileManager.default.copyItem(at: package.url, to: archive)
            #else
            try await fetch(package, archive: archive, progress: progress)
            #endif
            try Task.checkCancellation()
            try verify(archive, package: package)
            progress(1)
            return archive
        } catch {
            try? FileManager.default.removeItem(at: archive)
            throw error
        }
    }

    private static func fetch(_ package: UpdatePackage, archive: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
        let delegate = UpdateDownload(size: package.size, destination: archive, progress: progress)
        let request = URLRequest(url: package.url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 45)
        let session = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        try await delegate.download(request, session: session)
    }

    static func verify(_ archive: URL, package: UpdatePackage) throws {
        let archiveSize = try archive.resourceValues(forKeys: [.fileSizeKey]).fileSize
        guard let archiveSize, Int64(archiveSize) == package.size else { throw UpdateFailure("安装包大小不符，请重新下载。") }
        let file = try FileHandle(forReadingFrom: archive)
        defer { try? file.close() }
        var hash = SHA256()
        while let data = try file.read(upToCount: 256 * 1024), !data.isEmpty {
            try Task.checkCancellation()
            hash.update(data: data)
        }
        guard hash.finalize().map({ String(format: "%02x", $0) }).joined() == package.sha256 else {
            throw UpdateFailure("安装包校验失败，请重新下载。")
        }
    }

    static func install(_ archive: URL, package: UpdatePackage, destination: URL) throws {
        try verify(archive, package: package)
        let files = FileManager.default
        let destination = destination.resolvingSymlinksInPath()
        guard destination.pathExtension == "app", let installed = Bundle(url: destination),
              MacUpdateSource.acceptsInstalledBundle(installed.bundleIdentifier) else {
            throw UpdateFailure("请从已安装的工作台应用运行更新。")
        }
        let parent = destination.deletingLastPathComponent()
        let staging = parent.appending(path: ".workbench-update-" + UUID().uuidString, directoryHint: .isDirectory)
        try files.createDirectory(at: staging, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        var keepStaging = false
        defer { if !keepStaging { try? files.removeItem(at: staging) } }
        try run("/usr/bin/ditto", ["-x", "-k", archive.path, staging.path], error: "无法解开更新包。")
        let apps = try files.contentsOfDirectory(at: staging, includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            .filter { $0.pathExtension == "app" }
        guard apps.count == 1, let candidate = apps.first,
              try candidate.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]).isDirectory == true,
              try candidate.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == false,
              let bundle = Bundle(url: candidate), bundle.bundleIdentifier == MacUpdateSource.bundleIdentifier,
              bundle.infoDictionary?["CFBundleShortVersionString"] as? String == package.version else {
            throw UpdateFailure("安装包的应用或版本与发布清单不符。")
        }
        try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", candidate.path], error: "未能验证应用签名。")

        // Prepare the backup location before swapping so a failed swap leaves the running app in place.
        let backup = parent.appending(path: destination.deletingPathExtension().lastPathComponent + " 旧版 " + UUID().uuidString.prefix(8) + ".backup")
        guard renamex_np(candidate.path, destination.path, UInt32(RENAME_SWAP)) == 0 else {
            throw UpdateFailure("无法替换应用。请确认工作台安装目录可写，或使用发布页的安装包。")
        }
        do {
            try files.moveItem(at: candidate, to: backup)
        } catch {
            if renamex_np(candidate.path, destination.path, UInt32(RENAME_SWAP)) != 0 {
                keepStaging = true
                throw UpdateFailure("新版已放入应用目录，旧版保存在 \(candidate.path)。")
            }
            throw UpdateFailure("无法保存旧版备份，已恢复原应用。")
        }
    }

    private static func run(_ path: String, _ arguments: [String], error: String) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw UpdateFailure(error) }
    }
}

private final class UpdateDownload: NSObject, URLSessionDownloadDelegate, Sendable {
    let size: Int64
    let destination: URL
    let progress: @Sendable (Double) -> Void
    private struct State {
        var cancelled = false
        var task: URLSessionDownloadTask?
        var continuation: CheckedContinuation<Void, any Error>?
    }
    private let state = OSAllocatedUnfairLock(initialState: State())

    init(size: Int64, destination: URL, progress: @escaping @Sendable (Double) -> Void) {
        self.size = size
        self.destination = destination
        self.progress = progress
    }

    func download(_ request: URLRequest, session: URLSession) async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let task = session.downloadTask(with: request)
                state.withLock {
                    $0.continuation = continuation
                    $0.task = task
                    if $0.cancelled { task.cancel() }
                    else { task.resume() }
                }
            }
        } onCancel: {
            state.withLock {
                $0.cancelled = true
                $0.task?.cancel()
            }
        }
    }

    private func finish(_ result: Result<Void, any Error>) {
        let continuation = state.withLock {
            let continuation = $0.continuation
            $0.continuation = nil
            $0.task = nil
            return continuation
        }
        continuation?.resume(with: result)
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        do {
            guard let response = downloadTask.response as? HTTPURLResponse,
                  response.statusCode == 200, response.url?.scheme == "https" else {
                throw UpdateFailure("安装包下载失败。")
            }
            try FileManager.default.moveItem(at: location, to: destination)
            finish(.success(()))
        } catch {
            finish(.failure(error))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        finish(.failure(error ?? UpdateFailure("安装包下载未完成。")))
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        guard totalBytesWritten <= size, totalBytesExpectedToWrite <= size else { downloadTask.cancel(); return }
        progress(min(0.99, Double(totalBytesWritten) / Double(size)))
    }
}

@MainActor
final class WorkbenchUpdates: ObservableObject {
    let current = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
    @Published private var available: AvailableUpdate?
    @Published private(set) var checking = false
    @Published private(set) var downloading = false
    @Published private(set) var installing = false
    @Published private(set) var installed = false
    @Published private(set) var progress = 0.0
    @Published private(set) var status = ""
    @Published private(set) var archive: URL?
    @Published var show = false
    @Published private(set) var receivePreviewUpdates = false
    private var transfer: Task<Void, Never>?
    private var showCheckResult = false
    private var installedBundle: URL?
    private var channel: String { receivePreviewUpdates ? "preview" : "stable" }
    private var cacheKey: String { "workbench.update.available." + cacheScope + "." + channel }
    private var checkKey: String { "workbench.update.checked." + cacheScope + "." + channel }
    private var cacheScope: String {
        #if WORKBENCH_LOCAL_UPDATE_TEST
        let hash = SHA256.hash(data: Data(MacUpdateSource.directory.path.utf8))
        return current + "." + hash.map { String(format: "%02x", $0) }.joined()
        #else
        return current
        #endif
    }

    init() {
        receivePreviewUpdates = UserDefaults.standard.bool(forKey: "workbench.update.receivePreview")
        if let data = UserDefaults.standard.data(forKey: cacheKey) {
            available = try? JSONDecoder().decode(AvailableUpdate.self, from: data)
        }
    }

    var nextVersion: String? { installed ? nil : available?.package.version }
    var notes: String { available?.notes ?? "" }
    var isPreview: Bool { available.map { ReleaseFeed.isPreview($0.package.version) } ?? false }
    func setReceivePreviewUpdates(_ enabled: Bool) {
        guard receivePreviewUpdates != enabled, !checking, !downloading, !installing else { return }
        receivePreviewUpdates = enabled
        UserDefaults.standard.set(enabled, forKey: "workbench.update.receivePreview")
        available = nil
        status = enabled ? "已切换到测试版通道。" : "已切换到稳定版通道。"
        Task { await check(manual: true) }
    }
    func open() {
        show = true
        if available == nil && !installed { Task { await check(manual: true) } }
    }

    func check(manual: Bool = false) async {
        if checking {
            if manual {
                showCheckResult = true
                status = "正在检查更新…"
            }
            return
        }
        guard !downloading, !installing, !installed else { return }
        if !manual {
            if archive != nil || current.isEmpty { return }
            let next = UserDefaults.standard.object(forKey: checkKey) as? Date ?? .distantPast
            guard next <= .now else { return }
        }
        checking = true
        showCheckResult = manual
        if manual { status = "正在检查更新…" }
        defer { checking = false }
        do {
            let result = try await MacUpdateService.lookup(current: current, channel: channel)
            if let archive, result?.package != available?.package {
                try? FileManager.default.removeItem(at: archive)
                self.archive = nil
            }
            available = result
            UserDefaults.standard.set(result.flatMap { try? JSONEncoder().encode($0) }, forKey: cacheKey)
            UserDefaults.standard.set(Date.now.addingTimeInterval(86400), forKey: checkKey)
            if showCheckResult { status = result == nil ? "当前已是最新版本。" : "" }
        } catch {
            if !Task.isCancelled {
                UserDefaults.standard.set(Date.now.addingTimeInterval(1800), forKey: checkKey)
                if showCheckResult { status = error.localizedDescription }
            }
        }
    }

    func download() {
        guard !checking, !downloading, !installing, archive == nil, let selected = available else { return }
        downloading = true
        status = "正在下载更新…"
        progress = 0
        transfer = Task { [self] in
            defer { downloading = false; transfer = nil }
            do {
                let latest = try await MacUpdateService.lookup(current: current, channel: channel)
                guard latest?.package == selected.package else {
                    available = latest
                    UserDefaults.standard.set(latest.flatMap { try? JSONEncoder().encode($0) }, forKey: cacheKey)
                    status = "可用版本已变化，请查看最新说明。"
                    return
                }
                archive = try await MacUpdateService.download(selected.package) { [weak self] fraction in
                    Task { @MainActor in self?.progress = fraction }
                }
                status = "安装包已校验，可以安装。"
            } catch {
                status = Task.isCancelled ? "下载已取消。" : error.localizedDescription
            }
        }
    }

    func cancel() { transfer?.cancel() }

    func discardDownload() {
        guard !checking, !downloading, !installing, let archive else { return }
        try? FileManager.default.removeItem(at: archive)
        self.archive = nil
        status = ""
    }

    func install(manager: ManagerModel) {
        guard !downloading, !installing, let archive, let package = available?.package else { return }
        let running = manager.runningSessionCount
        guard running == 0 else {
            status = "还有 \(running) 个终端正在运行。请在任务完成后回来安装。"
            return
        }
        installing = true
        manager.installingApplicationUpdate = true
        status = "正在安装更新…"
        let destination = Bundle.main.bundleURL.resolvingSymlinksInPath()
        Task {
            defer { installing = false; manager.installingApplicationUpdate = installed }
            do {
                try await Task.detached { try MacUpdateService.install(archive, package: package, destination: destination) }.value
                try? FileManager.default.removeItem(at: archive)
                self.archive = nil
                installedBundle = destination
                installed = true
                UserDefaults.standard.removeObject(forKey: cacheKey)
                restart()
            } catch {
                status = error.localizedDescription
            }
        }
    }

    func restart() {
        guard let installedBundle else { return }
        // The running executable was moved to the backup. Relaunch the captured install
        // path after this process exits so Launch Services cannot reuse the old process.
        let relaunch = Process()
        relaunch.executableURL = URL(fileURLWithPath: "/bin/sh")
        relaunch.arguments = ["-c", """
            attempts=0
            while /bin/kill -0 "$1" 2>/dev/null; do
                [ "$attempts" -lt 100 ] || exit 1
                attempts=$((attempts + 1))
                /bin/sleep 0.1
            done
            exec /usr/bin/open -n "$2"
            """, "workbench-relaunch", String(ProcessInfo.processInfo.processIdentifier), installedBundle.path]
        do {
            try relaunch.run()
        } catch {
            status = "更新已安装。请退出并重新打开工作台。"
            return
        }
        NSApplication.shared.terminate(nil)
        exit(EXIT_SUCCESS)
    }
}

struct WorkbenchUpdateSheet: View {
    @ObservedObject var updates: WorkbenchUpdates
    let manager: ManagerModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("工作台更新").font(.headline)
            Toggle("接收测试版更新", isOn: Binding(
                get: { updates.receivePreviewUpdates },
                set: { updates.setReceivePreviewUpdates($0) }
            ))
            .disabled(updates.checking || updates.downloading || updates.installing)
            Text(updates.receivePreviewUpdates ? "测试版通道 · 可能包含未完成改动" : "稳定版通道")
                .font(.callout).foregroundStyle(updates.receivePreviewUpdates ? .orange : .secondary)
            #if WORKBENCH_LOCAL_UPDATE_TEST
            Text("本机更新测试").foregroundStyle(.secondary)
            #endif
            if updates.isPreview { Text("测试版 · 可下载安装").foregroundStyle(.orange) }
            Text("当前版本 \(updates.current)\(updates.nextVersion.map { " · 新版本 \($0)" } ?? "")")
                .foregroundStyle(.secondary)
            if updates.nextVersion != nil {
                ScrollView { Text(updates.notes.isEmpty ? "此版本暂无更新说明。" : updates.notes).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled) }
                    .frame(maxHeight: 230)
            }
            if updates.downloading { ProgressView(value: updates.progress) }
            if updates.checking || updates.installing { ProgressView().controlSize(.small) }
            if !updates.status.isEmpty { Text(updates.status).font(.callout) }
            HStack {
                Link("发布页", destination: releasePage)
                Spacer()
                if updates.downloading {
                    Button("取消下载") { updates.cancel() }
                } else {
                    Button("稍后") { updates.show = false }.disabled(updates.installing)
                    if updates.installed {
                        Button("重启工作台") { updates.restart() }.buttonStyle(.borderedProminent)
                    } else if updates.archive != nil {
                        Button("丢弃安装包") { updates.discardDownload() }.disabled(updates.installing || updates.checking)
                        Button("安装并重启") { updates.install(manager: manager) }.buttonStyle(.borderedProminent).disabled(updates.installing || updates.checking)
                    } else if updates.nextVersion != nil {
                        Button("重新检查") { Task { await updates.check(manual: true) } }.disabled(updates.checking)
                        Button("下载更新") { updates.download() }.buttonStyle(.borderedProminent).disabled(updates.checking)
                    } else {
                        Button("检查更新") { Task { await updates.check(manual: true) } }.disabled(updates.checking)
                    }
                }
            }
        }
        .padding(22)
        .frame(width: 500)
        .interactiveDismissDisabled(updates.installing)
    }
}
