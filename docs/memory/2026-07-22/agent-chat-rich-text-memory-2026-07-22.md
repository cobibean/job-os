# Agent chat rich text memory - 2026-07-22

## Session summary

JobOS assistant replies now render safe, compact Markdown in the real installed desktop app. User messages remain literal text. The installed Mac mini app passed a visible end-to-end acceptance response with a heading, bold text, nested bullets, an external link, inline code, a fenced TypeScript block, and a blockquote.

## Decisions made

- Use `react-markdown` without raw-HTML plugins.
- Keep user-authored messages on the existing plain `<p>` path.
- Allow only bounded absolute HTTP/HTTPS links.
- Open validated links through a narrow trusted Electron IPC handler and `shell.openExternal`; the main JobOS window still rejects normal popup/navigation attempts.
- Replace Markdown images with inert text labels so assistant content cannot trigger remote tracking requests.
- Preserve the existing assistant working/completed/interrupted state and streaming cursor behavior.

## Files changed

- `apps/desktop/package.json`
- `pnpm-lock.yaml`
- `apps/desktop/src/renderer/components/AssistantMarkdown.tsx`
- `apps/desktop/src/renderer/components/AgentPanel.tsx`
- `apps/desktop/src/renderer/components/AgentPanel.test.tsx`
- `apps/desktop/src/renderer/styles.css`
- `apps/desktop/src/shared/externalLinks.ts`
- `apps/desktop/src/shared/externalLinks.test.ts`
- `apps/desktop/src/shared/contracts.ts`
- `apps/desktop/src/preload/preload.cts`
- `apps/desktop/src/main/main.ts`

## Verification

Source verification passed after the final code edit:

- Desktop tests: 25 files, 154 tests passed.
- Focused Markdown/link tests: 15 tests passed.
- Desktop TypeScript typecheck passed.
- Desktop lint passed with 0 warnings/errors.
- Desktop production build passed.
- Independent Codex review at medium reasoning passed after its first review identified and the implementation fixed nonfunctional Electron links and automatic remote-image loading.

Product commit and pushed `origin/main` at acceptance time:

- `bb1180cc280513f016e4a40c8ee789726ddd3c96` (`[verified] feat(desktop): render agent Markdown safely`)

Installed-app acceptance:

- Packaged with `pnpm --filter @jobos/desktop package:mac`.
- Candidate app and fresh ZIP extraction passed deep strict signature verification.
- ZIP integrity passed; executable is arm64.
- ZIP size: `143880831` bytes.
- ZIP SHA-256: `94b42aece098529034f5642b94abfe4713d86e269158f56c3979a7a910aa8f59`.
- Installed destination: `/Users/jacobilangemm/Applications/JobOS.app`.
- Accepted installed PID: `33576`, mapped to the exact installed executable.
- The live visible composer produced a streaming response that settled into correctly styled rich text without horizontal panel overflow.
- Clicking the visible OpenAI link dispatched external navigation without replacing the JobOS window.
- Final screenshot: `/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-agent-rich-text-installed-2026-07-22.png`.

## Constraints

- The unrelated existing edit in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` was not staged or modified by this work.
- The local API was not restarted because this feature changes only desktop renderer/main/preload behavior and does not change API-owned persistence or contracts.
- Do not transfer a MacBook build until Cobi approves the Mac mini manual acceptance result.
