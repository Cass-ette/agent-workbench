import Foundation

@MainActor
final class RemoteAttachments {
    struct Upload {
        let session: String
        let owner: String
        let path: URL
        let total: Int
        var count: Int
        var complete: Bool
    }
    private var uploads: [String: Upload] = [:]
    private let root: URL
    init(root: URL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/cchp-client/claude-test/attachments/remote")) { self.root = root }

    func receive(_ request: [String: Any], session: String, owner: String) throws -> [String: Any] {
        guard let id = request["upload_id"] as? String, UUID(uuidString: id) != nil,
              let size = request["size"] as? Int, size > 0, size <= 20 * 1024 * 1024,
              let offset = request["offset"] as? Int, offset >= 0,
              let encoded = request["data"] as? String, let data = Data(base64Encoded: encoded), !data.isEmpty, data.count <= 131_072 else {
            throw RemoteWorkbenchError.invalid("附件参数无效或超过 20 MB")
        }
        let fm = FileManager.default
        if uploads[id] == nil {
            guard offset == 0, uploads.values.filter({ !$0.complete }).count < 4 else { throw RemoteWorkbenchError.invalid("请重新选择附件或等待当前传输完成") }
            let original = ((request["name"] as? String ?? "attachment") as NSString).lastPathComponent
            let name = String(original.map { $0.isLetter || $0.isNumber || ".-_".contains($0) ? $0 : "_" }.prefix(80))
            try fm.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let file = root.appendingPathComponent(id + "-" + name)
            guard !fm.fileExists(atPath: file.path), fm.createFile(atPath: file.path, contents: nil, attributes: [.posixPermissions: 0o600]) else { throw RemoteWorkbenchError.invalid("电脑无法保存附件") }
            uploads[id] = Upload(session: session, owner: owner, path: file, total: size, count: 0, complete: false)
        }
        guard var upload = uploads[id], upload.session == session, upload.owner == owner, upload.total == size,
              upload.count == offset, !upload.complete, offset + data.count <= size,
              !upload.path.hasDirectoryPath else { throw RemoteWorkbenchError.invalid("附件传输顺序不一致，请重新选择") }
        let handle = try FileHandle(forWritingTo: upload.path)
        defer { try? handle.close() }
        try handle.seekToEnd(); try handle.write(contentsOf: data)
        upload.count += data.count; upload.complete = upload.count == size; uploads[id] = upload
        return ["received": upload.count, "complete": upload.complete]
    }

    func path(id: String, session: String, owner: String) throws -> String {
        guard let upload = uploads[id], upload.session == session, upload.owner == owner, upload.complete else { throw RemoteWorkbenchError.invalid("附件尚未传输完成") }
        return upload.path.path
    }
    func cancel(id: String, session: String, owner: String) {
        guard let upload = uploads[id], upload.session == session, upload.owner == owner else { return }
        if !upload.complete { try? FileManager.default.removeItem(at: upload.path) }
        uploads.removeValue(forKey: id)
    }
}
