import Foundation

/// Model metadata only. Each key is scoped to both profile and gateway.
@MainActor
final class ModelCatalogCache {
    struct Entry: Codable {
        var models: [String] = []
        var selected = ""
    }

    private let defaults: UserDefaults
    private let storageKey = "models.catalog.v1"
    private var entries: [String: Entry]

    init(defaults: UserDefaults) {
        self.defaults = defaults
        entries = defaults.data(forKey: storageKey).flatMap {
            try? JSONDecoder().decode([String: Entry].self, from: $0)
        } ?? [:]
    }

    private func key(_ profile: String, _ gateway: String) -> String {
        profile + "|" + gateway.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    func entry(profile: String, gateway: String) -> Entry {
        entries[key(profile, gateway)] ?? Entry()
    }

    func remember(profile: String, gateway: String, selected: String, models: [String]? = nil) {
        guard !profile.isEmpty else { return }
        let scope = key(profile, gateway)
        var entry = entries[scope] ?? Entry()
        if let models { entry.models = models }
        entry.selected = selected
        entries[scope] = entry
        if let data = try? JSONEncoder().encode(entries) { defaults.set(data, forKey: storageKey) }
    }
}
