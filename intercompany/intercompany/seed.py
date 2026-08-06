"""Idempotent seed data for the intercompany Phase-1 demo.

Run from bench:
    bench --site <site> execute intercompany.intercompany.seed.seed_demo

Creates two companies (QCS Holding, QCS Logistics), the Due-to/Due-from
accounts in both, a shared Item, an Internal Customer in B (representing A),
an Internal Supplier in A (representing B), and an Intercompany Rule
with an SI->PI mapping row. Re-runs are no-ops.
"""

import frappe
from frappe.utils import nowdate

CO_A = "QCS Holding"
CO_B = "QCS Logistics"
CO_C = "QCS UAE"
ABBR_A = "QCSH"
ABBR_B = "QCSL"
ABBR_C = "QCSU"
CURRENCY = "SAR"
CURRENCY_C = "AED"
# 1 SAR = 0.9793 AED roughly; 1 AED = 1.0211 SAR (matches the design mockup)
SAR_TO_AED = 0.9793
AED_TO_SAR = 1.0211
ITEM_CODE = "IC-DEMO-ITEM"
STOCK_ITEM_CODE = "IC-DEMO-STOCK-ITEM"
WAREHOUSE_A = "IC Stores"  # display name; full name will be "IC Stores - QCSH"
WAREHOUSE_B = "IC Stores"  # full name will be "IC Stores - QCSL"


def seed_demo():
	_ensure_company(CO_A, ABBR_A, CURRENCY)
	_ensure_company(CO_B, ABBR_B, CURRENCY)
	_ensure_company(CO_C, ABBR_C, CURRENCY_C)
	_ensure_company_defaults(CO_A, ABBR_A)
	_ensure_company_defaults(CO_B, ABBR_B)
	_ensure_company_defaults(CO_C, ABBR_C)

	# CO_A ↔ CO_B clearing accounts (both SAR)
	due_to_a = _ensure_clearing_account("Due to QCS Logistics", CO_A, ABBR_A, "Liability", "Current Liabilities")
	due_from_a = _ensure_clearing_account("Due from QCS Logistics", CO_A, ABBR_A, "Asset", "Current Assets")
	due_to_b = _ensure_clearing_account("Due to QCS Holding", CO_B, ABBR_B, "Liability", "Current Liabilities")
	due_from_b = _ensure_clearing_account("Due from QCS Holding", CO_B, ABBR_B, "Asset", "Current Assets")

	# CO_A ↔ CO_C clearing accounts (A-side SAR, C-side AED)
	due_to_a_uae = _ensure_clearing_account("Due to QCS UAE", CO_A, ABBR_A, "Liability", "Current Liabilities")
	due_from_a_uae = _ensure_clearing_account("Due from QCS UAE", CO_A, ABBR_A, "Asset", "Current Assets")
	due_to_c = _ensure_clearing_account("Due to QCS Holding", CO_C, ABBR_C, "Liability", "Current Liabilities")
	due_from_c = _ensure_clearing_account("Due from QCS Holding", CO_C, ABBR_C, "Asset", "Current Assets")

	_ensure_price_list("Standard Selling SAR", CURRENCY, selling=1)
	_ensure_price_list("Standard Buying SAR", CURRENCY, buying=1)
	_ensure_price_list("Standard Selling AED", CURRENCY_C, selling=1)
	_ensure_price_list("Standard Buying AED", CURRENCY_C, buying=1)
	_ensure_item(ITEM_CODE)
	wh_a = _ensure_warehouse(WAREHOUSE_A, CO_A, ABBR_A)
	wh_b = _ensure_warehouse(WAREHOUSE_B, CO_B, ABBR_B)
	_ensure_stock_item(STOCK_ITEM_CODE, wh_a, wh_b)

	_ensure_currency_exchange(CURRENCY, CURRENCY_C, SAR_TO_AED)
	_ensure_currency_exchange(CURRENCY_C, CURRENCY, AED_TO_SAR)

	# CO_A ↔ CO_B (same currency)
	internal_customer = _ensure_internal_customer(CO_B, allowed=[CO_A, CO_B])
	internal_supplier = _ensure_internal_supplier(CO_A, allowed=[CO_A, CO_B, CO_C])
	rel_ab = _ensure_relationship(
		"AB",
		CO_A, CO_B,
		due_to_a, due_from_a, due_to_b, due_from_b,
		internal_customer, internal_supplier,
	)

	# CO_A ↔ CO_C (multicurrency)
	internal_customer_uae = _ensure_internal_customer(CO_C, allowed=[CO_A, CO_C])
	rel_ac = _ensure_relationship(
		"AC",
		CO_A, CO_C,
		due_to_a_uae, due_from_a_uae, due_to_c, due_from_c,
		internal_customer_uae, internal_supplier,
	)

	# Mappings are global, so they are seeded once rather than per relationship.
	_ensure_document_mappings([
		("Sales Invoice", "Purchase Invoice", "Item to Item"),
		("Delivery Note", "Purchase Receipt", "Warehouse to Warehouse"),
		("Journal Entry", "Journal Entry", "Mirror with Sign Flip"),
	])

	frappe.db.commit()
	print(f"Seed complete. Relationships: {rel_ab} (SAR↔SAR), {rel_ac} (SAR↔AED)")
	return rel_ab


# ---------- helpers ----------

def _ensure_company(name, abbr, currency=CURRENCY):
	arabic_name = "كيو سي إس"  # placeholder Arabic name to satisfy ZATCA QR hook
	tax_id = "300000000000003"  # placeholder 15-digit Saudi VAT number for ZATCA QR
	if frappe.db.exists("Company", name):
		# Backfill ZATCA-required fields on existing companies (idempotent).
		if frappe.db.get_value("Company", name, "company_name_in_arabic") != arabic_name:
			frappe.db.set_value("Company", name, "company_name_in_arabic", arabic_name)
		if not frappe.db.get_value("Company", name, "tax_id"):
			frappe.db.set_value("Company", name, "tax_id", tax_id)
		return
	doc = frappe.new_doc("Company")
	doc.company_name = name
	doc.company_name_in_arabic = arabic_name
	doc.tax_id = tax_id
	doc.abbr = abbr
	doc.default_currency = currency
	# Pick country: AED → United Arab Emirates, else Saudi Arabia (fallback to first available)
	if currency == "AED":
		doc.country = frappe.db.get_value("Country", "United Arab Emirates", "name") or \
			frappe.db.get_value("Country", "Saudi Arabia", "name") or \
			frappe.db.get_value("Country", {}, "name")
	else:
		doc.country = frappe.db.get_value("Country", "Saudi Arabia", "name") or \
			frappe.db.get_value("Country", {}, "name")
	doc.create_chart_of_accounts_based_on = "Standard Template"
	doc.chart_of_accounts = "Standard"
	doc.insert(ignore_permissions=True)


def _ensure_currency_exchange(from_currency, to_currency, rate):
	"""Idempotently create a Currency Exchange row for FX lookups."""
	if frappe.db.exists(
		"Currency Exchange",
		{"from_currency": from_currency, "to_currency": to_currency},
	):
		return
	# Ensure both currencies are enabled in the Currency master
	for c in (from_currency, to_currency):
		if frappe.db.exists("Currency", c) and not frappe.db.get_value("Currency", c, "enabled"):
			frappe.db.set_value("Currency", c, "enabled", 1)
	ce = frappe.new_doc("Currency Exchange")
	ce.from_currency = from_currency
	ce.to_currency = to_currency
	ce.exchange_rate = rate
	ce.for_buying = 1
	ce.for_selling = 1
	ce.insert(ignore_permissions=True)


def _ensure_company_defaults(company, abbr):
	"""Backfill ERPNext company defaults that the standard CoA setup may miss
	for hand-built test companies. Idempotent: only sets values that are blank."""
	defaults = {
		"default_receivable_account": ("Receivable", "Debtors", "Asset"),
		"default_payable_account": ("Payable", "Creditors", "Liability"),
		"default_income_account": (None, "Sales", "Income"),
		"default_expense_account": (None, "Cost of Goods Sold", "Expense"),
		"stock_received_but_not_billed": (
			"Stock Received But Not Billed",
			"Stock Received But Not Billed",
			"Liability",
		),
		"round_off_account": (None, "Round Off", "Expense"),
	}
	for fld, (acct_type, fallback_name, root_type) in defaults.items():
		if frappe.db.get_value("Company", company, fld):
			continue
		acct = None
		if acct_type:
			acct = frappe.db.get_value(
				"Account",
				{"company": company, "account_type": acct_type, "is_group": 0},
				"name",
			)
		if not acct and fallback_name:
			acct = frappe.db.get_value(
				"Account",
				{"company": company, "account_name": fallback_name, "is_group": 0},
				"name",
			)
		if not acct and acct_type == "Stock Received But Not Billed":
			acct = _create_account(
				"Stock Received But Not Billed", company, abbr,
				root_type="Liability", account_type="Stock Received But Not Billed",
				parent_label="Current Liabilities",
			)
		if not acct and fld == "round_off_account":
			acct = _create_account(
				"Round Off", company, abbr,
				root_type="Expense", account_type="Round Off",
				parent_label="Indirect Expenses",
			)
		if acct:
			frappe.db.set_value("Company", company, fld, acct)


def _create_account(account_name, company, abbr, root_type, account_type, parent_label):
	full = f"{account_name} - {abbr}"
	if frappe.db.exists("Account", full):
		return full
	parent = frappe.db.get_value(
		"Account",
		{"company": company, "account_name": parent_label, "is_group": 1},
		"name",
	) or frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": root_type},
		"name",
	)
	if not parent:
		return None
	acc = frappe.new_doc("Account")
	acc.account_name = account_name
	acc.parent_account = parent
	acc.company = company
	acc.account_type = account_type
	acc.root_type = root_type
	acc.is_group = 0
	acc.insert(ignore_permissions=True)
	return acc.name


def _ensure_clearing_account(account_name, company, abbr, root_type, parent_label):
	full = f"{account_name} - {abbr}"
	if frappe.db.exists("Account", full):
		return full

	parent = frappe.db.get_value(
		"Account",
		{"company": company, "account_name": parent_label, "is_group": 1},
		"name",
	)
	if not parent:
		parent = frappe.db.get_value(
			"Account",
			{"company": company, "is_group": 1, "root_type": root_type},
			"name",
		)
	if not parent:
		raise frappe.ValidationError(f"No group account found for {company} / {root_type}")

	acc = frappe.new_doc("Account")
	acc.account_name = account_name
	acc.parent_account = parent
	acc.company = company
	# Leave account_type blank — Receivable/Payable would force a Party on every JE,
	# which is wrong for an intercompany clearing ledger.
	acc.account_type = ""
	acc.root_type = root_type
	acc.is_group = 0
	acc.insert(ignore_permissions=True)
	return acc.name


def _ensure_price_list(name, currency, selling=0, buying=0):
	if frappe.db.exists("Price List", name):
		return name
	pl = frappe.new_doc("Price List")
	pl.price_list_name = name
	pl.currency = currency
	pl.selling = selling
	pl.buying = buying
	pl.enabled = 1
	pl.insert(ignore_permissions=True)
	return pl.name


def _ensure_item(code):
	if frappe.db.exists("Item", code):
		return
	itm = frappe.new_doc("Item")
	itm.item_code = code
	itm.item_name = "IC Demo Service"
	itm.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	itm.stock_uom = "Nos"
	itm.is_stock_item = 0
	itm.insert(ignore_permissions=True)


def _ensure_warehouse(name, company, abbr):
	full = f"{name} - {abbr}"
	if frappe.db.exists("Warehouse", full):
		return full
	wh = frappe.new_doc("Warehouse")
	wh.warehouse_name = name
	wh.company = company
	wh.is_group = 0
	wh.insert(ignore_permissions=True)
	return wh.name


def _ensure_stock_item(code, default_wh_a, default_wh_b):
	if frappe.db.exists("Item", code):
		return
	itm = frappe.new_doc("Item")
	itm.item_code = code
	itm.item_name = "IC Demo Stock Item"
	itm.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	itm.stock_uom = "Nos"
	itm.is_stock_item = 1
	# Default warehouse per company
	itm.append("item_defaults", {"company": CO_A, "default_warehouse": default_wh_a})
	itm.append("item_defaults", {"company": CO_B, "default_warehouse": default_wh_b})
	itm.insert(ignore_permissions=True)


def _ensure_internal_customer(represents_company, allowed=None):
	name = f"{represents_company} - Internal"
	allowed = allowed or [CO_A, CO_B]
	# This customer is used in CO_A's books, so its default price list / currency
	# should match CO_A's books (SAR), not represents_company's currency.
	default_price_list = "Standard Selling SAR" if frappe.db.exists("Price List", "Standard Selling SAR") else None
	if frappe.db.exists("Customer", name):
		_merge_allowed_companies("Customer", name, allowed)
		# Backfill default price list so SIs default to SAR currency
		if default_price_list and frappe.db.get_value("Customer", name, "default_price_list") != default_price_list:
			frappe.db.set_value("Customer", name, "default_price_list", default_price_list)
		if frappe.db.get_value("Customer", name, "default_currency") != CURRENCY:
			frappe.db.set_value("Customer", name, "default_currency", CURRENCY)
		return name
	cus = frappe.new_doc("Customer")
	cus.customer_name = name
	cus.customer_type = "Company"
	cus.customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "All Customer Groups"
	cus.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
	cus.is_internal_customer = 1
	cus.represents_company = represents_company
	cus.default_currency = CURRENCY
	if default_price_list:
		cus.default_price_list = default_price_list
	for co in allowed:
		cus.append("companies", {"company": co})
	cus.insert(ignore_permissions=True)
	return cus.name


def _ensure_internal_supplier(represents_company, allowed=None):
	name = f"{represents_company} - Internal"
	allowed = allowed or [CO_A, CO_B]
	if frappe.db.exists("Supplier", name):
		_merge_allowed_companies("Supplier", name, allowed)
		return name
	sup = frappe.new_doc("Supplier")
	sup.supplier_name = name
	sup.supplier_type = "Company"
	sup.supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name") or "All Supplier Groups"
	sup.is_internal_supplier = 1
	sup.represents_company = represents_company
	for co in allowed:
		sup.append("companies", {"company": co})
	sup.insert(ignore_permissions=True)
	return sup.name


def _merge_allowed_companies(doctype, name, allowed):
	doc = frappe.get_doc(doctype, name)
	existing = {row.company for row in (doc.companies or [])}
	added = False
	for co in allowed:
		if co not in existing:
			doc.append("companies", {"company": co})
			added = True
	if added:
		doc.save(ignore_permissions=True)


def _ensure_relationship(key, company_a, company_b, due_to_a, due_from_a, due_to_b, due_from_b, internal_customer, internal_supplier):
	existing = frappe.db.get_value(
		"Intercompany Rule",
		[
			["company_a", "=", company_a],
			["company_b", "=", company_b],
		],
		"name",
	) or frappe.db.get_value(
		"Intercompany Rule",
		[
			["company_a", "=", company_b],
			["company_b", "=", company_a],
		],
		"name",
	)
	if existing:
		return existing

	rel = frappe.new_doc("Intercompany Rule")
	rel.company_a = company_a
	rel.company_b = company_b
	rel.internal_customer_a = internal_customer  # customer in A repr B
	rel.internal_supplier_b = internal_supplier  # supplier in B repr A
	rel.posting_mode = "Threshold-based"
	rel.auto_submit_threshold = 10000
	rel.fx_policy = "Posting Date"
	rel.on_error = "Block"
	rel.due_to_a = due_to_a
	rel.due_from_a = due_from_a
	rel.due_to_b = due_to_b
	rel.due_from_b = due_from_b
	rel.insert(ignore_permissions=True)
	return rel.name


def _ensure_document_mappings(wanted):
	"""Document mappings are global — they live on the Intercompany Settings single."""
	settings = frappe.get_single("Intercompany Settings")
	have = {row.source_doctype for row in (settings.document_mapping or [])}
	added = False
	for src, tgt, rule in wanted:
		if src in have:
			continue
		settings.append("document_mapping", {
			"source_doctype": src,
			"target_doctype": tgt,
			"mapping_rule": rule,
			"pricing_rule": "1:1",
			"status": "Active",
		})
		added = True
	if added:
		settings.save(ignore_permissions=True)


@frappe.whitelist()
def seed_demo_whitelisted():
	"""Bench-callable wrapper: bench --site <site> execute intercompany.intercompany.seed.seed_demo_whitelisted"""
	return seed_demo()


# =====================================================================
# Test scenarios: drives real SIs through the dispatcher to populate
# the workspace, inbox, and log so you can visually verify the UI.
#
# Run AFTER seed_demo:
#   bench --site <site> execute intercompany.intercompany.seed.seed_test_scenarios
# =====================================================================

SCENARIOS = [
	# (label, qty, rate, action_on_inbox)
	("Auto-post small invoice",        1,    500,  None),       # under threshold → auto-posted
	("Auto-post medium invoice",       2,   1500,  None),       # under threshold → auto-posted
	("Auto-post tiny invoice",         5,     80,  None),       # under threshold → auto-posted
	("Pending review — large",         1,  25000,  None),       # over threshold → Pending
	("Pending review — very large",    3,  18000,  None),       # over threshold → Pending
	("Accepted from inbox",            1,  35000,  "accept"),   # over → then accept
	("Rejected from inbox",            1,  42000,  "reject"),   # over → then reject
]


def seed_test_scenarios():
	"""Generate a mixed set of intercompany transactions for UI/visual testing.

	Idempotent: if a SI with the same demo bill_no already exists for the
	scenario, that scenario is skipped.
	"""
	from frappe.utils import nowdate, add_days

	seed_demo()

	rel = frappe.get_doc(
		"Intercompany Rule",
		frappe.db.get_value(
			"Intercompany Rule",
			[["company_a", "in", [CO_A, CO_B]], ["company_b", "in", [CO_A, CO_B]]],
			"name",
		),
	)
	customer = rel.internal_customer_a

	created = []
	for idx, (label, qty, rate, action) in enumerate(SCENARIOS):
		marker = f"IC-DEMO-{idx:02d}"
		if frappe.db.exists("Sales Invoice", {"po_no": marker}):
			print(f"[skip] {label} — already seeded")
			continue

		si = frappe.new_doc("Sales Invoice")
		si.company = CO_A
		si.customer = customer
		si.posting_date = add_days(nowdate(), -idx)
		si.due_date = add_days(nowdate(), 30 - idx)
		si.po_no = marker
		si.currency = CURRENCY
		si.conversion_rate = 1.0
		si.price_list_currency = CURRENCY
		si.plc_conversion_rate = 1.0
		si.selling_price_list = "Standard Selling SAR"
		si.ignore_pricing_rule = 1
		si.append("items", {
			"item_code": ITEM_CODE,
			"qty": qty,
			"rate": rate,
		})
		si.submit()
		print(f"[done] {label} — SI {si.name} (SAR {qty * rate:,.2f})")
		created.append(si.name)

		# Drive inbox action if requested
		if action:
			inbox_name = frappe.db.get_value(
				"Intercompany Ledger",
				{"entry_type": "Transaction", "source_doctype": "Sales Invoice", "source_name": si.name},
				"name",
			)
			if not inbox_name:
				continue
			inbox = frappe.get_doc("Intercompany Ledger", inbox_name)
			if action == "accept":
				inbox.accept()
				print(f"        ↳ accepted ledger entry {inbox_name}")
			elif action == "reject":
				inbox.reject(reason="Demo rejection")
				print(f"        ↳ rejected ledger entry {inbox_name}")

	# Top up stock and drive Delivery Note + Journal Entry scenarios
	_seed_stock_for_dn()
	_seed_dn_scenarios(rel, customer)
	_seed_je_scenarios(rel)
	_seed_multicurrency_scenarios()

	# Add one entry that lands in the Failed bucket — manually drop an Event row.
	if not frappe.db.exists("Intercompany Ledger", {"action": "Demo failure"}):
		event = frappe.new_doc("Intercompany Ledger")
		event.entry_type = "Event"
		event.source_doctype = "Sales Invoice"
		event.source_name = created[0] if created else "DEMO"
		event.source_company = CO_A
		event.action = "Demo failure"
		event.status = "Failed"
		event.message = "Synthetic failure for KPI demo"
		event.insert(ignore_permissions=True)
		event.submit()

	frappe.db.commit()

	# Print summary
	print()
	print("=" * 60)
	print("SEEDED COUNTS")
	print("=" * 60)
	for status in ("Pending", "Accepted", "Rejected", "Failed"):
		c = frappe.db.count("Intercompany Ledger", {"entry_type": "Transaction", "status": status})
		print(f"  Ledger txn   {status:10s}: {c}")
	for status in ("Success", "Queued", "Failed"):
		c = frappe.db.count("Intercompany Ledger", {"entry_type": "Event", "status": status})
		print(f"  Ledger event {status:10s}: {c}")
	print(f"  Sales Invoices issued: {len(created)} (this run)")
	return created


@frappe.whitelist()
def reset_test_data():
	"""Wipe all demo SI/PI/Inbox/Log/JE rows so seed_test_scenarios can be re-run.
	Does NOT touch Companies, Accounts, Items, Customer/Supplier or the Relationship.

	Run: bench --site <site> execute intercompany.intercompany.seed.reset_test_data
	"""
	# Cancel + delete demo SIs (cascade will pull PIs and inboxes)
	demo_sis = frappe.get_all(
		"Sales Invoice",
		filters={"po_no": ["like", "IC-DEMO-%"]},
		fields=["name", "docstatus"],
	)
	for si in demo_sis:
		try:
			doc = frappe.get_doc("Sales Invoice", si.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Sales Invoice", si.name, force=1, ignore_permissions=True)
		except Exception as e:
			print(f"skip SI {si.name}: {e}")

	# Sweep any orphan ledger entries (both types) from past runs
	for entry in frappe.get_all("Intercompany Ledger", pluck="name"):
		try:
			doc = frappe.get_doc("Intercompany Ledger", entry)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Intercompany Ledger", entry, force=1, ignore_permissions=True)
		except Exception:
			pass
	for je in frappe.get_all(
		"Journal Entry",
		filters={"user_remark": ["like", "%IC clearing for%"]},
		pluck="name",
	):
		try:
			doc = frappe.get_doc("Journal Entry", je)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Journal Entry", je, force=1, ignore_permissions=True)
		except Exception:
			pass
	for pi in frappe.get_all(
		"Purchase Invoice",
		filters={"custom_intercompany_reference": ["like", "%-%"]},
		pluck="name",
	):
		try:
			doc = frappe.get_doc("Purchase Invoice", pi)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Purchase Invoice", pi, force=1, ignore_permissions=True)
		except Exception:
			pass
	# Demo Delivery Notes (po_no = IC-DEMO-DN-NN)
	for dn in frappe.get_all("Delivery Note", filters={"po_no": ["like", "IC-DEMO-DN-%"]}, pluck="name"):
		try:
			doc = frappe.get_doc("Delivery Note", dn)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Delivery Note", dn, force=1, ignore_permissions=True)
		except Exception:
			pass
	# Demo Purchase Receipts created by the dispatcher
	for pr in frappe.get_all("Purchase Receipt", filters={"custom_intercompany_reference": ["like", "%-%"]}, pluck="name"):
		try:
			doc = frappe.get_doc("Purchase Receipt", pr)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Purchase Receipt", pr, force=1, ignore_permissions=True)
		except Exception:
			pass
	# Demo Journal Entries (source side has user_remark "IC-DEMO-JE-...")
	for je in frappe.get_all(
		"Journal Entry",
		filters={"user_remark": ["like", "%IC-DEMO-JE-%"]},
		pluck="name",
	):
		try:
			doc = frappe.get_doc("Journal Entry", je)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Journal Entry", je, force=1, ignore_permissions=True)
		except Exception:
			pass
	# Demo MC SIs (po_no = IC-DEMO-MC-NN)
	for si in frappe.get_all("Sales Invoice", filters={"po_no": ["like", "IC-DEMO-MC-%"]}, pluck="name"):
		try:
			doc = frappe.get_doc("Sales Invoice", si)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Sales Invoice", si, force=1, ignore_permissions=True)
		except Exception as e:
			print(f"skip MC SI {si}: {e}")
	# Demo stock receipts that fed the DN
	for se in frappe.get_all("Stock Entry", filters={"remarks": ["like", "%IC-DEMO-STOCK%"]}, pluck="name"):
		try:
			doc = frappe.get_doc("Stock Entry", se)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Stock Entry", se, force=1, ignore_permissions=True)
		except Exception:
			pass
	frappe.db.commit()
	print("Test data reset. Run seed_test_scenarios next.")


# =====================================================================
# DN and JE scenarios
# =====================================================================

DN_SCENARIOS = [
	# (label, qty, rate, action_on_inbox)
	("Auto-post small delivery",       2,    400,  None),       # 800 → under threshold
	("Pending review — large delivery",10,   2500,  None),       # 25,000 → over threshold
]

JE_SCENARIOS = [
	# (label, dr_account_key, cr_account_key, amount, description)
	("Recharge from Co-A — small",  "due_from_a", "due_to_a", 1500,  "Mgmt fee recharge"),
	("Recharge from Co-A — large",  "due_from_a", "due_to_a", 22000, "Quarter-end accrual"),
]


def _seed_stock_for_dn():
	"""Material Receipt 100 units of the stock item into CO_A's warehouse so DNs can ship."""
	from frappe.utils import nowdate

	wh_a = f"{WAREHOUSE_A} - {ABBR_A}"
	if frappe.db.exists("Stock Entry", {"remarks": "IC-DEMO-STOCK seed"}):
		return
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Receipt"
	se.company = CO_A
	se.posting_date = nowdate()
	se.remarks = "IC-DEMO-STOCK seed"
	se.append("items", {
		"item_code": STOCK_ITEM_CODE,
		"qty": 100,
		"basic_rate": 100,
		"t_warehouse": wh_a,
	})
	try:
		se.submit()
		print(f"[stock] Material receipt 100 units of {STOCK_ITEM_CODE} into {wh_a}")
	except Exception as e:
		print(f"[stock] receipt failed: {e}")


def _seed_dn_scenarios(rel, customer):
	from frappe.utils import nowdate, add_days

	wh_a = f"{WAREHOUSE_A} - {ABBR_A}"
	for idx, (label, qty, rate, action) in enumerate(DN_SCENARIOS):
		marker = f"IC-DEMO-DN-{idx:02d}"
		if frappe.db.exists("Delivery Note", {"po_no": marker}):
			print(f"[skip-DN] {label} — already seeded")
			continue
		dn = frappe.new_doc("Delivery Note")
		dn.company = CO_A
		dn.customer = customer
		dn.posting_date = add_days(nowdate(), -idx)
		dn.po_no = marker
		dn.currency = CURRENCY
		dn.conversion_rate = 1.0
		dn.price_list_currency = CURRENCY
		dn.plc_conversion_rate = 1.0
		dn.selling_price_list = "Standard Selling SAR"
		dn.ignore_pricing_rule = 1
		dn.set_warehouse = wh_a
		dn.append("items", {
			"item_code": STOCK_ITEM_CODE,
			"qty": qty,
			"rate": rate,
			"warehouse": wh_a,
		})
		try:
			dn.submit()
			frappe.db.commit()
			print(f"[done-DN] {label} — DN {dn.name} (SAR {qty * rate:,.2f})")
		except Exception as e:
			print(f"[fail-DN] {label}: {e}")


MC_SCENARIOS = [
	# (label, qty, rate, action_on_inbox)
	("MC Auto-post small (SAR→AED)",   1,    900,  None),       # 900 → under 10k threshold
	("MC Pending — large (SAR→AED)",   1,  18000,  None),       # over → Pending, FX stamped
	("MC Accepted from inbox",         2,   8000,  "accept"),   # 16k over → accept
]


def _seed_multicurrency_scenarios():
	"""Drive Sales Invoices for the SAR↔AED relationship to exercise FX handling."""
	from frappe.utils import nowdate, add_days

	rel_ac_name = frappe.db.get_value(
		"Intercompany Rule",
		[["company_a", "in", [CO_A, CO_C]], ["company_b", "in", [CO_A, CO_C]]],
		"name",
	)
	if not rel_ac_name:
		print("[mc] No SAR↔AED relationship found — skipping")
		return
	rel_ac = frappe.get_doc("Intercompany Rule", rel_ac_name)
	customer = rel_ac.internal_customer_a
	if not customer:
		print("[mc] Multicurrency relationship has no internal_customer_a — skipping")
		return

	for idx, (label, qty, rate, action) in enumerate(MC_SCENARIOS):
		marker = f"IC-DEMO-MC-{idx:02d}"
		if frappe.db.exists("Sales Invoice", {"po_no": marker, "docstatus": 1}):
			print(f"[skip-MC] {label} — already seeded")
			continue
		# Clean stale drafts from prior failed runs
		for stale in frappe.get_all("Sales Invoice", filters={"po_no": marker, "docstatus": 0}, pluck="name"):
			frappe.delete_doc("Sales Invoice", stale, force=1, ignore_permissions=True)

		si = frappe.new_doc("Sales Invoice")
		si.company = CO_A  # source SAR company
		si.customer = customer
		si.posting_date = add_days(nowdate(), -idx)
		si.due_date = add_days(nowdate(), 30 - idx)
		si.po_no = marker
		si.currency = CURRENCY  # SI is in SAR (source company default)
		si.conversion_rate = 1.0
		si.price_list_currency = CURRENCY
		si.plc_conversion_rate = 1.0
		si.selling_price_list = "Standard Selling SAR"
		si.ignore_pricing_rule = 1
		si.append("items", {
			"item_code": ITEM_CODE,
			"qty": qty,
			"rate": rate,
		})
		try:
			si.submit()
			frappe.db.commit()
			print(f"[done-MC] {label} — SI {si.name} (SAR {qty * rate:,.2f})")
		except Exception as e:
			print(f"[fail-MC] {label}: {e}")
			continue

		if action:
			inbox_name = frappe.db.get_value(
				"Intercompany Ledger",
				{"entry_type": "Transaction", "source_doctype": "Sales Invoice", "source_name": si.name},
				"name",
			)
			if inbox_name:
				try:
					inbox = frappe.get_doc("Intercompany Ledger", inbox_name)
					if action == "accept":
						inbox.accept()
						print(f"        ↳ accepted {inbox_name}")
					elif action == "reject":
						inbox.reject(reason="Demo MC rejection")
						print(f"        ↳ rejected {inbox_name}")
					frappe.db.commit()
				except Exception as e:
					print(f"        ↳ {action} failed: {e}")


def _seed_je_scenarios(rel):
	from frappe.utils import nowdate, add_days

	for idx, (label, dr_key, cr_key, amount, description) in enumerate(JE_SCENARIOS):
		remark = f"IC-DEMO-JE-{idx:02d} {description}"
		if frappe.db.exists("Journal Entry", {"user_remark": remark, "docstatus": 1}):
			print(f"[skip-JE] {label} — already seeded")
			continue
		# Clean up any drafts from a previous failed attempt
		for stale in frappe.get_all("Journal Entry", filters={"user_remark": remark, "docstatus": 0}, pluck="name"):
			frappe.delete_doc("Journal Entry", stale, force=1, ignore_permissions=True)
		dr_account = getattr(rel, dr_key)
		cr_account = getattr(rel, cr_key)
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = CO_A
		je.posting_date = add_days(nowdate(), -idx)
		je.user_remark = remark
		je.append("accounts", {"account": dr_account, "debit_in_account_currency": amount})
		je.append("accounts", {"account": cr_account, "credit_in_account_currency": amount})
		try:
			je.submit()
			frappe.db.commit()
			print(f"[done-JE] {label} — JE {je.name} (SAR {amount:,.2f})")
		except Exception as e:
			print(f"[fail-JE] {label}: {e}")
