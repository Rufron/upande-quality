# Name: KPI Strip API
# Type: Server Script · API
# API method: kpi_strip
# Enabled: yes
# Modified: 2026-05-12 17:21:48
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
try:
    # ------------------------------------------------------------
    # KPI strip — lean endpoint for the top-of-page summary
    # Returns just the 6 headline numbers, no chart data, no per-block
    # breakdowns. Designed to be fast and small.
    # ------------------------------------------------------------

    data = {}
    try:
        data = frappe.request.get_json(silent=True) or {}
    except Exception:
        data = {}
    if not data:
        data = dict(frappe.form_dict or {})

    preset    = str(data.get('preset')    or '').strip()
    date_str  = str(data.get('date')      or '').strip()
    from_date = str(data.get('from_date') or '').strip()
    to_date   = str(data.get('to_date')   or '').strip()
    block     = str(data.get('block')     or '').strip()

    def date_clause(col):
        if from_date and to_date:
            return {'clause': 'DATE(' + col + ') BETWEEN %s AND %s', 'params': [from_date, to_date]}
        if preset == 'all':
            return {'clause': '1=1', 'params': []}
        if preset == 'week':
            return {'clause': col + ' >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)', 'params': []}
        if preset == 'month':
            return {'clause': col + ' >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)', 'params': []}
        if preset == 'yesterday':
            return {'clause': 'DATE(' + col + ') = DATE_SUB(CURDATE(), INTERVAL 1 DAY)', 'params': []}
        d = date_str or str(frappe.utils.nowdate())
        return {'clause': 'DATE(' + col + ') = %s', 'params': [d]}

    dc = date_clause('ca.allocated_at')
    base_clause = dc['clause']
    base_params = list(dc['params'])
    if block:
        base_clause = base_clause + ' AND ca.block_ref = %s'
        base_params.append(block)

    IN_FIELD = "('In Field','Filling','Full','Ready for Collection')"
    ON_ROAD  = "('Released','In Transit')"
    AT_PACK  = "('Received','Returned')"

    def q(sql, params):
        rows = frappe.db.sql(sql, tuple(params))
        return int(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else 0

    harvested    = q('SELECT COUNT(*) FROM `tabCrate Allocation` ca WHERE ' + base_clause, base_params)
    in_field     = q('SELECT COUNT(*) FROM `tabCrate Allocation` ca WHERE ' + base_clause + ' AND ca.status IN ' + IN_FIELD, base_params)
    on_road      = q('SELECT COUNT(*) FROM `tabCrate Allocation` ca WHERE ' + base_clause + ' AND ca.status IN ' + ON_ROAD,  base_params)
    at_packhouse = q('SELECT COUNT(*) FROM `tabCrate Allocation` ca WHERE ' + base_clause + ' AND ca.status IN ' + AT_PACK,   base_params)
    departed     = on_road + at_packhouse

    delivery_rate = round(at_packhouse / harvested * 100, 1) if harvested > 0 else None

    # Plan target — same logic as dashboard_today2
    dc_plan = date_clause('pd.`date`')
    plan_rows = frappe.db.sql(
        'SELECT SUM(pd.target_crates) AS t '
        'FROM `tabAvocado Harvest Plan Day` pd '
        'JOIN `tabAvocado Weekly Harvest Plan` p ON p.name = pd.parent '
        "WHERE p.status IN ('Active','Closed') AND " + dc_plan['clause'],
        tuple(dc_plan['params']), as_dict=True
    )
    plan_target = int(plan_rows[0].t) if plan_rows and plan_rows[0].t else None

    frappe.response['message'] = {
        'plan_target':    plan_target,
        'harvested':      harvested,
        'at_packhouse':   at_packhouse,
        'on_road':        on_road,
        'in_field':       in_field,
        'departed':       departed,
        'delivery_rate':  delivery_rate,
        'filter': {
            'preset':    preset,
            'date':      date_str,
            'from_date': from_date,
            'to_date':   to_date,
            'block':     block,
        },
        'generated_at': frappe.utils.now(),
    }

except Exception as e:
    frappe.log_error('kpi_strip', str(e))
    frappe.response['message'] = {'error': str(e)}
