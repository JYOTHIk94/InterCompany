# Copyright (c) 2026, jyothi.p@quarkcs.com and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestIntercompanyLedger(FrappeTestCase):
	def test_doctype_meta(self):
		meta = frappe.get_meta("Intercompany Ledger")
		self.assertTrue(meta.is_submittable)
		fieldnames = {f.fieldname for f in meta.fields}
		# Transaction side (was Intercompany Inbox)
		for required in (
			"entry_type",
			"target_doctype",
			"target_name",
			"policy_reason",
			"error_message",
			"accepted_by",
			"accepted_on",
		):
			self.assertIn(required, fieldnames)
		# Event side (was Intercompany Log)
		for required in ("action", "message", "timestamp"):
			self.assertIn(required, fieldnames)

	def test_bulk_accept_signature(self):
		from intercompany.intercompany.doctype.intercompany_ledger.intercompany_ledger import (
			bulk_accept,
		)
		self.assertTrue(callable(bulk_accept))

	def test_transaction_rejects_event_status(self):
		entry = frappe.new_doc("Intercompany Ledger")
		entry.entry_type = "Transaction"
		entry.source_company = entry.target_company = frappe.db.get_value("Company", {}, "name")
		entry.source_doctype = "Company"
		entry.source_name = entry.source_company
		entry.status = "Queued"  # an Event status
		self.assertRaises(frappe.ValidationError, entry.insert)

	def test_event_requires_action(self):
		entry = frappe.new_doc("Intercompany Ledger")
		entry.entry_type = "Event"
		entry.source_doctype = "Company"
		entry.source_name = frappe.db.get_value("Company", {}, "name")
		entry.status = "Success"
		self.assertRaises(frappe.ValidationError, entry.insert)
