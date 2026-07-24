# Job Navigation collapsible status sections — 2026-07-24

## Outcome

Job Navigation status groups are independently collapsible when ordering is set to **Status**. Every visible status group starts collapsed; clicking its full-width header toggles only that group. Manual, Recent, and Alphabetical ordering continue to render the existing flat job list.

## Implementation

- `apps/desktop/src/renderer/components/JobNavigator.tsx`
  - groups status-sorted jobs by status;
  - keeps expanded state independently by group;
  - exposes `aria-expanded` and `aria-controls` on each header;
  - keeps controlled lists mounted with `hidden` while collapsed.
- `apps/desktop/src/renderer/styles.css`
  - adds full-width section-header interaction, hover/focus treatment, animated chevrons, and stable count alignment.
- `apps/desktop/src/renderer/components/JobNavigator.test.tsx`
  - proves all groups start collapsed;
  - proves Inbox and Applied toggle independently;
  - proves collapsing one group does not disturb another.

Product commit: `4c3a47eec86d072bc308cd31e8445b25aaf2789d`

## Verification

- Focused `JobNavigator.test.tsx`: passed.
- Desktop typecheck: passed.
- Full `pnpm check`: passed.
- Independent Codex staged-diff review and focused follow-up review: passed with no blockers.
- Exact installed app: `/Users/jacobilangemm/Applications/JobOS.app`.
- Installed `app.asar` matched the packaged app byte-for-byte before launch.
- Exact installed renderer inspection confirmed:
  - initial Status state: Inbox, Applied, and Inactive all `aria-expanded="false"` with zero visible rows;
  - expanding Inbox showed five rows while Applied and Inactive stayed collapsed;
  - collapsing Inbox restored all groups to zero visible rows.
- Native-window screenshots:
  - collapsed: `jobos-status-sections-collapsed-2026-07-24.png`;
  - Inbox expanded: `jobos-status-inbox-expanded-2026-07-24.png`.

## MacBook updater

Verified outer updater:

`JobOS-MacBook-Update-20260724185755245-4c3a47ee-845350bd76badf9695ef497df88db1eb.zip`

- outer SHA-256: `29beabfae2dcb20b385de186898a2ec94c7b4935c7160b36a1ef503b42aa7c0d`;
- source commit: `4c3a47eec86d072bc308cd31e8445b25aaf2789d`;
- architecture: arm64;
- outer archive and inner app ZIP passed integrity checks;
- updater helper is executable;
- packaged app passed strict deep code-signature verification;
- updater smoke-install test passed.

Taildrop could not complete immediately because `jacobis-macbook-pro` was offline and returned `502 Bad Gateway`. A quiet five-minute retry job was scheduled for up to 48 hours; it reports once when Taildrop accepts the verified ZIP.

## Working-tree boundary

Do not stage or overwrite Cobi's existing edit in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md`.
