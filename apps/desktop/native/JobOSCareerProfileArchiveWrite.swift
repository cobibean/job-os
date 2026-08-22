import CryptoKit
import Darwin
import Foundation

private let maxArchiveBytes = 100 * 1024 * 1024

private func fail(_ message: String, code: Int32 = 4) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

private func writeAll(_ descriptor: Int32, _ data: Data) throws {
    try data.withUnsafeBytes { rawBuffer in
        guard let base = rawBuffer.baseAddress else { return }
        var offset = 0
        while offset < rawBuffer.count {
            let written = Darwin.write(descriptor, base.advanced(by: offset), rawBuffer.count - offset)
            if written < 0 {
                if errno == EINTR { continue }
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
            offset += written
        }
    }
}

private func testHandshakeIfRequested() throws {
    guard CommandLine.arguments.count == 4, CommandLine.arguments[3] == "--test-handshake" else { return }
    var ready: UInt8 = 1
    guard Darwin.write(3, &ready, 1) == 1 else { throw POSIXError(.EIO) }
    var resume: UInt8 = 0
    guard Darwin.read(4, &resume, 1) == 1 else { throw POSIXError(.EIO) }
}

guard CommandLine.arguments.count == 3 || CommandLine.arguments.count == 4 else {
    fail("usage: jobos-career-profile-archive-write <destination> <sha256> [--test-handshake]", code: 2)
}

let destination = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
let expected = CommandLine.arguments[2]
let destinationName = destination.lastPathComponent
let directoryPath = destination.deletingLastPathComponent().path
guard !destinationName.isEmpty,
      destinationName != ".",
      destinationName != "..",
      !destinationName.contains("/"),
      expected.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
    fail("invalid archive write request", code: 2)
}

let directoryDescriptor = Darwin.open(directoryPath, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
guard directoryDescriptor >= 0 else { fail("archive directory open failed") }
defer { Darwin.close(directoryDescriptor) }
var directoryStat = stat()
guard fstat(directoryDescriptor, &directoryStat) == 0, (directoryStat.st_mode & S_IFMT) == S_IFDIR else {
    fail("archive destination is not a directory")
}

do {
    try testHandshakeIfRequested()
    let temporaryName = ".jobos-career-profile-\(UUID().uuidString).tmp"
    let temporaryDescriptor = temporaryName.withCString {
        openat(directoryDescriptor, $0, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
    }
    guard temporaryDescriptor >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    var renamed = false
    defer {
        Darwin.close(temporaryDescriptor)
        if !renamed { temporaryName.withCString { _ = unlinkat(directoryDescriptor, $0, 0) } }
    }

    var digest = SHA256()
    var byteCount = 0
    while true {
        let chunk = FileHandle.standardInput.readData(ofLength: 1024 * 1024)
        if chunk.isEmpty { break }
        byteCount += chunk.count
        guard byteCount <= maxArchiveBytes else { fail("archive exceeds 100 MiB") }
        digest.update(data: chunk)
        try writeAll(temporaryDescriptor, chunk)
    }
    guard byteCount > 0 else { fail("archive is empty") }
    guard fsync(temporaryDescriptor) == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    var temporaryStat = stat()
    guard fstat(temporaryDescriptor, &temporaryStat) == 0,
          (temporaryStat.st_mode & S_IFMT) == S_IFREG,
          temporaryStat.st_size == byteCount,
          (temporaryStat.st_mode & 0o777) == 0o600 else {
        fail("archive temporary file failed verification")
    }
    let actual = digest.finalize().map { String(format: "%02x", $0) }.joined()
    guard actual == expected else { fail("archive digest mismatch") }
    let renameResult = temporaryName.withCString { temporaryPointer in
        destinationName.withCString { destinationPointer in
            renameat(directoryDescriptor, temporaryPointer, directoryDescriptor, destinationPointer)
        }
    }
    guard renameResult == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    renamed = true
    guard fsync(directoryDescriptor) == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
} catch {
    fail("archive write failed: \(error.localizedDescription)")
}
