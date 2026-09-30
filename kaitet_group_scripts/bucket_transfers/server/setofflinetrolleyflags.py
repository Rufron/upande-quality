# Name: setOfflineTrolleyFlags
# Type: Server Script · API
# API method: setOfflineTrolleyFlags
# Enabled: yes
# Modified: 2026-07-29 00:31:32
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Frappe Server Script (Type: API), api_method = setOfflineTrolleyFlags
# Additive-only sync for the offline Bucket Requests app. Marks specific
# Pick List Item rows as loaded-in-trolley or in-transit WITHOUT submitting the
# OPL (stays draft). Targets exact rows by their Pick List Item name (the app
# stored pick_list_item_id at download), so bucket-QR reuse can never hit the
# wrong OPL. Once a bucket is loaded/in-transit it has physically left its
# shelf, so its Shelf Item is deleted too (mirrors the discard flow).
# Payload: { "data": { "pli_ids": ["<name>", ...], "flag": "loaded" | "transit",
#                       "truck": "<Vehicle name>" (optional, only used on loaded) } }


def remove_bucket_from_shelf(bucket_id):
    """Delete the bucket's Shelf Item row(s) so it no longer shows on its shelf.
    Idempotent — no Shelf Item is a no-op. Returns the shelves touched."""
    removed = []
    if not bucket_id:
        return removed
    try:
        shelf_items = frappe.db.get_all(
            "Shelf Item",
            filters={"bucket_id": bucket_id},
            fields=["name", "parent"],
        )
        for item in shelf_items:
            frappe.delete_doc("Shelf Item", item.name, force=1)
            removed.append(item.parent)
            # Touch parent Shelf (keeps UI + modified in sync)
            frappe.db.set_value("Shelf", item.parent, "modified", frappe.utils.now())
    except Exception:
        # Shelf removal is not critical to the flag sync — never fail on it.
        pass
    return removed


frappe.response["message"] = {"status": "error", "message": "Script failed"}

try:
    data = frappe.request.get_json()
    if isinstance(data, dict) and "data" in data:
        data = data.get("data")
    data = data or {}

    pli_ids = data.get("pli_ids") or []
    flag = data.get("flag") or ""
    truck = data.get("truck") or ""

    field = None
    if flag == "loaded":
        field = "custom_loaded_in_trolley"
    elif flag == "transit":
        field = "custom_in_transit"

    if not field:
        frappe.response["message"] = {"status": "error", "message": "Invalid flag (expected 'loaded' or 'transit')."}
    elif not pli_ids:
        frappe.response["message"] = {"status": "error", "message": "No pick list items supplied."}
    else:
        updated = 0
        missing = 0
        removed_shelves = []
        i = 0
        while i < len(pli_ids):
            name = pli_ids[i]
            if name and frappe.db.exists("Pick List Item", name):
                # A bucket may occupy MULTIPLE rows of the same OPL (mixed-box /
                # split allocations); the app only holds ONE row per bucket, so
                # flag every sibling row for that bucket in the same OPL — scanning
                # the bucket once must update them all, or the OPL never completes.
                parent = frappe.db.get_value("Pick List Item", name, "parent")
                bucket = frappe.db.get_value("Pick List Item", name, "custom_bucket")
                sibs = []
                if parent and bucket:
                    sibs = frappe.get_all(
                        "Pick List Item",
                        filters={"parent": parent, "parenttype": "Order Pick List",
                                 "custom_bucket": bucket},
                        fields=["name"],
                    )
                if not sibs:
                    sibs = [{"name": name}]
                # Additive only: set the one flag to 1, leave everything else.
                for sib in sibs:
                    frappe.db.set_value("Pick List Item", sib["name"], field, 1, update_modified=True)
                    # On load, also stamp the chosen truck onto the row (Data field).
                    if flag == "loaded" and truck:
                        frappe.db.set_value("Pick List Item", sib["name"], "custom_transit_truck", truck, update_modified=True)
                # The bucket has left the shelf now it's on the trolley/truck —
                # remove its Shelf Item so the shelf reflects reality.
                removed_shelves = removed_shelves + remove_bucket_from_shelf(bucket)
                updated = updated + 1
            else:
                missing = missing + 1
            i = i + 1
        frappe.db.commit()
        frappe.response["message"] = {
            "status": "success",
            "updated": updated,
            "missing": missing,
            "flag": flag,
            "removed_from_shelves": list(set(removed_shelves)),
        }

except Exception as e:
    frappe.response["message"] = {"status": "error", "message": str(e)}
