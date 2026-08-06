import frappe


def run():
	"""Submit a fresh MC SI and watch what happens to the PI rate."""
	from frappe.utils import nowdate
	out = []

	# Clean ALL artefacts of past debug runs (SI, PI, inbox, JE)
	for si in frappe.get_all("Sales Invoice", filters={"po_no": "IC-DEMO-MC-DEBUG"}, pluck="name"):
		# Inbox rows for this SI
		for ib in frappe.get_all("Intercompany Ledger", filters={"source_name": si}, pluck="name"):
			try: frappe.delete_doc("Intercompany Ledger", ib, force=1, ignore_permissions=True)
			except: pass
		# PIs for this SI
		for pi in frappe.get_all("Purchase Invoice", filters={"custom_intercompany_reference": si}, pluck="name"):
			try:
				d = frappe.get_doc("Purchase Invoice", pi)
				if d.docstatus == 1:
					try: d.cancel()
					except: pass
				frappe.delete_doc("Purchase Invoice", pi, force=1, ignore_permissions=True)
			except: pass
		# Clearing JEs for this SI
		for je in frappe.get_all("Journal Entry", filters={"user_remark": ["like", f"%{si}%"]}, pluck="name"):
			try:
				d = frappe.get_doc("Journal Entry", je)
				if d.docstatus == 1:
					try: d.cancel()
					except: pass
				frappe.delete_doc("Journal Entry", je, force=1, ignore_permissions=True)
			except: pass
		# Cancel + delete the SI
		try:
			d = frappe.get_doc("Sales Invoice", si)
			if d.docstatus == 1:
				try: d.cancel()
				except: pass
			frappe.delete_doc("Sales Invoice", si, force=1, ignore_permissions=True)
		except: pass
	frappe.db.commit()

	customer = frappe.db.get_value(
		"Intercompany Rule",
		{"company_a": "QCS Holding", "company_b": "QCS UAE"},
		"internal_customer_a",
	)

	si = frappe.new_doc("Sales Invoice")
	si.company = "QCS Holding"
	si.customer = customer
	si.posting_date = nowdate()
	si.due_date = nowdate()
	si.po_no = "IC-DEMO-MC-DEBUG"
	si.currency = "SAR"
	si.conversion_rate = 1.0
	si.price_list_currency = "SAR"
	si.plc_conversion_rate = 1.0
	si.selling_price_list = "Standard Selling SAR"
	si.ignore_pricing_rule = 1
	si.append("items", {"item_code": "IC-DEMO-ITEM", "qty": 1, "rate": 900})
	si.submit()

	# Now find the auto-created PI
	pi_name = frappe.db.get_value("Purchase Invoice", {"custom_intercompany_reference": si.name}, "name")
	if pi_name:
		pi = frappe.get_doc("Purchase Invoice", pi_name)
		out.append(f"PI {pi.name} ds={pi.docstatus} currency={pi.currency} gt={pi.grand_total} item_rate={pi.items[0].rate}")
	else:
		out.append("No PI created!")
	return "\n".join(out)
