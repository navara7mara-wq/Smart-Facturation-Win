# RC-2 controlled remediation starting state

## Historical RC-1 reference

- Git reference: `refs/tags/rc-1-audited`
- Checkpoint commit: `c79f42d64f3a0b07c93e919a56c2f5bdddf091f2`
- Checkpoint tree: `24d4654eb7ba5eaf54370640be33f24938283a99`
- Parent baseline commit: `965f0f81a368d3557a187f57424c87bff0c3298a`
- Files captured: 206 tracked/untracked source, test, resource, and RC-1 quality/evidence files.
- Normal Git index remained unchanged with zero staged files.

The checkpoint deliberately excludes ignored business databases, private keys, user uploads/backups, build/dist/output directories, and temporary QA databases. These are not source artifacts and must not be committed. The original business database remains separately protected by the RC-1 hashes and logical comparison evidence.

## RC-1 evidence preservation

- Historical defect register: `quality/DEFECT_REGISTER.md`
- SHA-256 at remediation start: `40924a1da3fe297325ca940aa998864f6c375d8647295da40301ae6b7fea2eb3`
- RC-1 audit report and all `quality/evidence/` artifacts are included in the checkpoint reference.
- `quality/DEFECT_REGISTER.md` is frozen and will not be edited during RC-2. Remediation status is recorded in separate RC-2 documents.

## Starting repository state

- Branch: `master`
- HEAD: `965f0f81a368d3557a187f57424c87bff0c3298a`
- Working tree: dirty by design; RC-1 contained 45 modified tracked files plus untracked application, test, resource, and quality files.
- Complete audit-close status: `quality/evidence/git_status_final.txt`.
- Original business database at remediation start: `data/pos_ai.sqlite3`, 655,360 bytes, closing RC-1 SHA-256 `9afc2cb7ba7a00b52f612c015d5a14a3df248c72177c1ab220571d61a5e925d4`; destructive remediation tests are forbidden against it.

## Remediation authority and limitations

- Phase: RC-1 controlled remediation to RC-2.
- Final production certification is explicitly out of scope.
- Financial rounding and historical rate-snapshot choices remain subject to authoritative business-rule confirmation where the repository does not prove the rule.
