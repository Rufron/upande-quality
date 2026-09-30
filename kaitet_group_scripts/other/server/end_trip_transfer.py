# Name: End Trip Transfer
# Type: Server Script · API
# API method: end_trip_transfer
# Enabled: NO (disabled on the site)
# Modified: 2025-05-29 11:28:28
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
delivery_trip = frappe.form_dict.get('delivery_trip')

trip = frappe.get_doc('Delivery Trip', delivery_trip)

if trip.status != "In Transit":
    frappe.throw("Trip must be In Transit to end")

if trip.end_time:
    frappe.throw("Trip already completed")
    
frappe.db.set_value("Delivery Trip", delivery_trip, {
    "custom_arrival_time": frappe.utils.now_datetime(),
    "status": "Delivered"
})
frappe.db.commit()

trip.reload()

# Get all delivery notes in this trip and add their items
for stop in trip.delivery_stops:
    dn = frappe.get_doc("Delivery Note", stop.delivery_note)
    
    for item in dn.items:
        stock_entry = frappe.new_doc("Stock Entry")
        stock_entry.stock_entry_type = "Material Transfer"
        stock_entry.company = dn.company
        stock_entry.custom_farm = dn.custom_farm
        stock_entry.custom_business_unit = dn.custom_business_unit
        
        stock_entry.append("items", {
            "item_code": item.item_code,
            "qty": item.qty,
            
            # Make it easily editable
            # Pass from client script
            "s_warehouse": "Goods In Transit - KR",
            "t_warehouse": "Yoghurt Karen Store - KR"
        })

stock_entry.insert()
stock_entry.submit()

# return "Trip completed and yoghurt moved to Karen Yoghurt Store"
