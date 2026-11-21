import AppKit
import SwiftUI

private struct SSHConnectionCode: Encodable {
    let name: String
    let host: String
    let user: String
    let port: Int
    let host_key: String
    let platform = "macos"
    let remember = true
}

struct SSHConnectionSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var addresses: [String] = []
    @State private var host = ""
    @State private var port = "22"
    @State private var status = ""
    private let user = NSUserName()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("手机连接这台电脑").font(.headline)
            Text("先在系统设置的「通用 → 共享」开启远程登录，并允许此账号连接。")
                .foregroundStyle(.secondary)
            if !addresses.isEmpty {
                Picker("本机地址", selection: $host) {
                    if !addresses.contains(host) { Text("自定义").tag(host) }
                    ForEach(addresses, id: \.self) { Text($0).tag($0) }
                }
            }
            TextField("手机可达的地址", text: $host)
            TextField("SSH 端口", text: $port)
            Text("登录账号 \(user)。同一网络可用局域网地址，异地可填 Tailscale 地址。")
                .font(.caption).foregroundStyle(.secondary)
            if !status.isEmpty { Text(status).font(.caption) }
            HStack {
                Spacer()
                Button("关闭") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("复制连接码", action: copyCode).buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 470)
        .task {
            addresses = await Task.detached {
                let task = Process()
                let pipe = Pipe()
                task.executableURL = URL(fileURLWithPath: "/sbin/ifconfig")
                task.standardOutput = pipe
                guard (try? task.run()) != nil else { return [String]() }
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                task.waitUntilExit()
                return String(decoding: data, as: UTF8.self).split(separator: "\n").compactMap { line in
                    let fields = line.split(whereSeparator: \.isWhitespace)
                    guard fields.count > 1, fields[0] == "inet", !fields[1].hasPrefix("127."),
                          !fields[1].hasPrefix("169.254.") else { return nil }
                    return String(fields[1])
                }
            }.value
            if host.isEmpty { host = addresses.first ?? "" }
        }
    }

    private func copyCode() {
        let address = host.trimmingCharacters(in: .whitespacesAndNewlines)
        guard address.range(of: #"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,252}|[0-9a-fA-F:]*:[0-9a-fA-F:]+)$"#,
                            options: .regularExpression) != nil,
              let port = Int(port), (1...65535).contains(port) else {
            status = "请填写有效的地址和端口。"
            return
        }
        let key = (try? String(contentsOfFile: "/etc/ssh/ssh_host_ed25519_key.pub", encoding: .utf8))?
            .split(whereSeparator: \.isWhitespace).prefix(2).joined(separator: " ") ?? ""
        guard key.range(of: #"^ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}$"#, options: .regularExpression) != nil else {
            status = "读不到电脑的 SSH 主机密钥。请先开启远程登录。"
            return
        }
        let code = SSHConnectionCode(name: Host.current().localizedName ?? "Mac", host: address,
                                     user: user, port: port, host_key: key)
        guard let data = try? JSONEncoder().encode(code) else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("workbench-ssh-v1:" + data.base64EncodedString(), forType: .string)
        status = "已复制。到手机工作台的「运行环境 → 添加电脑」粘贴。"
    }
}
