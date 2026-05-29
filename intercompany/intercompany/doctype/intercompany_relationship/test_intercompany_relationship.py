# Copyright (c) 2026, jyothi.p@quarkcs.com and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestIntercompanyRelationship(FrappeTestCase):
	def test_imports_resolve(self):
		"""Regression: services and overrides must be importable."""
		import intercompany.intercompany.services.inbox_service  # noqa: F401
		import intercompany.intercompany.services.log_service  # noqa: F401
		import intercompany.intercompany.services.posting_service  # noqa: F401
		import intercompany.intercompany.services.fx_service  # noqa: F401
		import intercompany.intercompany.overrides.sales_invoice  # noqa: F401
		import intercompany.intercompany.overrides.delivery_note  # noqa: F401
		import intercompany.intercompany.overrides.journal_entry  # noqa: F401

	def test_same_company_rejected(self):
		rel = frappe.new_doc("Intercompany Relationship")
		rel.company_a = "_Test Company"
		rel.company_b = "_Test Company"
		with self.assertRaises(frappe.ValidationError):
			rel.validate_companies()
