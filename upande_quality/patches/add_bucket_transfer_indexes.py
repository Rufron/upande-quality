import frappe

# getInTransitBuckets (mobile/api.py) selects one delivery date's orders and
# their pick lists. Neither column was indexed, so every load scanned all of
# Order Pick List and grew slower with history. add_index is a no-op when the
# index already exists.


def execute():
	frappe.db.add_index("Sales Order", ["delivery_date"])
	frappe.db.add_index("Order Pick List", ["sales_order"])
