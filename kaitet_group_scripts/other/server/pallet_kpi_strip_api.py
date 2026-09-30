# Name: Pallet KPI Strip API
# Type: Server Script · API
# API method: pallet_kpi_strip
# Enabled: yes
# Modified: 2026-05-13 10:23:31
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
try:
    rows = frappe.db.sql(
        'SELECT status, box_count, net_weight_kg, gross_weight_kg '
        'FROM `tabPallet Record`',
        as_dict=True
    )

    total = len(rows)
    closed = sum(1 for r in rows if r.status == 'Closed')
    opened = sum(1 for r in rows if r.status == 'Open')
    total_cartons = sum(int(r.box_count or 0) for r in rows)
    total_net = sum(float(r.net_weight_kg or 0) for r in rows)
    total_gross = sum(float(r.gross_weight_kg or 0) for r in rows)
    weighed = [r for r in rows if float(r.net_weight_kg or 0) > 0]
    avg_net = round(sum(float(r.net_weight_kg or 0) for r in weighed) / len(weighed), 1) if weighed else 0

    frappe.response['message'] = {
        'total_pallets':  total,
        'closed':         closed,
        'open':           opened,
        'total_cartons':  total_cartons,
        'total_net_kg':   round(total_net, 1),
        'total_gross_kg': round(total_gross, 1),
        'avg_net_kg':     avg_net,
        'weighed_count':  len(weighed),
        'closed_pct':     round(closed / total * 100, 1) if total else 0,
        'open_pct':       round(opened / total * 100, 1) if total else 0,
        'generated_at':   frappe.utils.now(),
    }
except Exception as e:
    frappe.log_error('pallet_kpi_strip', str(e))
    frappe.response['message'] = {'error': str(e)}
