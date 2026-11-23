import AppKit
import Combine
import Foundation

struct KeyProfile: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let gateway: String?
}

struct LastLaunch: Decodable {
    let client: String?
    let profile_id: String
    let model: String
    let workspace: String
    let session_mode: String
}

private struct GatewayState: Decodable {
    let gateway: String
    let profiles: [KeyProfile]
    let last_launch: LastLaunch?
    let workspaces: [String]
}

struct ModelResponse: Decodable, Sendable {
    let models: [String]
    let gateway: String?
}

struct LocalHistoryEntry: Decodable, Identifiable, Equatable {
    let sessionID: String
    let history_ref: String
    let client: String
    let title: String
    let workspace: String
    let model: String
    let configured_model: String?
    let profile_id: String
    let profile_name: String
    let gateway: String
    let updated_at: Int
    let scope: String?
    let preview: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "id"
        case history_ref, client, title, workspace, model, configured_model, profile_id, profile_name, gateway, updated_at, scope, preview
    }

    var id: String { history_ref }
    var clientKind: ClientKind { client == "codex" ? .codex : .claude }
    var isGlobal: Bool { scope == "global" }
    var updatedDate: Date { Date(timeIntervalSince1970: TimeInterval(updated_at)) }
}

private struct HistoryListResponse: Decodable {
    let sessions: [LocalHistoryEntry]
}

struct UsageSession: Decodable, Identifiable, Equatable {
    let id: String
    let client: String
    let model: String
    let workspace: String
    let input_tokens: Int?
    let output_tokens: Int?
    let cached_input_tokens: Int?
    let cache_creation_input_tokens: Int?
    let reasoning_output_tokens: Int?
    let estimated_cost: Double?
    let estimated_cost_upper: Double?
    let pricing_note: String?
    let cost_source: String?

    var costLabel: String { usageCostLabel(estimated_cost, upper: estimated_cost_upper, label: cost_source == "client" ? "客户端估算" : cost_source == "reference" ? "参考估算" : "网关单价估算") }
}

struct UsageSnapshot: Decodable, Equatable {
    let inputTokens: Int?
    let outputTokens: Int?
    let cachedInputTokens: Int?
    let cacheCreationInputTokens: Int?
    let reasoningOutputTokens: Int?
    let requests: Int
    let bySource: [String: Int]
    let pricingStatus: String
    let estimatedCost: Double?
    var estimatedCostUpper: Double? = nil
    var pricedSessions: Int? = nil
    var unpricedSessions: Int? = nil
    var pricingNotice: String? = nil
    let sessions: [UsageSession]
    let unreadableFiles: Int

    enum CodingKeys: String, CodingKey {
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
        case cachedInputTokens = "cached_input_tokens"
        case cacheCreationInputTokens = "cache_creation_input_tokens"
        case reasoningOutputTokens = "reasoning_output_tokens"
        case requests
        case bySource = "by_source"
        case pricingStatus = "pricing_status"
        case estimatedCost = "estimated_cost"
        case estimatedCostUpper = "estimated_cost_upper"
        case pricedSessions = "priced_sessions"
        case unpricedSessions = "unpriced_sessions"
        case pricingNotice = "pricing_notice"
        case sessions
        case unreadableFiles = "unreadable_files"
    }

    static let empty = UsageSnapshot(inputTokens: nil, outputTokens: nil,
                                     cachedInputTokens: nil, cacheCreationInputTokens: nil,
                                     reasoningOutputTokens: nil, requests: 0,
                                     bySource: [:], pricingStatus: "unavailable",
                                     estimatedCost: nil, sessions: [], unreadableFiles: 0)

    var costLabel: String {
        let label = pricingStatus.contains("partial") ? "部分会话估算" : pricingStatus.contains("reference") ? "含参考值估算" : pricingStatus.contains("mixed") ? "混合来源估算" : pricingStatus.contains("catalog") ? "网关单价估算" : "客户端估算"
        return usageCostLabel(estimatedCost, upper: estimatedCostUpper, label: label)
    }
}

func usageCount(_ value: Int?) -> String { value.map { $0.formatted() } ?? "—" }

func usageCostLabel(_ cost: Double?, upper: Double?, label: String) -> String {
    guard let cost else { return "待匹配单价 · 可在网关售价中设置来源" }
    if let upper, upper > cost + 0.00000001 { return String(format: "%@ $%.4f–$%.4f", label, cost, upper) }
    if cost > 0 && cost < 0.0001 { return label + " <$0.0001" }
    return String(format: "%@ $%.4f", label, cost)
}

private struct AddedProfile: Decodable {
    let id: String
    let name: String
    let gateway: String
    let model_count: Int
}

private struct RemovedProfile: Decodable {
    let id: String
    let name: String
    let removed: Bool
}

private struct GatewayUpdateResponse: Decodable {
    let gateway: String
}

enum SessionMode: String, CaseIterable, Identifiable {
    case new
    case `continue`
    case resume

    var id: String { rawValue }

    var title: String {
        switch self {
        case .new: "新会话"
        case .continue: "恢复最近对话"
        case .resume: "从历史创建分支"
        }
    }

    var compactTitle: String {
        switch self {
        case .new: "新会话"
        case .continue: "恢复最近对话"
        case .resume: "创建分支"
        }
    }
}

enum ClientKind: String, CaseIterable, Identifiable {
    case claude
    case codex

    var id: String { rawValue }

    var title: String {
        switch self {
        case .claude: "Claude"
        case .codex: "Codex"
        }
    }
}

enum LaunchScope: String, CaseIterable, Identifiable {
    case workbench
    case system

    var id: String { rawValue }

    var title: String {
        switch self {
        case .workbench: "工作台配置"
        case .system: "系统 CLI"
        }
    }
}

enum ModelFilter: String, CaseIterable, Identifiable {
    case all
    case favorites
    case codingNames
    case visionNames
    case imageNames

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "全部"
        case .favorites: "收藏"
        case .codingNames: "编程名候选"
        case .visionNames: "视觉名候选"
        case .imageNames: "图像生成名候选"
        }
    }
}

private struct BackendError: LocalizedError, Sendable {
    let message: String
    var errorDescription: String? { message }
}

private func shellQuote(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

struct TerminalLaunchSpec: Equatable {
    let id = UUID()
    let executable: String
    let arguments: [String]
    let workspace: String
    let environment: [String]
}

@MainActor
final class TerminalSession: ObservableObject, Identifiable {
    let id: UUID
    let client: ClientKind
    let profileID: String
    let profileName: String
    let model: String
    let workspace: String
    let sessionMode: SessionMode
    let gateway: String
    let isGlobal: Bool
    let sourceHistoryRef: String?
    let permissions: String
    let launchSpec: TerminalLaunchSpec
    let control = EmbeddedTerminalControl()
    @Published private(set) var isRunning = true
    @Published private(set) var exitCode: Int32?

    init(client: ClientKind, profile: KeyProfile?, model: String, workspace: String,
         sessionMode: SessionMode, gateway: String, launchSpec: TerminalLaunchSpec,
         isGlobal: Bool = false, sourceHistoryRef: String? = nil, permissions: String = "系统 CLI 权限") {
        id = launchSpec.id
        self.client = client
        profileID = profile?.id ?? "global"
        profileName = profile?.name ?? "系统全局"
        self.model = model
        self.workspace = workspace
        self.sessionMode = sessionMode
        self.gateway = gateway
        self.isGlobal = isGlobal
        self.sourceHistoryRef = sourceHistoryRef
        self.permissions = permissions
        self.launchSpec = launchSpec
    }

    var title: String { "\(client.title) · \(isGlobal ? "系统全局" : model)" }
    var workspaceName: String { URL(fileURLWithPath: workspace).lastPathComponent }

    func stop() {
        control.stop()
    }

    func processDidExit(_ code: Int32?) {
        exitCode = code
        isRunning = false
    }
}

private struct BackendClient: Sendable {
    let scriptURL: URL
    let pythonPath: String

    init() throws {
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("backend/claude-cchp-test.py")
        let development = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Projects/claude-cchp-manager/backend/claude-cchp-test.py")
        if let bundled, FileManager.default.isReadableFile(atPath: bundled.path) {
            scriptURL = bundled
        } else if FileManager.default.isReadableFile(atPath: development.path) {
            scriptURL = development
        } else {
            throw BackendError(message: "找不到本机 Claude 后端脚本。请重新构建应用。")
        }
        let environment = ProcessInfo.processInfo.environment
        let configuredPython = environment["CCHP_MANAGER_PYTHON"].map { [$0] } ?? []
        let pathCandidates = (environment["PATH"] ?? "")
            .split(separator: ":")
            .map { String($0) + "/python3" }
        // Finder's PATH often selects the system Python with an older TLS stack first.
        let candidates = configuredPython
            + ["/opt/homebrew/bin/python3", "/usr/local/bin/python3",
               NSHomeDirectory() + "/miniconda3/bin/python3"]
            + pathCandidates
            + ["/usr/bin/python3"]
        guard let python = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            throw BackendError(message: "找不到 Python 3；无法启动隔离后端。")
        }
        pythonPath = python
    }

    func run(_ arguments: [String], stdin: Data? = nil) async throws -> Data {
        let script = scriptURL.path
        let python = pythonPath
        return try await Task.detached(priority: .userInitiated) {
            let process = Process()
            let output = Pipe()
            let errors = Pipe()
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = [script] + arguments
            process.standardOutput = output
            process.standardError = errors
            if stdin != nil {
                process.standardInput = Pipe()
            }
            do {
                try process.run()
                if let stdin, let input = process.standardInput as? Pipe {
                    input.fileHandleForWriting.write(stdin)
                    try input.fileHandleForWriting.close()
                }
                let data = output.fileHandleForReading.readDataToEndOfFile()
                let errorData = errors.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                guard process.terminationStatus == 0 else {
                    let message = String(data: errorData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
                    throw BackendError(message: message?.isEmpty == false ? message! : "本机后端退出，代码 \(process.terminationStatus)。")
                }
                return data
            } catch let error as BackendError {
                throw error
            } catch {
                throw BackendError(message: "无法运行本机后端：\(error.localizedDescription)")
            }
        }.value
    }
}

@MainActor
final class ManagerModel: ObservableObject {
    @Published var isToolsPresented = false

    func manageTools(_ action: String, client: ClientKind, payload: [String: Any]? = nil) async throws -> ToolBridgeState {
        guard let backend else { throw BackendError(message: "后端尚未就绪") }
        let input = try payload.map { try JSONSerialization.data(withJSONObject: $0) }
        let result = try await backend.run([action, "--client", client.rawValue], stdin: input)
        return try JSONDecoder().decode(ToolBridgeState.self, from: result)
    }

    @Published private(set) var agentPermissions = AgentPermissions.load()
    @Published var isAgentPermissionsPresented = AgentPermissions.load() == nil

    func saveAgentPermissions(_ settings: AgentPermissions) {
        guard let data = try? JSONEncoder().encode(settings) else { return }
        UserDefaults.standard.set(data, forKey: AgentPermissions.storageKey)
        agentPermissions = settings
        isAgentPermissionsPresented = false
        statusMessage = "已保存权限设置；之后新建、恢复和分支的工作台会话使用此设置。"
        isStatusError = false
    }

    private func ensureAgentPermissions() -> Bool {
        guard agentPermissions != nil else {
            isAgentPermissionsPresented = true
            return false
        }
        return true
    }
    @Published var activeClient: ClientKind = .claude {
        didSet {
            if let history = selectedHistory, history.clientKind != activeClient {
                selectedHistory = nil
                sessionMode = .new
            }
            UserDefaults.standard.set(activeClient.rawValue, forKey: "client.kind")
            statusMessage = activeClient == .codex
                ? "Codex 使用独立 CODEX_HOME，不会修改原配置。"
                : "Claude 使用隔离设置、MCP 和会话目录。"
            isStatusError = false
        }
    }
    @Published var launchScope: LaunchScope = .workbench {
        didSet { UserDefaults.standard.set(launchScope.rawValue, forKey: "launch.scope") }
    }
    @Published var profiles: [KeyProfile] = []
    @Published var selectedProfileID = ""
    @Published var models: [String] = []
    @Published var selectedModel = "" {
        didSet {
            if !selectedModel.isEmpty {
                modelCatalog.remember(profile: selectedProfileID, gateway: gateway, selected: selectedModel)
            }
        }
    }
    @Published private(set) var modelLoadError = ""
    @Published private(set) var isUsingCachedModels = false
    @Published var modelSearch = ""
    @Published var modelFilter: ModelFilter = .all
    @Published var workspace = ""
    @Published var workspaces: [String] = []
    @Published var sessionMode: SessionMode = .new
    @Published var gateway = "http://127.0.0.1:8081"
    @Published var defaultGateway = "http://127.0.0.1:8081"
    @Published var isLoading = true
    @Published var isLoadingModels = false
    @Published var isModelPickerPresented = false
    @Published var isGatewaySettingsPresented = false
    @Published var isTerminalAppearancePresented = false
    @Published var gatewayDraft = "http://127.0.0.1:8081"
    @Published var isSavingGateway = false
    @Published var isAddProfilePresented = false
    @Published var isAddingProfile = false
    @Published var isDeletingProfile = false
    @Published var isRestoreConfirmationPresented = false
    @Published private(set) var isFindingRecentSession = false
    @Published var isHistoryPresented = false
    @Published var isLoadingHistory = false
    @Published var historySessions: [LocalHistoryEntry] = []
    @Published var selectedHistory: LocalHistoryEntry?
    @Published var historyError = ""
    @Published var historyScope: String = "workspace"
    @Published var addProfileError = ""
    @Published var addProfileStatus = ""
    @Published var statusMessage = "正在读取本机配置…"
    @Published var isStatusError = false
    @Published var terminalSessions: [TerminalSession] = []
    @Published private(set) var selectedTerminalID: UUID?
    @Published var usage = UsageSnapshot.empty
    @Published var isLoadingUsage = false
    @Published var usageError = ""
    @Published var gatewayAccount: GatewayAccount?
    @Published var gatewayBilling: GatewayBilling?
    @Published var isLoadingGatewayUsage = false
    @Published var gatewayUsageError = ""
    @Published var isBillingSettingsPresented = false
    @Published var billingConsoleDraft = ""
    @Published var billingTokenDraft = ""
    @Published var billingSettingsError = ""
    @Published var hasBillingToken = false
    @Published var isSavingBilling = false
    @Published var modelPricing: ModelPricingSnapshot?
    @Published var isLoadingPricing = false
    @Published var pricingError = ""
    @Published private var favoriteKeys = Set(UserDefaults.standard.stringArray(forKey: "models.favorite") ?? [])
    let terminalStyle = SharedTerminalStyle()
    @Published private(set) var remoteEnabled = false
    private var remoteServer: RemoteHTTPServer?
    private let remoteDiscovery = RemoteDiscoveryPublisher()
    @Published private(set) var remoteDiscoveryMessage = ""
    private let remoteAttachments = RemoteAttachments()

    func setRemoteEnabled(_ enabled: Bool) {
        if !enabled {
            remoteDiscovery.stop(); remoteDiscoveryMessage = ""
            remoteServer?.stop(); remoteServer = nil; remoteEnabled = false
            UserDefaults.standard.set(false, forKey: "remote.enabled")
            return
        }
        guard remoteServer == nil else { return }
        do {
            remoteServer = try RemoteHTTPServer(endpoint: FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".agent-workbench/remote.json")) { [weak self] data in
                guard let self else { return Data("{\"ok\":false,\"error\":\"电脑工作台已退出\"}".utf8) }
                return await self.handleRemoteRequest(data)
            }
            remoteEnabled = true
            refreshRemoteDiscovery()
            UserDefaults.standard.set(true, forKey: "remote.enabled")
        } catch { setError("无法启动手机接续服务：\(error.localizedDescription)") }
    }

    func restoreRemoteService() {
        if UserDefaults.standard.bool(forKey: "remote.enabled") { setRemoteEnabled(true) }
    }

    func refreshRemoteDiscovery(port: Int? = nil) {
        if let port { UserDefaults.standard.set(port, forKey: "remote.ssh.port") }
        guard remoteEnabled else { return }
        let savedPort = UserDefaults.standard.integer(forKey: "remote.ssh.port")
        let key = (try? String(contentsOfFile: "/etc/ssh/ssh_host_ed25519_key.pub", encoding: .utf8)) ?? ""
        remoteDiscovery.onFailure = { [weak self] message in
            Task { @MainActor [weak self] in self?.remoteDiscoveryMessage = message }
        }
        do {
            try remoteDiscovery.start(publicKey: key, port: savedPort == 0 ? 22 : savedPort)
            remoteDiscoveryMessage = "局域网自动发现已开启；换网后手机会核对电脑身份并更新地址"
        } catch { remoteDiscoveryMessage = error.localizedDescription }
    }

    private func remoteSessionRow(_ session: TerminalSession) -> [String: Any] {
        ["id": session.id.uuidString, "title": session.title, "client": session.client.rawValue,
         "workspace": session.workspace, "profile_id": session.profileID, "model": session.model,
         "scope": session.isGlobal ? "system" : "workbench", "permissions": session.permissions,
         "running": session.isRunning]
    }

    private func handleRemoteRequest(_ data: Data) async -> Data {
        let response: [String: Any]
        do {
            guard remoteEnabled, let request = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let op = request["op"] as? String else { throw RemoteWorkbenchError.invalid("请求无效") }
            if op != "ping" {
                guard let clientID = request["client_id"] as? String, UUID(uuidString: clientID) != nil else { throw RemoteWorkbenchError.invalid("控制端标识无效") }
            }
            let value = try await remoteOperation(request, op: op)
            response = ["ok": true, "value": value]
        } catch { response = ["ok": false, "error": error.localizedDescription] }
        return (try? JSONSerialization.data(withJSONObject: response)) ?? Data("{\"ok\":false,\"error\":\"响应无法编码\"}".utf8)
    }

    private func remoteOperation(_ request: [String: Any], op: String) async throws -> Any {
        func text(_ key: String) -> String { request[key] as? String ?? "" }
        if op == "ping" { return ["schema": 1] }
        guard let backend else { throw RemoteWorkbenchError.invalid("电脑工作台正在读取配置") }
        if op == "state" {
            return ["schema": 1, "platform": "macos", "capabilities": ["sessions", "launch", "system-cli", "history", "models", "attachments"],
                    "profiles": profiles.map { ["id": $0.id, "name": $0.name, "gateway": $0.gateway ?? defaultGateway] },
                    "workspace": workspace, "workspaces": workspaces, "client": activeClient.rawValue,
                    "profile_id": selectedProfileID, "model": selectedModel,
                    "permissions": ["codex": agentPermissions?.codex.rawValue ?? "unreviewed", "claude": agentPermissions?.claude.rawValue ?? "unreviewed"],
                    "sessions": terminalSessions.map(remoteSessionRow)] as [String: Any]
        }
        if op == "models" {
            guard profiles.contains(where: { $0.id == text("profile_id") }) else { throw RemoteWorkbenchError.invalid("电脑调用配置不存在") }
            let data = try await backend.run(["profile-models", "--profile-id", text("profile_id")])
            return try JSONSerialization.jsonObject(with: data)
        }
        if op == "history" {
            var args = ["history-list-global"]
            if !text("workspace").isEmpty { args += ["--workspace", text("workspace")] }
            if ["codex", "claude"].contains(text("client")) { args += ["--client", text("client")] }
            return try JSONSerialization.jsonObject(with: await backend.run(args))
        }
        if op == "launch" {
            guard !installingApplicationUpdate, let client = ClientKind(rawValue: text("client")) else { throw RemoteWorkbenchError.invalid("电脑正在更新或客户端无效") }
            let directory = text("workspace")
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: directory, isDirectory: &isDirectory), isDirectory.boolValue else { throw RemoteWorkbenchError.invalid("电脑项目目录不存在") }
            let previous = Set(terminalSessions.map(\.id))
            let action = text("history_action")
            if !action.isEmpty {
                let data = try await backend.run(["history-list-global", "--workspace", directory, "--client", client.rawValue])
                let rows = try JSONDecoder().decode(HistoryListResponse.self, from: data).sessions
                let entry = action == "recent" ? rows.first : rows.first(where: { $0.history_ref == text("history_ref") })
                guard let entry else { throw RemoteWorkbenchError.invalid("电脑当前项目找不到这条对话") }
                if action == "original" || action == "recent" {
                    let running = terminalSessions.filter { $0.isRunning && $0.workspace == directory && $0.client == client }
                    if !running.contains(where: { $0.sourceHistoryRef == entry.history_ref }), running.contains(where: { $0.sourceHistoryRef == nil }) {
                        throw RemoteWorkbenchError.invalid("此项目有正在运行的会话，请从电脑运行列表接续；要恢复其他历史，请先确认原会话已结束")
                    }
                    restoreOriginal(entry)
                }
                else if action == "fork" {
                    guard ensureAgentPermissions(), let profile = profiles.first(where: { $0.id == text("profile_id") }), !text("model").isEmpty else { throw RemoteWorkbenchError.invalid("请先在电脑确认权限并选择调用配置、模型") }
                    launchWorkbench(client: client, profile: profile, model: text("model"), directory: directory, targetGateway: profile.gateway ?? defaultGateway, mode: .resume, history: entry)
                } else { throw RemoteWorkbenchError.invalid("历史操作无效") }
            } else if text("scope") == "system" { launchGlobal(client: client, directory: directory) }
            else {
                guard ensureAgentPermissions(), let profile = profiles.first(where: { $0.id == text("profile_id") }), !text("model").isEmpty else { throw RemoteWorkbenchError.invalid("请先在电脑确认权限并选择调用配置、模型") }
                launchWorkbench(client: client, profile: profile, model: text("model"), directory: directory, targetGateway: profile.gateway ?? defaultGateway, mode: .new, history: nil)
            }
            guard !isStatusError, let session = terminalSessions.first(where: { !previous.contains($0.id) }) ?? selectedTerminal else { throw RemoteWorkbenchError.invalid(statusMessage) }
            return remoteSessionRow(session)
        }
        guard let id = UUID(uuidString: text("session_id")), let session = terminalSessions.first(where: { $0.id == id }) else { throw RemoteWorkbenchError.invalid("电脑会话已结束或不存在，请刷新") }
        let clientID = text("client_id")
        switch op {
        case "attachment":
            _ = try session.control.replay.requireControl(clientID)
            return try remoteAttachments.receive(request, session: text("session_id"), owner: clientID)
        case "attachment-cancel":
            remoteAttachments.cancel(id: text("upload_id"), session: text("session_id"), owner: clientID)
            return ["ok": true]
        case "attachment-paste":
            let path = try remoteAttachments.path(id: text("upload_id"), session: text("session_id"), owner: clientID)
            let input = "\u{1b}[200~" + shellQuote(path) + " " + "\u{1b}[201~"
            try session.control.remoteInput(Data(input.utf8), client: clientID, inputID: text("upload_id"))
            return ["attached": true]
        case "attach":
            guard session.isRunning else { throw RemoteWorkbenchError.invalid("电脑进程已退出；请使用恢复历史") }
            session.control.replay.claim(clientID)
            return remoteSessionRow(session)
        case "output":
            var value = session.control.replay.snapshot(after: request["after"] as? Int ?? 0, client: clientID)
            value["exit_code"] = session.isRunning ? NSNull() : (session.exitCode ?? 0) as Any
            return value
        case "input":
            guard session.isRunning, let bytes = Data(base64Encoded: text("data")), bytes.count <= 65_536 else { throw RemoteWorkbenchError.invalid("终端已退出或输入过大") }
            try session.control.remoteInput(bytes, client: clientID, inputID: text("input_id")); return ["accepted": true]
        case "resize":
            try session.control.remoteResize(cols: request["cols"] as? Int ?? 80, rows: request["rows"] as? Int ?? 24, client: clientID); return ["ok": true]
        case "detach": session.control.replay.release(clientID); return ["detached": true]
        case "stop":
            _ = try session.control.replay.requireControl(clientID)
            session.stop(); return ["stopping": true]
        default: throw RemoteWorkbenchError.invalid("电脑版本尚不支持此功能")
        }
    }

    private var backend: BackendClient?
    private let modelCatalog: ModelCatalogCache
    private let modelLoader: (@Sendable (String) async throws -> ModelResponse)?
    private var modelRequestID: UUID?
    private var pendingSessionMode: SessionMode = .new
    private var gatewayUsageRequest: UUID?
    private var lastGatewayUsage: Date?
    private var lastPricingRefresh: Date?
    private var pricingRequest: UUID?
    private var billingSettingsProfileID = ""

    init(modelDefaults: UserDefaults = .standard,
         modelLoader: (@Sendable (String) async throws -> ModelResponse)? = nil) {
        modelCatalog = ModelCatalogCache(defaults: modelDefaults)
        self.modelLoader = modelLoader
        activeClient = ClientKind(rawValue: UserDefaults.standard.string(forKey: "client.kind") ?? "") ?? .claude
        launchScope = LaunchScope(rawValue: UserDefaults.standard.string(forKey: "launch.scope") ?? "") ?? .workbench
    }

    var selectedProfile: KeyProfile? {
        profiles.first { $0.id == selectedProfileID }
    }

    var selectedTerminal: TerminalSession? {
        terminalSessions.first { $0.id == selectedTerminalID }
    }

    func selectTerminal(_ id: UUID?) {
        guard selectedTerminalID != id else { return }
        selectedTerminal?.control.setSelected(false)
        selectedTerminalID = id
        selectedTerminal?.control.setSelected(true)
    }

    func moveTerminal(_ id: UUID, to targetIndex: Int) {
        guard let sourceIndex = terminalSessions.firstIndex(where: { $0.id == id }) else { return }
        let destination = max(0, min(targetIndex, terminalSessions.count - 1))
        guard sourceIndex != destination else { return }
        let session = terminalSessions.remove(at: sourceIndex)
        terminalSessions.insert(session, at: destination)
    }

    var agentRunning: Bool { terminalSessions.contains(where: \.isRunning) }

    var runningSessionCount: Int { terminalSessions.filter(\.isRunning).count }

    @Published var installingApplicationUpdate = false

    var canLaunch: Bool {
        guard !installingApplicationUpdate, !isFindingRecentSession, !workspace.isEmpty, FileManager.default.fileExists(atPath: workspace) else { return false }
        if launchScope == .system { return true }
        return selectedProfile != nil && !selectedModel.isEmpty && !isLoadingModels
    }

    var filteredModels: [String] {
        models.filter { item in
            let matchesSearch = modelSearch.isEmpty || item.localizedCaseInsensitiveContains(modelSearch)
            let matchesFilter: Bool
            switch modelFilter {
            case .all: matchesFilter = true
            case .favorites: matchesFilter = isFavorite(item)
            case .codingNames: matchesFilter = Self.hasNameHint(item, terms: ["code", "coder", "coding", "devstral", "codestral", "kimi", "deepseek", "qwen", "glm", "minimax", "claude", "gpt"])
            case .visionNames: matchesFilter = Self.hasNameHint(item, terms: ["vision", "-vl", "_vl", "visual", "multimodal"])
            case .imageNames: matchesFilter = Self.hasNameHint(item, terms: ["image", "dall-e", "dalle", "flux", "imagen", "seedream", "nano-banana", "stable-diffusion"])
            }
            return matchesSearch && matchesFilter
        }
    }

    private static func hasNameHint(_ model: String, terms: [String]) -> Bool {
        let identifier = model.lowercased()
        return terms.contains { identifier.contains($0) }
    }

    private func favoriteKey(_ model: String) -> String {
        "\(activeClient.rawValue)|\(selectedProfileID)|\(model)"
    }

    func isFavorite(_ model: String) -> Bool {
        favoriteKeys.contains(favoriteKey(model))
    }

    func toggleFavorite(_ model: String) {
        var favorites = favoriteKeys
        let key = favoriteKey(model)
        if favorites.contains(key) { favorites.remove(key) } else { favorites.insert(key) }
        favoriteKeys = favorites
        UserDefaults.standard.set(Array(favorites).sorted(), forKey: "models.favorite")
    }

    func showGatewaySettings() {
        gatewayDraft = gateway
        isGatewaySettingsPresented = true
    }

    func showTerminalAppearance() {
        isTerminalAppearancePresented = true
    }

    func saveGateway() async {
        guard let backend else { return }
        isSavingGateway = true
        defer { isSavingGateway = false }
        do {
            let payload = try JSONSerialization.data(withJSONObject: ["url": gatewayDraft])
            let profileID = selectedProfile?.id
            let action: [String]
            if let profileID, profileID != "free" {
                action = ["set-profile-gateway-stdin", "--profile-id", profileID]
            } else {
                action = ["set-gateway-stdin"]
            }
            let data = try await backend.run(action, stdin: payload)
            let saved = try JSONDecoder().decode(GatewayUpdateResponse.self, from: data)
            let value = saved.gateway
            let gatewayChanged = gateway != value
            gateway = value
            if profileID == nil || profileID == "free" { defaultGateway = value }
            if let profileID, let index = profiles.firstIndex(where: { $0.id == profileID }) {
                profiles[index] = KeyProfile(id: profiles[index].id, name: profiles[index].name, gateway: value)
            }
            if gatewayChanged {
                let cached = modelCatalog.entry(profile: selectedProfileID, gateway: value)
                models = cached.models
                selectedModel = cached.selected
                modelLoadError = ""
            }
            isGatewaySettingsPresented = false
            statusMessage = "网关地址已保存；正在刷新当前 Key 的模型列表。"
            isStatusError = false
            resetGatewayUsage()
            if !selectedProfileID.isEmpty { await loadModels(for: selectedProfileID) }
            await refreshGatewayUsage()
        } catch {
            setError(error.localizedDescription)
        }
    }

    func load() async {
        do {
            let client = try BackendClient()
            backend = client
            let data = try await client.run(["gui-state"])
            let state = try JSONDecoder().decode(GatewayState.self, from: data)
            defaultGateway = state.gateway
            gateway = state.gateway
            profiles = state.profiles
            workspaces = state.workspaces

            let last = state.last_launch
            if let last, profiles.contains(where: { $0.id == last.profile_id }) {
                activeClient = ClientKind(rawValue: last.client ?? "claude") ?? .claude
                selectedProfileID = last.profile_id
                gateway = selectedProfile?.gateway ?? defaultGateway
                workspace = last.workspace
                selectedModel = last.model
            } else {
                selectedProfileID = profiles.first?.id ?? ""
                workspace = state.workspaces.first ?? NSHomeDirectory()
            }
            if let profileGateway = selectedProfile?.gateway { gateway = profileGateway }
            isLoading = false
            if !selectedProfileID.isEmpty {
                await loadModels(for: selectedProfileID)
            } else {
                statusMessage = "添加一把当前网关的调用 Key 后开始使用。"
            }
            await refreshUsage()
            await refreshGatewayUsage()
        } catch {
            isLoading = false
            setError(error.localizedDescription)
        }
    }

    func refreshUsage() async {
        guard let backend, !isLoadingUsage else { return }
        isLoadingUsage = true
        defer { isLoadingUsage = false }
        do {
            let data = try await backend.run(["usage"])
            usage = try JSONDecoder().decode(UsageSnapshot.self, from: data)
            usageError = ""
        } catch {
            usageError = "用量读取失败；保留上次结果"
        }
    }

    func refreshPricing(force: Bool = false) async {
        guard let backend, !selectedProfileID.isEmpty, !isLoadingPricing else { return }
        if !force, let lastPricingRefresh, Date().timeIntervalSince(lastPricingRefresh) < 300 { return }
        let profileID = selectedProfileID, requestGateway = gateway, request = UUID()
        pricingRequest = request
        isLoadingPricing = true
        defer { if pricingRequest == request { isLoadingPricing = false } }
        do {
            let data = try await backend.run(["profile-pricing", "--profile-id", profileID] + (force ? ["--force"] : []))
            let snapshot = try JSONDecoder().decode(ModelPricingSnapshot.self, from: data)
            guard pricingRequest == request, selectedProfileID == profileID, gateway == requestGateway,
                  snapshot.gateway == requestGateway else { return }
            modelPricing = snapshot
            pricingError = ""
            lastPricingRefresh = Date()
            await refreshUsage()
        } catch {
            guard pricingRequest == request, selectedProfileID == profileID else { return }
            pricingError = "价格刷新失败，保留缓存；可指定公开价格来源。"
            lastPricingRefresh = Date()
        }
    }

    func savePricingSource(_ url: String) async {
        guard let backend, !isLoadingPricing else { return }
        let profileID = selectedProfileID, requestGateway = gateway, request = UUID()
        pricingRequest = request
        isLoadingPricing = true
        defer { if pricingRequest == request { isLoadingPricing = false } }
        do {
            let payload = try JSONSerialization.data(withJSONObject: ["url": url])
            let data = try await backend.run(["save-pricing-source-stdin", "--profile-id", profileID], stdin: payload)
            guard pricingRequest == request, selectedProfileID == profileID, gateway == requestGateway else { return }
            modelPricing = try JSONDecoder().decode(ModelPricingSnapshot.self, from: data)
            pricingError = ""
            lastPricingRefresh = Date()
            await refreshUsage()
        } catch {
            guard pricingRequest == request, selectedProfileID == profileID else { return }
            pricingError = "公开地址校验失败，未改变价格来源。"
        }
    }

    private func resetGatewayUsage() {
        pricingRequest = nil
        modelPricing = nil
        pricingError = ""
        isLoadingPricing = false
        lastPricingRefresh = nil
        gatewayUsageRequest = nil
        gatewayAccount = nil
        gatewayBilling = nil
        gatewayUsageError = ""
        isLoadingGatewayUsage = false
        lastGatewayUsage = nil
    }

    func refreshGatewayUsage(force: Bool = false) async {
        guard let backend, let profile = selectedProfile, !isLoadingGatewayUsage else { return }
        if !force, let lastGatewayUsage, Date().timeIntervalSince(lastGatewayUsage) < 60 { return }
        let requestID = UUID()
        let profileID = profile.id
        let requestGateway = gateway
        gatewayUsageRequest = requestID
        isLoadingGatewayUsage = true
        defer {
            if gatewayUsageRequest == requestID { isLoadingGatewayUsage = false }
        }
        do {
            async let accountData = backend.run(["profile-account", "--profile-id", profileID])
            async let billData = backend.run(["profile-billing", "--profile-id", profileID])
            let (account, bills) = try await (accountData, billData)
            let decodedAccount = try JSONDecoder().decode(GatewayAccount.self, from: account)
            let decodedBills = try JSONDecoder().decode(GatewayBilling.self, from: bills)
            guard gatewayUsageRequest == requestID, selectedProfileID == profileID, gateway == requestGateway,
                  decodedAccount.gateway == requestGateway, decodedBills.gateway == requestGateway else { return }
            gatewayAccount = decodedAccount
            gatewayBilling = decodedBills
            gatewayUsageError = ""
            lastGatewayUsage = Date()
        } catch {
            guard gatewayUsageRequest == requestID, selectedProfileID == profileID, gateway == requestGateway else { return }
            gatewayUsageError = "网关查询失败；如有旧结果，请以其查询时间为准。"
            lastGatewayUsage = Date()
        }
    }

    func showBillingSettings() async {
        guard let backend, let profile = selectedProfile else { return }
        billingSettingsProfileID = profile.id
        billingTokenDraft = ""
        billingSettingsError = ""
        do {
            let data = try await backend.run(["billing-settings", "--profile-id", profile.id])
            guard selectedProfileID == profile.id else { return }
            let settings = try JSONDecoder().decode(BillingSettings.self, from: data)
            billingConsoleDraft = settings.console
            hasBillingToken = settings.configured
            isBillingSettingsPresented = true
        } catch { gatewayUsageError = error.localizedDescription }
    }

    func saveBillingSettings() async {
        guard let backend, selectedProfileID == billingSettingsProfileID, !isSavingBilling else { return }
        let profileID = billingSettingsProfileID
        isSavingBilling = true
        billingSettingsError = ""
        defer { isSavingBilling = false }
        do {
            let payload = try JSONSerialization.data(withJSONObject: ["console": billingConsoleDraft, "token": billingTokenDraft])
            let data = try await backend.run(["save-billing-stdin", "--profile-id", profileID], stdin: payload)
            guard selectedProfileID == profileID else { return }
            gatewayBilling = try JSONDecoder().decode(GatewayBilling.self, from: data)
            billingTokenDraft = ""
            hasBillingToken = true
            isBillingSettingsPresented = false
            lastGatewayUsage = nil
        } catch { billingSettingsError = error.localizedDescription }
    }

    func disconnectBilling() async {
        guard let backend, selectedProfileID == billingSettingsProfileID, !isSavingBilling else { return }
        isSavingBilling = true
        defer { isSavingBilling = false }
        do {
            _ = try await backend.run(["remove-billing", "--profile-id", billingSettingsProfileID])
            gatewayBilling = nil
            hasBillingToken = false
            billingTokenDraft = ""
            isBillingSettingsPresented = false
            lastGatewayUsage = nil
            await refreshGatewayUsage(force: true)
        } catch { billingSettingsError = error.localizedDescription }
    }

    func selectProfile(_ profile: KeyProfile) async {
        guard selectedProfileID != profile.id else {
            if models.isEmpty || !modelLoadError.isEmpty { await loadModels(for: profile.id) }
            return
        }
        selectedProfileID = profile.id
        gateway = profile.gateway ?? defaultGateway
        let cached = modelCatalog.entry(profile: profile.id, gateway: gateway)
        models = cached.models
        selectedModel = cached.selected
        modelLoadError = ""
        isUsingCachedModels = !models.isEmpty
        resetGatewayUsage()
        async let gatewayRefresh: () = refreshGatewayUsage()
        await loadModels(for: profile.id)
        await gatewayRefresh
    }

    func loadModels(for profileID: String) async {
        guard profileID == selectedProfileID, backend != nil || modelLoader != nil else { return }
        let requestGateway = gateway
        let requestID = UUID()
        modelRequestID = requestID
        let cached = modelCatalog.entry(profile: profileID, gateway: requestGateway)
        if models.isEmpty { models = cached.models }
        if selectedModel.isEmpty { selectedModel = cached.selected }
        isUsingCachedModels = !models.isEmpty
        modelLoadError = ""
        isLoadingModels = true
        defer {
            if modelRequestID == requestID { isLoadingModels = false }
        }
        do {
            let response: ModelResponse
            if let modelLoader { response = try await modelLoader(profileID) }
            else {
                let data = try await backend!.run(["profile-models", "--profile-id", profileID])
                response = try JSONDecoder().decode(ModelResponse.self, from: data)
            }
            guard modelRequestID == requestID, selectedProfileID == profileID, gateway == requestGateway else { return }
            let fetched = Array(Set(response.models.filter { !$0.isEmpty })).sorted()
            guard !fetched.isEmpty else { throw BackendError(message: "网关未返回可用模型。") }
            models = fetched
            if let responseGateway = response.gateway { gateway = responseGateway }
            if !models.contains(selectedModel) {
                selectedModel = models.first ?? ""
            }
            modelCatalog.remember(profile: profileID, gateway: gateway, selected: selectedModel, models: models)
            isUsingCachedModels = false
            isLoadingModels = false
            statusMessage = "\(models.count) 个可用模型"
            isStatusError = false
            await refreshPricing()
        } catch {
            guard modelRequestID == requestID, selectedProfileID == profileID, gateway == requestGateway else { return }
            modelLoadError = error.localizedDescription
            isUsingCachedModels = !models.isEmpty || !selectedModel.isEmpty
            setError("模型列表读取失败：\(modelLoadError)" + (isUsingCachedModels ? " 已保留上次模型选择。" : " 请重试或切换调用配置。"))
        }
    }

    func addProfile(name: String, key: String, gateway profileGateway: String) async -> Bool {
        guard let backend else {
            addProfileError = "本机后端尚未就绪。"
            return false
        }
        isAddingProfile = true
        addProfileError = ""
        addProfileStatus = "正在仅查询模型白名单…"
        do {
            let payload = try JSONSerialization.data(withJSONObject: ["name": name, "key": key, "gateway": profileGateway])
            let data = try await backend.run(["add-profile-stdin"], stdin: payload)
            let added = try JSONDecoder().decode(AddedProfile.self, from: data)
            try await refreshProfiles()
            selectedProfileID = added.id
            gateway = added.gateway
            selectedModel = ""
            models = []
            resetGatewayUsage()
            await loadModels(for: added.id)
            await refreshGatewayUsage()
            addProfileStatus = "已保存；找到 \(added.model_count) 个模型，未发起推理。"
            isAddingProfile = false
            return true
        } catch {
            addProfileError = error.localizedDescription
            addProfileStatus = ""
            isAddingProfile = false
            return false
        }
    }

    func deleteProfile(_ profile: KeyProfile) async {
        guard profile.id != "free", !isDeletingProfile, let backend else { return }
        isDeletingProfile = true
        defer { isDeletingProfile = false }
        let activeSessions = terminalSessions.filter { $0.isRunning && $0.profileID == profile.id }.count
        do {
            let data = try await backend.run(["remove-profile", "--profile-id", profile.id])
            let result = try JSONDecoder().decode(RemovedProfile.self, from: data)
            guard result.removed, result.id == profile.id else {
                throw BackendError(message: "本机没有确认删除这把 Key。")
            }
            try await refreshProfiles()
            favoriteKeys = favoriteKeys.filter {
                !$0.hasPrefix("claude|\(profile.id)|") && !$0.hasPrefix("codex|\(profile.id)|")
            }
            UserDefaults.standard.set(Array(favoriteKeys).sorted(), forKey: "models.favorite")
            if billingSettingsProfileID == profile.id {
                isBillingSettingsPresented = false
                billingTokenDraft = ""
                hasBillingToken = false
            }
            if selectedProfileID == profile.id {
                selectedProfileID = profiles.first?.id ?? ""
                selectedModel = ""
                gateway = selectedProfile?.gateway ?? defaultGateway
                models = []
                resetGatewayUsage()
                if !selectedProfileID.isEmpty {
                    await loadModels(for: selectedProfileID)
                    await refreshGatewayUsage()
                }
            }
            let runningNotice = activeSessions == 0
                ? ""
                : " 已启动的 \(activeSessions) 个终端仍可能持有该 Key，结束终端后凭据才会从进程中释放。"
            statusMessage = "已删除调用配置「\(result.name)」及其本机 Key 和账单 Token；历史对话已保留。\(runningNotice)"
            isStatusError = false
        } catch {
            setError(error.localizedDescription)
        }
    }

    private func refreshProfiles() async throws {
        guard let backend else { return }
        let data = try await backend.run(["gui-state"])
        let state = try JSONDecoder().decode(GatewayState.self, from: data)
        profiles = state.profiles
        workspaces = state.workspaces
    }

    func chooseWorkspace() {
        let panel = NSOpenPanel()
        panel.title = "选择工作目录"
        panel.message = "\(activeClient.title) 将从所选目录启动。"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        if panel.runModal() == .OK, let url = panel.url {
            setWorkspace(url.standardizedFileURL.path)
            rememberWorkspace(workspace)
        }
    }

    func setWorkspace(_ path: String) {
        guard FileManager.default.fileExists(atPath: path) else { return }
        let next = URL(fileURLWithPath: path).standardizedFileURL.path
        if let selectedHistory,
           URL(fileURLWithPath: selectedHistory.workspace).standardizedFileURL.path != next {
            self.selectedHistory = nil
            sessionMode = .new
        }
        workspace = next
        rememberWorkspace(workspace)
    }

    private func rememberWorkspace(_ path: String) {
        workspaces.removeAll { $0 == path }
        workspaces.insert(path, at: 0)
        workspaces = Array(workspaces.prefix(12))
    }

    func requestLaunch() {
        guard !installingApplicationUpdate else { return }
        if launchScope == .system {
            launchGlobal(client: activeClient)
            return
        }
        guard ensureAgentPermissions() else { return }
        if sessionMode == .continue {
            restoreRecentInProject()
            return
        }
        guard !selectedProfileID.isEmpty, !selectedModel.isEmpty, !workspace.isEmpty else {
            setError("请选择调用 Key、模型和工作目录。")
            return
        }
        pendingSessionMode = sessionMode
        if sessionMode == .resume {
            guard selectedHistory != nil else {
                Task { await showHistory() }
                return
            }
            isRestoreConfirmationPresented = true
            return
        }
        launchConfirmed()
    }

    func selectSessionMode(_ mode: SessionMode) {
        switch mode {
        case .new:
            selectedHistory = nil
            sessionMode = .new
        case .continue:
            restoreRecentInProject()
        case .resume:
            Task { await showHistory() }
        }
    }

    private func restoreRecentInProject() {
        guard let backend, !installingApplicationUpdate, !isFindingRecentSession else { return }
        guard !workspace.isEmpty else { setError("请先选择项目目录。"); return }
        let directory = workspace
        let client = activeClient
        isFindingRecentSession = true
        Task {
            defer { isFindingRecentSession = false }
            do {
                let data = try await backend.run(["history-list-global", "--workspace", directory, "--client", client.rawValue])
                guard workspace == directory, activeClient == client else { return }
                guard let entry = try JSONDecoder().decode(HistoryListResponse.self, from: data).sessions.first else {
                    setError("当前项目暂无 \(client.title) 对话可恢复。")
                    return
                }
                restoreOriginal(entry)
            } catch { setError(error.localizedDescription) }
        }
    }

    func showHistory() async {
        guard let backend else { return }
        guard !isLoadingHistory else { return }
        guard !workspace.isEmpty else { setError("请先选择项目目录。"); return }
        historyScope = "workspace"
        isHistoryPresented = true
        isLoadingHistory = true
        historyError = ""
        do {
            let data = try await backend.run(["history-list-global", "--workspace", workspace])
            historySessions = try JSONDecoder().decode(HistoryListResponse.self, from: data).sessions
        } catch {
            historyError = error.localizedDescription
        }
        isLoadingHistory = false
    }

    func showGlobalHistory() async {
        guard let backend else { return }
        guard !isLoadingHistory else { return }
        historyScope = "global"
        isHistoryPresented = true
        isLoadingHistory = true
        historyError = ""
        do {
            let data = try await backend.run(["history-list-global"])
            historySessions = try JSONDecoder().decode(HistoryListResponse.self, from: data).sessions
        } catch {
            historyError = error.localizedDescription
        }
        isLoadingHistory = false
    }

    func chooseHistory(_ entry: LocalHistoryEntry) {
        guard historyScope == "workspace" else {
            isHistoryPresented = false
            restoreOriginal(entry)
            return
        }
        activeClient = entry.clientKind
        selectedHistory = entry
        sessionMode = .resume
        isHistoryPresented = false
    }

    private func restoreOriginal(_ entry: LocalHistoryEntry) {
        guard !installingApplicationUpdate else { return }
        var isDirectory: ObjCBool = false
        guard !entry.workspace.isEmpty, FileManager.default.fileExists(atPath: entry.workspace, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            setError("原项目目录不存在或无法访问。")
            return
        }
        if let existing = terminalSessions.first(where: { $0.isRunning && $0.sourceHistoryRef == entry.history_ref }) {
            selectTerminal(existing.id)
            return
        }
        if entry.isGlobal {
            launchGlobal(client: entry.clientKind, history: entry)
            return
        }
        guard ensureAgentPermissions() else { return }
        let originalProfile = profiles.first { $0.id == entry.profile_id }
        guard let originalProfile else {
            setError("原调用配置已移除或无法识别。可在原项目从历史创建分支。")
            return
        }
        launchWorkbench(client: entry.clientKind, profile: originalProfile,
                        model: entry.configured_model ?? entry.model, directory: entry.workspace,
                        targetGateway: entry.gateway, mode: .resume, history: entry, original: true)
    }

    func launchConfirmed() {
        guard !installingApplicationUpdate, let selectedProfile else { return }
        guard ensureAgentPermissions() else { return }
        let mode = pendingSessionMode
        let history = mode == .resume ? selectedHistory : nil
        guard mode != .resume || history != nil else { return }
        launchWorkbench(client: history?.clientKind ?? activeClient, profile: selectedProfile,
                        model: selectedModel, directory: workspace, targetGateway: gateway,
                        mode: mode, history: history)
        pendingSessionMode = .new
        selectedHistory = nil
        sessionMode = .new
    }

    private func launchWorkbench(client: ClientKind, profile: KeyProfile, model: String, directory: String,
                                 targetGateway: String, mode: SessionMode, history: LocalHistoryEntry?,
                                 original: Bool = false) {
        guard let backend, let agentPermissions else { return }
        let action: String
        if original { action = "history-resume-original" }
        else if let history { action = history.isGlobal ? "history-import-launch" : "history-launch" }
        else { action = client == .codex ? "codex-launch" : "launch" }
        let shellPath = ProcessInfo.processInfo.environment["SHELL"].flatMap {
            FileManager.default.isExecutableFile(atPath: $0) ? $0 : nil
        } ?? "/bin/zsh"
        let permissions = agentPermissions.mode(for: client)
        var backendArguments = [backend.scriptURL.path, action, "--workspace", directory,
                                "--permission-mode", permissions.rawValue]
        if !original {
            backendArguments += ["--profile-id", profile.id, "--model", model,
                                 "--session-mode", mode.rawValue, "--gateway-url", targetGateway]
        }
        if let history {
            backendArguments += ["--history-ref", history.history_ref,
                                 "--session-id", history.sessionID, "--client", history.client]
            if !original { backendArguments.append("--fork-history") }
        }
        let backendCommand = ([backend.pythonPath] + backendArguments).map(shellQuote).joined(separator: " ")
        let inheritedPath = ProcessInfo.processInfo.environment["PATH"] ?? "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        let terminalPath = "\(NSHomeDirectory())/.local/bin:\(inheritedPath)"
        let backendStatus = "backend_status=$?; if [ \"$backend_status\" -ne 0 ]; then exit \"$backend_status\"; fi"
        let launch = TerminalLaunchSpec(
            executable: shellPath,
            arguments: ["-l", "-i", "-c", "trap ':' INT; printf '%s\\n' \(shellQuote("权限：" + permissions.label(for: client))); \(backendCommand); \(backendStatus); exec \(shellQuote(shellPath)) -l -i"],
            workspace: directory,
            environment: [
                "HOME=\(NSHomeDirectory())",
                "PATH=\(terminalPath)",
                "SHELL=\(shellPath)",
                "TERM=xterm-256color",
                "COLORTERM=truecolor",
                "LANG=en_US.UTF-8",
            ] + agentNetworkEnvironment(ProcessInfo.processInfo.environment)
        )
        let session = TerminalSession(client: client, profile: profile, model: model,
                                      workspace: directory, sessionMode: mode, gateway: targetGateway,
                                      launchSpec: launch, sourceHistoryRef: original ? history?.history_ref : nil,
                                      permissions: permissions.label(for: client))
        terminalSessions.append(session)
        selectTerminal(session.id)
        if let history {
            statusMessage = original ? "正在用原配置恢复「\(history.title)」。" : "正在从「\(history.title)」创建分支。"
        } else {
            statusMessage = "已创建独立的 \(client.title) 终端会话。"
        }
        isStatusError = false
    }

    func launchGlobal(client: ClientKind, history: LocalHistoryEntry? = nil, directory: String? = nil) {
        guard !installingApplicationUpdate else { return }
        let directory = history?.workspace ?? directory ?? workspace
        var isDirectory: ObjCBool = false
        guard !directory.isEmpty, FileManager.default.fileExists(atPath: directory, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            setError("请选择一个存在的项目目录后再启动系统 CLI。")
            return
        }
        let shellPath = ProcessInfo.processInfo.environment["SHELL"].flatMap {
            FileManager.default.isExecutableFile(atPath: $0) ? $0 : nil
        } ?? "/bin/zsh"
        var command = [client.rawValue]
        if let history {
            guard UUID(uuidString: history.sessionID) != nil else { setError("历史会话标识无效。"); return }
            command += [client == .codex ? "resume" : "--resume", history.sessionID]
        }
        let shellCommand = "cd \(shellQuote(directory)) && \(command.map(shellQuote).joined(separator: " ")); exec \(shellQuote(shellPath)) -l -i"
        var inherited = ProcessInfo.processInfo.environment
        // These describe our actual PTY, regardless of how Finder or another
        // terminal launched the app. Keep CLI homes, credentials and proxies.
        inherited["TERM"] = "xterm-256color"
        inherited["COLORTERM"] = "truecolor"
        let launch = TerminalLaunchSpec(
            executable: shellPath,
            arguments: ["-l", "-i", "-c", shellCommand],
            workspace: directory,
            environment: inherited.map { "\($0.key)=\($0.value)" }
        )
        let session = TerminalSession(client: client, profile: nil, model: "系统配置",
                                      workspace: directory, sessionMode: history == nil ? .new : .resume,
                                      gateway: "系统环境", launchSpec: launch, isGlobal: true, sourceHistoryRef: history?.history_ref)
        terminalSessions.append(session)
        selectTerminal(session.id)
        statusMessage = history.map { "正在用系统 CLI 恢复「\($0.title)」。" } ?? "已用系统配置启动 \(client.title)。"
        isStatusError = false
    }

    func processDidExit(sessionID: UUID, exitCode: Int32?) {
        guard let session = terminalSessions.first(where: { $0.id == sessionID }) else { return }
        session.processDidExit(exitCode)
        Task { await refreshUsage() }
        if let exitCode, exitCode != 0 {
            setError("\(session.client.title) 已退出，状态码 \(exitCode)。")
        } else {
            statusMessage = "\(session.client.title) 会话已结束。"
            isStatusError = false
        }
    }

    func closeSession(_ session: TerminalSession) {
        if session.isRunning { session.stop() }
        if selectedTerminalID == session.id {
            selectTerminal(terminalSessions.last(where: { $0.id != session.id })?.id)
        }
        terminalSessions.removeAll { $0.id == session.id }
    }

    private func setError(_ message: String) {
        statusMessage = message
        isStatusError = true
    }
}

