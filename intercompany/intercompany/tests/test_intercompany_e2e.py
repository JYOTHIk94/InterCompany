"""End-to-end test that drives a real Sales Invoice through the
Intercompany dispatcher and asserts the counter PI + clearing JE + Inbox row.

Run:
    bench --site <site> run-tests --app intercompany --module intercompany.intercompany.tests.test_intercompany_e2e
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from intercompany.intercompany.seed import (
	CO_A, CO_B, ITEM_CODE, seed_demo,
)


class TestIntercompanyE2E(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_demo()
		cls.rel = frappe.get_doc(
			"Intercompany Rule",
			frappe.db.get_value(
				"Intercompany Rule",
				[["company_a", "in", [CO_A, CO_B]], ["company_b", "in", [CO_A, CO_B]]],
				"name",
			),
		)
		cls.internal_customer = cls.rel.internal_customer_a or cls.rel.internal_customer_b
		assert cls.internal_customer, "Seed did not set an internal customer on the relationship"

	# ---------- threshold under: full auto-post ----------
	def test_under_threshold_auto_posts_pi_and_inbox(self):
		si = self._make_si(qty=1, rate=500)
		si.submit()

		inbox = self._inbox_for(si)
		self.assertIsNotNone(inbox, "Inbox row should be created")
		self.assertEqual(inbox.target_doctype, "Purchase Invoice")
		self.assertEqual(inbox.source_company, CO_A)
		self.assertEqual(inbox.target_company, CO_B)
		self.assertIn(inbox.status, ("Accepted",))

		pi = frappe.get_doc("Purchase Invoice", inbox.target_name)
		self.assertEqual(pi.docstatus, 1, "PI should be auto-submitted under threshold")
		self.assertEqual(pi.company, CO_B)
		self.assertEqual(pi.custom_intercompany_reference, si.name)

		jes = frappe.get_all(
			"Journal Entry",
			filters={"user_remark": ["like", f"%{si.name}%"]},
			fields=["name", "company"],
		)
		self.assertEqual(len(jes), 2, "Two clearing JEs (one per company) should be drafted")
		companies = {j.company for j in jes}
		self.assertEqual(companies, {CO_A, CO_B})

	# ---------- threshold over: PI parked as draft, status Pending ----------
	def test_over_threshold_drafts_pi_and_parks_inbox(self):
		si = self._make_si(qty=1, rate=50000)
		si.submit()

		inbox = self._inbox_for(si)
		self.assertIsNotNone(inbox)
		self.assertEqual(inbox.status, "Pending")
		self.assertTrue(inbox.policy_reason and "Threshold" in inbox.policy_reason)

		pi = frappe.get_doc("Purchase Invoice", inbox.target_name)
		self.assertEqual(pi.docstatus, 0, "PI should remain draft awaiting accept")

	# ---------- inbox accept submits the parked PI ----------
	def test_inbox_accept_submits_pi(self):
		si = self._make_si(qty=1, rate=50000)
		si.submit()
		inbox = self._inbox_for(si)
		self.assertEqual(inbox.status, "Pending")

		inbox_doc = frappe.get_doc("Intercompany Ledger", inbox.name)
		inbox_doc.accept()

		inbox_doc.reload()
		self.assertEqual(inbox_doc.status, "Accepted")
		self.assertTrue(inbox_doc.accepted_by)
		self.assertTrue(inbox_doc.accepted_on)
		pi = frappe.get_doc("Purchase Invoice", inbox_doc.target_name)
		self.assertEqual(pi.docstatus, 1)

	# ---------- inbox reject deletes draft PI ----------
	def test_inbox_reject_removes_draft(self):
		si = self._make_si(qty=1, rate=50000)
		si.submit()
		inbox = self._inbox_for(si)
		target_name = inbox.target_name

		inbox_doc = frappe.get_doc("Intercompany Ledger", inbox.name)
		inbox_doc.reject(reason="Demo rejection")

		inbox_doc.reload()
		self.assertEqual(inbox_doc.status, "Rejected")
		self.assertFalse(
			frappe.db.exists("Purchase Invoice", target_name),
			"Rejected draft PI should be deleted",
		)

	# ---------- cancel cascade reverses the counter PI ----------
	def test_cancel_cascade_reverses_counter_pi(self):
		si = self._make_si(qty=1, rate=500)
		si.submit()
		inbox = self._inbox_for(si)
		pi_name = inbox.target_name

		si.reload()
		si.cancel()

		pi = frappe.get_doc("Purchase Invoice", pi_name)
		self.assertEqual(pi.docstatus, 2, "Counter PI must cancel when source SI cancels")

		inbox_doc = frappe.get_doc("Intercompany Ledger", inbox.name)
		self.assertEqual(inbox_doc.status, "Rejected")

	# ---------- idempotency: resubmit-after-amend does not double-post ----------
	def test_idempotency_no_duplicate_inbox(self):
		si = self._make_si(qty=1, rate=500)
		si.submit()

		# Manually re-trigger the hook — should be a no-op
		from intercompany.intercompany.services.posting_service import process_ic_event
		process_ic_event(si)

		count = frappe.db.count(
			"Intercompany Ledger",
			{"entry_type": "Transaction", "source_doctype": "Sales Invoice", "source_name": si.name},
		)
		self.assertEqual(count, 1, "Re-firing the hook must not create a second inbox row")

	# ---------- helpers ----------
	def _make_si(self, qty, rate):
		si = frappe.new_doc("Sales Invoice")
		si.company = CO_A
		si.customer = self.internal_customer
		si.posting_date = nowdate()
		si.due_date = nowdate()
		si.currency = "SAR"
		si.conversion_rate = 1.0
		si.price_list_currency = "SAR"
		si.plc_conversion_rate = 1.0
		si.selling_price_list = "Standard Selling SAR"
		si.ignore_pricing_rule = 1
		si.append("items", {
			"item_code": ITEM_CODE,
			"qty": qty,
			"rate": rate,
			"conversion_factor": 1,
		})
		return si

	def _inbox_for(self, source_doc):
		name = frappe.db.get_value(
			"Intercompany Ledger",
			{
				"entry_type": "Transaction",
				"source_doctype": source_doc.doctype,
				"source_name": source_doc.name,
			},
			"name",
		)
		return frappe.get_doc("Intercompany Ledger", name) if name else None
