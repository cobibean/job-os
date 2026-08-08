import CryptoKit
import Darwin
import Foundation

private enum HelperExit: Int32 {
    case usage = 2
    case conflict = 3
    case failure = 4
}

private func fail(_ message: String, code: HelperExit) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code.rawValue)
}

private func sha256(_ url: URL) throws -> String {
    let digest = SHA256.hash(data: try Data(contentsOf: url, options: [.mappedIfSafe]))
    return digest.map { String(format: "%02x", $0) }.joined()
}

private func swap(_ first: String, _ second: String) throws {
    if renameatx_np(AT_FDCWD, first, AT_FDCWD, second, UInt32(RENAME_SWAP)) != 0 {
        let code = POSIXErrorCode(rawValue: errno) ?? .EIO
        throw POSIXError(code)
    }
}

private func syncParent(of file: URL) throws {
    let directory = file.deletingLastPathComponent().path
    let descriptor = Darwin.open(directory, O_RDONLY)
    guard descriptor >= 0 else {
        let code = POSIXErrorCode(rawValue: errno) ?? .EIO
        throw POSIXError(code)
    }
    defer { Darwin.close(descriptor) }
    guard fsync(descriptor) == 0 else {
        let code = POSIXErrorCode(rawValue: errno) ?? .EIO
        throw POSIXError(code)
    }
}

if CommandLine.arguments.count == 2 {
    do {
        try syncParent(of: URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL)
        exit(EXIT_SUCCESS)
    } catch {
        fail("directory sync failed: \(error.localizedDescription)", code: .failure)
    }
}

guard CommandLine.arguments.count == 4 else {
    fail("usage: jobos-docx-atomic-replace <canonical> <sibling-temp> <expected-sha256>", code: .usage)
}

let canonical = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
let temporary = URL(fileURLWithPath: CommandLine.arguments[2]).standardizedFileURL
let expected = CommandLine.arguments[3]

guard canonical.deletingLastPathComponent() == temporary.deletingLastPathComponent(),
      expected.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
    fail("invalid atomic replacement request", code: .usage)
}

let coordinator = NSFileCoordinator(filePresenter: nil)
var coordinationError: NSError?
var operationError: Error?
var conflict = false

coordinator.coordinate(writingItemAt: canonical, options: .forReplacing, error: &coordinationError) { coordinated in
    var exchanged = false
    do {
        guard try sha256(coordinated) == expected else {
            conflict = true
            return
        }
        try swap(temporary.path, coordinated.path)
        exchanged = true
        guard try sha256(temporary) == expected else {
            try swap(temporary.path, coordinated.path)
            try syncParent(of: coordinated)
            exchanged = false
            conflict = true
            return
        }
        try syncParent(of: coordinated)
    } catch {
        if exchanged {
            do {
                try swap(temporary.path, coordinated.path)
                try syncParent(of: coordinated)
                exchanged = false
            } catch {
                operationError = error
                return
            }
        }
        operationError = error
    }
}

if let error = coordinationError ?? operationError as NSError? {
    fail("atomic replacement failed: \(error.localizedDescription)", code: .failure)
}
if conflict {
    exit(HelperExit.conflict.rawValue)
}
exit(EXIT_SUCCESS)
