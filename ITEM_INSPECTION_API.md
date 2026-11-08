# Saved Items inspections — backend v1

This is the backend foundation for a later project-page inspection editor.
The released photo guide stays unchanged until its frontend follow-up is reviewed.

## Contract

Both routes require sign-in and an owned **root** item ID:

- `GET /api/items/{root_item_id}/inspections`: latest inspection revision, or
  `null` when none exists. Child estimate IDs, nonexistent IDs and another
  owner's roots return the same 404. A read never creates an inspection.
- `POST /api/items/{root_item_id}/inspections`: append a revision, return 201.
  No existing inspection, estimate, outcome, project stage or price is rewritten.

Required: `request_id` (UUID), `expected_previous_id` (UUID or explicit null),
and `kind` (`chair`, `storage`, `general`). Optional facts:

| Field | Meaning |
| --- | --- |
| `guide_version` | Only `furniture-v1`; defaults to it. |
| `brand_hint` | `herman_miller`, `steelcase`, or null; chair guide selection only, never a verified brand. |
| `label_text` | Up to 500 characters, exact user-entered text; null means not recorded. |
| `notes` | Up to 5,000 characters; independent of the paid assessment description's 500-character limit. |
| `inspected_on` | Optional user-entered calendar date, YYYY-MM-DD. Never inferred from the save timestamp. |
| `answers` | Up to five check IDs for this kind, each `unchecked`, `okay`, or `issue`. Missing checks normalize to `unchecked`. |

The v1 check IDs match frontend #115:

- Chair: `chair-frame`, `chair-height`, `chair-controls`, `chair-surfaces`, `chair-wheels`.
- Storage: `storage-frame`, `storage-moving`, `storage-finish`, `storage-damp`, `storage-fit`.
- General: `general-frame`, `general-moving`, `general-surface`, `general-damp`, `general-fit`.

Wrong-category IDs, unknown answers, unknown fields, unsupported guide versions,
invalid dates, non-string/oversized text and NUL characters are rejected with 422.
Text is preserved verbatim, including whitespace; the future UI must render it
as plain text. The backend neither decodes labels nor generates verdicts.

The response contains `id`, `root_item_id`, `previous_id`, `revision_number`,
`schema_version: 1`, `source: "user_reported"`, UTC `created_at`, and normalized
`inputs`. It contains no price, condition score, authenticity or model verdict.

## Save and retry behavior

Every save is a complete snapshot. Omitted optional facts become unknown; an
omitted check becomes unchecked. The old revision remains in the database.
Changing category saves only the selected category's answers in that revision.
Old revisions are retained internally; this version exposes the latest only.

The client must load the latest revision and send its ID as `expected_previous_id`.
A stale edit returns 409 without writing. After an unclear response, resend the
**same request ID and payload**. A matching retry returns the original record,
even if a later revision now exists; it does not move the latest record backward.
Reusing that UUID with another owner, root or normalized payload returns a
generic 409 without revealing the original record. The fingerprint sorts object
keys and normalizes missing check answers/default fields before comparison.

PostgreSQL locks the existing item root to serialize first saves and updates.
The global UUID primary key separately handles UUID collisions across roots.
Only that named collision is recovered; other database failures propagate after
rollback. No provider, assessment, pricing or calculator call is made.

## Deployment and follow-up

`init_db()` adds only `item_inspection_revisions`, using the existing startup
create-if-missing mechanism. No existing columns, tables or rows are migrated.
The application database role already needs CREATE TABLE for this mechanism.
Existing endpoints and response shapes are unchanged; old frontends keep working.

The frontend follow-up should mirror this contract in `src/lib/types.ts`, add
validated API readers/writers, and show explicit save/retry/conflict states on the
project page. It must preserve unsaved observations, clear drafts when owners or
sessions change, retain exact UUID/payload on retry, and never parse an old freeform
assessment description into fabricated structured inspection facts. No automatic
inspection save is added to the paid assessment action.

## Verification

The ordinary suite tests API behavior, isolation, retries, validation, snapshots,
and additive schema creation on SQLite. A separate CI job uses disposable local
PostgreSQL 16 to repeat all 15 contract tests and run three eight-client races:
identical retries, competing edits, and the same UUID across different roots.
The first two verify seven real PostgreSQL lock waiters; the third verifies seven
actual named primary-key violations. Each test removes its random schema.

The PostgreSQL tests accept only a localhost database named
`flipforge_inspection_test`, without URL query parameters. They do not run against
production. This proves test persistence and concurrency, not production backups
or real signed-in device behavior.
