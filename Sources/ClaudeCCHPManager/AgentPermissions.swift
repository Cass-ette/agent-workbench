import SwiftUI

func agentNetworkEnvironment(_ source: [String: String]) -> [String] {
    let proxyNames = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    var environment = source.filter { proxyNames.contains($0.key) }
    let bypass = ["127.0.0.1", "localhost", "::1"] + ["NO_PROXY", "no_proxy"].flatMap {
        (source[$0] ?? "").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
    var seen = Set<String>()
    let value = bypass.filter { seen.insert($0).inserted }.joined(separator: ",")
    environment["NO_PROXY"] = value
    environment["no_proxy"] = value
    return environment.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }
}

enum AgentPermissionMode: String, Codable, CaseIterable {
    case restricted, standard, full

    func label(for client: ClientKind) -> String {
        if client == .claude { return self == .full ? "跳过工具确认" : "标准审批" }
        switch self {
        case .restricted: return "项目内写入 · 限制联网"
        case .standard: return "项目内写入 · 允许联网"
        case .full: return "完整访问 · 按需审批"
        }
    }
}

struct AgentPermissions: Codable, Equatable {
    var codex: AgentPermissionMode = .standard
    var claude: AgentPermissionMode = .standard
    static let storageKey = "agentPermissions.v1"

    func mode(for client: ClientKind) -> AgentPermissionMode { client == .codex ? codex : claude }

    static func load() -> Self? {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let settings = try? JSONDecoder().decode(Self.self, from: data),
              settings.claude != .restricted else { return nil }
        return settings
    }
}

struct AgentPermissionsSheet: View {
    @ObservedObject var model: ManagerModel
    @State private var codex: AgentPermissionMode
    @State private var claude: AgentPermissionMode
    @State private var confirmed = false

    init(model: ManagerModel) {
        self.model = model
        _codex = State(initialValue: model.agentPermissions?.codex ?? .standard)
        _claude = State(initialValue: model.agentPermissions?.claude ?? .standard)
    }

    private var needsConfirmation: Bool {
        (codex == .full && model.agentPermissions?.codex != .full)
            || (claude == .full && model.agentPermissions?.claude != .full)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Agent 权限设置").font(.title2.weight(.semibold))
            Text("为工作台里的 Codex 和 Claude 选择权限。允许联网统一适用于 Git、下载依赖和其他网络请求。")
            Picker("Codex 权限", selection: $codex) {
                Text("项目内写入，允许联网（推荐）").tag(AgentPermissionMode.standard)
                Text("项目内写入，限制联网").tag(AgentPermissionMode.restricted)
                Text("完整访问，保留按需审批").tag(AgentPermissionMode.full)
            }
            Text("项目模式保留文件沙箱，项目外写入等操作由 Codex 按需申请。完整访问关闭这层沙箱，普通命令可能直接操作项目外文件，并非逐条确认。")
                .foregroundStyle(.secondary)
            Picker("Claude 权限", selection: $claude) {
                Text("标准审批（推荐）").tag(AgentPermissionMode.standard)
                Text("跳过工具权限确认").tag(AgentPermissionMode.full)
            }
            Text("标准模式按 Claude 自己的规则确认命令、联网与文件操作；它与 Codex 的文件沙箱不同。跳过确认会允许工具直接执行。")
                .foregroundStyle(.secondary)
            if needsConfirmation {
                Toggle("我确认启用上述高权限，理解它可访问当前账号可读写的项目外文件。", isOn: $confirmed)
                    .foregroundStyle(.orange)
            }
            Text("只影响之后新建、恢复和分支的工作台会话。系统 CLI 沿用外部设置。macOS 隐私目录仍由系统授权；工作台不会自动获取管理员权限。")
                .foregroundStyle(.secondary)
            Link("权限与工具接入说明（浏览器、技能、MCP）",
                 destination: URL(string: "https://github.com/Cass-ette/claude-cchp-manager/blob/main/docs/agent-permissions.zh-CN.md")!)
            HStack {
                Spacer()
                Button("取消") { model.isAgentPermissionsPresented = false }
                Button("保存权限设置") {
                    model.saveAgentPermissions(AgentPermissions(codex: codex, claude: claude))
                }
                .buttonStyle(.borderedProminent)
                .disabled(needsConfirmation && !confirmed)
            }
        }
        .font(.system(size: 13))
        .padding(24)
        .frame(width: 560)
        .onChange(of: codex) { _, _ in confirmed = false }
        .onChange(of: claude) { _, _ in confirmed = false }
    }
}
