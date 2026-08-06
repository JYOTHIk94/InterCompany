# Copyright (c) 2026, jyothi.p@quarkcs.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from intercompany.intercompany.services.ledger_service import log_ic

# A Transaction row is the routable record for one source document: it carries the
# amount, the counter-doc and the accept/reject workflow. An Event row is one line
# of the audit trail. Both live here so the ledger is a single chronological view.
TRANSACTION_STATUSES = ("Pending", "Accepted", "Rejected", "Failed")
EVENT_STATUSES = ("Success", "Failed", "Queued")


class IntercompanyLedger(Document):
	def validate(self):
		if not self.timestamp:
			self.timestamp = now_datetime()

		if self.entry_type == "Transaction":
			self.validate_transaction()
		else:
			self.validate_event()

	def validate_transaction(self):
		for fieldname in ("source_company", "target_company"):
			if not self.get(fieldname):
				frappe.throw(
					_("{0} is required on a Transaction entry").format(
						frappe.bold(self.meta.get_label(fieldname))
					)
				)
		if self.status not in TRANSACTION_STATUSES:
			frappe.throw(
				_("Status {0} is not valid on a Transaction entry. Use one of: {1}").format(
					frappe.bold(self.status), ", ".join(TRANSACTION_STATUSES)
				)
			)

	def validate_event(self):
		if not self.action:
			frappe.throw(_("{0} is required on an Event entry").format(frappe.bold(_("Action"))))
		if self.status not in EVENT_STATUSES:
			frappe.throw(
				_("Status {0} is not valid on an Event entry. Use one of: {1}").format(
					frappe.bold(self.status), ", ".join(EVENT_STATUSES)
				)
			)

	def on_submit(self):
		"""Frappe's native Submit button lands here.

		Event rows are submitted purely to freeze them as an audit record, so they
		short-circuit. For Transaction rows, the Auto path and accept() both stamp
		status/target *before* they submit, so the rest only does real work when
		someone submits a still-Pending row straight from the form.
		"""
		if self.entry_type != "Transaction":
			return

		if self.status == "Accepted" and self.target_name:
			return

		# A row rejected by policy is closed. Submitting must not build a
		# counter-doc — the counter-doc exists for the Accepted case only.
		if self.status == "Rejected":
			frappe.throw(_("A rejected intercompany ledger entry cannot be submitted"))

		self._authorize()
		self._materialize_target()

		# The row is already at docstatus 1 here, so write through db_set rather
		# than save() — this is the post-submit mutation path.
		self.db_set("target_doctype", self.target_doctype)
		self.db_set("target_name", self.target_name)
		self.db_set("status", "Accepted")
		self.db_set("accepted_by", frappe.session.user)
		self.db_set("accepted_on", now_datetime())

		log_ic(self, "Ledger accept", "Success", self.target_name, company=self.target_company)

	@frappe.whitelist()
	def accept(self):
		self._require_transaction()
		self._authorize()
		if self.status == "Accepted":
			return self.name
		if self.status == "Rejected":
			frappe.throw(_("This entry was rejected by policy and cannot be accepted"))

		self._materialize_target()

		self.status = "Accepted"
		self.accepted_by = frappe.session.user
		self.accepted_on = now_datetime()
		self.save(ignore_permissions=True)
		try:
			self.submit()
		except frappe.exceptions.DocstatusTransitionError:
			pass

		log_ic(self, "Ledger accept", "Success", self.target_name, company=self.target_company)
		return self.name

	@frappe.whitelist()
	def reject(self, reason=None):
		self._require_transaction()
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
		log_ic(self, "Ledger reject", "Success", reason or "", company=self.target_company)
		return self.name

	def _materialize_target(self):
		"""Create-or-submit the counter doc and post the clearing legs.

		Sets self.target_doctype / self.target_name but does not persist them —
		the caller decides whether that's a save() or a db_set().
		"""
		from intercompany.intercompany.services.posting_service import (
			_create_target,
			_post_gl_legs,
			_resolve_mapping,
		)

		rel = self._relationship()
		source = frappe.get_doc(self.source_doctype, self.source_name)

		if self.target_name:
			# Threshold policy pre-builds a draft counter-doc; just submit it.
			target = frappe.get_doc(self.target_doctype, self.target_name)
			if target.docstatus == 0:
				target.submit()
		else:
			# Manual policy defers creation to here.
			if not rel:
				frappe.throw(_("No Intercompany Rule found for this ledger entry"))
			mapping = _resolve_mapping(self.source_doctype)
			if not mapping:
				frappe.throw(_("No active mapping for {0}").format(self.source_doctype))
			target = _create_target(
				source, rel, mapping, submit=True, fx_rate=self.fx_rate or 1.0
			)
			self.target_doctype = mapping.target_doctype
			self.target_name = target.name

		# Post clearing legs once the target is submitted
		if rel:
			_post_gl_legs(source, rel, target, self.amount or 0, fx_rate=self.fx_rate or 1.0)

		return target

	def _require_transaction(self):
		if self.entry_type != "Transaction":
			frappe.throw(_("Only a Transaction entry can be accepted or rejected"))

	def _authorize(self):
		rel = self._relationship()
		approver_role = (rel and rel.approver_role) or "System Manager"
		roles = set(frappe.get_roles(frappe.session.user))
		if approver_role not in roles and "System Manager" not in roles:
			frappe.throw(_("You need the {0} role to act on this ledger entry").format(approver_role))

	def _relationship(self):
		if not (self.source_company and self.target_company):
			return None
		name = frappe.db.get_value(
			"Intercompany Rule",
			[
				["company_a", "in", [self.source_company, self.target_company]],
				["company_b", "in", [self.source_company, self.target_company]],
			],
			"name",
		)
		return frappe.get_doc("Intercompany Rule", name) if name else None


@frappe.whitelist()
def bulk_accept(names):
	import json
	if isinstance(names, str):
		names = json.loads(names)
	results = []
	for n in names:
		try:
			frappe.get_doc("Intercompany Ledger", n).accept()
			results.append({"name": n, "ok": True})
		except Exception as e:
			results.append({"name": n, "ok": False, "error": str(e)})
	return results
