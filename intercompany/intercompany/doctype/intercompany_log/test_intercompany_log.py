# Copyright (c) 2026, jyothi.p@quarkcs.com and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestIntercompanyLog(FrappeTestCase):
	def test_action_and_message_fields_present(self):
		meta = frappe.get_meta("Intercompany Log")
		fieldnames = {f.fieldname for f in meta.fields}
		self.assertIn("action", fieldnames)
		self.assertIn("message", fieldnames)
