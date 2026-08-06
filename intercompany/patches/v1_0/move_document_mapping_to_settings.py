import frappe

MAPPING_FIELDS = (
	"source_doctype",
	"target_doctype",
	"mapping_rule",
	"pricing_rule",
	"markup_pct",
	"status",
)


def execute():
	"""Move `document_mapping` rows off each Intercompany Rule onto the new
	`Intercompany Settings` single, where mappings are now defined globally.

	Runs pre-model-sync so the rows are read while the Rule still declares the
	field, and so the single exists before anything tries to resolve a mapping.
	"""
	frappe.reload_doc("intercompany", "doctype", "intercompany_settings")

	rows = frappe.db.get_all(
		"Intercompany Document Mapping",
		filters={"parenttype": "Intercompany Rule", "parentfield": "document_mapping"},
		fields=["parent", "idx", *MAPPING_FIELDS],
		order_by="parent asc, idx asc",
	)
	if not rows:
		return

	settings = frappe.get_single("Intercompany Settings")
	kept = {row.source_doctype: row for row in (settings.document_mapping or [])}

	# Mappings were per-relationship and are now global, so identical rows across
	# relationships collapse into one. Report any that differ — the first row wins
	# and the rest are dropped, which is a config change worth seeing.
	conflicts = []
	for row in rows:
		existing = kept.get(row.source_doctype)
		if existing:
			if any(existing.get(f) != row.get(f) for f in MAPPING_FIELDS):
				conflicts.append((row.parent, row.source_doctype))
			continue
		kept[row.source_doctype] = settings.append(
			"document_mapping", {f: row.get(f) for f in MAPPING_FIELDS}
		)

	settings.flags.ignore_permissions = True
	settings.save()

	frappe.db.delete(
		"Intercompany Document Mapping",
		{"parenttype": "Intercompany Rule", "parentfield": "document_mapping"},
	)

	print(f"Moved {len(settings.document_mapping)} document mapping(s) to Intercompany Settings")
	for parent, source in conflicts:
		print(f"  dropped divergent mapping for {source} from {parent}")
