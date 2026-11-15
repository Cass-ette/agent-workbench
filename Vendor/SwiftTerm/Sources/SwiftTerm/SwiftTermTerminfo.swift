// The app uses standard shell capabilities; an empty table lets the terminal
// answer unsupported XTGETTCAP queries without a generated package plugin.
enum SwiftTermTerminfo {
    static let xtgettcapReplies: [String: String] = [:]
}
