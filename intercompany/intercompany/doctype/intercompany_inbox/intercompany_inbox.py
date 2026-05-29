# Copyright (c) 2026, jyothi.p@quarkcs.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from intercompany.intercompany.services.log_service import log_ic


class IntercompanyInbox(Document):
	@frappe.whitelist()
	def accept(self):
		self._authorize()
		if self.status == "Accepted":
			return self.name
		if not (self.target_doctype and self.target_name):
			frappe.throw(_("No draft target document is linked to this inbox row"))

		target = frappe.get_doc(self.target_doctype, self.target_name)
		if target.docstatus == 0:
			target.submit()

		# Post clearing legs once the target is submitted
		from intercompany.intercompany.services.posting_service import _post_gl_legs
		rel = self._relationship()
		if rel:
			source = frappe.get_doc(self.source_doctype, self.source_name)
			_post_gl_legs(source, rel, target, self.amount or 0, fx_rate=self.fx_rate or 1.0)

		self.status = "Accepted"
		self.accepted_by = frappe.session.user
		self.accepted_on = now_datetime()
		self.save(ignore_permissions=True)
		try:
			self.submit()
		except frappe.exceptions.DocstatusTransitionError:
			pass

		log_ic(self, "Inbox accept", "Success", self.target_name)
		return self.name

	@frappe.whitelist()
	def reject(self, reason=None):
		self._authorize()
		if self.status == "Rejected":
			return self.name

		if self.target_doctype and self.target_name:
			try:
				tgt = frappe.get_doc(self.target_doctype, self.target_name)
				if tgt.docstatus == 0:
					frappe.delete_doc(self.target_doctype, self.target_name, force=1)
				elif tgt.docstatus == 1:
					tgt.cancel()
			except frappe.DoesNotExistError:
				pass

		self.status = "Rejected"
		self.error_message = reason or self.error_message
		self.save(ignore_permissions=True)
		log_ic(self, "Inbox reject", "Success", reason or "")
		return self.name

	def _authorize(self):
		rel = self._relationship()
		approver_role = (rel and rel.approver_role) or "System Manager"
		roles = set(frappe.get_roles(frappe.session.user))
		if approver_role not in roles and "System Manager" not in roles:
			frappe.throw(_("You need the {0} role to act on this inbox row").format(approver_role))

	def _relationship(self):
		if not (self.source_company and self.target_company):
			return None
		name = frappe.db.get_value(
			"Intercompany Relationship",
			[
				["company_a", "in", [self.source_company, self.target_company]],
				["company_b", "in", [self.source_company, self.target_company]],
			],
			"name",
		)
		return frappe.get_doc("Intercompany Relationship", name) if name else None


@frappe.whitelist()
def bulk_accept(names):
	import json
	if isinstance(names, str):
		names = json.loads(names)
	results = []
	for n in names:
		try:
			frappe.get_doc("Intercompany Inbox", n).accept()
			results.append({"name": n, "ok": True})
		except Exception as e:
			results.append({"name": n, "ok": False, "error": str(e)})
	return results
