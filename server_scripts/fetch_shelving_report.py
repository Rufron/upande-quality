# Server Script: Fetch Shelving Report  (API, api_method: fetch_shelving_report)
# Deploy source of truth is fixtures/server_script.json — this .py is a
# readable mirror for review/history. Edit both together, or re-export after
# changing the Server Script in Desk. Runs under Frappe RestrictedPython
# (no imports; no augmented assignment on dict/list items; frappe.db.sql ok).

filters = frappe.form_dict
farm = filters.get('farm', '')
status_filter = filters.get('status', '')
date_from = filters.get('date_from', '')
date_to = filters.get('date_to', '')

where_clauses = []
values = {}

# 1. All buckets currently on shelves (match Bucket Status: no age cutoff, must have a bucket_id)
where_clauses.append("IFNULL(si.bucket_id,'') <> ''")

# 2. Filter by farm if selected
if farm:
    if farm.startswith('['):
        farms = frappe.parse_json(farm)
    else:
        farms = [farm]
    if farms:
        where_clauses.append('s.farm IN %(farms)s')
        values['farms'] = tuple(farms)

where_sql = ('WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''

# Core query tracking individual bucket quantities accurately
shelf_sql = """
    SELECT
        si.variety AS variety,
        s.farm AS farm,
        DATE(si.date_added) AS shelf_date,
        s.name AS shelf,
        si.warehouse AS warehouse,
        si.greenhouse AS origin_greenhouse,
        si.stem_length AS stem_length,
        si.stem_qty AS qty,
        si.bucket_id AS bucket_id,
        si.harvest_date AS harvest_date
    FROM `tabShelf Item` si
    JOIN `tabShelf` s ON si.parent = s.name
    """ + where_sql + """
    ORDER BY si.date_added DESC
"""

# Harvest date now lives on the Shelf Item itself (Stock Entry.custom_harvest_batch_no
# was dropped in the cleanup), so no separate Stock Entry lookup is needed.
shelf_rows = frappe.db.sql(shelf_sql, values, as_dict=1)

today = frappe.utils.today()

# Actual stems harvested today — read from Stock Entry (Harvesting), the same
# source/filter the Production Dashboard uses, so the "Today" KPI tallies with it.
se_where = "se.docstatus = 1 AND se.stock_entry_type = 'Harvesting' AND se.posting_date = %(today)s"
se_values = {'today': today}
if values.get('farms'):
    se_where += " AND se.custom_farm IN %(farms)s"
    se_values['farms'] = values['farms']

harvested_today_rows = frappe.db.sql(
    "SELECT COALESCE(SUM(sed.qty), 0) AS total "
    "FROM `tabStock Entry` se "
    "JOIN `tabStock Entry Detail` sed ON sed.parent = se.name "
    "WHERE " + se_where,
    se_values, as_dict=1)
harvested_today = (harvested_today_rows[0]['total'] if harvested_today_rows else 0) or 0

status_map = {'green': '\U0001F7E2', 'orange': '\U0001F7E0', 'red': '\U0001F534', 'unknown': '⚪'}
target_status = status_map.get(status_filter, '') if status_filter else ''

result = []
for r in shelf_rows:
    bid = r.get('bucket_id') or ''
    harvest_date = r.get('harvest_date')
    ref_date = str(harvest_date) if harvest_date else str(r.get('shelf_date') or '')

    if ref_date:
        days = frappe.utils.date_diff(today, ref_date)
        
        # No age cutoff — show all shelf stock so the total matches Bucket Status.
        if days <= 3:
            status = '\U0001F7E2'  # Fresh (Green)
        elif days == 4:
            status = '\U0001F7E0'  # Ageing (Orange)
        else:
            status = '\U0001F534'  # 5+ days old (Red)
    else:
        status = '⚪'

    if target_status and status != target_status:
        continue
    if date_from and ref_date and ref_date < date_from:
        continue
    if date_to and ref_date and ref_date > date_to:
        continue

    result.append({
        'variety': r.get('variety') or '',
        'farm': r.get('farm') or '',
        'status': status,
        'date_added': ref_date,
        'shelf': r.get('shelf') or '',
        'warehouse': r.get('warehouse') or '',
        'origin_greenhouse': r.get('origin_greenhouse') or '',
        'stem_length': r.get('stem_length') or '',
        'qty': r.get('qty') or 0,
        'bucket_id': bid,
    })

frappe.response['message'] = {'rows': result, 'total': len(result), 'harvested_today': harvested_today}
