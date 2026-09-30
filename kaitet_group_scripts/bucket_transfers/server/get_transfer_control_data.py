# Name: Get Transfer Control Data
# Type: Server Script · API
# API method: getTransferControlData
# Enabled: yes
# Modified: 2026-08-02 09:20:55
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# v16 port exists: upande_packhouse/api/transfer_control.py
# ----------------------------------------------------------------------
# Transfer Control — live feed for the sales team's bucket-transfer planning
# cockpit (Web Page `transfer-control`). Kapkolia is the packhouse; buckets for
# an order are grown/graded at remote farms (Simotwo, Torongo, Chepsito, …) and
# must be transferred in whole-order runs so orders don't sit half-done.
#
# Grounded in the REAL Order Pick List model (verified against live data):
#   - source farm  = first word of COALESCE(custom_source_warehouse, warehouse)
#                    e.g. "Torongo Receiving Cold Store - KR" -> "Torongo".
#   - source shelf = pli.custom_shelf — this is the bucket's CURRENT shelf, i.e.
#                    the remote farm shelf (TRG-…/SMT-…/CHP-…) while awaiting
#                    transfer; it becomes the Kapkolia shelf after it is shelved.
#   - destination  = opl.custom_farm ('Kapkolia').
#   - state        = the four transfer flags (awaiting_transfer -> loaded_in_
#                    trolley -> in_transit -> shelved). 'home' = at the packhouse.
#   - variety      = pli.item_code ; stems = pli.stock_qty (qty is bunches).
#   - outbound     = o.custom_truck_details ; schedule = custom_schedule_number ;
#                    mixed box = custom_is_mixed_box_pick_list.
#
# Returns raw per-pick-list + per-bucket data; the page derives the plan/impact
# client-side (manual priority, whole-order rules). Default window: SO
# delivery_date in [today, today+2].
PACK = 'Kapkolia'
fd = frappe.form_dict
from_date = fd.get('from_date') or frappe.utils.today()
to_date = fd.get('to_date') or frappe.utils.add_days(frappe.utils.today(), 2)

# Pick lists in the window that still have at least one bucket to move
# (awaiting / loaded / in transit — i.e. not yet shelved at the packhouse).
head = frappe.db.sql("""
    SELECT DISTINCT o.name
    FROM `tabOrder Pick List` o
    JOIN `tabSales Order` so ON so.name = o.sales_order
    JOIN `tabPick List Item` pli ON pli.parent = o.name AND pli.parenttype = 'Order Pick List'
    WHERE o.docstatus < 2
      AND so.delivery_date BETWEEN %(f)s AND %(t)s
      AND (pli.custom_awaiting_transfer = 1 OR pli.custom_loaded_in_trolley = 1 OR pli.custom_in_transit = 1)
""", {'f': from_date, 't': to_date}, as_dict=True)
names = [h['name'] for h in head]

orders = []
farms_set = {}
if names:
    rows = frappe.db.sql("""
        SELECT
            pli.parent               AS opl,
            o.custom_order_name      AS order_name,
            o.sales_order            AS sales_order,
            so.customer              AS customer,
            so.delivery_date         AS delivery_date,
            o.custom_truck_details   AS truck,
            o.custom_schedule_number AS schedule,
            o.custom_team            AS team,
            o.custom_item_group      AS item_group,
            o.custom_is_mixed_box_pick_list AS mixed,
            o.custom_mix_group       AS mix_group,
            o.creation               AS created,
            pli.idx                  AS box,
            pli.custom_bucket        AS bucket,
            pli.item_code            AS variety,
            pli.stock_qty            AS stems,
            pli.custom_shelf         AS shelf,
            pli.custom_transit_truck AS transit_truck,
            SUBSTRING_INDEX(COALESCE(NULLIF(pli.custom_source_warehouse, ''), pli.warehouse), ' ', 1) AS src_farm,
            pli.custom_awaiting_transfer   AS awaiting,
            pli.custom_loaded_in_trolley   AS loaded,
            pli.custom_in_transit          AS transit,
            pli.custom_shelved             AS shelved
        FROM `tabPick List Item` pli
        JOIN `tabOrder Pick List` o ON o.name = pli.parent
        LEFT JOIN `tabSales Order` so ON so.name = o.sales_order
        WHERE pli.parenttype = 'Order Pick List' AND pli.parent IN %(names)s
        ORDER BY pli.parent, pli.idx
    """, {'names': tuple(names)}, as_dict=True)

    def state_of(r):
        # A bucket already at the packhouse (shelved, or sourced at Kapkolia, or
        # carrying no transfer flag) is 'home'. Otherwise the most-advanced flag
        # wins: in transit > loaded > awaiting.
        if int(r.get('shelved') or 0):
            return 'home'
        if (r.get('src_farm') or '') == PACK:
            return 'home'
        if int(r.get('transit') or 0):
            return 'transit'
        if int(r.get('loaded') or 0):
            return 'loaded'
        if int(r.get('awaiting') or 0):
            return 'farm'
        return 'home'

    by_opl = {}
    order_of = []
    for r in rows:
        opl = r['opl']
        if opl not in by_opl:
            by_opl[opl] = {
                'ref': opl,
                'opl': opl,
                'order_name': r.get('order_name') or opl,
                'customer': r.get('customer') or '',
                'so': r.get('sales_order') or '',
                'delivery_date': str(r.get('delivery_date') or ''),
                'truck': (r.get('truck') or '').strip() or 'Unassigned',
                'schedule': r.get('schedule'),
                'team': r.get('team') or '',
                'item_group': r.get('item_group') or '',
                'mixed': 1 if int(r.get('mixed') or 0) else 0,
                'mix_group': r.get('mix_group') or '',
                'created': str(r.get('created') or ''),
                'buckets': [],
            }
            order_of.append(opl)
        st = state_of(r)
        farm = r.get('src_farm') or PACK
        by_opl[opl]['buckets'].append({
            'id': r.get('bucket') or '',
            'box': int(r.get('box') or 0),
            'variety': r.get('variety') or '',
            'stems': int(r.get('stems') or 0),
            'farm': farm,
            'shelf': r.get('shelf') or '',
            'state': st,
            'transit_truck': (r.get('transit_truck') or '').strip(),
        })
        if st in ('farm', 'loaded', 'transit') and farm != PACK:
            farms_set[farm] = 1

    for opl in order_of:
        o = by_opl[opl]
        o['total'] = sum(b['stems'] for b in o['buckets'])
        orders.append(o)

frappe.response['orders'] = orders
frappe.response['farms'] = sorted(farms_set.keys())
frappe.response['packhouse'] = PACK
frappe.response['window'] = {'from': str(from_date), 'to': str(to_date)}
frappe.response['generated_at'] = str(frappe.utils.now())
