# Name: Fetch Transit Time Pivot Data
# Type: Server Script · API
# API method: fetchTransitTimePivotData
# Enabled: yes
# Modified: 2026-04-16 14:43:01
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# ─────────────────────────────────────────────────────────────────────────────
# Frappe Server Script  –  fetchTransitTimePivotData
# Type : API
# API Method : fetchTransitTimePivotData
#
# TWO-PASS APPROACH for performance:
#   Pass 1: Fetch all Receiving entries for the date range (fast, date-indexed)
#   Pass 2: Batch-fetch Harvesting, Quarantine, Shelf data using IN (bucket_ids)
#   Assemble rows in Python — avoids per-row correlated subqueries entirely.
#
# No imports. No augmented assignment. No in-place slicing. No returns.
# No hasattr. No isinstance with builtins not exposed in Frappe sandbox.
# No augmented operators (+=, -=). Use x = x + y instead.
# ─────────────────────────────────────────────────────────────────────────────

try:
    # ── Parse incoming filters ──────────────────────────────────────────────
    year        = frappe.form_dict.get("year", "")
    farm        = frappe.form_dict.get("farm", "")
    week        = frappe.form_dict.get("week", "")
    variety     = frappe.form_dict.get("variety", "")
    greenhouse  = frappe.form_dict.get("greenhouse", "")
    from_date   = frappe.form_dict.get("from_date", "")
    to_date     = frappe.form_dict.get("to_date", "")

    # ── Build WHERE clause for Receiving entries ────────────────────────────
    conditions = [
        "recv.docstatus = 1",
        "recv.stock_entry_type IN ('Receiving', 'Late Receipt')",
        "recv.custom_received_bucket_id IS NOT NULL",
        "recv.custom_received_bucket_id != ''"
    ]
    values = {}

    if year:
        conditions.append("YEAR(recv.posting_date) = %(year)s")
        values["year"] = int(year)

    if farm:
        conditions.append("recv.custom_farm = %(farm)s")
        values["farm"] = farm

    if week:
        conditions.append("WEEK(recv.posting_date, 1) = %(week)s")
        values["week"] = int(week)

    if variety:
        conditions.append("ri.item_code = %(variety)s")
        values["variety"] = variety

    if greenhouse:
        conditions.append("recv.custom_greenhouse = %(greenhouse)s")
        values["greenhouse"] = greenhouse

    if from_date:
        conditions.append("recv.posting_date >= %(from_date)s")
        values["from_date"] = from_date

    if to_date:
        conditions.append("recv.posting_date <= %(to_date)s")
        values["to_date"] = to_date

    where_clause = " AND ".join(conditions)

    # ════════════════════════════════════════════════════════════════════════
    # PASS 1: Fetch Receiving entries + item + employee (all fast joins)
    # ════════════════════════════════════════════════════════════════════════

    recv_sql = (
        "SELECT"
        "  recv.name AS recv_name,"
        "  recv.posting_date,"
        "  recv.posting_time,"
        "  recv.custom_farm,"
        "  recv.custom_greenhouse,"
        "  recv.custom_received_bucket_id,"
        "  recv.custom_harvester,"
        "  ri.t_warehouse,"
        "  ri.item_code,"
        "  ri.qty,"
        "  IFNULL(emp.employee_name, recv.custom_harvester) AS grower_name"
        " FROM `tabStock Entry` recv"
        " INNER JOIN `tabStock Entry Detail` ri"
        "   ON ri.parent = recv.name AND ri.idx = 1"
        " LEFT JOIN `tabEmployee` emp"
        "   ON emp.name = recv.custom_harvester"
        " WHERE " + where_clause +
        " ORDER BY recv.posting_date DESC, recv.posting_time DESC"
    )

    recv_rows = frappe.db.sql(recv_sql, values, as_dict=1)

    if not recv_rows:
        frappe.response["message"] = {
            "success": True,
            "data": [],
            "record_count": 0,
            "filter_options": {}
        }

    else:
        # ── Collect all bucket IDs and posting dates ────────────────────────
        bucket_ids = []
        posting_dates = []
        for r in recv_rows:
            bid = r.get("custom_received_bucket_id") or ""
            if bid and bid not in bucket_ids:
                bucket_ids.append(bid)
            pd = str(r.get("posting_date") or "")
            if pd and pd not in posting_dates:
                posting_dates.append(pd)

        # ── Build safe IN clause placeholders ───────────────────────────────
        # Frappe db.sql uses %(name)s params, but for IN we need positional
        # Use frappe.db.escape to safely quote each value
        def escape_list(items):
            parts = []
            for item in items:
                parts.append(frappe.db.escape(item))
            return ", ".join(parts)

        bucket_in = escape_list(bucket_ids)
        dates_in = escape_list(posting_dates)

        # ════════════════════════════════════════════════════════════════════
        # PASS 2A: Batch fetch Harvesting entries (same bucket + same day)
        # ════════════════════════════════════════════════════════════════════

        harvest_map = {}
        if bucket_ids:
            harv_sql = (
                "SELECT"
                "  h.custom_bucket_id AS bucket_id,"
                "  h.posting_date,"
                "  h.posting_time,"
                "  h.creation"
                " FROM `tabStock Entry` h"
                " WHERE h.custom_bucket_id IN (" + bucket_in + ")"
                "   AND h.stock_entry_type = 'Harvesting'"
                "   AND h.docstatus = 1"
                "   AND h.posting_date IN (" + dates_in + ")"
                " ORDER BY h.creation DESC"
            )
            harv_rows = frappe.db.sql(harv_sql, as_dict=1)

            # Keep only the latest per bucket+date
            for hr in harv_rows:
                hkey = str(hr.get("bucket_id") or "") + "|" + str(hr.get("posting_date") or "")
                if hkey not in harvest_map:
                    harvest_map[hkey] = {
                        "posting_date": hr.get("posting_date"),
                        "posting_time": hr.get("posting_time"),
                    }

        # ════════════════════════════════════════════════════════════════════
        # PASS 2B: Batch fetch Quarantine entries
        # ════════════════════════════════════════════════════════════════════

        quarantine_map = {}
        if bucket_ids:
            quar_sql = (
                "SELECT"
                "  q.custom_received_bucket_id AS bucket_id,"
                "  q.posting_date,"
                "  q.posting_time,"
                "  q.creation"
                " FROM `tabStock Entry` q"
                " WHERE q.custom_received_bucket_id IN (" + bucket_in + ")"
                "   AND q.stock_entry_type = 'Receiving Quarantined'"
                "   AND q.docstatus = 1"
                " ORDER BY q.creation DESC"
            )
            quar_rows = frappe.db.sql(quar_sql, as_dict=1)

            for qr in quar_rows:
                qkey = str(qr.get("bucket_id") or "")
                if qkey not in quarantine_map:
                    quarantine_map[qkey] = {
                        "posting_date": qr.get("posting_date"),
                        "posting_time": qr.get("posting_time"),
                    }

        # ════════════════════════════════════════════════════════════════════
        # PASS 2C: Batch fetch Shelf Items
        # ════════════════════════════════════════════════════════════════════

        shelf_map = {}
        if bucket_ids:
            shelf_sql = (
                "SELECT"
                "  si.bucket_id,"
                "  si.date_added,"
                "  si.creation"
                " FROM `tabShelf Item` si"
                " WHERE si.bucket_id IN (" + bucket_in + ")"
                " ORDER BY si.creation DESC"
            )
            shelf_rows = frappe.db.sql(shelf_sql, as_dict=1)

            for sr in shelf_rows:
                skey = str(sr.get("bucket_id") or "")
                if skey not in shelf_map:
                    shelf_map[skey] = {
                        "date_added": sr.get("date_added"),
                    }

        # ════════════════════════════════════════════════════════════════════
        # PASS 3: Assemble rows
        # ════════════════════════════════════════════════════════════════════

        # Helper: extract HH:MM:SS from a time/timedelta value
        def fmt_time(t_val):
            if not t_val:
                return ""
            raw = str(t_val)
            if "," in raw:
                raw = raw.split(",")[1].strip()
            if "." in raw:
                raw = raw.split(".")[0]
            parts = raw.strip().split(":")
            if len(parts) == 3:
                return str(parts[0]).zfill(2) + ":" + str(parts[1]).zfill(2) + ":" + str(parts[2]).zfill(2)
            if len(parts) == 2:
                return str(parts[0]).zfill(2) + ":" + str(parts[1]).zfill(2) + ":00"
            return ""

        # Helper: compute minutes between two (date, time) pairs
        def mins_between(date1, time1, date2, time2):
            if not date1 or not time1 or not date2 or not time2:
                return None
            try:
                dt1 = frappe.utils.get_datetime(str(date1) + " " + fmt_time(time1))
                dt2 = frappe.utils.get_datetime(str(date2) + " " + fmt_time(time2))
                diff = dt2 - dt1
                return int(diff.total_seconds()) // 60
            except:
                return None

        # Helper: compute minutes between a datetime and a (date, time) pair
        def mins_from_datetime(dt_val, date2, time2):
            if not dt_val or not date2 or not time2:
                return None
            try:
                dt1 = frappe.utils.get_datetime(str(dt_val))
                dt2 = frappe.utils.get_datetime(str(date2) + " " + fmt_time(time2))
                diff = dt1 - dt2
                return int(diff.total_seconds()) // 60
            except:
                return None

        rows_out = []
        for r in recv_rows:
            bucket_id = r.get("custom_received_bucket_id") or ""
            recv_date = r.get("posting_date")
            recv_time = r.get("posting_time")

            # Lookup harvest
            hkey = bucket_id + "|" + str(recv_date or "")
            harv = harvest_map.get(hkey, {})
            harv_date = harv.get("posting_date")
            harv_time = harv.get("posting_time")

            # Lookup quarantine
            quar = quarantine_map.get(bucket_id, {})
            quar_date = quar.get("posting_date")
            quar_time = quar.get("posting_time")

            # Lookup shelf
            shelf = shelf_map.get(bucket_id, {})
            shelf_dt = shelf.get("date_added")

            # Compute intervals
            transit_min = mins_between(harv_date, harv_time, recv_date, recv_time)
            recv_to_quar_min = mins_between(recv_date, recv_time, quar_date, quar_time)
            recv_to_shelf_min = mins_from_datetime(shelf_dt, recv_date, recv_time) if shelf_dt else None
            quar_to_shelf_min = None
            if quar_date and quar_time and shelf_dt:
                quar_to_shelf_min = mins_from_datetime(shelf_dt, quar_date, quar_time)

            # Format shelf time
            shelf_time_str = ""
            if shelf_dt:
                try:
                    shelf_time_str = fmt_time(str(shelf_dt).split(" ")[1]) if " " in str(shelf_dt) else ""
                except:
                    shelf_time_str = ""

            row_out = {
                "Date": str(recv_date) if recv_date else "",
                "Year": int(str(recv_date)[:4]) if recv_date else 0,
                "Week": "W" + str(frappe.utils.get_datetime(str(recv_date)).isocalendar()[1]).zfill(2) if recv_date else "",
                "Farm": r.get("custom_farm") or "",
                "GH": r.get("custom_greenhouse") or "",
                "Coldroom": r.get("t_warehouse") or "",
                "Variety": r.get("item_code") or "",
                "Grower": r.get("grower_name") or "",
                "Tag ID": bucket_id,
                "Stems": int(r.get("qty") or 0),
                "Harvest Time": fmt_time(harv_time),
                "Receive Time": fmt_time(recv_time),
                "Quarantine Time": fmt_time(quar_time),
                "Shelving Time": shelf_time_str,
                "Transit Time (min)": transit_min,
                "Receive to Quarantine (min)": recv_to_quar_min,
                "Receive to Shelf (min)": recv_to_shelf_min,
                "Quarantine to Shelf (min)": quar_to_shelf_min,
            }

            rows_out.append(row_out)

        # ── Filter options ──────────────────────────────────────────────────
        filter_options = {}

        filter_options["years"] = [r.yr for r in frappe.db.sql(
            "SELECT DISTINCT YEAR(posting_date) AS yr"
            " FROM `tabStock Entry`"
            " WHERE docstatus = 1 AND stock_entry_type IN ('Receiving', 'Late Receipt')"
            " ORDER BY yr DESC",
            as_dict=1
        )]

        filter_options["farms"] = [r.custom_farm for r in frappe.db.sql(
            "SELECT DISTINCT custom_farm"
            " FROM `tabStock Entry`"
            " WHERE docstatus = 1 AND stock_entry_type IN ('Receiving', 'Late Receipt')"
            " AND custom_farm IS NOT NULL AND custom_farm != ''"
            " ORDER BY custom_farm",
            as_dict=1
        )]

        filter_options["varieties"] = [r.item_code for r in frappe.db.sql(
            "SELECT DISTINCT sed.item_code"
            " FROM `tabStock Entry Detail` sed"
            " INNER JOIN `tabStock Entry` se ON sed.parent = se.name"
            " WHERE se.docstatus = 1 AND se.stock_entry_type IN ('Receiving', 'Late Receipt')"
            " AND sed.item_code IS NOT NULL AND sed.item_code != ''"
            " ORDER BY sed.item_code",
            as_dict=1
        )]

        filter_options["greenhouses"] = [r.custom_greenhouse for r in frappe.db.sql(
            "SELECT DISTINCT custom_greenhouse"
            " FROM `tabStock Entry`"
            " WHERE docstatus = 1 AND stock_entry_type IN ('Receiving', 'Late Receipt')"
            " AND custom_greenhouse IS NOT NULL AND custom_greenhouse != ''"
            " ORDER BY custom_greenhouse",
            as_dict=1
        )]

        filter_options["weeks"] = [r.wk for r in frappe.db.sql(
            "SELECT DISTINCT WEEK(posting_date, 1) AS wk"
            " FROM `tabStock Entry`"
            " WHERE docstatus = 1 AND stock_entry_type IN ('Receiving', 'Late Receipt')"
            " ORDER BY wk",
            as_dict=1
        )]

        # ── Return ──────────────────────────────────────────────────────────
        frappe.response["message"] = {
            "success": True,
            "data": rows_out,
            "record_count": len(rows_out),
            "filter_options": filter_options
        }

except Exception as e:
    frappe.log_error(str(e), "fetchTransitTimePivotData Error")
    frappe.response["message"] = {
        "success": False,
        "error": str(e)
    }
