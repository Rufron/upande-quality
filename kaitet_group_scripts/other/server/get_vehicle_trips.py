# Name: Get Vehicle Trips
# Type: Server Script · API
# API method: get_vehicle_trips
# Enabled: yes
# Modified: 2026-06-08 10:30:00
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
try:
    DEG = 3.141592653589793 / 180.0
    EARTH_KM = 6371.0
    EARTH_M = 6371000.0
    STOP_RADIUS_M = 30.0
    STOP_SECS = 10 * 60.0
    MAX_KMH = 140.0
    SPEED_LIMIT = 80.0
    MIN_TRIP_KM = 0.2
    LAT_MIN = -5.0
    LAT_MAX = 5.0
    LNG_MIN = 33.0
    LNG_MAX = 42.0

    vehicle   = frappe.form_dict.get("vehicle")
    imei_arg  = frappe.form_dict.get("imei")
    from_date = frappe.form_dict.get("from_date")
    to_date   = frappe.form_dict.get("to_date")

    # Build filter on the GPS table. Prefer vehicle (it's the link);
    # fall back to imei if only that was supplied.
    flt = {}
    if vehicle:
        flt["vehicle"] = vehicle
    elif imei_arg:
        flt["imei"] = imei_arg

    if not flt:
        frappe.response["message"] = {"error": "vehicle or imei is required"}
    else:
        if from_date and to_date:
            flt["timestamp"] = ["between", [from_date + " 00:00:00", to_date + " 23:59:59"]]
        elif from_date:
            flt["timestamp"] = [">=", from_date + " 00:00:00"]
        elif to_date:
            flt["timestamp"] = ["<=", to_date + " 23:59:59"]

        rows = frappe.get_all("GPS",
            filters=flt,
            fields=["timestamp", "latitude", "longitude", "imei", "vehicle"],
            order_by="timestamp asc",
            limit_page_length=0)

        resolved_imei = imei_arg
        resolved_vehicle = vehicle

        # ---- clean fixes ----
        fixes = []
        for r in rows:
            if not r.timestamp:
                continue
            lat = None
            lng = None
            try:
                lat = float(r.latitude)
                lng = float(r.longitude)
            except (TypeError, ValueError):
                lat = None
            if lat is None:
                continue
            if lat < LAT_MIN or lat > LAT_MAX or lng < LNG_MIN or lng > LNG_MAX:
                continue
            if not resolved_imei and r.imei:
                resolved_imei = r.imei
            if not resolved_vehicle and r.vehicle:
                resolved_vehicle = r.vehicle
            fixes.append({"t": str(r.timestamp), "lat": lat, "lng": lng})

        n = len(fixes)

        # ---- per-segment distance + speed (for stats, not stop detection) ----
        idx = 1
        while idx < n:
            a = fixes[idx - 1]
            b = fixes[idx]
            mlat = (a["lat"] + b["lat"]) / 2.0 * DEG
            ct = 1.0
            tot = 1.0
            x2 = mlat * mlat
            cn = 1
            while cn <= 8:
                ct = -ct * x2 / ((2 * cn - 1) * (2 * cn))
                tot += ct
                cn += 1
            dx = (b["lng"] - a["lng"]) * DEG * tot
            dy = (b["lat"] - a["lat"]) * DEG
            dist_km = ((dx * dx + dy * dy) ** 0.5) * EARTH_KM
            dt = 0.0
            try:
                dt = (frappe.utils.get_datetime(b["t"]) - frappe.utils.get_datetime(a["t"])).total_seconds()
            except Exception:
                dt = 0.0
            kmh = (dist_km / dt * 3600.0) if dt > 0 else 0.0
            b["seg_km"] = dist_km
            b["seg_dt"] = dt
            b["kmh"] = kmh if kmh <= MAX_KMH else 0.0
            idx += 1
        if n > 0:
            fixes[0]["seg_km"] = 0.0
            fixes[0]["seg_dt"] = 0.0
            fixes[0]["kmh"] = 0.0

        # ---- POSITION-BASED stop detection + trip segmentation ----
        trips = []
        stops_all = []
        i = 0
        cur_start_i = None

        while i < n:
            anchor = fixes[i]
            j = i + 1
            while j < n:
                mlat = (anchor["lat"] + fixes[j]["lat"]) / 2.0 * DEG
                ct = 1.0
                tot = 1.0
                x2 = mlat * mlat
                cn = 1
                while cn <= 8:
                    ct = -ct * x2 / ((2 * cn - 1) * (2 * cn))
                    tot += ct
                    cn += 1
                dx = (fixes[j]["lng"] - anchor["lng"]) * DEG * tot
                dy = (fixes[j]["lat"] - anchor["lat"]) * DEG
                d_m = ((dx * dx + dy * dy) ** 0.5) * EARTH_M
                if d_m > STOP_RADIUS_M:
                    break
                j += 1
            cluster_secs = 0.0
            if j - 1 > i:
                try:
                    cluster_secs = (frappe.utils.get_datetime(fixes[j-1]["t"]) - frappe.utils.get_datetime(anchor["t"])).total_seconds()
                except Exception:
                    cluster_secs = 0.0

            if cluster_secs >= STOP_SECS:
                if cur_start_i is not None and i > cur_start_i:
                    trips.append({"a": cur_start_i, "b": i})
                stops_all.append({
                    "lat": anchor["lat"], "lng": anchor["lng"],
                    "from": anchor["t"], "to": fixes[j-1]["t"],
                    "minutes": round(cluster_secs / 60.0, 1),
                })
                cur_start_i = j
                i = j
            else:
                if cur_start_i is None:
                    cur_start_i = i
                i += 1

        if cur_start_i is not None and (n - 1) > cur_start_i:
            trips.append({"a": cur_start_i, "b": n - 1})

        # ---- build trip summaries ----
        out_trips = []
        total_km = 0.0
        total_stops = len(stops_all)
        trip_no = 0
        ti = 0
        while ti < len(trips):
            seg = trips[ti]
            a = seg["a"]
            b = seg["b"]
            dist_km = 0.0
            max_kmh = 0.0
            speed_sum = 0.0
            speed_n = 0
            move_secs = 0.0
            idle_secs = 0.0
            speeding = 0
            path = []
            path.append([fixes[a]["lat"], fixes[a]["lng"], 0.0])
            k = a + 1
            while k <= b:
                f = fixes[k]
                dist_km += f["seg_km"]
                kmh = f["kmh"]
                if kmh >= 3.0:
                    move_secs += f["seg_dt"]
                    speed_sum += kmh
                    speed_n += 1
                    if kmh > max_kmh:
                        max_kmh = kmh
                    if kmh > SPEED_LIMIT:
                        speeding += 1
                else:
                    idle_secs += f["seg_dt"]
                path.append([f["lat"], f["lng"], round(kmh, 1)])
                k += 1

            if dist_km < MIN_TRIP_KM:
                ti += 1
                continue

            trip_no += 1
            start_t = fixes[a]["t"]
            end_t = fixes[b]["t"]
            dur = 0.0
            try:
                dur = (frappe.utils.get_datetime(end_t) - frappe.utils.get_datetime(start_t)).total_seconds()
            except Exception:
                dur = 0.0
            avg_kmh = (speed_sum / speed_n) if speed_n else 0.0

            trip_stops = []
            si = 0
            while si < len(stops_all):
                s = stops_all[si]
                if s["from"] >= start_t and s["to"] <= end_t:
                    trip_stops.append(s)
                si += 1

            out_trips.append({
                "index": trip_no,
                "date": (start_t or "")[0:10],
                "start": start_t,
                "end": end_t,
                "distance_km": round(dist_km, 2),
                "max_speed_kmh": round(max_kmh, 1),
                "avg_moving_kmh": round(avg_kmh, 1),
                "duration_minutes": round(dur / 60.0, 1),
                "moving_minutes": round(move_secs / 60.0, 1),
                "idle_minutes": round(idle_secs / 60.0, 1),
                "stop_count": len(trip_stops),
                "stops": trip_stops,
                "speeding_segments": speeding,
                "over_limit": (max_kmh > SPEED_LIMIT),
                "speed_limit_kmh": 80,
                "path": path,
                "point_count": (b - a + 1),
            })
            total_km += dist_km
            ti += 1

        assigned = 0
        ai = 0
        while ai < len(out_trips):
            assigned += out_trips[ai]["stop_count"]
            ai += 1
        parked_count = total_stops - assigned
        if parked_count < 0:
            parked_count = 0

        frappe.response["message"] = {
            "vehicle": resolved_vehicle,
            "imei": resolved_imei,
            "from_date": from_date,
            "to_date": to_date,
            "source": "GPS",
            "fix_count": n,
            "trip_count": len(out_trips),
            "total_distance_km": round(total_km, 2),
            "total_stops": total_stops,
            "parked_stop_count": parked_count,
            "stop_radius_m": 30,
            "stop_threshold_minutes": 10,
            "trips": out_trips,
        }

except Exception as e:
    frappe.response["message"] = {
        "error": "server_exception",
        "detail": str(e),
    }
