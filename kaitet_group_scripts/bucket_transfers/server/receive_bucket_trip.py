# Name: Receive Bucket Trip
# Type: Server Script · API
# API method: receiveBucketTrip
# Enabled: yes
# Modified: 2026-08-22 15:06:21
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# v16 port exists: upande_packhouse/mobile/api.py, api/transfer_control.py
# ----------------------------------------------------------------------
name = frappe.form_dict.get("name")
if not name or not frappe.db.exists("Bucket Request Trip", name):
    frappe.response["message"] = {"status": "error", "message": "Trip not found."}
else:
    current = frappe.db.get_value("Bucket Request Trip", name, "status")
    if current != "Dispatched":
        frappe.response["message"] = {
            "status": "error",
            "message": "Trip is " + str(current) + "; only Dispatched trips can be received.",
        }
    else:
        frappe.db.set_value("Bucket Request Trip", name, {
            "status": "Received",
            "received_at": frappe.utils.now(),
        })
        frappe.db.commit()
        frappe.response["message"] = {"status": "success", "name": name, "trip_status": "Received"}
