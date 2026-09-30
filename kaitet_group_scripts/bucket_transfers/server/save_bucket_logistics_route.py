# Name: Save Bucket Logistics Route
# Type: Server Script · API
# API method: saveBucketLogisticsRoute
# Enabled: yes
# Modified: 2026-08-07 12:11:37
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# v16 port exists: upande_packhouse/api/transfer_control.py
# ----------------------------------------------------------------------
# Frappe Server Script (Type: API), api_method = saveBucketLogisticsRoute
# Upsert a truck's PLANNED ROUTE for a date — decided each morning, one doc per
# (date, vehicle). Each leg is a Link to an existing Farm Distance record (a road
# segment), stored in sequence; the farms a truck may serve are derived from the
# leg endpoints. Runs with ignore_permissions so planners don't need direct doctype
# rights (matches saveBucketTrip's pattern).
# Payload: { date?, vehicle, legs }  legs = "|~|"-joined Farm Distance record names,
# in travel order (e.g. "Kapkolia-Torongo|~|Torongo-Chepsito|~|Chepsito-Kapkolia").
# An empty `legs` clears the route (truck goes back to "unrestricted" for the day).
fd = frappe.form_dict
route_date = fd.get("date") or frappe.utils.today()
vehicle = fd.get("vehicle") or ""
legs_raw = fd.get("legs") or ""

if not vehicle:
    frappe.response["message"] = {"status": "error", "message": "A vehicle is required."}
else:
    leg_names = []
    parts = legs_raw.split("|~|") if legs_raw else []
    p = 0
    while p < len(parts):
        v = (parts[p] or "").strip()
        if v:
            leg_names.append(v)
        p = p + 1

    fd_map = {}
    if leg_names:
        rows = frappe.get_all("Farm Distance", filters={"name": ["in", leg_names]},
                              fields=["name", "from_farm", "to_farm", "distance_km"])
        i = 0
        while i < len(rows):
            fd_map[rows[i]["name"]] = rows[i]
            i = i + 1

    name = "BLR-" + str(route_date) + "-" + str(vehicle)
    if frappe.db.exists("Bucket Logistics Route", name):
        doc = frappe.get_doc("Bucket Logistics Route", name)
    else:
        doc = frappe.new_doc("Bucket Logistics Route")
        doc.route_date = route_date
        doc.vehicle = vehicle
    doc.set("legs", [])
    total_km = 0.0
    j = 0
    while j < len(leg_names):
        ln = leg_names[j]
        info = fd_map.get(ln)
        if info:
            row = doc.append("legs", {})
            row.leg = ln
            row.from_farm = info.get("from_farm") or ""
            row.to_farm = info.get("to_farm") or ""
            row.distance_km = float(info.get("distance_km") or 0)
            total_km = total_km + row.distance_km
        j = j + 1
    doc.total_km = total_km
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.response["message"] = {"status": "success", "name": doc.name, "legs": len(leg_names), "total_km": total_km}
