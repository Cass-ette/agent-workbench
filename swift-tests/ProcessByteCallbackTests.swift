import Foundation
import AppKit
import XCTest
@testable import SwiftTerm
@testable import ClaudeCCHPManager

final class ProcessByteCallbackTests: XCTestCase {
    @MainActor
    func testSustainedOutputThroughRealPTYAndReplay() async throws {
        let view = FileDropTerminalView(frame: CGRect(x: 0, y: 0, width: 640, height: 400), options: TerminalOptions())
        let control = EmbeddedTerminalControl()
        control.attach(view)
        let batches = Locked(0)
        let replay = control.replay
        view.setProcessByteHandler { bytes in
            replay.append(bytes)
            batches.withLock { $0 += 1 }
        }
        let script = "import os,time\nfor i in range(12000):\n os.write(1,b'workbench-output-batch\\r\\n'); time.sleep(.0015)\nos.write(1,b'PTY_STRESS_COMPLETE\\r\\n')"
        view.startProcess(executable: "/usr/bin/python3", args: ["-u", "-c", script], environment: ["TERM=xterm-256color"])
        defer { view.terminate() }
        let deadline = Date().addingTimeInterval(40)
        var complete = false
        while Date() < deadline {
            let end = replay.snapshot(after: .max, client: "")["after"] as? Int ?? 0
            let chunk = replay.snapshot(after: max(0, end - 256), client: "")["data"] as? String ?? ""
            if String(decoding: Data(base64Encoded: chunk) ?? Data(), as: UTF8.self).contains("PTY_STRESS_COMPLETE") {
                complete = true
                break
            }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertTrue(complete, "Continuous output must finish without losing the PTY reader")
        XCTAssertGreaterThan(batches.withLock { $0 }, 8_000, "Exercise more deliveries than the original stack overflow")
    }

    func testRepeatedReadsOnPTYSizeStackDoNotAccumulateCallbackWrappers() {
        let callback = LockedProcessByteCallback()
        let received = Locked(0)
        callback.replace { bytes in
            received.withLock { $0 += bytes == [65] ? 1 : -1 }
        }
        let finished = DispatchSemaphore(value: 0)
        let worker = Thread {
            for index in 0..<50_000 {
                if index.isMultiple(of: 2) {
                    callback.call([65][...])
                } else {
                    [UInt8(65)].withUnsafeBufferPointer { callback.call($0.span) }
                }
            }
            finished.signal()
        }
        worker.stackSize = 524_288
        worker.start()
        XCTAssertEqual(finished.wait(timeout: .now() + 30), .success)
        XCTAssertEqual(received.withLock { $0 }, 50_000)
    }

    func testCallbackCanReplaceAndRemoveItselfDuringDelivery() {
        let callback = LockedProcessByteCallback()
        let received = Locked([UInt8]())
        callback.replace { bytes in
            received.withLock { $0.append(contentsOf: bytes) }
            callback.replace { bytes in
                received.withLock { $0.append(contentsOf: bytes) }
                callback.replace(with: nil)
            }
        }
        callback.call([1][...])
        callback.call([2][...])
        callback.call([3][...])
        XCTAssertEqual(received.withLock { $0 }, [1, 2])
    }
}
