import frappe
from frappe.model.document import Document
from frappe.model.naming import make_autoname

class FlowerQualityAudit(Document):
    def autoname(self):
        prefix_map = {
            "Bud Count": "bd-.###",
            "Head Size": "hs-.###",
            "Stem Weight": "sw-.###",
            "Spray Diameter": "sd-.###"
        }
        
        # Get naming pattern based on selected audit_type, fallback to generic prefix
        pattern = prefix_map.get(self.audit_type, "fqa-.###")
        
        # Generates incrementing series like bd-001, hs-001, etc.
        self.name = make_autoname(pattern)