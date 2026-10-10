# Server scripts (readable source)

Readable copies of the Frappe **Server Script** (API type) records that back the
shelving / cold-room pages. The **deploy source of truth** is
`upande_quality/fixtures/server_script.json` (imported on `bench migrate`); these
`.py` files mirror the `script` field of each entry for review and version history.

| File | Server Script | api_method | Used by |
|------|---------------|-----------|---------|
| `fetch_shelving_report.py` | Fetch Shelving Report | `fetch_shelving_report` | shelving-report (Shelf Detail tab) |
| `get_shelving_harvested.py` | Shelving Harvested | `get_shelving_harvested` | shelving-report "Harvested" KPI |
| `get_coldroom_occupancy.py` | Cold Room Occupancy | `get_coldroom_occupancy` | shelving-report (Cold Room tab) |

Keep these in sync with the fixture when either changes. They run under Frappe
RestrictedPython (no imports; no `+=` on dict/list items; `frappe.db.sql` allowed).
