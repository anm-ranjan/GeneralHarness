# 2026-08-02 — SQLite storage and Projects → Threads restructuring

## Product model

The frontend hierarchy was simplified from Projects → Tasks → Sessions to
Projects → Threads. Backend compatibility is deliberately retained: existing
`/api/sessions` routes, session identifiers, provider integration, and internal
model names remain in place.

Tasks now behave as colour-coded thread labels. New threads created without an
explicit label are placed under the automatically created `General` label.
The database supports multiple labels per thread while the current UI presents
one primary label. Deleting a label reassigns its primary threads to `General`
instead of deleting them.

## SQLite storage

`SessionStore` was replaced with a transactional SQLite implementation. The
database stores projects, labels, threads, queued messages, ordered events,
change manifests, provider summaries/raw events, attachments, images, and
recorded audio. Binary content is SHA-256 deduplicated and stored as BLOBs.
Temporary filesystem materializations are regenerated when providers require a
path.

The runtime layout is now:

```text
data/
  <configured-name>.sqlite3
  workspaces/
  temporary-materializations/
```

The database filename defaults to `myharness.sqlite3` and is configurable as a
filename inside the data directory. WAL, foreign-key enforcement, integrity
checks, schema versioning, and atomic migration promotion are enabled.

The legacy JSON/JSONL/filesystem store is imported automatically on first
startup. After a successful integrity-checked migration, the previous store is
moved to `data/legacy-flat-store/` as a rollback copy. Database rows become the
authoritative state; temporary materializations remain disposable.

Backup/import, search consumers, attachment serving, transcription, queued
attachment cleanup, Electron packaged-data detection, installer configuration,
and documentation were updated for the new storage model.

## Collision finding and Codex benchmark

A migration benchmark used the local Codex project/thread history as a
realistic event corpus. It exposed an important flaw in the first schema:
random eight-hex event IDs are not globally unique across threads. One event
out of roughly 79,000 would have been dropped. Schema version 2 therefore uses
thread-local ordering without a global event-ID uniqueness constraint, and a
regression test covers duplicate IDs across different threads.

Final private benchmark results:

- 286 rollout files and 79,111 events imported without loss.
- Approximately 197 MiB of source data produced a 229 MiB SQLite database.
- Import completed in 7.7 seconds, about 10,242 events per second.
- A full event scan completed in approximately 1.0 second.

The temporary benchmark database was removed after measurement, and transcript
content was not printed or committed.

## Verification

- Full backend suite: 264 tests passed on Jarvis (263 before the final title
  regression was added).
- Frontend: 114 tests passed on both macOS and Jarvis; production builds passed.
- Installer/Electron: 35 tests passed.
- Rust TUI: 62 tests passed; Clippy passed with one pre-existing
  `too_many_arguments` warning.
- Backend compile sweep and Node/shell syntax checks passed.
- Live browser testing verified project creation, automatic `General` label
  creation, label colour controls, and thread creation.
- `git diff --check` passed.

## Deployment

The current source was deployed to:

- macOS: `/Users/animesh/Software/Harness_2BOrNot2B`
- Jarvis: `/home/animesh/Applications/Harness_2BOrNot2B`

Runtime `data/`, machine-local `agent_config.yaml`, fleet settings, credentials,
environments, and installed dependencies were excluded from synchronization.
Configuration hashes matched before and after deployment on both machines.

The macOS ARM64 DMG and Jarvis x86-64 AppImage/DEB were built successfully with
the configured `2B|!2B` display name. Their filesystem-safe artifact prefix is
`2B-2B`. The new Jarvis Debian artifact resolved to package name
`myharness-electron`.
Replacing the system-installed package was not completed because Jarvis rejected
non-interactive `sudo` authentication; the updated checkout and AppImage are
available in the deployment directory.

## Installer and unified Electron storage follow-up

The setup wizard now reuses an existing `storage.data_dir` and
`storage.database_filename` as the defaults during a rerun, preventing an
upgrade from silently selecting a new empty database. Configurations predating
the storage section fall back to the checkout's `data/` directory and the
default SQLite filename. The user's chosen brand name remains independent of
the storage filename.

Electron no longer selects or migrates to a separate `userData/data` directory.
An Electron-started backend receives the same `storage.data_dir` configured for
all other launchers, with `MYHARNESS_WEB_DATA_DIR` retained as the explicit
process-level override. Installer and fleet text now describes projects,
labels, and threads.

Focused follow-up verification passed 42 Node installer/packaging tests and 45
backend configuration/Electron tests. Existing live YAML files were not read or
modified; packaged applications should be rebuilt after their installer run has
written the intended absolute unified data directory.

## Repository state

Work was completed on `Restructuring_SQLite_Storage`. The unrelated pre-existing
`AGENTS.md` modification remains deliberately unstaged. No runtime database,
credentials, configuration secrets, benchmark content, build output, or
environment files are included in the commit.
