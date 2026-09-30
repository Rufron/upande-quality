# Name: Load Trolley In Truck
# Type: Server Script · API
# API method: loadTrolleyInTruck
# Enabled: yes
# Modified: 2026-07-01 11:34:06
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
try:
    data = frappe.form_dict.get("data")
    if not data:
        frappe.throw("No data provided")

    if isinstance(data, str):
        data = frappe.parse_json(data)

    trolley_id = data.get("trolley_id")
    custom_transit_truck = data.get("truck_id")

    if not trolley_id:
        frappe.throw("trolley_id is required")

    if not custom_transit_truck:
        frappe.throw("custom_transit_truck is required")

    rows = frappe.get_all(
        "Pick List Item",
        filters={
            "custom_trolley_id": trolley_id,
            "custom_loaded_in_trolley": 1,
            "custom_in_transit": 0
        },
        fields=["name", "parent", "custom_bucket"],
        order_by="parent asc"
    )

    if not rows:
        frappe.response["message"] = {
            "status": "error",
            "message": "No buckets found for trolley " + str(trolley_id)
        }
    else:
        opl_map = {}
        bucket_ids = []
        for row in rows:
            parent = row.get("parent")
            if parent not in opl_map:
                opl_map[parent] = []
            opl_map[parent].append(row.get("name"))
            bid = row.get("custom_bucket")
            if bid and bid not in bucket_ids:
                bucket_ids.append(bid)

        updated_count = 0
        for opl_name, child_names in opl_map.items():
            doc = frappe.get_doc("Order Pick List", opl_name)
            for loc_row in doc.locations:
                if loc_row.name in child_names:
                    loc_row.custom_in_transit = 1
                    loc_row.custom_transit_truck=custom_transit_truck
                    loc_row.custom_shelf = ""
                    updated_count += 1
            doc.save(ignore_permissions=True)

        # Bulk-remove these buckets from their Shelf child tables — once loaded to
        # a truck they have physically left the remote shelf. One save per Shelf.
        shelf_removed_count = 0
        if bucket_ids:
            lower_ids = [b.lower() for b in bucket_ids]
            shelf_items = frappe.get_all(
                "Shelf Item",
                filters={"bucket_id": ["in", bucket_ids]},
                fields=["parent"]
            )
            shelf_names = []
            for si in shelf_items:
                if si.parent not in shelf_names:
                    shelf_names.append(si.parent)
            for shelf_name in shelf_names:
                shelf_doc = frappe.get_doc("Shelf", shelf_name)
                kept = [it for it in shelf_doc.items if (it.bucket_id or "").lower() not in lower_ids]
                removed = len(shelf_doc.items) - len(kept)
                if removed > 0:
                    shelf_doc.items = kept
                    shelf_doc.save(ignore_permissions=True)
                    shelf_removed_count += removed

        frappe.db.commit()

        frappe.response["message"] = {
            "status": "success",
            "message": str(updated_count) + " bucket(s) from trolley " + str(trolley_id) + " loaded to " + str(custom_transit_truck) + ". " + str(shelf_removed_count) + " shelf row(s) removed."
        }

except Exception as e:
    frappe.log_error("loadTrolleyInTruck error", str(e))
    frappe.response["message"] = {
        "status": "error",
        "message": str(e)
    }
