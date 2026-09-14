import frappe
from frappe.model.document import Document


class QualityReporting(Document):
    def before_save(self):
        self.set_bunches_allocated()

    def set_bunches_allocated(self):
        """Bunches Allocated = total bunches in the linked Order Pick List.

        On a Pick List Item, `qty` is expressed in bunches (UOM e.g. "Bunch (10)")
        while `stock_qty` is stems, so the OPL's total bunches is SUM(qty) across
        its Pick List Items. Derived (read-only) — recomputed on every save.
        """
        opl = self.get("custom_order_pick_list")
        if opl and frappe.db.exists("Order Pick List", opl):
            total = frappe.db.sql(
                """
                SELECT COALESCE(SUM(qty), 0)
                FROM `tabPick List Item`
                WHERE parent = %s AND parenttype = 'Order Pick List'
                """,
                opl,
            )[0][0]
            self.custom_bunches_allocated = int(total or 0)
        else:
            self.custom_bunches_allocated = 0
