# Feature Inventory and Risk Classification

| Area | Discovered features | Risk |
|---|---|---|
| Desktop/runtime | Single-instance desktop shell, local random port, server thread, pywebview window, logging, source/package modes | CRITICAL |
| First run/auth | Setup, login/logout, forced password change, lockout, server-side sessions, CSRF | CRITICAL |
| Users/permissions | Users, passwords, roles, active/lock state, cleanup helper, role and per-user permissions, branch isolation | CRITICAL |
| Dashboard | KPI totals, lifecycle distribution, monthly TTC, paid amounts, overdue >60 days, filters/queues | HIGH |
| Billing table | Search, branch/client/direction/nature/typology/status/date filters, sorting, pagination, inline tracking update, Excel export | HIGH |
| Company | Legal identity, logo, bank/account fields, branches | HIGH |
| Clients | Client CRUD, direction CRUD, mapping to company branches, soft deletion/active state | HIGH |
| Typologies | CRUD/activation, application sigle and document full-label snapshots | HIGH |
| Site partners | Subcontractors and design offices, CRUD, import preview/confirm/report, PO-dependent requirements | HIGH |
| BPU | General catalogue CRUD/import, category tabs, search, ST version import/review/confirm/activate/history/canonical mapping | CRITICAL |
| Purchase orders | CRUD/view, nature, dates, amount, client/company direction, attachment, search/sort/page, Excel import/export | CRITICAL |
| Sites | CRUD, PO relationship, code/name/region/typology, subcontractor/design office, deletion rules | HIGH |
| Invoices | Regular/NDC create/edit/read/reopen/cancel/restore, line resolution, numbering, totals, multi-site, tracking dates, lifecycle/audit/locks | CRITICAL |
| Invoice documents | Invoice XLSX, invoice/DQ/DE HTML preview, PDF generation/open/download/archive/cleanup | CRITICAL |
| Templates/settings | Document template settings, visual blocks, logos, financial rates, contract reference | HIGH |
| Backup/restore | SQLite online backup, list/download, integrity validation, pre-restore backup, restore | CRITICAL |
| Imports | Partner and PO templates, validation, preview batches, confirm/apply, reports | HIGH |
| Licensing | DEMO, activation request, install signed licence, machine identity, limits, publisher License Studio | HIGH |
| Status/about/help | DB/dependency/runtime status, version/about, user guide/changelog | MEDIUM |
| Build/release | PyInstaller, bundled Node/modules/assets, portable ZIP, Inno Setup, hashes, optional Authenticode signing | CRITICAL |
| Accessibility/localization | French UI, French money/date display, Unicode inputs, keyboard/dialog behavior | MEDIUM/HIGH |

## Routes discovered

Main pages: `/`, `/table-facturation-new`, `/table-facturation`, `/invoices`, `/purchase-orders`, `/clients`, `/bpu`, `/settings`, `/company`, `/company-branches`, `/templates`, `/users`, `/backup`, `/license`, `/about`, `/status`.

Important APIs/actions: `/bpu/item`, `/bpu/search`, `/table-facturation-new/update`, invoice XLSX/PDF/preview routes, partner and purchase-order import template/preview/confirm/report routes, backup create/restore/download, licence request/install, attachments and exports, plus create/update/delete handlers for core entities.

Legacy redirects: `/mobilis` → `/clients`, `/contract` → `/company`, `/sites` → `/purchase-orders`, `/archives` → `/invoices`.

