# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Mobile API — server scripts called by the Upande mobile apps, ported
# verbatim from DB Server Scripts (bench + kaitet-group v15 live) into
# version-controlled whitelisted methods. Bodies keep frappe.form_dict /
# frappe.response exactly as the live scripts used them.

import frappe
from frappe import _
import json


@frappe.whitelist()
def clearOplAllocations():
    # Frappe Server Script (Type: API), api_method = clearOplAllocations
    # Cleanup for orders damaged by the mixed-box bug.
    #   - Reverses the Sales Order lines linked to the OPL (clears alloc flags + OPL link)
    #   - Deletes the Bucket Allocation Status allocated to those SO(s) for those varieties
    #   - Force-deletes the OPL itself (bypass submit guard / link checks / permissions / hooks)
    # Works even if the OPL has ALREADY been deleted: it then derives the sales order(s)
    # and varieties from the Sales Order lines that still carry the (now dangling) custom_opl.
    # Payload: { opl: "<Order Pick List name>" }
    # Constraints: no import / def / += — keep it flat.

    frappe.response["message"] = {"success": False, "error": "Script failed"}

    try:
        opl_name = frappe.form_dict.get("opl")
        if not opl_name:
            frappe.response["message"] = {"success": False, "error": "No OPL provided"}
        else:
            opl_exists = frappe.db.exists("Order Pick List", opl_name)
            so_list = []
            varieties = []

            # 1) Reverse + collect the Sales Order lines linked to this OPL.
            reset_items = frappe.get_all("Sales Order Item",
                                         filters={"custom_opl": opl_name},
                                         fields=["name", "parent", "item_code"])
            reset_count = 0
            i = 0
            while i < len(reset_items):
                r = reset_items[i]
                frappe.db.set_value("Sales Order Item", r.name, {
                    "custom_fully_allocated": 0,
                    "custom_stock_available": 0,
                    "custom_opl": ""
                })
                if r.parent and r.parent not in so_list:
                    so_list = so_list + [r.parent]
                if r.item_code and r.item_code not in varieties:
                    varieties = varieties + [r.item_code]
                reset_count = reset_count + 1
                i = i + 1

            # 2) If the OPL still exists, fold in its own SO + pick-list varieties.
            if opl_exists:
                opl = frappe.get_doc("Order Pick List", opl_name)
                if opl.sales_order and opl.sales_order not in so_list:
                    so_list = so_list + [opl.sales_order]
                locs = opl.locations or []
                j = 0
                while j < len(locs):
                    ic = locs[j].item_code
                    if ic and ic not in varieties:
                        varieties = varieties + [ic]
                    j = j + 1

            # 3) Delete Bucket Allocation Status allocated to those SO(s) for those varieties.
            bas_names = []
            s = 0
            while s < len(so_list):
                rows = frappe.get_all("Bucket Allocations",
                                      filters={"parenttype": "Bucket Allocation Status", "sales_order": so_list[s]},
                                      fields=["parent"])
                t = 0
                while t < len(rows):
                    p = rows[t].parent
                    if p and p not in bas_names:
                        bas_names = bas_names + [p]
                    t = t + 1
                s = s + 1

            deleted_bas = 0
            k = 0
            while k < len(bas_names):
                p = bas_names[k]
                if frappe.db.exists("Bucket Allocation Status", p):
                    ic = frappe.db.get_value("Bucket Allocation Status", p, "item_code")
                    if (not varieties) or (ic in varieties):
                        frappe.delete_doc("Bucket Allocation Status", p, force=1,
                                          ignore_permissions=True, ignore_on_trash=True)
                        deleted_bas = deleted_bas + 1
                k = k + 1

            # 4) Force-delete the pick list if it still exists.
            if opl_exists:
                if opl.docstatus == 1:
                    frappe.db.set_value("Order Pick List", opl_name, "docstatus", 2)
                frappe.delete_doc("Order Pick List", opl_name, force=1,
                                  ignore_permissions=True, ignore_on_trash=True)

            frappe.db.commit()
            frappe.response["message"] = {
                "success": True,
                "opl": opl_name,
                "opl_existed": 1 if opl_exists else 0,
                "sales_orders": so_list,
                "varieties": varieties,
                "deleted_bas": deleted_bas,
                "reset_so_items": reset_count
            }

    except Exception as e:
        frappe.db.rollback()
        frappe.response["message"] = {"success": False, "error": str(e)}


@frappe.whitelist()
def correctDetails():
    data = frappe.request.get_json()
    kind = (data.get("kind") or "").strip().lower()
    target_id = (data.get("id") or "").strip()
    bucket_id = (data.get("bucket_id") or "").strip()
    new_variety = (data.get("variety") or "").strip()
    new_stem_length = (data.get("stem_length") or "").strip()

    # Permission gate
    has_role_rows = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "role": "Harvest Details Updater"},
        fields=["name"],
        limit=1,
    )
    is_admin = frappe.session.user == "Administrator"
    user_has_role = bool(has_role_rows) or is_admin

    if not user_has_role:
        frappe.response["http_status_code"] = 403
        frappe.response["data"] = {"error": "Role 'Harvest Details Updater' required."}

    if user_has_role:
        if kind not in ("bunch", "bucket"):
            frappe.response["http_status_code"] = 400
            frappe.response["data"] = {"error": "kind must be 'bunch' or 'bucket'."}

        if kind in ("bunch", "bucket"):
            if not target_id:
                frappe.response["http_status_code"] = 400
                frappe.response["data"] = {"error": "id is required."}

            if target_id and not new_variety and not new_stem_length:
                frappe.response["http_status_code"] = 400
                frappe.response["data"] = {"error": "Provide variety, stem_length, or both."}

            if target_id and (new_variety or new_stem_length):
                try:
                    log = []

                    if kind == "bunch":
                        bunch_id = target_id
                        if not bucket_id:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {"error": "bucket_id required for bunch corrections."}

                        if bucket_id:
                            session_prefix = bucket_id + "-"
                            gradings = frappe.get_all(
                                "Stock Entry",
                                filters={
                                    "custom_bunch_id": bunch_id,
                                    "stock_entry_type": "Grading",
                                    "custom_harvest_batch_no": ["like", session_prefix + "%"],
                                },
                                fields=["name", "custom_harvest_batch_no", "custom_stem_length"],
                                order_by="creation desc",
                                limit=1,
                            )

                            if not gradings:
                                frappe.response["http_status_code"] = 404
                                frappe.response["data"] = {
                                    "error": "No grading record for bunch " + bunch_id +
                                             " in bucket " + bucket_id,
                                }

                            if gradings:
                                grading_name = gradings[0]["name"]
                                batch_no = gradings[0].get("custom_harvest_batch_no") or ""

                                harvest_rows = frappe.get_all(
                                    "Stock Entry",
                                    filters={
                                        "custom_harvest_batch_no": batch_no,
                                        "stock_entry_type": "Harvesting",
                                    },
                                    fields=["name"],
                                    limit=1,
                                )
                                harvest_name = harvest_rows[0]["name"] if harvest_rows else ""

                                if frappe.db.exists("Bunch QR Code", bunch_id):
                                    bunch_doc = frappe.get_doc("Bunch QR Code", bunch_id)
                                    changed = False
                                    if new_variety and bunch_doc.item_code != new_variety:
                                        bunch_doc.item_code = new_variety
                                        changed = True
                                    if new_stem_length and bunch_doc.stem_length != new_stem_length:
                                        bunch_doc.stem_length = new_stem_length
                                        changed = True
                                    if changed:
                                        bunch_doc.save(ignore_permissions=True)
                                        log.append("Bunch QR Code " + bunch_id + " updated")

                                if new_stem_length and gradings[0].get("custom_stem_length") != new_stem_length:
                                    frappe.db.set_value("Stock Entry", grading_name,
                                                        "custom_stem_length", new_stem_length)
                                    log.append("Grading SE " + grading_name + " stem length updated")
                                    if harvest_name:
                                        frappe.db.set_value("Stock Entry", harvest_name,
                                                            "custom_stem_length", new_stem_length)
                                        log.append("Harvest SE " + harvest_name + " stem length updated")

                                frappe.db.commit()
                                frappe.response["data"] = {
                                    "status": "success",
                                    "message": "Corrections applied to bunch " + bunch_id + ".",
                                    "kind": "bunch",
                                    "id": bunch_id,
                                    "bucket_id": bucket_id,
                                    "new_variety": new_variety or None,
                                    "new_stem_length": new_stem_length or None,
                                    "updates": log,
                                }

                    if kind == "bucket":
                        bid = target_id
                        # Resolve current session by direct latest-SE lookup across
                        # both bucket fields (Receiving uses custom_bucket_id).
                        sixty_days_ago = frappe.utils.add_to_date(
                            frappe.utils.now_datetime(), days=-60)

                        def latest_se_for(field_name):
                            rows = frappe.get_all(
                                "Stock Entry",
                                filters={
                                    field_name: bid,
                                    "creation": [">=", str(sixty_days_ago)],
                                },
                                fields=["name", "custom_harvest_batch_no",
                                        "custom_bucket_id",
                                        "posting_date", "posting_time", "creation"],
                                order_by="posting_date desc, posting_time desc, creation desc",
                                limit=1,
                            )
                            return rows[0] if rows else None

                        candidates = []
                        for fld in ("custom_bucket_id",):
                            row = latest_se_for(fld)
                            if row and row.get("custom_harvest_batch_no"):
                                candidates.append(row)

                        def recency_key(r):
                            return (str(r.get("posting_date") or ""),
                                    str(r.get("posting_time") or ""),
                                    str(r.get("creation") or ""))

                        target_session = ""
                        canonical_bucket = bid
                        if candidates:
                            latest = candidates[0]
                            for c in candidates[1:]:
                                if recency_key(c) > recency_key(latest):
                                    latest = c
                            bn = latest.get("custom_harvest_batch_no") or ""
                            canonical_bucket = (latest.get("custom_bucket_id") or
                                                latest.get("custom_bucket_id") or bid)
                            parts = bn.split("-") if bn else []
                            if len(parts) >= 6:
                                dd = parts[5].split(" ")[0]
                                target_session = "-".join(parts[0:5]) + "-" + dd

                        if not target_session:
                            frappe.response["http_status_code"] = 404
                            frappe.response["data"] = {
                                "error": "Couldn't resolve a current session for bucket " + bid + ".",
                            }

                        if target_session:
                            narrow_date = ""
                            sparts = target_session.split("-")
                            if len(sparts) >= 6:
                                narrow_date = sparts[3] + "-" + sparts[4] + "-" + sparts[5]

                            # All Grading SEs in this session — one per bunch (sprays)
                            # or just one (standards).
                            grading_ses = frappe.get_all(
                                "Stock Entry",
                                filters={
                                    "custom_harvest_batch_no": ["like", target_session + "%"],
                                    "stock_entry_type": "Grading",
                                    "posting_date": narrow_date,
                                },
                                fields=["name", "custom_stem_length",
                                        "custom_bunch_id", "custom_harvest_batch_no"],
                            )

                            # Paired Harvest SEs share exact batch_no with their grading.
                            # Match by batch_no rather than bucket_id to keep the pairing
                            # bunch-accurate.
                            batch_nos = []
                            for g in grading_ses:
                                bnv = g.get("custom_harvest_batch_no")
                                if bnv:
                                    batch_nos.append(bnv)
                            harvest_ses = []
                            if batch_nos:
                                harvest_ses = frappe.get_all(
                                    "Stock Entry",
                                    filters={
                                        "custom_harvest_batch_no": ["in", batch_nos],
                                        "stock_entry_type": "Harvesting",
                                        "posting_date": narrow_date,
                                    },
                                    fields=["name", "custom_stem_length",
                                            "custom_harvest_batch_no"],
                                )

                            # Helper: rewrite items[].item_code on a parent SE
                            def rewrite_se_items(parent_name, new_item):
                                rows = frappe.get_all(
                                    "Stock Entry Detail",
                                    filters={"parent": parent_name},
                                    fields=["name", "item_code"],
                                )
                                changed_any = False
                                for r in rows:
                                    if r.get("item_code") != new_item:
                                        frappe.db.set_value("Stock Entry Detail", r["name"], {
                                            "item_code": new_item,
                                            "item_name": new_item,
                                            "description": new_item,
                                        })
                                        changed_any = True
                                return changed_any

                            # Apply corrections to every Grading SE + its Bunch QR
                            for g in grading_ses:
                                g_name = g["name"]
                                if new_stem_length and g.get("custom_stem_length") != new_stem_length:
                                    frappe.db.set_value("Stock Entry", g_name,
                                                        "custom_stem_length", new_stem_length)
                                    log.append("Grading SE " + g_name + " stem length updated")
                                if new_variety and rewrite_se_items(g_name, new_variety):
                                    log.append("Grading SE " + g_name + " items[].item_code → " +
                                               new_variety)

                                bunch_id = g.get("custom_bunch_id") or ""
                                if bunch_id and frappe.db.exists("Bunch QR Code", bunch_id):
                                    bdoc = frappe.get_doc("Bunch QR Code", bunch_id)
                                    touched = False
                                    if new_variety and bdoc.item_code != new_variety:
                                        bdoc.item_code = new_variety
                                        touched = True
                                    if new_stem_length and bdoc.stem_length != new_stem_length:
                                        bdoc.stem_length = new_stem_length
                                        touched = True
                                    if touched:
                                        bdoc.save(ignore_permissions=True)
                                        log.append("Bunch QR Code " + bunch_id + " updated")

                            # Apply to paired Harvest SEs
                            for h in harvest_ses:
                                h_name = h["name"]
                                if new_stem_length and h.get("custom_stem_length") != new_stem_length:
                                    frappe.db.set_value("Stock Entry", h_name,
                                                        "custom_stem_length", new_stem_length)
                                    log.append("Harvest SE " + h_name + " stem length updated")
                                if new_variety and rewrite_se_items(h_name, new_variety):
                                    log.append("Harvest SE " + h_name + " items[].item_code → " +
                                               new_variety)

                            # Receiving / Late Receipt — single SE for the whole bucket
                            upper = ""
                            if narrow_date:
                                upper = frappe.utils.add_days(narrow_date, 14)
                            receive_filters = {
                                "stock_entry_type": ["in", ["Receiving", "Late Receipt"]],
                                "custom_bucket_id": canonical_bucket,
                            }
                            if narrow_date and upper:
                                receive_filters["posting_date"] = ["between", [narrow_date, upper]]
                            receive_ses = frappe.get_all(
                                "Stock Entry",
                                filters=receive_filters,
                                fields=["name", "custom_stem_length"],
                            )
                            for r in receive_ses:
                                if new_stem_length and r.get("custom_stem_length") != new_stem_length:
                                    frappe.db.set_value("Stock Entry", r["name"],
                                                        "custom_stem_length", new_stem_length)
                                    log.append("Receiving SE " + r["name"] + " stem length updated")
                                if new_variety and rewrite_se_items(r["name"], new_variety):
                                    log.append("Receiving SE " + r["name"] + " items[].item_code → " +
                                               new_variety)

                            # Shelf Item update (bucket may be on a shelf)
                            shelf_items = frappe.get_all(
                                "Shelf Item",
                                filters={"bucket_id": canonical_bucket},
                                fields=["name", "variety", "stem_length"],
                                limit=5,
                            )
                            for si in shelf_items:
                                updates = {}
                                if new_variety and si.get("variety") != new_variety:
                                    updates["variety"] = new_variety
                                if new_stem_length and si.get("stem_length") != new_stem_length:
                                    updates["stem_length"] = new_stem_length
                                    updates["custom_stem_length"] = new_stem_length
                                if updates:
                                    frappe.db.set_value("Shelf Item", si["name"], updates)
                                    log.append("Shelf Item " + si["name"] + " updated")

                            frappe.db.commit()
                            frappe.response["data"] = {
                                "status": "success",
                                "message": "Corrections applied to bucket " + canonical_bucket +
                                           " (" + str(len(grading_ses)) + " bunch" +
                                           ("" if len(grading_ses) == 1 else "es") + ").",
                                "kind": "bucket",
                                "id": canonical_bucket,
                                "new_variety": new_variety or None,
                                "new_stem_length": new_stem_length or None,
                                "bunches_affected": len(grading_ses),
                                "updates": log,
                            }

                except Exception as e:
                    frappe.db.rollback()
                    frappe.log_error("correctDetails error: " + str(e))
                    frappe.response["http_status_code"] = 500
                    frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def createDiscardEntry():
    # ---------------------------------------------------------
    # DISCARD BUCKET SERVER SCRIPT
    # Discards buckets that are 5 days old or more.
    #
    # Discard-list path: when called with from_discard_request=1, the bucket is
    # verified to be on an APPROVED Discard Request for the farm, then the age and
    # allocation checks (which approval overrides) are BYPASSED. not_received and
    # already_discarded are kept — the first is structural (the discard Stock Entry
    # is built from the receiving entry), the second prevents duplicate discards.
    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # FETCH LATEST RECEIVING OR LATE RECEIPT
    # ---------------------------------------------------------
    def fetch_latest_receiving(bucket_id, result):
        result["receiving_doc"] = None

        entries = frappe.get_all(
            "Stock Entry",
            filters={
                "stock_entry_type": ["in", ["Receiving", "Late Receipt"]],
                "custom_bucket_id": bucket_id,
                "docstatus": 1
            },
            fields=["name"],
            order_by="creation desc",
            limit=1
        )

        if entries:
            try:
                doc = frappe.get_doc("Stock Entry", entries[0].name)
                result["receiving_doc"] = doc
            except:
                pass


    # ---------------------------------------------------------
    # CALCULATE BUCKET AGE IN DAYS
    # ---------------------------------------------------------
    def calculate_bucket_age(receiving_doc):
        """Calculate age of bucket in days from posting date"""
        if not receiving_doc:
            return 0

        posting_datetime = frappe.utils.get_datetime(f"{receiving_doc.posting_date} {receiving_doc.posting_time}")
        current_datetime = frappe.utils.now_datetime()

        age_days = frappe.utils.date_diff(current_datetime, posting_datetime)
        return age_days


    # ---------------------------------------------------------
    # CHECK IF BUCKET IS ALREADY DISCARDED
    # ---------------------------------------------------------
    def is_bucket_discarded(bucket_id):
        """Check if bucket already has a discard entry"""
        today = frappe.utils.today()
        discard_entries = frappe.get_all(
            "Stock Entry",
            filters={
                "stock_entry_type": "Discard",
                "custom_bucket_id": bucket_id,
                "docstatus": 1,
                "posting_date": today
            },
            fields=["name"],
            limit=1
        )

        return len(discard_entries) > 0


    # ---------------------------------------------------------
    # CHECK IF BUCKET IS ALLOCATED TO AN OPL CREATED TODAY
    # ---------------------------------------------------------
    def is_bucket_in_todays_opl(bucket_id):
        """Check if bucket is allocated to an Order Pick List created today"""
        today = frappe.utils.today()
        opl_items = frappe.get_all(
            "Pick List Item",
            filters={
                "custom_bucket": bucket_id,
                "parenttype": "Order Pick List",
                "docstatus": 1
            },
            fields=["parent"],
            limit=1,
            order_by="creation desc"
        )

        if not opl_items:
            return False

        # Check if the parent OPL was created today
        opl = frappe.get_all(
            "Order Pick List",
            filters={
                "name": opl_items[0].parent,
                "date_created": today
            },
            fields=["name"],
            limit=1
        )

        return len(opl) > 0


    # ---------------------------------------------------------
    # CHECK IF BUCKET IS ON AN APPROVED DISCARD REQUEST
    # ---------------------------------------------------------
    def is_in_approved_discard_request(bucket_id, farm):
        """True if the bucket is a row on an Approved Discard Request (for the farm,
        when farm is supplied). Gates the validation bypass so only list-authorised
        buckets skip the age/allocation checks."""
        # Farm lives on the CHILD row, not the parent request — filter the bucket
        # rows by farm, then confirm the parent request is Approved.
        child_filters = {"bucket_id": bucket_id, "parenttype": "Discard Request"}
        if farm:
            child_filters["farm"] = farm
        rows = frappe.get_all(
            "Discard Request Bucket",
            filters=child_filters,
            fields=["parent"],
            limit=50
        )
        i = 0
        while i < len(rows):
            dr = frappe.get_all(
                "Discard Request",
                filters={"name": rows[i].parent, "workflow_state": "Approved"},
                fields=["name"],
                limit=1
            )
            if dr:
                return True
            i = i + 1
        return False


    # ---------------------------------------------------------
    # CHECK IF BUCKET IS ON A FINAL DISCARD REQUEST (farm-agnostic)
    # ---------------------------------------------------------
    def is_on_final_discard_request(bucket_id):
        """True if the bucket is a row on a FINAL Discard Request — one that has been
        approved and submitted (workflow_state 'Approved' AND docstatus 1). Once a
        request is final the manager has authorised the discard, so the age check
        must NOT block it (a listed bucket is discarded regardless of age). This is
        intentionally farm-agnostic and independent of the from_discard_request flag,
        so the age bypass holds even for an older app build or a re-shelved bucket
        whose row farm no longer matches the station."""
        rows = frappe.get_all(
            "Discard Request Bucket",
            filters={"bucket_id": bucket_id, "parenttype": "Discard Request"},
            fields=["parent"],
            limit=50
        )
        i = 0
        while i < len(rows):
            dr = frappe.get_all(
                "Discard Request",
                filters={"name": rows[i].parent, "workflow_state": "Approved", "docstatus": 1},
                fields=["name"],
                limit=1
            )
            if dr:
                return True
            i = i + 1
        return False


    # ---------------------------------------------------------
    # CHECK IF BUCKET IS ALLOCATED TO AN ORDER
    # ---------------------------------------------------------
    def is_bucket_allocated(bucket_id):
        """True if the bucket currently appears in Bucket Allocation Status (allocated
        to a sales order). Hard backstop: a discard request is a fetch-time snapshot,
        so a listed bucket may have been allocated afterwards — never discard it."""
        allocated = frappe.get_all(
            "Bucket Allocation Status",
            filters={"bucket_id": bucket_id},
            fields=["name"],
            limit=1
        )
        return len(allocated) > 0


    # ---------------------------------------------------------
    # CREATE DISCARD STOCK ENTRY
    # ---------------------------------------------------------
    def create_discard_entry(receiving_doc, result):
        """Create Discard stock entry for discard"""

        try:
            # Get item details from receiving entry
            recv_item = receiving_doc.items[0]

            # Create new Stock Entry for discard
            discard_entry = frappe.new_doc("Stock Entry")
            discard_entry.stock_entry_type = "Discard"
            discard_entry.purpose = "Discard"
            discard_entry.company = receiving_doc.company
            discard_entry.posting_date = frappe.utils.now_datetime().date()
            discard_entry.posting_time = frappe.utils.now_datetime().time()
            discard_entry.set_posting_time = 1

            # Copy custom fields from receiving entry
            discard_entry.farm = receiving_doc.farm
            discard_entry.custom_location = receiving_doc.custom_location
            discard_entry.custom_business_unit = receiving_doc.custom_business_unit
            discard_entry.custom_greenhouse = receiving_doc.custom_greenhouse
            discard_entry.custom_harvester = receiving_doc.custom_harvester
            discard_entry.custom_harvester_payroll_number = receiving_doc.custom_harvester_payroll_number
            discard_entry.custom_harvest_batch_no = receiving_doc.custom_harvest_batch_no
            discard_entry.custom_bucket_id = receiving_doc.custom_bucket_id
            discard_entry.custom_stem_length = receiving_doc.custom_stem_length
            discard_entry.custom_graded_by = receiving_doc.custom_graded_by
            discard_entry.custom_grader_payroll_number = receiving_doc.custom_grader_payroll_number

            # Set warehouses
            discard_entry.from_warehouse = recv_item.t_warehouse  # Taking from receiving warehouse

            # Add item to discard entry
            discard_item = discard_entry.append("items", {})
            discard_item.item_code = recv_item.item_code
            discard_item.item_name = recv_item.item_name
            discard_item.description = recv_item.description
            discard_item.item_group = recv_item.item_group
            discard_item.qty = recv_item.qty
            discard_item.uom = recv_item.uom
            discard_item.stock_uom = recv_item.stock_uom
            discard_item.conversion_factor = recv_item.conversion_factor
            discard_item.s_warehouse = recv_item.t_warehouse  # From receiving warehouse
            discard_item.expense_account = recv_item.expense_account
            discard_item.cost_center = recv_item.cost_center
            discard_item.allow_zero_valuation_rate = 1

            # Copy custom fields from receiving item
            discard_item.custom_grower = recv_item.custom_grower
            discard_item.custom_harvester = recv_item.custom_harvester
            discard_item.custom_bunched_by = recv_item.custom_bunched_by
            discard_item.custom_number_of_stems = recv_item.custom_number_of_stems

            # Insert and submit
            discard_entry.insert(ignore_permissions=True)
            discard_entry.submit()

            result["discard_entry"] = discard_entry.name

        except Exception as e:
            frappe.log_error(f"Error creating discard entry: {str(e)}", "Discard Entry Creation Error")
            raise


    # ---------------------------------------------------------
    # REMOVE BUCKET FROM SHELF
    # ---------------------------------------------------------
    def remove_bucket_from_shelf(bucket_id, result):
        """Remove bucket from all shelves"""
        result["removed_from_shelf"] = []

        try:
            # Find all shelf items with this bucket
            shelf_items = frappe.db.get_all(
                'Shelf Item',
                filters={'bucket_id': bucket_id},
                fields=['name', 'parent']
            )

            removed_shelves = []

            for item in shelf_items:
                frappe.delete_doc('Shelf Item', item.name, force=1)
                removed_shelves.append(item.parent)

                # Touch parent Shelf (keeps UI + modified in sync)
                frappe.db.set_value(
                    'Shelf',
                    item.parent,
                    'modified',
                    frappe.utils.now()
                )

            result["removed_from_shelf"] = list(set(removed_shelves))

        except Exception as e:
            frappe.log_error(f"Error removing bucket from shelf: {str(e)}", "Remove From Shelf Error")
            # Don't raise - shelf removal is not critical for discard


    # ---------------------------------------------------------
    # MARK BUCKET DISCARDED ON EVERY DISCARD REQUEST
    # ---------------------------------------------------------
    def mark_discarded_on_requests(bucket_id):
        """Set discarded=1 on ALL Discard Request Bucket rows for this bucket, across
        every request (a bucket is often listed on several requests — nightly re-lists
        plus manual ones). Keeps the requests' checkbox in sync so a discarded bucket
        never shows as pending. Filter is case-insensitive at the DB layer."""
        try:
            rows = frappe.get_all(
                "Discard Request Bucket",
                filters={"bucket_id": bucket_id, "parenttype": "Discard Request"},
                fields=["name"]
            )
            for row in rows:
                frappe.db.set_value(
                    "Discard Request Bucket", row["name"], "discarded", 1,
                    update_modified=False
                )
        except Exception as e:
            frappe.log_error(f"Error marking discarded on requests: {str(e)}", "Discard Flag Error")
            # Don't raise - flagging is not critical to the discard itself


    # ---------------------------------------------------------
    # MAIN EXECUTION BLOCK
    # ---------------------------------------------------------
    try:
        data = frappe.request.get_json()
        bucket_id = data.get("bucket_id")
        override_age = False  # Allow discarding young buckets if True
        # Discard-list context: bypass the age + allocation checks once the bucket is
        # verified to be on an Approved Discard Request for the farm.
        from_discard_request = data.get("from_discard_request")
        farm = data.get("farm")

        if not bucket_id:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "bucket_id_missing",
                "message": "Bucket ID is required.",
                "payload": {}
            }
        else:
            result = {}
            bypass = False
            done = False

            # When the discard comes via the discard list, authorise the bypass by
            # verifying the bucket is actually on an Approved Discard Request — and
            # hard-block it if it has since been allocated (the list is a fetch-time
            # snapshot, so a bucket can be allocated after the request was built).
            if from_discard_request:
                if is_bucket_allocated(bucket_id):
                    frappe.response["data"] = {
                        "status": "failed",
                        "reason": "bucket_allocated",
                        "message": "This bucket has been allocated to an order and can no longer be discarded.",
                        "payload": {"bucket_id": bucket_id}
                    }
                    done = True
                elif is_in_approved_discard_request(bucket_id, farm):
                    bypass = True
                else:
                    frappe.response["data"] = {
                        "status": "failed",
                        "reason": "not_in_discard_list",
                        "message": "This bucket is not on an approved discard list.",
                        "payload": {"bucket_id": bucket_id}
                    }
                    done = True

            if not done:
                # Fetch receiving entry
                fetch_latest_receiving(bucket_id, result)
                receiving_doc = result.get("receiving_doc")

                if not receiving_doc:
                    frappe.response["data"] = {
                        "status": "failed",
                        "reason": "not_received",
                        "message": "This bucket has no Receiving or Late Receipt entry.",
                        "payload": {"bucket_id": bucket_id}
                    }
                else:
                    # Already-discarded guard is kept even on the bypass path to avoid
                    # creating a duplicate Discard stock entry.
                    if is_bucket_discarded(bucket_id):
                        frappe.response["data"] = {
                            "status": "failed",
                            "reason": "already_discarded",
                            "message": "This bucket has already been discarded.",
                            "payload": {"bucket_id": bucket_id}
                        }
                    # Allocation check — bypassed for approved discard-list buckets.
                    elif (not bypass) and is_bucket_in_todays_opl(bucket_id):
                        frappe.response["data"] = {
                            "status": "failed",
                            "reason": "bucket_allocated",
                            "message": "Cannot discard,this bucket has been allocated to an order.",
                            "payload": {"bucket_id": bucket_id}
                        }
                    else:
                        # Calculate bucket age
                        age_days = calculate_bucket_age(receiving_doc)

                        # Get variety from receiving entry
                        variety = receiving_doc.items[0].item_code
                        qty = receiving_doc.items[0].qty

                        # Age check — bypassed for discard-list buckets AND for any
                        # bucket already on a FINAL (approved + submitted) discard
                        # request, since the manager has authorised that discard.
                        on_final_dr = is_on_final_discard_request(bucket_id)
                        if age_days < 5 and not override_age and not bypass and not on_final_dr:
                            frappe.response["data"] = {
                                "status": "failed",
                                "reason": "bucket_too_young",
                                "message": f"Bucket is only {age_days} days old. Discards are only allowed for buckets 5 days or older.",
                                "payload": {
                                    "bucket_id": bucket_id,
                                    "age_days": age_days,
                                    "variety": variety,
                                    "stems": qty
                                }
                            }
                        else:
                            # Create discard entry
                            create_discard_entry(receiving_doc, result)

                            # Remove bucket from shelf
                            remove_bucket_from_shelf(bucket_id, result)

                            # Flag the bucket discarded on every Discard Request that lists it
                            mark_discarded_on_requests(bucket_id)

                            success_message = f"Bucket {bucket_id} discarded successfully. Age: {age_days} days, Variety: {variety}, Stems: {qty}."
                            if (bypass or on_final_dr) and age_days < 5:
                                success_message += " (Discard-request age override applied)"

                            if result.get("removed_from_shelf"):
                                success_message += f" Removed from shelf(s): {', '.join(result['removed_from_shelf'])}."

                            frappe.response["data"] = {
                                "status": "success",
                                "message": success_message,
                                "payload": {
                                    "bucket_id": bucket_id,
                                    "age_days": age_days,
                                    "variety": variety,
                                    "stems": qty,
                                    "discard_entry": result.get("discard_entry"),
                                    "override_age": override_age,
                                    "from_discard_request": bool(from_discard_request),
                                    "removed_from_shelves": result.get("removed_from_shelf", [])
                                }
                            }

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(f"Unexpected error in discard script", e)
        frappe.response["data"] = {
            "status": "error",
            "reason": "unknown_error",
            "message": f"An unexpected error occurred: {str(e)}"
        }


@frappe.whitelist()
def createReceivingStockEntry():
    try:
        data = frappe.request.get_json()
        # Real-world scans/typed entries can carry stray leading/trailing
        # whitespace (a hand-typed QR payload, a scanner quirk) that never
        # matches Bucket QR Code's exact id -- trim before the exists check
        # below, same as other input fields elsewhere in this file already do.
        bucket_id = (data.get("bucket_id") or "").strip() or None
        custom_receiving_batch_id = data.get("custom_receiving_batch_id")

        def blank_geo():
            frappe.response["farm"] = ""
            frappe.response["greenhouse"] = ""
            frappe.response["stem_length"] = ""
            frappe.response["number_of_stems"] = ""

        if not bucket_id:
            frappe.response["http_status_code"] = 422
            frappe.response["message"] = "Missing required field: bucket_id"
            frappe.response["status"] = "error"
            blank_geo()
            raise Exception("bucket_id is required")

        if not frappe.db.exists("Bucket QR Code", bucket_id):
            frappe.response["http_status_code"] = 404
            frappe.response["message"] = "This bucket " + str(bucket_id) + " does not exist and has never been used in the system"
            frappe.response["status"] = "not_exist"
            blank_geo()
            return

        # ======================================
        # BUCKET STATUS IS THE GUARD (with row lock)
        # ======================================
        # Mirrors the exact pattern createHarvestStockEntry uses on the other
        # side of this same bucket lifecycle: In Use <-> Available, guarded by
        # for_update=True so a second near-simultaneous receive of the SAME
        # bucket (double-tap, retry after a slow response) blocks on this row
        # lock instead of racing past the status check, then correctly sees
        # "Available" once it unblocks and short-circuits to already_received.
        #
        # Previously this checked a per-Stock-Entry custom_receiving_entry
        # link field that was never actually getting set, so every scan
        # re-aggregated the bucket's ENTIRE harvest history instead of just
        # the current cycle -- that's what "received more than the harvest"
        # was: not a duplicate bug alone, but unscoped accumulation across
        # every harvest this bucket has ever had. Scoping to last_stock_entry
        # (set to "In Use" only by the current harvest, per the harvest-side
        # guard that blocks re-harvesting an In Use bucket) fixes both.
        try:
            bucket_qr_doc = frappe.get_doc("Bucket QR Code", bucket_id, for_update=True)
        except Exception as lock_err:
            # Under real concurrent load (two near-simultaneous scans of the
            # same bucket) the loser can hit a row-version conflict acquiring
            # this very lock, before the winner's own commit is even visible
            # yet to this transaction. That's the DB protecting us, not a
            # real failure -- re-fetch fresh (no lock needed; the winner has
            # already committed by the time this driver-level error surfaces)
            # and report it the same way a sequential repeat scan would.
            if "changed since last read" not in str(lock_err):
                raise
            frappe.db.rollback()
            bucket_qr_doc = frappe.get_doc("Bucket QR Code", bucket_id)
            last_se = bucket_qr_doc.last_stock_entry
            total = 0
            last_se_doc = None
            if last_se:
                last_se_doc = frappe.get_doc("Stock Entry", last_se)
                total = sum(
                    float(r["qty"] or 0)
                    for r in frappe.db.get_all("Stock Entry Detail", filters={"parent": last_se}, fields=["qty"])
                )
            frappe.response["http_status_code"] = 200
            frappe.response["message"] = "Bucket already received " + str(bucket_id)
            frappe.response["status"] = "already_received"
            frappe.response["farm"] = last_se_doc.farm if last_se_doc else ""
            frappe.response["greenhouse"] = last_se_doc.custom_greenhouse if last_se_doc else ""
            frappe.response["stem_length"] = last_se_doc.custom_stem_length if last_se_doc else ""
            frappe.response["number_of_stems"] = str(total)
            return

        if bucket_qr_doc.status != "In Use":
            last_se = bucket_qr_doc.last_stock_entry
            if not last_se:
                frappe.response["http_status_code"] = 404
                frappe.response["message"] = "No stock entry found for bucket " + str(bucket_id)
                frappe.response["status"] = "not_harvested"
                blank_geo()
                return
            last_se_doc = frappe.get_doc("Stock Entry", last_se)
            total = sum(
                float(r["qty"] or 0)
                for r in frappe.db.get_all("Stock Entry Detail", filters={"parent": last_se}, fields=["qty"])
            )
            frappe.response["http_status_code"] = 200
            frappe.response["message"] = "Bucket already received " + str(bucket_id)
            frappe.response["status"] = "already_received"
            frappe.response["farm"] = last_se_doc.farm
            frappe.response["greenhouse"] = last_se_doc.custom_greenhouse
            frappe.response["stem_length"] = last_se_doc.custom_stem_length
            frappe.response["number_of_stems"] = str(total)
            return

        # Latest only -- never guess a journey boundary by aggregating
        # historical "unclaimed" entries. That invented aggregation (not
        # something asked for) is what actually caused a real 2026-09-09
        # incident: bucket "e36be6" had unclaimed Harvesting entries at FOUR
        # different farms spanning six weeks (a bucket_id collision seeded
        # by a migration/backfill run), and aggregating "every unclaimed
        # entry" merged all of it into one Receiving entry.
        #
        # Standard roses harvest once per cycle (the "already in use" guard
        # on the harvest side prevents a second one), so there's only ever
        # one live entry to receive. Spray Roses grade straight in the
        # field: several grading scans can legitimately pile up in ONE real
        # session before the bucket is received, and current_journey_start
        # (set on the harvest that started that session) is what bounds
        # that -- but only when it's actually there. When it's not (a
        # bucket whose journey started before this field existed, or any
        # other gap), don't fall back to scanning history at all: take just
        # the single latest Harvesting entry, exactly like the standards
        # case, rather than merging whatever else happens to share this
        # bucket_id text.
        all_harvests = frappe.get_all(
            "Stock Entry",
            filters={"custom_bucket_id": bucket_id, "stock_entry_type": "Harvesting", "docstatus": 1},
            fields=["name", "farm", "custom_greenhouse", "custom_harvester", "custom_stem_length",
                    "custom_cut_stage", "posting_date", "custom_receiving_entry", "creation"],
            order_by="creation desc",
        )
        if not all_harvests:
            frappe.response["http_status_code"] = 404
            frappe.response["message"] = "No stock entry found for bucket " + str(bucket_id)
            frappe.response["status"] = "not_harvested"
            blank_geo()
            return

        latest = all_harvests[0]
        if latest.get("custom_receiving_entry"):
            # The most recent harvest is already claimed -- nothing new to
            # receive (a data-integrity gap if the bucket is still "In Use").
            frappe.response["http_status_code"] = 404
            frappe.response["message"] = "Bucket " + str(bucket_id) + " is marked In Use but has no unreceived harvest entries"
            frappe.response["status"] = "not_harvested"
            blank_geo()
            return

        journey_start = bucket_qr_doc.current_journey_start
        start_creation = frappe.db.get_value("Stock Entry", journey_start, "creation") if journey_start else None
        if start_creation:
            unclaimed = [
                h for h in all_harvests
                if not h.get("custom_receiving_entry") and h.get("creation") >= start_creation
            ]
            unclaimed.reverse()  # oldest first, matching the rest of this function's expectations
        else:
            unclaimed = [latest]

        if not unclaimed:
            # Data-integrity gap (In Use with nothing unclaimed) -- surface it
            # rather than guessing at a harvest to receive.
            frappe.response["http_status_code"] = 404
            frappe.response["message"] = "Bucket " + str(bucket_id) + " is marked In Use but has no unreceived harvest entries"
            frappe.response["status"] = "not_harvested"
            blank_geo()
            return

        # Header-level fields: a bucket doesn't change farm/greenhouse mid-cycle,
        # so the earliest unclaimed harvest is as good a representative as any.
        first = unclaimed[0]
        farm = first["farm"]
        greenhouse = first["custom_greenhouse"]
        harvester_id = first["custom_harvester"]
        stem_length = first["custom_stem_length"] or ""
        cut_stage = first["custom_cut_stage"] or ""
        harvest_date = first["posting_date"]

        # Grading details (grader + grading date) for this bucket. There's no
        # real persisted batch-number field to scope this more tightly by
        # (custom_harvest_batch_no is set in memory by createHarvestStockEntry
        # but was never actually a Custom Field on this site, so it never
        # survives a reload), and with several harvest entries now in play
        # there's no reliable one-to-one match to a specific Grading entry
        # either -- "most recent Grading entry for this bucket" is a known
        # simplification, informational only (header-level, not per row).
        grading_row = frappe.db.get_value(
            "Stock Entry",
            {"stock_entry_type": "Grading", "custom_bucket_id": bucket_id, "docstatus": 1},
            ["custom_graded_by", "custom_grading_date"],
            as_dict=True,
            order_by="creation desc",
        )
        graded_by = grading_row.custom_graded_by if grading_row else None
        grading_date = grading_row.custom_grading_date if grading_row else None

        # Group by (variety, stem length) across every unclaimed harvest --
        # stem length lives on the harvest's OWN parent record (never on its
        # child row; nothing populates it there), so it's carried down from
        # each contributing harvest entry rather than read off its item rows.
        # This is what actually answers "multiple varieties / multiple
        # lengths of the same variety": each distinct (item, length) pair
        # becomes its own row on the Receiving entry, with the length set on
        # the CHILD row (custom_stem_length exists there and was simply never
        # used) rather than collapsed into a single parent-level value.
        variety_length_qty = {}
        variety_length_src = {}
        total_stems = 0
        for h in unclaimed:
            h_length = h.get("custom_stem_length") or ""
            for r in frappe.db.get_all("Stock Entry Detail", filters={"parent": h["name"]},
                                       fields=["item_code", "qty", "t_warehouse"]):
                v = r.get("item_code")
                key = (v, h_length)
                q = float(r.get("qty") or 0)
                variety_length_qty[key] = variety_length_qty.get(key, 0) + q
                if key not in variety_length_src:
                    variety_length_src[key] = r.get("t_warehouse") or h.get("custom_greenhouse") or greenhouse
                total_stems += q

        # A bucket can sit unreceived for days (a Spray Roses bucket that's
        # never been re-scanned since its last grading pass, or a Standard
        # Roses bucket nobody's got round to receiving) -- receiving it is
        # still valid (posting backdates to the harvest date below), but the
        # operator scanning it TODAY may not realize it's stale: they may
        # have the wrong bucket, or expect today's harvest to be in there.
        # Surface that explicitly and require an explicit confirm_receive
        # before actually creating the Receiving entry, rather than silently
        # backdating. "No harvest today" = the most recent unclaimed entry
        # in this journey isn't dated today; a bucket topped up today (even
        # if it also carries older entries from the same still-open journey)
        # does not trigger this.
        latest_harvest_date = max(h["posting_date"] for h in unclaimed)
        if (
            frappe.utils.getdate(latest_harvest_date) != frappe.utils.getdate(frappe.utils.nowdate())
            and not data.get("confirm_receive")
        ):
            varieties_seen = sorted({v for (v, _length) in variety_length_qty})
            variety_display = varieties_seen[0] if len(varieties_seen) == 1 else ", ".join(varieties_seen)
            frappe.response["http_status_code"] = 200
            frappe.response["status"] = "no_harvest_on_date"
            frappe.response["message"] = (
                "Bucket " + str(bucket_id) + " was not harvested today. Last harvest was on "
                + frappe.utils.formatdate(latest_harvest_date) + " -- " + (variety_display or "Unspecified")
                + " from " + str(greenhouse) + " (" + str(farm) + "). It has not been received yet -- "
                "receive it now?"
            )
            frappe.response["farm"] = farm
            frappe.response["greenhouse"] = greenhouse
            frappe.response["stem_length"] = stem_length
            frappe.response["number_of_stems"] = str(total_stems)
            frappe.response["variety"] = variety_display
            frappe.response["harvest_date"] = str(latest_harvest_date)
            return

        farm_doc = frappe.get_doc("Farm", farm)
        company = farm_doc.company
        abbr = frappe.db.get_value("Company", company, "abbr")
        cost_center = frappe.db.get_value("Company", company, "cost_center") or ("Main - " + str(abbr))
        # Per-farm receiving cold store (created once as setup; not auto-made here).
        to_warehouse = str(farm) + " Receiving Cold Store - " + str(abbr)

        posting_date2 = frappe.utils.getdate(harvest_date)
        curr_date = frappe.utils.getdate(frappe.utils.nowdate())
        days_difference = (curr_date - posting_date2).days
        # Old harvests post on the harvest date; same-day post today.
        valid_posting_date = harvest_date if days_difference >= 1 else frappe.utils.nowdate()

        items = []
        for v, length in sorted(variety_length_qty):
            items.append({
                "item_code": v,
                "qty": variety_length_qty[(v, length)],
                "uom": "Stems",
                "stock_uom": "Stems",
                "conversion_factor": 1,
                "t_warehouse": to_warehouse,
                "s_warehouse": variety_length_src[(v, length)],
                "cost_center": cost_center,
                "custom_stem_length": length,
                "allow_zero_valuation_rate": 1,
            })

        try:
            stock_entry = frappe.get_doc({
                "doctype": "Stock Entry",
                "stock_entry_type": "Receiving",
                "custom_bucket_id": bucket_id,
                "company": company,
                "set_posting_time": 1,
                "posting_date": valid_posting_date,
                "to_warehouse": to_warehouse,
                "cost_center": cost_center,
                "farm": farm,
                "custom_greenhouse": greenhouse,
                "custom_harvester": harvester_id,
                "custom_cut_stage": cut_stage,
                "custom_harvest_date": harvest_date,
                "custom_grading_date": grading_date,
                "custom_graded_by": graded_by,
                "business_unit": "Roses",
                "custom_stem_length": stem_length,
                "custom_receiving_batch_id": custom_receiving_batch_id,
                "items": items,
            })
            stock_entry.insert(ignore_permissions=True)
            stock_entry.flags.ignore_validate = True
            stock_entry.submit()

            # Kept for audit/traceability lookups that already read this field --
            # link EVERY harvest entry this receive actually claimed, not just
            # one, so the Connections tab shows the full set (and so none of
            # them get picked up again by a later receive on this bucket).
            for h in unclaimed:
                frappe.db.set_value("Stock Entry", h["name"], "custom_receiving_entry", stock_entry.name)

            # THE fix: release the bucket back to Available so it can be
            # harvested into again, and so a repeat/retry scan of the SAME
            # bucket sees "already received" instead of creating another
            # Receiving entry. Still under the for_update lock taken above.
            bucket_qr_doc.status = "Available"
            bucket_qr_doc.last_stock_entry = stock_entry.name
            bucket_qr_doc.current_journey_start = None
            bucket_qr_doc.save(ignore_permissions=True)
            frappe.db.commit()

            varieties_received = sorted({v for (v, _length) in variety_length_qty})
            variety_display = (
                varieties_received[0] if len(varieties_received) == 1 else ", ".join(varieties_received)
            )
            frappe.response["http_status_code"] = 200
            frappe.response["message"] = "Receiving Stock Entry created successfully"
            frappe.response["stock_entry_name"] = stock_entry.name
            frappe.response["status"] = "received"
            frappe.response["farm"] = farm
            frappe.response["greenhouse"] = greenhouse
            frappe.response["stem_length"] = stem_length
            frappe.response["number_of_stems"] = str(total_stems)
            frappe.response["variety"] = variety_display
        except Exception as inner_e:
            frappe.db.rollback()
            # The for_update lock narrows the window a lot, but under real
            # concurrent load (two near-simultaneous scans of the same
            # bucket) the loser can still reach this point after the winner
            # has already committed -- the DB's own row-version check on the
            # bucket save is what actually catches it here (driver-specific
            # exception class, hence matching on the message rather than
            # importing a particular driver's error type). Report it the same
            # way a sequential repeat scan would read, not as a scary error:
            # nothing was left half-done -- the rollback above undoes this
            # attempt's own Stock Entry too, not just the bucket update.
            if "changed since last read" in str(inner_e) or "TimestampMismatchError" in str(type(inner_e)):
                fresh_bucket = frappe.get_doc("Bucket QR Code", bucket_id)
                last_se = fresh_bucket.last_stock_entry
                total = 0
                already_received_variety = ""
                if last_se:
                    last_se_items = frappe.db.get_all(
                        "Stock Entry Detail", filters={"parent": last_se}, fields=["item_code", "qty"]
                    )
                    total = sum(float(r["qty"] or 0) for r in last_se_items)
                    already_varieties = sorted({r["item_code"] for r in last_se_items if r.get("item_code")})
                    already_received_variety = (
                        already_varieties[0] if len(already_varieties) == 1 else ", ".join(already_varieties)
                    )
                frappe.response["http_status_code"] = 200
                frappe.response["message"] = "Bucket already received " + str(bucket_id)
                frappe.response["status"] = "already_received"
                frappe.response["farm"] = farm
                frappe.response["greenhouse"] = greenhouse
                frappe.response["stem_length"] = stem_length
                frappe.response["number_of_stems"] = str(total)
                frappe.response["variety"] = already_received_variety
            else:
                frappe.response["http_status_code"] = 500
                frappe.response["message"] = "Error creating stock entry: " + str(inner_e)
                frappe.log_error("Receiving Error", str(inner_e))
                frappe.response["status"] = "error"
                frappe.response["farm"] = farm
                frappe.response["greenhouse"] = greenhouse
                frappe.response["stem_length"] = stem_length
                frappe.response["number_of_stems"] = str(total_stems)
    except Exception as e:
        if not frappe.response.get("http_status_code"):
            frappe.response["http_status_code"] = 500
            frappe.response["message"] = "An error occurred while creating the receiving stock entry"
            frappe.response["error"] = str(e)
            frappe.response["status"] = "error"
            frappe.response["farm"] = ""
            frappe.response["greenhouse"] = ""
            frappe.response["stem_length"] = ""
            frappe.response["number_of_stems"] = ""


def _write_shelved_log(shelf_item_row, shelf_id, farm):
    """Write a Shelving Log "Shelved" row for one just-created Shelf Item row.
    Called once per Shelf Item row a bucket produces (a bucket can carry
    several varieties/lengths). shelf_item_row must already have its `name`
    populated (i.e. called after the parent Shelf has been saved)."""
    frappe.get_doc({
        "doctype": "Shelving Log",
        "bucket_id": shelf_item_row.bucket_id,
        "shelf": shelf_id,
        "farm": farm,
        "variety": shelf_item_row.variety,
        "stem_length": shelf_item_row.stem_length,
        "stem_qty": shelf_item_row.stem_qty,
        "greenhouse": shelf_item_row.greenhouse,
        "warehouse": shelf_item_row.warehouse,
        "cut_stage": shelf_item_row.cut_stage,
        "harvest_date": shelf_item_row.harvest_date,
        "receiving_date": shelf_item_row.receiving_date,
        "harvester": shelf_item_row.harvester,
        "graded_by": shelf_item_row.graded_by,
        "grading_date": shelf_item_row.grading_date,
        "reason": "Shelved",
        "shelved_on": frappe.utils.now(),
        "shelved_by": frappe.session.user,
        "shelf_item": shelf_item_row.name,
    }).insert(ignore_permissions=True)


@frappe.whitelist()
def createShelvingEntry():
    # ---------------------------------------------------------
    # VALIDATION (NO RETURNS)
    # ---------------------------------------------------------
    def validation(data, result):
        validation_rule = [
            ('shelf_id_not_null', "Shelf ID is missing."),
            ('bucket_id_not_null', "Bucket ID is missing."),
            ('two_buckets_per_shelf', "The shelf is full."),
            ('duplicate_entry', "The bucket has already been shelved."),
            ('farm_not_null', "Farm is missing.")
        ]

        result["passed"] = True
        result["reason"] = "validation_successful"
        result["message"] = "Validation successful."
        result["shelf_doc"] = None

        # shelf_id_not_null
        if not data.get('shelf_id'):
            result["passed"] = False
            result["reason"] = validation_rule[0][0]
            result["message"] = validation_rule[0][1]
            frappe.log_error("Shelf ID Validation Failed", f"Shelf ID is missing. Data: {data}")

        # bucket_id_not_null
        elif not data.get('bucket_id'):
            result["passed"] = False
            result["reason"] = validation_rule[1][0]
            result["message"] = validation_rule[1][1]
            frappe.log_error("Bucket ID Validation Failed", f"Bucket ID is missing. Data: {data}")

        # farm_not_null
        elif not data.get('farm'):
            result["passed"] = False
            result["reason"] = validation_rule[4][0]
            result["message"] = validation_rule[4][1]
            frappe.log_error("Farm Validation Failed", f"Farm is missing. Data: {data}")

        # If prelim validation passed → load/create shelf
        if result["passed"]:
            shelf_id = data.get('shelf_id')
            try:
                shelf_doc = frappe.get_doc("Shelf", shelf_id)
            except frappe.DoesNotExistError:
                new_shelf_doc = frappe.new_doc("Shelf")
                new_shelf_doc.name = shelf_id
                new_shelf_doc.shelf_id = shelf_id
                new_shelf_doc.insert()
                shelf_doc = new_shelf_doc

            result["shelf_doc"] = shelf_doc
            bucket_id = data.get("bucket_id")

            # duplicate_entry - CHECK CURRENT SHELF
            if shelf_doc and shelf_doc.items:
                for item in shelf_doc.items:
                    if item.bucket_id.lower() == bucket_id.lower():
                        result["passed"] = False
                        result["reason"] = validation_rule[3][0]
                        result["message"] = validation_rule[3][1]
                        frappe.log_error("Duplicate Entry Validation Failed", 
                                       f"Bucket {bucket_id} already exists on shelf {shelf_id}. Data: {data}")

            # duplicate_entry - CHECK ALL OTHER SHELVES
            #
            # A bucket sitting on ANOTHER shelf is normally a true duplicate and is
            # blocked. BUT a transfer bucket whose load-to-truck sync failed (no
            # internet) is still physically on its REMOTE origin shelf even though it
            # has been carried to the sales farm. Blocking it here strands the order.
            # So: if the bucket is a transfer bucket (a Pick List Item on a draft OPL
            # carrying any transfer flag), do NOT block — record the stale remote
            # shelves so the main flow can remove them (self-heal the skipped
            # transfer) and log a Skipped Transfer anomaly. Non-transfer duplicates
            # are still blocked as before.
            result["stale_transfer_shelves"] = []
            if result["passed"]:
                other_shelves = frappe.get_all(
                    "Shelf Item",
                    filters={
                        "bucket_id": bucket_id,
                        "parent": ["!=", shelf_id]  # Exclude current shelf
                    },
                    fields=["name", "parent"],
                )

                if other_shelves:
                    transfer_rows = []
                    if frappe.get_meta("Pick List Item").get_field("bucket"):
                        transfer_rows = frappe.db.sql("""
                        SELECT pli.name
                        FROM `tabPick List Item` pli
                        JOIN `tabOrder Pick List` opl ON opl.name = pli.parent AND opl.docstatus = 0
                        WHERE pli.parenttype = 'Order Pick List'
                          AND pli.bucket = %s
                          AND (pli.awaiting_transfer = 1
                               OR pli.in_transit = 1
                               OR pli.loaded_in_trolley = 1)
                        LIMIT 1
                    """, bucket_id, as_dict=True)

                    if transfer_rows:
                        # Skipped transfer: keep going, clean up the remote shelves.
                        result["stale_transfer_shelves"] = other_shelves
                    else:
                        result["passed"] = False
                        result["reason"] = validation_rule[3][0]
                        result["message"] = f"The bucket has already been shelved on shelf {other_shelves[0].parent}."
                        frappe.log_error("Duplicate Entry Validation Failed",
                                       f"Bucket {bucket_id} already shelved on shelf {other_shelves[0].parent}. Data: {data}")

            # two_buckets_per_shelf (2 max) - one bucket may now span several rows
            # (one Shelf Item per variety), so count DISTINCT buckets, not rows.
            existing_buckets = set()
            if shelf_doc.items:
                for it in shelf_doc.items:
                    existing_buckets.add((it.bucket_id or "").lower())
            existing_buckets.discard(bucket_id.lower())
            if result["passed"] and len(existing_buckets) >= 2:
                result["passed"] = False
                result["reason"] = validation_rule[2][0]
                result["message"] = validation_rule[2][1]
                frappe.log_error("Shelf Capacity Validation Failed", 
                               f"Shelf {shelf_id} is full (2 buckets max). Data: {data}")


    # ---------------------------------------------------------
    # FETCH LATEST RECEIVING OR LATE RECEIPT (NO RETURNS)
    # ---------------------------------------------------------
    def fetch_latest_receiving(bucket_id, result):
        result["receiving_doc"] = None

        entries = frappe.get_all(
            "Stock Entry",
            filters={
                "stock_entry_type": ["in", ["Receiving", "Late Receipt"]],
                "custom_bucket_id": bucket_id,
                "docstatus": 1
            },
            fields=["name"],
            order_by="creation desc",
            limit=1
        )

        if entries:
            try:
                doc = frappe.get_doc("Stock Entry", entries[0].name)
                result["receiving_doc"] = doc
            except:
                pass


    # ---------------------------------------------------------
    # MARK BUCKET SHELVED (NO RETURNS)
    # ---------------------------------------------------------
    def mark_bucket_as_shelved(bucket_id, receiving_doc, result):
        result["shelved_info"] = None

        if receiving_doc:
            try:
                # custom_shelved_at_kapkolia was removed from Stock Entry Detail in the
                # new Stock Entry build; only set it if the column still exists so this
                # never fails on the submitted receiving entry.
                if frappe.db.exists("Custom Field", {"dt": "Stock Entry Detail", "fieldname": "custom_shelved_at_kapkolia"}) and receiving_doc.items:
                    item = receiving_doc.items[0]
                    item.custom_shelved_at_kapkolia = 1
                    receiving_doc.save(ignore_permissions=True)
                    result["shelved_info"] = {
                        "stock_entry": receiving_doc.name,
                        "shelved": True
                    }
            except:
                result["shelved_info"] = None


    # ---------------------------------------------------------
    # UPDATE OPL TRANSIT STATUS (NO RETURNS)
    # ---------------------------------------------------------
    def update_transit_status(bucket_id, shelf_id, result):
        # Once a transfer bucket is shelved it has left the transfer pipeline, so
        # clear EVERY transfer flag (in_transit / awaiting_transfer / loaded_in_trolley)
        # and mark it shelved. Leaving any of these set keeps the bucket "awaiting
        # transfer" and blocks the OPL from ever submitting. Handles all transfer
        # entry paths (in-transit AND offline load-to-trolley), not just in_transit.
        # Local (non-transfer) rows carry no flag, so they're left untouched.
        result["transit_updated"] = False

        if not frappe.get_meta("Pick List Item").get_field("bucket"):
            return
        rows = frappe.get_all(
            "Pick List Item",
            filters={"bucket": bucket_id},
            fields=["name", "parent"],
        )
        updated_opls = []
        for r in rows:
            opl_doc = frappe.get_doc("Order Pick List", r.parent)
            if opl_doc.docstatus != 0:
                continue
            changed = False
            for row in opl_doc.table_ytkc:
                if row.name == r.name:
                    is_transfer = (
                        (row.in_transit or 0) == 1
                        or (row.awaiting_transfer or 0) == 1
                        or (row.loaded_in_trolley or 0) == 1
                    )
                    if is_transfer:
                        row.in_transit = 0
                        row.awaiting_transfer = 0
                        row.loaded_in_trolley = 0
                        row.shelved = 1
                        row.shelf = shelf_id
                        changed = True
                    break
            if changed:
                opl_doc.save(ignore_permissions=True)
                updated_opls.append(r.parent)

        if updated_opls:
            result["transit_updated"] = True
            result["transit_opl"] = updated_opls[0]


    # ─────────────────────────────────────────────────────
    # NEW: UPDATE BUCKET ALLOCATION STATUS TRANSIT FLAGS
    # ─────────────────────────────────────────────────────
    def update_bas_transit_status(bucket_id, variety, farm, shelf_id, result):
        """
        Clear in_transit flag and update farm/shelf location in Bucket Allocation Status.
        Recalculate available_quantity to make balance available for allocation.
        """
        result["bas_updated"] = False
        result["bas_available_qty"] = 0

        try:
            bas_name = frappe.db.get_value(
                "Bucket Allocation Status",
                {"bucket_id": bucket_id, "item_code": variety},
                "name"
            )

            if bas_name:
                bas_doc = frappe.get_doc("Bucket Allocation Status", bas_name)

                # Only update if it was marked in_transit
                if bas_doc.in_transit == 1:
                    bas_doc.in_transit = 0
                    bas_doc.shelf_farm = farm
                    bas_doc.shelf_location = shelf_id

                    # Recalculate available quantity
                    bas_doc.available_quantity = bas_doc.total_quantity - bas_doc.allocated_quantity

                    bas_doc.save(ignore_permissions=True)
                    frappe.db.commit()

                    result["bas_updated"] = True
                    result["bas_available_qty"] = bas_doc.available_quantity

                    frappe.log_error(
                        title="BAS Transit Cleared",
                        message=f"Bucket: {bucket_id}, Variety: {variety}, Farm: {farm}\n"
                                f"Total: {bas_doc.total_quantity}, Allocated: {bas_doc.allocated_quantity}, "
                                f"Available: {bas_doc.available_quantity}"
                    )
        except Exception as e:
            frappe.log_error("BAS Transit Update Failed", str(e))


    # ─────────────────────────────────────────────────────
    # NEW: CHECK IF OPL CAN BE AUTO-SUBMITTED
    # ─────────────────────────────────────────────────────
    def check_and_submit_opl(bucket_id, result):
        """
        Check if OPL(s) referencing this bucket can now be submitted.

        Delegates entirely to upande_packhouse's single central readiness
        check (sales_allocation._try_submit_opl_if_complete /
        opl_submit_blockers) instead of a separate, weaker "are the transfer
        rows shelved" loop that used to live here. That loop started
        `all_ready = True` and only ever flipped it to False for rows already
        flagged as a transfer -- an OPL with no transfer rows at all (or one
        whose transfer rows just got shelved) would submit the instant this
        ran, regardless of whether its required stems were actually fully
        allocated yet, or whether a mixed-box/bunch OPL's sibling colour-lines
        were still unallocated. The central helper checks stem coverage
        (against the GLOBAL confirmed-stems total, not scoped to one farm)
        and mix/bunch-group completeness in addition to transfer status, and
        Order Pick List's own before_submit re-enforces the same rules as a
        doctype-level backstop even if this call site were ever bypassed.
        """
        result["opl_submitted"] = []

        try:
            if not frappe.get_meta("Pick List Item").get_field("bucket"):
                return

            from upande_packhouse.upande_packhouse.page.sales_allocation.sales_allocation import _try_submit_opl_if_complete

            # Find OPL(s) that reference this bucket
            opl_rows = frappe.db.sql("""
                SELECT DISTINCT parent
                FROM `tabPick List Item`
                WHERE bucket = %s
            """, bucket_id, as_dict=True)

            for row in opl_rows:
                opl_name = row.parent
                # Skip (don't re-report) an OPL that was already submitted
                # before this bucket-shelve event -- _try_submit_opl_if_complete
                # would still return True for it (idempotent), but only a
                # freshly-submitted-just-now OPL belongs in this response.
                if frappe.db.get_value("Order Pick List", opl_name, "docstatus") == 1:
                    continue
                if not _try_submit_opl_if_complete(opl_name):
                    continue

                frappe.db.commit()
                result["opl_submitted"].append(opl_name)
                frappe.log_error(
                    title="OPL Auto-Submitted",
                    message=f"OPL {opl_name} auto-submitted after bucket {bucket_id} shelved. "
                            f"All items now ready for packing."
                )

        except Exception as e:
            frappe.log_error("OPL Auto-Submit Check Failed", str(e))


    # ---------------------------------------------------------
    # MAIN EXECUTION BLOCK
    # ---------------------------------------------------------
    try:
        data = frappe.request.get_json()
        result = {}

        validation(data, result)

        # VALIDATION FAILED → respond
        if not result.get("passed"):
            frappe.response["data"] = {
                "status": "failed",
                "reason": result.get("reason"),
                "message": result.get("message"),
                "payload": {
                    "shelf_id": data.get('shelf_id'),
                    "bucket_id": data.get('bucket_id')
                }
            }
        else:
            bucket_id = data.get("bucket_id")
            shelf_id = data.get("shelf_id")
            farm = data.get("farm")

            # FETCH RECEIVING
            fetch_latest_receiving(bucket_id, result)
            receiving_doc = result.get("receiving_doc")

            if not receiving_doc:
                frappe.log_error("Receiving Entry Not Found", 
                               f"No Receiving or Late Receipt entry found for bucket {bucket_id}. Data: {data}")
                frappe.response["data"] = {
                    "status": "failed",
                    "reason": "not_received",
                    "message": "This bucket has no Receiving or Late Receipt entry.",
                    "payload": {"bucket_id": bucket_id}
                }
            else:
                # ─────────────────────────────────────────────────────────────
                # VALIDATIONS: prevent stale / old-cycle receiving data
                # ─────────────────────────────────────────────────────────────
                today_date = frappe.utils.getdate(frappe.utils.today())
                recv_date = frappe.utils.getdate(receiving_doc.posting_date)

                # 1. Harvest-to-Receiving gap must be ≤ 1 day
                harvest_entry = frappe.get_all(
                    "Stock Entry",
                    filters={
                        "stock_entry_type": "Harvesting",
                        "custom_bucket_id": bucket_id,
                        "posting_date": recv_date,
                        "docstatus": 1
                    },
                    fields=["name", "posting_date"],
                    order_by="creation desc",
                    limit=1
                )

                if not harvest_entry:
                    harvest_entry = frappe.get_all(
                        "Stock Entry",
                        filters={
                            "stock_entry_type": "Harvesting",
                            "custom_bucket_id": bucket_id,
                            "posting_date": frappe.utils.add_days(recv_date, -1),
                            "docstatus": 1
                        },
                        fields=["name", "posting_date"],
                        order_by="creation desc",
                        limit=1
                    )

                if not harvest_entry:
                    frappe.log_error("Harvesting Entry Not Found Validation Failed",
                                    f"No harvesting entry found for bucket {bucket_id} "
                                    f"on or before receiving date {recv_date}. Data: {data}")
                    frappe.response["data"] = {
                        "status": "failed",
                        "reason": "no_matching_harvest",
                        "message": f"No harvesting entry found for bucket {bucket_id} "
                                   f"within 1 day of receiving date ({recv_date}).",
                        "payload": {
                            "bucket_id": bucket_id,
                            "received_on": str(recv_date)
                        }
                    }
                else:
                    harvest_date = frappe.utils.getdate(harvest_entry[0].posting_date)
                    harvest_to_recv_days = (recv_date - harvest_date).days

                    if harvest_to_recv_days > 1:
                        frappe.log_error("Harvest-to-Receiving Gap Validation Failed",
                                        f"Bucket {bucket_id} harvested on {harvest_date}, "
                                        f"received on {recv_date} ({harvest_to_recv_days} days gap). Data: {data}")
                        frappe.response["data"] = {
                            "status": "failed",
                            "reason": "harvest_receiving_gap_too_large",
                            "message": f"Bucket harvested on {harvest_date} but received on {recv_date} "
                                       f"({harvest_to_recv_days} days apart). Maximum allowed gap is 1 day.",
                            "payload": {
                                "bucket_id": bucket_id,
                                "harvested_on": str(harvest_date),
                                "received_on": str(recv_date),
                                "gap_days": harvest_to_recv_days
                            }
                        }
                    else:
                        # 2. Staleness check
                        days_since_receiving = (today_date - recv_date).days
                        origin_farm = receiving_doc.farm or farm
                        max_allowed_days = 50 if origin_farm and origin_farm.lower() == "kapkolia" else 40

                        if days_since_receiving > max_allowed_days:
                            frappe.log_error("Stale Receiving Date Validation Failed",
                                            f"Bucket {bucket_id} received on {recv_date} "
                                            f"({days_since_receiving} days ago). "
                                            f"Origin farm: {origin_farm}, max allowed: {max_allowed_days} days. Data: {data}")
                            frappe.response["data"] = {
                                "status": "failed",
                                "reason": "stale_receiving_date",
                                "message": f"Cannot shelf bucket — received on {recv_date} "
                                           f"({days_since_receiving} days ago). "
                                           f"Maximum allowed for {origin_farm} is {max_allowed_days} day(s).",
                                "payload": {
                                    "bucket_id": bucket_id,
                                    "origin_farm": origin_farm,
                                    "received_on": str(recv_date),
                                    "days_since_receiving": days_since_receiving,
                                    "max_allowed_days": max_allowed_days
                                }
                            }
                        else:
                            # 3. Max shelving age (Production Settings) -- distinct
                            # from the staleness check above: that one bounds days
                            # since RECEIVING (fixed 40/50-day cap, meant for normal
                            # operation); this one bounds days since HARVEST via a
                            # configurable cap, introduced 2026-09-10 specifically to
                            # keep pre-fix (contaminated) buckets off the shelf while
                            # the 2026-09-09 journey/receiving fixes bed in. Blank or
                            # 0 means no cap -- distinct reason/message so the two
                            # checks are never confused with each other in the field.
                            max_shelving_age = frappe.db.get_single_value("Production Settings", "max_shelving_age")
                            harvest_age_days = (today_date - harvest_date).days

                            if max_shelving_age and harvest_age_days > max_shelving_age:
                                frappe.log_error("Max Shelving Age Validation Failed",
                                                f"Bucket {bucket_id} harvested on {harvest_date} "
                                                f"({harvest_age_days} days ago). "
                                                f"Max shelving age: {max_shelving_age} day(s). Data: {data}")
                                frappe.response["data"] = {
                                    "status": "failed",
                                    "reason": "harvest_too_old",
                                    "message": f"Cannot shelf bucket — harvested on {harvest_date} "
                                               f"({harvest_age_days} days ago), over the maximum shelving "
                                               f"age of {max_shelving_age} day(s).",
                                    "payload": {
                                        "bucket_id": bucket_id,
                                        "harvested_on": str(harvest_date),
                                        "harvest_age_days": harvest_age_days,
                                        "max_shelving_age": max_shelving_age
                                    }
                                }
                                return

                            # ─────────────────────────────────────────────────────
                            # ALL CHECKS PASSED → proceed with shelving
                            # ─────────────────────────────────────────────────────

                            # ---------------------------------------------------------
                            # CHECK IF BUCKET WAS IN TRANSIT → update OPL
                            # ---------------------------------------------------------
                            update_transit_status(bucket_id, shelf_id, result)

                            # ---------------------------------------------------------
                            # SHELVING LOGIC
                            # ---------------------------------------------------------
                            recv_item = receiving_doc.items[0]
                            qty = recv_item.qty
                            variety = recv_item.item_code
                            origin_greenhouse = recv_item.s_warehouse
                            warehouse = recv_item.t_warehouse

                            # ---------------------------------------------------------
                            # STEM LENGTH FALLBACK LOGIC
                            # ---------------------------------------------------------
                            stem_length = None

                            # A — Grading
                            grading = frappe.get_all(
                                "Stock Entry",
                                filters={
                                    "stock_entry_type": "Grading",
                                    "custom_bucket_id": bucket_id,
                                    "docstatus": 1
                                },
                                fields=["name", "custom_stem_length"],
                                order_by="creation desc",
                                limit=1
                            )

                            if grading and grading[0].custom_stem_length:
                                stem_length = grading[0].custom_stem_length

                            # B — Harvesting
                            if not stem_length:
                                harvesting = frappe.get_all(
                                    "Stock Entry",
                                    filters={
                                        "stock_entry_type": "Harvesting",
                                        "custom_bucket_id": bucket_id,
                                        "docstatus": 1
                                    },
                                    fields=["name", "custom_stem_length"],
                                    order_by="creation desc",
                                    limit=1
                                )

                                if harvesting and harvesting[0].custom_stem_length:
                                    stem_length = harvesting[0].custom_stem_length

                            # C — Receiving fallback
                            if not stem_length:
                                stem_length = receiving_doc.custom_stem_length

                            # ---------------------------------------------------------
                            shelf_doc = result.get("shelf_doc")
                            shelf_doc.farm = farm

                            # A bucket can be RECEIVED at one farm's coldstore but
                            # SHELVED at a different one (physically carried there --
                            # e.g. a satellite farm's harvest consolidated onto
                            # Kapkolia's sales shelf). The Shelf Item's warehouse must
                            # reflect where the stock actually is NOW (this shelf's own
                            # farm), or allocation keeps reading a warehouse the stems
                            # have already left.
                            #
                            # Posts the ARRIVAL hop(s) of the real `SO Warehouse Mapping`
                            # route (upande_packhouse.stock_movement) -- the same
                            # ledger-aware, multi-hop engine allocation's
                            # move_allocation_to_sold already uses for the terminal
                            # (sale) hop. stock_movement.post_arrival stops BEFORE that
                            # terminal hop (shelving is an arrival, not a sale) and is
                            # safe to call every time: each leg is skipped once this
                            # bucket's own ledger balance in that warehouse is zero, so
                            # re-shelving-adjacent calls never double-move stock. This
                            # replaces an earlier, simpler farm-keyed "Farm Transfer"
                            # mechanism (roses_warehouse_map.transfer_to_farm_warehouse)
                            # that overlapped with this engine instead of using it.
                            from upande_packhouse import stock_movement
                            business_unit = data.get("business_unit") or "Roses"
                            warehouse_by_ri = {}
                            for ri in receiving_doc.items:
                                arrival = stock_movement.post_arrival(
                                    bucket_id=bucket_id,
                                    item_code=ri.item_code,
                                    qty=ri.qty,
                                    source_warehouse=ri.t_warehouse,
                                    business_unit=business_unit,
                                    farm=farm,
                                    stem_length=stem_length,
                                )
                                warehouse_by_ri[ri.name] = arrival["warehouse"]

                            # Add bucket to shelf
                            total_qty = 0
                            new_items = []
                            for ri in receiving_doc.items:
                                new_item = shelf_doc.append("items", {})
                                new_item.bucket_id = bucket_id
                                new_item.variety = ri.item_code
                                new_item.date_added = frappe.utils.now_datetime()
                                new_item.stem_length = stem_length
                                new_item.stem_qty = ri.qty
                                new_item.greenhouse = ri.s_warehouse
                                new_item.warehouse = warehouse_by_ri.get(ri.name, ri.t_warehouse)
                                new_item.cut_stage = receiving_doc.custom_cut_stage
                                new_item.harvest_date = harvest_date
                                new_item.receiving_date = recv_date
                                new_item.farm = farm
                                new_item.harvester = receiving_doc.custom_harvester
                                new_item.graded_by = receiving_doc.custom_graded_by
                                new_item.grading_date = receiving_doc.custom_grading_date
                                total_qty += (ri.qty or 0)
                                new_items.append(new_item)
                            qty = total_qty

                            shelf_doc.save()

                            # Shelving Log: one "Shelved" row per Shelf Item row just created.
                            for new_item in new_items:
                                _write_shelved_log(new_item, shelf_id, farm)

                            # Mark bucket as shelved in receiving entry
                            mark_bucket_as_shelved(bucket_id, receiving_doc, result)

                            # ─────────────────────────────────────────────────────
                            # SKIPPED TRANSFER SELF-HEAL
                            # The load-to-truck sync failed, so the bucket is still on
                            # its remote origin shelf. It has now physically arrived and
                            # been shelved here, so remove the stale remote Shelf Item(s)
                            # and record a Skipped Transfer anomaly. update_transit_status()
                            # above already moved the OPL row to this shelf + cleared the
                            # transfer flags, so the order no longer fails on a skipped step.
                            # ─────────────────────────────────────────────────────
                            stale_shelves = result.get("stale_transfer_shelves") or []
                            if stale_shelves:
                                removed_from = []
                                for shi in stale_shelves:
                                    try:
                                        old_shelf = shi.get("parent")
                                        frappe.delete_doc("Shelf Item", shi.get("name"), force=1, ignore_permissions=True)
                                        removed_from.append(old_shelf)
                                        frappe.db.set_value("Shelf", old_shelf, "modified", frappe.utils.now())
                                    except Exception:
                                        pass
                                try:
                                    prev_shelf = removed_from[0] if removed_from else None
                                    opl_ref = result.get("transit_opl")
                                    frappe.get_doc({
                                        "doctype": "Bucket Reuse Anomaly",
                                        "bucket_id": bucket_id,
                                        "skipped_step": "Skipped Transfer",
                                        "detected_on": frappe.utils.now(),
                                        "farm": farm,
                                        "greenhouse": origin_greenhouse,
                                        "variety": variety,
                                        "stems": qty,
                                        "previous_shelf": prev_shelf,
                                        "discard_request": None,
                                    }).insert(ignore_permissions=True)
                                except Exception:
                                    frappe.log_error("Skipped Transfer anomaly log failed", "anomaly")
                                result["skipped_transfer"] = {"removed_from": removed_from}

                            # ─────────────────────────────────────────────────────
                            # NEW: UPDATE BUCKET ALLOCATION STATUS
                            # ─────────────────────────────────────────────────────
                            update_bas_transit_status(bucket_id, variety, farm, shelf_id, result)

                            # ─────────────────────────────────────────────────────
                            # NEW: CHECK IF OPL CAN BE AUTO-SUBMITTED
                            # ─────────────────────────────────────────────────────
                            check_and_submit_opl(bucket_id, result)

                            # Build response message
                            msg = f"Bucket {bucket_id} shelved successfully with {qty} stems."
                            if result.get("shelved_info"):
                                msg += f" Updated receiving entry: {result['shelved_info']['stock_entry']}."
                            if result.get("transit_updated"):
                                msg += f" Transit status updated on {result['transit_opl']}."
                            if result.get("skipped_transfer"):
                                removed_list = result["skipped_transfer"].get("removed_from") or []
                                msg += f" Skipped transfer recovered — removed from {', '.join(removed_list)}."
                            if result.get("bas_updated"):
                                msg += f" BAS cleared: {result['bas_available_qty']} stems now available."
                            if result.get("opl_submitted"):
                                msg += f" Auto-submitted OPLs: {', '.join(result['opl_submitted'])}."

                            frappe.response["data"] = {
                                "status": "success",
                                "message": msg,
                                "payload": {
                                    "shelf_id": shelf_id,
                                    "bucket_id": bucket_id,
                                    "stems": qty,
                                    "stem_length": stem_length,
                                    "transit_updated": result.get("transit_updated", False),
                                    "bas_updated": result.get("bas_updated", False),
                                    "bas_available_qty": result.get("bas_available_qty", 0),
                                    "opl_submitted": result.get("opl_submitted", []),
                                    "skipped_transfer": result.get("skipped_transfer", None)
                                }
                            }

                            frappe.db.commit()

    except Exception as e:
        frappe.log_error(f"Unexpected error", e)
        frappe.response["data"] = {
            "status": "error",
            "reason": "unknown_error",
            "message": f"An unexpected error occurred: {str(e)}"
        }


@frappe.whitelist()
def deleteSavedTrolleys():
    # Frappe Server Script (Type: API), api_method = deleteSavedTrolleys
    # Undo a SAVED trolley grouping WITHOUT re-shelving its buckets.
    # Clears custom_trolley_id / custom_loaded_in_trolley / custom_awaiting_transfer
    # on the trolley's Pick List Item rows, leaving custom_shelf untouched.
    # Only acts on not-yet-loaded rows (custom_in_transit != 1); loaded ones are
    # skipped and reported back.
    #
    # Matching is Pick List Item-FIRST (by custom_trolley_id), because saved
    # trolleys live on OPLs of EITHER docstatus (draft AND submitted) — an earlier
    # version scoped to draft OPLs only and matched 0 rows for submitted-OPL
    # trolleys. Farm is used only as a cross-farm safety check via the parent OPL.
    #
    # Payload (form params): trolley_ids = "T1|~|T2", farm = "Karen", dry_run = "1" (optional)
    # safe_exec: no def/import/+=/.append/parse_json/sql.

    frappe.response["message"] = {"status": "error", "message": "Script failed"}

    try:
        ids_raw = frappe.form_dict.get("trolley_ids") or ""
        farm = frappe.form_dict.get("farm") or ""
        dry_run = (frappe.form_dict.get("dry_run") or "") == "1"

        trolley_ids = []
        parts = ids_raw.split("|~|")
        p = 0
        while p < len(parts):
            v = parts[p].strip()
            if v != "":
                trolley_ids = trolley_ids + [v]
            p = p + 1

        if len(trolley_ids) == 0:
            frappe.response["message"] = {"status": "error", "message": "No trolley ids supplied."}
        else:
            rows = frappe.get_all(
                "Pick List Item",
                filters=[["custom_trolley_id", "in", trolley_ids]],
                fields=["name", "parent", "custom_trolley_id", "custom_in_transit"],
            )

            # Resolve parent OPL farms once (small set) for the cross-farm safety check.
            parents = {}
            i = 0
            while i < len(rows):
                parents[rows[i].parent] = 1
                i = i + 1
            pnames = []
            for k in parents:
                pnames = pnames + [k]
            farm_by_parent = {}
            if len(pnames) > 0:
                opls = frappe.get_all("Order Pick List", filters=[["name", "in", pnames]],
                                      fields=["name", "custom_farm"])
                j = 0
                while j < len(opls):
                    farm_by_parent[opls[j].name] = opls[j].custom_farm
                    j = j + 1

            cleared_count = 0
            skipped = {}
            skipped_farm = {}
            m = 0
            while m < len(rows):
                r = rows[m]
                row_farm = farm_by_parent.get(r.parent, "")
                if farm != "" and row_farm != farm:
                    skipped_farm[r.custom_trolley_id] = 1
                elif r.custom_in_transit == 1:
                    skipped[r.custom_trolley_id] = 1
                else:
                    if not dry_run:
                        frappe.db.set_value("Pick List Item", r.name, {
                            "custom_trolley_id": "",
                            "custom_loaded_in_trolley": 0,
                            "custom_awaiting_transfer": 0,
                        }, update_modified=True)
                    cleared_count = cleared_count + 1
                m = m + 1

            skipped_loaded = []
            for k2 in skipped:
                skipped_loaded = skipped_loaded + [k2]
            skipped_other_farm = []
            for k3 in skipped_farm:
                skipped_other_farm = skipped_other_farm + [k3]

            frappe.response["message"] = {
                "status": "success",
                "dry_run": dry_run,
                "cleared_count": cleared_count,
                "matched_rows": len(rows),
                "skipped_loaded": skipped_loaded,
                "skipped_other_farm": skipped_other_farm,
            }

    except Exception as e:
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def fetchAllocatedBuckets():
    # Frappe Server Script (Type: API), api_method = fetchAllocatedBuckets
    # Buckets awaiting transfer for a farm, for the bucket-requests app.
    # Visibility is gated by STATE (custom_awaiting_transfer=1, custom_in_transit=0),
    # NOT by OPL creation date — a bucket allocated on a previous day is still
    # awaiting transfer until it is loaded/shelved, so it must remain downloadable.
    # Payload: { "farm": "<farm>", "opl_name": "<optional OPL>" }
    payload = frappe.request.get_json()
    if not payload or 'farm' not in payload:
        frappe.response["error"] = "Farm is required in JSON payload"
        frappe.response["http_status_code"] = 400
    else:
        farm_name = payload['farm']
        opl_name = payload.get('opl_name')  # Optional: filter by specific OPL

        # Delivery-date window: buckets processed today go out on the next days, so
        # only download OPLs whose Sales Order delivers in [tomorrow, day-after]
        # (e.g. run on the 29th -> deliveries on the 30th and 31st). Overridable via
        # payload from_date/to_date. A specific opl_name request ignores the window.
        from_date = payload.get('from_date')
        to_date = payload.get('to_date')
        if not from_date:
            from_date = str(frappe.utils.add_days(frappe.utils.today(), 1))
        if not to_date:
            to_date = str(frappe.utils.add_days(frappe.utils.today(), 2))

        vehicles = frappe.get_all(
            "Vehicle",
            filters={"custom_dispatch_truck": ["!=", 1]},
            fields=["name"],
            pluck="name",
        )

        # ── Step 1: Pick List Item buckets awaiting transfer for this farm ─────────
        #    State (awaiting_transfer=1, not yet in transit) is the gate; buckets
        #    drop off automatically once transferred/shelved. No date filter.
        pli_filters = {
            "custom_awaiting_transfer": 1,
            "custom_in_transit": 0,
            "custom_bucket": ["!=", ""],
            "warehouse": ["like", "%" + farm_name + "%"],
            "parenttype": "Order Pick List",
        }
        if opl_name:
            pli_filters["parent"] = opl_name

        pick_list_items = frappe.get_all(
            "Pick List Item",
            filters=pli_filters,
            fields=[
                "name",
                "parent",
                "item_code",
                "item_name",
                "custom_bucket",
                "custom_shelf",
                "warehouse",
                "qty",
                "uom",
                "custom_stem_length",
                "sales_order",
                "sales_order_item",
            ],
        )

        if not pick_list_items:
            frappe.response["message"] = (
                "No remote buckets awaiting transfer found for farm: " + farm_name
            )
            frappe.response["data"] = []
            frappe.response["vehicles"] = vehicles
            frappe.response["http_status_code"] = 200
        else:
            # ── Step 2: Parent OPL info for the matched buckets (docstatus 0/1) ────
            opl_names = list(set(item["parent"] for item in pick_list_items))
            opl_docs = frappe.get_all(
                "Order Pick List",
                filters={"name": ["in", opl_names], "docstatus": ["in", [0, 1]]},
                fields=[
                    "name",
                    "creation",
                    "customer",
                    "custom_order_name",
                    "custom_consignee",
                    "sales_order",
                    "custom_status",
                ],
            )
            opl_map = {o["name"]: o for o in opl_docs}

            # ── Delivery-date window: keep only OPLs whose Sales Order delivers in
            #    [from_date, to_date]. Skipped for a specific opl_name request. ──────
            if not opl_name:
                so_names = list(set(
                    (opl_map[n].get("sales_order")) for n in opl_map if opl_map[n].get("sales_order")
                ))
                in_window_so = {}
                if so_names:
                    so_rows = frappe.get_all(
                        "Sales Order",
                        filters={"name": ["in", so_names],
                                 "delivery_date": ["between", [from_date, to_date]]},
                        fields=["name"],
                    )
                    for sr in so_rows:
                        in_window_so[sr["name"]] = 1
                # Rebuild opl_map to only OPLs whose SO is in the delivery window.
                kept = {}
                for n in opl_map:
                    so = opl_map[n].get("sales_order")
                    if so and so in in_window_so:
                        kept[n] = opl_map[n]
                opl_map = kept

            # Drop items whose parent OPL is out-of-window / cancelled / missing.
            pick_list_items = [it for it in pick_list_items if it["parent"] in opl_map]

            if not pick_list_items:
                frappe.response["message"] = (
                    "No remote buckets awaiting transfer found for farm: " + farm_name
                )
                frappe.response["data"] = []
                frappe.response["vehicles"] = vehicles
                frappe.response["http_status_code"] = 200
            else:
                # ── Step 3: Bulk fetch latest harvest date per bucket ──────────────
                bucket_ids = list(set(item["custom_bucket"] for item in pick_list_items))
                all_harvest_entries = frappe.get_all(
                    "Stock Entry",
                    filters={
                        "stock_entry_type": "Harvesting",
                        "custom_bucket_id": ["in", bucket_ids],
                        "docstatus": 1,
                    },
                    fields=["custom_bucket_id", "posting_date", "posting_time"],
                    order_by="custom_bucket_id asc, posting_date desc, posting_time desc",
                )
                bucket_harvest_map = {}
                for entry in all_harvest_entries:
                    bid = entry["custom_bucket_id"]
                    if bid not in bucket_harvest_map:
                        bucket_harvest_map[bid] = {
                            "harvest_date": entry["posting_date"],
                            "harvest_time": entry["posting_time"],
                        }

                # ── Step 4: Assemble result ───────────────────────────────────────
                # A bucket can appear on MULTIPLE Pick List Item rows of the same OPL
                # (mixed-box / split allocations). The app treats a physical bucket as
                # one, so collapse duplicates to a single row per (OPL, bucket) — the
                # transfer sync (setOfflineTrolleyFlags) flags all sibling rows anyway.
                result = []
                seen_bucket = {}
                for item in pick_list_items:
                    bucket_id = item["custom_bucket"]
                    dedupe_key = str(item["parent"]) + "||" + str(bucket_id).lower()
                    if dedupe_key in seen_bucket:
                        continue
                    seen_bucket[dedupe_key] = 1
                    harvest_info = bucket_harvest_map.get(bucket_id, {})
                    opl_info = opl_map.get(item["parent"], {})
                    created = opl_info.get("creation")
                    allocated_date = str(created)[:10] if created else None
                    result.append({
                        # OPL Information
                        "opl_name": item["parent"],
                        "customer": opl_info.get("customer"),
                        "order_name": opl_info.get("custom_order_name"),
                        "consignee": opl_info.get("custom_consignee"),
                        "sales_order": item["sales_order"],
                        "opl_status": opl_info.get("custom_status"),
                        # Item Information
                        "pick_list_item_id": item["name"],
                        "item_code": item["item_code"],
                        "item_name": item["item_name"],
                        "qty": item["qty"],
                        "uom": item["uom"],
                        "stem_length": item["custom_stem_length"],
                        # Location Information
                        "shelf_location": item["custom_shelf"],
                        "warehouse": item["warehouse"],
                        # Bucket Information
                        "bucket_id": bucket_id,
                        "harvest_date": harvest_info.get("harvest_date"),
                        "harvest_time": harvest_info.get("harvest_time"),
                        "allocated_date": allocated_date,
                    })

                frappe.response["message"] = (
                    "Found " + str(len(result)) + " remote buckets awaiting transfer for " + farm_name
                )
                frappe.response["data"] = result
                frappe.response["vehicles"] = vehicles
                frappe.response["http_status_code"] = 200


@frappe.whitelist()
def fetchColdPostHarvestChemicals():
    try:
        rows = frappe.db.sql(
            "SELECT chemical_name, default_unit FROM `tabPost Harvest Chemicals` "
            "WHERE IFNULL(disabled,0)=0 ORDER BY chemical_name", as_dict=1)
        frappe.response["message"] = [
            {"name": r.chemical_name, "item_name": r.chemical_name, "stock_uom": r.default_unit or "g"}
            for r in rows
        ]
    except Exception as e:
        frappe.log_error("fetchColdPostHarvestChemicals error", str(e))
        frappe.response["message"] = []


@frappe.whitelist()
def fetchMixingTanks():
    # ── fetchMixingTanks ──────────────────────────────────────────────────
    # Body: { farm }  (or query string) — returns active tanks for a farm.
    try:
        farm = frappe.form_dict.get("farm") or ""
        filters = {"is_active": 1}
        if farm:
            filters["farm"] = farm
        rows = frappe.get_all(
            "Post Harvest Mixing Tank",
            filters=filters,
            fields=["name", "farm", "tank_name", "capacity_l", "location"],
            order_by="farm asc, tank_name asc",
            limit_page_length=0,
        )
        frappe.response["message"] = {"status": "success", "data": rows}
    except Exception as e:
        frappe.log_error("fetchMixingTanks error", str(e))
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def fetchPackhouseQCFormData():
    try:
        data     = frappe.form_dict
        opl_name = (data.get("order_pick_list", "")
                    or data.get("order_spec", "")
                    or data.get("order_spec_id", ""))
        box_scan = data.get("box_label", "").strip() if data.get("box_label") else ""
        spec_filter = data.get("specification", "").strip() if data.get("specification") else ""
        resolved_box_name = ""

        # Scanning a box resolves the order directly -- no need to search for it.
        if box_scan and not opl_name:
            box_doc = frappe.db.get_value(
                "Box Label", box_scan, ["name", "order_pick_list"], as_dict=1
            )
            if not box_doc:
                # Fallback: scanned value might be the box_label text field, not
                # the docname, if the printed code encodes that instead.
                alt_name = frappe.db.get_value("Box Label", {"box_label": box_scan}, "name")
                if alt_name:
                    box_doc = frappe.db.get_value(
                        "Box Label", alt_name, ["name", "order_pick_list"], as_dict=1
                    )
            if box_doc:
                opl_name = box_doc.get("order_pick_list", "")
                resolved_box_name = box_doc.get("name", "")

        # Helper: extract greenhouse prefix from warehouse name
        def greenhouse_from_wh(wh):
            if not wh:
                return ""
            # Strip company suffix  " - KR" / " - KF" etc.
            if " - " in wh:
                wh = wh.rsplit(" - ", 1)[0]
            # Warehouses that are not greenhouse locations
            non_gh = ["Packhouse", "Rejects", "WIP", "Transit",
                      "Main Store", "Cold Room", "Finished"]
            for skip in non_gh:
                if skip in wh:
                    return ""
            # Clip at receiving/cold-store suffix to keep only the name prefix
            for clip in ["Receiving", "Cold"]:
                if clip in wh:
                    wh = wh.split(clip)[0].strip()
                    break
            return wh.strip()

        # Helper: full Specification detail (every field + box items +
        # consumables), given just the spec name -- decoupled from any
        # particular order/variety, so it also works for a spec the operator
        # picked/corrected manually (e.g. Final QC's scan-based flow, where the
        # order's own variety may have no Sales Order Item -> Specification
        # link at all).
        def build_spec_detail(spec_name):
            if not spec_name or not frappe.db.exists("Specifications", spec_name):
                return None
            spec_doc = frappe.get_doc("Specifications", spec_name)
            return {
                "spec_name":              spec_doc.name,
                "customer":               spec_doc.customer,
                "category_code":          spec_doc.category_code,
                "ftnft":                  spec_doc.ftnft,
                "spec_type":              spec_doc.spec_type,
                "status":                 spec_doc.status,
                "valid_from":             str(spec_doc.valid_from or ""),
                "expiry_date":            str(spec_doc.expiry_date or ""),
                "cut_stage":              spec_doc.cut_stage,
                "defoliation_length":     spec_doc.defoliation_length,
                "rubber_band_type":       spec_doc.rubber_band_type,
                "rubber_band_distance_1": spec_doc.rubber_band_distance_1,
                "rubber_band_distance_2": spec_doc.rubber_band_distance_2,
                "box_assortment":         spec_doc.box_assortment,
                "consumables_charge":     spec_doc.consumables_charge,
                "documentation_charge":   spec_doc.documentation_charge,
                "certificate_of_origin":  spec_doc.certificate_of_origin,
                "box_items": [
                    {
                        "bunch_type":         bi.bunch_type,
                        "colour":             bi.get("colour"),
                        "variety":            bi.get("variety"),
                        "hz_bud_count_range": bi.hz_bud_count_range,
                        "stems_per_bunch":    bi.stems_per_bunch,
                        "length":             bi.length,
                        "box_type":           bi.box_type,
                        "bunches_per_box":    bi.bunches_per_box,
                        "pack_rate":          bi.pack_rate,
                    }
                    for bi in spec_doc.box_items
                ],
                "approved_varieties": [
                    {
                        "variety":     av.variety,
                        "colour":      av.colour,
                        "headsize_cm": av.get("headsize_cm"),
                        "budcount":    av.get("budcount"),
                    }
                    for av in spec_doc.approved_varieties
                ],
                "consumables": [
                    {
                        "consumable_type": c.consumable_type,
                        "description":     c.description,
                        "qty_per_bunch":    c.qty_per_bunch,
                        "qty_per_box":      c.qty_per_box,
                        "price_inclusive":  c.price_inclusive,
                    }
                    for c in spec_doc.consumables
                ],
            }

        # ── 1. Control Points with Control Area ──────────────────────────
        cp_rows = frappe.db.sql(
            "SELECT name, control_point, control_area"
            " FROM `tabQC Control Point` ORDER BY control_area, name",
            as_dict=1
        )
        control_points = []
        for cp in cp_rows:
            control_points.append({
                "name":          cp.get("name"),
                "control_point": cp.get("control_point") or cp.get("name"),
                "control_area":  cp.get("control_area") or cp.get("control_point") or cp.get("name")
            })

        # ── 1b. Active Specifications, for the "filter orders by spec" picker ──
        spec_rows = frappe.db.sql(
            "SELECT name, spec_name, customer, category_code, box_assortment,"
            " cut_stage, status"
            " FROM `tabSpecifications` WHERE status = 'Active'"
            " ORDER BY spec_name LIMIT 500",
            as_dict=1
        )
        specifications_list = [dict(r) for r in spec_rows]

        # ── 2. Active OPLs (submitted), optionally filtered by team and/or the
        # selected Specification. `team` filters straight on the Order Pick List
        # (never read before, so team filtering did nothing). For a Specification
        # the old FK path (Sales Order Item.custom_line) is blank on nearly all
        # orders, so orders never appeared -- instead resolve the spec's customer
        # and show that customer's active orders.
        team_filter = data.get("team", "").strip() if data.get("team") else ""
        opl_conds = ["docstatus = 1"]
        opl_vals  = []
        if spec_filter:
            spec_customer = frappe.db.get_value("Specifications", spec_filter, "customer") or ""
            if spec_customer:
                opl_conds.append("customer = %s")
                opl_vals.append(spec_customer)
        if team_filter:
            opl_conds.append("team = %s")
            opl_vals.append(team_filter)
        opl_rows = frappe.db.sql(
            "SELECT name, customer, team, farm,"
            " custom_total_stems, order_name, schedule_number"
            " FROM `tabOrder Pick List`"
            " WHERE " + " AND ".join(opl_conds) +
            " ORDER BY modified DESC LIMIT 300",
            opl_vals, as_dict=1
        )
        order_specs = []
        for o in opl_rows:
            order_specs.append({
                "name":            o.get("name"),
                "order_name":      o.get("order_name", ""),
                "customer":        o.get("customer", ""),
                "team":            o.get("team", ""),
                "farm":            o.get("farm", ""),
                "total_stems":     o.get("custom_total_stems", 0),
                "status":          o.get("custom_status", ""),
                "schedule_number": o.get("schedule_number", "")
            })

        # ── 3. Item locations + varieties + greenhouses for selected OPL ─
        item_locations           = []
        variety_options          = []
        greenhouse_options       = []
        packing_guide            = []
        variety_bunches          = []
        total_bunches            = 0
        pending_quarantine_stems = 0

        if opl_name:
            loc_rows = frappe.db.sql(
                "SELECT idx, item_code, item_name, warehouse,"
                " qty, stock_qty, uom, conversion_factor, custom_sale_order_item,"
                " stem_length"
                " FROM `tabPick List Item`"
                " WHERE parent = %s ORDER BY idx",
                opl_name, as_dict=1
            )
            item_locations = [
                {k: v for k, v in r.items() if k not in ("custom_sale_order_item", "stem_length")}
                for r in loc_rows
            ]

            # Extract unique varieties and greenhouses
            seen_v  = set()
            seen_gh = set()
            for loc in loc_rows:
                v  = loc.get("item_code") or loc.get("item_name")
                gh = greenhouse_from_wh(loc.get("warehouse", ""))
                if v and v not in seen_v:
                    seen_v.add(v)
                    variety_options.append(v)
                if gh and gh not in seen_gh:
                    seen_gh.add(gh)
                    greenhouse_options.append(gh)

            # Packing Guide (Order Pick List child) is the authoritative per-variety
            # breakdown: every variety in the order with its colour, length and
            # bunch/stem counts -- richer than the picked Pick List Item rows, which
            # only cover what has been allocated so far. Drive the variety list and
            # total bunches from here so bunch sampling sees the whole order.
            pg_rows = frappe.db.sql(
                "SELECT variety, colour, item_name, length, stems_per_bunch,"
                " bunches, stems, pack_rate, box_type, box_kind, sales_order_item"
                " FROM `tabPacking Guide` WHERE parent = %s ORDER BY idx",
                opl_name, as_dict=1
            )
            packing_guide = [dict(r) for r in pg_rows]
            pg_agg = {}
            pg_order = []
            for r in pg_rows:
                pv = r.get("variety")
                if not pv:
                    continue
                total_bunches = total_bunches + (r.get("bunches") or 0)
                if pv not in pg_agg:
                    pg_agg[pv] = {
                        "variety":         pv,
                        "colour":          r.get("colour") or "",
                        "length":          r.get("length") or "",
                        "stems_per_bunch": r.get("stems_per_bunch") or 0,
                        "total_bunches":   0,
                        "total_stems":     0,
                    }
                    pg_order.append(pv)
                pg_agg[pv]["total_bunches"] = pg_agg[pv]["total_bunches"] + (r.get("bunches") or 0)
                pg_agg[pv]["total_stems"]   = pg_agg[pv]["total_stems"] + (r.get("stems") or 0)
                if pv not in variety_options:
                    variety_options.append(pv)
            for pv in pg_order:
                variety_bunches.append(pg_agg[pv])

            # Packhouse QC doctype removed on this site; no prior-quarantine tracking.
            pending_quarantine_stems = 0

        # Authoritative header for the resolved order -- needed because a scanned
        # box may resolve an OPL outside the "last 300" list the dropdown shows.
        order_pick_list_detail = None
        if opl_name:
            opl_row = frappe.db.get_value(
                "Order Pick List", opl_name,
                ["name", "customer", "team", "farm", "custom_total_stems",
                 "order_name", "schedule_number"],
                as_dict=1
            )
            if opl_row:
                order_pick_list_detail = {
                    "name":            opl_row.get("name"),
                    "order_name":      opl_row.get("order_name", ""),
                    "customer":        opl_row.get("customer", ""),
                    "team":            opl_row.get("team", ""),
                    "farm":            opl_row.get("farm", ""),
                    "total_stems":     opl_row.get("custom_total_stems", 0),
                    "status":          opl_row.get("custom_status", ""),
                    "schedule_number": opl_row.get("schedule_number", "")
                }

        # ── 3b. What's actually recorded on the scanned box itself ───────
        # An order can carry more than one variety, so "the order's first
        # variety" isn't necessarily what's in THIS box -- Box Label's own
        # box_item rows are the ground truth for this specific box, letting the
        # operator compare the box's own recorded length/pack rate against the
        # specification below rather than a same-order guess. Computed before
        # the spec matching below so that matching can prefer this box's own
        # recorded length when disambiguating between spec variants.
        scanned_box_detail = None
        scanned_box_variety = ""
        scanned_box_length = ""
        if resolved_box_name:
            box_full = frappe.get_doc("Box Label", resolved_box_name)
            first_item = box_full.box_item[0] if box_full.box_item else None
            scanned_box_variety = first_item.variety if first_item else ""
            scanned_box_length = (first_item.length if first_item else "") or box_full.length or ""
            scanned_box_detail = {
                "box_number":      box_full.box_number,
                "box_total_count": box_full.box_total_count,
                "pack_rate":       box_full.pack_rate,
                "length":          scanned_box_length,
                "customer":        box_full.customer,
                "items": [
                    {"variety": bi.variety, "qty": bi.qty, "length": bi.length}
                    for bi in box_full.box_item
                ],
            }

        # ── 3c. Matching Specification for each variety in this order ────
        # Primary path is a real FK chain, not a guess: each Pick List Item row
        # carries `custom_sale_order_item`, the child docname of the Sales
        # Order Item that variety was allocated against, and that Sales Order
        # Item's `custom_line` field holds the exact Specifications doc name.
        # That link is blank on most orders today, though -- so when it's
        # empty, fall back to matching this customer's Specifications by
        # variety (and length, to disambiguate variants like "52CM"/"62CM")
        # against the Spec Box Item child table. No manual picking either way:
        # if neither path finds anything, there simply is no specification yet.
        def find_spec_by_customer_variety(customer, variety, length):
            # variety lives on the `Spec Approved Variety` child (approved_varieties),
            # NOT on `Spec Box Item` -- the old query referenced the non-existent
            # `bi.variety` column and crashed the whole form load (1054).
            if not customer or not variety:
                return None
            rows = frappe.db.sql(
                "SELECT s.name FROM `tabSpecifications` s"
                " JOIN `tabSpec Approved Variety` av ON av.parent = s.name"
                " WHERE s.customer = %(customer)s AND av.variety = %(variety)s"
                " AND s.status = 'Active' LIMIT 1",
                {"customer": customer, "variety": variety}, as_dict=1
            )
            return rows[0]["name"] if rows else None

        specifications = {}
        if opl_name and loc_rows:
            variety_soi = {}
            variety_length = {}
            for loc in loc_rows:
                v   = loc.get("item_code") or loc.get("item_name")
                soi = loc.get("custom_sale_order_item")
                if v and soi and v not in variety_soi:
                    variety_soi[v] = soi
                if v and v not in variety_length:
                    variety_length[v] = loc.get("stem_length") or ""

            order_customer = order_pick_list_detail.get("customer") if order_pick_list_detail else ""
            for variety, soi_name in variety_soi.items():
                spec_name = frappe.db.get_value("Sales Order Item", soi_name, "custom_line")
                if not spec_name:
                    # Prefer the scanned box's own recorded length for its own
                    # variety (most precise); otherwise fall back to whatever
                    # this order line itself recorded.
                    length = scanned_box_length if variety == scanned_box_variety else variety_length.get(variety, "")
                    spec_name = find_spec_by_customer_variety(order_customer, variety, length)
                detail = build_spec_detail(spec_name)
                if detail:
                    specifications[variety] = detail

        # ── 3d. Full detail for a directly-picked/overridden spec ────────
        # Independent of any order/variety match -- covers Reject Recorder's
        # Customer->Spec picker, used before an order/variety is even chosen.
        specification_detail = build_spec_detail(spec_filter) if spec_filter else None

        # ── 4. QC Parameters (per-issue concern types) ───────────────────
        params = frappe.db.sql(
            "SELECT name, parameter, tolerance_thresholds"
            " FROM `tabQC Parameters` ORDER BY parameter",
            as_dict=1
        )

        # ── 4b. Packhouse Rejection Reasons (top-level overall reason) ────
        reasons = frappe.db.sql(
            "SELECT name, reason FROM `tabPackhouse Rejection Reason` ORDER BY reason",
            as_dict=1
        )

        # ── 5. Real boxes packed for this order (Box Label doctype) ──────
        boxes = []
        box_total_count = 0
        if opl_name:
            box_rows = frappe.db.sql(
                "SELECT name, box_number, box_total_count, pack_rate"
                " FROM `tabBox Label` WHERE order_pick_list = %s ORDER BY box_number",
                opl_name, as_dict=1
            )
            boxes = [dict(r) for r in box_rows]
            if boxes:
                box_total_count = boxes[0].get("box_total_count") or len(boxes)

        # ── 6. QC Incharge options ────────────────────────────────────────
        qc_roles = [
            "Packhouse Manager", "Quality Manager", "QUALITY CONTROLLER",
            "System Manager", "Administrator"
        ]
        role_ph = ",".join(["%s"] * len(qc_roles))
        incharge_rows = frappe.db.sql(
            "SELECT DISTINCT u.name, u.full_name"
            " FROM `tabUser` u"
            " JOIN `tabHas Role` r ON r.parent = u.name"
            " WHERE r.role IN (" + role_ph + ") AND u.enabled = 1"
            " ORDER BY u.full_name LIMIT 50",
            qc_roles, as_dict=1
        )
        if not incharge_rows:
            incharge_rows = frappe.db.sql(
                "SELECT name, full_name FROM `tabUser`"
                " WHERE enabled = 1 AND user_type = 'System User'"
                " ORDER BY full_name LIMIT 50",
                as_dict=1
            )

        frappe.response["message"] = {
            "success":                  True,
            "control_points":           control_points,
            "order_specs":              order_specs,      # list of active OPLs
            "item_locations":           item_locations,
            "varieties":                variety_options,
            "variety_bunches":          variety_bunches,   # per-variety bunch/stem totals
            "packing_guide":            packing_guide,     # raw Packing Guide rows
            "total_bunches":            total_bunches,     # order total bunches
            "greenhouses":              greenhouse_options,
            "params":                   [dict(p) for p in params],
            "reasons":                  [dict(r) for r in reasons],
            "boxes":                    boxes,
            "box_total_count":          box_total_count,
            "order_pick_list_detail":   order_pick_list_detail,
            "specifications":           specifications,   # keyed by variety
            "specifications_list":      specifications_list,
            "specification_detail":     specification_detail,
            "scanned_box_detail":       scanned_box_detail,
            "scanned_box_variety":      scanned_box_variety,
            "qc_incharge_options":      [dict(u) for u in incharge_rows],
            "pending_quarantine_stems": pending_quarantine_stems
        }

    except Exception as e:
        frappe.log_error(str(e), "fetchPackhouseQCFormData Error")
        frappe.response["message"] = {"success": False, "error": str(e)}


@frappe.whitelist()
def fetchQcParameters():
    try:
        data = frappe.get_all(
            "QC Parameters",
            fields=["name", "parameter", "tolerance_thresholds"]
        )

        frappe.response["message"] = data

    except Exception as e:
        frappe.throw(str(e))


@frappe.whitelist()
def getBatchByBucket():
    data = frappe.request.get_json()

    bucket_id = data.get("bucket_id")
    if not bucket_id:
        frappe.throw("bucket_id is required")

    bucket_id = bucket_id.strip()
    bucket_upper = bucket_id.upper()

    # 1. Find the receiving entry + batch_no. custom_bucket_id comparison is
    # case-insensitive by collation, so no LOWER() (keeps the index usable).
    receiving = frappe.db.sql("""
        SELECT name, custom_receiving_batch_id AS batch_no,
               farm AS farm, company
        FROM `tabStock Entry`
        WHERE custom_bucket_id = %s
          AND stock_entry_type IN ('Receiving', 'Late Receipt')
          AND docstatus = 1
        ORDER BY creation DESC
        LIMIT 1
    """, (bucket_id,), as_dict=1)

    if not receiving:
        frappe.throw("No Receiving Stock Entry found for bucket %s" % bucket_upper)

    batch_no = receiving[0].batch_no or ""
    farm = receiving[0].farm or ""
    company = receiving[0].company or ""

    if not batch_no:
        frappe.throw("No batch number found for bucket %s" % bucket_upper)

    # 2. Base receiving rows for the batch (one per bucket). Simple, no self-joins.
    recv_rows = frappe.db.sql("""
        SELECT se.custom_bucket_id AS bucket_id, sei.qty AS stems,
               sei.item_code, sei.item_name, sei.t_warehouse AS warehouse,
               sei.basic_rate, sei.cost_center,
               se.farm AS farm, se.custom_greenhouse AS greenhouse,
               se.name AS stock_entry, se.creation
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sei ON sei.parent = se.name
        WHERE se.custom_receiving_batch_id = %s
          AND se.stock_entry_type IN ('Receiving', 'Late Receipt')
          AND se.docstatus = 1
        ORDER BY se.creation ASC
    """, (batch_no,), as_dict=1)

    # 3. Every movement for the batch, flat. Per-bucket quarantine/accept/reject
    # state is derived in Python -- avoids the previous 5-way self-join that
    # exploded into a cartesian product and timed the request out.
    move_rows = frappe.db.sql("""
        SELECT LOWER(se.custom_bucket_id) AS bid, se.stock_entry_type AS se_type,
               se.creation AS creation, sei.s_warehouse AS s_wh,
               sei.t_warehouse AS t_wh, sei.qty AS qty
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sei ON sei.parent = se.name
        WHERE se.custom_receiving_batch_id = %s AND se.docstatus = 1
    """, (batch_no,), as_dict=1)

    q_wh = {}        # bid -> latest quarantine warehouse
    q_wh_time = {}   # bid -> creation of that latest quarantine move
    accept = {}      # bid -> released back (Material Transfer out of quarantine)
    reject = {}      # bid -> rejected out (Quarantine Rejects)
    q_in = {}        # bid -> stems moved into quarantine
    q_out = {}       # bid -> stems moved out of quarantine
    for m in move_rows:
        bid = m.bid or ""
        twl = (m.t_wh or "").lower()
        swl = (m.s_wh or "").lower()
        qty = m.qty or 0
        is_q_move = (m.se_type in ("Receiving Quarantined", "Quarantine Transfer")) or ("quarantin" in twl)
        if is_q_move:
            prev = q_wh_time.get(bid)
            if prev is None or (m.creation and m.creation > prev):
                q_wh_time[bid] = m.creation
                q_wh[bid] = m.t_wh or ""
        if m.se_type == "Material Transfer" and "quarantin" in swl:
            accept[bid] = True
        if m.se_type == "Quarantine Rejects":
            reject[bid] = True
        if "quarantin" in twl:
            q_in[bid] = q_in.get(bid, 0) + qty
        if "quarantin" in swl:
            q_out[bid] = q_out.get(bid, 0) + qty

    # 4. Build the bucket list (Python, in-memory).
    buckets = []
    total_stems = 0
    seen = set()
    has_active_quarantine = False

    for row in recv_rows:
        bid_lower = (row.bucket_id or "").lower()
        if bid_lower in seen:
            continue
        seen.add(bid_lower)
        bid_upper = (row.bucket_id or "").upper()

        net_q = int((q_in.get(bid_lower, 0) or 0) - (q_out.get(bid_lower, 0) or 0))
        is_in_quarantine = net_q > 0
        quarantine_stems = net_q if net_q > 0 else 0

        has_release_accept = bool(accept.get(bid_lower))
        has_release_reject = bool(reject.get(bid_lower))

        if is_in_quarantine:
            status = "quarantined"
            has_active_quarantine = True
        elif has_release_reject and has_release_accept:
            status = "processed"
        elif has_release_accept:
            status = "accepted"
        elif has_release_reject:
            status = "rejected"
        else:
            status = "pending"

        buckets.append({
            "bucket_id": bid_upper,
            "stems": (quarantine_stems if is_in_quarantine else int(row.stems or 0)),
            "item_code": row.item_code or "",
            "item_name": row.item_name or "",
            "warehouse": row.warehouse or "",
            "quarantine_warehouse": q_wh.get(bid_lower, "") or "",
            "basic_rate": row.basic_rate or 0,
            "cost_center": row.cost_center or "",
            "farm": row.farm or "",
            "greenhouse": row.greenhouse or "",
            "stock_entry": row.stock_entry or "",
            "status": status,
            "selected": True,
            "quarantine_stems": quarantine_stems,
            "is_in_quarantine": is_in_quarantine,
        })
        total_stems += int(row.stems or 0)

    frappe.response["status"] = "success"
    frappe.response["message"] = "Batch %s loaded" % batch_no
    frappe.response["batch_no"] = batch_no
    frappe.response["farm"] = farm
    frappe.response["company"] = company
    frappe.response["total_buckets"] = len(buckets)
    frappe.response["total_stems"] = total_stems
    frappe.response["buckets"] = buckets
    frappe.response["scanned_bucket"] = bucket_upper
    frappe.response["has_active_quarantine"] = has_active_quarantine


@frappe.whitelist()
def getBucketStatus():
    bucket_id = frappe.form_dict.get('bucket_id')

    try:
        if not bucket_id:
            frappe.response['data'] = {
                "error": "Missing bucket_id"
            }
        else:
            stock_entries = frappe.get_all(
                "Stock Entry",
                filters={
                    "custom_bucket_id": bucket_id,
                    "stock_entry_type": "Harvesting"
                },
                fields=[
                    "name",
                    "farm",
                    "custom_greenhouse",
                    "custom_stem_length",
                    "posting_date"
                ]
            )

            if not stock_entries:
                frappe.response['data'] = {
                    "message": "No Stock Entries found for this bucket."
                }
            else:
                data = []
                for se in stock_entries:
                    stock_entry_items = frappe.get_all(
                        "Stock Entry Detail",
                        filters={
                            "parent": se.name
                        },
                        fields=["qty", "item_name"]
                    )

                    total_stems = sum(item.qty or 0 for item in stock_entry_items)
                    variety = stock_entry_items[0].item_name if stock_entry_items else None

                    posting_date = None
                    if se.posting_date:
                        posting_date = str(se.posting_date)

                    data.append({
                        "stock_entry": se.name,
                        "custom_farm": se.farm,
                        "custom_greenhouse": se.custom_greenhouse,
                        "custom_stem_length": se.custom_stem_length,
                        "number_of_stems": total_stems,
                        "variety": variety,
                        "posting_date": posting_date
                    })

                frappe.response['data'] = data

    except Exception as e:
        frappe.log_error(f"Error in getBucketStatus: {e}")
        frappe.response['data'] = {
            "error": str(e)
        }


@frappe.whitelist()
def getDiscardRequestBuckets():
    # Frappe Server Script (Type: API), api_method = getDiscardRequestBuckets
    # The discard work-list for a farm: Discard Request Bucket child rows of an
    # APPROVED Discard Request whose bucket is STILL PHYSICALLY ON A SHELF AT THAT
    # FARM. The operator "can only discard what's on the list", and can only discard
    # what's actually on the shelf in front of them. frappe.get_all ignores user
    # permissions so any logged-in operator can read it.
    # Payload: { "farm": "<Farm>" }  ->  { status, farm, buckets:[...], count }
    #
    # Why "currently on a shelf at the farm" is the right filter (not "ever
    # discarded"): buckets are REUSED across many harvest cycles. A bucket discarded
    # in a PRIOR cycle can be re-harvested, re-shelved, re-aged and legitimately
    # re-listed — the old "ever-discarded" exclusion wrongly hid all of those, badly
    # under-reporting the list. The live `Shelf Item` is the physical truth: a bucket
    # that has been discarded / issued / transferred away has NO Shelf Item at this
    # farm, while a re-shelved bucket has a fresh one. Allocated buckets are also
    # excluded (reserved for an order, must not be discarded).
    frappe.response["message"] = {"status": "error", "buckets": []}
    try:
        data = frappe.request.get_json() or {}
        farm = data.get("farm")
        if not farm:
            frappe.response["message"] = {"status": "error", "message": "farm is required.", "buckets": []}
        else:
            # Approved requests: name -> creation, so a bucket listed on several
            # requests resolves to the LATEST one (max creation).
            approved = frappe.get_all(
                "Discard Request",
                filters={"workflow_state": "Approved"},
                fields=["name", "creation"],
                limit_page_length=0,
            )
            approved_names = {}
            i = 0
            while i < len(approved):
                approved_names[approved[i].name] = str(approved[i].creation or "")
                i = i + 1

            # Live shelf state at THIS farm — the physical set of buckets currently on
            # a shelf whose farm = the requested farm AND that have been sitting there
            # long enough to be discardable. Discarded / issued / transferred buckets
            # aren't here; a re-shelved (reused) bucket IS here but only qualifies once
            # its CURRENT occupancy has aged past `discard_age` — so a bucket that was
            # discarded in a prior cycle and freshly re-shelved with new flowers is not
            # flagged for discard while those flowers are still fresh. Age = now minus
            # Shelf Item.date_added (how long the current flowers have sat on the shelf,
            # matching how the nightly Auto Discard Request selects). UPPER()-folded
            # because bucket_id casing is inconsistent across tables.
            # Age basis = the bucket's LATEST Harvesting Stock Entry (falling back to
            # when the current flowers were shelved), exactly as the nightly Auto
            # Discard Request selects. Using the latest harvest makes reuse correct: a
            # re-harvested bucket is measured on its NEW flowers, so it only qualifies
            # once those have aged past discard_age.
            discard_age = frappe.db.get_single_value('Production Settings', 'discard_age')
            if not discard_age:
                discard_age = 5.0
            cutoff = frappe.utils.add_days(frappe.utils.nowdate(), -int(float(discard_age)))
            shelf_rows = frappe.db.sql(
                """SELECT DISTINCT UPPER(si.bucket_id) AS b
                   FROM `tabShelf Item` si
                   JOIN `tabShelf` sh ON sh.name = si.parent
                   LEFT JOIN (
                       SELECT custom_bucket_id, MAX(posting_date) AS hd
                       FROM `tabStock Entry`
                       WHERE stock_entry_type = 'Harvesting'
                         AND custom_bucket_id IS NOT NULL AND custom_bucket_id != ''
                       GROUP BY custom_bucket_id
                   ) h ON UPPER(h.custom_bucket_id) = UPPER(si.bucket_id)
                   WHERE sh.farm = %s AND si.bucket_id IS NOT NULL AND si.bucket_id != ''
                     AND COALESCE(h.hd, DATE(si.date_added)) <= %s""",
                (farm, cutoff), as_dict=True,
            )
            on_shelf = {}
            s = 0
            while s < len(shelf_rows):
                b = shelf_rows[s].get("b")
                if b:
                    on_shelf[b] = 1
                s = s + 1

            # Allocated buckets must never be discarded — checked against the live
            # (cleared-nightly) Bucket Allocation Status. UPPER()-folded.
            alloc_rows = frappe.get_all(
                "Bucket Allocation Status",
                filters={},
                fields=["bucket_id"],
                limit_page_length=0,
            )
            allocated = {}
            k = 0
            while k < len(alloc_rows):
                bid = alloc_rows[k].get("bucket_id")
                if bid:
                    allocated[str(bid).upper()] = 1
                k = k + 1

            rows = frappe.get_all(
                "Discard Request Bucket",
                filters={"farm": farm, "parenttype": "Discard Request"},
                fields=["bucket_id", "shelf", "variety", "stem_qty", "age_days",
                        "is_shelved", "greenhouse", "stem_length", "parent"],
                limit_page_length=0,
            )
            # De-duplicate: a bucket can appear on several Approved requests (nightly
            # re-lists + manual). Keep only the row from the LATEST request (max
            # creation), keyed case-insensitively by bucket id.
            best = {}
            best_created = {}
            j = 0
            while j < len(rows):
                r = rows[j]
                bid = r.get("bucket_id")
                parent = r.get("parent")
                created = approved_names.get(parent) or ""
                up = str(bid).upper() if bid else ""
                # parent Approved (created truthy) AND currently on a shelf at this
                # farm AND not allocated.
                if bid and created and (up in on_shelf) and (up not in allocated):
                    key = str(bid).lower()
                    if (key not in best) or (created > best_created.get(key, "")):
                        best[key] = {
                            "bucket_id": bid,
                            "shelf": r.get("shelf"),
                            "variety": r.get("variety"),
                            "stem_qty": r.get("stem_qty"),
                            "age_days": r.get("age_days"),
                            "is_shelved": 1 if r.get("is_shelved") else 0,
                            "greenhouse": r.get("greenhouse"),
                            "stem_length": r.get("stem_length"),
                            "discard_request": parent,
                        }
                        best_created[key] = created
                j = j + 1
            buckets = list(best.values())
            frappe.response["message"] = {"status": "success", "farm": farm, "buckets": buckets, "count": len(buckets)}
    except Exception as e:
        frappe.response["message"] = {"status": "error", "message": str(e), "buckets": []}


@frappe.whitelist()
def getDispatchTrucks():
    # Frappe Server Script (Type: API), api_method = getDispatchTrucks
    # Truck list for the offline Bucket Requests "Load to truck" picker:
    # every Vehicle whose "Dispatch Truck?" (custom_dispatch_truck) is UNCHECKED (0).
    # frappe.get_all ignores user permissions, so any logged-in operator can read it.
    # Response: { "status": "success", "trucks": [{"name","license_plate"}], "count" }
    frappe.response["message"] = {"status": "error", "trucks": []}
    try:
        rows = frappe.get_all(
            "Vehicle",
            filters={"custom_dispatch_truck": 0},
            fields=["name", "license_plate"],
            order_by="name asc",
            limit_page_length=0,
        )
        trucks = []
        i = 0
        while i < len(rows):
            r = rows[i]
            trucks = trucks + [{"name": r.get("name"), "license_plate": r.get("license_plate")}]
            i = i + 1
        frappe.response["message"] = {"status": "success", "trucks": trucks, "count": len(trucks)}
    except Exception as e:
        frappe.response["message"] = {"status": "error", "message": str(e), "trucks": []}


@frappe.whitelist()
def getFarmPlannedTrips():
    # Frappe Server Script (Type: API), api_method = getFarmPlannedTrips
    # For the cold-store attendant on the Bucket Requests app: the upcoming planned
    # trips (Bucket Request Trip) that will collect buckets from THIS farm. For each
    # trip it returns the WHOLE collection route with a live loading status per stop,
    # so the attendant can see who is holding up the run — a farm whose trolleys are
    # not yet loaded will delay everyone after it. READ-ONLY and standalone: it does
    # NOT touch the production allocation/trolley scripts.
    # Payload: { "farm": "<farm>" }  (JSON body, like fetchAllocatedBuckets)
    payload = frappe.request.get_json() or {}
    farm_name = payload.get('farm') if payload else None
    if not farm_name:
        farm_name = frappe.form_dict.get('farm')

    if not farm_name:
        frappe.response["message"] = {"status": "error", "message": "farm is required", "data": []}
    else:
        like = "%" + farm_name + "%"
        # Trips (not yet dispatched) that include at least one order row from this farm.
        name_rows = frappe.db.sql("""
            SELECT DISTINCT t.name AS name
            FROM `tabBucket Request Trip` t
            INNER JOIN `tabBucket Request Trip Order` o ON o.parent = t.name
            WHERE t.status != 'Dispatched'
              AND ( o.farm = %(farm)s OR o.farm LIKE %(like)s OR %(farm)s LIKE CONCAT('%%', o.farm, '%%') )
        """, {"farm": farm_name, "like": like}, as_dict=True)
        trip_names = [r['name'] for r in name_rows]

        if not trip_names:
            frappe.response["message"] = {"status": "success", "data": [], "farm": farm_name}
        else:
            headers = frappe.get_all(
                "Bucket Request Trip",
                filters={"name": ["in", trip_names]},
                fields=["name", "vehicle", "trip_date", "status", "collection_order",
                        "total_buckets", "capacity_buckets"],
                order_by="trip_date asc, name asc",
                limit_page_length=0,
            )
            # ALL order rows for these trips (every farm on the route, not just this one).
            rows = frappe.get_all(
                "Bucket Request Trip Order",
                filters={"parent": ["in", trip_names]},
                fields=["parent", "order_pick_list", "order_name", "customer", "farm",
                        "varieties", "buckets", "stems"],
                limit_page_length=0,
            )

            # ── Live loading state per (OPL, farm-short) from Pick List Item flags ──
            # awaiting (on a shelf, not yet on a trolley) -> loaded (on a trolley) ->
            # in_transit (picked up by the truck) -> shelved (arrived at packhouse).
            opls = []
            seen_opl = {}
            i = 0
            while i < len(rows):
                op = rows[i].get('order_pick_list')
                if op and op not in seen_opl:
                    seen_opl[op] = 1
                    opls.append(op)
                i = i + 1

            portion_state = {}  # "opl||farmShort" -> {awaiting, loaded, transit, shelved, total}
            if opls:
                pli = frappe.get_all(
                    "Pick List Item",
                    filters={"parent": ["in", opls], "parenttype": "Order Pick List",
                             "custom_bucket": ["!=", ""]},
                    fields=["parent", "custom_bucket", "warehouse",
                            "custom_awaiting_transfer", "custom_loaded_in_trolley",
                            "custom_in_transit", "custom_shelved"],
                    limit_page_length=0,
                )
                seen_bkt = {}
                p = 0
                while p < len(pli):
                    it = pli[p]
                    wh = it.get('warehouse') or ''
                    farm_short = wh.split(' ')[0] if wh else ''
                    op = it.get('parent')
                    bkt = it.get('custom_bucket') or ''
                    dk = str(op) + '||' + str(bkt).lower()
                    if bkt and (dk in seen_bkt):
                        p = p + 1
                        continue
                    if bkt:
                        seen_bkt[dk] = 1
                    key = str(op) + '||' + farm_short
                    if key not in portion_state:
                        portion_state[key] = {"awaiting": 0, "loaded": 0, "transit": 0, "shelved": 0, "total": 0}
                    st = portion_state[key]
                    st["total"] = st["total"] + 1
                    if int(it.get('custom_shelved') or 0):
                        st["shelved"] = st["shelved"] + 1
                    elif int(it.get('custom_in_transit') or 0):
                        st["transit"] = st["transit"] + 1
                    elif int(it.get('custom_loaded_in_trolley') or 0):
                        st["loaded"] = st["loaded"] + 1
                    else:
                        st["awaiting"] = st["awaiting"] + 1
                    p = p + 1

            # Group order rows by trip.
            by_trip = {}
            i = 0
            while i < len(rows):
                r = rows[i]
                tn = r.get('parent')
                if tn not in by_trip:
                    by_trip[tn] = []
                by_trip[tn].append(r)
                i = i + 1

            data = []
            h = 0
            while h < len(headers):
                hd = headers[h]
                tn = hd['name']
                trip_rows = by_trip.get(tn) or []

                # Aggregate per farm on this trip: planned buckets + live state + this
                # farm's own order lines (for the Requests-tab mapping).
                farm_map = {}
                farm_order = []
                your_orders = []
                j = 0
                while j < len(trip_rows):
                    r = trip_rows[j]
                    f = r.get('farm') or '?'
                    if f not in farm_map:
                        farm_map[f] = {"planned": 0, "awaiting": 0, "loaded": 0,
                                       "transit": 0, "shelved": 0, "total": 0}
                        farm_order.append(f)
                    fm = farm_map[f]
                    pb = int(r.get('buckets') or 0)
                    fm["planned"] = fm["planned"] + pb
                    key = str(r.get('order_pick_list')) + '||' + f
                    ps = portion_state.get(key)
                    if ps:
                        fm["awaiting"] = fm["awaiting"] + ps["awaiting"]
                        fm["loaded"] = fm["loaded"] + ps["loaded"]
                        fm["transit"] = fm["transit"] + ps["transit"]
                        fm["shelved"] = fm["shelved"] + ps["shelved"]
                        fm["total"] = fm["total"] + ps["total"]
                    is_my_farm = (f == farm_name) or (f and f in farm_name) or (f and farm_name in f)
                    if is_my_farm:
                        your_orders.append({
                            "opl": r.get('order_pick_list') or '',
                            "order_name": r.get('order_name') or r.get('order_pick_list') or '',
                            "customer": r.get('customer') or '',
                            "varieties": r.get('varieties') or '',
                            "buckets": pb,
                        })
                    j = j + 1

                # Order the farms by the collection route, then any extras.
                seq_raw = (hd.get('collection_order') or '').split(',') if hd.get('collection_order') else []
                seq = []
                s = 0
                while s < len(seq_raw):
                    v = seq_raw[s].strip()
                    if v:
                        seq.append(v)
                    s = s + 1
                ordered = []
                s = 0
                while s < len(seq):
                    if seq[s] in farm_map:
                        ordered.append(seq[s])
                    s = s + 1
                s = 0
                while s < len(farm_order):
                    if farm_order[s] not in ordered:
                        ordered.append(farm_order[s])
                    s = s + 1

                # Build the stops with a live status label; flag the first stop that is
                # not yet ready/moving as the process bottleneck.
                stops = []
                bottleneck_found = 0
                trip_transit = 0
                your_stop = 0
                farm_buckets = 0
                s = 0
                while s < len(ordered):
                    f = ordered[s]
                    fm = farm_map[f]
                    total = fm["total"]
                    awaiting = fm["awaiting"]
                    loaded = fm["loaded"]
                    transit = fm["transit"]
                    shelved = fm["shelved"]
                    done_ish = loaded + transit + shelved

                    status = "waiting"
                    if total > 0 and shelved == total:
                        status = "done"
                    elif total > 0 and (transit + shelved) == total:
                        status = "transit"
                    elif awaiting == 0 and done_ish > 0 and total > 0:
                        status = "ready"
                    elif done_ish > 0:
                        status = "loading"
                    else:
                        status = "waiting"

                    delaying = 0
                    if bottleneck_found == 0 and (status == "waiting" or status == "loading"):
                        delaying = 1
                        bottleneck_found = 1
                    if transit > 0 or shelved > 0:
                        trip_transit = 1

                    is_you = 0
                    if (f == farm_name) or (f and f in farm_name) or (f and farm_name in f):
                        is_you = 1
                        your_stop = s + 1
                        farm_buckets = fm["planned"]

                    stops.append({
                        "farm": f, "stop": s + 1, "is_you": is_you,
                        "planned": fm["planned"], "total": total, "awaiting": awaiting,
                        "loaded": loaded, "transit": transit, "shelved": shelved,
                        "done_count": done_ish, "status": status, "delaying": delaying,
                    })
                    s = s + 1

                data.append({
                    "trip": tn,
                    "vehicle": hd.get('vehicle') or '',
                    "trip_date": str(hd.get('trip_date') or ''),
                    "status": hd.get('status') or 'Draft',
                    "capacity": int(hd.get('capacity_buckets') or 0),
                    "trip_buckets": int(hd.get('total_buckets') or 0),
                    "in_transit": trip_transit,
                    "farm_buckets": farm_buckets,
                    "your_stop": your_stop,
                    "total_stops": len(stops),
                    "stops": stops,
                    "orders": your_orders,
                })
                h = h + 1

            frappe.response["message"] = {"status": "success", "data": data, "farm": farm_name}


@frappe.whitelist()
def getInTransitBuckets():
    # Frappe Server Script (Type: API), api_method = getInTransitBuckets
    # Bucket Transfers board for the packhouse coldroom/shelving person. Shows every
    # bucket that has been transferred (has a transit truck) and is currently either
    # IN TRANSIT (incoming) or SHELVED (arrived), grouped by order, filtered by the
    # order's Sales Order delivery_date. Each bucket carries its shelved flag so the
    # UI can render a green "Shelved" / grey "Not shelved" pill; each group carries
    # shelved/total counts so the client can bucket it into the tabs:
    #   none shelved -> incoming/not-shelved ; some -> shelving in progress ; all -> ready to issue.
    # Payload: { "from_date": "YYYY-MM-DD", "to_date": "YYYY-MM-DD" } (default: tomorrow).
    # Response: { status, from_date, to_date, groups: [{ opl_name, order_name, customer,
    #             farm, truck, delivery_date, total, shelved_count,
    #             buckets: [{bucket_id, variety, stems, stem_length, shelf, shelved}] }], count }
    frappe.response["message"] = {"status": "error", "groups": []}
    try:
        data = frappe.request.get_json() or {}
        from_date = data.get("from_date")
        to_date = data.get("to_date")
        # Default window = tomorrow (the coldroom preps for the next day's dispatch).
        if not from_date:
            from_date = str(frappe.utils.add_days(frappe.utils.today(), 1))
        if not to_date:
            to_date = from_date

        rows = frappe.db.sql(
            """
            SELECT pli.parent AS opl_name, pli.bucket AS bucket_id,
                   pli.item_name AS variety, pli.item_code AS item_code,
                   pli.stem_length AS stem_length, pli.stock_qty AS stems,
                   pli.transit_truck AS truck, pli.shelf AS shelf,
                   pli.shelved AS shelved,
                   opl.order_name AS order_name, opl.customer AS customer,
                   opl.farm AS farm, opl.creation AS created,
                   so.delivery_date AS delivery_date
            FROM `tabPick List Item` pli
            INNER JOIN `tabOrder Pick List` opl
                ON pli.parent = opl.name AND pli.parenttype = 'Order Pick List'
            LEFT JOIN `tabSales Order` so ON so.name = opl.sales_order
            WHERE pli.transit_truck IS NOT NULL AND pli.transit_truck != ''
              AND (pli.in_transit = 1 OR pli.shelved = 1)
              AND so.delivery_date BETWEEN %(f)s AND %(t)s
            ORDER BY so.delivery_date ASC, opl.creation DESC, pli.bucket ASC
            """,
            {"f": from_date, "t": to_date}, as_dict=True,
        )

        order_map = {}
        order_list = []
        i = 0
        while i < len(rows):
            r = rows[i]
            opl = r.get("opl_name")
            if opl not in order_map:
                grp = {
                    "opl_name": opl,
                    "order_name": r.get("order_name") or opl,
                    "customer": r.get("customer"),
                    "farm": r.get("farm"),
                    "truck": r.get("truck") or "",
                    "delivery_date": str(r.get("delivery_date")) if r.get("delivery_date") else "",
                    "buckets": [],
                    "shelved_count": 0,
                }
                order_map[opl] = grp
                order_list = order_list + [grp]
            grp = order_map[opl]
            if not grp["truck"] and r.get("truck"):
                grp["truck"] = r.get("truck")
            if r.get("bucket_id"):
                is_shelved = 1 if r.get("shelved") else 0
                grp["buckets"] = grp["buckets"] + [{
                    "bucket_id": r.get("bucket_id"),
                    "variety": r.get("variety") or r.get("item_code"),
                    "stems": r.get("stems"),
                    "stem_length": r.get("stem_length"),
                    "shelf": r.get("shelf"),
                    "shelved": is_shelved,
                }]
                grp["shelved_count"] = grp["shelved_count"] + is_shelved
            i = i + 1

        groups = []
        j = 0
        while j < len(order_list):
            g = order_list[j]
            g["total"] = len(g["buckets"])
            groups = groups + [g]
            j = j + 1

        frappe.response["message"] = {
            "status": "success",
            "from_date": from_date,
            "to_date": to_date,
            "groups": groups,
            "count": len(groups),
        }
    except Exception as e:
        frappe.response["message"] = {"status": "error", "message": str(e), "groups": []}


@frappe.whitelist()
def getSavedTrolleys():
    try:
        farm = frappe.form_dict.get("farm")
        if not farm:
            frappe.throw("farm is required")

        # Today's allocations only — OPLs created today. A trolley saved against an
        # older (yesterday's) pick list must not linger in today's list.
        opl_today = frappe.get_all(
            "Order Pick List",
            filters={"creation": [">=", frappe.utils.nowdate()]},
            fields=["name"],
            pluck="name",
        )

        rows = frappe.get_all(
            "Pick List Item",
            filters={
                "warehouse": ["like", farm + "%"],
                "custom_loaded_in_trolley": 1,
                "custom_in_transit": 0,
                "custom_trolley_id": ["is", "set"],
                "parent": ["in", opl_today],
            },
            fields=[
                "parent as opl_name",
                "custom_trolley_id as trolley_id",
                "custom_bucket as bucket_id",
                "item_code",
                "item_name",
                "custom_shelf as shelf_location",
                "custom_stem_length as stem_length",
                "qty",
                "uom",
                "custom_truck as truck",
                "warehouse"
            ],
            order_by="custom_trolley_id asc, idx asc"
        )

        trolley_map = {}
        for row in rows:
            tid = row.get("trolley_id")
            if tid not in trolley_map:
                trolley_map[tid] = {
                    "trolley_id": tid,
                    "truck_id": "",
                    "buckets": []
                }
            trolley_map[tid]["buckets"].append({
                "opl_name": row.get("opl_name"),
                "bucket_id": row.get("bucket_id"),
                "item_code": row.get("item_code"),
                "item_name": row.get("item_name"),
                "shelf_location": row.get("shelf_location"),
                "stem_length": row.get("stem_length"),
                "qty": row.get("qty"),
                "uom": row.get("uom"),
                "truck": row.get("truck"),
                "warehouse": row.get("warehouse")
            })

        trolleys = list(trolley_map.values())

        frappe.response["message"] = {
            "status": "success",
            "data": trolleys
        }

    except Exception as e:
        frappe.log_error("getSavedTrolleys error", str(e))
        frappe.response["message"] = {
            "status": "error",
            "message": str(e)
        }


@frappe.whitelist()
def getShelfAvailability():
    varieties = frappe.form_dict.get('varieties')
    stem_length = frappe.form_dict.get('stem_length')

    if isinstance(varieties, str):
        try:
            varieties = json.loads(varieties)
        except Exception:
            varieties = [v.strip() for v in varieties.split(',') if v.strip()]

    if not varieties:
        frappe.response['availability'] = {}
    else:
        item_filters = {"variety": ["in", varieties]}
        if stem_length:
            item_filters["stem_length"] = stem_length

        rows = frappe.get_all("Shelf Item", filters=item_filters,
                              fields=["variety", "stem_qty", "parent"])
        parents = list({r.parent for r in rows if r.parent})
        shelf_farm = {}
        if parents:
            for s in frappe.get_all("Shelf", filters={"name": ["in", parents]}, fields=["name", "farm"]):
                shelf_farm[s.name] = s.farm or "Unknown"

        agg = {}
        for r in rows:
            farm = shelf_farm.get(r.parent) or "Unknown"
            agg.setdefault(r.variety, {})
            agg[r.variety][farm] = agg[r.variety].get(farm, 0) + (r.stem_qty or 0)

        frappe.response['availability'] = agg
        frappe.response['stem_length'] = stem_length


@frappe.whitelist()
def getShelvingDemand():
    # Frappe Server Script (Type: API), api_method = getShelvingDemand
    # Shelving target board: for TOMORROW's delivery date (what is processed today),
    # aggregate demand per variety from Sales Orders and compare to what is already
    # covered — reserved (Bucket Allocation Status) and physically on the shelf
    # (Shelf Item) — so the shelving person sees how much of each variety still
    # needs to be brought in, and can avoid over-shelving slow movers.
    #
    # Non-double-count rule: an allocated bucket is normally still on its shelf
    # (allocated stems are a subset of shelf stems), but once transferred it leaves
    # the shelf. So coverage per variety = MAX(on_shelf, allocated) — the greater of
    # what is physically present or what is reserved — which credits allocated stems
    # that already left the shelf without counting an allocated-still-on-shelf bucket
    # twice. target_to_shelve = max(0, demand - coverage).
    # Payload: optional { "date": "YYYY-MM-DD", "farm": "<Farm>" }.
    #   date  -> defaults to tomorrow.
    #   farm  -> when supplied, ON-SHELF and ALLOCATED are scoped to that farm's
    #            shelves (the station's farm), so the shelver sees THIS farm's
    #            coverage of tomorrow's demand. Demand stays the full tomorrow total
    #            (the target to fill). Blank farm = company-wide (all farms).
    frappe.response["message"] = {"status": "error", "rows": []}
    try:
        data = frappe.request.get_json() or {}
        target_date = data.get("date")
        if not target_date:
            target_date = str(frappe.utils.add_days(frappe.utils.today(), 1))
        farm = (data.get("farm") or "").strip()

        # Sales farms are the ones flagged sales_shelf=1 in Production Settings'
        # shelf_locations child table. The shelving guide is only relevant to a
        # sales farm (that is where allocation picks from). A non-sales farm gets a
        # clear "not applicable" so the app can hide the guide for it.
        sales_farms = {}
        ps = frappe.get_doc("Production Settings", "Production Settings")
        for loc in (ps.get("shelf_locations") or []):
            if loc.get("sales_shelf"):
                sales_farms[loc.get("farm")] = 1
        is_sales_farm = 1 if (farm and farm in sales_farms) else 0
        not_applicable = bool(farm) and not is_sales_farm

        # ── Demand: tomorrow's delivery, stems per variety (stock_qty = stems) ─────
        demand_rows = frappe.db.sql(
            """
            SELECT soi.item_code AS variety, COALESCE(SUM(soi.stock_qty), 0) AS stems
            FROM `tabSales Order Item` soi
            INNER JOIN `tabSales Order` so ON so.name = soi.parent
            WHERE so.docstatus = 1
              AND so.delivery_date = %(d)s
              AND so.company = 'Karen Roses'
              AND so.status NOT IN ('Cancelled', 'Closed')
            GROUP BY soi.item_code
            """,
            {"d": target_date}, as_dict=True,
        )

        # ── Allocated (reserved) stems per variety, from the live allocation table.
        #    Scoped to the station's farm (shelf_farm) when a farm is supplied. ─────
        alloc_params = {}
        alloc_farm_cond = ""
        if farm:
            alloc_farm_cond = " AND shelf_farm = %(farm)s"
            alloc_params["farm"] = farm
        alloc_rows = frappe.db.sql(
            """
            SELECT item_code AS variety, COALESCE(SUM(allocated_quantity), 0) AS stems
            FROM `tabBucket Allocation Status`
            WHERE item_code IS NOT NULL AND item_code != ''
            """ + alloc_farm_cond + """
            GROUP BY item_code
            """, alloc_params, as_dict=True,
        )

        # ── On-shelf stems per variety. Scoped to the station's farm when supplied,
        #    else all Karen Roses farms. ────────────────────────────────────────────
        shelf_params = {}
        shelf_farm_cond = ""
        if farm:
            shelf_farm_cond = " AND sh.farm = %(farm)s"
            shelf_params["farm"] = farm
        shelf_rows = frappe.db.sql(
            """
            SELECT si.variety AS variety, COALESCE(SUM(si.stem_qty), 0) AS stems,
                   COUNT(DISTINCT si.bucket_id) AS buckets
            FROM `tabShelf Item` si
            INNER JOIN `tabShelf` sh ON sh.name = si.parent
            INNER JOIN `tabFarm` f ON f.name = sh.farm
            WHERE si.variety IS NOT NULL AND si.variety != ''
              AND si.stem_qty > 0
              AND f.company = 'Karen Roses'
            """ + shelf_farm_cond + """
            GROUP BY si.variety
            """, shelf_params, as_dict=True,
        )

        demand = {}
        for r in demand_rows:
            demand[r["variety"]] = float(r["stems"] or 0)
        allocated = {}
        for r in alloc_rows:
            allocated[r["variety"]] = float(r["stems"] or 0)
        shelf = {}
        shelf_buckets = {}
        for r in shelf_rows:
            shelf[r["variety"]] = float(r["stems"] or 0)
            shelf_buckets[r["variety"]] = int(r["buckets"] or 0)

        # Union of varieties that have demand (the board is demand-driven; a variety
        # with stock but no demand for tomorrow is a slow mover -> target 0, shown so
        # the shelver can SEE it is over-stocked).
        varieties = {}
        for v in demand:
            varieties[v] = 1
        for v in allocated:
            varieties[v] = 1
        for v in shelf:
            varieties[v] = 1

        rows = []
        total_demand = 0.0
        total_target = 0.0
        for v in varieties:
            dem = demand.get(v, 0.0)
            alc = allocated.get(v, 0.0)
            shf = shelf.get(v, 0.0)
            coverage = shf if shf > alc else alc   # max(shelf, allocated)
            target = dem - coverage
            if target < 0:
                target = 0.0
            rows.append({
                "variety": v,
                "demand": dem,
                "allocated": alc,
                "on_shelf": shf,
                "shelf_buckets": shelf_buckets.get(v, 0),
                "coverage": coverage,
                "target_to_shelve": target,
            })
            total_demand = total_demand + dem
            total_target = total_target + target

        # Biggest shelving gaps first; ties broken by demand.
        rows = sorted(rows, key=lambda x: (x["target_to_shelve"], x["demand"]), reverse=True)

        if not_applicable:
            frappe.response["message"] = {
                "status": "not_applicable",
                "date": target_date,
                "farm": farm,
                "is_sales_farm": 0,
                "rows": [],
                "totals": {"demand": 0, "target_to_shelve": 0, "varieties": 0},
            }
        else:
            frappe.response["message"] = {
                "status": "success",
                "date": target_date,
                "farm": farm,
                "is_sales_farm": is_sales_farm,
                "rows": rows,
                "totals": {
                    "demand": total_demand,
                    "target_to_shelve": total_target,
                    "varieties": len(rows),
                },
            }
    except Exception as e:
        frappe.response["message"] = {"status": "error", "message": str(e), "rows": []}


@frappe.whitelist()
def getTraceability():
    # getTraceability — bucket/bunch journey for the mobile Traceability scan.
    #
    # Rewritten for the kaitet-group live schema, which differs from the schema the
    # previous version was authored against. On live, Stock Entry has NO
    # custom_harvest_batch_no / custom_farm / custom_received_bucket_id. Instead:
    #   - farm                     (Link)   — the source farm
    #   - custom_bucket_id         (Link)   — the bucket (single field, no "received" variant)
    #   - custom_receiving_batch_id(Data)   — an opaque receiving-RUN code, shared across
    #                                          many buckets, so it is NOT a per-session key
    #   - variety lives on the item rows (Stock Entry Detail.item_code), not the header
    #
    # Because a bucket is reused across many harvest sessions over time, we scope a
    # scan to the CURRENT session by anchoring on the most recent Stock Entry for the
    # bucket and clustering by date-gap: entries chained within GAP_DAYS of each other
    # form one session; a larger gap marks a boundary (prior reuse is excluded).

    WINDOW_DAYS = 5

    bucket_id = frappe.form_dict.get('bucket_id')
    bunch_id = frappe.form_dict.get('bunch_id')

    if not bucket_id and not bunch_id:
        frappe.response['data'] = {"error": "Missing bucket_id or bunch_id"}

    if bucket_id or bunch_id:
        try:
            kind = "bucket"
            scanned_bunch_id = bunch_id or ""
            anchor_grading_name = ""

            # ── Resolve bunch -> its grading SE -> bucket ──────────────────────────
            if bunch_id and not bucket_id:
                kind = "bunch"
                grading_for_bunch = frappe.get_all(
                    "Stock Entry",
                    filters={"custom_bunch_id": bunch_id, "stock_entry_type": "Grading",
                             "docstatus": ["<", 2]},
                    fields=["name", "custom_bucket_id"],
                    order_by="creation desc",
                    limit=1,
                )
                if grading_for_bunch:
                    bucket_id = grading_for_bunch[0].get("custom_bucket_id") or ""
                    anchor_grading_name = grading_for_bunch[0].get("name") or ""

            # ── All Stock Entries for this bucket (newest first) ───────────────────
            all_ses = []
            if bucket_id:
                all_ses = frappe.get_all(
                    "Stock Entry",
                    filters={"custom_bucket_id": bucket_id, "docstatus": ["<", 2]},
                    fields=["name", "owner", "stock_entry_type", "posting_date",
                            "posting_time", "creation", "farm", "custom_greenhouse",
                            "custom_stem_length", "custom_harvester", "custom_bunch_id",
                            "custom_graded_by", "custom_issued_to",
                            "custom_receiving_batch_id", "to_warehouse", "custom_cut_stage"],
                    order_by="posting_date desc, posting_time desc, creation desc",
                    limit=200,
                )

            def abs_gap_days(a, b):
                # date_diff needs a plain date; posting_date is already one, but be
                # defensive against timestamps by taking the date portion.
                ad = str(a or "").split(" ")[0]
                bd = str(b or "").split(" ")[0]
                if not ad or not bd:
                    return 999
                try:
                    d = frappe.utils.date_diff(ad, bd)
                    return d if d >= 0 else -d
                except Exception:
                    return 999

            # ── Item rows for every entry (variety + qty live on the rows, not the
            #    header) — one bulk query, used both to scope the session by variety
            #    and to total stems per stage. ───────────────────────────────────────
            all_names = [se.get("name") for se in all_ses if se.get("name")]
            parent_item = {}
            parent_qty = {}
            if all_names:
                sed_rows = frappe.get_all(
                    "Stock Entry Detail",
                    filters={"parent": ["in", all_names]},
                    fields=["parent", "item_code", "qty", "transfer_qty"],
                )
                for rr in sed_rows:
                    p = rr.get("parent") or ""
                    if not p:
                        continue
                    if p not in parent_item and rr.get("item_code"):
                        parent_item[p] = rr.get("item_code")
                    q = float(rr.get("transfer_qty") or rr.get("qty") or 0)
                    parent_qty[p] = parent_qty.get(p, 0.0) + q

            def variety_of(se):
                return parent_item.get(se.get("name") or "", "")

            # ── Scope to the current session by variety + a tight date window ──────
            # A bucket is reused across many sessions, and busy buckets are harvested
            # into daily, so a pure date-gap chain never breaks. The current session
            # is defined by the most recent Grading/Receiving (the current contents);
            # we keep entries of that same variety within WINDOW_DAYS of it.
            session_ses = []
            if all_ses:
                anchor = None
                if anchor_grading_name:
                    for se in all_ses:
                        if se.get("name") == anchor_grading_name:
                            anchor = se
                            break
                if anchor is None:
                    for se in all_ses:  # all_ses is posting_date desc
                        if se.get("stock_entry_type") in ("Grading", "Receiving", "Late Receipt"):
                            anchor = se
                            break
                if anchor is None:
                    anchor = all_ses[0]

                anchor_variety = variety_of(anchor)
                if not anchor_variety and scanned_bunch_id:
                    anchor_variety = ""  # fall back to no-variety filter below
                anchor_variety_lc = (anchor_variety or "").strip().lower()
                anchor_date = anchor.get("posting_date")

                for se in all_ses:
                    if abs_gap_days(anchor_date, se.get("posting_date")) > WINDOW_DAYS:
                        continue
                    sv = (variety_of(se) or "").strip().lower()
                    # Keep when variety matches the session, or when we have no
                    # session variety to match on, or the entry itself has none.
                    if anchor_variety_lc and sv and sv != anchor_variety_lc:
                        continue
                    session_ses.append(se)

            # Split by stage, each ordered oldest-first for display.
            def by_type(types):
                out = []
                for se in session_ses:
                    if se.get("stock_entry_type") in types:
                        out.append(se)
                out.reverse()  # session_ses is newest-first
                return out

            harvest_ses = by_type(("Harvesting",))
            grading_ses = by_type(("Grading",))
            receive_ses = by_type(("Receiving", "Late Receipt"))
            reject_ses = by_type(("Quarantine Rejects",))

            def stage_variety(se_list):
                for se in se_list:
                    v = parent_item.get(se.get("name") or "")
                    if v:
                        return v
                return ""

            def sum_stems(se_list):
                if not se_list:
                    return None
                total = 0.0
                for se in se_list:
                    total = total + parent_qty.get(se.get("name") or "", 0.0)
                return total if total else None

            harvest_qty = sum_stems(harvest_ses)
            grading_qty = sum_stems(grading_ses)
            receive_qty = sum_stems(receive_ses)

            # ── Shelf (live) / Shelving Log (durable, for issued buckets) ──────────
            shelf = None
            on_shelf_live = False
            shelf_from_log = False
            if bucket_id:
                try:
                    shelf_rows = frappe.get_all(
                        "Shelf Item",
                        filters={"bucket_id": bucket_id},
                        fields=["name", "owner", "parent", "variety", "stem_length",
                                "stem_qty", "greenhouse", "date_added"],
                        order_by="date_added desc",
                        limit=1,
                    )
                    if shelf_rows:
                        shelf = shelf_rows[0]
                        on_shelf_live = True
                        shelf_farm_rows = frappe.get_all(
                            "Shelf", filters={"name": shelf["parent"]},
                            fields=["farm"], limit=1)
                        shelf["shelf_farm"] = (shelf_farm_rows[0].get("farm") if shelf_farm_rows else "")
                except Exception:
                    shelf = None
                if not shelf:
                    try:
                        log_rows = frappe.get_all(
                            "Shelving Log",
                            filters={"bucket_id": bucket_id},
                            fields=["shelf", "farm", "variety", "stem_length", "stem_qty",
                                    "greenhouse", "shelved_on", "shelved_by"],
                            order_by="shelved_on desc",
                            limit=1,
                        )
                        if log_rows:
                            lr = log_rows[0]
                            shelf = {
                                "parent": lr.get("shelf"), "owner": lr.get("shelved_by"),
                                "variety": lr.get("variety"), "stem_length": lr.get("stem_length"),
                                "stem_qty": lr.get("stem_qty"), "greenhouse": lr.get("greenhouse"),
                                "date_added": lr.get("shelved_on"), "shelf_farm": lr.get("farm"),
                            }
                            shelf_from_log = True
                    except Exception:
                        shelf = None

            # ── Order Pick List allocation lines for this bucket ───────────────────
            session_start_date = ""
            if session_ses:
                session_start_date = str(session_ses[-1].get("posting_date") or "")
            opl_rows = []
            if bucket_id:
                try:
                    opl_filters = {"custom_bucket": bucket_id}
                    if session_start_date:
                        opl_filters["creation"] = [">=", session_start_date + " 00:00:00"]
                    opl_rows = frappe.get_all(
                        "Pick List Item",
                        filters=opl_filters,
                        fields=["name", "owner", "parent", "custom_issued", "stock_qty",
                                "item_code", "custom_stem_length", "creation"],
                        order_by="creation desc",
                        limit=50,
                    )
                except Exception:
                    opl_rows = []

            bunch_info = None
            has_data = harvest_ses or grading_ses or receive_ses or shelf or opl_rows
            if not has_data:
                frappe.response['data'] = {"error": "No records found for this bucket or bunch."}

            if has_data:
                rose_type = "Standards"
                if grading_ses or scanned_bunch_id:
                    rose_type = "Spray Roses"

                # Primary variety: prefer receiving, then grading, then harvest, then shelf.
                variety = (stage_variety(receive_ses) or stage_variety(grading_ses) or
                           stage_variety(harvest_ses) or (shelf or {}).get("variety") or "")

                # Resolve the bunch (scanned, or the first grading bunch in the session).
                resolved_bunch_id = scanned_bunch_id or ""
                if not resolved_bunch_id:
                    for se in grading_ses:
                        if se.get("custom_bunch_id"):
                            resolved_bunch_id = se.get("custom_bunch_id")
                            break
                if resolved_bunch_id:
                    try:
                        if frappe.db.exists("Bunch QR Code", resolved_bunch_id):
                            b = frappe.get_doc("Bunch QR Code", resolved_bunch_id)
                            bunch_info = {
                                "bunch_id": resolved_bunch_id,
                                "variety": b.get("item_code") or "",
                                "stem_length": b.get("stem_length") or "",
                                "bunch_size": str(b.get("bunch_size") or ""),
                                "farm": b.get("farm") or "",
                            }
                    except Exception:
                        bunch_info = None

                # Bulk-resolve grader full names.
                grading_owners = set()
                for g in grading_ses:
                    if g.get("owner"):
                        grading_owners.add(g.get("owner"))
                user_names = {}
                if grading_owners:
                    users = frappe.get_all("User", filters={"name": ["in", list(grading_owners)]},
                                           fields=["name", "full_name"])
                    for u in users:
                        user_names[u["name"]] = u.get("full_name") or u["name"]

                # Bulk-fetch Bunch QR Code rows for every session bunch.
                bunch_ids_needed = []
                for g in grading_ses:
                    bid = g.get("custom_bunch_id") or ""
                    if bid and bid not in bunch_ids_needed:
                        bunch_ids_needed.append(bid)
                bunch_qr_map = {}
                if bunch_ids_needed:
                    try:
                        qr_rows = frappe.get_all("Bunch QR Code",
                            filters={"name": ["in", bunch_ids_needed]},
                            fields=["name", "item_code", "stem_length", "bunch_size"])
                        for q in qr_rows:
                            bunch_qr_map[q["name"]] = q
                    except Exception:
                        bunch_qr_map = {}

                bunches = []
                for g in grading_ses:
                    bid = g.get("custom_bunch_id") or ""
                    if not bid:
                        continue
                    qr = bunch_qr_map.get(bid, {})
                    submitter_name = user_names.get(g.get("owner") or "", g.get("owner") or "")
                    bunches.append({
                        "bunch_id": bid,
                        "variety": qr.get("item_code") or "",
                        "stem_length": qr.get("stem_length") or "",
                        "bunch_size": str(qr.get("bunch_size") or ""),
                        "grading_se": g.get("name") or "",
                        "grading_stem_length": g.get("custom_stem_length") or "",
                        "graded_by": submitter_name,
                        "issued_opl": g.get("custom_issued_to") or "",
                    })

                # Quarantine rejects removed from this bucket in the session.
                quarantine_rejected_qty = sum_stems(reject_ses) or 0.0

                number_of_stems = receive_qty or grading_qty or harvest_qty
                if number_of_stems is not None and quarantine_rejected_qty:
                    net_stems = float(number_of_stems) - float(quarantine_rejected_qty)
                    number_of_stems = net_stems if net_stems > 0 else 0

                issued_count = 0
                for r in opl_rows:
                    if r.get("custom_issued") == 1:
                        issued_count = issued_count + 1
                on_opl = bool(opl_rows)
                on_shelf = on_shelf_live
                received = bool(receive_ses)
                graded = bool(grading_ses)

                partially_issued = issued_count > 0 and issued_count < len(opl_rows)
                fully_issued = issued_count > 0 and issued_count == len(opl_rows) and on_opl

                if fully_issued:
                    status = "Issued"
                elif on_opl and (partially_issued or issued_count == 0):
                    status = "Pending Issue"
                elif on_shelf:
                    status = "On Shelf"
                elif received:
                    status = "Received"
                elif graded:
                    status = "Graded"
                else:
                    status = "Harvested"

                h = harvest_ses[0] if harvest_ses else {}
                recv = receive_ses[-1] if receive_ses else {}

                stem_length = (
                    (shelf or {}).get("stem_length") or
                    recv.get("custom_stem_length") or
                    (grading_ses[0].get("custom_stem_length") if grading_ses else "") or
                    h.get("custom_stem_length") or ""
                )
                greenhouse = (
                    (shelf or {}).get("greenhouse") or
                    h.get("custom_greenhouse") or
                    recv.get("custom_greenhouse") or ""
                ).split(" - ")[0]
                farm = (
                    h.get("farm") or
                    recv.get("farm") or
                    (shelf or {}).get("shelf_farm") or
                    (bunch_info or {}).get("farm") or ""
                )

                all_dates = []
                for se in (harvest_ses + grading_ses + receive_ses):
                    if se.get("posting_date"):
                        all_dates.append(str(se["posting_date"]))
                if shelf and shelf.get("date_added"):
                    all_dates.append(str(shelf["date_added"]).split(" ")[0])
                all_dates.sort()
                latest_date = all_dates[-1] if all_dates else ""

                # Session batch code (opaque receiving-run id) for display only.
                batch_no = recv.get("custom_receiving_batch_id") or ""

                stages = []

                if harvest_ses:
                    h_first = harvest_ses[0]
                    h_last = harvest_ses[-1]
                    harvester = h_first.get("custom_harvester") or ""
                    multi_note = ""
                    if len(harvest_ses) > 1:
                        multi_note = " (" + str(len(harvest_ses)) + " entries)"
                    stages.append({
                        "stage": "Harvest",
                        "doc": h_last.get("name") or "",
                        "date": str(h_first.get("posting_date") or ""),
                        "datetime": str(h_first.get("posting_date") or "") + " " + str(h_first.get("posting_time") or ""),
                        "variety": stage_variety(harvest_ses) or variety,
                        "stem_length": h_first.get("custom_stem_length") or "",
                        "qty": harvest_qty,
                        "who": harvester,
                        "who_kind": "payroll" if harvester else "",
                        "user": h_first.get("owner") or "",
                        "detail": (h_first.get("custom_greenhouse") or "").split(" - ")[0] + multi_note,
                        "harvest_time": str(h_first.get("posting_time") or "").split(".")[0],
                        "cut_stage": h_first.get("custom_cut_stage") or "",
                    })

                if grading_ses:
                    g_first = grading_ses[0]
                    g_last = grading_ses[-1]
                    grading_submitters = []
                    for g in grading_ses:
                        nm = user_names.get(g.get("owner") or "", g.get("owner") or "")
                        if nm and nm not in grading_submitters:
                            grading_submitters.append(nm)
                    grader_label = ", ".join(grading_submitters)
                    stages.append({
                        "stage": "Grading",
                        "doc": g_last.get("name") or "",
                        "date": str(g_first.get("posting_date") or ""),
                        "datetime": str(g_first.get("posting_date") or "") + " " + str(g_first.get("posting_time") or ""),
                        "variety": stage_variety(grading_ses) or variety,
                        "stem_length": g_first.get("custom_stem_length") or "",
                        "qty": grading_qty,
                        "who": grader_label,
                        "who_kind": "user" if grader_label else "",
                        "user": g_first.get("owner") or "",
                        "detail": str(len(grading_ses)) + " bunches",
                    })

                if receive_ses:
                    r0 = receive_ses[-1]
                    stages.append({
                        "stage": "Receiving",
                        "doc": r0.get("name") or "",
                        "date": str(r0.get("posting_date") or ""),
                        "datetime": str(r0.get("posting_date") or "") + " " + str(r0.get("posting_time") or ""),
                        "receiving_time": str(r0.get("posting_time") or "").split(".")[0],
                        "variety": stage_variety(receive_ses) or variety,
                        "stem_length": r0.get("custom_stem_length") or "",
                        "qty": receive_qty,
                        "who": "",
                        "who_kind": "",
                        "user": r0.get("owner") or "",
                        "detail": (r0.get("to_warehouse") or "").split(" - ")[0] +
                                  (" (Late)" if r0.get("stock_entry_type") == "Late Receipt" else ""),
                    })

                if reject_ses and quarantine_rejected_qty:
                    rj_first = reject_ses[0]
                    rj_last = reject_ses[-1]
                    stages.append({
                        "stage": "Quarantine Rejects",
                        "doc": rj_last.get("name") or "",
                        "date": str(rj_first.get("posting_date") or ""),
                        "datetime": str(rj_first.get("posting_date") or "") + " " + str(rj_first.get("posting_time") or ""),
                        "variety": variety,
                        "stem_length": stem_length,
                        "qty": quarantine_rejected_qty,
                        "who": "",
                        "who_kind": "",
                        "user": rj_first.get("owner") or "",
                        "detail": str(int(quarantine_rejected_qty)) + " stems rejected from quarantine (removed from inventory)",
                    })

                if shelf:
                    stages.append({
                        "stage": "Shelving",
                        "doc": shelf.get("parent") or "",
                        "date": str(shelf.get("date_added") or "").split(" ")[0],
                        "datetime": str(shelf.get("date_added") or ""),
                        "shelving_time": (str(shelf.get("date_added") or "").split(" ")[1].split(".")[0]) if (" " in str(shelf.get("date_added") or "")) else "",
                        "variety": shelf.get("variety") or variety,
                        "stem_length": shelf.get("stem_length") or "",
                        "qty": float(shelf.get("stem_qty") or 0) or None,
                        "who": "",
                        "who_kind": "",
                        "user": shelf.get("owner") or "",
                        "detail": "Shelf " + (shelf.get("parent") or "") + (" (issued/cleared)" if shelf_from_log else ""),
                    })

                if on_opl:
                    opl_to_stems = {}
                    for r in opl_rows:
                        parent = r.get("parent") or ""
                        if not parent:
                            continue
                        opl_to_stems[parent] = opl_to_stems.get(parent, 0.0) + float(r.get("stock_qty") or 0)
                    opl_qty_total = sum(opl_to_stems.values())
                    opl_first = opl_rows[0]
                    if len(opl_to_stems) == 1:
                        only_opl = list(opl_to_stems.keys())[0]
                        opl_label = only_opl + " — " + str(int(opl_to_stems[only_opl])) + " stems"
                    else:
                        opl_label = str(len(opl_to_stems)) + " OPLs — " + str(int(opl_qty_total)) + " stems"
                    stages.append({
                        "stage": "Allocation",
                        "doc": opl_first.get("parent") or "",
                        "date": "",
                        "datetime": "",
                        "variety": opl_first.get("item_code") or variety,
                        "stem_length": "",
                        "qty": opl_qty_total if opl_qty_total else None,
                        "who": "",
                        "who_kind": "",
                        "user": opl_first.get("owner") or "",
                        "detail": opl_label,
                    })

                if issued_count > 0:
                    issued_qty_total = 0.0
                    issued_users = set()
                    issued_opls = set()
                    for r in opl_rows:
                        if r.get("custom_issued") == 1:
                            issued_qty_total = issued_qty_total + float(r.get("stock_qty") or 0)
                            if r.get("owner"):
                                issued_users.add(r.get("owner"))
                            if r.get("parent"):
                                issued_opls.add(r.get("parent"))
                    issued_label = (str(int(issued_qty_total)) + " stems issued (" +
                                    str(issued_count) + " of " + str(len(opl_rows)) + " lines)")
                    stages.append({
                        "stage": "Issued",
                        "doc": ", ".join(sorted(issued_opls)) if issued_opls else "",
                        "date": "",
                        "datetime": "",
                        "variety": variety,
                        "stem_length": "",
                        "qty": issued_qty_total if issued_qty_total else None,
                        "who": "",
                        "who_kind": "",
                        "user": ", ".join(sorted(issued_users)) if issued_users else "",
                        "detail": issued_label,
                    })

                # ── Warnings ───────────────────────────────────────────────────────
                warnings = []
                if quarantine_rejected_qty and quarantine_rejected_qty > 0:
                    warnings.append(
                        str(int(quarantine_rejected_qty)) +
                        " stems rejected from quarantine (Quarantine Rejects) -- removed from this bucket")

                variety_lc = (variety or "").strip().lower()
                wrong_bunch_varieties = set()
                for bnch in bunches:
                    bv = (bnch.get("variety") or "").strip().lower()
                    if bv and variety_lc and bv != variety_lc:
                        wrong_bunch_varieties.add(bnch.get("variety") or "")
                if wrong_bunch_varieties:
                    warnings.append(
                        "Bunches in this bucket claim variety " +
                        ", ".join(sorted(wrong_bunch_varieties)) +
                        " but the session is " + (variety or "?") +
                        " — physical bucket may contain mixed varieties")

                varieties_seen = []
                for st in stages:
                    v = (st.get("variety") or "").strip().lower()
                    if v and v not in varieties_seen:
                        varieties_seen.append(v)
                if len(varieties_seen) > 1:
                    warnings.append("Variety differs across stages: " + ", ".join(varieties_seen))

                lengths_seen = []
                for st in stages:
                    lv = (st.get("stem_length") or "").strip().lower()
                    if lv and lv not in lengths_seen:
                        lengths_seen.append(lv)
                if len(lengths_seen) > 1:
                    warnings.append("Stem length differs across stages: " + ", ".join(lengths_seen))

                # ── Bucket Allocation Status (canonical allocation totals) ──────────
                allocation = None
                if bucket_id:
                    try:
                        bas_rows = frappe.get_all(
                            "Bucket Allocation Status",
                            filters={"bucket_id": bucket_id},
                            fields=["name", "item_code", "stem_length", "shelf_location",
                                    "shelf_farm", "total_quantity", "allocated_quantity",
                                    "available_quantity", "harvest_date"],
                            order_by="modified desc",
                            limit=1,
                        )
                        if bas_rows:
                            ba = bas_rows[0]
                            total = float(ba.get("total_quantity") or 0)
                            alloc = float(ba.get("allocated_quantity") or 0)
                            avail = float(ba.get("available_quantity") or 0)
                            allocation = {
                                "exists": True,
                                "variety": ba.get("item_code") or "",
                                "stem_length": ba.get("stem_length") or "",
                                "shelf_location": ba.get("shelf_location") or "",
                                "shelf_farm": ba.get("shelf_farm") or "",
                                "total_quantity": total,
                                "allocated_quantity": alloc,
                                "available_quantity": avail,
                                "is_allocated": alloc > 0,
                                "fully_allocated": total > 0 and alloc >= total,
                                "harvest_date": str(ba.get("harvest_date") or ""),
                            }
                        else:
                            allocation = {
                                "exists": False, "is_allocated": False, "fully_allocated": False,
                                "total_quantity": 0, "allocated_quantity": 0, "available_quantity": 0,
                            }
                    except Exception:
                        allocation = None

                frappe.response['data'] = {
                    "kind": kind,
                    "rose_type": rose_type,
                    "bucket_id": bucket_id or "",
                    "bunch_id": resolved_bunch_id,
                    "status": status,
                    "variety": variety,
                    "farm": farm,
                    "greenhouse": greenhouse,
                    "stem_length": stem_length,
                    "number_of_stems": number_of_stems,
                    "stems_rejected": quarantine_rejected_qty or 0,
                    "date": latest_date,
                    "batch_no": batch_no,
                    "session_size": len(bunches),
                    "bunch_info": bunch_info,
                    "bunches": bunches,
                    "stages": stages,
                    "warnings": warnings,
                    "allocation": allocation,
                }

        except Exception as e:
            frappe.log_error("getTraceability error: " + str(e))
            frappe.response['data'] = {"error": str(e)}


@frappe.whitelist()
def gradingReplacementOptions():
    data = frappe.request.get_json()
    order_pick_list = (data.get("order_pick_list") or "").strip()
    variety = (data.get("variety") or "").strip()
    stem_length_in = (data.get("stem_length") or "").strip()
    bucket_in = (data.get("bucket_id") or "").strip()


    def farm_max_age(farm_name):
        try:
            ps = frappe.get_doc("Production Settings")
            for row in (ps.shelf_locations or []):
                if row.farm == farm_name and row.enabled:
                    return int(row.max_allocation_age or 5)
        except Exception:
            pass
        return 5


    def latest_harvest_dates(bucket_ids):
        if not bucket_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(bucket_ids))
        hd_rows = frappe.db.sql(
            "SELECT bucket_id, harvest_date FROM ("
            " SELECT custom_bucket_id AS bucket_id, posting_date AS harvest_date,"
            " ROW_NUMBER() OVER (PARTITION BY custom_bucket_id ORDER BY creation DESC) AS rn"
            " FROM `tabStock Entry`"
            " WHERE stock_entry_type = 'Harvesting' AND docstatus = 1"
            " AND custom_bucket_id IN (" + placeholders + ")"
            " ) ranked WHERE rn = 1",
            list(bucket_ids), as_dict=True,
        )
        result = {}
        for r in hd_rows:
            bid = r.get("bucket_id")
            if bid:
                result[str(bid).upper()] = r.get("harvest_date")
        return result


    def sort_fifo(rows):
        # Lambda is blocked in server scripts; sort by (harvest_date, date_added)
        # via an index-keyed tuple so rows never get compared directly.
        keyed = []
        pos = 0
        for fr in rows:
            keyed.append((str(fr.get("harvest_date")), str(fr.get("date_added")), pos, fr))
            pos = pos + 1
        keyed.sort()
        return [t[3] for t in keyed]


    # Permission gate
    has_role_rows = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "role": "Harvest Details Updater"},
        fields=["name"],
        limit=1,
    )
    is_admin = frappe.session.user == "Administrator"
    user_has_role = bool(has_role_rows) or is_admin

    if not user_has_role:
        frappe.response["http_status_code"] = 403
        frappe.response["data"] = {"error": "Role 'Harvest Details Updater' required."}

    if user_has_role:
        if not order_pick_list or not variety:
            frappe.response["http_status_code"] = 400
            frappe.response["data"] = {"error": "order_pick_list and variety are required."}

        if order_pick_list and variety:
            try:
                item_group = frappe.db.get_value("Item", variety, "item_group") or ""
                is_spray = (item_group == "Spray Roses")

                if not is_spray:
                    frappe.response["data"] = {
                        "supported": False,
                        "item_group": item_group,
                        "candidates": [],
                        "count": 0,
                        "message": "Replacement is only available for Spray Roses for now.",
                    }

                if is_spray:
                    pli_filters = {"parent": order_pick_list, "item_code": variety}
                    if bucket_in:
                        pli_filters["custom_bucket"] = bucket_in
                    pli_rows = frappe.get_all(
                        "Pick List Item",
                        filters=pli_filters,
                        fields=["name", "custom_stem_length", "custom_shelf",
                                "custom_bucket", "warehouse", "conversion_factor"],
                        order_by="idx asc",
                        limit=1,
                    )
                    if not pli_rows and bucket_in:
                        frappe.response["http_status_code"] = 404
                        frappe.response["data"] = {
                            "error": "Scanned bucket " + bucket_in + " isn't part of this order for " + variety + "."
                        }
                    elif not pli_rows:
                        frappe.response["http_status_code"] = 404
                        frappe.response["data"] = {
                            "error": "No pick list line for variety " + variety + " on " + order_pick_list + "."
                        }

                    if pli_rows:
                        pli = pli_rows[0]
                        pick_list_item = pli.get("name")
                        destination_bucket = pli.get("custom_bucket") or bucket_in or ""
                        stem_length = stem_length_in or (pli.get("custom_stem_length") or "")
                        shelf_name = pli.get("custom_shelf") or ""
                        conv = float(pli.get("conversion_factor") or 10) or 10.0

                        farm = ""
                        if shelf_name:
                            shelf_meta = frappe.get_all(
                                "Shelf", filters={"name": shelf_name}, fields=["farm"], limit=1
                            )
                            if shelf_meta:
                                farm = shelf_meta[0].get("farm") or ""
                        if not farm and destination_bucket:
                            hv = frappe.get_all(
                                "Stock Entry",
                                filters={"custom_bucket_id": destination_bucket,
                                         "stock_entry_type": "Harvesting"},
                                fields=["farm"],
                                order_by="posting_date desc, creation desc",
                                limit=1,
                            )
                            if hv:
                                farm = hv[0].get("farm") or ""

                        if not stem_length or not farm:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {
                                "error": "Cannot determine stem length / farm for this order line."
                            }

                        if stem_length and farm:
                            discard_age = frappe.db.get_single_value("Production Settings", "discard_age")
                            discard_age = float(discard_age) if discard_age else 5.0
                            max_age = farm_max_age(farm)
                            today = frappe.utils.nowdate()

                            params = [variety, stem_length, farm]
                            exclude_dest = ""
                            if destination_bucket:
                                exclude_dest = " AND si.bucket_id != %s"
                                params.append(destination_bucket)

                            base_rows = frappe.db.sql(
                                """
                                SELECT si.name AS shelf_item, si.parent AS shelf,
                                       si.bucket_id, si.variety, si.stem_length,
                                       si.stem_qty, si.date_added, si.warehouse, si.greenhouse,
                                       s.farm,
                                       COALESCE(bas.allocated_quantity, 0) AS allocated_qty,
                                       (COALESCE(si.stem_qty, 0) - COALESCE(bas.allocated_quantity, 0)) AS available_qty
                                FROM `tabShelf Item` si
                                INNER JOIN `tabShelf` s ON s.name = si.parent
                                LEFT JOIN `tabBucket Allocation Status` bas
                                   ON bas.bucket_id = si.bucket_id AND bas.item_code = si.variety
                                WHERE si.variety = %s
                                  AND si.stem_length = %s
                                  AND s.farm = %s""" + exclude_dest + """
                                  AND (bas.in_transit = 0 OR bas.in_transit IS NULL)
                                  AND (COALESCE(si.stem_qty, 0) - COALESCE(bas.allocated_quantity, 0)) > 0
                                  AND si.bucket_id NOT IN (
                                      SELECT drb.bucket_id
                                      FROM `tabDiscard Request Bucket` drb
                                      INNER JOIN `tabDiscard Request` dr ON dr.name = drb.parent
                                      WHERE dr.docstatus = 1 AND dr.workflow_state = 'Approved'
                                        AND drb.discarded = 0 AND drb.bucket_id IS NOT NULL
                                  )
                                LIMIT 500
                                """,
                                tuple(params),
                                as_dict=True,
                            )

                            bucket_ids = list({r.get("bucket_id") for r in base_rows if r.get("bucket_id")})
                            hd_map = latest_harvest_dates(bucket_ids)

                            fresh = []
                            for r in base_rows:
                                bid = r.get("bucket_id")
                                hd = hd_map.get(str(bid).upper()) if bid else None
                                if not hd:
                                    continue
                                age_days = frappe.utils.date_diff(today, str(hd))
                                if age_days >= discard_age or age_days > max_age:
                                    continue
                                r["harvest_date"] = hd
                                r["age_days"] = age_days
                                fresh.append(r)

                            fresh = sort_fifo(fresh)

                            candidates = []
                            for r in fresh:
                                candidates.append({
                                    "bucket_id": r.get("bucket_id") or "",
                                    "shelf": r.get("shelf") or "",
                                    "shelf_item": r.get("shelf_item") or "",
                                    "variety": r.get("variety") or "",
                                    "stem_length": r.get("stem_length") or "",
                                    "stem_qty": float(r.get("stem_qty") or 0) or None,
                                    "allocated_qty": float(r.get("allocated_qty") or 0),
                                    "available_qty": float(r.get("available_qty") or 0),
                                    "greenhouse": (r.get("greenhouse") or "").split(" - ")[0],
                                    "warehouse": r.get("warehouse") or "",
                                    "date_added": str(r.get("harvest_date")).split(" ")[0],
                                    "age_days": r.get("age_days"),
                                })

                            frappe.response["data"] = {
                                "supported": True,
                                "item_group": item_group,
                                "pick_list_item": pick_list_item,
                                "destination_bucket": destination_bucket,
                                "conversion_factor": int(conv),
                                "criteria": {
                                    "variety": variety,
                                    "stem_length": stem_length,
                                    "farm": farm,
                                },
                                "candidates": candidates,
                                "count": len(candidates),
                            }

            except Exception as e:
                try:
                    frappe.log_error(title="gradingReplacementOptions error", message=str(e))
                except Exception:
                    pass
                frappe.response["http_status_code"] = 500
                frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def listBucketOpls():
    data = frappe.request.get_json()
    bucket_id = (data.get("bucket_id") or "").strip()

    if not bucket_id:
        frappe.response["http_status_code"] = 400
        frappe.response["data"] = {"error": "bucket_id is required."}

    if bucket_id:
        try:
            # Pick List Items referencing this bucket
            pli_rows = frappe.get_all(
                "Pick List Item",
                filters={"custom_bucket": bucket_id},
                fields=[
                    "name", "parent", "item_code", "stock_qty", "qty",
                    "custom_issued", "custom_sale_order_item",
                    "custom_stem_length", "creation",
                ],
                order_by="creation desc",
                limit=50,
            )

            # Bulk-fetch parent OPL info for these PLIs
            opl_names = list({r["parent"] for r in pli_rows if r.get("parent")})
            opl_map = {}
            if opl_names:
                opl_rows = frappe.get_all(
                    "Order Pick List",
                    filters={"name": ["in", opl_names]},
                    fields=[
                        "name", "customer", "custom_order_name",
                        "date_created", "custom_total_stems",
                        "custom_status", "custom_team", "custom_business_unit",
                        "sales_order",
                    ],
                )
                for o in opl_rows:
                    opl_map[o["name"]] = o

            # Build response, grouped by Pick List Item (one row per PLI)
            opls = []
            for r in pli_rows:
                parent = r.get("parent") or ""
                o = opl_map.get(parent, {})
                stems = 0
                try:
                    stems = int(float(r.get("stock_qty") or 0))
                except Exception:
                    stems = 0
                bunches = 0
                try:
                    bunches = int(float(r.get("qty") or 0))
                except Exception:
                    bunches = 0
                opls.append({
                    "pick_list_item": r["name"],
                    "opl_name": parent,
                    "order_name": o.get("custom_order_name") or "",
                    "customer": o.get("customer") or "",
                    "team": o.get("custom_team") or "",
                    "date_created": str(o.get("date_created") or ""),
                    "total_stems": int(o.get("custom_total_stems") or 0) if o.get("custom_total_stems") else 0,
                    "opl_status": o.get("custom_status") or "",
                    "sales_order": o.get("sales_order") or "",
                    "sale_order_item": r.get("custom_sale_order_item") or "",
                    "item_code": r.get("item_code") or "",
                    "stem_length": r.get("custom_stem_length") or "",
                    "stems_from_this_bucket": stems,
                    "bunches_from_this_bucket": bunches,
                    "issued": r.get("custom_issued") == 1,
                    "creation": str(r.get("creation") or ""),
                })

            frappe.response["data"] = {
                "bucket_id": bucket_id,
                "opls": opls,
                "count": len(opls),
            }

        except Exception as e:
            frappe.log_error("listBucketOpls error: " + str(e))
            frappe.response["http_status_code"] = 500
            frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def listBunchDestinations():
    data = frappe.request.get_json()
    variety = (data.get("variety") or "").strip()
    stem_length = (data.get("stem_length") or "").strip()
    farm = (data.get("farm") or "").strip()
    exclude_bucket = (data.get("exclude_bucket") or "").strip()


    def farm_max_age(farm_name):
        try:
            ps = frappe.get_doc("Production Settings")
            for row in (ps.shelf_locations or []):
                if row.farm == farm_name and row.enabled:
                    return int(row.max_allocation_age or 5)
        except Exception:
            pass
        return 5


    def latest_harvest_dates(bucket_ids):
        if not bucket_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(bucket_ids))
        hd_rows = frappe.db.sql(
            "SELECT bucket_id, harvest_date FROM ("
            " SELECT custom_bucket_id AS bucket_id, posting_date AS harvest_date,"
            " ROW_NUMBER() OVER (PARTITION BY custom_bucket_id ORDER BY creation DESC) AS rn"
            " FROM `tabStock Entry`"
            " WHERE stock_entry_type = 'Harvesting' AND docstatus = 1"
            " AND custom_bucket_id IN (" + placeholders + ")"
            " ) ranked WHERE rn = 1",
            list(bucket_ids), as_dict=True,
        )
        result = {}
        for r in hd_rows:
            bid = r.get("bucket_id")
            if bid:
                result[str(bid).upper()] = r.get("harvest_date")
        return result


    def sort_fifo(rows):
        keyed = []
        pos = 0
        for fr in rows:
            keyed.append((str(fr.get("harvest_date")), str(fr.get("date_added")), pos, fr))
            pos = pos + 1
        keyed.sort()
        return [t[3] for t in keyed]


    # Permission gate
    has_role_rows = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "role": "Harvest Details Updater"},
        fields=["name"],
        limit=1,
    )
    is_admin = frappe.session.user == "Administrator"
    user_has_role = bool(has_role_rows) or is_admin

    if not user_has_role:
        frappe.response["http_status_code"] = 403
        frappe.response["data"] = {"error": "Role 'Harvest Details Updater' required."}

    if user_has_role:
        if not variety or not stem_length or not farm:
            frappe.response["http_status_code"] = 400
            frappe.response["data"] = {"error": "variety, stem_length and farm are required."}

        if variety and stem_length and farm:
            try:
                discard_age = frappe.db.get_single_value("Production Settings", "discard_age")
                discard_age = float(discard_age) if discard_age else 5.0
                max_age = farm_max_age(farm)
                today = frappe.utils.nowdate()

                params = [variety, stem_length, farm]
                extra = ""
                if exclude_bucket:
                    extra = " AND si.bucket_id != %s"
                    params.append(exclude_bucket)

                base_rows = frappe.db.sql(
                    """
                    SELECT si.name AS shelf_item, si.parent AS shelf,
                           si.bucket_id, si.variety, si.stem_length,
                           si.stem_qty, si.date_added, si.warehouse, si.greenhouse,
                           s.farm,
                           COALESCE(bas.allocated_quantity, 0) AS allocated_qty,
                           (COALESCE(si.stem_qty, 0) - COALESCE(bas.allocated_quantity, 0)) AS available_qty
                    FROM `tabShelf Item` si
                    INNER JOIN `tabShelf` s ON s.name = si.parent
                    LEFT JOIN `tabBucket Allocation Status` bas
                       ON bas.bucket_id = si.bucket_id AND bas.item_code = si.variety
                    WHERE si.variety = %s
                      AND si.stem_length = %s
                      AND s.farm = %s""" + extra + """
                      AND (bas.in_transit = 0 OR bas.in_transit IS NULL)
                      AND (COALESCE(si.stem_qty, 0) - COALESCE(bas.allocated_quantity, 0)) > 0
                      AND si.bucket_id NOT IN (
                          SELECT drb.bucket_id
                          FROM `tabDiscard Request Bucket` drb
                          INNER JOIN `tabDiscard Request` dr ON dr.name = drb.parent
                          WHERE dr.docstatus = 1 AND dr.workflow_state = 'Approved'
                            AND drb.discarded = 0 AND drb.bucket_id IS NOT NULL
                      )
                    LIMIT 500
                    """,
                    tuple(params),
                    as_dict=True,
                )

                bucket_ids = list({r.get("bucket_id") for r in base_rows if r.get("bucket_id")})
                hd_map = latest_harvest_dates(bucket_ids)

                fresh = []
                for r in base_rows:
                    bid = r.get("bucket_id")
                    hd = hd_map.get(str(bid).upper()) if bid else None
                    if not hd:
                        continue
                    age_days = frappe.utils.date_diff(today, str(hd))
                    if age_days >= discard_age or age_days > max_age:
                        continue
                    r["harvest_date"] = hd
                    r["age_days"] = age_days
                    fresh.append(r)

                fresh = sort_fifo(fresh)

                destinations = []
                for r in fresh:
                    destinations.append({
                        "bucket_id": r.get("bucket_id") or "",
                        "shelf": r.get("shelf") or "",
                        "shelf_item": r.get("shelf_item") or "",
                        "variety": r.get("variety") or "",
                        "stem_length": r.get("stem_length") or "",
                        "stem_qty": float(r.get("stem_qty") or 0) or None,
                        "allocated_qty": float(r.get("allocated_qty") or 0),
                        "available_qty": float(r.get("available_qty") or 0),
                        "greenhouse": (r.get("greenhouse") or "").split(" - ")[0],
                        "warehouse": r.get("warehouse") or "",
                        "date_added": str(r.get("harvest_date")).split(" ")[0],
                        "age_days": r.get("age_days"),
                    })

                frappe.response["data"] = {
                    "criteria": {
                        "variety": variety,
                        "stem_length": stem_length,
                        "farm": farm,
                    },
                    "destinations": destinations,
                    "count": len(destinations),
                }

            except Exception as e:
                try:
                    frappe.log_error(title="listBunchDestinations error", message=str(e))
                except Exception:
                    pass
                frappe.response["http_status_code"] = 500
                frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def listPendingReshelving():
    # No permission gate — viewing pending bunches is informational.
    # Action (reshelving) goes through moveBunch which IS gated.

    try:
        rows = frappe.get_all(
            "Stock Entry",
            filters={
                "custom_pending_reshelving": 1,
                "stock_entry_type": "Grading",
            },
            fields=[
                "name", "custom_bunch_id", "custom_pending_source_bucket",
                "custom_pending_since", "custom_stem_length",
                "custom_harvest_batch_no", "farm",
                "owner", "modified_by",
            ],
            order_by="custom_pending_since asc",
            limit_page_length=200,
        )

        # Resolve full names of who flagged each bunch
        user_keys = set()
        for r in rows:
            if r.get("modified_by"):
                user_keys.add(r["modified_by"])
        user_names = {}
        if user_keys:
            users = frappe.get_all(
                "User",
                filters={"name": ["in", list(user_keys)]},
                fields=["name", "full_name"],
            )
            for u in users:
                user_names[u["name"]] = u.get("full_name") or u["name"]

        # Fetch corrected variety from Bunch QR Code for each bunch
        bunch_ids = list({r["custom_bunch_id"] for r in rows if r.get("custom_bunch_id")})
        bunch_meta = {}
        if bunch_ids:
            b_rows = frappe.get_all(
                "Bunch QR Code",
                filters={"name": ["in", bunch_ids]},
                fields=["name", "item_code", "stem_length", "bunch_size", "farm"],
            )
            for b in b_rows:
                bunch_meta[b["name"]] = b

        pending = []
        for r in rows:
            bid = r.get("custom_bunch_id") or ""
            b = bunch_meta.get(bid, {})
            pending.append({
                "grading_se": r["name"],
                "bunch_id": bid,
                "source_bucket": r.get("custom_pending_source_bucket") or "",
                "pending_since": str(r.get("custom_pending_since") or ""),
                "variety": b.get("item_code") or "",
                "stem_length": b.get("stem_length") or r.get("custom_stem_length") or "",
                "bunch_size": str(b.get("bunch_size") or ""),
                "farm": b.get("farm") or r.get("farm") or "",
                "flagged_by": user_names.get(r.get("modified_by") or "",
                                             r.get("modified_by") or ""),
            })

        frappe.response["data"] = {
            "pending": pending,
            "count": len(pending),
        }

    except Exception as e:
        frappe.log_error("listPendingReshelving error: " + str(e))
        frappe.response["http_status_code"] = 500
        frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def listReplacementCandidates():
    data = frappe.request.get_json()
    bucket_id = (data.get("bucket_id") or "").strip()


    def farm_max_age(farm_name):
        try:
            ps = frappe.get_doc("Production Settings")
            for row in (ps.shelf_locations or []):
                if row.farm == farm_name and row.enabled:
                    return int(row.max_allocation_age or 5)
        except Exception:
            pass
        return 5


    def latest_harvest_dates(bucket_ids):
        if not bucket_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(bucket_ids))
        hd_rows = frappe.db.sql(
            "SELECT bucket_id, harvest_date FROM ("
            " SELECT custom_bucket_id AS bucket_id, posting_date AS harvest_date,"
            " ROW_NUMBER() OVER (PARTITION BY custom_bucket_id ORDER BY creation DESC) AS rn"
            " FROM `tabStock Entry`"
            " WHERE stock_entry_type = 'Harvesting' AND docstatus = 1"
            " AND custom_bucket_id IN (" + placeholders + ")"
            " ) ranked WHERE rn = 1",
            list(bucket_ids), as_dict=True,
        )
        result = {}
        for r in hd_rows:
            bid = r.get("bucket_id")
            if bid:
                result[str(bid).upper()] = r.get("harvest_date")
        return result


    def sort_fifo(rows):
        keyed = []
        pos = 0
        for fr in rows:
            keyed.append((str(fr.get("harvest_date")), str(fr.get("date_added")), pos, fr))
            pos = pos + 1
        keyed.sort()
        return [t[3] for t in keyed]


    # Permission check
    has_role_rows = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "role": "Harvest Details Updater"},
        fields=["name"],
        limit=1,
    )
    is_admin = frappe.session.user == "Administrator"
    user_has_role = bool(has_role_rows) or is_admin

    if not user_has_role:
        frappe.response["http_status_code"] = 403
        frappe.response["data"] = {"error": "Role 'Harvest Details Updater' required."}

    if user_has_role:
        if not bucket_id:
            frappe.response["http_status_code"] = 400
            frappe.response["data"] = {"error": "bucket_id is required."}

        if bucket_id:
            try:
                pli_rows = frappe.get_all(
                    "Pick List Item",
                    filters={"custom_bucket": bucket_id},
                    fields=["item_code", "custom_stem_length", "custom_shelf", "warehouse"],
                    order_by="creation desc",
                    limit=1,
                )

                variety = ""
                stem_length = ""
                shelf_name = ""
                if pli_rows:
                    variety = pli_rows[0].get("item_code") or ""
                    stem_length = pli_rows[0].get("custom_stem_length") or ""
                    shelf_name = pli_rows[0].get("custom_shelf") or ""

                farm = ""
                if shelf_name:
                    shelf_meta = frappe.get_all(
                        "Shelf",
                        filters={"name": shelf_name},
                        fields=["farm"],
                        limit=1,
                    )
                    if shelf_meta:
                        farm = shelf_meta[0].get("farm") or ""

                if not farm:
                    old_harvest_rows = frappe.get_all(
                        "Stock Entry",
                        filters={"custom_bucket_id": bucket_id, "stock_entry_type": "Harvesting"},
                        fields=["farm"],
                        order_by="posting_date desc, posting_time desc, creation desc",
                        limit=1,
                    )
                    if old_harvest_rows:
                        farm = old_harvest_rows[0].get("farm") or ""

                if not variety or not stem_length or not farm:
                    frappe.response["http_status_code"] = 400
                    frappe.response["data"] = {
                        "error": "Cannot determine variety/length/farm for bucket " + bucket_id
                    }

                if variety and stem_length and farm:
                    discard_age = frappe.db.get_single_value("Production Settings", "discard_age")
                    discard_age = float(discard_age) if discard_age else 5.0
                    max_age = farm_max_age(farm)
                    today = frappe.utils.nowdate()

                    base_rows = frappe.db.sql(
                        """
                        SELECT si.name AS shelf_item, si.parent AS shelf,
                               si.bucket_id, si.variety, si.stem_length,
                               si.stem_qty, si.date_added, si.warehouse, si.greenhouse,
                               s.farm,
                               COALESCE(bas.allocated_quantity, 0) AS allocated_qty,
                               (COALESCE(si.stem_qty, 0) - COALESCE(bas.allocated_quantity, 0)) AS available_qty
                        FROM `tabShelf Item` si
                        INNER JOIN `tabShelf` s ON s.name = si.parent
                        LEFT JOIN `tabBucket Allocation Status` bas
                           ON bas.bucket_id = si.bucket_id AND bas.item_code = si.variety
                        WHERE si.variety = %s
                          AND si.stem_length = %s
                          AND s.farm = %s
                          AND si.bucket_id != %s
                          AND (bas.in_transit = 0 OR bas.in_transit IS NULL)
                          AND (COALESCE(si.stem_qty, 0) - COALESCE(bas.allocated_quantity, 0)) > 0
                          AND si.bucket_id NOT IN (
                              SELECT drb.bucket_id
                              FROM `tabDiscard Request Bucket` drb
                              INNER JOIN `tabDiscard Request` dr ON dr.name = drb.parent
                              WHERE dr.docstatus = 1 AND dr.workflow_state = 'Approved'
                                AND drb.discarded = 0 AND drb.bucket_id IS NOT NULL
                          )
                        LIMIT 500
                        """,
                        (variety, stem_length, farm, bucket_id),
                        as_dict=True,
                    )

                    bucket_ids = list({r.get("bucket_id") for r in base_rows if r.get("bucket_id")})
                    hd_map = latest_harvest_dates(bucket_ids)

                    fresh = []
                    for r in base_rows:
                        bid = r.get("bucket_id")
                        hd = hd_map.get(str(bid).upper()) if bid else None
                        if not hd:
                            continue
                        age_days = frappe.utils.date_diff(today, str(hd))
                        if age_days >= discard_age or age_days > max_age:
                            continue
                        r["harvest_date"] = hd
                        r["age_days"] = age_days
                        fresh.append(r)

                    fresh = sort_fifo(fresh)

                    candidates = []
                    for r in fresh:
                        candidates.append({
                            "bucket_id": r.get("bucket_id") or "",
                            "shelf": r.get("shelf") or "",
                            "shelf_item": r.get("shelf_item") or "",
                            "variety": r.get("variety") or "",
                            "stem_length": r.get("stem_length") or "",
                            "stem_qty": float(r.get("stem_qty") or 0) or None,
                            "allocated_qty": float(r.get("allocated_qty") or 0),
                            "available_qty": float(r.get("available_qty") or 0),
                            "greenhouse": (r.get("greenhouse") or "").split(" - ")[0],
                            "warehouse": r.get("warehouse") or "",
                            "date_added": str(r.get("harvest_date")).split(" ")[0],
                            "age_days": r.get("age_days"),
                        })

                    frappe.response["data"] = {
                        "criteria": {
                            "variety": variety,
                            "stem_length": stem_length,
                            "farm": farm,
                        },
                        "candidates": candidates,
                        "count": len(candidates),
                    }

            except Exception as e:
                try:
                    frappe.log_error(title="listReplacementCandidates error", message=str(e))
                except Exception:
                    pass
                frappe.response["http_status_code"] = 500
                frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def loadTrolleyInTruck():
    try:
        data = frappe.form_dict.get("data")
        if not data:
            frappe.throw("No data provided")

        if isinstance(data, str):
            data = frappe.parse_json(data)

        trolley_id = data.get("trolley_id")
        custom_transit_truck = data.get("truck_id")

        if not trolley_id:
            frappe.throw("trolley_id is required")

        if not custom_transit_truck:
            frappe.throw("custom_transit_truck is required")

        rows = frappe.get_all(
            "Pick List Item",
            filters={
                "custom_trolley_id": trolley_id,
                "custom_loaded_in_trolley": 1,
                "custom_in_transit": 0
            },
            fields=["name", "parent", "custom_bucket"],
            order_by="parent asc"
        )

        if not rows:
            frappe.response["message"] = {
                "status": "error",
                "message": "No buckets found for trolley " + str(trolley_id)
            }
        else:
            opl_map = {}
            bucket_ids = []
            for row in rows:
                parent = row.get("parent")
                if parent not in opl_map:
                    opl_map[parent] = []
                opl_map[parent].append(row.get("name"))
                bid = row.get("custom_bucket")
                if bid and bid not in bucket_ids:
                    bucket_ids.append(bid)

            updated_count = 0
            for opl_name, child_names in opl_map.items():
                doc = frappe.get_doc("Order Pick List", opl_name)
                for loc_row in doc.locations:
                    if loc_row.name in child_names:
                        loc_row.custom_in_transit = 1
                        loc_row.custom_transit_truck=custom_transit_truck
                        loc_row.custom_shelf = ""
                        updated_count += 1
                doc.save(ignore_permissions=True)

            # Bulk-remove these buckets from their Shelf child tables — once loaded to
            # a truck they have physically left the remote shelf. One save per Shelf.
            shelf_removed_count = 0
            if bucket_ids:
                lower_ids = [b.lower() for b in bucket_ids]
                shelf_items = frappe.get_all(
                    "Shelf Item",
                    filters={"bucket_id": ["in", bucket_ids]},
                    fields=["parent"]
                )
                shelf_names = []
                for si in shelf_items:
                    if si.parent not in shelf_names:
                        shelf_names.append(si.parent)
                for shelf_name in shelf_names:
                    shelf_doc = frappe.get_doc("Shelf", shelf_name)
                    kept = [it for it in shelf_doc.items if (it.bucket_id or "").lower() not in lower_ids]
                    removed = len(shelf_doc.items) - len(kept)
                    if removed > 0:
                        shelf_doc.items = kept
                        shelf_doc.save(ignore_permissions=True)
                        shelf_removed_count += removed

            frappe.db.commit()

            frappe.response["message"] = {
                "status": "success",
                "message": str(updated_count) + " bucket(s) from trolley " + str(trolley_id) + " loaded to " + str(custom_transit_truck) + ". " + str(shelf_removed_count) + " shelf row(s) removed."
            }

    except Exception as e:
        frappe.log_error("loadTrolleyInTruck error", str(e))
        frappe.response["message"] = {
            "status": "error",
            "message": str(e)
        }


@frappe.whitelist()
def moveBunch():
    data = frappe.request.get_json()
    bunch_id = (data.get("bunch_id") or "").strip()
    source_bucket_id = (data.get("source_bucket_id") or "").strip()
    new_variety = (data.get("variety") or "").strip()
    new_stem_length = (data.get("stem_length") or "").strip()
    dest_bucket_id = (data.get("dest_bucket_id") or "").strip()

    # Permission gate
    has_role_rows = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "role": "Harvest Details Updater"},
        fields=["name"],
        limit=1,
    )
    is_admin = frappe.session.user == "Administrator"
    user_has_role = bool(has_role_rows) or is_admin

    if not user_has_role:
        frappe.response["http_status_code"] = 403
        frappe.response["data"] = {"error": "Role 'Harvest Details Updater' required."}

    if user_has_role:
        if not bunch_id or not source_bucket_id:
            frappe.response["http_status_code"] = 400
            frappe.response["data"] = {"error": "bunch_id and source_bucket_id are required."}

        if bunch_id and source_bucket_id:
            try:
                # 1. Find the bunch's grading SE (must be in the source bucket's session)
                source_prefix = source_bucket_id + "-"
                grading_rows = frappe.get_all(
                    "Stock Entry",
                    filters={
                        "custom_bunch_id": bunch_id,
                        "stock_entry_type": "Grading",
                        "custom_harvest_batch_no": ["like", source_prefix + "%"],
                    },
                    fields=["name", "custom_harvest_batch_no", "custom_stem_length",
                            "farm", "custom_pending_reshelving"],
                    order_by="creation desc",
                    limit=1,
                )
                if not grading_rows:
                    frappe.response["http_status_code"] = 404
                    frappe.response["data"] = {
                        "error": "No grading record for bunch " + bunch_id +
                                 " in bucket " + source_bucket_id
                    }

                if grading_rows:
                    grading = grading_rows[0]
                    grading_name = grading["name"]
                    source_batch = grading.get("custom_harvest_batch_no") or ""

                    # Paired harvest SE (for stem_length updates)
                    harvest_rows = frappe.get_all(
                        "Stock Entry",
                        filters={"custom_harvest_batch_no": source_batch,
                                 "stock_entry_type": "Harvesting"},
                        fields=["name"],
                        limit=1,
                    )
                    harvest_name = harvest_rows[0]["name"] if harvest_rows else ""

                    log = []

                    # 2. Apply corrections to Bunch QR Code + Grading SE + paired Harvest SE
                    if frappe.db.exists("Bunch QR Code", bunch_id):
                        bunch_doc = frappe.get_doc("Bunch QR Code", bunch_id)
                        changed = False
                        if new_variety and bunch_doc.item_code != new_variety:
                            bunch_doc.item_code = new_variety
                            changed = True
                        if new_stem_length and bunch_doc.stem_length != new_stem_length:
                            bunch_doc.stem_length = new_stem_length
                            changed = True
                        if changed:
                            bunch_doc.save(ignore_permissions=True)
                            log.append("Bunch QR Code " + bunch_id + " updated")

                    if new_stem_length and grading.get("custom_stem_length") != new_stem_length:
                        frappe.db.set_value("Stock Entry", grading_name,
                                            "custom_stem_length", new_stem_length)
                        log.append("Grading SE " + grading_name + " stem length updated")
                        if harvest_name:
                            frappe.db.set_value("Stock Entry", harvest_name,
                                                "custom_stem_length", new_stem_length)
                            log.append("Harvest SE " + harvest_name + " stem length updated")

                    # Propagate variety correction to the bunch's own Grading SE + paired
                    # Harvest SE detail rows. Each bunch in a sprays session has its own
                    # SE pair, so this only touches THIS bunch's records.
                    if new_variety:
                        for se_name in [grading_name] + ([harvest_name] if harvest_name else []):
                            detail_rows = frappe.get_all(
                                "Stock Entry Detail",
                                filters={"parent": se_name},
                                fields=["name", "item_code"],
                            )
                            for row in detail_rows:
                                if row.get("item_code") != new_variety:
                                    frappe.db.set_value("Stock Entry Detail", row["name"], {
                                        "item_code": new_variety,
                                        "item_name": new_variety,
                                        "description": new_variety,
                                    })
                            if detail_rows:
                                log.append("SE " + se_name + " items[].item_code → " + new_variety)

                    # 3. Decide what the bunch's "true" variety / length now is
                    final_variety = new_variety
                    final_length = new_stem_length
                    if not final_variety:
                        # No variety correction — use existing bunch QR's value
                        if frappe.db.exists("Bunch QR Code", bunch_id):
                            bq = frappe.get_doc("Bunch QR Code", bunch_id)
                            final_variety = bq.get("item_code") or ""
                    if not final_length:
                        if frappe.db.exists("Bunch QR Code", bunch_id):
                            bq = frappe.get_doc("Bunch QR Code", bunch_id)
                            final_length = bq.get("stem_length") or ""

                    # 4. Determine the source PLI + farm + bunch_size
                    source_pli_rows = frappe.get_all(
                        "Pick List Item",
                        filters={"custom_bucket": source_bucket_id},
                        fields=["name", "parent", "qty", "stock_qty",
                                "conversion_factor", "custom_shelf"],
                        order_by="creation desc",
                        limit=1,
                    )
                    source_pli = source_pli_rows[0] if source_pli_rows else None

                    # Farm of the source bucket — preferred from OPL's shelf, else from harvest SE
                    source_farm = ""
                    if source_pli and source_pli.get("custom_shelf"):
                        sh = frappe.get_all(
                            "Shelf",
                            filters={"name": source_pli["custom_shelf"]},
                            fields=["farm"],
                            limit=1,
                        )
                        if sh:
                            source_farm = sh[0].get("farm") or ""
                    if not source_farm:
                        source_farm = grading.get("farm") or ""

                    # Bunch size in stems — from Grading SE detail rows
                    bunch_size_stems = 10
                    grading_items = frappe.get_all(
                        "Stock Entry Detail",
                        filters={"parent": grading_name},
                        fields=["transfer_qty", "qty"],
                    )
                    ss = 0.0
                    for gi in grading_items:
                        ss = ss + float(gi.get("transfer_qty") or gi.get("qty") or 0)
                    if ss > 0:
                        bunch_size_stems = int(ss)

                    # 5. Validate / locate the destination bucket
                    dest_shelf_item = None
                    if dest_bucket_id:
                        matched = frappe.db.sql(
                            """
                            SELECT si.name AS shelf_item, si.parent AS shelf,
                                   si.bucket_id, si.variety, si.stem_length, si.stem_qty,
                                   s.farm
                            FROM `tabShelf Item` si
                            INNER JOIN `tabShelf` s ON s.name = si.parent
                            WHERE si.bucket_id = %s
                              AND si.variety = %s
                              AND si.stem_length = %s
                              AND s.farm = %s
                            LIMIT 1
                            """,
                            (dest_bucket_id, final_variety, final_length, source_farm),
                            as_dict=True,
                        )
                        if not matched:
                            frappe.response["http_status_code"] = 404
                            frappe.response["data"] = {
                                "error": "Destination " + dest_bucket_id +
                                         " is not a valid shelved bucket for " +
                                         final_variety + " / " + final_length +
                                         " from " + source_farm + "."
                            }
                        if matched:
                            dest_shelf_item = matched[0]

                    # 6. Either move the bunch OR flag pending reshelving
                    if dest_shelf_item is None and not dest_bucket_id:
                        # No destination requested → flag pending
                        now_ts = frappe.utils.now()
                        frappe.db.set_value("Stock Entry", grading_name, {
                            "custom_pending_reshelving": 1,
                            "custom_pending_since": now_ts,
                            "custom_pending_source_bucket": source_bucket_id,
                        })
                        log.append("Bunch flagged pending reshelving")

                        # Reduce source PLI by one bunch
                        if source_pli:
                            new_qty = max(0, (source_pli.get("qty") or 0) - 1)
                            new_stock_qty = max(0, (source_pli.get("stock_qty") or 0) - bunch_size_stems)
                            if new_qty <= 0 and new_stock_qty <= 0:
                                # Delete empty PLI
                                frappe.delete_doc("Pick List Item", source_pli["name"],
                                                  force=1, ignore_permissions=True)
                                log.append("Empty PLI deleted from " + (source_pli.get("parent") or ""))
                            else:
                                frappe.db.set_value("Pick List Item", source_pli["name"], {
                                    "qty": new_qty,
                                    "stock_qty": new_stock_qty,
                                })
                                log.append("Source PLI reduced to " + str(new_qty) + " bunches")

                        # Clear the bunch's issued_to (it's no longer with the order)
                        frappe.db.set_value("Stock Entry", grading_name,
                                            "custom_issued_to", None)

                        # Decrement source bucket's Shelf Item if still shelved — the
                        # bunch physically left the bucket to wait on the packhouse floor.
                        src_shelf_rows = frappe.get_all(
                            "Shelf Item",
                            filters={"bucket_id": source_bucket_id},
                            fields=["name", "parent", "stem_qty"],
                            order_by="date_added desc",
                            limit=1,
                        )
                        if src_shelf_rows:
                            src_si = src_shelf_rows[0]
                            src_current = 0.0
                            try:
                                src_current = float(src_si.get("stem_qty") or 0)
                            except Exception:
                                src_current = 0.0
                            src_new = max(0, src_current - bunch_size_stems)
                            frappe.db.set_value("Shelf Item", src_si["name"],
                                                "stem_qty", src_new)
                            if src_si.get("parent"):
                                frappe.db.set_value("Shelf", src_si["parent"],
                                                    "modified", now_ts)
                            log.append("Source shelf item " + str(src_si["name"]) +
                                       " stem_qty " + str(int(src_current)) + " → " + str(int(src_new)))

                        frappe.db.commit()

                        frappe.response["data"] = {
                            "status": "pending",
                            "message": "Bunch " + bunch_id +
                                       " moved to pending reshelving. Awaiting a matching bucket.",
                            "bunch_id": bunch_id,
                            "source_bucket": source_bucket_id,
                            "corrected_variety": final_variety,
                            "corrected_stem_length": final_length,
                            "updates": log,
                        }

                    elif dest_shelf_item is not None:
                        # Execute the move
                        now_ts = frappe.utils.now()

                        # a) Clear state-only fields on Grading SE — do NOT rewrite
                        #    custom_bucket_id (would falsify "where this bunch was graded").
                        #    Variety/length already updated above on items[] + custom_stem_length.
                        frappe.db.set_value("Stock Entry", grading_name, {
                            "custom_pending_reshelving": 0,
                            "custom_pending_since": None,
                            "custom_pending_source_bucket": None,
                            "custom_issued_to": None,
                        })

                        # b) Reduce source PLI (or delete if empty)
                        if source_pli:
                            new_qty = max(0, (source_pli.get("qty") or 0) - 1)
                            new_stock_qty = max(0, (source_pli.get("stock_qty") or 0) - bunch_size_stems)
                            if new_qty <= 0 and new_stock_qty <= 0:
                                frappe.delete_doc("Pick List Item", source_pli["name"],
                                                  force=1, ignore_permissions=True)
                                log.append("Empty PLI deleted from " + (source_pli.get("parent") or ""))
                            else:
                                frappe.db.set_value("Pick List Item", source_pli["name"], {
                                    "qty": new_qty,
                                    "stock_qty": new_stock_qty,
                                })
                                log.append("Source PLI reduced to " + str(new_qty) + " bunches")
                            # Touch parent OPL
                            if source_pli.get("parent"):
                                frappe.db.set_value("Order Pick List",
                                                    source_pli["parent"], "modified", now_ts)

                        # c) Decrement source bucket's Shelf Item (if still on a shelf).
                        #    The bunch physically left, so the source shelf shouldn't
                        #    keep counting these stems.
                        src_shelf_rows = frappe.get_all(
                            "Shelf Item",
                            filters={"bucket_id": source_bucket_id},
                            fields=["name", "parent", "stem_qty"],
                            order_by="date_added desc",
                            limit=1,
                        )
                        if src_shelf_rows:
                            src_si = src_shelf_rows[0]
                            src_current = 0.0
                            try:
                                src_current = float(src_si.get("stem_qty") or 0)
                            except Exception:
                                src_current = 0.0
                            src_new = max(0, src_current - bunch_size_stems)
                            frappe.db.set_value("Shelf Item", src_si["name"],
                                                "stem_qty", src_new)
                            if src_si.get("parent"):
                                frappe.db.set_value("Shelf", src_si["parent"],
                                                    "modified", now_ts)
                            log.append("Source shelf item " + str(src_si["name"]) +
                                       " stem_qty " + str(int(src_current)) + " → " + str(int(src_new)))

                        # d) Increase destination Shelf Item stems
                        current = dest_shelf_item.get("stem_qty") or 0
                        try:
                            current = float(current)
                        except Exception:
                            current = 0.0
                        new_stem_qty = current + bunch_size_stems
                        frappe.db.set_value("Shelf Item", dest_shelf_item["shelf_item"],
                                            "stem_qty", new_stem_qty)
                        frappe.db.set_value("Shelf", dest_shelf_item["shelf"],
                                            "modified", now_ts)
                        log.append("Destination shelf item " + str(dest_shelf_item["shelf_item"]) +
                                   " stem_qty " + str(int(current)) + " → " + str(int(new_stem_qty)))

                        frappe.db.commit()

                        frappe.response["data"] = {
                            "status": "moved",
                            "message": "Bunch " + bunch_id +
                                       " moved from " + source_bucket_id + " to " + dest_bucket_id + ".",
                            "bunch_id": bunch_id,
                            "source_bucket": source_bucket_id,
                            "dest_bucket": dest_bucket_id,
                            "dest_shelf": dest_shelf_item.get("shelf") or "",
                            "stems_moved": bunch_size_stems,
                            "corrected_variety": final_variety,
                            "corrected_stem_length": final_length,
                            "updates": log,
                        }

            except Exception as e:
                frappe.db.rollback()
                frappe.log_error("moveBunch error: " + str(e))
                frappe.response["http_status_code"] = 500
                frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def releaseFromQuarantine():
    data = frappe.request.get_json()

    bucket_id = data.get("bucket_id", "")
    action = data.get("action", "")
    stems_to_release = data.get("stems_to_release")
    batch_no = data.get("batch_no", "")

    if not bucket_id:
        frappe.throw("bucket_id is required")

    if not batch_no:
        frappe.throw("batch_no is required")

    if action not in ("accept", "reject"):
        frappe.throw("action must be 'accept' or 'reject'")

    ACCEPTED_STORAGE = "Kapkolia Receiving Storage - KR"

    bucket_id_lower = bucket_id.strip().lower()
    bucket_id_upper = bucket_id.strip().upper()

    CONTINUITY_FIELDS = ["custom_greenhouse", "custom_harvester"]


    def build_movement(stock_entry_type, purpose, s_warehouse, t_warehouse, qty, entry):
        item = {
            "item_code": entry.item_code or "",
            "item_name": entry.item_name or "",
            "qty": qty,
            "transfer_qty": qty,
            "uom": "Stems",
            "stock_uom": "Stems",
            "conversion_factor": 1.0,
            "s_warehouse": s_warehouse,
            "cost_center": entry.cost_center or "",
            "basic_rate": entry.basic_rate or 0,
            "basic_amount": qty * (entry.basic_rate or 0),
            "allow_zero_valuation_rate": 1,
        }
        if t_warehouse:
            item["t_warehouse"] = t_warehouse

        se_data = {
            "doctype": "Stock Entry",
            "stock_entry_type": stock_entry_type,
            "purpose": purpose,
            "custom_bucket_id": bucket_id_lower,
            "custom_harvest_batch_no": entry.custom_harvest_batch_no or "",
            "custom_receiving_batch_id": batch_no,
            "company": entry.company or "",
            "posting_date": frappe.utils.nowdate(),
            "posting_time": frappe.utils.nowtime(),
            "set_posting_time": 1,
            "items": [item],
        }
        for field in CONTINUITY_FIELDS:
            if entry.get(field):
                se_data[field] = entry.get(field)

        se_doc = frappe.get_doc(se_data)
        # Skip link checks so a missing continuity master (e.g. Harvester) never
        # aborts the quarantine release; mirrors the batch-quality stock moves.
        se_doc.flags.ignore_links = True
        se_doc.insert(ignore_permissions=True)
        se_doc.submit()
        return se_doc


    # Step 1: all submitted stock entries for this bucket + batch (one indexed query).
    all_entries = frappe.db.sql("""
        SELECT se.name, se.stock_entry_type,
               sei.qty, sei.item_code, sei.item_name,
               sei.s_warehouse, sei.t_warehouse,
               sei.cost_center, sei.basic_rate,
               se.company, se.custom_receiving_batch_id,
               se.custom_greenhouse, se.custom_harvester,
               se.creation
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sei ON sei.parent = se.name
        WHERE se.custom_bucket_id = %s
            AND se.custom_receiving_batch_id = %s
            AND se.docstatus = 1
        ORDER BY se.creation DESC
    """, (bucket_id_lower, batch_no), as_dict=1)

    # Step 2: find the entry that put stock into a quarantine warehouse for this batch.
    quarantine_entry = None

    # Strategy A: any entry whose t_warehouse is a quarantine warehouse.
    for e in all_entries:
        t_wh = (e.t_warehouse or "").lower()
        if "quarantine" in t_wh and e.custom_receiving_batch_id == batch_no:
            quarantine_entry = e
            break

    # Strategy B: specific quarantine stock entry types.
    if not quarantine_entry:
        for e in all_entries:
            if e.stock_entry_type in ("Receiving Quarantined", "Quarantine Transfer") and e.custom_receiving_batch_id == batch_no:
                quarantine_entry = e
                break

    if not quarantine_entry:
        frappe.throw("No quarantined entry found for bucket %s in batch %s" % (bucket_id_upper, batch_no))

    entry = quarantine_entry
    quarantine_wh = entry.t_warehouse or ""
    original_wh = entry.s_warehouse or ""

    # Step 3: available = NET stems currently in quarantine for this bucket
    # (moved in minus already moved back out via accept/reject). This accounts
    # for partial and repeated releases, and naturally prevents over-release
    # without a separate double-release guard.
    net_row = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN sei.t_warehouse LIKE '%%Quarantin%%' THEN sei.qty ELSE 0 END)
          - SUM(CASE WHEN sei.s_warehouse LIKE '%%Quarantin%%' THEN sei.qty ELSE 0 END) AS net
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sei ON sei.parent = se.name
        WHERE se.custom_bucket_id = %s
          AND se.custom_receiving_batch_id = %s
          AND se.docstatus = 1
    """, (bucket_id_lower, batch_no), as_dict=1)
    available_stems = int(net_row[0].net) if (net_row and net_row[0].net is not None) else 0

    if available_stems <= 0:
        frappe.throw("Bucket %s (Batch: %s) has no stems remaining in quarantine." % (bucket_id_upper, batch_no))

    # Step 4: how many stems to release.
    stems_count = available_stems
    if stems_to_release:
        stems_count = int(stems_to_release)

    if stems_count > available_stems:
        frappe.throw("Only %s stems available in quarantine for bucket %s (Batch: %s)" % (available_stems, bucket_id_upper, batch_no))

    # Step 5: split the bucket and fully resolve it in one submit.
    # The entered stems count applies to the chosen action; the remainder gets the
    # opposite treatment so the whole bucket leaves quarantine:
    #   accept N -> Material Transfer(N) back to storage + Quarantine Rejects(rest)
    #   reject N -> Quarantine Rejects(N) removed from stock + Material Transfer(rest)
    target_warehouse = original_wh if original_wh else ACCEPTED_STORAGE
    # Rejected stems are moved into the company's Rejects warehouse (tracked),
    # not written off. Resolve it dynamically, falling back to the KR default.
    rejects_warehouse = frappe.db.get_value(
        "Warehouse",
        {"company": entry.company, "disabled": 0, "warehouse_name": ["like", "%Reject%"]},
        "name",
    ) or "Rejects - KR"

    if action == "accept":
        accepted_qty = stems_count
        rejected_qty = available_stems - stems_count
    else:  # reject
        rejected_qty = stems_count
        accepted_qty = available_stems - stems_count

    accepted_entry = None
    rejected_entry = None

    # Issue the rejected stems out of inventory first (frees quarantine stock),
    # then move the accepted stems back to storage.
    if rejected_qty > 0:
        rejected_entry = build_movement("Quarantine Rejects", "Material Transfer", quarantine_wh, rejects_warehouse, rejected_qty, entry)
    if accepted_qty > 0:
        accepted_entry = build_movement("Quarantine Accept", "Material Transfer", quarantine_wh, target_warehouse, accepted_qty, entry)

    frappe.db.commit()

    stock_entries = [dd.name for dd in (accepted_entry, rejected_entry) if dd]

    msg_parts = []
    if accepted_qty > 0:
        msg_parts.append("%s accepted -> %s" % (accepted_qty, target_warehouse))
    if rejected_qty > 0:
        msg_parts.append("%s rejected -> %s" % (rejected_qty, rejects_warehouse))
    detail = "; ".join(msg_parts) if msg_parts else "no stems processed"

    frappe.response["status"] = "success"
    frappe.response["message"] = "Bucket %s (Batch: %s): %s." % (bucket_id_upper, batch_no, detail)
    frappe.response["stock_entries"] = stock_entries
    frappe.response["stock_entry"] = stock_entries[0] if stock_entries else None
    frappe.response["accepted_stems"] = accepted_qty
    frappe.response["rejected_stems"] = rejected_qty
    frappe.response["stems_released"] = accepted_qty
    frappe.response["remaining_in_quarantine"] = available_stems - accepted_qty - rejected_qty
    frappe.response["target_warehouse"] = target_warehouse
    frappe.response["rejects_warehouse"] = rejects_warehouse


def _resolve_bucket_quarantine(bucket_id, batch_no, action, stems_to_release):
    """Fully resolve ONE bucket out of quarantine in a single submit.

    Mirrors releaseFromQuarantine's per-bucket logic, but raises ValueError on a
    bad/empty bucket and does NOT commit, so the batch endpoint can process many
    buckets and commit once. The whole bucket always leaves quarantine:
        accept N -> Quarantine Accept(N) to storage + Quarantine Rejects(rest)
        reject N -> Quarantine Rejects(N) to rejects WH + Quarantine Accept(rest)
    """
    ACCEPTED_STORAGE = "Kapkolia Receiving Storage - KR"
    CONTINUITY_FIELDS = ["custom_greenhouse", "custom_harvester"]

    bucket_id_lower = bucket_id.strip().lower()
    bucket_id_upper = bucket_id.strip().upper()

    def build_movement(stock_entry_type, purpose, s_warehouse, t_warehouse, qty, entry):
        item = {
            "item_code": entry.item_code or "",
            "item_name": entry.item_name or "",
            "qty": qty,
            "transfer_qty": qty,
            "uom": "Stems",
            "stock_uom": "Stems",
            "conversion_factor": 1.0,
            "s_warehouse": s_warehouse,
            "cost_center": entry.cost_center or "",
            "basic_rate": entry.basic_rate or 0,
            "basic_amount": qty * (entry.basic_rate or 0),
            "allow_zero_valuation_rate": 1,
        }
        if t_warehouse:
            item["t_warehouse"] = t_warehouse

        se_data = {
            "doctype": "Stock Entry",
            "stock_entry_type": stock_entry_type,
            "purpose": purpose,
            "custom_bucket_id": bucket_id_lower,
            "custom_harvest_batch_no": entry.custom_harvest_batch_no or "",
            "custom_receiving_batch_id": batch_no,
            "company": entry.company or "",
            "posting_date": frappe.utils.nowdate(),
            "posting_time": frappe.utils.nowtime(),
            "set_posting_time": 1,
            "items": [item],
        }
        for field in CONTINUITY_FIELDS:
            if entry.get(field):
                se_data[field] = entry.get(field)

        se_doc = frappe.get_doc(se_data)
        se_doc.flags.ignore_links = True
        se_doc.insert(ignore_permissions=True)
        se_doc.submit()
        return se_doc

    all_entries = frappe.db.sql("""
        SELECT se.name, se.stock_entry_type,
               sei.qty, sei.item_code, sei.item_name,
               sei.s_warehouse, sei.t_warehouse,
               sei.cost_center, sei.basic_rate,
               se.company, se.custom_receiving_batch_id,
               se.custom_greenhouse, se.custom_harvester,
               se.creation
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sei ON sei.parent = se.name
        WHERE se.custom_bucket_id = %s
            AND se.custom_receiving_batch_id = %s
            AND se.docstatus = 1
        ORDER BY se.creation DESC
    """, (bucket_id_lower, batch_no), as_dict=1)

    quarantine_entry = None
    for e in all_entries:
        t_wh = (e.t_warehouse or "").lower()
        if "quarantine" in t_wh and e.custom_receiving_batch_id == batch_no:
            quarantine_entry = e
            break
    if not quarantine_entry:
        for e in all_entries:
            if e.stock_entry_type in ("Receiving Quarantined", "Quarantine Transfer") and e.custom_receiving_batch_id == batch_no:
                quarantine_entry = e
                break
    if not quarantine_entry:
        raise ValueError("No quarantined entry found for bucket %s in batch %s" % (bucket_id_upper, batch_no))

    entry = quarantine_entry
    quarantine_wh = entry.t_warehouse or ""
    original_wh = entry.s_warehouse or ""

    net_row = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN sei.t_warehouse LIKE '%%Quarantin%%' THEN sei.qty ELSE 0 END)
          - SUM(CASE WHEN sei.s_warehouse LIKE '%%Quarantin%%' THEN sei.qty ELSE 0 END) AS net
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sei ON sei.parent = se.name
        WHERE se.custom_bucket_id = %s
          AND se.custom_receiving_batch_id = %s
          AND se.docstatus = 1
    """, (bucket_id_lower, batch_no), as_dict=1)
    available_stems = int(net_row[0].net) if (net_row and net_row[0].net is not None) else 0

    if available_stems <= 0:
        raise ValueError("Bucket %s (Batch: %s) has no stems remaining in quarantine." % (bucket_id_upper, batch_no))

    stems_count = available_stems
    if stems_to_release not in (None, ""):
        stems_count = int(stems_to_release)

    if stems_count > available_stems:
        raise ValueError("Only %s stems available in quarantine for bucket %s (Batch: %s)" % (available_stems, bucket_id_upper, batch_no))

    target_warehouse = original_wh if original_wh else ACCEPTED_STORAGE
    rejects_warehouse = frappe.db.get_value(
        "Warehouse",
        {"company": entry.company, "disabled": 0, "warehouse_name": ["like", "%Reject%"]},
        "name",
    ) or "Rejects - KR"

    if action == "accept":
        accepted_qty = stems_count
        rejected_qty = available_stems - stems_count
    else:  # reject
        rejected_qty = stems_count
        accepted_qty = available_stems - stems_count

    accepted_entry = None
    rejected_entry = None
    if rejected_qty > 0:
        rejected_entry = build_movement("Quarantine Rejects", "Material Transfer", quarantine_wh, rejects_warehouse, rejected_qty, entry)
    if accepted_qty > 0:
        accepted_entry = build_movement("Quarantine Accept", "Material Transfer", quarantine_wh, target_warehouse, accepted_qty, entry)

    stock_entries = [dd.name for dd in (accepted_entry, rejected_entry) if dd]
    return {
        "bucket_id": bucket_id_upper,
        "accepted_stems": accepted_qty,
        "rejected_stems": rejected_qty,
        "stock_entries": stock_entries,
        "target_warehouse": target_warehouse,
        "rejects_warehouse": rejects_warehouse,
        "remaining_in_quarantine": available_stems - accepted_qty - rejected_qty,
    }


@frappe.whitelist()
def releaseBatchFromQuarantine():
    """Resolve an ENTIRE quarantined batch in one submit.

    Payload:
      { "batch_no": "...",
        "buckets": [
            {"bucket_id": "...", "rejected_stems": N},   # accept the rest
            {"bucket_id": "..."},                        # no issues -> accept all
            ...
        ] }

    Per bucket the flagged `rejected_stems` go to Quarantine Rejects and everything
    else in that bucket is accepted (Quarantine Accept); a bucket with no
    `rejected_stems` (or 0) is fully accepted. No bucket is left partially in
    quarantine. `action` + `stems_to_release` are still honoured if a caller sends
    them instead. Each bucket is isolated with a savepoint, so one bad bucket is
    reported in `errors` and never rolls back the others; a single commit persists
    everything that succeeded.
    """
    data = frappe.request.get_json()
    data = data.get("data", data)

    batch_no = data.get("batch_no", "")
    buckets = data.get("buckets", []) or []

    if not batch_no:
        frappe.throw("batch_no is required")
    if not buckets:
        frappe.throw("buckets is required")

    results = []
    errors = []
    total_accepted = 0
    total_rejected = 0

    for i, b in enumerate(buckets):
        bucket_id = (b.get("bucket_id") or "").strip()
        if not bucket_id:
            continue

        # Preferred input: a per-bucket rejected count (rest is accepted).
        # Falls back to an explicit action + stems_to_release if provided.
        if b.get("rejected_stems") not in (None, ""):
            action = "reject"
            stems_to_release = int(b.get("rejected_stems"))
        else:
            action = b.get("action") or "accept"
            if action not in ("accept", "reject"):
                action = "accept"
            stems_to_release = b.get("stems_to_release")

        sp = "bkt_%d" % i
        frappe.db.savepoint(sp)
        try:
            res = _resolve_bucket_quarantine(bucket_id, batch_no, action, stems_to_release)
            total_accepted += res["accepted_stems"]
            total_rejected += res["rejected_stems"]
            results.append(res)
        except Exception as e:
            frappe.db.rollback(save_point=sp)
            errors.append({"bucket_id": bucket_id.upper(), "error": str(e)})

    frappe.db.commit()

    frappe.response["status"] = "success" if results else "error"
    frappe.response["message"] = "Batch %s: %d bucket(s) resolved (%d accepted, %d rejected)%s." % (
        batch_no, len(results), total_accepted, total_rejected,
        ("; %d failed" % len(errors)) if errors else "",
    )
    frappe.response["batch_no"] = batch_no
    frappe.response["buckets_resolved"] = len(results)
    frappe.response["total_accepted_stems"] = total_accepted
    frappe.response["total_rejected_stems"] = total_rejected
    frappe.response["results"] = results
    frappe.response["errors"] = errors


@frappe.whitelist()
def replaceBucket():
    data = frappe.request.get_json()
    bucket_id = (data.get("bucket_id") or "").strip()
    requested_new_bucket = (data.get("new_bucket_id") or "").strip()
    # Specific Pick List Item to swap. Required when a bucket is allocated to multiple
    # OPLs — the caller must pick one so other orders aren't affected.
    requested_pli = (data.get("pick_list_item") or "").strip()

    # Permission check via Has Role (same role as bunch correction)
    has_role_rows = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "role": "Harvest Details Updater"},
        fields=["name"],
        limit=1,
    )
    is_admin = frappe.session.user == "Administrator"
    user_has_role = bool(has_role_rows) or is_admin

    if not user_has_role:
        frappe.response["http_status_code"] = 403
        frappe.response["data"] = {"error": "Role 'Harvest Details Updater' required."}

    if user_has_role:
        if not bucket_id:
            frappe.response["http_status_code"] = 400
            frappe.response["data"] = {"error": "bucket_id is required."}

        if bucket_id:
            try:
                # 1. Find Pick List Item(s) that own this bucket.
                #    If pick_list_item is given, scope to that exact row. Otherwise,
                #    fall back to the latest — but require a single match. If multiple
                #    PLIs exist and the caller didn't pick, return 409 with the list.
                base_pli_filters = {"custom_bucket": bucket_id}
                if requested_pli:
                    base_pli_filters["name"] = requested_pli
                pli_rows = frappe.get_all(
                    "Pick List Item",
                    filters=base_pli_filters,
                    fields=[
                        "name", "parent", "parenttype",
                        "item_code", "item_name", "description",
                        "qty", "stock_qty", "uom", "conversion_factor", "stock_uom",
                        "warehouse", "custom_stem_length", "custom_shelf",
                        "custom_sale_order_item", "custom_box_id",
                        "custom_truck", "custom_rate", "custom_packrate",
                        "sales_order", "sales_order_item",
                        "custom_issued", "creation",
                    ],
                    order_by="creation desc",
                    limit=50 if not requested_pli else 1,
                )

                if not pli_rows:
                    frappe.response["http_status_code"] = 404
                    frappe.response["data"] = {
                        "error": "No allocation found for bucket " + bucket_id +
                                 (" (pick_list_item " + requested_pli + ")" if requested_pli else "") + "."
                    }

                if pli_rows and not requested_pli and len(pli_rows) > 1:
                    # Bucket is allocated to multiple OPLs — caller must pick one.
                    opl_names = list({r["parent"] for r in pli_rows if r.get("parent")})
                    frappe.response["http_status_code"] = 409
                    frappe.response["data"] = {
                        "error": "Bucket " + bucket_id + " is allocated to " +
                                 str(len(pli_rows)) + " Pick List Items across " +
                                 str(len(opl_names)) + " OPL(s). " +
                                 "Supply pick_list_item to choose one.",
                        "needs_pick_list_item": True,
                        "candidate_pick_list_items": [r["name"] for r in pli_rows],
                    }

                if pli_rows and (requested_pli or len(pli_rows) == 1):
                    pli = pli_rows[0]
                    opl_name = pli.get("parent")
                    sale_order_item = pli.get("custom_sale_order_item")
                    so_name = pli.get("sales_order")
                    variety = pli.get("item_code") or ""
                    stem_length = pli.get("custom_stem_length") or ""

                    # 2. Determine farm — prefer the shelf the bucket was picked from (OPL's source).
                    # This stays consistent even when the same bucket has been reused across farms.
                    shelf_name = pli.get("custom_shelf") or ""
                    farm = ""
                    if shelf_name:
                        shelf_meta = frappe.get_all(
                            "Shelf",
                            filters={"name": shelf_name},
                            fields=["farm"],
                            limit=1,
                        )
                        if shelf_meta:
                            farm = shelf_meta[0].get("farm") or ""
                    if not farm:
                        old_harvest_rows = frappe.get_all(
                            "Stock Entry",
                            filters={"custom_bucket_id": bucket_id, "stock_entry_type": "Harvesting"},
                            fields=["farm"],
                            order_by="posting_date desc, posting_time desc, creation desc",
                            limit=1,
                        )
                        if old_harvest_rows:
                            farm = old_harvest_rows[0].get("farm") or ""

                    if not variety or not stem_length or not farm:
                        frappe.response["http_status_code"] = 400
                        frappe.response["data"] = {
                            "error": "Cannot determine variety/length/farm for bucket " + bucket_id
                        }

                    if variety and stem_length and farm:
                        # 3. Find candidate replacement Shelf Item(s)
                        # If new_bucket_id is provided, validate THAT bucket matches the criteria.
                        # Otherwise, fall back to FIFO auto-pick.
                        if requested_new_bucket:
                            candidates = frappe.db.sql(
                                """
                                SELECT si.name AS shelf_item, si.parent AS shelf,
                                       si.bucket_id, si.variety, si.stem_length,
                                       si.stem_qty, si.date_added,
                                       si.warehouse, si.greenhouse
                                FROM `tabShelf Item` si
                                INNER JOIN `tabShelf` s ON s.name = si.parent
                                WHERE si.variety = %s
                                  AND si.stem_length = %s
                                  AND s.farm = %s
                                  AND si.bucket_id = %s
                                  AND si.bucket_id != %s
                                LIMIT 1
                                """,
                                (variety, stem_length, farm, requested_new_bucket, bucket_id),
                                as_dict=True,
                            )
                            if not candidates:
                                frappe.response["http_status_code"] = 404
                                frappe.response["data"] = {
                                    "error": "Bucket " + requested_new_bucket +
                                             " is not a valid replacement (must be shelved, " +
                                             variety + " " + stem_length + " from " + farm + ")."
                                }
                        else:
                            candidates = frappe.db.sql(
                                """
                                SELECT si.name AS shelf_item, si.parent AS shelf,
                                       si.bucket_id, si.variety, si.stem_length,
                                       si.stem_qty, si.date_added,
                                       si.warehouse, si.greenhouse
                                FROM `tabShelf Item` si
                                INNER JOIN `tabShelf` s ON s.name = si.parent
                                WHERE si.variety = %s
                                  AND si.stem_length = %s
                                  AND s.farm = %s
                                  AND si.bucket_id != %s
                                ORDER BY si.date_added ASC
                                LIMIT 1
                                """,
                                (variety, stem_length, farm, bucket_id),
                                as_dict=True,
                            )

                            if not candidates:
                                frappe.response["http_status_code"] = 404
                                frappe.response["data"] = {
                                    "error": "No replacement available for " + variety +
                                             " " + stem_length + " from " + farm + "."
                                }

                        if candidates:
                            new = candidates[0]
                            new_bucket_id = new["bucket_id"]
                            new_shelf = new["shelf"]
                            new_shelf_item = new["shelf_item"]
                            new_warehouse = new.get("warehouse") or pli.get("warehouse") or ""
                            now_ts = frappe.utils.now()

                            # Swap-in-place: update the existing Pick List Item to point at the
                            # new bucket. This avoids adding/removing rows on a submitted OPL.
                            frappe.db.set_value("Pick List Item", pli["name"], {
                                "custom_bucket": new_bucket_id,
                                "custom_shelf": new_shelf,
                                "warehouse": new_warehouse,
                                "custom_issued": 1,
                            })

                            # Un-issue the old bucket's latest SE
                            old_se_rows = frappe.get_all(
                                "Stock Entry",
                                filters={"custom_bucket_id": bucket_id, "docstatus": 1},
                                fields=["name"],
                                order_by="creation desc",
                                limit=1,
                            )
                            if old_se_rows:
                                frappe.db.set_value(
                                    "Stock Entry", old_se_rows[0]["name"],
                                    "custom_issued_to", None
                                )

                            # Issue the replacement bucket's latest SE
                            new_se_rows = frappe.get_all(
                                "Stock Entry",
                                filters={"custom_bucket_id": new_bucket_id, "docstatus": 1},
                                fields=["name"],
                                order_by="creation desc",
                                limit=1,
                            )
                            if new_se_rows:
                                frappe.db.set_value(
                                    "Stock Entry", new_se_rows[0]["name"],
                                    "custom_issued_to", sale_order_item
                                )

                            # Remove the replacement bucket from its shelf
                            shl_log = frappe.db.get_value("Shelving Log", {"shelf_item": new_shelf_item, "reason": "Shelved"}, "name")
                            if shl_log:
                                frappe.db.set_value("Shelving Log", shl_log, {"reason": "Replaced", "removed_on": frappe.utils.now_datetime()}, update_modified=False)
                            frappe.delete_doc("Shelf Item", new_shelf_item,
                                              force=1, ignore_permissions=True)
                            frappe.db.set_value("Shelf", new_shelf, "modified", now_ts)

                            # Touch parent OPL
                            frappe.db.set_value("Order Pick List", opl_name, "modified", now_ts)

                            frappe.db.commit()

                            frappe.response["data"] = {
                                "status": "success",
                                "message": "Bucket " + bucket_id + " replaced with " + new_bucket_id + ".",
                                "old_bucket": bucket_id,
                                "new_bucket": new_bucket_id,
                                "opl": opl_name,
                                "sale_order_item": sale_order_item,
                                "variety": variety,
                                "stem_length": stem_length,
                                "farm": farm,
                            }

            except Exception as e:
                frappe.db.rollback()
                frappe.log_error("replaceBucket error: " + str(e))
                frappe.response["http_status_code"] = 500
                frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def replaceBunchInOpl():
    data = frappe.request.get_json()
    pick_list_item = (data.get("pick_list_item") or "").strip()
    donor_bucket_id = (data.get("donor_bucket_id") or "").strip()
    stems_in = data.get("stems")
    reason = (data.get("reason") or "Bunch replacement").strip()


    def farm_max_age(farm_name):
        try:
            ps = frappe.get_doc("Production Settings")
            for row in (ps.shelf_locations or []):
                if row.farm == farm_name and row.enabled:
                    return int(row.max_allocation_age or 5)
        except Exception:
            pass
        return 5


    has_role_rows = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "role": "Harvest Details Updater"},
        fields=["name"],
        limit=1,
    )
    is_admin = frappe.session.user == "Administrator"
    user_has_role = bool(has_role_rows) or is_admin

    if not user_has_role:
        frappe.response["http_status_code"] = 403
        frappe.response["data"] = {"error": "Role 'Harvest Details Updater' required."}

    if user_has_role:
        if not pick_list_item or not donor_bucket_id:
            frappe.response["http_status_code"] = 400
            frappe.response["data"] = {
                "error": "pick_list_item and donor_bucket_id are required."
            }

        if pick_list_item and donor_bucket_id:
            try:
                pli_rows = frappe.get_all(
                    "Pick List Item",
                    filters={"name": pick_list_item},
                    fields=["name", "parent", "qty", "stock_qty", "conversion_factor",
                            "item_code", "custom_stem_length", "custom_bucket",
                            "custom_shelf", "warehouse"],
                    limit=1,
                )
                if not pli_rows:
                    frappe.response["http_status_code"] = 404
                    frappe.response["data"] = {"error": "Pick List Item " + pick_list_item + " not found."}

                if pli_rows:
                    pli = pli_rows[0]
                    opl_name = pli.get("parent") or ""
                    req_variety = pli.get("item_code") or ""
                    req_length = pli.get("custom_stem_length") or ""

                    conv = float(pli.get("conversion_factor") or 10) or 10.0
                    if stems_in is not None:
                        try:
                            stems = int(float(stems_in))
                        except Exception:
                            stems = int(conv)
                    else:
                        stems = int(conv)
                    if stems <= 0:
                        stems = 10

                    donor_rows = frappe.db.sql(
                        """
                        SELECT si.name AS shelf_item, si.parent AS shelf,
                               si.bucket_id, si.variety, si.stem_length, si.stem_qty,
                               s.farm,
                               (COALESCE(si.stem_qty, 0) - COALESCE(bas.allocated_quantity, 0)) AS available_qty,
                               COALESCE(bas.in_transit, 0) AS in_transit,
                               (SELECT seh.posting_date FROM `tabStock Entry` seh
                                 WHERE seh.stock_entry_type = 'Harvesting' AND seh.docstatus = 1
                                   AND seh.custom_bucket_id = si.bucket_id
                                 ORDER BY seh.creation DESC LIMIT 1) AS harvest_date,
                               (EXISTS (
                                   SELECT 1 FROM `tabDiscard Request Bucket` drb
                                   INNER JOIN `tabDiscard Request` dr ON dr.name = drb.parent
                                   WHERE dr.docstatus = 1
                                     AND dr.workflow_state = 'Approved'
                                     AND drb.discarded = 0
                                     AND drb.bucket_id = si.bucket_id
                               )) AS on_discard_list
                        FROM `tabShelf Item` si
                        INNER JOIN `tabShelf` s ON s.name = si.parent
                        LEFT JOIN `tabBucket Allocation Status` bas
                           ON bas.bucket_id = si.bucket_id AND bas.item_code = si.variety
                        WHERE si.bucket_id = %s
                        LIMIT 1
                        """,
                        (donor_bucket_id,),
                        as_dict=True,
                    )
                    if not donor_rows:
                        frappe.response["http_status_code"] = 404
                        frappe.response["data"] = {
                            "error": "Donor bucket " + donor_bucket_id + " not found on any shelf."
                        }

                    if donor_rows:
                        donor = donor_rows[0]
                        donor_variety = donor.get("variety") or ""
                        donor_length = donor.get("stem_length") or ""
                        donor_available = float(donor.get("available_qty") or 0)
                        in_transit = 1 if donor.get("in_transit") else 0
                        on_discard = 1 if donor.get("on_discard_list") else 0
                        harvest_date = donor.get("harvest_date")
                        discard_age = frappe.db.get_single_value("Production Settings", "discard_age")
                        discard_age = float(discard_age) if discard_age else 5.0
                        max_age = farm_max_age(donor.get("farm"))
                        age_days = frappe.utils.date_diff(frappe.utils.nowdate(), str(harvest_date)) if harvest_date else None

                        if on_discard:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {
                                "error": "Donor bucket " + donor_bucket_id +
                                         " is on today's discard list and can't be used for replacement."
                            }

                        elif in_transit:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {
                                "error": "Donor bucket " + donor_bucket_id +
                                         " is in transit between farms and can't be used for replacement."
                            }

                        elif not harvest_date:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {
                                "error": "Donor bucket " + donor_bucket_id +
                                         " has no Harvesting entry, so its age can't be verified."
                            }

                        elif age_days >= discard_age or age_days > max_age:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {
                                "error": "Donor bucket " + donor_bucket_id + " is " + str(int(age_days)) +
                                         " days old and past the allocation age limit."
                            }

                        elif req_variety and donor_variety and donor_variety != req_variety:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {
                                "error": "Donor variety '" + donor_variety +
                                         "' does not match the order's required variety '" +
                                         req_variety + "'."
                            }

                        elif req_length and donor_length and donor_length != req_length:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {
                                "error": "Donor stem length '" + donor_length +
                                         "' does not match the order's required length '" +
                                         req_length + "'."
                            }

                        elif donor_available < stems:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {
                                "error": "Donor only has " + str(int(donor_available)) +
                                         " stems available; need " + str(stems) + "."
                            }

                        else:
                            now_ts = frappe.utils.now()
                            log = []

                            new_qty = float(pli.get("qty") or 0) + 1
                            new_stock_qty = float(pli.get("stock_qty") or 0) + stems
                            frappe.db.set_value("Pick List Item", pli["name"], {
                                "qty": new_qty,
                                "stock_qty": new_stock_qty,
                            })
                            log.append("PLI " + pli["name"] + " restored: qty " +
                                       str(int(new_qty)) + " / " + str(int(new_stock_qty)) + " stems")
                            if opl_name:
                                frappe.db.set_value("Order Pick List", opl_name,
                                                    "modified", now_ts)

                            donor_si_current = float(donor.get("stem_qty") or 0)
                            donor_si_new = max(0, donor_si_current - stems)
                            frappe.db.set_value("Shelf Item", donor["shelf_item"],
                                                "stem_qty", donor_si_new)
                            frappe.db.set_value("Shelf", donor["shelf"],
                                                "modified", now_ts)
                            log.append("Donor shelf item " + str(donor["shelf_item"]) +
                                       " stem_qty " + str(int(donor_si_current)) +
                                       " -> " + str(int(donor_si_new)))

                            try:
                                log_doc = frappe.get_doc({
                                    "doctype": "Stem Replacement Log",
                                    "pick_list_item": pli["name"],
                                    "opl": opl_name,
                                    "destination_bucket": pli.get("custom_bucket") or "",
                                    "donor_bucket": donor_bucket_id,
                                    "donor_shelf": donor["shelf"] or "",
                                    "variety": donor_variety or req_variety,
                                    "stem_length": donor_length or req_length,
                                    "stems": stems,
                                    "replaced_at": now_ts,
                                    "reason": reason,
                                })
                                log_doc.insert(ignore_permissions=True)
                                log.append("Audit log " + log_doc.name + " written")
                            except Exception as le:
                                log.append("Audit log write failed: " + str(le))

                            frappe.db.commit()

                            frappe.response["data"] = {
                                "status": "replaced",
                                "message": "Replacement bunch (" + str(stems) +
                                           " stems) allocated to OPL " + opl_name +
                                           " from bucket " + donor_bucket_id + ".",
                                "pick_list_item": pli["name"],
                                "opl": opl_name,
                                "destination_bucket": pli.get("custom_bucket") or "",
                                "donor_bucket": donor_bucket_id,
                                "donor_shelf": donor["shelf"] or "",
                                "stems": stems,
                                "variety": donor_variety or req_variety,
                                "stem_length": donor_length or req_length,
                                "updates": log,
                            }

            except Exception as e:
                frappe.db.rollback()
                try:
                    frappe.log_error(title="replaceBunchInOpl error", message=str(e))
                except Exception:
                    pass
                frappe.response["http_status_code"] = 500
                frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def replaceStems():
    data = frappe.request.get_json()
    pick_list_item = (data.get("pick_list_item") or "").strip()
    donor_bucket_id = (data.get("donor_bucket_id") or "").strip()
    stems = data.get("stems") or 1
    try:
        stems = int(stems)
    except Exception:
        stems = 1
    reason = (data.get("reason") or "").strip()


    def farm_max_age(farm_name):
        try:
            ps = frappe.get_doc("Production Settings")
            for row in (ps.shelf_locations or []):
                if row.farm == farm_name and row.enabled:
                    return int(row.max_allocation_age or 5)
        except Exception:
            pass
        return 5


    # Permission gate
    has_role_rows = frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user, "role": "Harvest Details Updater"},
        fields=["name"],
        limit=1,
    )
    is_admin = frappe.session.user == "Administrator"
    user_has_role = bool(has_role_rows) or is_admin

    if not user_has_role:
        frappe.response["http_status_code"] = 403
        frappe.response["data"] = {"error": "Role 'Harvest Details Updater' required."}

    if user_has_role:
        if not pick_list_item or not donor_bucket_id or stems <= 0:
            frappe.response["http_status_code"] = 400
            frappe.response["data"] = {
                "error": "pick_list_item, donor_bucket_id and stems > 0 are required.",
            }

        if pick_list_item and donor_bucket_id and stems > 0:
            try:
                pli = frappe.db.get_value(
                    "Pick List Item", pick_list_item,
                    ["name", "parent", "custom_bucket", "item_code",
                     "custom_stem_length", "custom_sale_order_item"],
                    as_dict=True,
                )
                if not pli:
                    frappe.response["http_status_code"] = 404
                    frappe.response["data"] = {"error": "Pick List Item " + pick_list_item + " not found."}

                if pli:
                    donor_shelf_items = frappe.db.sql(
                        """
                        SELECT si.name AS shelf_item, si.parent AS shelf, si.stem_qty,
                               si.variety, si.stem_length, s.farm,
                               COALESCE(bas.allocated_quantity, 0) AS allocated_qty,
                               (COALESCE(si.stem_qty, 0) - COALESCE(bas.allocated_quantity, 0)) AS available_qty,
                               COALESCE(bas.in_transit, 0) AS in_transit,
                               (SELECT seh.posting_date FROM `tabStock Entry` seh
                                 WHERE seh.stock_entry_type = 'Harvesting' AND seh.docstatus = 1
                                   AND seh.custom_bucket_id = si.bucket_id
                                 ORDER BY seh.creation DESC LIMIT 1) AS harvest_date,
                               (EXISTS (
                                   SELECT 1 FROM `tabDiscard Request Bucket` drb
                                   INNER JOIN `tabDiscard Request` dr ON dr.name = drb.parent
                                   WHERE dr.docstatus = 1
                                     AND dr.workflow_state = 'Approved'
                                     AND drb.discarded = 0
                                     AND drb.bucket_id = si.bucket_id
                               )) AS on_discard_list
                        FROM `tabShelf Item` si
                        INNER JOIN `tabShelf` s ON s.name = si.parent
                        LEFT JOIN `tabBucket Allocation Status` bas
                           ON bas.bucket_id = si.bucket_id AND bas.item_code = si.variety
                        WHERE si.bucket_id = %s
                        ORDER BY si.date_added DESC
                        LIMIT 1
                        """,
                        (donor_bucket_id,),
                        as_dict=True,
                    )

                    if not donor_shelf_items:
                        frappe.response["http_status_code"] = 404
                        frappe.response["data"] = {
                            "error": "Donor bucket " + donor_bucket_id + " isn't on a shelf.",
                        }

                    if donor_shelf_items:
                        donor = donor_shelf_items[0]
                        try:
                            shelf_qty = float(donor.get("stem_qty") or 0)
                            available = float(donor.get("available_qty") or 0)
                            allocated = float(donor.get("allocated_qty") or 0)
                        except Exception:
                            shelf_qty = 0
                            available = 0
                            allocated = 0

                        in_transit = 1 if donor.get("in_transit") else 0
                        on_discard = 1 if donor.get("on_discard_list") else 0
                        harvest_date = donor.get("harvest_date")
                        discard_age = frappe.db.get_single_value("Production Settings", "discard_age")
                        discard_age = float(discard_age) if discard_age else 5.0
                        max_age = farm_max_age(donor.get("farm"))
                        age_days = frappe.utils.date_diff(frappe.utils.nowdate(), str(harvest_date)) if harvest_date else None

                        block = ""
                        if on_discard:
                            block = ("Donor bucket " + donor_bucket_id +
                                     " is on today's discard list and can't be used for replacement.")
                        elif in_transit:
                            block = ("Donor bucket " + donor_bucket_id +
                                     " is in transit between farms and can't be used for replacement.")
                        elif not harvest_date:
                            block = ("Donor bucket " + donor_bucket_id +
                                     " has no Harvesting entry, so its age can't be verified.")
                        elif age_days >= discard_age or age_days > max_age:
                            block = ("Donor bucket " + donor_bucket_id + " is " + str(int(age_days)) +
                                     " days old and past the allocation age limit.")
                        elif available < stems:
                            block = ("Donor bucket " + donor_bucket_id +
                                     " has only " + str(int(available)) + " stems available " +
                                     "(" + str(int(allocated)) + " of " + str(int(shelf_qty)) +
                                     " already allocated).")

                        if block:
                            frappe.response["http_status_code"] = 400
                            frappe.response["data"] = {"error": block}
                        else:
                            now_ts = frappe.utils.now()
                            new_qty = shelf_qty - stems

                            frappe.db.set_value(
                                "Shelf Item", donor["shelf_item"],
                                "stem_qty", new_qty,
                            )
                            frappe.db.set_value("Shelf", donor["shelf"], "modified", now_ts)

                            log_name = ""
                            try:
                                log = frappe.new_doc("Stem Replacement Log")
                                log.pick_list_item = pick_list_item
                                log.opl = pli.get("parent") or ""
                                log.sale_order_item = pli.get("custom_sale_order_item") or ""
                                log.destination_bucket = pli.get("custom_bucket") or ""
                                log.donor_bucket = donor_bucket_id
                                log.donor_shelf = donor["shelf"]
                                log.variety = donor.get("variety") or pli.get("item_code") or ""
                                log.stem_length = donor.get("stem_length") or pli.get("custom_stem_length") or ""
                                log.stems = stems
                                log.replaced_at = now_ts
                                if reason:
                                    log.reason = reason
                                log.insert(ignore_permissions=True)
                                log_name = log.name
                            except Exception:
                                log_name = ""

                            frappe.db.commit()

                            frappe.response["data"] = {
                                "status": "success",
                                "message": (
                                    "Take " + str(stems) + " stem" +
                                    ("" if stems == 1 else "s") +
                                    " from bucket " + donor_bucket_id +
                                    " on shelf " + donor["shelf"] + "."
                                ),
                                "log_name": log_name,
                                "donor_bucket": donor_bucket_id,
                                "donor_shelf": donor["shelf"],
                                "stems": stems,
                                "donor_remaining_stems": int(new_qty),
                                "donor_remaining_available": int(max(0, available - stems)),
                                "destination_bucket": pli.get("custom_bucket") or "",
                                "opl": pli.get("parent") or "",
                            }

            except Exception as e:
                frappe.db.rollback()
                try:
                    frappe.log_error(title="replaceStems error", message=str(e))
                except Exception:
                    pass
                frappe.response["http_status_code"] = 500
                frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def savePackhouseQC():
    try:
        data = frappe.form_dict

        def greenhouse_from_wh(wh):
            if not wh:
                return ""
            if " - " in wh:
                wh = wh.rsplit(" - ", 1)[0]
            non_gh = ["Packhouse", "Rejects", "WIP", "Transit",
                      "Main Store", "Cold Room", "Finished"]
            for skip in non_gh:
                if skip in wh:
                    return ""
            for clip in ["Receiving", "Cold"]:
                if clip in wh:
                    wh = wh.split(clip)[0].strip()
                    break
            return wh.strip()

        qc_stage        = data.get("qc_stage", "")
        inspection_type = data.get("inspection_type", "") or (
            "Online QC" if "Online" in qc_stage else "Final QC"
        )
        inspection_mode = data.get("inspection_mode", "")
        if not qc_stage:
            if inspection_type == "Online QC":
                qc_stage = (
                    "Online QC - Grading QC" if inspection_mode == "Grading QC"
                    else "Online QC - Reject Recorder"
                )
            else:
                qc_stage = "Final QC"
        date_val = data.get("date", frappe.utils.today())

        # Grading QC does NOT create a Packhouse reject stock entry for rejected
        # stems -- those rejected bunches are handled through the replacement flow
        # instead. Reject Recorder / Final QC / Airport Returns still do.
        is_grading = (inspection_type == "Online QC" and inspection_mode == "Grading QC")
        # Reject Recorder must NOT raise a CAR -- Grading QC already raises the CAR
        # for the same order/variety, so doing it here would duplicate it.
        is_reject_recorder = (inspection_type == "Online QC" and inspection_mode == "Reject Recorder")

        control_point = data.get("control_point", "")
        control_area  = data.get("control_area", "")
        if control_point and not control_area:
            try:
                control_area = (frappe.db.get_value(
                    "QC Control Point", control_point, "control_area"
                ) or control_point)
            except Exception:
                control_area = control_point

        # Airport Returns is a scan-based return QC (control area "Airport
        # Returns"). Its stems are dispositioned as Reuse (shelved back to the
        # Kapkolia cold store) or Reject (moved to rejects) -- and it never
        # raises a CAR, since a customer return isn't a production defect.
        is_airport = (control_area or "").strip().lower() == "airport returns"
        # Give returns their own QC stage so the report doesn't lump them in
        # with genuine Final QC records.
        if is_airport:
            qc_stage = "Airport Returns"

        order_pick_list = (
            data.get("order_pick_list", "")
            or data.get("order_spec", "")
            or data.get("order_spec_id", "")
        )
        # custom_order_pick_list is now a Link on Quality Reporting -- keep only valid ids
        if order_pick_list and not frappe.db.exists("Order Pick List", order_pick_list):
            order_pick_list = ""

        variety   = data.get("variety", "")
        greenhouse = data.get("greenhouse", "")
        farm      = data.get("farm", "")
        box_label_raw = data.get("box_label", "")
        box_label = box_label_raw if frappe.db.exists("Box Label", box_label_raw) else ""

        specification_raw = data.get("specification", "")
        specification = specification_raw if frappe.db.exists("Specifications", specification_raw) else ""

        length = data.get("length", "")
        if not length and box_label:
            length = frappe.db.get_value(
                "Box Label Item", {"parent": box_label}, "length"
            ) or ""
        if not length and order_pick_list and variety:
            length = frappe.db.get_value(
                "Pick List Item",
                {"parent": order_pick_list, "item_code": variety},
                "stem_length"
            ) or ""
        # custom_length is now a Link to Stem Length -- keep only valid values
        if length and not frappe.db.exists("Stem Length", length):
            length = ""

        stems_affected   = int(data.get("stems_affected",   0) or 0)
        bunches_affected = int(data.get("bunches_affected",  0) or 0)
        stems_checked    = int(data.get("stems_checked",     0) or 0) or stems_affected
        boxes_checked    = int(data.get("boxes_checked",     0) or 0)
        boxes_staged     = int(data.get("boxes_staged",      0) or 0)
        boxes_quarantined= int(data.get("boxes_quarantined", 0) or 0)
        sampled_stems    = int(data.get("sampled_stems",     0) or 0)
        bunches_sampled  = int(data.get("bunches_sampled",   0) or 0)
        bunches_allocated= int(data.get("total_bunches",     0) or 0)
        # Grading QC replacement outcome -- stems taken from donor buckets, and
        # rejected bunches that were left short (not fully replaced).
        stems_replaced     = int(data.get("stems_replaced",     0) or 0)
        incomplete_stems   = int(data.get("incomplete_stems",   0) or 0)
        incomplete_bunches = int(data.get("incomplete_bunches", 0) or 0)
        # Airport Returns extras carried on the QC record.
        invoice_number   = data.get("invoice_number", "")
        packhouse        = data.get("packhouse", "")
        stock_age        = int(data.get("stock_age", 0) or 0)

        qc_incharge = data.get("qc_incharge", "") or data.get("inspector", "")
        recorder    = frappe.session.user

        reason     = data.get("reason", "")
        remarks    = data.get("remarks", "")
        issues_raw = data.get("issues", "[]")
        if isinstance(issues_raw, str):
            issues_list = frappe.parse_json(issues_raw)
        else:
            issues_list = issues_raw or []

        customer = data.get("customer", "")
        team     = data.get("team", "")
        opl_data = {}
        if order_pick_list:
            opl_data = frappe.db.get_value(
                "Order Pick List", order_pick_list,
                ["customer", "team", "farm", "custom_total_stems"], as_dict=1
            ) or {}
            customer = customer or opl_data.get("customer", "")
            team     = team     or opl_data.get("team", "")
            if not farm:
                farm = opl_data.get("farm", "")
        # custom_customer is now a Link to Customer -- keep only valid values
        if customer and not frappe.db.exists("Customer", customer):
            customer = ""

        def true_greenhouse_warehouse(bucket_id, fallback_wh):
            if bucket_id:
                rows = frappe.db.sql(
                    "SELECT custom_greenhouse FROM `tabStock Entry`"
                    " WHERE custom_bucket_id = %(bucket)s"
                    " AND stock_entry_type IN ('Receiving', 'Late Receipt')"
                    " AND docstatus = 1"
                    " AND custom_greenhouse IS NOT NULL AND custom_greenhouse != ''"
                    " ORDER BY creation DESC LIMIT 1",
                    {"bucket": bucket_id}, as_dict=1
                )
                if rows:
                    return rows[0]["custom_greenhouse"]
            return fallback_wh

        opl_variety_gh_pairs = []
        if order_pick_list:
            loc_rows = frappe.db.sql(
                "SELECT DISTINCT item_code, warehouse, bucket"
                " FROM `tabPick List Item` WHERE parent = %s",
                order_pick_list, as_dict=1
            )
            seen_pairs = set()
            for loc in loc_rows:
                v  = loc.get("item_code", "")
                cold_room_wh = loc.get("warehouse", "")
                wh = true_greenhouse_warehouse(loc.get("bucket", ""), cold_room_wh)
                gh = greenhouse_from_wh(wh)
                if v and gh:
                    pair_key = v + "|" + gh
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        opl_variety_gh_pairs.append({
                            "variety": v, "greenhouse": gh, "warehouse": wh,
                            "cold_room_warehouse": cold_room_wh,
                        })

            if not greenhouse and opl_variety_gh_pairs:
                greenhouse = opl_variety_gh_pairs[0].get("greenhouse", "")

        # -- Tally issues --
        stems_quarantined = 0
        stems_rejected    = 0
        reuse_stems       = 0
        for issue in issues_list:
            cnt    = int(issue.get("count", 0) or 0)
            action = issue.get("action", "")
            if action == "Quarantine":
                stems_quarantined += cnt
            elif action == "Reject":
                stems_rejected += cnt
            elif action == "Reuse":
                reuse_stems += cnt

        stems_accepted = max(0, stems_checked - stems_quarantined - stems_rejected)

        if stems_accepted > 0:
            overall_result = "Accepted"
        elif stems_rejected > 0:
            overall_result = "Rejected"
        elif stems_quarantined > 0:
            overall_result = "Quarantined"
        else:
            overall_result = "Accepted"

        final_decision = data.get("final_decision", "")
        if final_decision in ("Accept", "Quarantine", "Reject"):
            order_total_stems = int(opl_data.get("custom_total_stems", 0) or 0)
            stems_checked = order_total_stems or stems_checked
            if final_decision == "Accept":
                stems_quarantined = 0
                stems_accepted    = max(0, stems_checked - stems_rejected)
                overall_result    = "Accepted"
            elif final_decision == "Quarantine":
                stems_accepted    = 0
                stems_quarantined = stems_checked
                stems_rejected    = 0
                overall_result    = "Quarantined"
            else:
                stems_accepted    = 0
                stems_quarantined = 0
                stems_rejected    = stems_checked
                overall_result    = "Rejected"

        issue_rows = []
        for i, iss in enumerate(issues_list):
            param = iss.get("parameter", "")
            cnt   = int(iss.get("count", 0) or 0)
            if param and cnt > 0:
                issue_rows.append({
                    "doctype":   "Packhouse QC Issue",
                    "parameter": param,
                    "count":     cnt,
                    "action":    iss.get("action", ""),
                    "remarks":   iss.get("remarks", ""),
                    "idx":       i + 1
                })

        # Map packhouse issues -> Quality Reporting quality_parameters (valid QC Parameters only)
        qr_params = []
        for r in issue_rows:
            pname = r.get("parameter")
            if pname and frappe.db.exists("QC Parameters", pname):
                act = r.get("action")
                qr_params.append({
                    "parameter_name": pname,
                    "count": r.get("count", 0),
                    "action": act if act in ("Accepted", "Reject", "Quarantined") else "",
                })

        # control_point is a required Link to QC Control Point (staging: Intake/Packhouse)
        if not (control_point and frappe.db.exists("QC Control Point", control_point)):
            control_point = "Packhouse"

        # Sequential name: ControlPoint-Farm-NNN
        qr_prefix = str(control_point) + "-" + str(farm or "NA") + "-"
        existing_qr = frappe.get_all("Quality Reporting", filters=[["name", "like", qr_prefix + "%"]], fields=["name"])
        qr_seq = len(existing_qr) + 1
        qr_name = qr_prefix + str(qr_seq).zfill(3)
        while frappe.db.exists("Quality Reporting", qr_name):
            qr_seq = qr_seq + 1
            qr_name = qr_prefix + str(qr_seq).zfill(3)

        doc = frappe.get_doc({
            "doctype":                  "Quality Reporting",
            "control_point":            control_point,
            "farm":                     farm if (farm and frappe.db.exists("Farm", farm)) else None,
            "variety":                  variety if (variety and frappe.db.exists("Item", variety)) else None,
            "ghouse":                   greenhouse,
            "control_action":           overall_result,
            "stems_received":           stems_checked,
            "stems_checked":            stems_checked,
            "quarantined_stems":        stems_quarantined,
            "quality_parameters":       qr_params,
            "item_group":               data.get("item_group", ""),
            "bunches_allocated":        bunches_allocated,
            "bunches_sampled":          bunches_sampled,
            "bunches_affected":         bunches_affected,
            "inspection_type":          inspection_type,
            "inspection_mode":          inspection_mode,
            "control_area":             control_area,
            "boxes_checked":            boxes_checked,
            "boxes_staged":             boxes_staged,
            "boxes_quarantined":        boxes_quarantined,
            "stems_accepted":           stems_accepted,
            "stems_rejected":           stems_rejected,
            "stems_affected":           stems_affected,
            "custom_inspection_type":   inspection_type,
            "custom_inspection_mode":   inspection_mode,
            "custom_order_pick_list":   order_pick_list,
            "custom_customer":          customer,
            "custom_team":              team,
            "custom_control_area":      control_area,
            "custom_specification":     specification,
            "custom_box_label":         box_label,
            "custom_length":            length,
            "custom_stems_accepted":    stems_accepted,
            "custom_stems_rejected":    stems_rejected,
            "custom_stems_affected":    stems_affected,
            "custom_bunches_affected":  bunches_affected,
            "custom_boxes_checked":     boxes_checked,
            "custom_boxes_staged":      boxes_staged,
            "custom_boxes_quarantined": boxes_quarantined,
        })
        # Standard fields are canonical: also write the standard Link fields
        # (existence-checked so a stale value can never fail the insert).
        # order_pick_list / box_label / specification / length were already
        # blanked above when invalid; customer / team are checked here.
        if customer and frappe.db.exists("Customer", customer):
            doc.customer = customer
        if team and frappe.db.exists("Packing Teams", team):
            doc.team = team
        if specification:
            doc.specification = specification
        if length:
            doc.length = length
        if order_pick_list:
            doc.order_pick_list = order_pick_list
        if box_label:
            doc.box_label = box_label
        doc.name = qr_name
        doc.flags.name_set = True
        doc.insert(ignore_permissions=True)

        created_cars = []
        car_errors   = []

        PEST_DISEASE_PARAMS = {
            "Helicoverpa Egg", "Helicoverpa Damage", "Helicoverpa Larvae",
            "FCM Egg", "FCM Damages", "FCM Larvae",
            "Spodoptera Egg", "Spodoptera Damage", "Spodoptera Larvae",
            "Aphids", "Live Aphids", "Mites", "Mite Damage",
            "Thrips", "Thrips Damage", "Mealy Bugs", "White Flies",
            "Leaf Miner", "Slugs", "Poor Defoliation", "Poor Sizing",
            "Botrytis", "Fresh Powdery Mildew", "Dry Powdery Mildew",
            "Fresh Downy Mildew", "Dry Downy Mildew", "Black Spot", "Rust", "Rotting",
        }
        CAR_OTHER_THRESHOLD_PCT = 6.0
        car_base = sampled_stems if sampled_stems > 0 else stems_checked

        def issue_warrants_car(iss):
            param = iss.get("parameter", "")
            if param in PEST_DISEASE_PARAMS:
                return True
            cnt = int(iss.get("count", 0) or 0)
            return car_base > 0 and (cnt * 100.0 / car_base) > CAR_OTHER_THRESHOLD_PCT

        car_worthy = any(issue_warrants_car(r) for r in issue_rows)
        # Airport Returns never raise CARs -- a customer return isn't a farm defect.
        has_issues = (bool(issue_rows)
                      and (stems_rejected > 0 or stems_quarantined > 0)
                      and car_worthy
                      and not is_airport
                      and not is_reject_recorder)

        CAR_ASSIGNEE = "nouma@karenroses.com"

        CAR_CP_MAP = {
            "intake": "Intake", "cold room": "Cold Room", "coldroom": "Cold Room",
            "grading": "Grading", "packhouse": "Packing", "packing": "Packing",
            "dispatch": "Dispatch",
        }
        def to_car_control_point(*texts):
            for text in texts:
                lowered = (text or "").strip().lower()
                for key, val in CAR_CP_MAP.items():
                    if key in lowered:
                        return val
            return "Packing"
        car_control_point = to_car_control_point(control_area, control_point)

        if has_issues and opl_variety_gh_pairs:
            issue_summary = "; ".join(
                r["parameter"] + " x" + str(r["count"])
                for r in issue_rows
                if r.get("action") in ("Reject", "Quarantine")
            )
            week_start = frappe.utils.add_days(date_val, -7)

            for pair in opl_variety_gh_pairs:
                pair_variety    = pair.get("variety", "")
                pair_greenhouse = pair.get("greenhouse", "")
                pair_warehouse  = pair.get("warehouse", "")
                if not pair_variety or not pair_warehouse:
                    continue
                if not frappe.db.exists("Warehouse", pair_warehouse):
                    car_errors.append({
                        "variety": pair_variety, "greenhouse": pair_greenhouse,
                        "error": "Warehouse %s not found" % pair_warehouse,
                    })
                    continue

                try:
                    existing_car = frappe.db.get_value(
                        "Corrective Action Report",
                        {
                            "custom_greenhouse": pair_warehouse,
                            "control_point":     car_control_point,
                            "variety":           pair_variety,
                            "date_of_incident":  [">=", week_start],
                            "status":            ["!=", "Complete"]
                        },
                        "name"
                    )

                    if existing_car:
                        created_cars.append({"variety": pair_variety,
                                             "greenhouse": pair_greenhouse,
                                             "car": existing_car,
                                             "new": False})
                    else:
                        car_doc = frappe.get_doc({
                            "doctype":           "Corrective Action Report",
                            "variety":           pair_variety,
                            "issue":             issue_summary,
                            "date_of_incident":  date_val,
                            "farm":              farm,
                            "requested_by":      qc_incharge or frappe.session.user,
                            "control_point":     car_control_point,
                            "assigned_to":       CAR_ASSIGNEE,
                            "status":            "Pending",
                            "custom_greenhouse": pair_warehouse,
                            "custom_specification": specification,
                            "root_cause":        reason or issue_summary or "See linked Packhouse QC for detail",
                            "corrective_action_plan": "Pending review",
                            "target_date":       frappe.utils.add_days(date_val, 7)
                        })
                        car_doc.insert(ignore_permissions=True)
                        created_cars.append({"variety": pair_variety,
                                             "greenhouse": pair_greenhouse,
                                             "car": car_doc.name,
                                             "new": True})
                except Exception as car_exc:
                    car_errors.append({
                        "variety":    pair_variety,
                        "greenhouse": pair_greenhouse,
                        "error":      str(car_exc)
                    })

        new_cars = [c for c in created_cars if c.get("new")]
        if new_cars:
            try:
                frappe.db.set_value(
                    "Quality Reporting", doc.name,
                    "custom_corrective_action_report", new_cars[0]["car"],
                    update_modified=False
                )
            except Exception:
                pass

        # -- Stock Entry for rejected stems --
        # Grading QC is intentionally SKIPPED here (is_grading): its rejected bunches
        # are handled via the replacement flow, so we don't also move them to the
        # Rejects warehouse. Reject Recorder / Final QC / Airport Returns still post.
        se_name  = None
        se_error = None
        if stems_rejected > 0 and not is_grading:
            try:
                se_greenhouse_warehouse = ""
                se_source_warehouse = ""
                for pair in opl_variety_gh_pairs:
                    if pair.get("greenhouse") == greenhouse:
                        se_greenhouse_warehouse = pair.get("warehouse", "")
                        se_source_warehouse = pair.get("cold_room_warehouse", "")
                        break
                if not se_greenhouse_warehouse and opl_variety_gh_pairs:
                    se_greenhouse_warehouse = opl_variety_gh_pairs[0].get("warehouse", "")
                    se_source_warehouse = opl_variety_gh_pairs[0].get("cold_room_warehouse", "")

                reject_item_code = variety if variety and frappe.db.exists("Item", variety) else "PACKHOUSE-REJECTS-KR"
                reject_item_uom = frappe.db.get_value("Item", reject_item_code, "stock_uom") or "Nos"

                # Source warehouse to issue the rejected stems from (OPL location / cold room).
                source_wh = ""
                for cand in (se_source_warehouse, se_greenhouse_warehouse):
                    if cand and frappe.db.exists("Warehouse", cand):
                        source_wh = cand
                        break

                if is_airport:
                    # Airport returns have no live source -> receive into Rejects.
                    se_item = {
                        "doctype":          "Stock Entry Detail",
                        "item_code":        reject_item_code,
                        "qty":              stems_rejected,
                        "uom":              reject_item_uom,
                        "t_warehouse":      "Rejects - KR",
                        "farm":             farm if frappe.db.exists("Farm", farm) else "",
                        "custom_greenhouse": greenhouse,
                        "basic_rate":       0,
                        "allow_zero_valuation_rate": 1,
                    }
                    se_type    = "Airport rejects"
                    se_purpose = "Material Receipt"
                else:
                    # Packhouse rejects: transfer from source cold store to Rejects - KR.
                    se_item = {
                        "doctype":          "Stock Entry Detail",
                        "item_code":        reject_item_code,
                        "qty":              stems_rejected,
                        "uom":              reject_item_uom,
                        "s_warehouse":      source_wh,
                        "t_warehouse":      "Rejects - KR",
                        "farm":             farm if frappe.db.exists("Farm", farm) else "",
                        "custom_greenhouse": greenhouse,
                        "basic_rate":       0,
                        "allow_zero_valuation_rate": 1,
                    }
                    se_type    = "Packhouse Rejects"
                    se_purpose = "Material Transfer"

                se_doc = {
                    "doctype":          "Stock Entry",
                    "stock_entry_type": se_type,
                    "purpose":          se_purpose,
                    "company":          "Karen Roses",
                    "posting_date":     date_val,
                    "custom_packhouse_qc": doc.name,
                    "team":      team,
                    "items": [se_item]
                }
                if farm and frappe.db.exists("Farm", farm):
                    se_doc["farm"] = farm
                if se_greenhouse_warehouse and frappe.db.exists("Warehouse", se_greenhouse_warehouse):
                    se_doc["custom_greenhouse"] = se_greenhouse_warehouse

                se = frappe.get_doc(se_doc)
                se.insert(ignore_permissions=True)
                se.submit()
                se_name = se.name
            except Exception as se_exc:
                se_error = str(se_exc)

        # -- Reuse shelving (Airport Returns) --
        # Reused stems are shelved back into stock at the Kapkolia cold store,
        # keeping their farm/greenhouse. A plain Material Receipt is used; the
        # original stock age is recorded on the QC (true ledger-age carry-through
        # would need the original batch, which returns don't carry).
        reuse_se_name  = None
        reuse_se_error = None
        if is_airport and reuse_stems > 0:
            try:
                reuse_wh = "Kapkolia Receiving Cold Store - KR"
                reuse_item = variety if (variety and frappe.db.exists("Item", variety)) else ""
                if not frappe.db.exists("Warehouse", reuse_wh):
                    reuse_se_error = "Warehouse %s not found" % reuse_wh
                elif not reuse_item:
                    reuse_se_error = "Variety item '%s' not found" % variety
                else:
                    reuse_uom = frappe.db.get_value("Item", reuse_item, "stock_uom") or "Stems"
                    r_item = {
                        "doctype":           "Stock Entry Detail",
                        "item_code":         reuse_item,
                        "qty":               reuse_stems,
                        "uom":               reuse_uom,
                        "t_warehouse":       reuse_wh,
                        "basic_rate":        0,
                        "farm":              farm if frappe.db.exists("Farm", farm) else "",
                        "custom_greenhouse": greenhouse,
                    }
                    r_doc = {
                        "doctype":          "Stock Entry",
                        "stock_entry_type": "Material Receipt",
                        "purpose":          "Material Receipt",
                        "company":          "Karen Roses",
                        "posting_date":     date_val,
                        "custom_packhouse_qc": doc.name,
                        "team":      team,
                        "items": [r_item]
                    }
                    if farm and frappe.db.exists("Farm", farm):
                        r_doc["farm"] = farm
                    r_se = frappe.get_doc(r_doc)
                    r_se.insert(ignore_permissions=True)
                    r_se.submit()
                    reuse_se_name = r_se.name
            except Exception as reuse_exc:
                reuse_se_error = str(reuse_exc)

        frappe.response["message"] = {
            "success":                  True,
            "name":                     doc.name,
            "overall_result":           overall_result,
            "stems_accepted":           stems_accepted,
            "stems_quarantined":        stems_quarantined,
            "stems_rejected":           stems_rejected,
            "stems_reused":             reuse_stems,
            "stems_replaced":           stems_replaced,
            "incomplete_stems":         incomplete_stems,
            "incomplete_bunches":       incomplete_bunches,
            "corrective_action_reports": created_cars,
            "car_errors":               car_errors,
            "stock_entry":              se_name,
            "stock_entry_error":        se_error,
            "reuse_stock_entry":        reuse_se_name,
            "reuse_stock_entry_error":  reuse_se_error
        }

    except Exception as e:
        frappe.log_error(str(e), "savePackhouseQC Error")
        frappe.response["message"] = {"success": False, "error": str(e)}


@frappe.whitelist()
def saveTrolleyData():
    try:
        data = frappe.form_dict.get("data")
        if not data:
            frappe.throw("No data provided")
        if isinstance(data, str):
            data = frappe.parse_json(data)
        trolley_id = data.get("trolley_id")
        buckets = data.get("buckets", [])
        if not trolley_id:
            frappe.throw("trolley_id is required")
        if not buckets:
            frappe.throw("No buckets provided")
        updated_count = 0
        shelf_removed_count = 0
        errors = []
        for bucket in buckets:
            opl_name = bucket.get("opl_name")
            bucket_id = bucket.get("bucket_id")
            if not opl_name or not bucket_id:
                errors.append({"bucket_id": bucket_id, "error": "Missing opl_name or bucket_id"})
                continue
            doc = frappe.get_doc("Order Pick List", opl_name)
            found = False
            for row in doc.locations:
                if row.custom_bucket == bucket_id:
                    row.custom_loaded_in_trolley = 1
                    row.custom_trolley_id = trolley_id
                    row.custom_awaiting_transfer = 0
                    found = True
                    break
            if found:
                doc.save(ignore_permissions=True)
                updated_count += 1

                # Remove bucket from shelf
                shelf_items = frappe.get_all(
                    "Shelf Item",
                    filters={"bucket_id": bucket_id},
                    fields=["name", "parent"],
                    limit=1
                )
                if shelf_items:
                    shl_log = frappe.db.get_value("Shelving Log", {"shelf_item": shelf_items[0].name, "reason": "Shelved"}, "name")
                    if shl_log:
                        frappe.db.set_value("Shelving Log", shl_log, {"reason": "Transferred (Trolley/Truck)", "removed_on": frappe.utils.now_datetime()}, update_modified=False)
                    shelf_name = shelf_items[0].parent
                    shelf_doc = frappe.get_doc("Shelf", shelf_name)
                    shelf_doc.items = [item for item in shelf_doc.items if item.bucket_id.lower() != bucket_id.lower()]
                    shelf_doc.save(ignore_permissions=True)
                    shelf_removed_count += 1
            else:
                errors.append({"bucket_id": bucket_id, "error": "Bucket not found in OPL"})
        frappe.db.commit()
        msg = str(updated_count) + " bucket(s) loaded to trolley " + str(trolley_id)
        if shelf_removed_count > 0:
            msg += ". " + str(shelf_removed_count) + " bucket(s) removed from shelf"
        response = {
            "status": "success",
            "message": msg,
            "updated_count": updated_count,
            "shelf_removed_count": shelf_removed_count,
        }
        if errors:
            response["errors"] = errors
            if updated_count == 0:
                response["status"] = "error"
                response["message"] = "No buckets were saved"
        frappe.response["message"] = response
    except Exception as e:
        frappe.log_error("saveTrolleyData error", str(e))
        frappe.response["message"] = {
            "status": "error",
            "message": str(e)
        }


@frappe.whitelist()
def setOfflineTrolleyFlags():
    # Frappe Server Script (Type: API), api_method = setOfflineTrolleyFlags
    # Additive-only sync for the offline Bucket Requests app. Marks specific
    # Pick List Item rows as loaded-in-trolley or in-transit WITHOUT submitting the
    # OPL (stays draft). Targets exact rows by their Pick List Item name (the app
    # stored pick_list_item_id at download), so bucket-QR reuse can never hit the
    # wrong OPL. Once a bucket is loaded/in-transit it has physically left its
    # shelf, so its Shelf Item is deleted too (mirrors the discard flow).
    # Payload: { "data": { "pli_ids": ["<name>", ...], "flag": "loaded" | "transit",
    #                       "truck": "<Vehicle name>" (optional, only used on loaded) } }


    def remove_bucket_from_shelf(bucket_id):
        """Delete the bucket's Shelf Item row(s) so it no longer shows on its shelf.
        Idempotent — no Shelf Item is a no-op. Returns the shelves touched."""
        removed = []
        if not bucket_id:
            return removed
        try:
            shelf_items = frappe.db.get_all(
                "Shelf Item",
                filters={"bucket_id": bucket_id},
                fields=["name", "parent"],
            )
            for item in shelf_items:
                frappe.delete_doc("Shelf Item", item.name, force=1)
                removed.append(item.parent)
                # Touch parent Shelf (keeps UI + modified in sync)
                frappe.db.set_value("Shelf", item.parent, "modified", frappe.utils.now())
        except Exception:
            # Shelf removal is not critical to the flag sync — never fail on it.
            pass
        return removed


    frappe.response["message"] = {"status": "error", "message": "Script failed"}

    try:
        data = frappe.request.get_json()
        if isinstance(data, dict) and "data" in data:
            data = data.get("data")
        data = data or {}

        pli_ids = data.get("pli_ids") or []
        flag = data.get("flag") or ""
        truck = data.get("truck") or ""

        field = None
        if flag == "loaded":
            field = "custom_loaded_in_trolley"
        elif flag == "transit":
            field = "custom_in_transit"

        if not field:
            frappe.response["message"] = {"status": "error", "message": "Invalid flag (expected 'loaded' or 'transit')."}
        elif not pli_ids:
            frappe.response["message"] = {"status": "error", "message": "No pick list items supplied."}
        else:
            updated = 0
            missing = 0
            removed_shelves = []
            i = 0
            while i < len(pli_ids):
                name = pli_ids[i]
                if name and frappe.db.exists("Pick List Item", name):
                    # A bucket may occupy MULTIPLE rows of the same OPL (mixed-box /
                    # split allocations); the app only holds ONE row per bucket, so
                    # flag every sibling row for that bucket in the same OPL — scanning
                    # the bucket once must update them all, or the OPL never completes.
                    parent = frappe.db.get_value("Pick List Item", name, "parent")
                    bucket = frappe.db.get_value("Pick List Item", name, "custom_bucket")
                    sibs = []
                    if parent and bucket:
                        sibs = frappe.get_all(
                            "Pick List Item",
                            filters={"parent": parent, "parenttype": "Order Pick List",
                                     "custom_bucket": bucket},
                            fields=["name"],
                        )
                    if not sibs:
                        sibs = [{"name": name}]
                    # Additive only: set the one flag to 1, leave everything else.
                    for sib in sibs:
                        frappe.db.set_value("Pick List Item", sib["name"], field, 1, update_modified=True)
                        # On load, also stamp the chosen truck onto the row (Data field).
                        if flag == "loaded" and truck:
                            frappe.db.set_value("Pick List Item", sib["name"], "custom_transit_truck", truck, update_modified=True)
                    # The bucket has left the shelf now it's on the trolley/truck —
                    # remove its Shelf Item so the shelf reflects reality.
                    removed_shelves = removed_shelves + remove_bucket_from_shelf(bucket)
                    updated = updated + 1
                else:
                    missing = missing + 1
                i = i + 1
            frappe.db.commit()
            frappe.response["message"] = {
                "status": "success",
                "updated": updated,
                "missing": missing,
                "flag": flag,
                "removed_from_shelves": list(set(removed_shelves)),
            }

    except Exception as e:
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitBatchQuality():
    # Server Script: submit_batch_quality   (API method: submitBatchQuality)
    #
    # Creates a Quality Reporting doc per (variety, action) for a scanned batch and,
    # for Quarantined / Rejected varieties, a Corrective Action Report.
    #
    # Design notes / why this version exists:
    #  - Bucket data (item_code, warehouse, greenhouse, farm, rate, cost center) is
    #    read from the PAYLOAD first. The frontend already has it from
    #    getBatchByBucket, so reporting no longer depends on a submitted "Receiving"
    #    Stock Entry existing. A Receiving entry is now only used (when present) to
    #    fill gaps and to source the warehouse for the quarantine/reject stock move.
    #  - quality_concerns is VARIETY-KEYED: { item_code: { param_key: { count } } }.
    #  - quality_parameters.parameter_name is a Link to "QC Parameters", so each
    #    incoming param_key is resolved back to a real QC Parameters name. Unknown
    #    keys are dropped instead of crashing the insert.
    #  - Every Stock Entry, Quality Reporting and CAR insert is wrapped in its own
    #    try/except. A failure in one (e.g. a bad CAR field) is logged and skipped
    #    so it can NEVER roll back the others. The single commit at the end then
    #    persists everything that succeeded. This is the root-cause fix for "after
    #    a change the Quality Report AND the CAR both stopped being generated":
    #    previously any exception before the final commit rolled back the whole
    #    transaction, including reports that had already been inserted.

    data = frappe.request.get_json()
    data = data.get("data", data)
    frappe.log_error(json.dumps(data, indent=2), "Submit Batch Quality Payload")

    batch_no          = data.get("batch_no", "")
    control_point     = data.get("control_point", "Intake")
    batch_decision    = data.get("batch_decision", "ACCEPT")
    quarantine_scope  = data.get("quarantine_scope", "buckets")
    checked_stems     = data.get("checked_stems_sampled", 0)
    solution_level    = data.get("solution_level_liters", 0)
    solution_hygiene  = data.get("solution_hygiene", "")
    chlorine_ppm      = data.get("chlorine_ppm", 0)
    solution_ph       = data.get("solution_ph", "")
    farm              = data.get("farm", "")
    company           = data.get("company", "")
    buckets_data      = data.get("buckets", [])
    concerns_by_variety = data.get("quality_concerns", {}) or {}
    stems_by_variety    = data.get("stems_checked_by_variety", {}) or {}

    if not batch_no:
        frappe.throw("Missing batch number")


    def normalize_action(action):
        action = (action or "").strip().upper()
        if action in ["ACCEPT", "ACCEPTED"]:
            return "Accepted"
        elif action in ["QUARANTINE", "QUARANTINED"]:
            return "Quarantined"
        elif action in ["REJECT", "REJECTED"]:
            return "Rejected"
        return ""


    # Normalise a parameter label the same way the frontend does:
    #   trim -> drop punctuation (keep word chars + spaces) -> spaces to "_" -> lower
    def norm_param(s):
        s = (s or "").strip().lower()
        out = []
        prev_us = False
        for ch in s:
            if ch.isalnum() or ch == "_":
                out.append(ch)
                prev_us = (ch == "_")
            elif ch.isspace():
                if not prev_us:
                    out.append("_")
                    prev_us = True
            # any other punctuation is dropped
        return "".join(out)


    # Map normalised param key -> canonical QC Parameters name (the Link target)
    qc_param_map = {}
    for p in frappe.get_all("QC Parameters", fields=["name", "parameter"]):
        if p.get("parameter"):
            qc_param_map[norm_param(p.get("parameter"))] = p.get("name")
        qc_param_map[norm_param(p.get("name"))] = p.get("name")

    # CAR.control_point is a Select with a DIFFERENT option set than the QC form,
    # so map the incoming control point to a valid CAR option (default Intake).
    CAR_CP_MAP = {
        "intake": "Intake",
        "coldroom": "Cold Room",
        "cold room": "Cold Room",
        "grading": "Grading",
        "packhouse": "Packing",
        "packing": "Packing",
        "dispatch": "Dispatch",
        "field": "Intake",
    }
    car_control_point = CAR_CP_MAP.get((control_point or "").strip().lower(), "Intake")

    # Quality Reporting Select guards (an out-of-range value would fail validation).
    VALID_PH = {"3.0", "3.5", "4.0", "4.5", "5.0", "5.5", "6.0", "6.5"}
    solution_ph_val = str(solution_ph) if str(solution_ph) in VALID_PH else ""
    solution_hygiene_val = solution_hygiene if solution_hygiene in ("Clean", "Not Clean") else ""

    WAREHOUSE_CONFIG = {
        "rejected": "Kapkolia Receiving Quality B - KR",
        "quarantined": "Kapkolia Receiving Quarantined - KR",
    }
    STOCK_ENTRY_TYPES = {
        "quarantined": "Receiving Quarantined",
        "rejected": "Quarantine Rejects",
    }

    created_reports = []
    stock_entries = []
    corrective_action_reports = []
    skipped_buckets = []
    stock_move_errors = []
    total_accepted_stems = 0
    total_quarantined_stems = 0
    total_rejected_stems = 0


    def extract_time_string(pt_val):
        if not pt_val:
            return ""
        try:
            raw = str(pt_val)
            if "," in raw:
                raw = raw.split(",")[1].strip()
            if "." in raw:
                raw = raw.split(".")[0]
            parts = raw.strip().split(":")
            if len(parts) == 3:
                return str(parts[0]).zfill(2) + ":" + str(parts[1]).zfill(2) + ":" + str(parts[2]).zfill(2)
            if len(parts) == 2:
                return str(parts[0]).zfill(2) + ":" + str(parts[1]).zfill(2) + ":00"
            return ""
        except Exception:
            return ""


    def get_first_time(greenhouse, entry_type):
        if not greenhouse:
            return ""
        today = frappe.utils.nowdate()
        entry = frappe.db.sql("""
            SELECT posting_time
            FROM `tabStock Entry`
            WHERE custom_greenhouse = %s
                AND stock_entry_type = %s
                AND posting_date = %s
                AND docstatus = 1
            ORDER BY posting_time ASC
            LIMIT 1
        """, (greenhouse, entry_type, today), as_dict=1)
        if entry:
            return extract_time_string(entry[0].posting_time)
        return ""


    def compute_transit_time(harvest_time_str, arrival_time_str):
        if not harvest_time_str or not arrival_time_str:
            return ""
        try:
            today = frappe.utils.nowdate()
            harvest_dt = frappe.utils.get_datetime(str(today) + " " + harvest_time_str)
            arrival_dt = frappe.utils.get_datetime(str(today) + " " + arrival_time_str)
            if arrival_dt > harvest_dt:
                diff = arrival_dt - harvest_dt
                total_secs = int(diff.total_seconds())
                t_hours = total_secs // 3600
                t_minutes = (total_secs % 3600) // 60
                return str(t_hours) + "h " + str(t_minutes).zfill(2) + "m"
        except Exception:
            pass
        return ""


    # Resolve a farm's Unit Manager: first (alphabetical) enabled User holding the
    # per-farm role profile "Farm Manager {farm}". Cached per farm for this run.
    farm_unit_manager_cache = {}

    def farm_unit_manager(farm_name):
        fname = (farm_name or "").strip()
        if not fname:
            return ""
        if fname in farm_unit_manager_cache:
            return farm_unit_manager_cache[fname]
        result = ""
        try:
            rows = frappe.get_all(
                "User",
                filters=[["enabled", "=", 1], ["role_profiles.role_profile", "=", "Farm Manager " + fname]],
                fields=["name"],
                order_by="name asc",
                limit_page_length=1,
            )
            if rows:
                result = rows[0].get("name") or ""
        except Exception:
            result = ""
        farm_unit_manager_cache[fname] = result
        return result

    unit_manager = ""


    def should_create_car(greenhouse, variety):
        if not variety or not greenhouse:
            return True
        existing_cars = frappe.db.sql("""
            SELECT name, status, target_date, actual_completion_date
            FROM `tabCorrective Action Report`
            WHERE variety = %s
                AND custom_greenhouse = %s
                AND status IN ('Pending', 'WIP')
            ORDER BY creation DESC
            LIMIT 1
        """, (variety, greenhouse), as_dict=1)
        if not existing_cars:
            return True
        car = existing_cars[0]
        today = frappe.utils.getdate(frappe.utils.nowdate())
        target = frappe.utils.getdate(car.target_date) if car.target_date else None
        if target and target >= today:
            return False
        if target and target < today and not car.actual_completion_date:
            return True
        if not target:
            return False
        return True


    def build_child_entries(item_code, action):
        """Per-variety quality_parameters rows, resolving each param key to a valid
        QC Parameters Link. Unknown keys are skipped so the insert never crashes."""
        entries = []
        concerns = concerns_by_variety.get(item_code, {}) or {}
        if not isinstance(concerns, dict):
            return entries
        for param_key, details in concerns.items():
            if not isinstance(details, dict):
                continue
            count = int(details.get("count") or 0)
            if count <= 0:
                continue
            real_name = qc_param_map.get(norm_param(param_key))
            if not real_name:
                continue
            entries.append({
                "parameter_name": real_name,
                "count": count,
                "action": action if action in ("Quarantined", "Accepted", "Rejected", "Monitor") else "Monitor",
                "photo": details.get("photo", "") or "",
            })
        return entries


    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 1: process each bucket - accumulate per variety + optional stock move
    # ═══════════════════════════════════════════════════════════════════════════
    variety_groups = {}
    variety_stems_received = {}
    variety_greenhouse = {}
    variety_farm = {}

    for bucket in buckets_data:
        bucket_id = (bucket.get("bucket_id") or "").upper()
        bucket_stems = int(bucket.get("stems") or 0)
        bucket_selected = bucket.get("selected", False)

        if quarantine_scope == "buckets" and not bucket_selected:
            effective_action = "Accepted"
        elif quarantine_scope == "batch":
            effective_action = normalize_action(batch_decision)
        else:
            effective_action = normalize_action(bucket.get("action", batch_decision))

        # ── Bucket details from the payload (preferred) ──
        item_code = bucket.get("item_code") or ""
        item_name = bucket.get("item_name") or ""
        source_warehouse = bucket.get("warehouse") or ""
        basic_rate = bucket.get("basic_rate") or 0
        cost_center = bucket.get("cost_center") or ""
        greenhouse = bucket.get("greenhouse") or ""
        bucket_farm = bucket.get("farm") or farm

        # ── Optional Receiving Stock Entry: fill gaps + source for stock movement ──
        receiving_doc = None
        try:
            se_name = bucket.get("stock_entry") or ""
            if se_name and frappe.db.exists("Stock Entry", se_name):
                receiving_doc = frappe.get_doc("Stock Entry", se_name)
            else:
                receiving = frappe.db.sql("""
                    SELECT name FROM `tabStock Entry`
                    WHERE TRIM(LOWER(custom_bucket_id)) = %s
                        AND stock_entry_type = 'Receiving'
                        AND docstatus = 1
                    ORDER BY creation DESC LIMIT 1
                """, (bucket_id.lower(),), as_dict=1)
                if receiving:
                    receiving_doc = frappe.get_doc("Stock Entry", receiving[0].name)
        except Exception:
            receiving_doc = None

        if receiving_doc and receiving_doc.items:
            first_item = receiving_doc.items[0]
            if not item_code:
                item_code = first_item.item_code
            if not item_name:
                item_name = first_item.item_name
            if not source_warehouse:
                source_warehouse = first_item.t_warehouse
            if not basic_rate:
                basic_rate = first_item.basic_rate or 0
            if not cost_center:
                cost_center = first_item.cost_center or ""
            if not greenhouse:
                greenhouse = receiving_doc.custom_greenhouse or ""
            if not bucket_farm:
                bucket_farm = (receiving_doc.get("farm") or farm)

        # Without a variety there is nothing to report on.
        if not item_code:
            skipped_buckets.append(bucket_id)
            continue

        variety_stems_received[item_code] = variety_stems_received.get(item_code, 0) + bucket_stems
        if item_code not in variety_greenhouse and greenhouse:
            variety_greenhouse[item_code] = greenhouse
        if item_code not in variety_farm and bucket_farm:
            variety_farm[item_code] = bucket_farm

        effective_rejected = int(bucket.get("rejected_stems", 0) or 0)

        group_key = item_code + "|" + effective_action
        if group_key not in variety_groups:
            variety_groups[group_key] = {
                "item_code": item_code,
                "farm": bucket_farm,
                "greenhouse": greenhouse,
                "effective_action": effective_action,
                "total_quarantined_stems": 0,
                "total_accepted_stems": 0,
                "total_rejected_stems": 0,
                "bucket_count": 0,
            }
        group = variety_groups[group_key]
        group["bucket_count"] = group["bucket_count"] + 1
        if not group["greenhouse"] and greenhouse:
            group["greenhouse"] = greenhouse

        if effective_action == "Accepted":
            group["total_accepted_stems"] = group["total_accepted_stems"] + (bucket_stems - effective_rejected)
            group["total_rejected_stems"] = group["total_rejected_stems"] + effective_rejected
        elif effective_action == "Quarantined":
            group["total_quarantined_stems"] = group["total_quarantined_stems"] + bucket_stems
            group["total_rejected_stems"] = group["total_rejected_stems"] + effective_rejected
        elif effective_action == "Rejected":
            group["total_rejected_stems"] = group["total_rejected_stems"] + bucket_stems

        # ── Stock movement (quarantine / reject only). Best-effort: never blocks. ──
        if effective_action in ("Quarantined", "Rejected") and receiving_doc and source_warehouse:
            target_key = "quarantined" if effective_action == "Quarantined" else "rejected"
            target_warehouse = WAREHOUSE_CONFIG[target_key]
            entry_type = STOCK_ENTRY_TYPES[target_key]
            qty_to_move = bucket_stems if effective_action == "Rejected" else (bucket_stems - effective_rejected)
            if qty_to_move > 0:
                try:
                    se_data = {
                        "doctype": "Stock Entry",
                        "stock_entry_type": entry_type,
                        "purpose": "Material Transfer",
                        "custom_bucket_id": bucket_id.lower(),
                        "custom_harvest_batch_no": (receiving_doc.get("custom_harvest_batch_no") or ""),
                        "custom_receiving_batch_id": receiving_doc.custom_receiving_batch_id or "",
                        "company": company or receiving_doc.company or "",
                        "posting_date": frappe.utils.nowdate(),
                        "posting_time": frappe.utils.nowtime(),
                        "set_posting_time": 1,
                        "items": [{
                            "item_code": item_code,
                            "item_name": item_name,
                            "qty": qty_to_move,
                            "transfer_qty": qty_to_move,
                            "uom": "Stems",
                            "stock_uom": "Stems",
                            "conversion_factor": 1.0,
                            "s_warehouse": source_warehouse,
                            "t_warehouse": target_warehouse,
                            "cost_center": cost_center,
                            "basic_rate": basic_rate,
                            "basic_amount": qty_to_move * basic_rate,
                            "allow_zero_valuation_rate": 1,
                        }]
                    }
                    continuity_fields = [
                        "custom_farm", "custom_location", "custom_business_unit",
                        "custom_employee", "custom_employee_name", "custom_greenhouse",
                        "custom_harvester", "custom_stem_length", "custom_graded_by",
                        "custom_grader_payroll_number", "custom_biometric_verified",
                    ]
                    for field in continuity_fields:
                        if receiving_doc.get(field):
                            se_data[field] = receiving_doc.get(field)
                    se_doc = frappe.get_doc(se_data)
                    # Skip link checks so a missing continuity master (e.g. Harvester)
                    # never aborts the quarantine/reject stock move; mirrors the QR/CAR inserts.
                    se_doc.flags.ignore_links = True
                    se_doc.insert(ignore_permissions=True)
                    se_doc.submit()
                    stock_entries.append(se_doc.name)

                    # Only update the Receiving document when we create a "Quarantine Rejects" entry
                    if effective_action == "Rejected" and receiving_doc:
                        try:
                            # Update the Receiving document with reject information
                            receiving_doc.custom_total_rejected_qty = (receiving_doc.custom_total_rejected_qty or 0) + qty_to_move
                            receiving_doc.custom_rejection_stock_entry = se_doc.name
                            receiving_doc.custom_rejection_date = frappe.utils.nowdate()
                            receiving_doc.save(ignore_permissions=True)

                            frappe.log_error(
                                f"Updated Receiving {receiving_doc.name} with reject qty {qty_to_move} from {se_doc.name}", 
                                "Reject Population Success"
                            )
                        except Exception as pop_err:
                            frappe.log_error(
                                str(pop_err), 
                                f"Failed to update Receiving doc with rejects for bucket {bucket_id}"
                            )
                            # end of new addition (Quarantine rejects)

                except Exception as e:
                    stock_move_errors.append(bucket_id + ": " + str(e))
                    frappe.log_error(str(e), "Submit Batch Quality - stock move " + bucket_id)


    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 2: one Quality Reporting per (variety, action) + CAR for Q / R
    # ═══════════════════════════════════════════════════════════════════════════
    for group_key, group in variety_groups.items():
        item_code = group["item_code"]
        effective_action = group["effective_action"]
        greenhouse = group["greenhouse"] or variety_greenhouse.get(item_code, "")
        group_farm = group["farm"] or variety_farm.get(item_code, "") or farm
        group_unit_manager = farm_unit_manager(group_farm)

        gh_val = greenhouse if (greenhouse and frappe.db.exists("Warehouse", greenhouse)) else None
        farm_val = group_farm if (group_farm and frappe.db.exists("Farm", group_farm)) else None
        variety_val = item_code if frappe.db.exists("Item", item_code) else None

        harvest_time = get_first_time(greenhouse, "Harvesting")
        arrival_time = get_first_time(greenhouse, "Receiving")
        transit_time = compute_transit_time(harvest_time, arrival_time)

        quarantined_stems = group["total_quarantined_stems"] if effective_action == "Quarantined" else 0
        report_quality_params = build_child_entries(item_code, effective_action) if effective_action in ("Quarantined", "Rejected") else []
        stems_checked_for_variety = int(stems_by_variety.get(item_code) or checked_stems or 0)

        intake_data = {
            "doctype": "Quality Reporting",
            "control_point": control_point,
            "farm": farm_val,
            "ghouse": gh_val,
            "variety": variety_val,
            "harvest_time": harvest_time,
            "arrival_time": arrival_time,
            "transit_time": transit_time,
            "stems_received": variety_stems_received.get(item_code, 0),
            "stems_checked": stems_checked_for_variety,
            "stems_per_bucket": 0,
            "solution_level": solution_level,
            "solution_hygeine": solution_hygiene_val,
            "solution_ph": solution_ph_val,
            "chlorine_ppm": chlorine_ppm,
            "control_action": effective_action,
            "quarantined_stems": quarantined_stems,
            "quality_parameters": report_quality_params,
            "prepared_by": frappe.session.user,
            "unit_manager": group_unit_manager or None,
        }

        qr_prefix = str(control_point) + "-" + str(group_farm or farm or "NA") + "-"
        existing_qr = frappe.get_all("Quality Reporting", filters=[["name", "like", qr_prefix + "%"]], fields=["name"])
        qr_seq = len(existing_qr) + 1
        qr_name = qr_prefix + str(qr_seq).zfill(3)
        while frappe.db.exists("Quality Reporting", qr_name):
            qr_seq = qr_seq + 1
            qr_name = qr_prefix + str(qr_seq).zfill(3)

        try:
            intake_doc = frappe.get_doc(intake_data)
            intake_doc.name = qr_name
            intake_doc.flags.name_set = True
            intake_doc.insert(ignore_permissions=True)
            created_reports.append(intake_doc.name)
        except Exception as e:
            frappe.log_error(str(e), "Submit Batch Quality - QR " + item_code + "/" + effective_action)
            continue

        # ── Corrective Action Report (isolated: never rolls back the report) ──
        if effective_action in ("Quarantined", "Rejected") and variety_val:
            try:
                if should_create_car(greenhouse, item_code):
                    # Build issue text from quality concerns for this variety
                    issue_parts = []
                    variety_concerns = concerns_by_variety.get(item_code, {}) or {}
                    if isinstance(variety_concerns, dict):
                        for param_key, param_details in variety_concerns.items():
                            if isinstance(param_details, dict):
                                param_count = int(param_details.get("count") or 0)
                                if param_count > 0:
                                    issue_parts.append(str(param_key).replace("_", " ").title() + " (" + str(param_count) + ")")
                            elif param_details:
                                issue_parts.append(str(param_key).replace("_", " ").title())
                    issue_text = ", ".join(issue_parts) if issue_parts else ""

                    car_data = {
                        "doctype": "Corrective Action Report",
                        "variety": variety_val,
                        "date_of_incident": frappe.utils.nowdate(),
                        "requested_by": frappe.session.user,
                        "control_point": car_control_point,
                        "assigned_to": group_unit_manager or frappe.session.user,
                        "status": "Pending",
                        "issue": issue_text,
                        "root_cause": "To be determined",
                        "corrective_action_plan": "To be determined",
                        "target_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
                    }
                    if gh_val:
                        car_data["custom_greenhouse"] = gh_val
                    car_doc = frappe.get_doc(car_data)
                    car_doc.insert(ignore_permissions=True)
                    corrective_action_reports.append(car_doc.name)
            except Exception as e:
                frappe.log_error(str(e), "Submit Batch Quality - CAR " + item_code)

        total_accepted_stems += group["total_accepted_stems"]
        total_quarantined_stems += group["total_quarantined_stems"]
        total_rejected_stems += group["total_rejected_stems"]

    frappe.db.commit()

    frappe.response["status"] = "success"
    frappe.response["message"] = (
        "Batch " + batch_no + ": " + str(len(created_reports)) + " Quality Reports created. "
        + "Accepted: " + str(total_accepted_stems) + ", Quarantined: " + str(total_quarantined_stems)
        + ", Rejected: " + str(total_rejected_stems) + " stems. "
        + str(len(corrective_action_reports)) + " Corrective Action Report(s) raised."
    )
    frappe.response["reports"] = created_reports
    frappe.response["stock_entries"] = stock_entries
    frappe.response["corrective_action_reports"] = corrective_action_reports
    frappe.response["skipped_buckets"] = skipped_buckets
    frappe.response["stock_move_errors"] = stock_move_errors
    frappe.response["summary"] = {
        "total_accepted": total_accepted_stems,
        "total_quarantined": total_quarantined_stems,
        "total_rejected": total_rejected_stems,
        "varieties_processed": len(variety_groups),
        "buckets_processed": sum(g["bucket_count"] for g in variety_groups.values()),
        "buckets_skipped": len(skipped_buckets),
        "corrective_actions_raised": len(corrective_action_reports),
    }


def _submit_batch_quality_impl():
    # Server Script: submit_batch_quality   (API method: submitBatchQuality)
    #
    # Creates a Quality Reporting doc per (variety, action) for a scanned batch and,
    # for Quarantined / Rejected varieties, a Corrective Action Report.
    #
    # Design notes / why this version exists:
    #  - Bucket data (item_code, warehouse, greenhouse, farm, rate, cost center) is
    #    read from the PAYLOAD first. The frontend already has it from
    #    getBatchByBucket, so reporting no longer depends on a submitted "Receiving"
    #    Stock Entry existing. A Receiving entry is now only used (when present) to
    #    fill gaps and to source the warehouse for the quarantine/reject stock move.
    #  - quality_concerns is VARIETY-KEYED: { item_code: { param_key: { count } } }.
    #  - quality_parameters.parameter_name is a Link to "QC Parameters", so each
    #    incoming param_key is resolved back to a real QC Parameters name. Unknown
    #    keys are dropped instead of crashing the insert.
    #  - Every Stock Entry, Quality Reporting and CAR insert is wrapped in its own
    #    try/except. A failure in one (e.g. a bad CAR field) is logged and skipped
    #    so it can NEVER roll back the others. The single commit at the end then
    #    persists everything that succeeded. This is the root-cause fix for "after
    #    a change the Quality Report AND the CAR both stopped being generated":
    #    previously any exception before the final commit rolled back the whole
    #    transaction, including reports that had already been inserted.

    data = frappe.request.get_json()
    data = data.get("data", data)
    frappe.log_error(json.dumps(data, indent=2), "Submit Batch Quality Payload")

    batch_no          = data.get("batch_no", "")
    control_point     = data.get("control_point", "Intake")
    batch_decision    = data.get("batch_decision", "ACCEPT")
    quarantine_scope  = data.get("quarantine_scope", "buckets")
    checked_stems     = data.get("checked_stems_sampled", 0)
    solution_level    = data.get("solution_level_liters", 0)
    solution_hygiene  = data.get("solution_hygiene", "")
    chlorine_ppm      = data.get("chlorine_ppm", 0)
    solution_ph       = data.get("solution_ph", "")
    farm              = data.get("farm", "")
    company           = data.get("company", "")
    buckets_data      = data.get("buckets", [])
    concerns_by_variety = data.get("quality_concerns", {}) or {}
    stems_by_variety    = data.get("stems_checked_by_variety", {}) or {}

    if not batch_no:
        # Return the error in the same shape the client reads for success
        # (top-level `status`), so a failure is never mistaken for a submit.
        frappe.response["status"] = "error"
        frappe.response["http_status_code"] = 400
        frappe.response["message"] = "Missing batch number"
        return


    def normalize_action(action):
        action = (action or "").strip().upper()
        if action in ["ACCEPT", "ACCEPTED"]:
            return "Accepted"
        elif action in ["QUARANTINE", "QUARANTINED"]:
            return "Quarantined"
        elif action in ["REJECT", "REJECTED"]:
            return "Rejected"
        return ""


    # Normalise a parameter label the same way the frontend does:
    #   trim -> drop punctuation (keep word chars + spaces) -> spaces to "_" -> lower
    def norm_param(s):
        s = (s or "").strip().lower()
        out = []
        prev_us = False
        for ch in s:
            if ch.isalnum() or ch == "_":
                out.append(ch)
                prev_us = (ch == "_")
            elif ch.isspace():
                if not prev_us:
                    out.append("_")
                    prev_us = True
            # any other punctuation is dropped
        return "".join(out)


    # Map normalised param key -> canonical QC Parameters name (the Link target)
    qc_param_map = {}
    for p in frappe.get_all("QC Parameters", fields=["name", "parameter"]):
        if p.get("parameter"):
            qc_param_map[norm_param(p.get("parameter"))] = p.get("name")
        qc_param_map[norm_param(p.get("name"))] = p.get("name")

    # CAR.control_point is a Select with a DIFFERENT option set than the QC form,
    # so map the incoming control point to a valid CAR option (default Intake).
    CAR_CP_MAP = {
        "intake": "Intake",
        "coldroom": "Cold Room",
        "cold room": "Cold Room",
        "grading": "Grading",
        "packhouse": "Packing",
        "packing": "Packing",
        "dispatch": "Dispatch",
        "field": "Intake",
    }
    car_control_point = CAR_CP_MAP.get((control_point or "").strip().lower(), "Intake")

    # Quality Reporting Select guards (an out-of-range value would fail validation).
    VALID_PH = {"3.0", "3.5", "4.0", "4.5", "5.0", "5.5", "6.0", "6.5"}
    solution_ph_val = str(solution_ph) if str(solution_ph) in VALID_PH else ""
    solution_hygiene_val = solution_hygiene if solution_hygiene in ("Clean", "Not Clean") else ""

    # Quarantine/reject target warehouses follow the same "{Farm} {Purpose} - {abbr}"
    # convention as every other farm warehouse (e.g. "Kapkolia Receiving Cold Store
    # - UFL"). This used to be a static dict hardcoded to "Kapkolia" + "- KR", which
    # only ever worked for that one farm on that one company — every other farm (or
    # any company not abbreviated "KR") silently failed to find its target
    # warehouse. Resolved per-bucket from that bucket's own farm below.
    STOCK_ENTRY_TYPES = {
        "quarantined": "Receiving Quarantined",
        "rejected": "Quarantine Rejects",
    }

    def warehouse_for(target_key, bucket_farm, company):
        abbr = frappe.db.get_value("Company", company, "abbr") if company else None
        if not (bucket_farm and abbr):
            return None
        suffix = "Receiving Quarantined" if target_key == "quarantined" else "Rejects"
        return str(bucket_farm) + " " + suffix + " - " + str(abbr)

    created_reports = []
    stock_entries = []
    corrective_action_reports = []
    skipped_buckets = []
    stock_move_errors = []
    total_accepted_stems = 0
    total_quarantined_stems = 0
    total_rejected_stems = 0


    def extract_time_string(pt_val):
        if not pt_val:
            return ""
        try:
            raw = str(pt_val)
            if "," in raw:
                raw = raw.split(",")[1].strip()
            if "." in raw:
                raw = raw.split(".")[0]
            parts = raw.strip().split(":")
            if len(parts) == 3:
                return str(parts[0]).zfill(2) + ":" + str(parts[1]).zfill(2) + ":" + str(parts[2]).zfill(2)
            if len(parts) == 2:
                return str(parts[0]).zfill(2) + ":" + str(parts[1]).zfill(2) + ":00"
            return ""
        except Exception:
            return ""


    def get_first_time(greenhouse, entry_type):
        if not greenhouse:
            return ""
        today = frappe.utils.nowdate()
        entry = frappe.db.sql("""
            SELECT posting_time
            FROM `tabStock Entry`
            WHERE custom_greenhouse = %s
                AND stock_entry_type = %s
                AND posting_date = %s
                AND docstatus = 1
            ORDER BY posting_time ASC
            LIMIT 1
        """, (greenhouse, entry_type, today), as_dict=1)
        if entry:
            return extract_time_string(entry[0].posting_time)
        return ""


    def compute_transit_time(harvest_time_str, arrival_time_str):
        if not harvest_time_str or not arrival_time_str:
            return ""
        try:
            today = frappe.utils.nowdate()
            harvest_dt = frappe.utils.get_datetime(str(today) + " " + harvest_time_str)
            arrival_dt = frappe.utils.get_datetime(str(today) + " " + arrival_time_str)
            if arrival_dt > harvest_dt:
                diff = arrival_dt - harvest_dt
                total_secs = int(diff.total_seconds())
                t_hours = total_secs // 3600
                t_minutes = (total_secs % 3600) // 60
                return str(t_hours) + "h " + str(t_minutes).zfill(2) + "m"
        except Exception:
            pass
        return ""


    # Unit manager = the active Section Head assigned to this farm (Employee whose
    # custom_farm is this farm and designation is "Section Head"). Quality
    # Reporting.unit_manager is Link -> Employee (matching Farm.unit_manager's own
    # design), so it's stored as the Employee reference directly — displayed by
    # name, not payroll number, and populated whether or not that Employee has a
    # linked User account. Corrective Action Report.assigned_to is a hard
    # Link -> User (Frappe's assignment/ToDo mechanism requires a User), so we
    # separately track the Employee's user_id for that, best-effort. If the farm
    # has no Section Head configured, block with a clear instruction so quality is
    # never submitted without an accountable unit manager.
    unit_manager = ""       # Employee reference -> Quality Reporting.unit_manager
    unit_manager_user = ""  # User reference (if any) -> CAR.assigned_to
    if farm:
        mgr = frappe.db.get_value(
            "Employee",
            {"custom_farm": farm, "designation": "Section Head", "status": "Active"},
            ["name", "employee_name", "user_id"],
            as_dict=True,
        )
        if not mgr:
            # Return the error in the same shape the client reads for success
            # (top-level `status`), so a failure is never mistaken for a submit.
            frappe.response["status"] = "error"
            frappe.response["http_status_code"] = 400
            frappe.response["message"] = _("Please contact your IT administrator to add the unit manager for farm {0}").format(farm)
            return
        unit_manager = mgr.name or ""
        unit_manager_user = mgr.user_id or ""


    def should_create_car(car_farm, variety):
        # Corrective Action Report has no greenhouse field, only `farm` — dedupe
        # at that granularity (the field that actually exists and gets stored).
        if not variety or not car_farm:
            return True
        existing_cars = frappe.db.sql("""
            SELECT name, status, target_date, actual_completion_date
            FROM `tabCorrective Action Report`
            WHERE variety = %s
                AND farm = %s
                AND status IN ('Pending', 'WIP')
            ORDER BY creation DESC
            LIMIT 1
        """, (variety, car_farm), as_dict=1)
        if not existing_cars:
            return True
        car = existing_cars[0]
        today = frappe.utils.getdate(frappe.utils.nowdate())
        target = frappe.utils.getdate(car.target_date) if car.target_date else None
        if target and target >= today:
            return False
        if target and target < today and not car.actual_completion_date:
            return True
        if not target:
            return False
        return True


    def build_child_entries(item_code, action):
        """Per-variety quality_parameters rows, resolving each param key to a valid
        QC Parameters Link. Unknown keys are skipped so the insert never crashes."""
        entries = []
        concerns = concerns_by_variety.get(item_code, {}) or {}
        if not isinstance(concerns, dict):
            return entries
        for param_key, details in concerns.items():
            if not isinstance(details, dict):
                continue
            count = int(details.get("count") or 0)
            if count <= 0:
                continue
            real_name = qc_param_map.get(norm_param(param_key))
            if not real_name:
                continue
            entries.append({
                "parameter_name": real_name,
                "count": count,
                "action": action if action in ("Quarantined", "Accepted", "Rejected", "Monitor") else "Monitor",
                "photo": details.get("photo", "") or "",
            })
        return entries


    # PHASE 1: process each bucket - accumulate per variety + optional stock move
    variety_groups = {}
    variety_stems_received = {}
    variety_greenhouse = {}
    variety_farm = {}

    for bucket in buckets_data:
        bucket_id = (bucket.get("bucket_id") or "").upper()
        bucket_stems = int(bucket.get("stems") or 0)
        bucket_selected = bucket.get("selected", False)

        if quarantine_scope == "buckets" and not bucket_selected:
            effective_action = "Accepted"
        elif quarantine_scope == "batch":
            effective_action = normalize_action(batch_decision)
        else:
            effective_action = normalize_action(bucket.get("action", batch_decision))

        # Bucket details from the payload (preferred)
        item_code = bucket.get("item_code") or ""
        item_name = bucket.get("item_name") or ""
        source_warehouse = bucket.get("warehouse") or ""
        basic_rate = bucket.get("basic_rate") or 0
        cost_center = bucket.get("cost_center") or ""
        greenhouse = bucket.get("greenhouse") or ""
        bucket_farm = bucket.get("farm") or farm

        # Optional Receiving Stock Entry: fill gaps + source for stock movement
        receiving_doc = None
        try:
            se_name = bucket.get("stock_entry") or ""
            if se_name and frappe.db.exists("Stock Entry", se_name):
                receiving_doc = frappe.get_doc("Stock Entry", se_name)
            else:
                receiving = frappe.db.sql("""
                    SELECT name FROM `tabStock Entry`
                    WHERE TRIM(LOWER(custom_bucket_id)) = %s
                        AND stock_entry_type = 'Receiving'
                        AND docstatus = 1
                    ORDER BY creation DESC LIMIT 1
                """, (bucket_id.lower(),), as_dict=1)
                if receiving:
                    receiving_doc = frappe.get_doc("Stock Entry", receiving[0].name)
        except Exception:
            receiving_doc = None

        if receiving_doc and receiving_doc.items:
            first_item = receiving_doc.items[0]
            if not item_code:
                item_code = first_item.item_code
            if not item_name:
                item_name = first_item.item_name
            if not source_warehouse:
                source_warehouse = first_item.t_warehouse
            if not basic_rate:
                basic_rate = first_item.basic_rate or 0
            if not cost_center:
                cost_center = first_item.cost_center or ""
            if not greenhouse:
                greenhouse = receiving_doc.custom_greenhouse or ""
            if not bucket_farm:
                bucket_farm = receiving_doc.farm or farm

        # Without a variety there is nothing to report on.
        if not item_code:
            skipped_buckets.append(bucket_id)
            continue

        variety_stems_received[item_code] = variety_stems_received.get(item_code, 0) + bucket_stems
        if item_code not in variety_greenhouse and greenhouse:
            variety_greenhouse[item_code] = greenhouse
        if item_code not in variety_farm and bucket_farm:
            variety_farm[item_code] = bucket_farm

        effective_rejected = int(bucket.get("rejected_stems", 0) or 0)

        group_key = item_code + "|" + effective_action
        if group_key not in variety_groups:
            variety_groups[group_key] = {
                "item_code": item_code,
                "farm": bucket_farm,
                "greenhouse": greenhouse,
                "effective_action": effective_action,
                "total_quarantined_stems": 0,
                "total_accepted_stems": 0,
                "total_rejected_stems": 0,
                "bucket_count": 0,
            }
        group = variety_groups[group_key]
        group["bucket_count"] = group["bucket_count"] + 1
        if not group["greenhouse"] and greenhouse:
            group["greenhouse"] = greenhouse

        if effective_action == "Accepted":
            group["total_accepted_stems"] = group["total_accepted_stems"] + (bucket_stems - effective_rejected)
            group["total_rejected_stems"] = group["total_rejected_stems"] + effective_rejected
        elif effective_action == "Quarantined":
            group["total_quarantined_stems"] = group["total_quarantined_stems"] + bucket_stems
            group["total_rejected_stems"] = group["total_rejected_stems"] + effective_rejected
        elif effective_action == "Rejected":
            group["total_rejected_stems"] = group["total_rejected_stems"] + bucket_stems

        # Stock movement (quarantine / reject only). Best-effort: never blocks.
        if effective_action in ("Quarantined", "Rejected") and receiving_doc and source_warehouse:
            target_key = "quarantined" if effective_action == "Quarantined" else "rejected"
            target_warehouse = warehouse_for(target_key, bucket_farm, company or receiving_doc.company)
            if not target_warehouse:
                frappe.log_error(
                    "No farm/company to resolve the " + target_key + " warehouse for bucket " + bucket_id,
                    "Submit Batch Quality - stock move " + bucket_id,
                )
            entry_type = STOCK_ENTRY_TYPES[target_key]
            qty_to_move = bucket_stems if effective_action == "Rejected" else (bucket_stems - effective_rejected)
            if qty_to_move > 0:
                try:
                    se_data = {
                        "doctype": "Stock Entry",
                        "stock_entry_type": entry_type,
                        "purpose": "Material Transfer",
                        "custom_bucket_id": bucket_id.lower(),
                        "custom_receiving_batch_id": receiving_doc.custom_receiving_batch_id or "",
                        "company": company or receiving_doc.company or "",
                        "posting_date": frappe.utils.nowdate(),
                        "posting_time": frappe.utils.nowtime(),
                        "set_posting_time": 1,
                        "items": [{
                            "item_code": item_code,
                            "item_name": item_name,
                            "qty": qty_to_move,
                            "transfer_qty": qty_to_move,
                            "uom": "Stems",
                            "stock_uom": "Stems",
                            "conversion_factor": 1.0,
                            "s_warehouse": source_warehouse,
                            "t_warehouse": target_warehouse,
                            "cost_center": cost_center,
                            "basic_rate": basic_rate,
                            "basic_amount": qty_to_move * basic_rate,
                            "allow_zero_valuation_rate": 1,
                        }]
                    }
                    continuity_fields = [
                        "farm", "custom_location", "custom_business_unit",
                        "custom_greenhouse", "custom_harvester", "custom_stem_length",
                        "custom_graded_by", "biometric_verified",
                    ]
                    for field in continuity_fields:
                        if receiving_doc.get(field):
                            se_data[field] = receiving_doc.get(field)
                    se_doc = frappe.get_doc(se_data)
                    se_doc.insert(ignore_permissions=True)
                    se_doc.submit()
                    stock_entries.append(se_doc.name)

                    # Only update the Receiving document when we create a "Quarantine Rejects" entry
                    if effective_action == "Rejected" and receiving_doc:
                        try:
                            # Update the Receiving document with reject information
                            receiving_doc.custom_total_rejected_qty = (receiving_doc.custom_total_rejected_qty or 0) + qty_to_move
                            receiving_doc.custom_rejection_stock_entry = se_doc.name
                            receiving_doc.custom_rejection_date = frappe.utils.nowdate()
                            receiving_doc.save(ignore_permissions=True)

                            frappe.log_error(
                                f"Updated Receiving {receiving_doc.name} with reject qty {qty_to_move} from {se_doc.name}", 
                                "Reject Population Success"
                            )
                        except Exception as pop_err:
                            frappe.log_error(
                                frappe.get_traceback(), 
                                f"Failed to update Receiving doc with rejects for bucket {bucket_id}"
                            )
                            # end of new addition (Quarantine rejects)

                except Exception as e:
                    stock_move_errors.append(bucket_id + ": " + str(e))
                    frappe.log_error(frappe.get_traceback(), "Submit Batch Quality - stock move " + bucket_id)


    # PHASE 2: one Quality Reporting per (variety, action) + CAR for Q / R
    for group_key, group in variety_groups.items():
        item_code = group["item_code"]
        effective_action = group["effective_action"]
        greenhouse = group["greenhouse"] or variety_greenhouse.get(item_code, "")
        group_farm = group["farm"] or variety_farm.get(item_code, "") or farm

        gh_val = greenhouse if (greenhouse and frappe.db.exists("Warehouse", greenhouse)) else None
        farm_val = group_farm if (group_farm and frappe.db.exists("Farm", group_farm)) else None
        variety_val = item_code if frappe.db.exists("Item", item_code) else None

        harvest_time = get_first_time(greenhouse, "Harvesting")
        arrival_time = get_first_time(greenhouse, "Receiving")
        transit_time = compute_transit_time(harvest_time, arrival_time)

        quarantined_stems = group["total_quarantined_stems"] if effective_action == "Quarantined" else 0
        report_quality_params = build_child_entries(item_code, effective_action) if effective_action in ("Quarantined", "Rejected") else []
        stems_checked_for_variety = int(stems_by_variety.get(item_code) or checked_stems or 0)

        intake_data = {
            "doctype": "Quality Reporting",
            "control_point": control_point,
            "farm": farm_val,
            "ghouse": gh_val,
            "variety": variety_val,
            "harvest_time": harvest_time,
            "arrival_time": arrival_time,
            "transit_time": transit_time,
            "stems_received": variety_stems_received.get(item_code, 0),
            "stems_checked": stems_checked_for_variety,
            "stems_per_bucket": 0,
            "solution_level": solution_level,
            "solution_hygeine": solution_hygiene_val,
            "solution_ph": solution_ph_val,
            "chlorine_ppm": chlorine_ppm,
            "control_action": effective_action,
            "quarantined_stems": quarantined_stems,
            "quality_parameters": report_quality_params,
            "prepared_by": frappe.session.user,
            "unit_manager": unit_manager or None,
        }

        try:
            intake_doc = frappe.get_doc(intake_data)
            intake_doc.insert(ignore_permissions=True)
            created_reports.append(intake_doc.name)
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Submit Batch Quality - QR " + item_code + "/" + effective_action)
            continue

        # Corrective Action Report (isolated: never rolls back the report)
        if effective_action in ("Quarantined", "Rejected") and variety_val:
            try:
                if should_create_car(farm_val, item_code):
                    # Build issue text from quality concerns for this variety
                    issue_parts = []
                    variety_concerns = concerns_by_variety.get(item_code, {}) or {}
                    if isinstance(variety_concerns, dict):
                        for param_key, param_details in variety_concerns.items():
                            if isinstance(param_details, dict):
                                param_count = int(param_details.get("count") or 0)
                                if param_count > 0:
                                    issue_parts.append(str(param_key).replace("_", " ").title() + " (" + str(param_count) + ")")
                            elif param_details:
                                issue_parts.append(str(param_key).replace("_", " ").title())
                    issue_text = ", ".join(issue_parts) if issue_parts else ""

                    car_data = {
                        "doctype": "Corrective Action Report",
                        "variety": variety_val,
                        "date_of_incident": frappe.utils.nowdate(),
                        "requested_by": frappe.session.user,
                        "control_point": car_control_point,
                        "assigned_to": unit_manager_user or frappe.session.user,
                        "status": "Pending",
                        "issue": issue_text,
                        "root_cause": "To be determined",
                        "corrective_action_plan": "To be determined",
                        "target_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
                    }
                    if farm_val:
                        car_data["farm"] = farm_val
                    car_doc = frappe.get_doc(car_data)
                    car_doc.insert(ignore_permissions=True)
                    corrective_action_reports.append(car_doc.name)
            except Exception as e:
                frappe.log_error(frappe.get_traceback(), "Submit Batch Quality - CAR " + item_code)

        total_accepted_stems += group["total_accepted_stems"]
        total_quarantined_stems += group["total_quarantined_stems"]
        total_rejected_stems += group["total_rejected_stems"]

    frappe.db.commit()

    frappe.response["status"] = "success"
    frappe.response["message"] = (
        "Batch " + batch_no + ": " + str(len(created_reports)) + " Quality Reports created. "
        + "Accepted: " + str(total_accepted_stems) + ", Quarantined: " + str(total_quarantined_stems)
        + ", Rejected: " + str(total_rejected_stems) + " stems. "
        + str(len(corrective_action_reports)) + " Corrective Action Report(s) raised."
    )
    frappe.response["reports"] = created_reports
    frappe.response["stock_entries"] = stock_entries
    frappe.response["corrective_action_reports"] = corrective_action_reports
    frappe.response["skipped_buckets"] = skipped_buckets
    frappe.response["stock_move_errors"] = stock_move_errors
    frappe.response["summary"] = {
        "total_accepted": total_accepted_stems,
        "total_quarantined": total_quarantined_stems,
        "total_rejected": total_rejected_stems,
        "varieties_processed": len(variety_groups),
        "buckets_processed": sum(g["bucket_count"] for g in variety_groups.values()),
        "buckets_skipped": len(skipped_buckets),
        "corrective_actions_raised": len(corrective_action_reports),
    }


@frappe.whitelist()
def submitColdStoreTemperatureLog():
    try:
        data = frappe.form_dict.get("data")
        if isinstance(data, str):
            data = frappe.parse_json(data)
        if not data:
            frappe.throw("data is required")
        coldstore = data.get("coldstore")
        farm = data.get("farm")
        log_date = data.get("log_date") or frappe.utils.nowdate()
        if not coldstore: frappe.throw("coldstore is required")
        if not farm: frappe.throw("farm is required")

        name_guess = str(coldstore) + "-" + str(log_date)
        if frappe.db.exists("Cold Store Temperature Log", name_guess):
            doc = frappe.get_doc("Cold Store Temperature Log", name_guess)
        else:
            doc = frappe.new_doc("Cold Store Temperature Log")
            doc.farm = farm
            doc.coldstore = coldstore
            doc.log_date = log_date

        def fnum(x):
            if x is None: return 0.0
            if x == "":   return 0.0
            try: return float(x)
            except: return 0.0
        doc.temp_8am  = fnum(data.get("temp_8am"))
        doc.temp_10am = fnum(data.get("temp_10am"))
        doc.temp_12pm = fnum(data.get("temp_12pm"))
        doc.temp_2pm  = fnum(data.get("temp_2pm"))
        doc.temp_4pm  = fnum(data.get("temp_4pm"))
        doc.temp_6pm  = fnum(data.get("temp_6pm"))
        doc.temp_8pm  = fnum(data.get("temp_8pm"))
        doc.notes = data.get("notes") or doc.notes
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.response["message"] = {"status": "success", "name": doc.name,
            "message": "Temperature log " + doc.name + " saved"}
    except Exception as e:
        frappe.log_error("submitColdStoreTemperatureLog error", str(e))
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitColdroomCleaning():
    try:
        data = frappe.form_dict.get("data")
        if isinstance(data, str): data = frappe.parse_json(data)
        if not data: frappe.throw("data is required")
        if not data.get("farm"): frappe.throw("farm is required")
        if not data.get("mode_of_cleaning"): frappe.throw("mode_of_cleaning is required")
        doc = frappe.new_doc("Coldroom Cleaning Record")
        doc.farm = data.get("farm")
        doc.coldstore = data.get("coldstore") or None
        doc.cleaning_date = data.get("cleaning_date") or frappe.utils.nowdate()
        doc.mode_of_cleaning = data.get("mode_of_cleaning")
        doc.detergent_used = data.get("detergent_used") or None
        doc.detergent_rate = float(data.get("detergent_rate") or 0)
        doc.detergent_rate_unit = data.get("detergent_rate_unit") or "ml/L"
        doc.detergent_solution_volume_l = float(data.get("detergent_solution_volume_l") or 0)
        doc.disinfectant_used = data.get("disinfectant_used") or None
        doc.disinfectant_rate = float(data.get("disinfectant_rate") or 0)
        doc.disinfectant_rate_unit = data.get("disinfectant_rate_unit") or "ml/L"
        doc.disinfectant_solution_volume_l = float(data.get("disinfectant_solution_volume_l") or 0)
        doc.coldroom_volume_disinfected_m3 = float(data.get("coldroom_volume_disinfected_m3") or 0)
        doc.stock_quantity_stems = int(data.get("stock_quantity_stems") or 0)
        doc.equipment_used = data.get("equipment_used") or None
        doc.disinfected_by = data.get("disinfected_by") or ""
        doc.supervised_by = data.get("supervised_by") or ""
        doc.notes = data.get("notes") or ""
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.response["message"] = {"status": "success", "name": doc.name,
            "message": "Cleaning record " + doc.name + " saved"}
    except Exception as e:
        frappe.log_error("submitColdroomCleaning error", str(e))
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitColdroomInspection():
    try:
        data = frappe.form_dict.get("data")
        if isinstance(data, str): data = frappe.parse_json(data)
        if not data: frappe.throw("data is required")
        if not data.get("farm"): frappe.throw("farm is required")

        AREAS = ["floor", "roof", "walls", "lights", "doors", "shelves", "drainage", "evaporators"]

        doc = frappe.new_doc("Coldroom Inspection Log")
        doc.farm = data.get("farm")
        doc.coldstore = data.get("coldstore") or None
        doc.inspection_date = data.get("inspection_date") or frappe.utils.nowdate()
        doc.inspected_by = data.get("inspected_by") or ""
        doc.notes = data.get("notes") or ""

        for area in AREAS:
            multi = data.get(area + "_conditions")
            single = data.get(area + "_condition")
            vals = []
            if isinstance(multi, list):
                vals = [v for v in multi if v]
            elif isinstance(multi, str) and multi:
                vals = [x.strip() for x in multi.split(",") if x.strip()]
            elif single:
                vals = [single]
            for v in vals:
                row = doc.append(area + "_conditions", {})
                row.condition = v

        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.response["message"] = {"status": "success", "name": doc.name,
            "message": "Inspection " + doc.name + " saved"}
    except Exception as e:
        frappe.log_error("submitColdroomInspection error", str(e))
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitPackhouseCleaning():
    # Packhouse Cleaning submit (rev2 - cache refresh)
    try:
        data = frappe.form_dict.get("data")
        if isinstance(data, str): data = frappe.parse_json(data)
        if not data: frappe.throw("data is required")
        if not data.get("area"): frappe.throw("area is required")

        def num(v):
            try:
                return float(v)
            except Exception:
                return 0

        doc = frappe.new_doc("Packhouse Cleaning Record")
        doc.date = data.get("date") or frappe.utils.nowdate()
        doc.area = data.get("area")
        doc.mode_of_cleaning = data.get("mode_of_cleaning") or ""
        doc.detergent_used = data.get("detergent_used") or ""
        doc.detergent_rate = num(data.get("detergent_rate"))
        doc.detergent_rate_unit = data.get("detergent_rate_unit") or ""
        doc.detergent_volume_l = num(data.get("detergent_volume_l"))
        doc.disinfectant_used = data.get("disinfectant_used") or ""
        doc.disinfectant_rate = num(data.get("disinfectant_rate"))
        doc.disinfectant_rate_unit = data.get("disinfectant_rate_unit") or ""
        doc.disinfectant_volume_l = num(data.get("disinfectant_volume_l"))
        doc.disinfection_equipment = data.get("disinfection_equipment") or ""
        doc.inspected_by = data.get("inspected_by") or ""
        doc.remarks = data.get("remarks") or ""

        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.response["message"] = {"status": "success", "name": doc.name, "message": "Cleaning " + doc.name + " saved"}
    except Exception as e:
        frappe.log_error("submitPackhouseCleaning error", str(e))
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitPackhouseGlass():
    # Packhouse Glass Inspection submit (rev3 - per-component Table MultiSelect)
    try:
        data = frappe.form_dict.get("data")
        if isinstance(data, str): data = json.loads(data)
        if not data: frappe.throw("data is required")
        if not data.get("area"): frappe.throw("area is required")

        doc = frappe.new_doc("Packhouse Glass Inspection")
        doc.date = data.get("date") or frappe.utils.nowdate()
        doc.area = data.get("area")
        doc.inspected_by = data.get("inspected_by") or ""
        doc.remarks = data.get("remarks") or ""

        # Valid per-component Table MultiSelect fieldnames on the parent.
        ms_fields = [
            "window_glass", "fluorescent_tube", "digital_thermometer", "high_bay_lamps",
            "flood_lamps", "door_glass", "fire_extinguisher_glass", "cba_frame",
            "phone_gadget", "computer_screen", "photo_frames", "wall_clock",
            "weighing_scale", "vase_container", "wall_temp", "fire_exits",
            "printer_dashboard", "temperature_wall_check_box",
        ]

        def slugify(s):
            chars = []
            for ch in (s or "").lower():
                chars.append(ch if ch.isalnum() else " ")
            return "_".join("".join(chars).split())

        checks = data.get("checks") or []
        if isinstance(checks, str): checks = json.loads(checks)

        seen = {}
        for c in checks:
            if not isinstance(c, dict):
                continue
            comp = (c.get("component") or "").strip()
            cond = (c.get("condition") or "").strip()
            if not (comp and cond):
                continue
            # route each condition into its component's Table MultiSelect field
            fn = slugify(comp)
            if fn in ms_fields:
                picked = seen.get(fn) or []
                if cond not in picked:
                    picked.append(cond)
                    seen[fn] = picked
                    mrow = doc.append(fn, {})
                    mrow.condition = cond

        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.response["message"] = {"status": "success", "name": doc.name, "message": "Glass inspection " + doc.name + " saved"}
    except Exception as e:
        frappe.log_error("submitPackhouseGlass error", str(e))
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitPackhouseInspection():
    # Packhouse Inspection Log submit (rev5 - derive status from conditions)
    try:
        data = frappe.form_dict.get("data")
        if isinstance(data, str): data = json.loads(data)
        if not data: frappe.throw("data is required")
        if not data.get("area"): frappe.throw("area is required")

        doc = frappe.new_doc("Packhouse Inspection Log")
        doc.date = data.get("date") or frappe.utils.nowdate()
        doc.area = data.get("area")
        doc.inspected_by = data.get("inspected_by") or ""
        doc.remarks = data.get("remarks") or ""

        CANONICAL = [
            "Bunching Tables", "Sleeving Tables", "QC Table", "Guillotine", "Floor",
            "Walls", "Windows / Windowpanes", "Trolleys", "Drainage", "Sockets",
            "Lights", "Roof", "Roof Traces",
        ]
        # Conditions that mean the component is fine; anything else is a fault.
        OK = ("Clean", "In good condition", "Functioning")

        checks = data.get("checks") or []
        if isinstance(checks, str): checks = json.loads(checks)

        grouped = {}
        order = []
        for c in checks:
            if not isinstance(c, dict):
                continue
            comp = (c.get("component") or "").strip()
            cond = (c.get("condition") or "").strip()
            if not comp:
                continue
            if comp not in grouped:
                grouped[comp] = []
                order.append(comp)
            if cond and cond not in grouped[comp]:
                grouped[comp].append(cond)

        # Canonical components first, then any extras the app happened to send.
        components = list(CANONICAL)
        for comp in order:
            if comp not in components:
                components.append(comp)

        for comp in components:
            conds = grouped.get(comp, [])
            row = doc.append("components", {})
            row.component = comp
            row.conditions = ", ".join(conds)
            if not conds:
                row.status = "Not Checked"
            elif all(x in OK for x in conds):
                row.status = "√ Okay"
            else:
                row.status = "X Faulty"

        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.response["message"] = {"status": "success", "name": doc.name, "message": "Inspection " + doc.name + " saved"}
    except Exception as e:
        frappe.log_error("submitPackhouseInspection error", str(e))
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitSolutionMix():
    # ── submitSolutionMix ─────────────────────────────────────────────────
    # Body: { data: { farm, tank, mixing_date, mixing_time, ph, ppm, notes,
    #                 chemicals: [{chemical, amount, unit, photo, notes}] } }
    # Photos are URLs returned by /api/method/upload_file (already on disk).
    try:
        data = frappe.form_dict.get("data")
        if isinstance(data, str):
            data = frappe.parse_json(data)
        if not data:
            frappe.throw("data is required")

        farm = data.get("farm")
        tank = data.get("tank")
        chemicals = data.get("chemicals") or []
        if not farm: frappe.throw("farm is required")
        if not tank: frappe.throw("tank is required")
        if not chemicals: frappe.throw("at least one chemical is required")

        doc = frappe.new_doc("Solution Mix")
        doc.farm = farm
        doc.tank = tank
        doc.mixing_date = data.get("mixing_date") or frappe.utils.nowdate()
        doc.mixing_time = data.get("mixing_time") or frappe.utils.nowtime()
        doc.ph = data.get("ph") or 0
        doc.ppm = data.get("ppm") or 0
        doc.notes = data.get("notes") or ""

        for c in chemicals:
            if not c.get("chemical"):
                continue
            row = doc.append("chemicals", {})
            row.chemical = c.get("chemical")
            row.amount = c.get("amount") or 0
            row.unit = c.get("unit") or "g"
            row.photo = c.get("photo") or ""
            row.notes = c.get("notes") or ""

        doc.insert(ignore_permissions=True)

        # Link the per-chemical photos to the new doc so they appear in the
        # Attachments sidebar. Files uploaded via /api/method/upload_file with
        # no parent are floating — we adopt them here.
        photos = [c.get("photo") for c in chemicals if c.get("photo")]
        if photos:
            for url in photos:
                try:
                    file_name = frappe.db.get_value("File", {"file_url": url}, "name")
                    if file_name:
                        f = frappe.get_doc("File", file_name)
                        f.attached_to_doctype = "Solution Mix"
                        f.attached_to_name = doc.name
                        f.save(ignore_permissions=True)
                except Exception:
                    # Don't fail the whole submit just because the file link
                    # couldn't be repointed. The URL on the row is what matters.
                    pass

        frappe.db.commit()
        frappe.response["message"] = {
            "status": "success",
            "name": doc.name,
            "message": "Solution Mix " + doc.name + " saved",
        }
    except Exception as e:
        frappe.log_error("submitSolutionMix error", str(e))
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def update_submitted_field():
    # Frappe Server Script (Type: API), api_method = update_submitted_field
    # Directly patches a single field on a (possibly submitted) document via
    # frappe.db.set_value — bypasses the submit lock and doc validation.
    # Scoped to the Avocado Proforma Invoice doctypes so it can't be used to
    # rewrite arbitrary records.
    # Payload: { target_doctype, docname, fieldname, value }
    # Constraints: no import / def / += — keep it flat.

    frappe.response["message"] = {"success": False, "error": "Script failed"}

    try:
        dt = frappe.form_dict.get("target_doctype")
        dn = frappe.form_dict.get("docname")
        fieldname = frappe.form_dict.get("fieldname")
        value = frappe.form_dict.get("value")

        allowed = ["Avocado Proforma Invoice", "Avocado Proforma Invoice Item"]

        if dt not in allowed:
            frappe.response["message"] = {"success": False, "error": "Doctype not allowed: " + str(dt)}
        elif not dn or not fieldname:
            frappe.response["message"] = {"success": False, "error": "Missing docname or fieldname"}
        elif not frappe.db.exists(dt, dn):
            frappe.response["message"] = {"success": False, "error": str(dn) + " not found"}
        else:
            # Direct column write — no validate(), no on_update(), no submit guard.
            frappe.db.set_value(dt, dn, fieldname, value)
            frappe.db.commit()
            frappe.response["message"] = {"success": True, "fieldname": fieldname, "value": value}

    except Exception as e:
        frappe.response["message"] = {"success": False, "error": str(e)}



@frappe.whitelist()
def fetchVaselifeFormData():
    frappe.response["message"] = {"success": False, "error": "Script failed"}

    # ── Variety → Breeder map (temporary hardcode; normalised keys) ──────────
    BREEDER_MAP = {
        "1027-18-05": "MEILLAND",
        "1027-21-03 NB": "MEILLAND",
        "1275-16-01": "MEILLAND",
        "1280-18-03": "MEILLAND",
        "1570-19-03": "MEILLAND",
        "1630-18-01 NB": "MEILLAND",
        "18-17-8150-07": "DELBARD",
        "20-19-8627-01": "DELBARD",
        "20-19-8708-04": "DELBARD",
        "20-19-8712-001": "DELBARD",
        "20-19-8752-01": "DELBARD",
        "20-19-8862-01": "DELBARD",
        "21-20-8901-06": "DELBARD",
        "4220-17-62": "MEILLAND",
        "AFFECTION": "INTERPLANT",
        "AIMY": "DUMMEN ORANGE",
        "AKRA(RS21-K1305)": "DUMMEN ORANGE",
        "ALCHEMY": "NIRP",
        "AQUA": "DERUITER",
        "ATHENA": "DERUITER",
        "BANDOLERO": "CONTINENTAL",
        "BARBADOS": "CONTINENTAL",
        "BELALINDA BONET": "CONTINENTAL",
        "BELALINDA CERISE": "CONTINENTAL",
        "BELALINDA LORELEI": "CONTINENTAL",
        "BELALINDA LOVE": "CONTINENTAL",
        "BELLA ZARA": "SELECT",
        "BIRD NEST MUTATION": "DUMMEN ORANGE",
        "BIRDS DREAM": "DUMMEN ORANGE",
        "BLUSH ROSEVER": "NIRP",
        "BOMBASTIC": "JAN SPEK",
        "BOOMER": "INTERPLANT",
        "BOSA NOVA": "NIRP",
        "BRIDAL ROSEVER": "NIRP",
        "BRITNEY": "INTERPLANT",
        "BROOKE MUTATION.": "SCHREURS!",
        "BURLESQUE": "NIRP",
        "BYBLOS": "NIRP",
        "C-15-0029-010": "DERUITER",
        "C-15-0034-005": "DERUITER",
        "C-16-0098-005": "DERUITER",
        "C-16-0098-017": "DERUITER",
        "C-16-2071-003": "DERUITER",
        "C-18-0073-002": "DERUITER",
        "C-18-0073-022": "DERUITER",
        "C-18-0964-001": "DERUITER",
        "C-18-2038-04": "DERUITER",
        "CABANA": "DUMMEN ORANGE",
        "CAMILA": "INTERPLANT",
        "CANDLE LIGHT": "VANKLEEF",
        "CANDY FLOSS": "DERUITER",
        "CARADONA": "INTERPLANT",
        "CARRIE": "INTERPLANT",
        "CATCHING TRENDSETTER": "INTERPLANT",
        "CATCHY": "INTERPLANT",
        "CHEYENNE": "INTERPLANT",
        "CLASSIC SENSATION MUTATION": "DUMMEN ORANGE",
        "CLEOPATRA NB": "SELECT",
        "CONFIDENTIAL": "DERUITER",
        "COPACABANA": "DERUITER",
        "CR-20-1514-001": "DERUITER",
        "D 14-01": "DUMMEN ORANGE",
        "DANCE PARTY": "JAN SPEK",
        "DESERT QUEEN": "SCHREURS!",
        "DESTINY": "INTERPLANT",
        "DINARA": "JAN SPEK",
        "DIONNE": "INTERPLANT",
        "DISHA": "DELBARD",
        "DOLCE ROSEVER": "NIRP",
        "DOMINICA": "INTERPLANT",
        "DUCATI": "SELECT",
        "EMMA WOOD HOUSE": "JAN SPEK",
        "EMMA WOOD HOUSE MUTATION": "JAN SPEK",
        "EMMANCELLA": "DUMMEN ORANGE",
        "ENGLISH ROSEVER": "NIRP",
        "EXPLORER": "INTERPLANT",
        "FAIR LADY": "DERUITER",
        "FASHION GIRL": "NIRP",
        "FATAL ATTRACTION": "INTERPLANT",
        "FEMKE MUTATION": "INTERPLANT",
        "FIBONACCI YANTRA": "DERUITER",
        "FIRE FLASH": "INTERPLANT",
        "FIRE FLY": "INTERPLANT",
        "FIREWORKS": "SCHREURS!",
        "FLAMBEU ROSEVER": "NIRP",
        "FLINDERS LANE": "INTERPLANT",
        "FLIRT (190646-08)": "KORDES",
        "FRIVOLITY": "DELBARD",
        "FURIOSA": "INTERPLANT",
        "G-8-070": "DUMMEN ORANGE",
        "G17-773": "DUMMEN ORANGE",
        "G18-112": "DUMMEN ORANGE",
        "GARDEN CITY": "NIRP",
        "GNR 20-13": "NIRP",
        "GOLD MINE": "NIRP",
        "GOOD MOOD": "INTERPLANT",
        "GRACE ROSEVER": "NIRP",
        "HARD ROCK": "NIRP",
        "HARLEM": "NIRP",
        "HARMITAGE": "NIRP",
        "HARMONIA CERIES MUTATION": "DUMMEN ORANGE",
        "HARPER!": "SCHREURS!",
        "HIGH & MAGIC": "INTERPLANT",
        "HOLLY": "INTERPLANT",
        "HYPERICUM MAGICAL TRIUMPH": "INTERPLANT",
        "INEVITABLE": "NIRP",
        "INKER CHRISTINE": "INTERPLANT",
        "JULIETA": "DUMMEN ORANGE",
        "JUST MORE!": "SCHREURS!",
        "K0524-22": "INTERPLANT",
        "K0707-23": "INTERPLANT",
        "K0883-23": "INTERPLANT",
        "K1062-20": "INTERPLANT",
        "K2564-22": "INTERPLANT",
        "K2968-22": "INTERPLANT",
        "K3261-20": "INTERPLANT",
        "K3948-22": "INTERPLANT",
        "K4480-19": "INTERPLANT",
        "K4603-22": "INTERPLANT",
        "K4943-22": "NIRP",
        "K5029-20": "INTERPLANT",
        "K5187-22": "INTERPLANT",
        "K5255-23": "INTERPLANT",
        "K5489-22": "INTERPLANT",
        "K5712-20": "INTERPLANT",
        "K5825-22": "INTERPLANT",
        "K6506-22": "INTERPLANT",
        "KEIJSERS CORAL": "INTERPLANT",
        "KHIVA": "SCHREURS!",
        "LADY ELLA": "JAN SPEK",
        "LILAC WONDER": "UNITED SELECTION",
        "LOVELY HARPER!": "SCHREURS!",
        "LYRICA": "INTERPLANT",
        "MADAM BOMBASTIC": "JAN SPEK",
        "MADAM RED": "JAN SPEK",
        "MADREPERLA": "NIRP",
        "MALIBU PURPLE": "INTERPLANT",
        "MANDALA": "DERUITER",
        "MANDARIN": "DERUITER",
        "MAUREEN": "JAN SPEK",
        "MC-21-0084": "DERUITER",
        "MEMORY": "KORDES",
        "MIAMI VENUS": "NIRP",
        "MILKY WAY": "INTERPLANT",
        "MISS BOMBASTIC": "JAN SPEK",
        "MISTY BUBBLES": "INTERPLANT",
        "MONT ROYAL": "JAN SPEK",
        "MOONWALK": "INTERPLANT",
        "MOSHI MOSHI": "MEILLAND",
        "MOUNTAIN HIGH": "INTERPLANT",
        "N1-19-1337": "NIRP",
        "NAMASTAR": "INTERPLANT",
        "NEA 24 82": "NIRP",
        "NEA 24-84": "NIRP",
        "NI 19-1295": "NIRP",
        "NI 20-1144": "NIRP",
        "NIGHTINGALE": "INTERPLANT",
        "NIK 16 178": "NIRP",
        "NOVA ROSEVER": "NIRP",
        "NOVA ZEMBA": "DUMMEN ORANGE",
        "ON FIRE": "DUMMEN ORANGE",
        "ORANGE SPICE": "SELECT",
        "ORANGE SPOT": "DUMMEN ORANGE",
        "PASION BLIZZARD": "INTERPLANT",
        "PAULETTE": "SCHREURS!",
        "PAVILION BLUE": "INTERPLANT",
        "PC 11*243-17 EB": "CONTINENTAL",
        "PC 11*392.14-18 EB": "CONTINENTAL",
        "PC 121*292-5-19": "INTERPLANT",
        "PC 121*292.19 EB": "CONTINENTAL",
        "PC 121*37.2.19": "CONTINENTAL",
        "PC 121*37.20-19.": "CONTINENTAL",
        "PC 396*392-15 NB": "VIKING",
        "PC 400*14.2-19": "CONTINENTAL",
        "PC 400*243.2.19": "CONTINENTAL",
        "PC 400*243.24.19 EB": "CONTINENTAL",
        "PC 400*243.30-19 NB": "CONTINENTAL",
        "PC 400*243.6.19.": "CONTINENTAL",
        "PC 444*392-19": "CONTINENTAL",
        "PC 614*243.4-22EB": "CONTINENTAL",
        "PEACH ARIENA": "MEILLAND",
        "PEACH DIMENSION": "DERUITER",
        "PILADIO": "SCHREURS!",
        "PINA COLADA": "INTERPLANT",
        "PINA COLADA YELLOW": "INTERPLANT",
        "PINK DIMENSION": "DERUITER",
        "PINK FLOYD": "SCHREURS!",
        "PINK ICE": "INTERPLANT",
        "PINK IN TUITION.": "DELBARD",
        "PINK RHODOS": "INTERPLANT",
        "PINK SORBET": "DUMMEN ORANGE",
        "POENIA ROSEVER": "NIRP",
        "PORTIFINO": "NIRP",
        "PORTIFINO GARDEN": "NIRP",
        "PRICILLA": "SCHREURS!",
        "PRINCE CHARMING": "SELECT",
        "QUEEN ROSEVER": "NIRP",
        "RED AMAROK": "INTERPLANT",
        "RED IMPACT": "SELECT",
        "RED IN TUITION": "DELBARD",
        "RED TRENDSETTER": "INTERPLANT",
        "RED TROPHY": "INTERPLANT",
        "REVOLUTION": "DERUITER",
        "RLM UNDER A MAJESTIC SUNRISE": "VIKING",
        "RLM UNDER A MAJESTIC SUNRISE MUTATION": "VIKING",
        "ROMA ROSE SPRAY": "NIRP",
        "ROSEMANTIC NB": "NIRP",
        "ROUGE ROSEVER": "NIRP",
        "ROYAL BLUSH": "JAN SPEK",
        "ROYAL PORCELLINA": "INTERPLANT",
        "RS20-K0196": "DUMMEN ORANGE",
        "RS21-K1332": "DUMMEN ORANGE",
        "RS21-K1714": "DUMMEN ORANGE",
        "RS21-K2225": "DUMMEN ORANGE",
        "RS21-KO196": "DUMMEN ORANGE",
        "RS22-K0894": "DUMMEN ORANGE",
        "RS22-K0969": "DUMMEN ORANGE",
        "RS22-K1180": "DUMMEN ORANGE",
        "RS22-K3379": "DUMMEN ORANGE",
        "RS22-K3466": "DUMMEN ORANGE",
        "RS22-K3749": "DUMMEN ORANGE",
        "RS22-KO971": "DUMMEN ORANGE",
        "RS23-K0260": "DUMMEN ORANGE",
        "SAFINA": "INTERPLANT",
        "SALINERO": "INTERPLANT",
        "SAMONEL": "SCHREURS!",
        "SANCERRE": "INTERPLANT",
        "SANDRGHAM": "INTERPLANT",
        "SAXONY": "INTERPLANT",
        "SCROPPINO(K1141-22)": "INTERPLANT",
        "SECRET WHISPER": "DUMMEN ORANGE",
        "SEE YOU SOON": "SCHREURS!",
        "SELECT 16923": "SELECT",
        "SILK": "NIRP",
        "SILVA PINK": "DUMMEN ORANGE",
        "SMOOTHIE": "INTERPLANT",
        "SNOWFLAKE": "INTERPLANT",
        "SNOWY TRENDSETTER": "INTERPLANT",
        "SONORA": "INTERPLANT",
        "SOPHIA LOREN": "JAN SPEK",
        "SP-12720 NB": "SELECT",
        "SP-23520": "SELECT",
        "SP-25620": "SELECT",
        "SP-30720": "SELECT",
        "SP-31920": "SELECT",
        "SP-7119 NB": "SELECT",
        "SP1023 NB": "SELECT",
        "SPE19/1868-07": "JAN SPEK",
        "SPE20/1761-02": "JAN SPEK",
        "SPE21/1849-02": "JAN SPEK",
        "SR 06487": "SCHREURS!",
        "SR 11924!": "SCHREURS!",
        "SR 18068": "SCHREURS!",
        "SR 18158": "SCHREURS!",
        "ST.GEMINI{SR 17863}": "SCHREURS!",
        "STARION": "NIRP",
        "SUMMER DANCE": "INTERPLANT",
        "SUMMER ROSE": "INTERPLANT",
        "SUNCATCHER": "SELECT",
        "SUNCELL{SR 20860}": "SCHREURS!",
        "SUNNY GLORY": "INTERPLANT",
        "SWEET CERIES": "DUMMEN ORANGE",
        "SWEET DIMENSION": "DERUITER",
        "SWEET GISELLE": "INTERPLANT",
        "SWEET HARPER!": "SCHREURS!",
        "SWEET SMOOTHIE": "INTERPLANT",
        "SWEET SPOT": "NIRP",
        "SWEET UNIQUE": "INTERPLANT",
        "T0090-22": "INTERPLANT",
        "T0174-22": "INTERPLANT",
        "T0308-22": "INTERPLANT",
        "T0320-22": "INTERPLANT",
        "T0356-22": "INTERPLANT",
        "T0492-20": "INTERPLANT",
        "T0492-22": "INTERPLANT",
        "T0519-19": "INTERPLANT",
        "T0696-22": "INTERPLANT",
        "T0734-22": "INTERPLANT",
        "T0746-22": "INTERPLANT",
        "T1044-22": "INTERPLANT",
        "T1111-18": "INTERPLANT",
        "T1197-22": "INTERPLANT",
        "T1282-19": "INTERPLANT",
        "T1444-19": "INTERPLANT",
        "T1474-22": "INTERPLANT",
        "T1593-22": "INTERPLANT",
        "T3950-23": "INTERPLANT",
        "TAPDANCE": "INTERPLANT",
        "THRILLER": "INTERPLANT",
        "TICKLE PINK": "INTERPLANT",
        "TO126-22": "INTERPLANT",
        "TO165-22": "INTERPLANT",
        "TO288-22": "INTERPLANT",
        "TO306-22": "INTERPLANT",
        "TO312-22": "INTERPLANT",
        "TO314-22": "INTERPLANT",
        "TO492-20": "INTERPLANT",
        "TO796-22": "INTERPLANT",
        "TONIC ROSEVER": "NIRP",
        "TRALALA": "INTERPLANT",
        "TROPICAL AMAZON": "INTERPLANT",
        "TUSCANY ROSEVER": "NIRP",
        "UNICORN": "INTERPLANT",
        "UPPER CLASS": "INTERPLANT",
        "US 2725 Z": "UNITED SELECTION",
        "US-0264 Y": "UNITED SELECTION",
        "US-6020TMO2": "UNITED SELECTION",
        "US-6055B": "UNITED SELECTION",
        "US-6098 B": "UNITED SELECTION",
        "US-6098B": "UNITED SELECTION",
        "US-6366WM20": "UNITED SELECTION",
        "VERONICA": "INTERPLANT",
        "VIK 19-048 P": "VIKING",
        "VIK 23-150 P": "VIKING",
        "VIK 23-166 NB": "VIKING",
        "VIK 23-480 NB": "VIKING",
        "VIK 24-036-P": "VIKING",
        "VIK 24-407 NB": "VIKING",
        "VIK S 21-176-X": "VIKING",
        "VIK S-21-010-X": "VIKING",
        "VIK-21-216-P": "VIKING",
        "VIK-21-222-P": "VIKING",
        "VIK-23-037-P": "VIKING",
        "VIK-23-116 P": "VIKING",
        "VIK-23-484 P": "VIKING",
        "VIKING C": "VIKING",
        "VIKING D": "VIKING",
        "VIKING E": "VIKING",
        "VIKING F": "VIKING",
        "VILLANDRY": "SCHREURS!",
        "VIOLET LADY": "INTERPLANT",
        "VOLTARE": "VIKING",
        "WANGARE MATHAI": "DELBARD",
        "WEDDING INVITE": "INTERPLANT",
        "WENDY KRISTY": "INTERPLANT",
        "WHAM": "INTERPLANT",
        "WHITE DIAMOND": "INTERPLANT",
        "WHITE ICE": "NIRP",
        "WINTER GLOW": "DUMMEN ORANGE",
        "WITH HEART & SOUL": "INTERPLANT",
        "XLENCE": "INTERPLANT",
        "YELLOW BABE": "INTERPLANT",
        "YELLOWEEN": "INTERPLANT",
        "YVETTE": "INTERPLANT",
        "ZAER": "MEILLAND",
    }
    BREEDER_LIST = ["CONTINENTAL", "DELBARD", "DERUITER", "DUMMEN ORANGE", "INTERPLANT", "JAN SPEK", "KORDES", "MEILLAND", "NIRP", "SCHREURS!", "SELECT", "UNITED SELECTION", "VANKLEEF", "VIKING"]

    def norm_variety(x):
        return " ".join(str(x or "").upper().split())



    try:
        # ── Option lists — EDIT these to your real values ─────────────────────
        crops = ["Rose", "Spray Rose"]
        commercial_statuses = ["Trials", "Semi Commercial", "Commercial"]
        cut_stages = ["<1.5", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0", "5.0>"]

        # Failure reasons come from the Vaselife Parameter doctype (active only),
        # so QC can maintain them from the desk without touching this script.
        failure_reasons = [
            r.get("name")
            for r in frappe.get_all(
                "Vaselife Parameter",
                filters={"active": 1},
                fields=["name"],
                order_by="name asc",
            )
        ]

        # Items live in child groups (e.g. "Spray Roses - Regular"), so match every
        # descendant of the two parent trees via the Item Group nested set (lft/rgt).
        varieties_raw = frappe.db.sql("""
            SELECT i.name AS name, i.item_name AS item_name, i.item_group AS item_group
            FROM `tabItem` i
            JOIN `tabItem Group` g ON i.item_group = g.name
            JOIN `tabItem Group` p ON g.lft >= p.lft AND g.rgt <= p.rgt
            WHERE i.disabled = 0
              AND p.name IN ('Spray Roses', 'Standard Roses')
            ORDER BY i.item_name ASC
        """, as_dict=1)


        varieties = []
        for v in varieties_raw:
            vname = v.get("item_name") or v.get("name")
            # Match on display name first, then item code — normalised so minor
            # casing/spacing differences still resolve. No match -> blank, editable.
            breeder = (
                BREEDER_MAP.get(norm_variety(vname))
                or BREEDER_MAP.get(norm_variety(v.get("name")))
                or ""
            )
            varieties.append({
                "name": v.get("name"),
                "variety": vname,
                "breeder": breeder,
                "item_group": v.get("item_group") or "",
            })

        frappe.response["message"] = {
            "success": True,
            "breeders": [{"name": b} for b in BREEDER_LIST],
            "varieties": varieties,
            "crops": [{"name": x} for x in crops],
            "commercial_statuses": [{"name": x} for x in commercial_statuses],
            "cut_stages": [{"name": x} for x in cut_stages],
            "failure_reasons": [{"name": x} for x in failure_reasons],
        }

    except Exception as e:
        frappe.log_error(str(e), "fetchVaselifeFormData")
        frappe.response["message"] = {"success": False, "error": str(e)}


@frappe.whitelist()
def getVaselifeBucket():
    frappe.response["message"] = {"success": False, "error": "Script failed"}


    def extract_time_string(pt_val):
        if not pt_val:
            return ""
        try:
            raw = str(pt_val)
            if "," in raw:
                raw = raw.split(",")[1].strip()
            if "." in raw:
                raw = raw.split(".")[0]
            parts = raw.strip().split(":")
            if len(parts) == 3:
                return str(parts[0]).zfill(2) + ":" + str(parts[1]).zfill(2) + ":" + str(parts[2]).zfill(2)
            if len(parts) == 2:
                return str(parts[0]).zfill(2) + ":" + str(parts[1]).zfill(2) + ":00"
            return ""
        except Exception:
            return ""


    try:
        data = frappe.request.get_json() or {}
        bucket_id = (data.get("bucket_id") or "").strip()

        if not bucket_id:
            frappe.response["message"] = {"success": False, "error": "bucket_id is required."}
        else:
            entry = frappe.db.sql("""
                SELECT posting_date, posting_time,
                       farm, custom_greenhouse
                FROM `tabStock Entry`
                WHERE custom_bucket_id = %s
                  AND stock_entry_type = 'Harvesting'
                  AND docstatus = 1
                ORDER BY creation DESC
                LIMIT 1
            """, (bucket_id,), as_dict=1)

            if not entry:
                frappe.response["message"] = {
                    "success": False,
                    "error": "No harvest record found for bucket " + bucket_id + ".",
                }
            else:
                e = entry[0]
                frappe.response["message"] = {
                    "success": True,
                    "bucket_id": bucket_id,
                    "harvest_date": str(e.posting_date) if e.posting_date else "",
                    "harvest_time": extract_time_string(e.posting_time),
                    "farm": e.farm or "",
                    "greenhouse": e.custom_greenhouse or "",
                    "length": "",
                }

    except Exception as e:
        frappe.log_error(str(e), "getVaselifeBucket Error")
        frappe.response["message"] = {"success": False, "error": str(e)}


@frappe.whitelist()
def saveVaselifeSample():
    frappe.response["message"] = {"status": "error", "message": "Script failed"}


    def extract_date_suffix(sampling_date):
        parts = (sampling_date or "").split("-")
        if len(parts) == 3:
            return parts[2] + parts[1] + parts[0][2:]
        return frappe.utils.nowdate().replace("-", "")[2:]


    def num_to_letters(n):
        result = ""
        n = n + 1
        while n > 0:
            n -= 1
            result = chr(65 + (n % 26)) + result
            n //= 26
        return result


    def generate_sample_code(variety, sampling_date):
        variety_slug = (variety or "").replace(" ", "")
        prefix = variety_slug + extract_date_suffix(sampling_date)
        existing = frappe.get_all(
            "Vaselife Sample",
            filters=[["name", "like", prefix + "%"]],
            fields=["name"],
        )
        return prefix + num_to_letters(len(existing))


    def safe_link(doctype, value):
        if not value:
            return None
        return value if frappe.db.exists(doctype, value) else None


    def to_float(v):
        try:
            return float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None


    def to_int(v):
        try:
            return int(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None


    try:
        raw = frappe.request.get_json() or {}
        data = raw.get("data", raw)
        frappe.log_error(json.dumps(data, indent=2, default=str), "saveVaselifeSample Payload")

        sampling_date = (data.get("sampling_date") or "").strip()
        variety       = (data.get("variety") or "").strip()

        if not sampling_date:
            frappe.response["message"] = {"status": "error", "message": "sampling_date is required."}
        elif not variety:
            frappe.response["message"] = {"status": "error", "message": "variety is required."}
        elif not frappe.db.exists("Item", variety):
            frappe.response["message"] = {"status": "error", "message": "Variety '" + variety + "' not found."}
        else:
            sample_code = generate_sample_code(variety, sampling_date)

            sample_doc = frappe.get_doc({
                "doctype":           "Vaselife Sample",
                "name":              sample_code,
                "sampling_date":     sampling_date,
                "consignment":       data.get("consignment") or "",
                "supermarket_date":  data.get("supermarket_date") or None,
                "due_date":          data.get("due_date") or None,
                "vase_date":         data.get("vase_date") or None,
                "variety":           variety,
                "breeder":           safe_link("Breeder", data.get("breeder") or ""),
                "commercial_status": safe_link("Vaselife Commercial Status", data.get("commercial_status") or ""),
                "crop":              safe_link("Vaselife Crop", data.get("crop") or ""),
                "harvest_date":      data.get("harvest_date") or None,
                "harvest_time":      data.get("harvest_time") or None,
                "line_code":         data.get("line_code") or "",
                "farm":              safe_link("Farm", data.get("farm") or ""),
                "gh":                data.get("gh") or "",
                "length":            to_float(data.get("length")),
                "no_of_stems":       to_int(data.get("no_of_stems")),
                "bud_height":        to_float(data.get("bud_height")),
                "bud_width":         to_float(data.get("bud_width")),
                "initial_cut_stage": safe_link("Vaselife Cut Stage", data.get("initial_cut_stage") or ""),
                "prepared_by":       frappe.session.user,
            })

            sample_doc.insert(ignore_permissions=True)
            frappe.db.commit()

            frappe.response["message"] = {
                "status":      "success",
                "name":        sample_doc.name,
                "sample_code": sample_doc.name,
                "message":     "Sample " + sample_doc.name + " saved successfully.",
            }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "saveVaselifeSample Error")
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def saveVaselifeObservation():
    frappe.response["message"] = {"status": "error", "message": "Script failed"}

    try:
        raw = frappe.request.get_json() or {}
        data = raw.get("data", raw)
        frappe.log_error(json.dumps(data, indent=2, default=str), "saveVaselifeObservation Payload")

        sample_code = (data.get("sample_code") or "").strip()
        obs_date    = (data.get("date") or "").strip()

        if not sample_code:
            frappe.response["message"] = {"status": "error", "message": "sample_code is required."}
        elif not obs_date:
            frappe.response["message"] = {"status": "error", "message": "date is required."}
        elif not frappe.db.exists("Vaselife Sample", sample_code):
            frappe.response["message"] = {"status": "error", "message": "Sample '" + sample_code + "' not found."}
        else:
            stems_failed = data.get("stems_failed")
            try:
                stems_failed = int(stems_failed) if stems_failed not in (None, "") else 0
            except (ValueError, TypeError):
                stems_failed = 0

            reasons_raw = data.get("failure_reasons") or []
            if not isinstance(reasons_raw, list):
                reasons_raw = []

            cut_stage = (str(data.get("cut_stage")).strip() if data.get("cut_stage") not in (None, "") else None)

            # Build the child-table rows from the reasons payload. Each entry is
            # either a plain reason name or a dict; only reasons that exist as a
            # Vaselife Parameter are linked (unknowns stay in the JSON audit blob).
            reason_rows = []
            for it in reasons_raw:
                if isinstance(it, str):
                    nm, st = it.strip(), 0
                elif isinstance(it, dict):
                    nm = (it.get("name") or it.get("reason") or it.get("parameter") or it.get("label") or "").strip()
                    st = it.get("count") or it.get("qty") or it.get("stems") or 0
                else:
                    continue
                if not nm or not frappe.db.exists("Vaselife Parameter", nm):
                    continue
                try:
                    st = int(st)
                except (ValueError, TypeError):
                    st = 0
                reason_rows.append({"reason": nm, "stems": st})

            obs_doc = frappe.get_doc({
                "doctype":                "Vaselife Observation",
                "sample":                 sample_code,
                "date":                   obs_date,
                "stems_failed":           stems_failed,
                "cut_stage":              cut_stage,
                "failure_reasons_detail": reason_rows,
                "failure_reasons":        json.dumps(reasons_raw),  # kept as audit/raw
                "notes":                  data.get("notes") or "",
                "prepared_by":            frappe.session.user,
            })

            obs_doc.insert(ignore_permissions=True)
            frappe.db.commit()

            frappe.response["message"] = {
                "status":  "success",
                "name":    obs_doc.name,
                "message": "Observation " + obs_doc.name + " saved for sample " + sample_code + ".",
            }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "saveVaselifeObservation Error")
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def getCurrentUserRoles():
    try:
        user = frappe.session.user
        rows = frappe.get_all(
            "Has Role",
            filters={"parent": user, "parenttype": "User"},
            fields=["role"],
        )
        roles = [r["role"] for r in rows if r.get("role")]
        frappe.response["data"] = {
            "user": user,
            "roles": roles,
        }
    except Exception as e:
        frappe.log_error("getCurrentUserRoles error: " + str(e))
        frappe.response["http_status_code"] = 500
        frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def getGreenhouseData():
    try:
        data = frappe.request.get_json()
        greenhouse_name = data.get("greenhouse_name")
        if not greenhouse_name:
            frappe.throw("Greenhouse name is required.")

        # Fetch varieties via the most recent Crop Cycle for this greenhouse
        varieties = []
        cycles = frappe.get_all(
            "Crop Cycle",
            filters={"greenhouse": greenhouse_name},
            fields=["name"],
            order_by="modified desc",
            limit=1
        )
        if cycles:
            cycle_varieties = frappe.get_all(
                "Crop Cycle Variety",
                filters={"parent": cycles[0].name, "parenttype": "Crop Cycle"},
                fields=["variety", "area_m2"],
                order_by="variety asc"
            )
            varieties = [{"variety": v.variety, "area": v.area_m2} for v in cycle_varieties]

        # Fetch employees linked to this greenhouse
        employees = frappe.get_all(
            "Employee",
            filters={"custom_greenhouse": greenhouse_name},
            fields=["employee_name"]
        )

        frappe.response["data"] = {"varieties": varieties, "employees": employees}
    except Exception as e:
        frappe.log_error(message=str(e), title="Get greenhouse data error")
        frappe.throw(_("Error fetching greenhouse data: ") + str(e))


@frappe.whitelist()
def reportAppVersion():
    # reportAppVersion
    # Record one Mobile App Version Log row per authenticated user per day.
    # Sandbox rules:
    #   - no imports
    #   - no return statements (set frappe.response instead)
    #   - no augmented assignment, no in-place slicing
    #   - no hasattr/isinstance, no os/file/exec
    #   - use str() for date conversions

    user = frappe.session.user

    if user == "Guest":
        frappe.throw("Authentication required.")

    data = frappe.form_dict or {}
    app_version = data.get("app_version")
    platform = data.get("platform")
    device_model = data.get("device_model")

    if not app_version:
        frappe.throw("app_version is required.")

    today_str = str(frappe.utils.today())
    now_str = str(frappe.utils.now())

    existing = frappe.db.get_all(
        "Mobile App Version Log",
        filters={
            "user": user,
            "reported_on": [">=", today_str + " 00:00:00"],
        },
        fields=["name"],
        limit_page_length=1,
        order_by="reported_on desc",
    )

    if existing:
        log_name = existing[0]["name"]
        frappe.db.set_value(
            "Mobile App Version Log",
            log_name,
            {
                "app_version": app_version,
                "platform": platform,
                "device_model": device_model,
                "reported_on": now_str,
            },
        )
        action = "updated"
    else:
        doc = frappe.get_doc({
            "doctype": "Mobile App Version Log",
            "user": user,
            "app_version": app_version,
            "platform": platform,
            "device_model": device_model,
            "reported_on": now_str,
        })
        doc.insert(ignore_permissions=True)
        log_name = doc.name
        action = "created"

    frappe.response["message"] = {
        "ok": True,
        "name": log_name,
        "action": action,
        "user": user,
        "app_version": app_version,
    }


@frappe.whitelist()
def reportDeviceTelemetry():
    # Server Script (API), api_method = reportDeviceTelemetry
    # Inserts one Device Telemetry record from the posted payload. Fail-safe, no submit.
    # Payload: { "data": { device_id, model, os, app_version, ... , captured_at, is_connected } }
    frappe.response["message"] = {"status": "error", "message": "Script failed"}
    try:
        data = frappe.request.get_json()
        if isinstance(data, dict) and "data" in data:
            data = data.get("data")
        data = data or {}

        fields = [
            "device_id", "device_name", "model", "brand", "os", "os_version",
            "app_name", "app_version", "build", "ota_update_id", "ota_channel", "battery_level",
            "battery_state", "network_type", "cellular_generation",
            "storage_free", "storage_total", "user", "user_full_name", "captured_at",
        ]
        doc = {"doctype": "Device Telemetry"}
        i = 0
        while i < len(fields):
            f = fields[i]
            doc[f] = data.get(f)
            i = i + 1
        doc["is_connected"] = 1 if data.get("is_connected") else 0
        # Device sends ISO-8601 with a 'Z'/'T' which MySQL Datetime rejects — normalise
        # to 'YYYY-MM-DD HH:MM:SS.ffffff'.
        ts = data.get("captured_at")
        doc["captured_at"] = str(ts).replace("T", " ").replace("Z", "")[:26] if ts else None
        doc["raw_json"] = json.dumps(data)

        d = frappe.get_doc(doc)
        d.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.response["message"] = {"status": "success", "name": d.name}
    except Exception as e:
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitFieldRejects():
    variety = frappe.form_dict.get("variety")
    no_of_stems = frappe.form_dict.get("no_of_stems")
    rejection_reason = frappe.form_dict.get("rejection_reason")
    farm = frappe.form_dict.get("farm")
    greenhouse = frappe.form_dict.get("greenhouse")

    if not variety or not no_of_stems or not rejection_reason or not farm or not greenhouse:
        frappe.throw("Missing required fields: variety, no_of_stems, rejection_reason, farm, greenhouse")

    qty = frappe.utils.cint(no_of_stems)
    if qty <= 0:
        frappe.throw("Number of stems must be greater than 0")

    doc = frappe.get_doc({
        "doctype": "Stock Entry",
        "stock_entry_type": "Field Rejects",
        "posting_date": frappe.utils.today(),
        "posting_time": frappe.utils.nowtime(),
        "company": "Karen Roses",
        "farm": farm,
        "items": [
            {
                "s_warehouse": greenhouse,
                "t_warehouse": "Rejects - KR",
                "item_code": variety,
                "qty": qty,
                "custom_rejection_reason": rejection_reason,
            }
        ]
    })

    doc.insert()
    doc.submit()

    frappe.response["message"] = {
        "status": "success",
        "name": doc.name,
        "message": "Field rejection recorded successfully"
    }



@frappe.whitelist()
def fetchColdroomQCFormData():
    try:
        cp_rows = frappe.db.sql(
            "SELECT name, control_point, control_area FROM `tabQC Control Point` WHERE control_area = 'Cold Room' ORDER BY control_point",
            as_dict=1
        )
        control_points = [{"name": r.get("name"), "control_point": r.get("control_point") or r.get("name"), "control_area": r.get("control_area") or "Cold Room"} for r in cp_rows]

        params = frappe.db.sql("SELECT name, parameter, tolerance_thresholds FROM `tabQC Parameters` ORDER BY parameter", as_dict=1)
        reasons = [{"name": p.get("name"), "parameter": p.get("parameter") or p.get("name"), "tolerance_thresholds": p.get("tolerance_thresholds")} for p in params]

        qc_roles = ["Packhouse Manager", "Quality Manager", "QUALITY CONTROLLER", "System Manager", "Administrator"]
        role_ph = ",".join(["%s"] * len(qc_roles))
        incharge_rows = frappe.db.sql(
            "SELECT DISTINCT u.name, u.full_name FROM `tabUser` u JOIN `tabHas Role` r ON r.parent = u.name WHERE r.role IN (" + role_ph + ") AND u.enabled = 1 ORDER BY u.full_name LIMIT 50",
            qc_roles, as_dict=1
        )
        if not incharge_rows:
            incharge_rows = frappe.db.sql("SELECT name, full_name FROM `tabUser` WHERE enabled = 1 AND user_type = 'System User' ORDER BY full_name LIMIT 50", as_dict=1)

        frappe.response["message"] = {
            "success": True,
            "control_points": control_points,
            "reasons": reasons,
            "qc_incharge_options": [dict(u) for u in incharge_rows],
        }
    except Exception as e:
        frappe.log_error(str(e), "fetchColdroomQCFormData Error")
        frappe.response["message"] = {"success": False, "error": str(e)}


@frappe.whitelist()
def getColdroomBucket():
    try:
        data = frappe.form_dict
        bucket_id = (data.get("bucket_id") or "").strip()
        if not bucket_id:
            frappe.throw("bucket_id is required")
        bl = bucket_id.lower()

        # Buckets are REUSED across harvest sessions, so we must scope to the most
        # recent receiving for this bucket -- not aggregate its whole history (that
        # mixed farms and took the oldest date, exaggerating stock age).
        recv = frappe.db.sql("""
            SELECT name, farm, custom_greenhouse AS greenhouse,
                   custom_stem_length AS length, custom_location AS location,
                   custom_receiving_batch_id AS harvest_batch, posting_date
            FROM `tabStock Entry`
            WHERE LOWER(custom_bucket_id) = %s
              AND stock_entry_type IN ('Receiving', 'Late Receipt')
              AND docstatus = 1
            ORDER BY creation DESC
            LIMIT 1
        """, (bl,), as_dict=1)

        if not recv:
            frappe.throw("No receiving record found for bucket %s" % bucket_id.upper())

        entry = recv[0]

        def gh(w):
            w = w or ""
            if " - " in w:
                w = w.rsplit(" - ", 1)[0]
            return w.strip()

        # Stock Entry Detail has no stem-length column of its own — length is
        # only ever recorded on the parent Stock Entry, so every variety row
        # falls back to that header value.
        vrows = frappe.db.sql("""
            SELECT item_code, item_name, SUM(qty) AS stems
            FROM `tabStock Entry Detail`
            WHERE parent = %s
            GROUP BY item_code, item_name
            ORDER BY item_code
        """, (entry.get("name"),), as_dict=1)

        header_length = entry.get("length") or ""
        varieties = []
        for r in vrows:
            code = r.get("item_code") or r.get("item_name") or ""
            if not code:
                continue
            varieties.append({
                "variety": code,
                "item_name": r.get("item_name") or "",
                "stems": int(r.get("stems") or 0),
                "length": header_length,
            })

        # Stock age = today - HARVEST date. Harvest date is embedded in the harvest
        # batch no "{bucket}-{farm}-{variety}-{YYYY}-{MM}-{DD} HH:MM:SS"; fall back
        # to the receiving posting date if it can't be parsed.
        harvest_date = None
        hb = entry.get("harvest_batch") or ""
        parts = hb.split("-")
        if len(parts) >= 6:
            dd = parts[5].split(" ")[0]
            harvest_date = parts[3] + "-" + parts[4] + "-" + dd
        if not harvest_date and entry.get("posting_date"):
            harvest_date = str(entry.get("posting_date"))

        days_in_stock = 0
        if harvest_date:
            try:
                days_in_stock = frappe.utils.date_diff(frappe.utils.today(), harvest_date)
            except Exception:
                days_in_stock = 0
        if days_in_stock < 0:
            days_in_stock = 0

        frappe.response["message"] = {
            "success": True,
            "bucket_id": bucket_id.upper(),
            "farm": entry.get("farm") or "",
            "greenhouse": gh(entry.get("greenhouse")),
            "packhouse": entry.get("location") or "",
            "days_in_stock": days_in_stock,
            "harvest_date": harvest_date or "",
            "varieties": varieties,
        }
    except Exception as e:
        frappe.log_error(str(e), "getColdroomBucket Error")
        frappe.response["message"] = {"success": False, "error": str(e)}


@frappe.whitelist()
def saveColdroomQC():
    try:
        data = frappe.form_dict
        payload = data.get("data")
        if isinstance(payload, str):
            payload = frappe.parse_json(payload)
        if not payload:
            payload = data

        control_point = payload.get("control_point", "")
        if not control_point:
            frappe.throw("control_point is required")
        control_area = frappe.db.get_value("QC Control Point", control_point, "control_area") or "Cold Room"

        bucket_id = payload.get("bucket_id", "")
        rejections = payload.get("rejections") or []
        if isinstance(rejections, str):
            rejections = frappe.parse_json(rejections)

        doc = frappe.new_doc("Coldroom QC")
        doc.date = payload.get("date") or frappe.utils.nowdate()
        doc.control_point = control_point
        doc.control_area = control_area
        doc.bucket_id = bucket_id
        doc.farm = payload.get("farm", "")
        doc.greenhouse = payload.get("greenhouse", "")
        doc.packhouse = payload.get("packhouse", "")
        doc.days_in_stock = int(payload.get("days_in_stock", 0) or 0)
        qi = payload.get("qc_incharge", "")
        doc.qc_incharge = qi if (qi and frappe.db.exists("User", qi)) else None
        doc.recorder = frappe.session.user
        doc.remarks = payload.get("remarks", "")

        clean = []
        total = 0
        for r in rejections:
            if not isinstance(r, dict):
                continue
            variety = (r.get("variety") or "").strip()
            reason = (r.get("reason") or "").strip()
            stems = int(r.get("stems", 0) or 0)
            if not variety or not reason or stems <= 0:
                continue
            row = doc.append("rejections", {})
            row.variety = variety
            row.reason = reason
            row.stems = stems
            row.length = r.get("length", "")
            clean.append({"variety": variety, "stems": stems})
            total = total + stems

        doc.total_stems_rejected = total
        doc.insert(ignore_permissions=True)

        # Rejected stems move out of the cold store: Material Transfer -> Rejects,
        # typed 'Coldroom rejects'. Best-effort -- the QC record persists even if the
        # stock move fails (e.g. the stems already left the cold store).
        se_name = None
        se_error = None
        if clean:
            try:
                bl = (bucket_id or "").lower()
                recv = frappe.db.sql("""
                    SELECT name, company, custom_greenhouse, custom_receiving_batch_id,
                           custom_harvest_batch_no
                    FROM `tabStock Entry`
                    WHERE LOWER(custom_received_bucket_id) = %s
                      AND stock_entry_type IN ('Receiving','Late Receipt') AND docstatus = 1
                    ORDER BY creation DESC LIMIT 1
                """, (bl,), as_dict=1)

                company = "Karen Roses"
                gh_full = ""
                harvest_batch = ""
                recv_batch = ""
                wh_by_item = {}
                if recv:
                    r0 = recv[0]
                    company = r0.get("company") or company
                    gh_full = r0.get("custom_greenhouse") or ""
                    harvest_batch = r0.get("custom_harvest_batch_no") or ""
                    recv_batch = r0.get("custom_receiving_batch_id") or ""
                    dets = frappe.db.sql("""
                        SELECT item_code, t_warehouse, cost_center, basic_rate
                        FROM `tabStock Entry Detail` WHERE parent = %s
                    """, (r0.get("name"),), as_dict=1)
                    for d in dets:
                        if d.get("item_code") and d.get("t_warehouse"):
                            wh_by_item[d.get("item_code")] = d

                target_wh = "Rejects - KR"
                items = []
                for c in clean:
                    det = wh_by_item.get(c["variety"])
                    s_wh = det.get("t_warehouse") if det else ""
                    if not s_wh:
                        continue
                    item = {
                        "item_code": c["variety"],
                        "qty": c["stems"],
                        "transfer_qty": c["stems"],
                        "uom": frappe.db.get_value("Item", c["variety"], "stock_uom") or "Stems",
                        "conversion_factor": 1.0,
                        "s_warehouse": s_wh,
                        "t_warehouse": target_wh,
                        "basic_rate": (det.get("basic_rate") if det else 0) or 0,
                        "allow_zero_valuation_rate": 1,
                    }
                    if det and det.get("cost_center"):
                        item["cost_center"] = det.get("cost_center")
                    items.append(item)

                if items:
                    se = frappe.get_doc({
                        "doctype": "Stock Entry",
                        "stock_entry_type": "Coldroom rejects",
                        "purpose": "Material Transfer",
                        "company": company,
                        "posting_date": frappe.utils.nowdate(),
                        "posting_time": frappe.utils.nowtime(),
                        "set_posting_time": 1,
                        "custom_bucket_id": bl,
                        "custom_harvest_batch_no": harvest_batch,
                        "custom_receiving_batch_id": recv_batch,
                        "farm": doc.farm,
                        "custom_greenhouse": gh_full,
                        "items": items,
                    })
                    se.insert(ignore_permissions=True)
                    se.submit()
                    se_name = se.name
                    frappe.db.set_value("Coldroom QC", doc.name, "rejects_stock_entry", se_name, update_modified=False)
            except Exception as se_exc:
                se_error = str(se_exc)
                frappe.log_error(frappe.get_traceback(), "saveColdroomQC stock move " + (bucket_id or ""))

        frappe.db.commit()
        frappe.response["message"] = {
            "status": "success",
            "name": doc.name,
            "message": "Coldroom QC " + doc.name + " submitted",
            "total_stems_rejected": total,
            "stock_entry": se_name,
            "stock_entry_error": se_error,
        }
    except Exception as e:
        frappe.log_error(str(e), "saveColdroomQC Error")
        frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def transferBucket():
    """Move a bucket from its current shelf to another shelf on the SAME farm,
    re-syncing every place a bucket's location is cached for an existing order
    (Pick List Item.shelf, Bucket Allocation Status.shelf_location/shelf_farm)
    so a draft OPL never points a picker at a shelf the bucket has since left.
    Cross-farm moves are NOT handled here -- that's the separate truck-transfer
    flow (awaiting_transfer/in_transit/loaded_in_trolley on Pick List Item).
    A bucket moves as one physical unit: every Shelf Item row it has (one per
    variety/length) moves together."""
    try:
        data = frappe.request.get_json() or {}
        bucket_id = data.get("bucket_id")
        to_shelf_id = data.get("to_shelf_id")

        if not bucket_id:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "bucket_id_not_null",
                "message": "Bucket ID is missing.",
                "payload": {},
            }
            return
        if not to_shelf_id:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "to_shelf_id_not_null",
                "message": "Destination shelf ID is missing.",
                "payload": {"bucket_id": bucket_id},
            }
            return

        source_items = frappe.get_all(
            "Shelf Item",
            filters={"bucket_id": bucket_id},
            fields=[
                "name", "parent", "variety", "greenhouse", "warehouse", "stem_qty",
                "stem_length", "cut_stage", "harvest_date", "receiving_date", "farm",
                "harvester", "graded_by", "grading_date",
            ],
        )
        if not source_items:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "not_on_shelf",
                "message": "This bucket is not currently on any shelf.",
                "payload": {"bucket_id": bucket_id},
            }
            return

        from_shelf_id = source_items[0].parent

        if from_shelf_id == to_shelf_id:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "same_shelf",
                "message": "The bucket is already on shelf {0}.".format(to_shelf_id),
                "payload": {"bucket_id": bucket_id, "shelf_id": to_shelf_id},
            }
            return

        if not frappe.db.exists("Shelf", to_shelf_id):
            frappe.response["data"] = {
                "status": "failed",
                "reason": "destination_not_found",
                "message": "Shelf {0} does not exist.".format(to_shelf_id),
                "payload": {"bucket_id": bucket_id, "to_shelf_id": to_shelf_id},
            }
            return

        from_farm = frappe.db.get_value("Shelf", from_shelf_id, "farm")
        to_farm = frappe.db.get_value("Shelf", to_shelf_id, "farm")
        if from_farm != to_farm:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "cross_farm_not_allowed",
                "message": "Cannot transfer from {0} ({1}) to {2} ({3}) -- shelf-to-shelf "
                "transfer only works within the same farm.".format(
                    from_shelf_id, from_farm, to_shelf_id, to_farm
                ),
                "payload": {
                    "bucket_id": bucket_id,
                    "from_shelf_id": from_shelf_id,
                    "from_farm": from_farm,
                    "to_shelf_id": to_shelf_id,
                    "to_farm": to_farm,
                },
            }
            return

        mid_transfer = frappe.db.sql(
            """
            SELECT pli.name FROM `tabPick List Item` pli
            JOIN `tabOrder Pick List` opl ON opl.name = pli.parent AND opl.docstatus = 0
            WHERE pli.parenttype = 'Order Pick List' AND pli.bucket = %s
              AND (pli.awaiting_transfer = 1 OR pli.in_transit = 1
                   OR pli.loaded_in_trolley = 1) LIMIT 1""",
            bucket_id,
            as_dict=True,
        )
        if mid_transfer:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "mid_truck_transfer",
                "message": "This bucket is mid inter-farm transfer and can't be moved "
                "between shelves right now.",
                "payload": {"bucket_id": bucket_id},
            }
            return

        to_shelf = frappe.get_doc("Shelf", to_shelf_id)
        existing_buckets = {(it.bucket_id or "").lower() for it in (to_shelf.items or [])}
        existing_buckets.discard(bucket_id.lower())
        if len(existing_buckets) >= 2:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "two_buckets_per_shelf",
                "message": "The destination shelf is full.",
                "payload": {"bucket_id": bucket_id, "to_shelf_id": to_shelf_id},
            }
            return

        total_qty = 0
        new_items = []
        for src in source_items:
            new_item = to_shelf.append("items", {})
            new_item.bucket_id = bucket_id
            new_item.variety = src.variety
            new_item.greenhouse = src.greenhouse
            new_item.warehouse = src.warehouse
            new_item.stem_qty = src.stem_qty
            new_item.stem_length = src.stem_length
            new_item.date_added = frappe.utils.now_datetime()
            new_item.cut_stage = src.cut_stage
            new_item.harvest_date = src.harvest_date
            new_item.receiving_date = src.receiving_date
            new_item.farm = src.farm
            new_item.harvester = src.harvester
            new_item.graded_by = src.graded_by
            new_item.grading_date = src.grading_date
            new_items.append(new_item)
            total_qty += src.stem_qty or 0
        to_shelf.save(ignore_permissions=True)

        for src in source_items:
            frappe.delete_doc("Shelf Item", src.name, force=1, ignore_permissions=True)
        frappe.db.set_value("Shelf", from_shelf_id, "modified", frappe.utils.now())

        # Sync every not-yet-issued Pick List Item pointing at this bucket -- an
        # issued row's shelf is already moot, issuing already happened. Uses a raw
        # field update (not load-doc-then-save) because the parent Order Pick List
        # may already be submitted (a partially-picked order) and `shelf` has no
        # allow_on_submit flag -- loading the doc and calling .save() on it would
        # throw on a submitted OPL even with ignore_permissions=True, since that
        # only bypasses permission checks, not the submit-lock field validation.
        pli_rows = frappe.get_all(
            "Pick List Item", filters={"bucket": bucket_id, "issued": 0}, fields=["name", "parent"]
        )
        synced_opls = []
        for row in pli_rows:
            frappe.db.set_value("Pick List Item", row.name, "shelf", to_shelf_id, update_modified=False)
            synced_opls.append(row.parent)
        synced_opls = sorted(set(synced_opls))

        # Sync Bucket Allocation Status shelf fields, if a row exists per variety.
        bas_updated = []
        for src in source_items:
            bas_name = frappe.db.get_value(
                "Bucket Allocation Status", {"bucket_id": bucket_id, "item_code": src.variety}, "name"
            )
            if bas_name:
                frappe.db.set_value(
                    "Bucket Allocation Status", bas_name,
                    {"shelf_location": to_shelf_id, "shelf_farm": to_farm},
                    update_modified=False,
                )
                bas_updated.append(bas_name)

        # Shelving Log: close the row this Shelf Item row opened (correlated by
        # shelf_item name), open a new "Shelved" row for the destination.
        for i, src in enumerate(source_items):
            open_log = frappe.db.get_value(
                "Shelving Log",
                {"shelf_item": src.name, "reason": "Shelved", "removed_on": ["is", "not set"]},
                "name",
            )
            if open_log:
                frappe.db.set_value(
                    "Shelving Log", open_log,
                    {"removed_on": frappe.utils.now(), "reason": "Transferred (Shelf-to-Shelf)"},
                    update_modified=False,
                )
            _write_shelved_log(new_items[i], to_shelf_id, to_farm)

        frappe.db.commit()
        frappe.response["data"] = {
            "status": "success",
            "message": "Bucket {0} moved from {1} to {2} ({3} stems).".format(
                bucket_id, from_shelf_id, to_shelf_id, total_qty
            ),
            "payload": {
                "bucket_id": bucket_id,
                "from_shelf_id": from_shelf_id,
                "to_shelf_id": to_shelf_id,
                "stems": total_qty,
                "synced_opls": synced_opls,
                "bas_updated": bas_updated,
            },
        }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "transferBucket Error")
        frappe.response["data"] = {
            "status": "error",
            "reason": "unknown_error",
            "message": "An unexpected error occurred: {0}".format(str(e)),
        }


@frappe.whitelist()
def createOfflineIssuingEntry():
    """Report that a bucket was physically removed from its shelf for a reason
    other than a normal sales issue or a Discard-Request-driven discard (e.g.
    damage, quality hold, internal use). Posts a real stock-ledger movement
    (Stock Entry Type "Offline Issuing") from the CURRENT Shelf Item quantities
    (not the original receiving quantities -- a bucket may already have been
    partially issued), then clears the shelf and closes the Shelving Log.
    Blocked outright if the bucket is currently allocated to a sales order --
    use normal issuing for that instead."""
    try:
        data = frappe.request.get_json() or {}
        bucket_id = data.get("bucket_id")
        reason = (data.get("reason") or "").strip()

        if not bucket_id:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "bucket_id_not_null",
                "message": "Bucket ID is missing.",
                "payload": {},
            }
            return
        if not reason:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "reason_not_null",
                "message": "A reason is required to report an offline removal.",
                "payload": {"bucket_id": bucket_id},
            }
            return

        shelf_items = frappe.get_all(
            "Shelf Item",
            filters={"bucket_id": bucket_id},
            fields=["name", "parent", "variety", "greenhouse", "warehouse", "stem_qty", "stem_length", "farm"],
        )
        if not shelf_items:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "not_on_shelf",
                "message": "This bucket is not currently on any shelf.",
                "payload": {"bucket_id": bucket_id},
            }
            return

        allocated_orders = set()
        for si in shelf_items:
            bas_name = frappe.db.get_value(
                "Bucket Allocation Status", {"bucket_id": bucket_id, "item_code": si.variety}, "name"
            )
            if not bas_name:
                continue
            bas_doc = frappe.get_doc("Bucket Allocation Status", bas_name)
            if (bas_doc.allocated_quantity or 0) > 0:
                for row in bas_doc.bucket_allocations:
                    if not row.cancelled and not row.issued and row.sales_order:
                        allocated_orders.add(row.sales_order)
        if allocated_orders:
            frappe.response["data"] = {
                "status": "failed",
                "reason": "bucket_allocated",
                "message": "This bucket is allocated to order(s) {0} -- use normal issuing "
                "instead of an offline removal report.".format(", ".join(sorted(allocated_orders))),
                "payload": {"bucket_id": bucket_id, "sales_orders": sorted(allocated_orders)},
            }
            return

        # frappe.defaults.get_global_default("company") is unreliable -- this site
        # has no Global Defaults.default_company configured, so it silently
        # returns None (Stock Entry then fails validation on submit). Deriving
        # from the shelf item's own Farm is more correct anyway (multi-company
        # safe) and always set, since every Shelf Item carries its farm.
        entry = frappe.new_doc("Stock Entry")
        entry.stock_entry_type = "Offline Issuing"
        entry.purpose = "Material Issue"
        entry.company = frappe.db.get_value("Farm", shelf_items[0].farm, "company")
        entry.posting_date = frappe.utils.now_datetime().date()
        entry.posting_time = frappe.utils.now_datetime().time()
        entry.set_posting_time = 1
        entry.custom_bucket_id = bucket_id
        entry.remarks = reason
        entry.from_warehouse = shelf_items[0].warehouse

        total_qty = 0
        for si in shelf_items:
            item_meta = frappe.db.get_value(
                "Item", si.variety, ["item_name", "description", "item_group", "stock_uom"], as_dict=True
            )
            recv_row = frappe.db.sql(
                """
                SELECT sed.expense_account, sed.cost_center
                FROM `tabStock Entry Detail` sed
                JOIN `tabStock Entry` se ON se.name = sed.parent
                WHERE se.stock_entry_type IN ('Receiving', 'Late Receipt')
                  AND se.custom_bucket_id = %s AND se.docstatus = 1
                  AND sed.item_code = %s
                ORDER BY se.creation DESC LIMIT 1
                """,
                (bucket_id, si.variety),
                as_dict=True,
            )
            row = entry.append("items", {})
            row.item_code = si.variety
            row.item_name = item_meta.item_name if item_meta else si.variety
            row.description = item_meta.description if item_meta else None
            row.item_group = item_meta.item_group if item_meta else None
            row.qty = si.stem_qty
            row.uom = item_meta.stock_uom if item_meta else None
            row.stock_uom = item_meta.stock_uom if item_meta else None
            row.conversion_factor = 1
            row.s_warehouse = si.warehouse
            row.allow_zero_valuation_rate = 1
            if recv_row:
                row.expense_account = recv_row[0].expense_account
                row.cost_center = recv_row[0].cost_center
            total_qty += si.stem_qty or 0

        entry.insert(ignore_permissions=True)
        entry.submit()

        for si in shelf_items:
            frappe.delete_doc("Shelf Item", si.name, force=1, ignore_permissions=True)
            frappe.db.set_value("Shelf", si.parent, "modified", frappe.utils.now())

        for si in shelf_items:
            open_log = frappe.db.get_value(
                "Shelving Log",
                {"shelf_item": si.name, "reason": "Shelved", "removed_on": ["is", "not set"]},
                "name",
            )
            if open_log:
                frappe.db.set_value(
                    "Shelving Log", open_log,
                    {"removed_on": frappe.utils.now(), "reason": "Offline Issuing"},
                    update_modified=False,
                )

        frappe.db.commit()
        frappe.response["data"] = {
            "status": "success",
            "message": "Bucket {0} reported removed offline ({1} stems). Stock entry {2}.".format(
                bucket_id, total_qty, entry.name
            ),
            "payload": {"bucket_id": bucket_id, "stems": total_qty, "stock_entry": entry.name},
        }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "createOfflineIssuingEntry Error")
        frappe.response["data"] = {
            "status": "error",
            "reason": "unknown_error",
            "message": "An unexpected error occurred: {0}".format(str(e)),
        }

