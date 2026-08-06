import frappe
from frappe.utils import escape_html


def execute():
	"""Collapse the ledger to one row per source document.

	The latest event is stamped on the surviving row and every event — including
	that latest one — is appended to its comment trail. Events belonging to a
	document that has a Transaction row fold into it; events for a document that
	never got one (a missing mapping, an error raised before the row was opened)
	collapse onto the most recent of themselves.
	"""
	frappe.reload_doc("intercompany", "doctype", "intercompany_ledger")

	events = frappe.db.get_all(
		"Intercompany Ledger",
		filters={"entry_type": "Event"},
		fields=["name", "source_doctype", "source_name", "action", "status", "message", "timestamp"],
		order_by="timestamp asc, creation asc",
	)
	if not events:
		return

	under_transaction, standalone = {}, {}
	for ev in events:
		txn = _transaction_for(ev)
		if txn:
			under_transaction.setdefault(txn, []).append(ev)
		else:
			standalone.setdefault((ev.source_doctype, ev.source_name), []).append(ev)

	folded = 0
	for txn_name, rows in under_transaction.items():
		folded += _collapse(txn_name, rows, drop=rows)

	for rows in standalone.values():
		# No Transaction to fold into, so the most recent event becomes the row.
		folded += _collapse(rows[-1].name, rows, drop=rows[:-1])

	print(
		f"Folded {folded} Event row(s): {len(under_transaction)} Transaction row(s) "
		f"absorbed their trail, {len(standalone)} document(s) collapsed onto a single Event row"
	)


def _collapse(keeper_name, rows, drop):
	"""Stamp `rows`' latest event on `keeper_name`, comment them all, delete `drop`."""
	keeper = frappe.get_doc("Intercompany Ledger", keeper_name)
	for ev in rows:
		text = f"<b>{escape_html(ev.action or '')}</b> — {escape_html(ev.status or '')}"
		if ev.message:
			text += f"<br>{escape_html(str(ev.message))}"
		keeper.add_comment("Comment", text)

	latest = rows[-1]
	keeper.db_set("action", latest.action, update_modified=False)
	keeper.db_set("message", latest.message, update_modified=False)
	keeper.db_set("timestamp", latest.timestamp, update_modified=False)
	if any(ev.status == "Failed" for ev in rows):
		keeper.db_set("status", "Failed", update_modified=False)

	for ev in drop:
		# Event rows were submitted to freeze them; drop to cancelled first,
		# because delete_doc refuses a submitted record even with force.
		frappe.db.set_value("Intercompany Ledger", ev.name, "docstatus", 2, update_modified=False)
		frappe.delete_doc(
			"Intercompany Ledger", ev.name, force=1, ignore_permissions=True, delete_permanently=True
		)
	return len(drop)


def _transaction_for(ev):
	"""The Transaction row this event belongs to, or None."""
	if ev.source_doctype == "Intercompany Ledger":
		# Events raised by accept()/reject() already name their ledger row.
		return ev.source_name if frappe.db.exists("Intercompany Ledger", ev.source_name) else None

	return frappe.db.get_value(
		"Intercompany Ledger",
		{
			"entry_type": "Transaction",
			"source_doctype": ev.source_doctype,
			"source_name": ev.source_name,
		},
		"name",
	)
