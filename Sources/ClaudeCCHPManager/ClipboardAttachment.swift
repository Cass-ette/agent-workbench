import AppKit

enum ClipboardAttachment {
    private static let maxBytes = 20 * 1024 * 1024

    @MainActor
    static func saveImage(from pasteboard: NSPasteboard, in directory: URL) throws -> URL? {
        let types: [NSPasteboard.PasteboardType] = [.png, .tiff, .init("public.jpeg")]
        var bitmap: NSBitmapImageRep?
        for type in types {
            guard let data = pasteboard.data(forType: type) else { continue }
            guard data.count <= maxBytes else { throw failure("截图超过 20 MB，请保存文件后再拖入。") }
            bitmap = NSBitmapImageRep(data: data)
            if bitmap != nil { break }
        }
        if bitmap == nil, let image = NSImage(pasteboard: pasteboard), let data = image.tiffRepresentation {
            guard data.count <= maxBytes else { throw failure("截图超过 20 MB，请保存文件后再拖入。") }
            bitmap = NSBitmapImageRep(data: data)
        }
        guard let bitmap else { return nil }
        guard bitmap.pixelsWide > 0, bitmap.pixelsHigh > 0,
              bitmap.pixelsWide <= 16384, bitmap.pixelsHigh <= 16384,
              bitmap.pixelsWide * bitmap.pixelsHigh <= 40_000_000,
              let png = bitmap.representation(using: .png, properties: [:]), png.count <= maxBytes else {
            throw failure("截图尺寸过大或无法转换为 PNG，请保存文件后再拖入。")
        }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        let destination = directory.appendingPathComponent("clipboard-\(UUID().uuidString).png")
        guard FileManager.default.createFile(atPath: destination.path, contents: png,
                                            attributes: [.posixPermissions: 0o600]) else {
            throw failure("无法保存剪贴板图片，请检查工作台附件目录的写入权限。")
        }
        return destination
    }

    private static func failure(_ message: String) -> NSError {
        NSError(domain: "WorkbenchClipboard", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }
}
