# Name: deleteSavedTrolleys
# Type: Server Script · API
# API method: deleteSavedTrolleys
# Enabled: yes
# Modified: 2026-06-17 12:31:36
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Frappe Server Script (Type: API), api_method = deleteSavedTrolleys
# Undo a SAVED trolley grouping WITHOUT re-shelving its buckets.
# Clears custom_trolley_id / custom_loaded_in_trolley / custom_awaiting_transfer
# on the trolley's Pick List Item rows, leaving custom_shelf untouched.
# Only acts on not-yet-loaded rows (custom_in_transit != 1); loaded ones are
# skipped and reported back.
#
# Matching is Pick List Item-FIRST (by custom_trolley_id), because saved
# trolleys live on OPLs of EITHER docstatus (draft AND submitted) — an earlier
# version scoped to draft OPLs only and matched 0 rows for submitted-OPL
# trolleys. Farm is used only as a cross-farm safety check via the parent OPL.
#
# Payload (form params): trolley_ids = "T1|~|T2", farm = "Karen", dry_run = "1" (optional)
# safe_exec: no def/import/+=/.append/parse_json/sql.

frappe.response["message"] = {"status": "error", "message": "Script failed"}

try:
    ids_raw = frappe.form_dict.get("trolley_ids") or ""
    farm = frappe.form_dict.get("farm") or ""
    dry_run = (frappe.form_dict.get("dry_run") or "") == "1"

    trolley_ids = []
    parts = ids_raw.split("|~|")
    p = 0
    while p < len(parts):
        v = parts[p].strip()
        if v != "":
            trolley_ids = trolley_ids + [v]
        p = p + 1

    if len(trolley_ids) == 0:
        frappe.response["message"] = {"status": "error", "message": "No trolley ids supplied."}
    else:
        rows = frappe.get_all(
            "Pick List Item",
            filters=[["custom_trolley_id", "in", trolley_ids]],
            fields=["name", "parent", "custom_trolley_id", "custom_in_transit"],
        )

        # Resolve parent OPL farms once (small set) for the cross-farm safety check.
        parents = {}
        i = 0
        while i < len(rows):
            parents[rows[i].parent] = 1
            i = i + 1
        pnames = []
        for k in parents:
            pnames = pnames + [k]
        farm_by_parent = {}
        if len(pnames) > 0:
            opls = frappe.get_all("Order Pick List", filters=[["name", "in", pnames]],
                                  fields=["name", "custom_farm"])
            j = 0
            while j < len(opls):
                farm_by_parent[opls[j].name] = opls[j].custom_farm
                j = j + 1

        cleared_count = 0
        skipped = {}
        skipped_farm = {}
        m = 0
        while m < len(rows):
            r = rows[m]
            row_farm = farm_by_parent.get(r.parent, "")
            if farm != "" and row_farm != farm:
                skipped_farm[r.custom_trolley_id] = 1
            elif r.custom_in_transit == 1:
                skipped[r.custom_trolley_id] = 1
            else:
                if not dry_run:
                    frappe.db.set_value("Pick List Item", r.name, {
                        "custom_trolley_id": "",
                        "custom_loaded_in_trolley": 0,
                        "custom_awaiting_transfer": 0,
                    }, update_modified=True)
                cleared_count = cleared_count + 1
            m = m + 1

        skipped_loaded = []
        for k2 in skipped:
            skipped_loaded = skipped_loaded + [k2]
        skipped_other_farm = []
        for k3 in skipped_farm:
            skipped_other_farm = skipped_other_farm + [k3]

        frappe.response["message"] = {
            "status": "success",
            "dry_run": dry_run,
            "cleared_count": cleared_count,
            "matched_rows": len(rows),
            "skipped_loaded": skipped_loaded,
            "skipped_other_farm": skipped_other_farm,
        }

except Exception as e:
    frappe.response["message"] = {"status": "error", "message": str(e)}
