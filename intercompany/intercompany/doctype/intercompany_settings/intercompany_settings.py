# Copyright (c) 2026, Intercompany and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class IntercompanySettings(Document):
	def validate(self):
		self.validate_unique_active_source()

	def validate_unique_active_source(self):
		"""One Active row per Source DocType.

		Mapping resolution takes the first Active row matching the source
		DocType, so a second Active row for the same source would be dead
		configuration that silently never applies.
		"""
		seen = {}
		for row in self.document_mapping or []:
			if (row.status or "Active") != "Active":
				continue
			if row.source_doctype in seen:
				frappe.throw(
					_("Rows {0} and {1}: duplicate Active mapping for source {2}. Disable one of them.").format(
						seen[row.source_doctype], row.idx, frappe.bold(row.source_doctype)
					)
				)
			seen[row.source_doctype] = row.idx
