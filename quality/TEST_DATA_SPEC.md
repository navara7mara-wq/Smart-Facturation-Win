# Test Data Specification

## Isolation

- Root: `tmp/rc1-audit/`
- Database: `tmp/rc1-audit/data/QA_RC1.sqlite3`
- Uploads: `tmp/rc1-audit/uploads/`
- Exports: `tmp/rc1-audit/exports/`
- Backups: `tmp/rc1-audit/backups/`
- Downloads: `tmp/rc1-audit/downloads/`
- Environment variables: `PHOENIX_DB_PATH`, `PHOENIX_UPLOAD_DIR`, `PHOENIX_EXPORT_DIR`, `PHOENIX_BACKUP_DIR`, `PHOENIX_DOWNLOAD_DIR`.

All created identifiers and free text start with `QA_RC1_` where the field permits it. Never use cleanup helpers on the original DB.

## Scenario diversity

- Clients/directions: 10+ unique records including French accents, Arabic, spaces, hyphens and long legal names.
- Company branches: at least 3 branches and all roles.
- Purchase orders: 10+ records covering ACQUISITION, CONSTRUCTION, CONST_ACQUIS, NDC and MGC.
- Sites: at least 50, with varied typologies, regions and required partners.
- Invoices: at least 25 critical calculation scenarios, including normal and NDC, one-line/multi-line, boundary and rounding-sensitive values.
- Search/filter: at least 10 distinct queries, zero-result cases and combined filters.
- Volume: 100 representative records minimum; project automated fixture of 2,000 invoices may be used.

## Financial golden cases

The independent oracle uses decimal arithmetic and explicit two-decimal half-even rounding to mirror the observable Python implementation. This is an implementation conformance oracle, not legal approval of the rounding policy.

Mandatory categories:

1. quantity 1 / integer price;
2. decimal quantity;
3. small fractional price;
4. values ending at half-cent boundaries;
5. multiple lines with independent line rounding;
6. zero unit price with positive quantity;
7. large quantity and price within practical SQLite/float range;
8. retention 0%, default 5% and alternative configured rate;
9. tax 0%, default 19% and alternative configured rate;
10. normal vs NDC line constraints.

Invalid scenarios: empty lines, quantity 0, negative quantity/price, malformed decimal, duplicate invoice number, invalid NDC article, invalid site/type relationship, extremely large numeric input and non-finite values.

