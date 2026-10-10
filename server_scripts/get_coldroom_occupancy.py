# Server Script: Cold Room Occupancy  (API, api_method: get_coldroom_occupancy)
# Deploy source of truth is fixtures/server_script.json — this .py is a
# readable mirror for review/history. Edit both together, or re-export after
# changing the Server Script in Desk. Runs under Frappe RestrictedPython
# (no imports; no augmented assignment on dict/list items; frappe.db.sql ok).

CAP = 2
OLD_DAYS = 5

farm = frappe.form_dict.get('farm') or ''
farms = [f.strip() for f in farm.split(',') if f.strip()] if farm else []
params = {}
farm_cond = ""
if farms:
    farm_cond = " WHERE FIND_IN_SET(s.farm, %(fcsv)s)"
    params['fcsv'] = ",".join(farms)

shelf_rows = frappe.db.sql(
    "SELECT s.name AS shelf, s.shelf_id AS shelf_id, s.farm AS farm, "
    "COUNT(CASE WHEN IFNULL(si.bucket_id,'')<>'' THEN 1 END) AS buckets, "
    "COALESCE(SUM(si.stem_qty),0) AS stems, "
    "GROUP_CONCAT(DISTINCT NULLIF(si.bucket_id,'') ORDER BY si.bucket_id SEPARATOR ', ') AS bucket_ids, "
    "MIN(COALESCE(si.harvest_date, DATE(si.date_added))) AS oldest, "
    "DATEDIFF(CURDATE(), MIN(COALESCE(si.harvest_date, DATE(si.date_added)))) AS oldest_days "
    "FROM `tabShelf` s LEFT JOIN `tabShelf Item` si ON si.parent = s.name"
    + farm_cond +
    " GROUP BY s.name, s.shelf_id, s.farm", params, as_dict=1)

alloc_rows = frappe.db.sql(
    "SELECT shelf_location AS shelf, "
    "SUM(CASE WHEN total_quantity>0 AND available_quantity<=0 THEN 1 ELSE 0 END) AS fully_allocated, "
    "COALESCE(SUM(allocated_quantity),0) AS allocated_qty "
    "FROM `tabBucket Allocation Status` WHERE IFNULL(shelf_location,'')<>'' "
    "GROUP BY shelf_location", as_dict=1)
alloc_map = {}
for a in alloc_rows:
    alloc_map[a['shelf']] = a

farm_agg = {}
overall = {'shelves':0,'occupied':0,'buckets':0,'free':0,'old':0,'freeing':0}
old_list = []
freeing_list = []
for r in shelf_rows:
    fm = r['farm'] or 'Unassigned'
    if fm not in farm_agg:
        farm_agg[fm] = {'farm':fm,'shelves':0,'occupied':0,'buckets':0,'free':0,'old':0,'freeing':0}
    fa = farm_agg[fm]
    b = int(r['buckets'] or 0)
    fa['shelves'] = fa['shelves'] + 1
    overall['shelves'] = overall['shelves'] + 1
    fa['buckets'] = fa['buckets'] + b
    overall['buckets'] = overall['buckets'] + b
    if b > 0:
        fa['occupied'] = fa['occupied'] + 1
        overall['occupied'] = overall['occupied'] + 1
    else:
        fa['free'] = fa['free'] + 1
        overall['free'] = overall['free'] + 1
    od = int(r['oldest_days'] or 0)
    if b > 0 and od >= OLD_DAYS:
        fa['old'] = fa['old'] + 1
        overall['old'] = overall['old'] + 1
        old_list.append({'shelf':r['shelf_id'] or r['shelf'],'farm':fm,'buckets':b,'stems':int(r['stems'] or 0),'oldest':str(r['oldest']) if r['oldest'] else '','age':od,'bucket_ids':r['bucket_ids'] or ''})
    a = alloc_map.get(r['shelf'])
    fully = int(a['fully_allocated']) if a else 0
    if b > 0 and fully >= b:
        fa['freeing'] = fa['freeing'] + 1
        overall['freeing'] = overall['freeing'] + 1
        freeing_list.append({'shelf':r['shelf_id'] or r['shelf'],'farm':fm,'buckets':b,'allocated_qty':float(a['allocated_qty']) if a else 0,'bucket_ids':r['bucket_ids'] or ''})

overall['capacity'] = overall['shelves'] * CAP
overall['occupancy_pct'] = round(overall['buckets']*100.0/overall['capacity'],1) if overall['capacity'] else 0
by_farm = []
for fa in farm_agg.values():
    fa['capacity'] = fa['shelves'] * CAP
    fa['occupancy_pct'] = round(fa['buckets']*100.0/fa['capacity'],1) if fa['capacity'] else 0
    by_farm.append(fa)
by_farm.sort(key=lambda x: -x['occupancy_pct'])
old_list.sort(key=lambda x: -x['age'])
freeing_list.sort(key=lambda x: -x['buckets'])

frappe.response['message'] = {
    'capacity_per_shelf': CAP, 'old_days': OLD_DAYS,
    'overall': overall, 'by_farm': by_farm,
    'old_stock': old_list[:400], 'freeing_soon': freeing_list[:400],
}
