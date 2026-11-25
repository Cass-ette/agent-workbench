import Foundation
import SwiftUI

enum ReasoningEffort {
    static let levels = ["default", "low", "medium", "high", "xhigh", "max"]
    static func label(_ value: String) -> String { value == "default" ? "客户端默认" : value }
    static func command(client: ClientKind, level: String) -> String? {
        guard levels.contains(level) else { return nil }
        if level == "default" { return client == .codex ? "/model" : "/effort" }
        return client == .codex ? "/model --reasoning=\(level)" : "/effort \(level)"
    }
    static func key(client: ClientKind, profile: String, model: String) -> String {
        String(decoding: try! JSONEncoder().encode([client.rawValue, profile, model]), as: UTF8.self)
    }
}

struct ReasoningEffortSlider: View {
    @Binding var value: String
    var label = "思考强度"
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text(label).font(.headline); Spacer(); Text(ReasoningEffort.label(value)).monospaced() }
            Slider(value: Binding(get: { Double(ReasoningEffort.levels.firstIndex(of: value) ?? 0) },
                                  set: { value = ReasoningEffort.levels[Int($0.rounded())] }),
                   in: 0...Double(ReasoningEffort.levels.count - 1), step: 1)
                .accessibilityLabel(label)
            HStack {
                ForEach(ReasoningEffort.levels, id: \.self) { level in
                    Button(level == "default" ? "默认" : level) { value = level }
                        .buttonStyle(.plain).font(.caption2).frame(maxWidth: .infinity)
                        .foregroundStyle(value == level ? Color.accentColor : Color.secondary)
                }
            }
            Text("通用档位；具体支持范围由客户端、模型和上游决定。较高强度通常需要更多时间和 Token。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct CurrentSessionReasoning: View {
    @ObservedObject var model: ManagerModel
    let session: TerminalSession
    @State private var presented = false
    @State private var level = "high"
    var body: some View {
        Button { presented.toggle() } label: { Label("思考强度", systemImage: "slider.horizontal.3") }
            .buttonStyle(.borderless)
            .disabled(!session.isRunning)
            .popover(isPresented: $presented) {
                VStack(alignment: .leading, spacing: 14) {
                    ReasoningEffortSlider(value: $level, label: "当前会话 · \(session.client.title)")
                    Text("明确档位只调整强度，保留模型；客户端默认会打开原生设置。命令会填入当前终端，请在 CLI 输入框确认后按回车；由客户端确认生效。")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(ReasoningEffort.command(client: session.client, level: level) ?? "").font(.system(.caption, design: .monospaced))
                    if session.client == .codex {
                        Text("Codex 原生目录未收录的第三方模型可能拒绝 /model。若提示 Model unavailable，请设置该模型的启动默认强度，再自行结束并按原配置恢复对话；无需创建分支。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if session.client == .claude {
                        Text("Claude 可能把 /effort 选择保存为其默认值；系统 CLI 会话也遵循客户端自身规则。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Button("填入当前终端") {
                        model.insertReasoningCommand(sessionID: session.id, level: level)
                        presented = false
                    }.buttonStyle(.borderedProminent)
                }.padding(18).frame(width: 350)
            }
    }
}
