# Name: Transfer Receipt Notifications
# Type: Server Script · DocType Event
# DocType: Stock Entry
# Event: After Submit
# Enabled: NO (disabled on the site)
# Modified: 2026-07-02 17:57:03
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Trigger: Stock Entry - After Submit
# Notifies Store Keepers when transit goods are received

if doc.outgoing_stock_entry:

    # Fetch the original outward entry
    outward = frappe.get_doc("Stock Entry", doc.outgoing_stock_entry)

    # Determine receipt type
    per_transferred = outward.per_transferred or 0
    if per_transferred >= 100:
        receipt_type = "Full Receipt"
        receipt_color = "#34D399"
    else:
        receipt_type = "Partial Receipt"
        receipt_color = "#FBBF24"

    # Build items table rows
    items_rows = ""
    for item in doc.items:
        item_row = """
            <tr>
                <td style="padding: 8px; border: 1px solid #374151;">%s</td>
                <td style="padding: 8px; border: 1px solid #374151;">%s</td>
                <td style="padding: 8px; border: 1px solid #374151; text-align:right;">%s %s</td>
                <td style="padding: 8px; border: 1px solid #374151;">%s</td>
                <td style="padding: 8px; border: 1px solid #374151;">%s</td>
            </tr>
        """ % (
            item.item_code,
            item.item_name or "",
            item.qty,
            item.uom,
            item.s_warehouse or "",
            item.t_warehouse or "",
        )
        items_rows += item_row

    posting_date = frappe.utils.formatdate(doc.posting_date)
    site_url = frappe.utils.get_url()

    # Build the email message
    message = """
    <div style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">

        <div style="background: #1F2937; padding: 20px; border-radius: 8px 8px 0 0;">
            <h2 style="color: #F9FAFB; margin: 0;">Transit Goods Receipt Notification</h2>
        </div>

        <div style="background: #111827; padding: 24px; border-radius: 0 0 8px 8px;">

            <div style="background: %s22; border-left: 4px solid %s;
                        padding: 12px 16px; border-radius: 4px; margin-bottom: 20px;">
                <span style="color: %s; font-weight: bold; font-size: 16px;">
                    &#9679; %s
                </span>
            </div>

            <table style="width: 100%%; border-collapse: collapse; margin-bottom: 20px;">
                <tr>
                    <td style="padding: 6px 0; color: #9CA3AF; width: 40%%;">Inward Entry</td>
                    <td style="padding: 6px 0; color: #F9FAFB; font-weight: 500;">%s</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #9CA3AF;">Outward Entry</td>
                    <td style="padding: 6px 0; color: #F9FAFB; font-weight: 500;">%s</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #9CA3AF;">Receipt Date</td>
                    <td style="padding: 6px 0; color: #F9FAFB;">%s</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #9CA3AF;">Company</td>
                    <td style="padding: 6px 0; color: #F9FAFB;">%s</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #9CA3AF;">Received by</td>
                    <td style="padding: 6px 0; color: #F9FAFB;">%s</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #9CA3AF;">Outward Transfer %%</td>
                    <td style="padding: 6px 0; color: %s; font-weight: 500;">%s%% received</td>
                </tr>
            </table>

            <h3 style="color: #D1D5DB; margin-bottom: 10px;">Items Received</h3>
            <table style="width: 100%%; border-collapse: collapse; font-size: 13px;">
                <thead>
                    <tr style="background: #1F2937;">
                        <th style="padding: 8px; border: 1px solid #374151; color: #9CA3AF;
                                   text-align: left;">Item Code</th>
                        <th style="padding: 8px; border: 1px solid #374151; color: #9CA3AF;
                                   text-align: left;">Item Name</th>
                        <th style="padding: 8px; border: 1px solid #374151; color: #9CA3AF;
                                   text-align: right;">Qty</th>
                        <th style="padding: 8px; border: 1px solid #374151; color: #9CA3AF;
                                   text-align: left;">From</th>
                        <th style="padding: 8px; border: 1px solid #374151; color: #9CA3AF;
                                   text-align: left;">To</th>
                    </tr>
                </thead>
                <tbody style="color: #F9FAFB;">
                    %s
                </tbody>
            </table>

            <p style="color: #6B7280; font-size: 12px; margin-top: 24px;">
                This is an automated notification from ERPNext.
                View the full entry at
                <a href="%s/app/stock-entry/%s" style="color: #5E8BFF;">%s</a>.
            </p>

        </div>
    </div>
    """ % (
        receipt_color, receipt_color,
        receipt_color, receipt_type,
        doc.name,
        doc.outgoing_stock_entry,
        posting_date,
        doc.company,
        frappe.session.user,
        receipt_color, per_transferred,
        items_rows,
        site_url, doc.name, doc.name,
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
            subject=subject,
            message=message,
            now=True,
        )

        frappe.msgprint(
            "Transit receipt notification sent to %s Store Keeper(s)." % len(recipients),
            indicator="green",
            alert=True,
        )
