from frappe.model.document import Document


class CorrectiveActionReport(Document):
	def validate(self):
		sync_issues(self)


def sync_issues(car):
	"""The Issues table is the record; `issue` is its one-line summary, which notifications, list views and the
	open-CAR check read. A CAR saved with only the summary text (how the QC endpoints create them, and every
	CAR made before the table) gets its table filled from that text."""
	from upande_quality.mobile.api import car_concerns

	if not car.get("issues") and car.issue:
		for concern, stems in car_concerns(car.issue):
			if concern:
				car.append("issues", {"issue": concern, "stems": stems})
	if car.get("issues"):
		car.issue = ", ".join(f"{r.issue} ({r.stems})" if r.stems else r.issue for r in car.issues)
