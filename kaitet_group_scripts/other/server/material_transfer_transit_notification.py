# Name: Material Transfer Transit Notification
# Type: Server Script · DocType Event
# DocType: Stock Entry
# Event: After Submit
# Enabled: yes
# Modified: 2026-07-08 10:48:14
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Trigger: Stock Entry - After Submit
# Notifies Store Keepers on transit receipt: shows items received in THIS entry,
# and (if the transfer is still partial) an additional "Still Outstanding" section.
#
# Merges the logic of:
#   - "Outstanding Entries Notification" (outstanding-items computation)
#   - "Transfer Receipt Notifications" (received-items table + full/partial badge)
# Visual layout follows the "PE Incoming Payment - Karen Roses" notification format
# (navy header + status badge, gradient divider, boxed details section, footer bar).

if doc.outgoing_stock_entry:

    # Fetch the original outward entry
    outward = frappe.get_doc("Stock Entry", doc.outgoing_stock_entry)

    per_transferred = outward.per_transferred or 0
    if per_transferred >= 100:
        receipt_type = "Full Receipt"
        badge_color = "#34D399"
    else:
        receipt_type = "Partial Receipt"
        badge_color = "#FBBF24"

    # --- Build "Items Received" rows (this inward entry) ---
    received_rows = ""
    received_qty_map = {}
    for item in doc.items:
        existing = received_qty_map.get(item.item_code, 0)
        received_qty_map[item.item_code] = existing + item.transfer_qty

        received_row = """
            <tr>
                <td style="padding: 8px; border: 1px solid #d7e0c9;">%s</td>
                <td style="padding: 8px; border: 1px solid #d7e0c9;">%s</td>
                <td style="padding: 8px; border: 1px solid #d7e0c9; text-align:right;">%s %s</td>
                <td style="padding: 8px; border: 1px solid #d7e0c9;">%s</td>
                <td style="padding: 8px; border: 1px solid #d7e0c9;">%s</td>
            </tr>
        """ % (
            item.item_code,
            item.item_name or "",
            item.qty, item.uom,
            item.s_warehouse or "",
            item.t_warehouse or "",
        )
        received_rows = received_rows + received_row

    # --- Build "Outstanding Items" rows (compare outward vs everything received so far) ---
    outstanding_rows = ""
    has_outstanding = False

    if per_transferred < 100:
        for item in outward.items:
            sent_qty = item.transfer_qty or 0
            total_received = item.transferred_qty or 0
            remaining = sent_qty - total_received

            if remaining > 0:
                has_outstanding = True
                just_received = received_qty_map.get(item.item_code, 0)

                outstanding_row = """
                    <tr>
                        <td style="padding: 8px; border: 1px solid #d7e0c9;">%s</td>
                        <td style="padding: 8px; border: 1px solid #d7e0c9;">%s</td>
                        <td style="padding: 8px; border: 1px solid #d7e0c9; text-align:right;">%s %s</td>
                        <td style="padding: 8px; border: 1px solid #d7e0c9; text-align:right;">%s %s</td>
                        <td style="padding: 8px; border: 1px solid #d7e0c9; text-align:right;
                                   color: #c62828; font-weight: 700;">%s %s</td>
                        <td style="padding: 8px; border: 1px solid #d7e0c9;">%s</td>
                    </tr>
                """ % (
                    item.item_code,
                    item.item_name or "",
                    sent_qty, item.stock_uom,
                    just_received, item.stock_uom,
                    remaining, item.stock_uom,
                    item.t_warehouse or "",
                )
                outstanding_rows = outstanding_rows + outstanding_row

    posting_date = frappe.utils.formatdate(doc.posting_date)
    site_url = frappe.utils.get_url()

    # --- Outstanding section (only rendered when something is still outstanding) ---
    outstanding_section = ""
    if has_outstanding:
        outstanding_section = """
            <div style="background:#fdece7;border-left:5px solid #c62828;border-radius:0 8px 8px 0;padding:18px 22px;margin-bottom:22px;">
                <p style="color:#c62828;margin:0 0 13px 0;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;">Still Outstanding</p>
                <table style="width:100%%;border-collapse:collapse;font-size:12px;">
                    <thead>
                        <tr style="background:#f6d9d0;">
                            <th style="padding:8px;border:1px solid #d7e0c9;text-align:left;color:#7a1f1f;">Item Code</th>
                            <th style="padding:8px;border:1px solid #d7e0c9;text-align:left;color:#7a1f1f;">Item Name</th>
                            <th style="padding:8px;border:1px solid #d7e0c9;text-align:right;color:#7a1f1f;">Sent Qty</th>
                            <th style="padding:8px;border:1px solid #d7e0c9;text-align:right;color:#7a1f1f;">Received This Time</th>
                            <th style="padding:8px;border:1px solid #d7e0c9;text-align:right;color:#7a1f1f;">Still Outstanding</th>
                            <th style="padding:8px;border:1px solid #d7e0c9;text-align:left;color:#7a1f1f;">Transit Warehouse</th>
                        </tr>
                    </thead>
                    <tbody style="color:#3a1414;">
                        %s
                    </tbody>
                </table>
            </div>
        """ % (outstanding_rows,)

    message = """
    <div style="font-family:'Arial',sans-serif;max-width:700px;margin:0 auto;background:#eef4e8;padding:24px;">

        <div style="background:#001C36;border-radius:12px 12px 0 0;overflow:hidden;">
            <div style="padding:18px 30px 14px 30px;">
                <table style="width:100%%;border-collapse:collapse;">
                    <tr>
                        <td style="vertical-align:middle;">
                            <span style="color:#F9FAFB;font-size:18px;font-weight:700;">Transit Goods Receipt</span>
                        </td>
                        <td style="text-align:right;vertical-align:middle;">
                            <span style="background:%s;color:#001C36;padding:5px 14px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;">%s</span>
                        </td>
                    </tr>
                </table>
            </div>
            <div style="height:6px;background:linear-gradient(90deg,#001C36 0%%,#92AB42 60%%,#001C36 100%%);"></div>
        </div>

        <div style="background:#ffffff;padding:30px;border-left:1px solid #b0c890;border-right:1px solid #b0c890;">

            <h2 style="margin:0 0 6px 0;color:#001C36;font-size:20px;font-weight:700;">Transit Entry Submitted</h2>
            <p style="margin:0 0 26px 0;color:#3a6020;font-size:13px;">%s — %s%% of the original transfer has now been received.</p>

            <div style="background:#e4edd4;border-left:5px solid #001C36;border-radius:0 8px 8px 0;padding:18px 22px;margin-bottom:22px;">
                <p style="color:#001C36;margin:0 0 13px 0;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;">Transfer Details</p>
                <table style="width:100%%;border-collapse:collapse;">
                    <tr>
                        <td style="padding:7px 0;color:#3a6020;font-size:12px;width:160px;font-weight:600;">Inward Entry</td>
                        <td style="padding:7px 0;color:#001C36;font-size:13px;font-weight:700;">%s</td>
                    </tr>
                    <tr>
                        <td style="padding:7px 0;color:#3a6020;font-size:12px;font-weight:600;">Outward Entry</td>
                        <td style="padding:7px 0;font-size:12px;">%s</td>
                    </tr>
                    <tr>
                        <td style="padding:7px 0;color:#3a6020;font-size:12px;font-weight:600;">Receipt Date</td>
                        <td style="padding:7px 0;font-size:12px;">%s</td>
                    </tr>
                    <tr>
                        <td style="padding:7px 0;color:#3a6020;font-size:12px;font-weight:600;">Company</td>
                        <td style="padding:7px 0;font-size:12px;">%s</td>
                    </tr>
                    <tr>
                        <td style="padding:7px 0;color:#3a6020;font-size:12px;font-weight:600;">Received By</td>
                        <td style="padding:7px 0;font-size:12px;">%s</td>
                    </tr>
                </table>
            </div>

            <h3 style="color:#001C36;margin-bottom:10px;font-size:14px;">Items Received (This Entry)</h3>
            <table style="width:100%%;border-collapse:collapse;font-size:12px;margin-bottom:22px;">
                <thead>
                    <tr style="background:#e4edd4;">
                        <th style="padding:8px;border:1px solid #d7e0c9;text-align:left;color:#3a6020;">Item Code</th>
                        <th style="padding:8px;border:1px solid #d7e0c9;text-align:left;color:#3a6020;">Item Name</th>
                        <th style="padding:8px;border:1px solid #d7e0c9;text-align:right;color:#3a6020;">Qty</th>
                        <th style="padding:8px;border:1px solid #d7e0c9;text-align:left;color:#3a6020;">From</th>
                        <th style="padding:8px;border:1px solid #d7e0c9;text-align:left;color:#3a6020;">To</th>
                    </tr>
                </thead>
                <tbody style="color:#001C36;">
                    %s
                </tbody>
            </table>

            %s

            <div style="background:#e4edd4;border-radius:10px;padding:22px;text-align:center;">
                <p style="margin:0 0 4px 0;color:#001C36;font-size:14px;font-weight:700;">View Full Entry</p>
                <table style="margin:0 auto;border-collapse:collapse;">
                    <tr>
                        <td style="padding:0 8px;">
                            <a href="%s/app/stock-entry/%s" style="display:inline-block;background:#001C36;color:#ffffff;padding:12px 26px;text-decoration:none;border-radius:7px;font-weight:700;font-size:13px;letter-spacing:0.3px;">Open %s</a>
                        </td>
                    </tr>
                </table>
            </div>

        </div>

        <div style="background:#001C36;padding:18px 30px;text-align:center;border-radius:0 0 12px 12px;">
            <div style="height:3px;background:linear-gradient(90deg,#001C36 0%%,#92AB42 60%%,#001C36 100%%);border-radius:2px;margin-bottom:14px;"></div>
            <p style="margin:0 0 4px 0;color:rgba(255,255,255,0.5);font-size:11px;">This is an automated notification &middot; Do not reply to this email</p>
            <p style="margin:0;color:rgba(255,255,255,0.4);font-size:11px;">%s</p>
        </div>

    </div>
    """ % (
        badge_color, receipt_type,
        receipt_type, per_transferred,
        doc.name,
        doc.outgoing_stock_entry,
        posting_date,
        doc.company,
        frappe.session.user,
        received_rows,
        outstanding_section,
        site_url, doc.name, doc.name,
        doc.company,
    )

    # Get all users with Store Keeper role
    store_keepers = frappe.db.sql("""
        SELECT DISTINCT u.email, u.full_name
        FROM `tabUser` u
        INNER JOIN `tabHas Role` hr ON hr.parent = u.name
        WHERE hr.role = 'Store Keeper'
        AND u.enabled = 1
        AND u.email != ''
    """, as_dict=1)

    if not store_keepers:
        frappe.log_error(
            "No enabled Store Keeper users found for transit receipt notification.",
            "Transit Receipt Notification"
        )
    else:
        recipients = [sk.email for sk in store_keepers]

        subject = "[%s] Transit Receipt: %s (from %s) - %s" % (
            receipt_type,
            doc.name,
            doc.outgoing_stock_entry,
            doc.company,
        )

        frappe.sendmail(
            recipients=recipients,
            sender="dashboard@karenroses.com",
            subject=subject,
            message=message,
            now=True,
        )

        frappe.msgprint(
            "Transit receipt notification sent to %s Store Keeper(s)%s." % (
                len(recipients),
                " (items still outstanding)" if has_outstanding else "",
            ),
            indicator="orange" if has_outstanding else "green",
            alert=True,
        )
