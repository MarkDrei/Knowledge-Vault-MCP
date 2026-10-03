# ADR-0006: Git write flow and edit scope

Status: accepted (2026-10-03)

**Context.** The vault is also edited in Obsidian and synced through the same remote. The server must never lose or silently merge user content. The owner wants edit tools to work on the whole vault, not only on the inbox.

**Decision.**
- All git operations (periodic sync and write tools) run under one in-process lock.
- Write: `git pull --rebase` → change files → one commit with a machine-generated message → `git push` → reindex touched files.
- Any rebase or push failure aborts: the local branch is reset to the state before the operation and an error is returned. Nothing is auto-resolved.
- `add_note` always writes to the inbox. `update_note`, `append_note`, `move_note` and `delete_note` can act on any note in the vault.
- `move_note` rewrites `[[wikilinks]]` (incl. aliases and heading links) in all linking notes, in the same commit.

**Consequences.** The repo history is a complete audit log; any bad edit is revertable. Concurrent edits from Obsidian surface as clear errors to retry. Whole-vault edit rights mean a compromised token could alter curated notes, but only via commits that can be reverted.
