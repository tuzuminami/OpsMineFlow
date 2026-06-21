import AppKit
import CoreGraphics
import Darwin
import Foundation

private struct Configuration {
    let apiPort: Int
    let sessionID: String
    let stopFile: String
    let parentPID: Int32
    let interval: TimeInterval

    static func load(arguments: [String]) throws -> Configuration {
        func value(after flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), arguments.indices.contains(index + 1) else { return nil }
            return arguments[index + 1]
        }

        guard
            let apiPortValue = value(after: "--api-port"),
            let apiPort = Int(apiPortValue),
            let sessionID = value(after: "--session-id"),
            let stopFile = value(after: "--stop-file"),
            let parentValue = value(after: "--parent-pid"),
            let parentPID = Int32(parentValue)
        else {
            throw AgentError.invalidArguments
        }
        let interval = max(Double(value(after: "--interval") ?? "2") ?? 2, 0.5)
        return Configuration(
            apiPort: apiPort,
            sessionID: sessionID,
            stopFile: stopFile,
            parentPID: parentPID,
            interval: interval
        )
    }
}

private enum AgentError: Error {
    case invalidArguments
}

private struct AppSnapshot: Equatable {
    let name: String
    let bundleID: String
}

private struct EventPayload: Encodable {
    let session_id: String
    let sequence: Int
    let app_name: String
    let app_bundle_id: String
    let timestamp_start: String
    let timestamp_end: String
    let duration_seconds: Double
}

private struct HeartbeatPayload: Encodable {
    let session_id: String
    let current_app: String
}

private final class HTTPResult: @unchecked Sendable {
    var succeeded = false
}

private final class LocalAPIClient {
    private let baseURL: URL
    private let token: String
    private let encoder = JSONEncoder()

    init(apiPort: Int, token: String) {
        baseURL = URL(string: "http://127.0.0.1:\(apiPort)")!
        self.token = token
    }

    func post<T: Encodable>(_ path: String, payload: T) -> Bool {
        guard let body = try? encoder.encode(payload) else { return false }
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = 2
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(token, forHTTPHeaderField: "X-OpsMineFlow-Session")

        let result = HTTPResult()
        let semaphore = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { _, response, _ in
            if let httpResponse = response as? HTTPURLResponse {
                result.succeeded = (200..<300).contains(httpResponse.statusCode)
            }
            semaphore.signal()
        }.resume()
        _ = semaphore.wait(timeout: .now() + 3)
        return result.succeeded
    }
}

private func iso8601(_ date: Date) -> String {
    ISO8601DateFormatter.string(from: date, timeZone: .gmt, formatOptions: [.withInternetDateTime, .withFractionalSeconds])
}

private func frontmostApp() -> AppSnapshot? {
    let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    if let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] {
        for window in windows {
            let layer = window[kCGWindowLayer as String] as? Int ?? -1
            guard layer == 0, let rawPID = window[kCGWindowOwnerPID as String] as? Int else { continue }
            let pid = pid_t(rawPID)
            guard pid != getpid(), let app = NSRunningApplication(processIdentifier: pid) else { continue }
            let name = app.localizedName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !name.isEmpty {
                return AppSnapshot(name: name, bundleID: app.bundleIdentifier ?? "")
            }
        }
    }
    guard let app = NSWorkspace.shared.frontmostApplication else { return nil }
    return AppSnapshot(
        name: app.localizedName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "Unknown",
        bundleID: app.bundleIdentifier ?? ""
    )
}

private func parentIsAlive(_ pid: Int32) -> Bool {
    kill(pid, 0) == 0 || errno == EPERM
}

private func run() throws {
    let configuration = try Configuration.load(arguments: CommandLine.arguments)
    guard let token = ProcessInfo.processInfo.environment["OPSMINEFLOW_RECORDING_TOKEN"], !token.isEmpty else {
        throw AgentError.invalidArguments
    }

    let client = LocalAPIClient(apiPort: configuration.apiPort, token: token)
    let fileManager = FileManager.default
    var currentApp: AppSnapshot?
    var segmentStart = Date()
    var sequence = 0
    var pendingEvents: [EventPayload] = []

    func queueCurrentSegment(at end: Date) {
        guard let app = currentApp else { return }
        let duration = max(end.timeIntervalSince(segmentStart), 0)
        guard duration >= 0.25 else { return }
        sequence += 1
        pendingEvents.append(
            EventPayload(
                session_id: configuration.sessionID,
                sequence: sequence,
                app_name: app.name,
                app_bundle_id: app.bundleID,
                timestamp_start: iso8601(segmentStart),
                timestamp_end: iso8601(end),
                duration_seconds: duration
            )
        )
    }

    func sendPendingEvents() {
        while let event = pendingEvents.first {
            guard client.post("recording/events", payload: event) else { return }
            pendingEvents.removeFirst()
        }
    }

    while !fileManager.fileExists(atPath: configuration.stopFile) && parentIsAlive(configuration.parentPID) {
        let now = Date()
        let observedApp = frontmostApp()
        if observedApp != currentApp {
            queueCurrentSegment(at: now)
            currentApp = observedApp
            segmentStart = now
        }
        sendPendingEvents()
        _ = client.post(
            "recording/heartbeat",
            payload: HeartbeatPayload(session_id: configuration.sessionID, current_app: currentApp?.name ?? "")
        )
        Thread.sleep(forTimeInterval: configuration.interval)
    }

    queueCurrentSegment(at: Date())
    for _ in 0..<3 where !pendingEvents.isEmpty {
        sendPendingEvents()
        if !pendingEvents.isEmpty { Thread.sleep(forTimeInterval: 0.25) }
    }
}

do {
    try run()
} catch {
    fputs("OpsMineFlow agent failed: \(error)\n", stderr)
    exit(1)
}
