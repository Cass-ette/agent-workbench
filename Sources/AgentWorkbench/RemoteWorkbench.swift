import Foundation
import Network

enum RemoteWorkbenchError: LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case .invalid(let message) = self { message } else { nil } }
}

/// Bounded replay of one existing PTY. The IO thread only appends copied bytes.
final class RemoteTerminalBuffer: @unchecked Sendable {
    private static let capacity = 1_048_576
    private let lock = NSLock()
    // The PTY reader calls append synchronously. Keep the replay storage fixed
    // so continuous output does not allocate and copy the entire retained log.
    private var bytes = [UInt8](repeating: 0, count: RemoteTerminalBuffer.capacity)
    private var base = 0
    private var end = 0
    private var owner = ""
    private var expires = Date.distantPast
    private var releaseReason = ""
    private let now: () -> Date
    private var seen: [String] = []
    init(now: @escaping () -> Date = { Date() }) { self.now = now }
    var hasRemoteControl: Bool {
        lock.lock(); defer { lock.unlock() }
        return !owner.isEmpty && expires > now()
    }

    func append(_ data: [UInt8]) {
        lock.lock(); defer { lock.unlock() }
        guard !data.isEmpty else { return }
        let nextEnd = end + data.count
        var logical = end
        for byte in data {
            bytes[logical % Self.capacity] = byte
            logical += 1
        }
        end = nextEnd
        base = max(base, end - Self.capacity)
    }
    func claim(_ client: String) {
        lock.lock(); defer { lock.unlock() }
        owner = client; expires = now().addingTimeInterval(30); releaseReason = ""
    }
    func release(_ client: String? = nil) {
        lock.lock(); defer { lock.unlock() }
        if !owner.isEmpty && (client == nil || owner == client) {
            expires = .distantPast
            releaseReason = client == nil ? "desktop" : "read_only"
        }
    }
    func requireControl(_ client: String, inputID: String? = nil) throws -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !client.isEmpty, owner == client, expires > now() else {
            throw RemoteWorkbenchError.invalid("当前为只读查看；请重新点接续并控制")
        }
        expires = now().addingTimeInterval(30)
        if let inputID {
            guard !inputID.isEmpty, inputID.count <= 80 else { throw RemoteWorkbenchError.invalid("输入标识无效") }
            if seen.contains(inputID) { return false }
            seen.append(inputID)
            if seen.count > 256 { seen.removeFirst(seen.count - 256) }
        }
        return true
    }
    func snapshot(after: Int, client: String) -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        let active = !owner.isEmpty && expires > now()
        let controlled = !client.isEmpty && owner == client && active
        let state: String
        if controlled { state = "controlled"; expires = now().addingTimeInterval(30) }
        else if !client.isEmpty && owner == client { state = releaseReason.isEmpty ? "expired" : releaseReason }
        else { state = active ? "other_device" : "read_only" }
        let start = min(max(after, base), end)
        let length = min(end - start, 65_536)
        var chunk = [UInt8](); chunk.reserveCapacity(length)
        for offset in 0..<length { chunk.append(bytes[(start + offset) % Self.capacity]) }
        return ["data": Data(chunk).base64EncodedString(), "after": start + length,
                "truncated": after < base, "can_write": controlled, "control_state": state]
    }
}

/// Only SSH-authenticated helpers read the private rendezvous file. No LAN listener.
final class RemoteHTTPServer: @unchecked Sendable {
    private let listener: NWListener
    private let token = UUID().uuidString + UUID().uuidString
    private let endpoint: URL
    private let handler: @Sendable (Data) async -> Data
    private let queue = DispatchQueue(label: "workbench.remote.http", attributes: .concurrent)

    init(endpoint: URL, handler: @escaping @Sendable (Data) async -> Data) throws {
        self.endpoint = endpoint; self.handler = handler
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            if case .ready = state, let port = self.listener.port {
                do {
                    let fm = FileManager.default
                    try fm.createDirectory(at: endpoint.deletingLastPathComponent(), withIntermediateDirectories: true,
                                           attributes: [.posixPermissions: 0o700])
                    try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: endpoint.deletingLastPathComponent().path)
                    let data = try JSONSerialization.data(withJSONObject: ["schema": 1, "port": Int(port.rawValue), "token": self.token])
                    // atomic replacement keeps a helper from reading half a token.
                    try data.write(to: endpoint, options: .atomic)
                    try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: endpoint.path)
                } catch { self.listener.cancel() }
            }
        }
        listener.start(queue: queue)
    }
    func stop() {
        listener.cancel()
        if let data = try? Data(contentsOf: endpoint),
           let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any], value["token"] as? String == token {
            try? FileManager.default.removeItem(at: endpoint)
        }
    }
    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 70) { connection.cancel() }
        receive(connection, data: Data())
    }
    private func receive(_ connection: NWConnection, data: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] content, _, complete, error in
            guard let self else { connection.cancel(); return }
            var buffer = data; buffer.append(content ?? Data())
            guard buffer.count <= 2_097_152, error == nil else { connection.cancel(); return }
            if let end = buffer.range(of: Data("\r\n\r\n".utf8)) {
                let header = String(decoding: buffer[..<end.lowerBound], as: UTF8.self)
                let lines = header.components(separatedBy: "\r\n")
                var headers: [String: String] = [:]
                for line in lines.dropFirst() {
                    let parts = line.split(separator: ":", maxSplits: 1).map(String.init)
                    guard parts.count == 2, headers[parts[0].lowercased()] == nil else { connection.cancel(); return }
                    headers[parts[0].lowercased()] = parts[1].trimmingCharacters(in: .whitespaces)
                }
                guard lines.first == "POST /rpc HTTP/1.1", headers["authorization"] == "Bearer " + self.token,
                      headers["transfer-encoding"] == nil,
                      let length = Int(headers["content-length"] ?? ""), length > 0, length <= 1_048_576 else {
                    self.respond(connection, data: Data("{\"ok\":false,\"error\":\"Unauthorized request\"}".utf8), status: 403); return
                }
                if buffer.count >= end.upperBound + length {
                    let body = buffer.subdata(in: end.upperBound..<(end.upperBound + length))
                    Task { self.respond(connection, data: await self.handler(body), status: 200) }
                    return
                }
            } else if buffer.count > 8192 { connection.cancel(); return }
            if complete { connection.cancel(); return }
            self.receive(connection, data: buffer)
        }
    }
    private func respond(_ connection: NWConnection, data: Data, status: Int) {
        let header = "HTTP/1.1 \(status) \(status == 200 ? "OK" : "Forbidden")\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: \(data.count)\r\n\r\n"
        connection.send(content: Data(header.utf8) + data, completion: .contentProcessed { _ in connection.cancel() })
    }
}
