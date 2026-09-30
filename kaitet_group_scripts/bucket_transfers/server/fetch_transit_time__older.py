# Name: Fetch Transit Time
# Type: Server Script · API
# API method: fetch_transit_time  (same api_method as fetch_transit_time.py — two live scripts share it)
# Enabled: yes
# Modified: 2026-06-25 15:14:39
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
filters = frappe.form_dict
date_from = filters.get('from_date', '') or frappe.utils.today()
date_to   = filters.get('to_date',   '') or frappe.utils.today()

# All arithmetic done in SQL — no Python imports needed
sql = """
SELECT
    r.custom_farm        AS farm,
    r.custom_greenhouse  AS greenhouse,
    r.custom_harvest_batch_no AS batch_no,
    TIMESTAMPDIFF(MINUTE,
        MIN(TIMESTAMP(h.posting_date, SUBSTRING(h.posting_time, 1, 8))),
        MIN(TIMESTAMP(r.posting_date, SUBSTRING(r.posting_time, 1, 8)))
    ) AS mins
FROM `tabStock Entry` r
INNER JOIN `tabStock Entry` h
    ON  h.custom_harvest_batch_no = r.custom_harvest_batch_no
    AND h.stock_entry_type = 'Harvesting'
WHERE r.stock_entry_type = 'Receiving'
    AND r.posting_date BETWEEN %(from_date)s AND %(to_date)s
    AND r.custom_harvest_batch_no > ''
    AND r.custom_farm        > ''
    AND r.custom_greenhouse  > ''
GROUP BY r.custom_harvest_batch_no, r.custom_farm, r.custom_greenhouse
ORDER BY r.custom_farm, r.custom_greenhouse, MIN(TIMESTAMP(r.posting_date, SUBSTRING(r.posting_time,1,8))) ASC
"""

rows = frappe.db.sql(sql, {'from_date': date_from, 'to_date': date_to}, as_dict=1)

seen   = {}
result = []
for r in rows:
    farm = r.get('farm') or ''
    gh   = r.get('greenhouse') or ''
    if not farm or not gh:
        continue
    mins = r.get('mins')
    if mins is None:
        continue
    mins = int(mins)
    if mins < -120 or mins > 4320:
        continue
    key = farm + '||' + gh
    if key in seen:
        continue
    seen[key] = True
    result.append({
        'farm':            farm,
        'greenhouse':      gh,
        'transit_minutes': mins if mins >= 0 else 0
    })

frappe.response['message'] = {'rows': result, 'total': len(result)}
