# Copyright (c) 2026, jyothi.p@quarkcs.com and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestIntercompanyInbox(FrappeTestCase):
	def test_doctype_meta(self):
		meta = frappe.get_meta("Intercompany Inbox")
		self.assertTrue(meta.is_submittable)
		fieldnames = {f.fieldname for f in meta.fields}
		for required in ("target_doctype", "target_name", "policy_reason", "error_message", "accepted_by", "accepted_on"):
			self.assertIn(required, fieldnames)

	def test_bulk_accept_signature(self):
		from intercompany.intercompany.doctype.intercompany_inbox.intercompany_inbox import bulk_accept
		self.assertTrue(callable(bulk_accept))
