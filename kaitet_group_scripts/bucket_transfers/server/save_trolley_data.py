# Name: Save Trolley Data
# Type: Server Script · API
# API method: saveTrolleyData
# Enabled: yes
# Modified: 2026-07-21 22:36:31
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
try:
    data = frappe.form_dict.get("data")
    if not data:
        frappe.throw("No data provided")
    if isinstance(data, str):
        data = frappe.parse_json(data)
    trolley_id = data.get("trolley_id")
    buckets = data.get("buckets", [])
    if not trolley_id:
        frappe.throw("trolley_id is required")
    if not buckets:
        frappe.throw("No buckets provided")
    updated_count = 0
    shelf_removed_count = 0
    errors = []
    for bucket in buckets:
        opl_name = bucket.get("opl_name")
        bucket_id = bucket.get("bucket_id")
        if not opl_name or not bucket_id:
            errors.append({"bucket_id": bucket_id, "error": "Missing opl_name or bucket_id"})
            continue
        doc = frappe.get_doc("Order Pick List", opl_name)
        found = False
        for row in doc.locations:
            if row.custom_bucket == bucket_id:
                row.custom_loaded_in_trolley = 1
                row.custom_trolley_id = trolley_id
                row.custom_awaiting_transfer = 0
                found = True
                break
        if found:
            doc.save(ignore_permissions=True)
            updated_count += 1

            # Remove bucket from shelf
            shelf_items = frappe.get_all(
                "Shelf Item",
                filters={"bucket_id": bucket_id},
                fields=["name", "parent"],
                limit=1
            )
            if shelf_items:
                shl_log = frappe.db.get_value("Shelving Log", {"shelf_item": shelf_items[0].name, "reason": "Shelved"}, "name")
                if shl_log:
                    frappe.db.set_value("Shelving Log", shl_log, {"reason": "Transferred (Trolley/Truck)", "removed_on": frappe.utils.now_datetime()}, update_modified=False)
                shelf_name = shelf_items[0].parent
                shelf_doc = frappe.get_doc("Shelf", shelf_name)
                shelf_doc.items = [item for item in shelf_doc.items if item.bucket_id.lower() != bucket_id.lower()]
                shelf_doc.save(ignore_permissions=True)
                shelf_removed_count += 1
        else:
            errors.append({"bucket_id": bucket_id, "error": "Bucket not found in OPL"})
    frappe.db.commit()
    msg = str(updated_count) + " bucket(s) loaded to trolley " + str(trolley_id)
    if shelf_removed_count > 0:
        msg += ". " + str(shelf_removed_count) + " bucket(s) removed from shelf"
    response = {
        "status": "success",
        "message": msg,
        "updated_count": updated_count,
        "shelf_removed_count": shelf_removed_count,
    }
    if errors:
        response["errors"] = errors
        if updated_count == 0:
            response["status"] = "error"
            response["message"] = "No buckets were saved"
    frappe.response["message"] = response
except Exception as e:
    frappe.log_error("saveTrolleyData error", str(e))
    frappe.response["message"] = {
        "status": "error",
        "message": str(e)
    }
