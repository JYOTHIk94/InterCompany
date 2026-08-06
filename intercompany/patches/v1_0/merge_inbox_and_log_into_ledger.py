import frappe

OLD_DOCTYPES = ("Intercompany Inbox", "Intercompany Log")

# Columns copied verbatim from the old tables onto the ledger row.
INBOX_FIELDS = (
	"source_company",
	"source_doctype",
	"source_name",
	"target_company",
	"target_doctype",
	"target_name",
	"amount",
	"currency",
	"fx_rate",
	"status",
	"policy_reason",
	"retry_count",
	"last_attempt",
	"accepted_by",
	"accepted_on",
	"error_message",
)
LOG_FIELDS = ("source_doctype", "source_name", "action", "status", "message")

# Frappe stamps these itself on insert, so they are restored afterwards to keep
# the ledger's chronology and authorship faithful to the rows it replaces.
PRESERVED = ("creation", "modified", "owner", "modified_by")


def execute():
	"""Fold Intercompany Inbox + Intercompany Log into Intercompany Ledger.

	Inbox rows become entry_type=Transaction, Log rows become entry_type=Event.
	Runs pre-model-sync so the old tables are still intact while being read, and
	so the ledger exists before any hook resolves against it.
	"""
	frappe.reload_doc("intercompany", "doctype", "intercompany_ledger")

	if not any(frappe.db.table_exists(dt) for dt in OLD_DOCTYPES):
		_drop_old_doctypes()
		return

	inbox_map = _migrate_inbox()
	events = _migrate_logs(inbox_map)

	_clear_stale_ui_records()
	_drop_old_doctypes()

	print(f"Ledger: {len(inbox_map)} Transaction + {events} Event entries")


def _migrate_inbox():
	"""Returns {old inbox name: new ledger name} so log rows can be re-pointed."""
	mapping = {}
	if not frappe.db.table_exists("Intercompany Inbox"):
		return mapping

	rows = frappe.db.get_all(
		"Intercompany Inbox",
		fields=["name", "docstatus", *PRESERVED, *INBOX_FIELDS],
		order_by="creation asc",
	)
	for row in rows:
		payload = {f: row.get(f) for f in INBOX_FIELDS}
		payload["entry_type"] = "Transaction"
		# The inbox had no timestamp of its own; creation is the honest stand-in.
		payload["timestamp"] = row.creation
		mapping[row.name] = _insert(payload, row)
	return mapping


def _migrate_logs(inbox_map):
	if not frappe.db.table_exists("Intercompany Log"):
		return 0

	rows = frappe.db.get_all(
		"Intercompany Log",
		fields=[
			"name",
			"company",
			"timestamp",
			*PRESERVED,
			"reference_doctype as source_doctype",
			"reference_name as source_name",
			"action",
			"status",
			"message",
		],
		order_by="creation asc",
	)
	count = 0
	orphans = 0
	for row in rows:
		payload = {f: row.get(f) for f in LOG_FIELDS}
		payload["entry_type"] = "Event"
		payload["source_company"] = row.company
		payload["timestamp"] = row.timestamp or row.creation

		# Events logged against an inbox row must follow it to its ledger entry,
		# or they would dangle at a DocType that no longer exists.
		if payload["source_doctype"] == "Intercompany Inbox":
			new_name = inbox_map.get(payload["source_name"])
			if not new_name:
				# Already dangling before this patch ran: the inbox row it named
				# had been deleted, so there is nothing to re-point it at.
				orphans += 1
				continue
			payload["source_doctype"] = "Intercompany Ledger"
			payload["source_name"] = new_name

		# Events are append-only; they land submitted regardless of the old row.
		_insert(payload, row, docstatus=1)
		count += 1

	if orphans:
		print(f"  skipped {orphans} log row(s) naming an Intercompany Inbox that no longer existed")
	return count


def _insert(payload, row, docstatus=None):
	doc = frappe.new_doc("Intercompany Ledger")
	doc.update(payload)
	doc.flags.ignore_permissions = True
	doc.flags.ignore_validate = True
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	doc.insert()

	final_docstatus = row.docstatus if docstatus is None else docstatus
	restore = {f: row.get(f) for f in PRESERVED if row.get(f)}
	restore["docstatus"] = final_docstatus
	for field, value in restore.items():
		frappe.db.set_value(
			"Intercompany Ledger", doc.name, field, value, update_modified=False
		)
	return doc.name


def _clear_stale_ui_records():
	"""Drop UI records still pointing at the old doctypes.

	They are all fixtures, so `bench migrate` re-imports them against the ledger
	later in the same run. Clearing them first is what lets the DocType delete
	go through without a link check tripping.
	"""
	for doctype, field in (
		("Workspace Shortcut", "link_to"),
		("Workspace Link", "link_to"),
		("Number Card", "document_type"),
		("Dashboard Chart", "document_type"),
	):
		if not frappe.db.table_exists(doctype):
			continue
		for name in frappe.db.get_all(
			doctype, filters={field: ["in", OLD_DOCTYPES]}, pluck="name"
		):
			frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)

	# Renamed alongside the doctype it renders.
	if frappe.db.exists("Custom HTML Block", "Intercompany Inbox Activity"):
		frappe.delete_doc(
			"Custom HTML Block", "Intercompany Inbox Activity", force=1, ignore_permissions=True
		)


def _drop_old_doctypes():
	for doctype in OLD_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		frappe.delete_doc("DocType", doctype, force=1, ignore_permissions=True)
		if frappe.db.table_exists(doctype):
			frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{doctype}`")
		print(f"Dropped DocType {doctype}")
