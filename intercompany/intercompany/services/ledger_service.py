import frappe
from frappe.utils import escape_html, now_datetime

# One Intercompany Ledger row per source document. `create_transaction` opens it;
# `log_ic` stamps the latest event onto that same row and keeps the earlier ones
# as comments, so a document never spreads across several ledger rows.


def create_transaction(
	doc,
	rel,
	status="Pending",
	target_doctype=None,
	target_name=None,
	policy_reason=None,
	fx_rate=None,
	error_message=None,
	submit=False,
):
	existing = frappe.db.exists(
		"Intercompany Ledger",
		{"entry_type": "Transaction", "source_doctype": doc.doctype, "source_name": doc.name},
	)
	if existing:
		return existing

	target_company = rel.company_b if doc.company == rel.company_a else rel.company_a

	entry = frappe.new_doc("Intercompany Ledger")
	entry.entry_type = "Transaction"
	entry.source_company = doc.company
	entry.target_company = target_company
	entry.source_doctype = doc.doctype
	entry.source_name = doc.name
	entry.amount = (
		getattr(doc, "base_grand_total", None)
		or getattr(doc, "grand_total", None)
		or getattr(doc, "total_debit", None)
		or 0
	)
	entry.currency = getattr(doc, "currency", None)
	entry.fx_rate = fx_rate
	entry.status = status
	entry.target_doctype = target_doctype
	entry.target_name = target_name
	entry.policy_reason = policy_reason
	entry.error_message = error_message
	entry.insert(ignore_permissions=True)
	if submit:
		entry.submit()
	return entry.name


def log_ic(doc, action, status="Success", message="", company=None):
	"""Record an intercompany event against the ledger row for `doc`.

	If a row already exists it is stamped in place and the event is appended to
	its comment trail. Only an event with nothing to attach to — a missing
	mapping, an error raised before the row was opened — gets a row of its own.
	"""
	entry = _entry_for(doc)
	if entry:
		_stamp_event(entry, action, status, message)
		return entry.name

	entry = frappe.new_doc("Intercompany Ledger")
	entry.entry_type = "Event"
	entry.source_doctype = doc.doctype
	entry.source_name = doc.name
	# Standalone events have no target side, so the company must be supplied or
	# read off the source, else the row is invisible to permission_query_conditions.
	entry.source_company = company or getattr(doc, "company", None)
	entry.action = action
	entry.status = status
	entry.message = message
	entry.timestamp = now_datetime()
	# An audit row must be writable about any document, including one being
	# cancelled — cascade_cancel logs from inside the source doc's on_cancel,
	# by which point its docstatus is already 2 and link validation would refuse.
	entry.flags.ignore_links = True
	entry.insert(ignore_permissions=True)
	entry.submit()
	_comment(entry, action, status, message)
	return entry.name


def _entry_for(doc):
	"""The ledger row this event belongs to, if one is already open."""
	if doc.doctype == "Intercompany Ledger":
		# accept()/reject() log against the ledger row itself.
		return doc
	name = frappe.db.get_value(
		"Intercompany Ledger",
		{"entry_type": "Transaction", "source_doctype": doc.doctype, "source_name": doc.name},
		"name",
	)
	return frappe.get_doc("Intercompany Ledger", name) if name else None


def _stamp_event(entry, action, status, message):
	entry.db_set("action", action, update_modified=False)
	entry.db_set("message", message, update_modified=False)
	entry.db_set("timestamp", now_datetime(), update_modified=False)
	# The row's own status tracks the approval workflow, so an event must not
	# overwrite it — except a failure, which is terminal and is a valid status on
	# both entry types.
	if status == "Failed":
		entry.db_set("status", "Failed", update_modified=False)
	_comment(entry, action, status, message)


def _comment(entry, action, status, message):
	text = f"<b>{escape_html(action)}</b> — {escape_html(status)}"
	if message:
		text += f"<br>{escape_html(str(message))}"
	entry.add_comment("Comment", text)
