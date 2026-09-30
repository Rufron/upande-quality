# Name: Get Farm Planned Trips
# Type: Server Script · API
# API method: getFarmPlannedTrips
# Enabled: yes
# Modified: 2026-08-04 15:10:54
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Frappe Server Script (Type: API), api_method = getFarmPlannedTrips
# For the cold-store attendant on the Bucket Requests app: the upcoming planned
# trips (Bucket Request Trip) that will collect buckets from THIS farm. For each
# trip it returns the WHOLE collection route with a live loading status per stop,
# so the attendant can see who is holding up the run — a farm whose trolleys are
# not yet loaded will delay everyone after it. READ-ONLY and standalone: it does
# NOT touch the production allocation/trolley scripts.
# Payload: { "farm": "<farm>" }  (JSON body, like fetchAllocatedBuckets)
payload = frappe.request.get_json() or {}
farm_name = payload.get('farm') if payload else None
if not farm_name:
    farm_name = frappe.form_dict.get('farm')

if not farm_name:
    frappe.response["message"] = {"status": "error", "message": "farm is required", "data": []}
else:
    like = "%" + farm_name + "%"
    # Trips (not yet dispatched) that include at least one order row from this farm.
    name_rows = frappe.db.sql("""
        SELECT DISTINCT t.name AS name
        FROM `tabBucket Request Trip` t
        INNER JOIN `tabBucket Request Trip Order` o ON o.parent = t.name
        WHERE t.status != 'Dispatched'
          AND ( o.farm = %(farm)s OR o.farm LIKE %(like)s OR %(farm)s LIKE CONCAT('%%', o.farm, '%%') )
    """, {"farm": farm_name, "like": like}, as_dict=True)
    trip_names = [r['name'] for r in name_rows]

    if not trip_names:
        frappe.response["message"] = {"status": "success", "data": [], "farm": farm_name}
    else:
        headers = frappe.get_all(
            "Bucket Request Trip",
            filters={"name": ["in", trip_names]},
            fields=["name", "vehicle", "trip_date", "status", "collection_order",
                    "total_buckets", "capacity_buckets"],
            order_by="trip_date asc, name asc",
            limit_page_length=0,
        )
        # ALL order rows for these trips (every farm on the route, not just this one).
        rows = frappe.get_all(
            "Bucket Request Trip Order",
            filters={"parent": ["in", trip_names]},
            fields=["parent", "order_pick_list", "order_name", "customer", "farm",
                    "varieties", "buckets", "stems"],
            limit_page_length=0,
        )

        # ── Live loading state per (OPL, farm-short) from Pick List Item flags ──
        # awaiting (on a shelf, not yet on a trolley) -> loaded (on a trolley) ->
        # in_transit (picked up by the truck) -> shelved (arrived at packhouse).
        opls = []
        seen_opl = {}
        i = 0
        while i < len(rows):
            op = rows[i].get('order_pick_list')
            if op and op not in seen_opl:
                seen_opl[op] = 1
                opls.append(op)
            i = i + 1

        portion_state = {}  # "opl||farmShort" -> {awaiting, loaded, transit, shelved, total}
        if opls:
            pli = frappe.get_all(
                "Pick List Item",
                filters={"parent": ["in", opls], "parenttype": "Order Pick List",
                         "custom_bucket": ["!=", ""]},
                fields=["parent", "custom_bucket", "warehouse",
                        "custom_awaiting_transfer", "custom_loaded_in_trolley",
                        "custom_in_transit", "custom_shelved"],
                limit_page_length=0,
            )
            seen_bkt = {}
            p = 0
            while p < len(pli):
                it = pli[p]
                wh = it.get('warehouse') or ''
                farm_short = wh.split(' ')[0] if wh else ''
                op = it.get('parent')
                bkt = it.get('custom_bucket') or ''
                dk = str(op) + '||' + str(bkt).lower()
                if bkt and (dk in seen_bkt):
                    p = p + 1
                    continue
                if bkt:
                    seen_bkt[dk] = 1
                key = str(op) + '||' + farm_short
                if key not in portion_state:
                    portion_state[key] = {"awaiting": 0, "loaded": 0, "transit": 0, "shelved": 0, "total": 0}
                st = portion_state[key]
                st["total"] = st["total"] + 1
                if int(it.get('custom_shelved') or 0):
                    st["shelved"] = st["shelved"] + 1
                elif int(it.get('custom_in_transit') or 0):
                    st["transit"] = st["transit"] + 1
                elif int(it.get('custom_loaded_in_trolley') or 0):
                    st["loaded"] = st["loaded"] + 1
                else:
                    st["awaiting"] = st["awaiting"] + 1
                p = p + 1

        # Group order rows by trip.
        by_trip = {}
        i = 0
        while i < len(rows):
            r = rows[i]
            tn = r.get('parent')
            if tn not in by_trip:
                by_trip[tn] = []
            by_trip[tn].append(r)
            i = i + 1

        data = []
        h = 0
        while h < len(headers):
            hd = headers[h]
            tn = hd['name']
            trip_rows = by_trip.get(tn) or []

            # Aggregate per farm on this trip: planned buckets + live state + this
            # farm's own order lines (for the Requests-tab mapping).
            farm_map = {}
            farm_order = []
            your_orders = []
            j = 0
            while j < len(trip_rows):
                r = trip_rows[j]
                f = r.get('farm') or '?'
                if f not in farm_map:
                    farm_map[f] = {"planned": 0, "awaiting": 0, "loaded": 0,
                                   "transit": 0, "shelved": 0, "total": 0}
                    farm_order.append(f)
                fm = farm_map[f]
                pb = int(r.get('buckets') or 0)
                fm["planned"] = fm["planned"] + pb
                key = str(r.get('order_pick_list')) + '||' + f
                ps = portion_state.get(key)
                if ps:
                    fm["awaiting"] = fm["awaiting"] + ps["awaiting"]
                    fm["loaded"] = fm["loaded"] + ps["loaded"]
                    fm["transit"] = fm["transit"] + ps["transit"]
                    fm["shelved"] = fm["shelved"] + ps["shelved"]
                    fm["total"] = fm["total"] + ps["total"]
                is_my_farm = (f == farm_name) or (f and f in farm_name) or (f and farm_name in f)
                if is_my_farm:
                    your_orders.append({
                        "opl": r.get('order_pick_list') or '',
                        "order_name": r.get('order_name') or r.get('order_pick_list') or '',
                        "customer": r.get('customer') or '',
                        "varieties": r.get('varieties') or '',
                        "buckets": pb,
                    })
                j = j + 1

            # Order the farms by the collection route, then any extras.
            seq_raw = (hd.get('collection_order') or '').split(',') if hd.get('collection_order') else []
            seq = []
            s = 0
            while s < len(seq_raw):
                v = seq_raw[s].strip()
                if v:
                    seq.append(v)
                s = s + 1
            ordered = []
            s = 0
            while s < len(seq):
                if seq[s] in farm_map:
                    ordered.append(seq[s])
                s = s + 1
            s = 0
            while s < len(farm_order):
                if farm_order[s] not in ordered:
                    ordered.append(farm_order[s])
                s = s + 1

            # Build the stops with a live status label; flag the first stop that is
            # not yet ready/moving as the process bottleneck.
            stops = []
            bottleneck_found = 0
            trip_transit = 0
            your_stop = 0
            farm_buckets = 0
            s = 0
            while s < len(ordered):
                f = ordered[s]
                fm = farm_map[f]
                total = fm["total"]
                awaiting = fm["awaiting"]
                loaded = fm["loaded"]
                transit = fm["transit"]
                shelved = fm["shelved"]
                done_ish = loaded + transit + shelved

                status = "waiting"
                if total > 0 and shelved == total:
                    status = "done"
                elif total > 0 and (transit + shelved) == total:
                    status = "transit"
                elif awaiting == 0 and done_ish > 0 and total > 0:
                    status = "ready"
                elif done_ish > 0:
                    status = "loading"
                else:
                    status = "waiting"

                delaying = 0
                if bottleneck_found == 0 and (status == "waiting" or status == "loading"):
                    delaying = 1
                    bottleneck_found = 1
                if transit > 0 or shelved > 0:
                    trip_transit = 1

                is_you = 0
                if (f == farm_name) or (f and f in farm_name) or (f and farm_name in f):
                    is_you = 1
                    your_stop = s + 1
                    farm_buckets = fm["planned"]

                stops.append({
                    "farm": f, "stop": s + 1, "is_you": is_you,
                    "planned": fm["planned"], "total": total, "awaiting": awaiting,
                    "loaded": loaded, "transit": transit, "shelved": shelved,
                    "done_count": done_ish, "status": status, "delaying": delaying,
                })
                s = s + 1

            data.append({
                "trip": tn,
                "vehicle": hd.get('vehicle') or '',
                "trip_date": str(hd.get('trip_date') or ''),
                "status": hd.get('status') or 'Draft',
                "capacity": int(hd.get('capacity_buckets') or 0),
                "trip_buckets": int(hd.get('total_buckets') or 0),
                "in_transit": trip_transit,
                "farm_buckets": farm_buckets,
                "your_stop": your_stop,
                "total_stops": len(stops),
                "stops": stops,
                "orders": your_orders,
            })
            h = h + 1

        frappe.response["message"] = {"status": "success", "data": data, "farm": farm_name}
