# Name: Get Bucket Logistics
# Type: Server Script · API
# API method: getBucketLogistics
# Enabled: yes
# Modified: 2026-09-02 14:28:09
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# v16 port exists: upande_packhouse/api/bucket_logistics.py
# ----------------------------------------------------------------------
# Bucket Logistics — orders with buckets being transferred, for a delivery date.
# A bucket is "being transferred" iff custom_awaiting_transfer=1 OR custom_shelved=1.
# Source farm = first word of the source warehouse (custom_source_warehouse, else
# warehouse) — warehouses are named "<Farm> Receiving Cold Store - KR".
# Default delivery_date = TOMORROW.
fd = frappe.form_dict
delivery_date = fd.get('delivery_date') or frappe.utils.add_days(frappe.utils.today(), 1)

# source-farm expression (reused in SELECT + WHERE)
FARM_EXPR = "SUBSTRING_INDEX(COALESCE(NULLIF(pli.custom_source_warehouse,''), pli.warehouse), ' ', 1)"
TRANSFER = "(pli.custom_awaiting_transfer = 1 OR pli.custom_shelved = 1)"

params = {'d': delivery_date}
conds = ["opl.docstatus < 2", "pli.parenttype = 'Order Pick List'", "so.delivery_date = %(d)s", TRANSFER]
if fd.get('farm'):
    conds.append(FARM_EXPR + " = %(farm)s"); params['farm'] = fd.get('farm')
if fd.get('team'):
    conds.append("opl.custom_team = %(team)s"); params['team'] = fd.get('team')
where = " AND ".join(conds)

rows = frappe.db.sql("""
    SELECT
        opl.name                  AS opl,
        opl.custom_order_name     AS order_name,
        opl.sales_order           AS sales_order,
        so.customer               AS customer,
        so.delivery_date          AS delivery_date,
        opl.creation              AS initiated,
        GROUP_CONCAT(DISTINCT """ + FARM_EXPR + """ ORDER BY 1 SEPARATOR ', ') AS farm,
        MAX(pli.custom_transit_truck) AS truck,
        COUNT(*)                  AS total,
        SUM(pli.custom_awaiting_transfer = 1) AS awaiting,
        SUM(pli.custom_loaded_in_trolley = 1) AS trolley,
        SUM(pli.custom_in_transit = 1)        AS transit,
        SUM(pli.custom_shelved = 1)           AS shelved,
        SUM(pli.custom_ready_for_packing = 1) AS ready,
        SUM(pli.custom_issued = 1)            AS issued
    FROM `tabPick List Item` pli
    JOIN `tabOrder Pick List` opl ON opl.name = pli.parent
    LEFT JOIN `tabSales Order` so ON so.name = opl.sales_order
    WHERE """ + where + """
    GROUP BY opl.name
    ORDER BY opl.custom_order_name
""", params, as_dict=True)

for r in rows:
    for k in ['total', 'awaiting', 'trolley', 'transit', 'shelved', 'ready', 'issued']:
        r[k] = int(r.get(k) or 0)
    # Transfer initiation time = OPL creation datetime (full timestamp).
    r['initiated'] = str(r.get('initiated')) if r.get('initiated') else ''

# Arrival time per OPL = when the FIRST bucket of the order was shelved at the
# sales (destination) farm. Source of truth = the CONTINUOUS `Shelving Log`
# (NOT `Shelf Item`): issuing a bucket to the sales order clears its Shelf Item,
# so a fully-shelved-then-issued order would otherwise look like it never
# arrived. The log keeps every shelving forever, so we take, per bucket, its
# LATEST log entry (max creation) and keep it only if that entry is at the
# OPL's own farm (`o2.custom_farm`) — i.e. the bucket's current/most-recent
# shelving is at the destination. Arrival for the OPL = the earliest such
# `shelved_on`. Buckets are reused across orders, so we also require the
# shelving to have happened at/after the OPL was created (>= o2.creation) —
# otherwise a stale Kapkolia entry from a PREVIOUS cycle would falsely mark an
# order as arrived (and yield a negative transit time).
#
# PERFORMANCE: bucket_id / custom_bucket are both VARCHAR under
# utf8mb4_unicode_ci (case-insensitive) collation, so plain `a = b` is
# ALREADY a case-insensitive comparison — wrapping both sides in UPPER()
# (as this used to do, to defend against inconsistent case) buys nothing
# and, worse, stops MySQL from using the indexes on those columns for the
# join, forcing a full unindexed nested-loop across ~120k x ~100k rows
# (this was the actual cause of multi-minute/timeout loads on this page —
# not an N+1, a single query with an index-defeating join). Removing the
# UPPER() calls is behavior-neutral (same matches, same collation) and
# restores index use on both sides of every join here.
# Computed in a SEPARATE query so it can't inflate the status counts above.
opl_names = [r['opl'] for r in rows]
arrivals = {}
if opl_names:
    ar_rows = frappe.db.sql("""
        SELECT pli.parent AS opl, MIN(COALESCE(sl.shelved_on, sl.creation)) AS arrival
        FROM `tabPick List Item` pli
        JOIN `tabOrder Pick List` o2 ON o2.name = pli.parent
        JOIN `tabShelving Log` sl ON sl.bucket_id = pli.custom_bucket
        JOIN (
            SELECT sl2.bucket_id AS bid, MAX(sl2.creation) AS mx
            FROM `tabShelving Log` sl2
            JOIN `tabPick List Item` p2 ON p2.custom_bucket = sl2.bucket_id
            WHERE p2.parenttype = 'Order Pick List' AND p2.parent IN %(opls)s
            GROUP BY sl2.bucket_id
        ) latest ON latest.bid = sl.bucket_id AND latest.mx = sl.creation
        WHERE pli.parenttype = 'Order Pick List'
          AND sl.farm = o2.custom_farm
          AND COALESCE(sl.shelved_on, sl.creation) >= o2.creation
          AND pli.parent IN %(opls)s
        GROUP BY pli.parent
    """, {'opls': tuple(opl_names)}, as_dict=True)
    for a in ar_rows:
        arrivals[a['opl']] = a['arrival']
for r in rows:
    a = arrivals.get(r['opl'])
    r['arrived'] = str(a) if a else ''

# Distinct source farms for the date (unfiltered by the farm dropdown)
farm_rows = frappe.db.sql("""
    SELECT DISTINCT """ + FARM_EXPR + """ AS f
    FROM `tabPick List Item` pli
    JOIN `tabOrder Pick List` opl ON opl.name = pli.parent
    LEFT JOIN `tabSales Order` so ON so.name = opl.sales_order
    WHERE opl.docstatus < 2 AND pli.parenttype = 'Order Pick List'
      AND so.delivery_date = %(d)s AND """ + TRANSFER + """
    ORDER BY 1
""", {'d': delivery_date}, as_dict=True)
farms = [r['f'] for r in farm_rows if r.get('f')]

frappe.response['delivery_date'] = str(delivery_date)
frappe.response['orders'] = rows
frappe.response['farms'] = farms
frappe.response['total_orders'] = len(rows)
