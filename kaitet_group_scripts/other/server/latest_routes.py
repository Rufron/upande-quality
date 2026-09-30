# Name: Latest Routes
# Type: Server Script · API
# API method: latestRoutes
# Enabled: yes
# Modified: 2026-07-27 16:17:34
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------

if frappe.request.method == "GET":
    try:
        since = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-12)
        rows = frappe.db.sql("""
            SELECT rh.user, rh.route, rh.modified
            FROM `tabRoute History` rh
            INNER JOIN (
                SELECT user, MAX(modified) AS mx
                FROM `tabRoute History`
                WHERE modified >= %(since)s
                GROUP BY user
            ) t ON t.user = rh.user AND rh.modified = t.mx
            WHERE rh.modified >= %(since)s
        """, {"since": since}, as_dict=True)

        seen = {}
        deduped = []
        for r in rows:
            if r["user"] in seen:
                continue
            seen[r["user"]] = 1
            deduped.append({"user": r["user"], "route": r["route"],
                            "modified": str(r["modified"]) if r.get("modified") else None})
        rows = deduped

        frappe.response.pop("docs", None)
        frappe.response["status"] = "success"
        frappe.response["data"] = {"routes": rows, "count": len(rows)}
        frappe.response.http_status_code = 200
    except Exception as e:
        frappe.log_error(title="Latest Routes API Error", message=str(e))
        frappe.response["status"] = "error"
        frappe.response["message"] = f"Failed: {str(e)}"
        frappe.response.http_status_code = 500
else:
    frappe.response["status"] = "error"
    frappe.response["message"] = "Method not allowed. Use GET."
    frappe.response.http_status_code = 405
