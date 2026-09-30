# Name: Save Bucket Trip
# Type: Server Script · API
# API method: saveBucketTrip
# Enabled: yes
# Modified: 2026-08-07 12:06:49
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# v16 port exists: upande_packhouse/api/transfer_control.py
# ----------------------------------------------------------------------
# Upsert a Bucket Request Trip from the transfer-control planner. Enforces the
# vehicle's capacity (trolleys x buckets/trolley) server-side. Runs with
# ignore_permissions so sales users don't need direct doctype rights.
# Called via frappe.call, so args arrive in frappe.form_dict (the RestrictedPython
# sandbox has no JSON parser). `orders` is a delimited string: rows joined by the
# record separator \x1e, fields within a row by the unit separator \x1f, in order
# [order_pick_list, order_name, customer, farm, varieties, buckets, stems, full_farm_buckets].
# full_farm_buckets (8th field, optional — defaults to buckets) is the size of the farm
# portion this row was picked from; buckets < full_farm_buckets means a PARTIAL transfer,
# stored explicitly (is_partial) so the balance is queryable straight off the trip data,
# not just inferred by cross-referencing the live order feed.
fd = frappe.form_dict
name = fd.get("name") or ""
vehicle = fd.get("vehicle") or ""
trip_date = fd.get("trip_date") or frappe.utils.today()
status = fd.get("status") or "Draft"
notes = fd.get("notes") or ""
collection_order = fd.get("collection_order") or ""
farm = fd.get("farm") or ""
orders_raw = fd.get("orders") or ""

items = []
if orders_raw:
    rows = orders_raw.split("\x1e")
    r = 0
    while r < len(rows):
        if rows[r]:
            f = rows[r].split("\x1f")
            if len(f) >= 7:
                full = f[7] if len(f) >= 8 else f[5]   # default: full = buckets (not partial)
                items.append({
                    "order_pick_list": f[0], "order_name": f[1], "customer": f[2],
                    "farm": f[3], "varieties": f[4], "buckets": f[5], "stems": f[6], "full": full})
        r = r + 1

total_buckets = 0
total_stems = 0
i = 0
while i < len(items):
    total_buckets = total_buckets + int(items[i].get("buckets") or 0)
    total_stems = total_stems + int(items[i].get("stems") or 0)
    i = i + 1

cap = 0
if vehicle:
    v = frappe.db.get_value("Vehicle", vehicle, ["custom_trolley_capacity", "custom_buckets_per_trolley"], as_dict=True)
    if v:
        cap = int(v.get("custom_trolley_capacity") or 0) * int(v.get("custom_buckets_per_trolley") or 0)

# Trips are never locked — the doctype is not submittable, so users can amend a trip
# continuously (no confirm/cancel cycle). Only capacity is enforced.
if cap and total_buckets > cap:
    frappe.response["message"] = {
        "status": "error", "reason": "over_capacity",
        "message": vehicle + " holds " + str(cap) + " buckets but this trip has " + str(total_buckets) + ".",
        "total_buckets": total_buckets, "capacity_buckets": cap}
else:
    if name and frappe.db.exists("Bucket Request Trip", name):
        doc = frappe.get_doc("Bucket Request Trip", name)
    else:
        doc = frappe.new_doc("Bucket Request Trip")
    doc.vehicle = vehicle
    doc.trip_date = trip_date
    doc.status = status
    doc.notes = notes
    doc.collection_order = collection_order
    doc.farm = farm
    doc.total_buckets = total_buckets
    doc.total_stems = total_stems
    doc.capacity_buckets = cap
    doc.set("orders", [])
    i = 0
    while i < len(items):
        it = items[i]
        row = doc.append("orders", {})
        row.order_pick_list = it.get("order_pick_list") or ""
        row.order_name = it.get("order_name") or ""
        row.customer = it.get("customer") or ""
        row.farm = it.get("farm") or ""
        row.varieties = it.get("varieties") or ""
        row.buckets = int(it.get("buckets") or 0)
        row.stems = int(it.get("stems") or 0)
        full_n = int(it.get("full") or 0) or row.buckets
        row.full_farm_buckets = full_n
        row.is_partial = 1 if row.buckets < full_n else 0
        i = i + 1
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.response["message"] = {
        "status": "success", "name": doc.name, "total_buckets": total_buckets,
        "total_stems": total_stems, "capacity_buckets": cap}
