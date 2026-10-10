# Server Script: Shelving Harvested  (API, api_method: get_shelving_harvested)
# Deploy source of truth is fixtures/server_script.json — this .py is a
# readable mirror for review/history. Edit both together, or re-export after
# changing the Server Script in Desk. Runs under Frappe RestrictedPython
# (no imports; no augmented assignment on dict/list items; frappe.db.sql ok).

farm = frappe.form_dict.get('farm') or ''
date_from = frappe.form_dict.get('date_from') or frappe.utils.today()
date_to = frappe.form_dict.get('date_to') or frappe.utils.today()

# Harvest is attributed to a farm via the greenhouse's Warehouse.custom_farm
# (Stock Entry.custom_farm is unused) — same derivation as the Production Dashboard.
cond = ""
params = {'df': date_from, 'dt': date_to}
farms = [f.strip() for f in farm.split(',') if f.strip()] if farm else []
if farms:
    cond = " AND FIND_IN_SET(wh.custom_farm, %(fcsv)s)"
    params['fcsv'] = ",".join(farms)

rows = frappe.db.sql(
    "SELECT COALESCE(SUM(sed.qty),0) AS total "
    "FROM `tabStock Entry` se "
    "JOIN `tabStock Entry Detail` sed ON sed.parent = se.name "
    "LEFT JOIN `tabWarehouse` wh ON wh.name = se.custom_greenhouse "
    "WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting' "
    "AND se.posting_date BETWEEN %(df)s AND %(dt)s" + cond,
    params, as_dict=1)
harvested = (rows[0]['total'] if rows else 0) or 0
frappe.response['message'] = {'harvested': harvested, 'date_from': date_from, 'date_to': date_to}
