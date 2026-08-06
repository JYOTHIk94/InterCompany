import frappe
from frappe.model.rename_doc import rename_doc

OLD = "Intercompany Relationship"
NEW = "Intercompany Rule"


def execute():
	"""Rename the `Intercompany Relationship` DocType to `Intercompany Rule`.

	Runs pre-model-sync so the renamed table and its Link references are in
	place before the new `intercompany_rule` JSON is imported — otherwise the
	sync would create an empty second DocType alongside the old one.
	"""
	if not frappe.db.exists("DocType", OLD):
		return

	if frappe.db.exists("DocType", NEW):
		# Sync already created the new DocType; nothing safe to rename onto.
		return

	rename_doc("DocType", OLD, NEW, force=True, ignore_permissions=True)
	frappe.reload_doc("intercompany", "doctype", "intercompany_rule")
