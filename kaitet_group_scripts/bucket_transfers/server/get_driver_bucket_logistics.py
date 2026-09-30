# Name: Get Driver Bucket Logistics
# Type: Server Script · API
# API method: getDriverBucketLogistics
# Enabled: yes
# Modified: 2026-07-22 23:51:00
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Driver Bucket Logistics — per (source farm, order) transfer summary for a
# delivery date. Powers the app's driver screen: farms list + per-order status
# and "% loaded in trolley". Source farm = first word of the source warehouse.
fd = frappe.form_dict
delivery_date = fd.get('delivery_date') or frappe.utils.today()
FARM_EXPR = "SUBSTRING_INDEX(COALESCE(NULLIF(pli.custom_source_warehouse,''), pli.warehouse), ' ', 1)"
TRANSFER = "(pli.custom_awaiting_transfer = 1 OR pli.custom_shelved = 1)"

rows = frappe.db.sql("""
    SELECT
        """ + FARM_EXPR + """  AS farm,
        opl.name               AS opl,
        opl.custom_order_name  AS order_name,
        so.customer            AS customer,
        so.delivery_date       AS delivery_date,
        COUNT(*)               AS total,
        SUM(pli.custom_loaded_in_trolley = 1 OR pli.custom_in_transit = 1 OR pli.custom_shelved = 1) AS loaded,
        SUM(pli.custom_loaded_in_trolley = 1) AS trolley,
        SUM(pli.custom_awaiting_transfer = 1) AS awaiting,
        SUM(pli.custom_in_transit = 1)        AS transit,
        SUM(pli.custom_shelved = 1)           AS shelved,
        SUM(pli.custom_ready_for_packing = 1) AS ready,
        SUM(pli.custom_issued = 1)            AS issued
    FROM `tabPick List Item` pli
    JOIN `tabOrder Pick List` opl ON opl.name = pli.parent
    LEFT JOIN `tabSales Order` so ON so.name = opl.sales_order
    WHERE opl.docstatus < 2 AND pli.parenttype = 'Order Pick List'
      AND so.delivery_date = %(d)s AND """ + TRANSFER + """
    GROUP BY farm, opl.name
    ORDER BY farm, opl.custom_order_name
""", {'d': delivery_date}, as_dict=True)

for r in rows:
    for k in ['total', 'loaded', 'trolley', 'awaiting', 'transit', 'shelved', 'ready', 'issued']:
        r[k] = int(r.get(k) or 0)

frappe.response['message'] = {'delivery_date': str(delivery_date), 'rows': rows}
