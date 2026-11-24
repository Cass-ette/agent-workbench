import SwiftUI
import Charts

enum UsageTokenSeries: String, CaseIterable, Identifiable {
    case both, input, output
    var id: String { rawValue }
    var label: String {
        switch self {
        case .both: return "输入与输出"
        case .input: return "仅看输入"
        case .output: return "仅看输出"
        }
    }
}

struct UsageChart: View {
    let points: [UsagePoint]
    let money: Bool
    let series: UsageTokenSeries

    var body: some View {
        Chart(points) { point in
            if money {
                if let cost = point.estimated_cost {
                    BarMark(x: .value("分组", point.date), y: .value("USD", cost)).foregroundStyle(.orange)
                }
            } else {
                if series != .output, let input = point.input_tokens {
                    BarMark(x: .value("分组", point.date), y: .value("Token", input), stacking: .unstacked)
                        .position(by: .value("类型", "输入"))
                        .foregroundStyle(by: .value("类型", "输入"))
                        .accessibilityLabel("\(point.date) 输入")
                        .accessibilityValue(input.formatted())
                }
                if series != .input, let output = point.output_tokens {
                    BarMark(x: .value("分组", point.date), y: .value("Token", output), stacking: .unstacked)
                        .position(by: .value("类型", "输出"))
                        .foregroundStyle(by: .value("类型", "输出"))
                        .accessibilityLabel("\(point.date) 输出")
                        .accessibilityValue(output.formatted())
                }
            }
        }
        .chartForegroundStyleScale(domain: ["输入", "输出"], range: [Color(red: 102/255, green: 141/255, blue: 240/255), Color(red: 215/255, green: 144/255, blue: 69/255)])
        .chartLegend(money || series != .both ? .hidden : .visible)
    }
}

func projectMatches(_ workspace: String, _ project: String, _ children: Bool = false) -> Bool {
    if project.isEmpty { return true }
    if project == "__unknown" { return workspace.isEmpty }
    func normalize(_ value: String) -> String {
        var result = value
        while result.count > 1 && result.hasSuffix("/") { result.removeLast() }
        return result
    }
    let root = normalize(project), path = normalize(workspace)
    return !workspace.isEmpty && (path == root || (children && !path.isEmpty && path.hasPrefix(root == "/" ? "/" : root + "/")))
}

struct UsageAnalyticsView: View {
    @ObservedObject var model: ManagerModel
    @State private var sessionID = ""
    @State private var project = ""
    @State private var includesChildren = false
    @State private var profile = ""
    @State private var client = ""
    @State private var selectedModel = ""
    @State private var group = "date"
    @State private var period = "all"
    @State private var money = false
    @State private var tokenSeries: UsageTokenSeries = .both
    @State private var start = Calendar.current.date(byAdding: .day, value: -30, to: Date()) ?? Date()
    @State private var end = Date()

    private func day(_ date: Date) -> String {
        let format = DateFormatter(); format.timeZone = TimeZone(secondsFromGMT: 0); format.dateFormat = "yyyy-MM-dd"
        return format.string(from: date)
    }
    private func records(_ session: UsageSession) -> [UsagePoint] {
        (session.usage_records ?? session.timeline ?? [UsagePoint(date: "", model: session.model, input_tokens: session.input_tokens, output_tokens: session.output_tokens,
            cached_input_tokens: session.cached_input_tokens, cache_creation_input_tokens: session.cache_creation_input_tokens,
            reasoning_output_tokens: session.reasoning_output_tokens, estimated_cost: session.estimated_cost, unpriced_records: nil)])
        .map { point in
            var result = point
            result.session_id = result.session_id ?? session.id
            result.client = result.client ?? session.client
            result.project_path = result.project_path ?? session.workspace
            result.profile_id = result.profile_id ?? session.profile_id ?? ""
            result.profile_name = result.profile_name ?? session.profile_name ?? ""
            result.model = result.model ?? session.model
            return result
        }
    }
    private var allRecords: [UsagePoint] { model.usage.sessions.flatMap(records) }
    private var filteredRecords: [UsagePoint] { allRecords.filter {
        projectMatches($0.project_path ?? "", project, includesChildren) &&
        (profile.isEmpty || (profile == "__unknown" ? ($0.profile_id ?? "").isEmpty : $0.profile_id == profile)) &&
        (client.isEmpty || $0.client == client) && (selectedModel.isEmpty || $0.model == selectedModel)
    } }
    private var scoped: [UsageSession] { model.usage.sessions.filter { session in filteredRecords.contains { $0.session_id == session.id } } }
    private var sessions: [UsageSession] { scoped.filter { sessionID.isEmpty || $0.id == sessionID } }
    private var projects: [String] { Array(Set(allRecords.compactMap(\.project_path).filter { !$0.isEmpty })).sorted() }
    private var profiles: [String] { Array(Set(allRecords.compactMap(\.profile_id).filter { !$0.isEmpty })).sorted() }
    private var models: [String] { Array(Set(allRecords.compactMap(\.model).filter { !$0.isEmpty })).sorted() }
    private var lower: String {
        period == "custom" ? day(start) : period == "all" ? "" : day(Date().addingTimeInterval(-Double((Int(period) ?? 1) - 1) * 86400))
    }
    private var upper: String { period == "custom" ? day(end) : day(Date()) }
    private var points: [UsagePoint] {
        let rows = filteredRecords.filter { sessionID.isEmpty || $0.session_id == sessionID }
            .filter { period == "all" || (!$0.date.isEmpty && $0.date >= lower && $0.date <= upper) }
        return Dictionary(grouping: rows, by: groupKey).map { date, rows in
            func total(_ key: KeyPath<UsagePoint, Int?>) -> Int? { let known = rows.compactMap { $0[keyPath: key] }; return known.isEmpty ? nil : known.reduce(0, +) }
            let costs = rows.compactMap(\.estimated_cost)
            return UsagePoint(date: date, input_tokens: total(\.input_tokens), output_tokens: total(\.output_tokens),
                cached_input_tokens: total(\.cached_input_tokens), cache_creation_input_tokens: total(\.cache_creation_input_tokens),
                reasoning_output_tokens: total(\.reasoning_output_tokens), estimated_cost: costs.isEmpty ? nil : costs.reduce(0, +), unpriced_records: total(\.unpriced_records), unit_prices: Array(Set(rows.flatMap { $0.unit_prices ?? [] })))
        }.sorted { $0.date < $1.date }
    }
    private func groupKey(_ row: UsagePoint) -> String {
        switch group {
        case "project": return (row.project_path ?? "").isEmpty ? "未关联项目" : row.project_path!
        case "profile": return (row.profile_id ?? "").isEmpty ? "未识别配置" : row.profile_id!
        case "model": return (row.model ?? "").isEmpty ? "模型未记录" : row.model!
        case "client": return row.client ?? "客户端未记录"
        case "session": return row.session_id ?? "会话未记录"
        default: return row.date
        }
    }
    private func groupLabel(_ key: String) -> String {
        if group == "profile", let name = allRecords.first(where: { $0.profile_id == key })?.profile_name, !name.isEmpty { return name }
        return key
    }
    private var hasCost: Bool { points.contains { $0.estimated_cost != nil } }
    private var showMoney: Bool { money && hasCost }
    private func count(_ key: KeyPath<UsagePoint, Int?>) -> String {
        let values = points.compactMap { $0[keyPath: key] }; return values.isEmpty ? "—" : values.reduce(0, +).formatted()
    }
    private var cost: Double? { let values = points.compactMap(\.estimated_cost); return values.isEmpty ? nil : values.reduce(0, +) }
    private var missing: Int { points.reduce(0) { $0 + ($1.unpriced_records ?? 0) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text("用量与费用").font(.headline); Spacer(); Button("刷新") { Task { await model.refreshUsage() } }.disabled(model.isLoadingUsage) }
            Picker("项目目录", selection: $project) {
                Text("全部项目").tag(""); Text("未关联项目").tag("__unknown")
                ForEach(projects, id: \.self) { Text($0).tag($0) }
            }.onChange(of: project) { _, _ in sessionID = "" }
            if !project.isEmpty && project != "__unknown" { Toggle("包含子目录", isOn: $includesChildren).toggleStyle(.checkbox).onChange(of: includesChildren) { _, _ in sessionID = "" } }
            HStack {
                Picker("配置档案", selection: $profile) { Text("全部配置").tag(""); Text("未识别配置").tag("__unknown")
                    ForEach(profiles, id: \.self) { id in Text(allRecords.first(where: { $0.profile_id == id })?.profile_name.flatMap { $0.isEmpty ? nil : $0 } ?? id).tag(id) }
                }.onChange(of: profile) { _, _ in sessionID = "" }
                Picker("客户端", selection: $client) { Text("全部").tag(""); Text("Codex").tag("codex"); Text("Claude").tag("claude") }.onChange(of: client) { _, _ in sessionID = "" }
            }
            Picker("模型", selection: $selectedModel) { Text("全部模型").tag(""); ForEach(models, id: \.self) { Text($0).tag($0) } }.onChange(of: selectedModel) { _, _ in sessionID = "" }
            Picker("会话", selection: $sessionID) {
                Text("全部会话").tag("")
                ForEach(scoped) { session in
                    Text("\(session.client) · \(URL(fileURLWithPath: session.workspace).lastPathComponent) · \(session.model) · \(session.id.suffix(8))").tag(session.id)
                }
            }
            Picker("统计视角", selection: $group) { Text("每日趋势").tag("date"); Text("按项目").tag("project"); Text("按配置").tag("profile"); Text("按模型").tag("model"); Text("按客户端").tag("client"); Text("按会话").tag("session") }
            Picker("时间范围", selection: $period) { Text("全部").tag("all"); Text("今天").tag("1"); Text("近7天").tag("7"); Text("近30天").tag("30"); Text("自定义").tag("custom") }.pickerStyle(.segmented)
            if period == "custom" { HStack { DatePicker("开始", selection: $start, displayedComponents: .date); DatePicker("结束", selection: $end, displayedComponents: .date) }.environment(\.timeZone, TimeZone(secondsFromGMT: 0)!) }
            if lower > upper { Text("开始日期不能晚于结束日期").foregroundStyle(.orange) }
            HStack { Text("输入 \(count(\.input_tokens)) · 输出 \(count(\.output_tokens))"); Spacer(); Text(usageCostLabel(cost, upper: nil, label: missing > 0 ? "已提供部分费用" : "上游费用／按上游单价计算")) }.font(.caption.monospacedDigit())
            Text("缓存读 \(count(\.cached_input_tokens)) / 写 \(count(\.cache_creation_input_tokens)) · 推理 \(count(\.reasoning_output_tokens))").font(.caption).foregroundStyle(.secondary)
            Picker("图表指标", selection: $money) { Text("Token").tag(false); if hasCost { Text("费用 USD").tag(true) } }.pickerStyle(.segmented)
            if !showMoney {
                Picker("Token 系列", selection: $tokenSeries) { ForEach(UsageTokenSeries.allCases) { Text($0.label).tag($0) } }.pickerStyle(.segmented)
                Text(tokenSeries == .both ? "输入蓝／输出橙，并列同刻度；量级相差较大时可仅看输出。" : "单独按所选系列缩放；上方用量汇总不变。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ScrollView(.horizontal) {
                UsageChart(points: points.filter { !$0.date.isEmpty }, money: showMoney, series: tokenSeries)
                    .frame(width: CGFloat(max(500, points.count * 48)), height: 180).padding(.bottom, 4)
            }
            if group != "date" { ScrollView { LazyVStack(alignment: .leading, spacing: 5) { ForEach(points) { row in
                Text("\(groupLabel(row.date)) · 输入 \(usageCount(row.input_tokens)) · 输出 \(usageCount(row.output_tokens)) · \(usageCostLabel(row.estimated_cost, upper: nil, label: "费用"))").font(.caption).textSelection(.enabled)
            } } }.frame(maxHeight: 110) }
            if points.isEmpty { Text("所选范围没有用量记录").font(.caption).foregroundStyle(.secondary) }
            if group == "date", let unknown = points.first(where: { $0.date.isEmpty }) { Text("日期未知：输入 \(usageCount(unknown.input_tokens)) · 输出 \(usageCount(unknown.output_tokens))；已纳入全部，未绘入日期图。").font(.caption).foregroundStyle(.secondary) }
            if missing > 0 { Text("\(missing) 条记录缺少费用或单价；费用合计仅包含已知部分。").font(.caption).foregroundStyle(.orange) }
            if points.contains(where: { !($0.unit_prices ?? []).isEmpty }) {
                DisclosureGroup("上游提供的单价 · USD／百万 Token") {
                    ForEach(Array(Set(points.flatMap { $0.unit_prices ?? [] })), id: \.self) { price in
                        Text("\(price.model) · 输入 \(price.charges["prompt"].map { String($0) } ?? "未提供") · 输出 \(price.charges["completion"].map { String($0) } ?? "未提供") · 缓存读 \(price.charges["cache_read"].map { String($0) } ?? "未提供")／写 \(price.charges["cache_write"].map { String($0) } ?? "未提供")").font(.caption)
                    }
                }.font(.caption)
            }
            Text(model.usage.pricingNotice ?? "上游未提供费用时显示未提供。日期按 UTC。").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if !model.usageError.isEmpty { Text(model.usageError).foregroundStyle(.orange) }
            if model.usage.unreadableFiles > 0 { Text("\(model.usage.unreadableFiles) 个日志无法读取，统计不完整").font(.caption).foregroundStyle(.orange) }
            ScrollView { LazyVStack(alignment: .leading, spacing: 6) { ForEach(sessions) { session in
                Button { sessionID = session.id } label: { VStack(alignment: .leading) { Text("\(session.client) · \(session.model) · \(session.id.suffix(8))"); Text(session.workspace).foregroundStyle(.secondary).lineLimit(1) } }.buttonStyle(.plain)
            } } }.frame(maxHeight: 100)
        }
    }
}
