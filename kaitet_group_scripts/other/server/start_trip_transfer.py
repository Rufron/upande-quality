# Name: Start Trip Transfer
# Type: Server Script · API
# API method: start_trip_transfer
# Enabled: NO (disabled on the site)
# Modified: 2025-05-29 11:28:34
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
delivery_trip = frappe.form_dict.get('delivery_trip')
    
if not delivery_trip:
    frappe.throw("Delivery Trip parameter is required")

trip = frappe.get_doc('Delivery Trip', delivery_trip)

if trip.start_time:
    frappe.throw("Trip already started.")

frappe.db.set_value("Delivery Trip", delivery_trip, {
    "departure_time": frappe.utils.now_datetime(),
    "status": "In Transit"
})
frappe.db.commit()

trip.reload()

for stop in trip.delivery_stops:
    dn = frappe.get_doc("Delivery Note", stop.delivery_note)
    for item in dn.items:
        stock_entry = frappe.new_doc("Stock Entry")  # Only pass the Doctype name
        stock_entry.stock_entry_type = "Material Transfer"
        stock_entry.company = dn.company
        stock_entry.custom_farm = dn.custom_farm
        stock_entry.custom_business_unit = dn.custom_business_unit
        
        stock_entry.append("items", {
            "item_code": item.item_code,
            "qty": item.qty,

            # PLace this somewhere editable
            # Can fetch it from delvery note in the client script and pass 
            # it here 
            "s_warehouse": "Yogurt Coldroom - KR",
            "t_warehouse": "Goods In Transit - KR",
            
        })
        
        stock_entry.insert(ignore_permissions=True)
        stock_entry.submit()
