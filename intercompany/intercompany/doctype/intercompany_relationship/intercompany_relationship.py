# Copyright (c) 2026, jyothi.p@quarkcs.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class IntercompanyRelationship(Document):
	def validate(self):
		self.validate_companies()
		self.validate_accounts()
		self.validate_uniqueness()
		self.validate_internal_parties()

	def validate_companies(self):
		if self.company_a == self.company_b:
			frappe.throw(_("Company A and Company B cannot be the same"))

	def validate_accounts(self):
		required = {
			"due_to_a": self.due_to_a,
			"due_from_a": self.due_from_a,
			"due_to_b": self.due_to_b,
			"due_from_b": self.due_from_b,
		}
		missing = [k for k, v in required.items() if not v]
		if missing:
			frappe.throw(_("All Due-to/Due-from accounts are mandatory: {0}").format(", ".join(missing)))

		for fld, company in (
			("due_to_a", self.company_a),
			("due_from_a", self.company_a),
			("due_to_b", self.company_b),
			("due_from_b", self.company_b),
		):
			acct_company = frappe.db.get_value("Account", self.get(fld), "company")
			if acct_company and acct_company != company:
				frappe.throw(
					_("{0} must belong to {1}, not {2}").format(fld, company, acct_company)
				)

	def validate_uniqueness(self):
		dup = frappe.db.exists(
			"Intercompany Relationship",
			{
				"name": ["!=", self.name],
				"company_a": ["in", [self.company_a, self.company_b]],
				"company_b": ["in", [self.company_a, self.company_b]],
			},
		)
		if dup:
			frappe.throw(
				_("A relationship for this company pair already exists: {0}").format(dup)
			)

	def validate_internal_parties(self):
		if self.internal_customer_b:
			rep = frappe.db.get_value("Customer", self.internal_customer_b, "represents_company")
			if rep and rep != self.company_a:
				frappe.throw(
					_("Internal Customer in B must represent {0}").format(self.company_a)
				)
		if self.internal_customer_a:
			rep = frappe.db.get_value("Customer", self.internal_customer_a, "represents_company")
			if rep and rep != self.company_b:
				frappe.throw(
					_("Internal Customer in A must represent {0}").format(self.company_b)
				)
