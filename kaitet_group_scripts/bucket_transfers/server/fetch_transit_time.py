# Name: fetch_transit_time
# Type: Server Script · API
# API method: fetch_transit_time
# Enabled: yes
# Modified: 2026-06-26 00:43:57
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
from_date = frappe.form_dict.get("from_date") or frappe.utils.today()
to_date   = frappe.form_dict.get("to_date")   or frappe.utils.today()

rows = frappe.db.sql("""
    SELECT
        sub.greenhouse,
        sub.farm,
        sub.batch_no,
        ROUND(
            TIMESTAMPDIFF(SECOND, sub.first_harvest, sub.first_recv) / 60.0, 1
        ) AS transit_minutes
    FROM (
        SELECT
            h.custom_harvest_batch_no AS batch_no,
            h.custom_greenhouse       AS greenhouse,
            h.custom_farm             AS farm,
            MIN(CONCAT(h.posting_date, ' ', SUBSTRING(h.posting_time, 1, 8))) AS first_harvest,
            (
                SELECT MIN(CONCAT(r.posting_date, ' ', SUBSTRING(r.posting_time, 1, 8)))
                FROM `tabStock Entry` r
                WHERE r.custom_harvest_batch_no = h.custom_harvest_batch_no
                  AND r.stock_entry_type = 'Receiving'
                  AND r.docstatus = 1
            ) AS first_recv
        FROM `tabStock Entry` h
        WHERE h.stock_entry_type = 'Harvesting'
          AND h.docstatus = 1
          AND h.posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND h.custom_greenhouse IS NOT NULL AND h.custom_greenhouse != ''
          AND h.custom_farm IS NOT NULL AND h.custom_farm != ''
          AND h.custom_harvest_batch_no IS NOT NULL AND h.custom_harvest_batch_no != ''
        GROUP BY h.custom_harvest_batch_no, h.custom_greenhouse, h.custom_farm
    ) sub
    WHERE sub.first_recv IS NOT NULL
    HAVING transit_minutes >= 0 AND transit_minutes <= 1440
    ORDER BY sub.greenhouse, transit_minutes
""", {"from_date": from_date, "to_date": to_date}, as_dict=1)

frappe.response["message"] = {"rows": rows}
