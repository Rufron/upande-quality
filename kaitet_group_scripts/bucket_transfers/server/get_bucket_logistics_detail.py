# Name: Get Bucket Logistics Detail
# Type: Server Script · API
# API method: getBucketLogisticsDetail
# Enabled: yes
# Modified: 2026-07-22 23:14:43
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# v16 port exists: upande_packhouse/api/bucket_logistics.py
# ----------------------------------------------------------------------
# Per-bucket detail for one Order Pick List — raw Pick List Item flags as
# checkboxes. Only buckets being transferred (awaiting_transfer=1 OR shelved=1).
# Source farm = first word of custom_source_warehouse (else warehouse).
fd = frappe.form_dict
opl = fd.get('opl')
if not opl:
    frappe.response['buckets'] = []
else:
    FARM_EXPR = "SUBSTRING_INDEX(COALESCE(NULLIF(pli.custom_source_warehouse,''), pli.warehouse), ' ', 1)"
    params = {'opl': opl}
    extra = ""
    if fd.get('farm'):
        extra = " AND " + FARM_EXPR + " = %(farm)s"; params['farm'] = fd.get('farm')
    frappe.response['buckets'] = frappe.db.sql("""
        SELECT
            pli.custom_bucket            AS bucket,
            pli.item_code                AS variety,
            pli.custom_stem_length       AS length,
            pli.custom_shelf             AS shelf,
            pli.custom_transit_truck     AS truck,
            """ + FARM_EXPR + """        AS farm,
            pli.custom_box_id            AS box_id,
            pli.custom_awaiting_transfer AS awaiting,
            pli.custom_loaded_in_trolley AS trolley,
            pli.custom_in_transit        AS transit,
            pli.custom_shelved           AS shelved,
            pli.custom_ready_for_packing AS ready,
            pli.custom_issued            AS issued
        FROM `tabPick List Item` pli
        JOIN `tabOrder Pick List` o ON o.name = pli.parent
        WHERE pli.parenttype = 'Order Pick List' AND o.name = %(opl)s
          AND (pli.custom_awaiting_transfer = 1 OR pli.custom_shelved = 1)""" + extra + """
        ORDER BY pli.idx
        LIMIT 2000
    """, params, as_dict=True)
