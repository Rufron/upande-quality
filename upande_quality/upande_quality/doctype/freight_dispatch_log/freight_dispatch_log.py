import frappe
from frappe.model.document import Document
from datetime import datetime

class FreightDispatchLog(Document):
    def validate(self):
        self.calculate_time_taken_from_farm()
        self.calculate_freight_dwell_time()

    def calculate_time_taken_from_farm(self):
        if (
            self.farm_departure_date 
            and self.farm_departure_time 
            and self.freight_arrival_date 
            and self.freight_arrival_time
        ):
            dep_str = f"{self.farm_departure_date} {self.farm_departure_time}"
            arr_str = f"{self.freight_arrival_date} {self.freight_arrival_time}"
            
            fmt = "%Y-%m-%d %H:%M:%S"
            dep_dt = datetime.strptime(dep_str, fmt)
            arr_dt = datetime.strptime(arr_str, fmt)
            
            if arr_dt >= dep_dt:
                diff = arr_dt - dep_dt
                hours, remainder = divmod(diff.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                self.time_taken_from_farm = f"{hours:02}:{minutes:02}:{seconds:02}"

    def calculate_freight_dwell_time(self):
        if self.freight_arrival_time and self.departure_time:
            # Simple same-day time diff logic
            dummy_date = "2000-01-01"
            fmt = "%Y-%m-%d %H:%M:%S"
            arr_dt = datetime.strptime(f"{dummy_date} {self.freight_arrival_time}", fmt)
            dep_dt = datetime.strptime(f"{dummy_date} {self.departure_time}", fmt)
            
            if dep_dt >= arr_dt:
                diff = dep_dt - arr_dt
                hours, remainder = divmod(diff.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                self.freight_dwell_time = f"{hours:02}:{minutes:02}:{seconds:02}"