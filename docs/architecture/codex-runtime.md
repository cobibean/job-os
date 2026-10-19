# Codex runtime maintenance

JobOS bundles a hash-pinned Codex App Server for macOS arm64. The current
distribution receipt is `codex-runtime-receipt.json` in this directory. The
phase acceptance receipts remain historical evidence and do not select the
runtime for new builds.

To upgrade the runtime:

1. Select an upstream stable release and resolve its tag to a source commit.
2. Verify the release archive against the upstream asset digest, extract it,
   and verify its member sizes, binary hash, architecture, and strict signature.
3. Preserve that commit's LICENSE, NOTICE, and App Server v2 schema. Update the
   current receipt, Python runtime pin, packaged legal paths, and license gate.
4. Generate the schema with the downloaded binary and compare it semantically
   with the source schema. Run the connected-agent protocol and lifecycle tests.
5. Build and verify the app package. With an isolated JobOS ChatGPT login,
   verify model discovery, a first turn, a follow-up, and continuation after
   restarting the service. Confirm JobOS MCP tools remain available.

The model picker uses the signed-in account's `model/list` response, follows
pagination, excludes hidden models, and places the provider recommendation and
reasoning effort first. Saved agent defaults take precedence. Upgrading never
changes an existing chat's sealed model binding. Models should not be added by
guessing names or by copying a standalone Codex credential store.

The 0.163.0 upgrade preserves the stable methods and fields used by JobOS:
keyring-backed account operations, `model/list`, full MCP status, thread
start/resume/read, turn start/interrupt, and streamed item/turn events. Its
LICENSE and NOTICE are byte-identical to the previous 0.144.4 legal files.
