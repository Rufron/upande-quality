# Name: Delete Bucket Trip
# Type: Server Script · API
# API method: deleteBucketTrip
# Enabled: yes
# Modified: 2026-08-04 13:10:17
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# v16 port exists: upande_packhouse/api/transfer_control.py
# ----------------------------------------------------------------------
# Delete a Bucket Request Trip. Called via frappe.call -> args in form_dict.
name = frappe.form_dict.get("name")
if name and frappe.db.exists("Bucket Request Trip", name):
    frappe.delete_doc("Bucket Request Trip", name, ignore_permissions=True, force=1)
    frappe.db.commit()
    frappe.response["message"] = {"status": "success"}
else:
    frappe.response["message"] = {"status": "error", "message": "Trip not found."}
