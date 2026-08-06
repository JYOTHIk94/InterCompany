import frappe


def _user_companies(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	companies = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Company"},
		pluck="for_value",
	)
	return companies or None


def _company_clause(field, user):
	companies = _user_companies(user)
	if companies is None:
		return ""
	if not companies:
		return f"{field} = '__none__'"
	quoted = ", ".join(frappe.db.escape(c) for c in companies)
	return f"{field} in ({quoted})"


def ledger_query_conditions(user):
	# Event rows carry only source_company, so the target clause is a plain OR —
	# it simply never matches for them.
	clause_src = _company_clause("`tabIntercompany Ledger`.source_company", user)
	clause_tgt = _company_clause("`tabIntercompany Ledger`.target_company", user)
	if not clause_src:
		return ""
	return f"({clause_src} or {clause_tgt})"


def relationship_query_conditions(user):
	clause_a = _company_clause("`tabIntercompany Rule`.company_a", user)
	clause_b = _company_clause("`tabIntercompany Rule`.company_b", user)
	if not clause_a:
		return ""
	return f"({clause_a} or {clause_b})"
