# Name: getInTransitBuckets
# Type: Server Script · API
# API method: getInTransitBuckets
# Enabled: yes
# Modified: 2026-07-23 01:24:30
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Frappe Server Script (Type: API), api_method = getInTransitBuckets
# Bucket Transfers board for the packhouse coldroom/shelving person. Shows every
# bucket that has been transferred (has a transit truck) and is currently either
# IN TRANSIT (incoming) or SHELVED (arrived), grouped by order, filtered by the
# order's Sales Order delivery_date. Each bucket carries its shelved flag so the
# UI can render a green "Shelved" / grey "Not shelved" pill; each group carries
# shelved/total counts so the client can bucket it into the tabs:
#   none shelved -> incoming/not-shelved ; some -> shelving in progress ; all -> ready to issue.
# Payload: { "from_date": "YYYY-MM-DD", "to_date": "YYYY-MM-DD" } (default: tomorrow).
# Response: { status, from_date, to_date, groups: [{ opl_name, order_name, customer,
#             farm, truck, delivery_date, total, shelved_count,
#             buckets: [{bucket_id, variety, stems, stem_length, shelf, shelved}] }], count }
frappe.response["message"] = {"status": "error", "groups": []}
try:
    data = frappe.request.get_json() or {}
    from_date = data.get("from_date")
    to_date = data.get("to_date")
    # Default window = tomorrow (the coldroom preps for the next day's dispatch).
    if not from_date:
        from_date = str(frappe.utils.add_days(frappe.utils.today(), 1))
    if not to_date:
        to_date = from_date

    rows = frappe.db.sql(
        """
        SELECT pli.parent AS opl_name, pli.custom_bucket AS bucket_id,
               pli.item_name AS variety, pli.item_code AS item_code,
               pli.custom_stem_length AS stem_length, pli.stock_qty AS stems,
               pli.custom_transit_truck AS truck, pli.custom_shelf AS shelf,
               pli.custom_shelved AS shelved,
               opl.custom_order_name AS order_name, opl.customer AS customer,
               opl.custom_farm AS farm, opl.creation AS created,
               so.delivery_date AS delivery_date
        FROM `tabPick List Item` pli
        INNER JOIN `tabOrder Pick List` opl
            ON pli.parent = opl.name AND pli.parenttype = 'Order Pick List'
        LEFT JOIN `tabSales Order` so ON so.name = opl.sales_order
        WHERE pli.custom_transit_truck IS NOT NULL AND pli.custom_transit_truck != ''
          AND (pli.custom_in_transit = 1 OR pli.custom_shelved = 1)
          AND so.delivery_date BETWEEN %(f)s AND %(t)s
        ORDER BY so.delivery_date ASC, opl.creation DESC, pli.custom_bucket ASC
        """,
        {"f": from_date, "t": to_date}, as_dict=True,
    )

    order_map = {}
    order_list = []
    i = 0
    while i < len(rows):
        r = rows[i]
        opl = r.get("opl_name")
        if opl not in order_map:
            grp = {
                "opl_name": opl,
                "order_name": r.get("order_name") or opl,
                "customer": r.get("customer"),
                "farm": r.get("farm"),
                "truck": r.get("truck") or "",
                "delivery_date": str(r.get("delivery_date")) if r.get("delivery_date") else "",
                "buckets": [],
                "shelved_count": 0,
            }
            order_map[opl] = grp
            order_list = order_list + [grp]
        grp = order_map[opl]
        if not grp["truck"] and r.get("truck"):
            grp["truck"] = r.get("truck")
        if r.get("bucket_id"):
            is_shelved = 1 if r.get("shelved") else 0
            grp["buckets"] = grp["buckets"] + [{
                "bucket_id": r.get("bucket_id"),
                "variety": r.get("variety") or r.get("item_code"),
                "stems": r.get("stems"),
                "stem_length": r.get("stem_length"),
                "shelf": r.get("shelf"),
                "shelved": is_shelved,
            }]
            grp["shelved_count"] = grp["shelved_count"] + is_shelved
        i = i + 1

    groups = []
    j = 0
    while j < len(order_list):
        g = order_list[j]
        g["total"] = len(g["buckets"])
        groups = groups + [g]
        j = j + 1

    frappe.response["message"] = {
        "status": "success",
        "from_date": from_date,
        "to_date": to_date,
        "groups": groups,
        "count": len(groups),
    }
except Exception as e:
    frappe.response["message"] = {"status": "error", "message": str(e), "groups": []}
