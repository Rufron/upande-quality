# Name: Material Request (Transfer) Notification
# Type: Server Script · DocType Event
# DocType: Material Request
# Event: After Submit
# Enabled: NO (disabled on the site)
# Modified: 2025-12-03 01:31:01
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Check if the doc owner has the 'Sales Manager' role
owner_roles = frappe.db.get_all(
    "Has Role",
    filters={"parent": doc.owner},
    fields=["role"]
)
owner_role_names = [r["role"] for r in owner_roles]

# Proceed only if creator is Sales Manager 
# and request is Material Transfer + Yoghurt Packs
if (
    "Sales Manager" in owner_role_names and 
    doc.material_request_type == "Material Transfer" and 
    doc.custom_request_type == "Yoghurt Packs"
):
    # Get all active users with 'Manufacturing Manager' role
    manufacturing_managers = frappe.db.get_all(
        "Has Role",
        filters={"role": "Manufacturing Manager"},
        fields=["parent"]
    )

    for user in manufacturing_managers:
        frappe.get_doc({
            "doctype": "Notification Log",
            "subject": "Material Transfer Request Submitted",
            "for_user": user["parent"],
            "type": "Alert",
            "document_type": doc.doctype,
            "document_name": doc.name,
            "from_user": doc.owner,
            "email_content": f"A Material Request ({doc.name}) for Material Transfer (Yoghurt Packs) has been submitted by the Sales Manager. Please review it."
        }).insert(ignore_permissions=True)
