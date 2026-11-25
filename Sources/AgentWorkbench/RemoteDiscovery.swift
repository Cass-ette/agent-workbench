import Foundation
import CryptoKit

enum RemoteDiscoveryIdentity {
    static let serviceType = "_agent-wb._tcp."

    static func fingerprint(_ publicKey: String) -> String? {
        let parts = publicKey.split(whereSeparator: \.isWhitespace)
        guard parts.count >= 2, parts[0] == "ssh-ed25519",
              let blob = Data(base64Encoded: String(parts[1])), blob.count == 51 else { return nil }
        let normalized = parts.prefix(2).joined(separator: " ")
        return SHA256.hash(data: Data(normalized.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

/// Advertise the SSH port, never the loopback RPC port or its access token.
/// Bonjour refreshes the host's addresses when its network interfaces change.
final class RemoteDiscoveryPublisher: NSObject, NetServiceDelegate {
    private var service: NetService?
    private var identity = ""
    var onFailure: ((String) -> Void)?

    func start(publicKey: String, port: Int) throws {
        guard let fingerprint = RemoteDiscoveryIdentity.fingerprint(publicKey), (1...65535).contains(port) else {
            throw RemoteWorkbenchError.invalid("请先开启系统 SSH，再启用局域网自动发现")
        }
        let nextIdentity = "\(fingerprint):\(port)"
        if identity == nextIdentity, service != nil { return }
        stop()
        let value = NetService(domain: "local.", type: RemoteDiscoveryIdentity.serviceType,
                               name: "wb-" + fingerprint.prefix(20), port: Int32(port))
        value.delegate = self
        value.setTXTRecord(NetService.data(fromTXTRecord: [
            "v": Data("1".utf8), "key": Data(fingerprint.utf8), "platform": Data("macos".utf8),
        ]))
        service = value
        identity = nextIdentity
        value.publish()
    }

    func stop() {
        service?.stop()
        service?.delegate = nil
        service = nil
        identity = ""
    }

    func netService(_ sender: NetService, didNotPublish errorDict: [String: NSNumber]) {
        stop()
        onFailure?("局域网自动发现不可用；仍可使用连接码或手动地址")
    }

    deinit { stop() }
}
