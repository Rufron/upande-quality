# Name: Block OPL Submit While Awaiting Transfer
# Type: Server Script · DocType Event
# DocType: Order Pick List
# Event: Before Submit
# Enabled: NO (disabled on the site)
# Modified: 2026-08-10 16:49:58
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Before Submit guard for Order Pick List.
# Blocks submission from EVERY route (form, list-view bulk Submit, REST API) until
# every transfer bucket has been received and shelved at the sales farm.
# A bucket still being transferred is flagged either:
#   custom_awaiting_transfer = 1  (remote bucket, not yet picked/moved), or
#   custom_in_transit       = 1  (on the transfer truck, not yet shelved here).
# The awaiting flag is cleared at trolley-loading, so in_transit must also be checked.
# Local sales-shelf buckets carry neither flag and never block.
blocked = False
for row in doc.locations:
    if row.custom_in_transit == 1 or row.custom_awaiting_transfer == 1:
        blocked = True

if blocked:
    frappe.throw("Bucket awaiting transfer, opl draft")
