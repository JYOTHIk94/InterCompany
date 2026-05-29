import frappe


def create_inbox(
	doc,
	rel,
	status="Pending",
	target_doctype=None,
	target_name=None,
	policy_reason=None,
	fx_rate=None,
	error_message=None,
):
	existing = frappe.db.exists(
		"Intercompany Inbox",
		{"source_doctype": doc.doctype, "source_name": doc.name},
	)
	if existing:
		return existing

	target_company = rel.company_b if doc.company == rel.company_a else rel.company_a

	inbox = frappe.new_doc("Intercompany Inbox")
	inbox.source_company = doc.company
	inbox.target_company = target_company
	inbox.source_doctype = doc.doctype
	inbox.source_name = doc.name
	inbox.amount = (
		getattr(doc, "base_grand_total", None)
		or getattr(doc, "grand_total", None)
		or getattr(doc, "total_debit", None)
		or 0
	)
	inbox.currency = getattr(doc, "currency", None)
	inbox.fx_rate = fx_rate
	inbox.status = status
	inbox.target_doctype = target_doctype
	inbox.target_name = target_name
	inbox.policy_reason = policy_reason
	inbox.error_message = error_message
	inbox.insert(ignore_permissions=True)
	return inbox.name
