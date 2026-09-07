# RC-1 protection verification

- Original database at audit start: `data/pos_ai.sqlite3`, 655,360 bytes, SHA-256 `ad8be2a63111b6f676551bf7871279b3f1e8e3a227a1af390f4a73bd64172280`.
- Original database at audit close: 655,360 bytes, SHA-256 `9afc2cb7ba7a00b52f612c015d5a14a3df248c72177c1ab220571d61a5e925d4`.
- The physical SQLite file hash changed while the automated suite was running. The file is untracked by Git, so Git cannot be used as a content oracle.
- A QA-generated SQLite backup created at 14:16, before the final file timestamp, and backups created after the timestamp all have the same logical contents as the closing database.
- Read-only canonical comparison covered the full `sqlite_master` schema, `PRAGMA user_version`, every column and every row of every table (including `sqlite_sequence`). Result: no schema differences and no table differences.
- Both copies returned `PRAGMA integrity_check = ok`, zero `foreign_key_check` rows, and identical per-table logical SHA-256 digests.
- Conclusion: no business record, field value, relationship, schema object, or sequence value changed. The differing file SHA-256 is confined to SQLite's physical representation and is consistent with page/checkpoint rewriting; the exact byte-level cause was not determined.

## QA artifact isolation limitation

Automated backup tests produced six QA-generated files in the repository-level `backups/` directory at 14:16, 14:22, and 14:50. They are test artifacts, not genuine business backups. This exposes a test-isolation limitation: the tests isolate their database input but do not redirect the default backup output directory. The files were retained to avoid destructive cleanup and to preserve audit evidence.
