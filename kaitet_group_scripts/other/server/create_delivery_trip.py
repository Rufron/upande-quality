# Name: Create delivery trip
# Type: Server Script · DocType Event
# DocType: Delivery Note
# Event: Before Save
# Enabled: NO (disabled on the site)
# Modified: 2026-03-26 07:04:20
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Skip Coffee Harvest delivery notes
if doc.custom_business_unit == "Endebess Coffee":
    pass
else:
    if doc.workflow_state == "In Transit":
        
        # Create Delivery Trip and start trip
        delivery_trip = frappe.new_doc("Delivery Trip")
        
        delivery_trip.company = doc.company
        delivery_trip.custom_farm = doc.custom_farm
        delivery_trip.custom_business_unit = doc.custom_business_unit
        delivery_trip.driver = doc.custom_departing_driver
        delivery_trip.driver_name = doc.custom_departing_driver_name
        delivery_trip.vehicle = doc.custom_departing_vehicle
        delivery_trip.departure_time = doc.custom_departing_time
    
        customer_doc = frappe.get_doc("Customer", doc.customer)
        customer_address = customer_doc.customer_primary_address
        
        delivery_trip.append("delivery_stops", {
            "customer": doc.customer,
            "address": customer_address,
            "delivery_note": doc.name
        })
    
        for stop in delivery_trip.delivery_stops:
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
                    "uom": item.uom,
        
                    # PLace this somewhere editable
                    # Can fetch it from delvery note in the client script and pass 
                    # it here 
                    "s_warehouse": "Yogurt Coldroom - KR",
                    "t_warehouse": "Goods In Transit - KR",
                    
                })
                
                stock_entry.insert(ignore_permissions=True)
                stock_entry.submit()
        
        delivery_trip.insert()
        delivery_trip.submit()
        
        doc.set_warehouse = "Goods In Transit - KR"
        for item in doc.items:
            item.warehouse = "Goods In Transit - KR"
    
        delivery_trip.db_set("status", "In Transit")
        delivery_trip.reload()
        
    if doc.workflow_state == "Arrived":
        # End trip and update arrival time
        delivery_trip_name = frappe.db.get_value("Delivery Stop", 
            {"delivery_note": doc.name}, "parent")
            
        if delivery_trip_name:
            delivery_trip = frappe.get_doc("Delivery Trip", delivery_trip_name)
            delivery_trip.custom_arrival_time = frappe.utils.now_datetime()
            
            # Update delivery trip status
            delivery_trip.db_set("status", "Completed")
            delivery_trip.reload()
        else:
            frappe.throw("No delivery trip found for this delivery note")
