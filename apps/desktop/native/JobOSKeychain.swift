import Foundation
import Security

private let notFoundExit: Int32 = 44

private func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

private func arguments() -> (command: String, service: String, account: String) {
    let values = CommandLine.arguments
    guard values.count == 4 else {
        fail("usage: jobos-keychain <get|set|delete> <service> <account>")
    }
    let command = values[1]
    let service = values[2]
    let account = values[3]
    guard ["get", "set", "delete"].contains(command),
          !service.isEmpty,
          !account.isEmpty,
          !service.contains("\0"),
          !account.contains("\0") else {
        fail("invalid Keychain request")
    }
    return (command, service, account)
}

private func query(service: String, account: String) -> [CFString: Any] {
    [
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
    ]
}

let request = arguments()
let base = query(service: request.service, account: request.account)

switch request.command {
case "get":
    var lookup = base
    lookup[kSecReturnData] = true
    lookup[kSecMatchLimit] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(lookup as CFDictionary, &result)
    if status == errSecItemNotFound {
        exit(notFoundExit)
    }
    guard status == errSecSuccess, let data = result as? Data else {
        fail("Keychain credential could not be read")
    }
    FileHandle.standardOutput.write(data)
case "set":
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty, data.count <= 4096 else {
        fail("Keychain credential is invalid")
    }
    let update = [kSecValueData: data] as CFDictionary
    let updateStatus = SecItemUpdate(base as CFDictionary, update)
    if updateStatus == errSecItemNotFound {
        var insertion = base
        insertion[kSecValueData] = data
        insertion[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlock
        let addStatus = SecItemAdd(insertion as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            fail("Keychain credential could not be stored")
        }
    } else if updateStatus != errSecSuccess {
        fail("Keychain credential could not be stored")
    }
case "delete":
    let status = SecItemDelete(base as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
        fail("Keychain credential could not be deleted")
    }
default:
    fail("invalid Keychain request")
}
