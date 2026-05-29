import frappe


def log_ic(doc, action, status="Success", message=""):
	log = frappe.new_doc("Intercompany Log")
	log.reference_doctype = doc.doctype
	log.reference_name = doc.name
	log.company = getattr(doc, "company", None)
	log.action = action
	log.status = status
	log.message = message
	log.insert(ignore_permissions=True)
	return log.name
