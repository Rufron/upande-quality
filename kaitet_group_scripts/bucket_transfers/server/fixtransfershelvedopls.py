# Name: fixTransferShelvedOpls
# Type: Server Script · API
# API method: fixTransferShelvedOpls
# Enabled: yes
# Modified: 2026-07-14 16:04:11
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Frappe Server Script (Type: API), api_method = fixTransferShelvedOpls
# One-off / maintenance: for OPL rows whose bucket is SHELVED but still carries
# stale transfer flags (awaiting_transfer / in_transit / loaded_in_trolley),
# clear those flags, then submit any draft OPL whose transfer buckets are all
# shelved. Idempotent. Payload: { "data": { "opl": "<name>" } } — omit "opl" to
# sweep every affected draft OPL.
frappe.response["message"] = {"status": "error"}
try:
    data = frappe.request.get_json()
    if isinstance(data, dict) and "data" in data:
        data = data.get("data")
    data = data or {}
    opl = data.get("opl")

    if opl:
        names = [opl]
    else:
        rows = frappe.db.sql(
            """
            SELECT DISTINCT parent FROM `tabPick List Item`
            WHERE custom_shelved = 1
              AND (custom_awaiting_transfer = 1 OR custom_in_transit = 1 OR custom_loaded_in_trolley = 1)
            """,
            as_dict=True,
        )
        names = [r.parent for r in rows]

    fixed = []
    submitted = []
    i = 0
    while i < len(names):
        name = names[i]
        doc = frappe.get_doc("Order Pick List", name)
        if doc.docstatus == 0:
            changed = False
            for row in doc.locations:
                is_transfer = (
                    (row.custom_in_transit or 0) == 1
                    or (row.custom_awaiting_transfer or 0) == 1
                    or (row.custom_loaded_in_trolley or 0) == 1
                )
                if is_transfer and (row.custom_shelved or 0) == 1:
                    row.custom_in_transit = 0
                    row.custom_awaiting_transfer = 0
                    row.custom_loaded_in_trolley = 0
                    changed = True
            if changed:
                doc.save(ignore_permissions=True)
                fixed.append(name)

            all_ready = True
            for row in doc.locations:
                is_transfer = (
                    (row.custom_in_transit or 0) == 1
                    or (row.custom_awaiting_transfer or 0) == 1
                    or (row.custom_loaded_in_trolley or 0) == 1
                )
                if is_transfer and (row.custom_shelved or 0) != 1:
                    all_ready = False
                    break
            if all_ready:
                doc.flags.ignore_permissions = True
                try:
                    doc.submit()
                    submitted.append(name)
                except Exception as se:
                    pass
        i = i + 1

    frappe.db.commit()
    frappe.response["message"] = {"status": "success", "candidates": len(names), "fixed": fixed, "submitted": submitted}
except Exception as e:
    frappe.response["message"] = {"status": "error", "message": str(e)}
