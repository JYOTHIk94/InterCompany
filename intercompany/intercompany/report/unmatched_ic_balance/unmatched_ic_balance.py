import frappe


def execute(filters=None):
	columns = [
		{"label": "Relationship", "fieldname": "relationship", "fieldtype": "Link", "options": "Intercompany Rule", "width": 140},
		{"label": "Company A", "fieldname": "company_a", "fieldtype": "Link", "options": "Company", "width": 140},
		{"label": "Company B", "fieldname": "company_b", "fieldtype": "Link", "options": "Company", "width": 140},
		{"label": "A Due-from B", "fieldname": "a_due_from", "fieldtype": "Currency", "width": 130},
		{"label": "B Due-to A", "fieldname": "b_due_to", "fieldtype": "Currency", "width": 130},
		{"label": "Variance", "fieldname": "variance", "fieldtype": "Currency", "width": 130},
	]

	rows = []
	for rel in frappe.get_all(
		"Intercompany Rule",
		fields=["name", "company_a", "company_b", "due_from_a", "due_to_b"],
	):
		a_due_from = _balance(rel.due_from_a, rel.company_a)
		b_due_to = _balance(rel.due_to_b, rel.company_b)
		variance = (a_due_from or 0) - (b_due_to or 0)
		if abs(variance) > 0.01:
			rows.append({
				"relationship": rel.name,
				"company_a": rel.company_a,
				"company_b": rel.company_b,
				"a_due_from": a_due_from,
				"b_due_to": b_due_to,
				"variance": variance,
			})

	return columns, rows


def _balance(account, company):
	if not account or not company:
		return 0
	res = frappe.db.sql(
		"""select sum(debit) - sum(credit) as bal
		   from `tabGL Entry`
		   where account = %s and company = %s and is_cancelled = 0""",
		(account, company),
	)
	return (res and res[0][0]) or 0
