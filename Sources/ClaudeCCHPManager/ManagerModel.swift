import AppKit
import Combine
import Foundation

struct KeyProfile: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
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

private struct ModelResponse: Decodable {
    let models: [String]
}

private struct AddedProfile: Decodable {
    let id: String
    let name: String
    let model_count: Int
}

enum SessionMode: String, CaseIterable, Identifiable {
    case new
    case `continue`
    case resume

    var id: String { rawValue }

    var title: String {
        switch self {
        case .new: "新会话"
        case .continue: "继续最近会话"
        case .resume: "选择历史会话"
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

private struct BackendError: LocalizedError, Sendable {
    let message: String
    var errorDescription: String? { message }
}

struct TerminalLaunchSpec: Equatable {
    let id = UUID()
    let executable: String
    let arguments: [String]
    let workspace: String
    let environment: [String]
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
        let candidates = (ProcessInfo.processInfo.environment["PATH"] ?? "")
            .split(separator: ":")
            .map { String($0) + "/python3" }
            + ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3",
               NSHomeDirectory() + "/miniconda3/bin/python3"]
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
    @Published var activeClient: ClientKind = .claude {
        didSet {
            UserDefaults.standard.set(activeClient.rawValue, forKey: "client.kind")
            statusMessage = activeClient == .codex
                ? "Codex 使用独立 CODEX_HOME，不会修改原配置。"
                : "Claude 使用隔离设置、MCP 和会话目录。"
            isStatusError = false
        }
    }
    @Published var profiles: [KeyProfile] = []
    @Published var selectedProfileID = ""
    @Published var models: [String] = []
    @Published var selectedModel = ""
    @Published var modelSearch = ""
    @Published var workspace = ""
    @Published var workspaces: [String] = []
    @Published var sessionMode: SessionMode = .new
    @Published var gateway = "http://127.0.0.1:8081"
    @Published var isLoading = true
    @Published var isLoadingModels = false
    @Published var isModelPickerPresented = false
    @Published var isAddProfilePresented = false
    @Published var isAddingProfile = false
    @Published var isRestoreConfirmationPresented = false
    @Published var addProfileError = ""
    @Published var addProfileStatus = ""
    @Published var statusMessage = "正在读取本机配置…"
    @Published var isStatusError = false
    @Published var embeddedLaunchSpec: TerminalLaunchSpec?
    @Published var agentRunning = false

    private var backend: BackendClient?
    private var pendingSessionMode: SessionMode = .new

    init() {
        activeClient = ClientKind(rawValue: UserDefaults.standard.string(forKey: "client.kind") ?? "") ?? .claude
    }

    var selectedProfile: KeyProfile? {
        profiles.first { $0.id == selectedProfileID }
    }

    var filteredModels: [String] {
        guard !modelSearch.isEmpty else { return models }
        return models.filter { $0.localizedCaseInsensitiveContains(modelSearch) }
    }

    func load() async {
        do {
            let client = try BackendClient()
            backend = client
            let data = try await client.run(["gui-state"])
            let state = try JSONDecoder().decode(GatewayState.self, from: data)
            gateway = state.gateway
            profiles = state.profiles
            workspaces = state.workspaces

            let last = state.last_launch
            if let last, profiles.contains(where: { $0.id == last.profile_id }) {
                activeClient = ClientKind(rawValue: last.client ?? "claude") ?? .claude
                selectedProfileID = last.profile_id
                workspace = last.workspace
                selectedModel = last.model
                sessionMode = SessionMode(rawValue: last.session_mode) ?? .new
            } else {
                selectedProfileID = profiles.first?.id ?? ""
                workspace = state.workspaces.first ?? NSHomeDirectory()
            }
            isLoading = false
            if !selectedProfileID.isEmpty {
                await loadModels(for: selectedProfileID)
            } else {
                statusMessage = "添加一把本机 CCHP 调用 Key 后开始使用。"
            }
        } catch {
            isLoading = false
            setError(error.localizedDescription)
        }
    }

    func selectProfile(_ profile: KeyProfile) async {
        guard selectedProfileID != profile.id else { return }
        selectedProfileID = profile.id
        selectedModel = ""
        await loadModels(for: profile.id)
    }

    func loadModels(for profileID: String) async {
        guard let backend else { return }
        isLoadingModels = true
        do {
            let data = try await backend.run(["profile-models", "--profile-id", profileID])
            let response = try JSONDecoder().decode(ModelResponse.self, from: data)
            models = response.models.sorted()
            if !models.contains(selectedModel) {
                selectedModel = models.first ?? ""
            }
            statusMessage = "\(models.count) 个可用模型"
            isStatusError = false
        } catch {
            models = []
            selectedModel = ""
            setError(error.localizedDescription)
        }
        isLoadingModels = false
    }

    func addProfile(name: String, key: String) async -> Bool {
        guard let backend else {
            addProfileError = "本机后端尚未就绪。"
            return false
        }
        isAddingProfile = true
        addProfileError = ""
        addProfileStatus = "正在仅查询模型白名单…"
        do {
            let payload = try JSONSerialization.data(withJSONObject: ["name": name, "key": key])
            let data = try await backend.run(["add-profile-stdin"], stdin: payload)
            let added = try JSONDecoder().decode(AddedProfile.self, from: data)
            try await refreshProfiles()
            selectedProfileID = added.id
            selectedModel = ""
            await loadModels(for: added.id)
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
            workspace = url.standardizedFileURL.path
            rememberWorkspace(workspace)
        }
    }

    func setWorkspace(_ path: String) {
        guard FileManager.default.fileExists(atPath: path) else { return }
        workspace = URL(fileURLWithPath: path).standardizedFileURL.path
        rememberWorkspace(workspace)
    }

    private func rememberWorkspace(_ path: String) {
        workspaces.removeAll { $0 == path }
        workspaces.insert(path, at: 0)
        workspaces = Array(workspaces.prefix(12))
    }

    func requestLaunch() {
        guard !agentRunning else {
            setError("当前 agent 仍在运行；请先停止它，再启动新会话。")
            return
        }
        guard !selectedProfileID.isEmpty, !selectedModel.isEmpty, !workspace.isEmpty else {
            setError("请选择调用 Key、模型和工作目录。")
            return
        }
        if sessionMode == .new {
            launchConfirmed()
        } else {
            pendingSessionMode = sessionMode
            isRestoreConfirmationPresented = true
        }
    }

    func launchConfirmed() {
        guard !agentRunning, let backend, let selectedProfile else { return }
        let mode = pendingSessionMode == .new ? sessionMode : pendingSessionMode
        let action = activeClient == .codex ? "codex-launch" : "launch"
        embeddedLaunchSpec = TerminalLaunchSpec(
            executable: backend.pythonPath,
            arguments: [backend.scriptURL.path, action,
                        "--profile-id", selectedProfile.id,
                        "--model", selectedModel,
                        "--workspace", workspace,
                        "--session-mode", mode.rawValue],
            workspace: workspace,
            environment: [
                "HOME=\(NSHomeDirectory())",
                "PATH=\(ProcessInfo.processInfo.environment["PATH"] ?? "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")",
                "TERM=xterm-256color",
                "COLORTERM=truecolor",
                "LANG=en_US.UTF-8",
                "NO_PROXY=127.0.0.1,localhost,::1",
                "no_proxy=127.0.0.1,localhost,::1",
            ]
        )
        agentRunning = true
        statusMessage = "\(activeClient.title) 已在内嵌终端启动。"
        isStatusError = false
    }

    func processDidExit(_ exitCode: Int32?) {
        agentRunning = false
        if let exitCode, exitCode != 0 {
            setError("\(activeClient.title) 已退出，状态码 \(exitCode)。")
        } else {
            statusMessage = "\(activeClient.title) 会话已结束。"
            isStatusError = false
        }
    }

    private func setError(_ message: String) {
        statusMessage = message
        isStatusError = true
    }
}

