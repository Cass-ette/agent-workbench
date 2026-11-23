import XCTest
import AppKit
import SwiftTerm
@testable import ClaudeCCHPManager

final class RemoteWorkbenchTests: XCTestCase {
    @MainActor
    func testRemoteInputAndReplayUseTheExistingDesktopPTY() async throws {
        let view = FileDropTerminalView(frame: CGRect(x: 0, y: 0, width: 640, height: 400), options: TerminalOptions())
        let control = EmbeddedTerminalControl()
        control.attach(view)
        view.startProcess(executable: "/bin/cat", args: [], environment: ["TERM=xterm-256color"])
        defer { view.terminate() }
        control.detach(view)
        XCTAssertTrue(control.existingView === view)
        control.replay.claim("phone")
        try control.remoteResize(cols: 61, rows: 19, client: "phone")
        XCTAssertEqual(view.getWindowSize().ws_col, 61)
        try control.remoteInput(Data("shared-pty-check\n".utf8), client: "phone", inputID: "one")
        var output = ""
        for _ in 0..<100 {
            let snapshot = control.replay.snapshot(after: 0, client: "phone")
            output = String(decoding: Data(base64Encoded: snapshot["data"] as? String ?? "") ?? Data(), as: UTF8.self)
            if output.contains("shared-pty-check") { break }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertTrue(output.contains("shared-pty-check"))
        control.attach(view)
        view.send(source: view, data: Array("local-input\n".utf8)[...])
        XCTAssertFalse(control.replay.hasRemoteControl)
    }
    @MainActor
    func testAttachmentIsOrderedAndBoundToItsSession() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = RemoteAttachments(root: root)
        let id = UUID().uuidString
        let first: [String: Any] = ["upload_id": id, "name": "../../example.png", "size": 4, "offset": 0, "data": Data([1,2]).base64EncodedString()]
        XCTAssertEqual(try store.receive(first, session: "a", owner: "phone")["complete"] as? Bool, false)
        XCTAssertThrowsError(try store.receive(first, session: "b", owner: "phone"))
        var last = first; last["offset"] = 2; last["data"] = Data([3,4]).base64EncodedString()
        XCTAssertEqual(try store.receive(last, session: "a", owner: "phone")["complete"] as? Bool, true)
        let path = try store.path(id: id, session: "a", owner: "phone")
        XCTAssertEqual(URL(fileURLWithPath: path).deletingLastPathComponent().path, root.path)
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: path)), Data([1,2,3,4]))
        XCTAssertThrowsError(try store.path(id: id, session: "a", owner: "other"))
    }
    func testReplayIsBoundedAndControlDoesNotRepeatInput() throws {
        let buffer = RemoteTerminalBuffer()
        buffer.append(Array(repeating: 65, count: 1_048_580))
        let first = buffer.snapshot(after: 0, client: "phone")
        XCTAssertEqual(first["truncated"] as? Bool, true)
        XCTAssertEqual(first["after"] as? Int, 65_540)
        XCTAssertEqual(first["can_write"] as? Bool, false)
        buffer.claim("phone")
        XCTAssertTrue(try buffer.requireControl("phone", inputID: "1"))
        XCTAssertFalse(try buffer.requireControl("phone", inputID: "1"))
        XCTAssertThrowsError(try buffer.requireControl("other", inputID: "2"))
        buffer.release()
        XCTAssertThrowsError(try buffer.requireControl("phone", inputID: "2"))
        XCTAssertEqual(buffer.snapshot(after: 999999999, client: "phone")["after"] as? Int, 1_048_580)
    }

    func testHTTPRequiresPrivateTokenAndDoesNotExposeIt() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let endpoint = directory.appendingPathComponent("remote.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let server = try RemoteHTTPServer(endpoint: endpoint) { _ in Data("{\"ok\":true,\"value\":{\"schema\":1}}".utf8) }
        defer { server.stop() }
        for _ in 0..<100 {
            if FileManager.default.fileExists(atPath: endpoint.path) { break }
            try await Task.sleep(for: .milliseconds(20))
        }
        let info = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(contentsOf: endpoint)) as? [String: Any])
        let token = try XCTUnwrap(info["token"] as? String)
        let port = try XCTUnwrap(info["port"] as? Int)
        let attributes = try FileManager.default.attributesOfItem(atPath: endpoint.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/rpc")!)
        request.httpMethod = "POST"; request.httpBody = Data("{\"op\":\"ping\"}".utf8)
        let (denied, status) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((status as? HTTPURLResponse)?.statusCode, 403)
        XCTAssertFalse(String(decoding: denied, as: UTF8.self).contains(token))
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        let (allowed, allowedStatus) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((allowedStatus as? HTTPURLResponse)?.statusCode, 200)
        XCTAssertEqual(try (JSONSerialization.jsonObject(with: allowed) as? [String: Any])?["ok"] as? Bool, true)
        server.stop()
        XCTAssertFalse(FileManager.default.fileExists(atPath: endpoint.path))
    }
}
