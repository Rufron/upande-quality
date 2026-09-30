# Name: Get Saved Trolleys
# Type: Server Script · API
# API method: getSavedTrolleys
# Enabled: yes
# Modified: 2026-06-25 11:23:54
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
try:
    farm = frappe.form_dict.get("farm")
    if not farm:
        frappe.throw("farm is required")

    # Today's allocations only — OPLs created today. A trolley saved against an
    # older (yesterday's) pick list must not linger in today's list.
    opl_today = frappe.get_all(
        "Order Pick List",
        filters={"creation": [">=", frappe.utils.nowdate()]},
        fields=["name"],
        pluck="name",
    )

    rows = frappe.get_all(
        "Pick List Item",
        filters={
            "warehouse": ["like", farm + "%"],
            "custom_loaded_in_trolley": 1,
            "custom_in_transit": 0,
            "custom_trolley_id": ["is", "set"],
            "parent": ["in", opl_today],
        },
        fields=[
            "parent as opl_name",
            "custom_trolley_id as trolley_id",
            "custom_bucket as bucket_id",
            "item_code",
            "item_name",
            "custom_shelf as shelf_location",
            "custom_stem_length as stem_length",
            "qty",
            "uom",
            "custom_truck as truck",
            "warehouse"
        ],
        order_by="custom_trolley_id asc, idx asc"
    )

    trolley_map = {}
    for row in rows:
        tid = row.get("trolley_id")
        if tid not in trolley_map:
            trolley_map[tid] = {
                "trolley_id": tid,
                "truck_id": "",
                "buckets": []
            }
        trolley_map[tid]["buckets"].append({
            "opl_name": row.get("opl_name"),
            "bucket_id": row.get("bucket_id"),
            "item_code": row.get("item_code"),
            "item_name": row.get("item_name"),
            "shelf_location": row.get("shelf_location"),
            "stem_length": row.get("stem_length"),
            "qty": row.get("qty"),
            "uom": row.get("uom"),
            "truck": row.get("truck"),
            "warehouse": row.get("warehouse")
        })

    trolleys = list(trolley_map.values())

    frappe.response["message"] = {
        "status": "success",
        "data": trolleys
    }

except Exception as e:
    frappe.log_error("getSavedTrolleys error", str(e))
    frappe.response["message"] = {
        "status": "error",
        "message": str(e)
    }
