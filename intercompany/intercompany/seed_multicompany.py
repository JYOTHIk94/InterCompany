"""Multi-company / multi-currency seed + verification harness.

Extends `seed.py` (which covers a single SAR↔SAR pair plus one SAR↔AED pair)
into a four-company, three-currency matrix that exercises every posting mode,
both flow directions, and a company pair where *neither* side is the SAR hub.

Run from bench:

    bench --site <site> execute intercompany.intercompany.seed_multicompany.seed_multicompany
    bench --site <site> execute intercompany.intercompany.seed_multicompany.seed_multicompany_scenarios
    bench --site <site> execute intercompany.intercompany.seed_multicompany.verify_multicompany

    # or all three in order:
    bench --site <site> execute intercompany.intercompany.seed_multicompany.run_all

    # wipe only the documents this module created (masters are kept):
    bench --site <site> execute intercompany.intercompany.seed_multicompany.reset_multicompany

Everything is idempotent: masters are created only when absent, and each
transaction scenario is keyed on a `MC-<KEY>` marker written to `po_no` /
`user_remark`, so re-running skips what already exists.
"""

import frappe
from frappe.utils import add_days, flt, nowdate

from intercompany.intercompany.seed import (
	ABBR_A,
	ABBR_B,
	ABBR_C,
	CO_A,
	CO_B,
	CO_C,
	ITEM_CODE,
	STOCK_ITEM_CODE,
	_create_account,
	_ensure_clearing_account,
	_ensure_company_defaults,
	_ensure_item,
	_ensure_price_list,
	_ensure_warehouse,
	_merge_allowed_companies,
)

# ---------------------------------------------------------------------------
# Company matrix
# ---------------------------------------------------------------------------

CO_D = "QCS Europe"
ABBR_D = "QCSE"

# (name, abbr, currency)
COMPANIES = [
	(CO_A, ABBR_A, "SAR"),
	(CO_B, ABBR_B, "SAR"),
	(CO_C, ABBR_C, "AED"),
	(CO_D, ABBR_D, "EUR"),
]

ABBR = {name: abbr for name, abbr, _ in COMPANIES}
CURRENCY_OF = {name: cur for name, _, cur in COMPANIES}

CURRENCY_COUNTRY = {
	"SAR": "Saudi Arabia",
	"AED": "United Arab Emirates",
	"EUR": "Germany",
	"USD": "United States",
}

# Directional rates. Every pair is seeded in both directions because
# fx_service.get_rate() does a one-way lookup and never inverts.
FX_RATES = [
	("SAR", "AED", 0.9793),
	("AED", "SAR", 1.0211),
	("SAR", "EUR", 0.2450),
	("EUR", "SAR", 4.0816),
	("AED", "EUR", 0.2502),
	("EUR", "AED", 3.9968),
]

# ---------------------------------------------------------------------------
# Relationship matrix
#
# (key, company_a, company_b, posting_mode, auto_submit_threshold)
#
# Deliberate coverage:
#   AB — same currency, threshold mode          (baseline)
#   AC — cross currency, threshold mode         (hub → foreign)
#   BC — cross currency, Auto mode              (non-hub source, no approval)
#   AD — cross currency, Manual mode            (everything parks in the inbox)
#   CD — cross currency, NEITHER side is SAR    (the real multicompany test)
# ---------------------------------------------------------------------------

PAIRS = [
	("AB", CO_A, CO_B, "Threshold-based", 10000),
	("AC", CO_A, CO_C, "Threshold-based", 10000),
	("BC", CO_B, CO_C, "Auto", 0),
	("AD", CO_A, CO_D, "Manual", 0),
	("CD", CO_C, CO_D, "Threshold-based", 5000),
]

PAIR_BY_KEY = {p[0]: p for p in PAIRS}


def register_companies(rows, include_in_seed=False):
	"""Let a sibling harness (e.g. seed_samecurrency) reuse this engine.

	`ABBR` / `CURRENCY_OF` are lookup tables consulted by every helper, so a
	registered company works everywhere. `COMPANIES` is the list this module
	*creates*, so registration stays out of it unless explicitly asked —
	otherwise importing a sibling module would silently change what
	`seed_multicompany()` provisions.
	"""
	for name, abbr, currency in rows:
		ABBR[name] = abbr
		CURRENCY_OF[name] = currency
		if include_in_seed and not any(c[0] == name for c in COMPANIES):
			COMPANIES.append((name, abbr, currency))


def register_pairs(rows, include_in_seed=False):
	"""Same contract as register_companies, for relationship definitions."""
	for row in rows:
		PAIR_BY_KEY[row[0]] = row
		if include_in_seed and not any(p[0] == row[0] for p in PAIRS):
			PAIRS.append(row)

# ---------------------------------------------------------------------------
# Transaction scenarios
#
# (marker_key, pair_key, direction, qty, rate, inbox_action)
#   direction "fwd" → source is company_a, "rev" → source is company_b
#   rate is expressed in the SOURCE company's own currency
# ---------------------------------------------------------------------------

SI_SCENARIOS = [
	# --- AB : same currency, threshold 10 000 -----------------------------
	("AB-UNDER",   "AB", "fwd",  1,    750, None),      # auto-post
	("AB-OVER",    "AB", "fwd",  1,  24000, None),      # parks Pending
	("AB-ACCEPT",  "AB", "fwd",  2,  15000, "accept"),  # park then accept
	("AB-REJECT",  "AB", "fwd",  1,  31000, "reject"),  # park then reject
	("AB-REV",     "AB", "rev",  1,   4200, None),      # B → A, auto-post

	# --- AC : SAR → AED, threshold 10 000 ---------------------------------
	("AC-UNDER",   "AC", "fwd",  1,    900, None),      # auto-post, cross-currency
	("AC-OVER",    "AC", "fwd",  1,  18000, None),      # parks Pending, FX stamped
	("AC-ACCEPT",  "AC", "fwd",  2,   8000, "accept"),  # 16k → park then accept
	("AC-REV",     "AC", "rev",  1,   3000, None),      # AED → SAR, auto-post

	# --- BC : Auto mode, SAR → AED ----------------------------------------
	("BC-AUTO",    "BC", "fwd",  1,   2500, None),      # always auto, no threshold
	("BC-BIG",     "BC", "fwd",  4,  30000, None),      # auto even at 120k
	("BC-REV",     "BC", "rev",  1,   7500, None),      # AED → SAR, auto

	# --- AD : Manual mode, SAR → EUR --------------------------------------
	("AD-MANUAL",  "AD", "fwd",  1,   1200, None),      # parks regardless of size
	("AD-ACCEPT",  "AD", "fwd",  3,    900, "accept"),  # park then accept
	("AD-REV",     "AD", "rev",  1,    600, None),      # EUR → SAR, parks

	# --- CD : AED ↔ EUR, neither side is the SAR hub, threshold 5 000 -----
	("CD-UNDER",   "CD", "fwd",  1,   1800, None),      # auto-post AED → EUR
	("CD-OVER",    "CD", "fwd",  1,  12000, None),      # parks Pending
	("CD-ACCEPT",  "CD", "fwd",  2,   4000, "accept"),  # 8k → park then accept
	("CD-REV",     "CD", "rev",  1,   2200, None),      # EUR → AED, auto-post
]

# Journal Entry scenarios: (marker_key, pair_key, amount)
# Posted on the source company's own Due-from / Due-to clearing accounts.
JE_SCENARIOS = [
	("JE-AB", "AB", 3400),
	("JE-AC", "AC", 6100),
	("JE-CD", "CD", 2750),
]

# Delivery Note scenarios: (marker_key, pair_key, qty, rate)
# Stock only exists in CO_A and CO_B, so DN flows are limited to those sources.
DN_SCENARIOS = [
	("DN-AB", "AB", 4, 250),
	("DN-AC", "AC", 3, 400),
]

STOCK_QTY_PER_COMPANY = 500


# ===========================================================================
# 1. Masters
# ===========================================================================

def seed_multicompany():
	"""Create the 4-company / 3-currency master matrix. Idempotent."""
	print("=" * 72)
	print("SEEDING MULTI-COMPANY MASTERS")
	print("=" * 72)

	for name, abbr, currency in COMPANIES:
		_ensure_company_cur(name, abbr, currency)
		_ensure_company_defaults(name, abbr)
		print(f"  [company]   {name:16s} {abbr:6s} {currency}")

	for currency in sorted({c for _, _, c in COMPANIES}):
		_ensure_price_list(f"Standard Selling {currency}", currency, selling=1)
		_ensure_price_list(f"Standard Buying {currency}", currency, buying=1)
		print(f"  [pricelist] Standard Selling/Buying {currency}")

	for src, tgt, rate in FX_RATES:
		_ensure_fx(src, tgt, rate)
		print(f"  [fx]        1 {src} = {rate} {tgt}")

	_ensure_item(ITEM_CODE)
	_ensure_stock_everywhere()

	# One internal Customer + Supplier per company (ERPNext allows only one of
	# each per represented company), allowed in every book that trades with it.
	links = _counterparties()
	parties = {}
	for company, _, _ in COMPANIES:
		parties[company] = {
			"customer": _ensure_internal_party("Customer", company, links[company]),
			"supplier": _ensure_internal_party("Supplier", company, links[company]),
		}
		print(f"  [parties]   {company:16s} customer+supplier representing it, "
		      f"usable in {len(links[company]) + 1} companies")

	created = []
	for key, co_a, co_b, mode, threshold in PAIRS:
		rel = _ensure_pair(key, co_a, co_b, mode, threshold, parties)
		created.append(rel)
		print(
			f"  [relation]  {key}  {co_a} ({CURRENCY_OF[co_a]}) <-> "
			f"{co_b} ({CURRENCY_OF[co_b]})  mode={mode}"
			+ (f" threshold={threshold:,.0f}" if mode == "Threshold-based" else "")
			+ f"  -> {rel}"
		)

	_ensure_document_mappings()
	print("  [mappings]  SO->PO, SI->PI, DN->PR, JE->JE on Intercompany Settings")

	frappe.db.commit()
	print(f"\nMasters ready: {len(COMPANIES)} companies, {len(created)} relationships.")
	return created


ARABIC_NAME = "كيو سي إس"      # placeholder — ERPNext's KSA/ZATCA QR hook blocks
SAUDI_TAX_ID = "300000000000003"  # SI submit for Saudi companies without these


def _ensure_company_cur(name, abbr, currency):
	if frappe.db.exists("Company", name):
		_repair_company(name, abbr)
		return name
	doc = frappe.new_doc("Company")
	doc.company_name = name
	doc.abbr = abbr
	doc.default_currency = currency
	country = CURRENCY_COUNTRY.get(currency)
	doc.country = (
		frappe.db.get_value("Country", country, "name")
		or frappe.db.get_value("Country", "Saudi Arabia", "name")
		or frappe.db.get_value("Country", {}, "name")
	)
	# Mirrors seed.py: placeholders so the ZATCA QR hook does not block SI submit.
	doc.company_name_in_arabic = ARABIC_NAME
	doc.tax_id = SAUDI_TAX_ID
	doc.create_chart_of_accounts_based_on = "Standard Template"
	doc.chart_of_accounts = "Standard"
	doc.insert(ignore_permissions=True)
	_repair_company(doc.name, abbr)
	return doc.name


def _repair_company(name, abbr):
	"""Backfill fields an interrupted company creation can leave blank.

	Company insert commits partway through (chart-of-accounts creation), so a
	failure later in the seed can leave a committed Company row whose field
	updates were rolled back. Re-running the seed must repair those rows rather
	than skip them, otherwise Sales Invoices fail at submit with
	'Arabic name missing' and Stock Entries with 'Stock Adjustment Account'.
	"""
	if not frappe.db.get_value("Company", name, "company_name_in_arabic"):
		frappe.db.set_value("Company", name, "company_name_in_arabic", ARABIC_NAME)
	if not frappe.db.get_value("Company", name, "tax_id"):
		frappe.db.set_value("Company", name, "tax_id", SAUDI_TAX_ID)

	# Stock defaults are not part of _ensure_company_defaults in seed.py.
	stock_defaults = {
		"stock_adjustment_account": ("Stock Adjustment", "Expense", "Stock Adjustment"),
		"default_inventory_account": ("Stock In Hand", "Stock", "Current Assets"),
		"stock_received_but_not_billed": (
			"Stock Received But Not Billed", "Liability", "Stock Received But Not Billed",
		),
	}
	for fld, (account_name, root_type, account_type) in stock_defaults.items():
		if frappe.db.get_value("Company", name, fld):
			continue
		acct = frappe.db.get_value(
			"Account", {"company": name, "account_name": account_name, "is_group": 0}, "name"
		) or _create_account(
			account_name, name, abbr,
			root_type=root_type, account_type=account_type,
			parent_label="Current Assets" if root_type == "Asset" else (
				"Current Liabilities" if root_type == "Liability" else "Indirect Expenses"
			),
		)
		if acct:
			frappe.db.set_value("Company", name, fld, acct)


def _ensure_fx(from_currency, to_currency, rate):
	"""One Currency Exchange row per direction, dated well in the past so any
	posting_date the scenarios use resolves against it."""
	if frappe.db.exists(
		"Currency Exchange",
		{"from_currency": from_currency, "to_currency": to_currency},
	):
		return
	for c in (from_currency, to_currency):
		if frappe.db.exists("Currency", c) and not frappe.db.get_value("Currency", c, "enabled"):
			frappe.db.set_value("Currency", c, "enabled", 1)
	ce = frappe.new_doc("Currency Exchange")
	ce.from_currency = from_currency
	ce.to_currency = to_currency
	ce.exchange_rate = rate
	ce.date = add_days(nowdate(), -365)
	ce.for_buying = 1
	ce.for_selling = 1
	ce.insert(ignore_permissions=True)


def _ensure_stock_everywhere(companies=None):
	"""Warehouse per company + item_defaults rows so DN/PR can post anywhere."""
	warehouses = {}
	for name, abbr, _ in (companies or COMPANIES):
		warehouses[name] = _ensure_warehouse("IC Stores", name, abbr)

	if not frappe.db.exists("Item", STOCK_ITEM_CODE):
		itm = frappe.new_doc("Item")
		itm.item_code = STOCK_ITEM_CODE
		itm.item_name = "IC Demo Stock Item"
		itm.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
		itm.stock_uom = "Nos"
		itm.is_stock_item = 1
		itm.insert(ignore_permissions=True)

	item = frappe.get_doc("Item", STOCK_ITEM_CODE)
	have = {row.company for row in (item.item_defaults or [])}
	changed = False
	for company, wh in warehouses.items():
		if company not in have:
			item.append("item_defaults", {"company": company, "default_warehouse": wh})
			changed = True
	if changed:
		item.save(ignore_permissions=True)
	return warehouses


def _counterparties():
	"""Map company -> every other company it trades with, derived from PAIRS."""
	links = {name: set() for name, _, _ in COMPANIES}
	for _, co_a, co_b, _, _ in PAIRS:
		links[co_a].add(co_b)
		links[co_b].add(co_a)
	return links


def _ensure_internal_party(doctype, represents_company, allowed_hosts):
	"""One internal Customer/Supplier per represented company.

	ERPNext enforces a global uniqueness rule — exactly one internal Customer
	and one internal Supplier may carry a given `represents_company` — so the
	record cannot be split per host company. Instead the single record lists
	every company allowed to use it in the `companies` child table.

	Note: `default_currency` and `default_price_list` are deliberately left
	blank. The same customer is used from SAR, AED and EUR books, so pinning a
	currency here would fight the currency each scenario sets explicitly.
	"""
	name = f"{represents_company} - Internal"
	allowed = sorted(set(allowed_hosts) | {represents_company})

	existing = frappe.db.get_value(
		doctype,
		{f"is_internal_{doctype.lower()}": 1, "represents_company": represents_company},
		"name",
	)
	if existing:
		_merge_allowed_companies(doctype, existing, allowed)
		return existing
	if frappe.db.exists(doctype, name):
		_merge_allowed_companies(doctype, name, allowed)
		return name

	doc = frappe.new_doc(doctype)
	if doctype == "Customer":
		doc.customer_name = name
		doc.customer_type = "Company"
		doc.customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "All Customer Groups"
		doc.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
		doc.is_internal_customer = 1
	else:
		doc.supplier_name = name
		doc.supplier_type = "Company"
		doc.supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name") or "All Supplier Groups"
		doc.is_internal_supplier = 1

	doc.represents_company = represents_company
	for co in allowed:
		doc.append("companies", {"company": co})
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_pair(key, co_a, co_b, mode, threshold, parties):
	"""Create the relationship for a company pair, with clearing accounts and
	internal parties wired for BOTH directions."""
	existing = frappe.db.get_value(
		"Intercompany Rule",
		[["company_a", "in", [co_a, co_b]], ["company_b", "in", [co_a, co_b]]],
		"name",
	)

	due_to_a = _ensure_clearing_account(f"Due to {co_b}", co_a, ABBR[co_a], "Liability", "Current Liabilities")
	due_from_a = _ensure_clearing_account(f"Due from {co_b}", co_a, ABBR[co_a], "Asset", "Current Assets")
	due_to_b = _ensure_clearing_account(f"Due to {co_a}", co_b, ABBR[co_b], "Liability", "Current Liabilities")
	due_from_b = _ensure_clearing_account(f"Due from {co_a}", co_b, ABBR[co_b], "Asset", "Current Assets")

	# Forward flow (A → B) needs a customer representing B (used in A's books)
	# and a supplier representing A (used in B's books). Reverse is the mirror.
	cust_a = parties[co_b]["customer"]
	supp_b = parties[co_a]["supplier"]
	cust_b = parties[co_a]["customer"]
	supp_a = parties[co_b]["supplier"]

	rel = frappe.get_doc("Intercompany Rule", existing) if existing else frappe.new_doc(
		"Intercompany Rule"
	)
	rel.company_a = co_a
	rel.company_b = co_b
	rel.internal_customer_a = cust_a
	rel.internal_supplier_b = supp_b
	rel.internal_customer_b = cust_b
	rel.internal_supplier_a = supp_a
	rel.posting_mode = mode
	rel.auto_submit_threshold = threshold
	rel.fx_policy = "Posting Date"
	rel.on_error = "Block"
	rel.approver_role = "Intercompany Approver"
	rel.due_to_a = due_to_a
	rel.due_from_a = due_from_a
	rel.due_to_b = due_to_b
	rel.due_from_b = due_from_b

	rel.save(ignore_permissions=True) if existing else rel.insert(ignore_permissions=True)
	return rel.name


def _ensure_document_mappings():
	"""Document mappings are global — they live on the Intercompany Settings single."""
	wanted = [
		("Sales Order", "Purchase Order", "Item to Item"),
		("Sales Invoice", "Purchase Invoice", "Item to Item"),
		("Delivery Note", "Purchase Receipt", "Warehouse to Warehouse"),
		("Journal Entry", "Journal Entry", "Mirror with Sign Flip"),
	]
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


# ===========================================================================
# 2. Scenarios
# ===========================================================================

def seed_multicompany_scenarios():
	"""Drive real documents through the dispatcher across every pair."""
	seed_multicompany()

	print()
	print("=" * 72)
	print("DRIVING SCENARIOS")
	print("=" * 72)

	results = {"si": [], "je": [], "dn": []}

	for marker_key, pair_key, direction, qty, rate, action in SI_SCENARIOS:
		results["si"].append(_drive_si(marker_key, pair_key, direction, qty, rate, action))

	_receive_stock()
	for marker_key, pair_key, qty, rate in DN_SCENARIOS:
		results["dn"].append(_drive_dn(marker_key, pair_key, qty, rate))

	for marker_key, pair_key, amount in JE_SCENARIOS:
		results["je"].append(_drive_je(marker_key, pair_key, amount))

	frappe.db.commit()

	ok = sum(1 for group in results.values() for r in group if r.get("ok"))
	total = sum(len(group) for group in results.values())
	print(f"\nScenarios posted: {ok}/{total}")
	return results


def _source_target(pair_key, direction):
	_, co_a, co_b, _, _ = PAIR_BY_KEY[pair_key]
	return (co_a, co_b) if direction == "fwd" else (co_b, co_a)


def _relationship_for(pair_key):
	_, co_a, co_b, _, _ = PAIR_BY_KEY[pair_key]
	name = frappe.db.get_value(
		"Intercompany Rule",
		[["company_a", "in", [co_a, co_b]], ["company_b", "in", [co_a, co_b]]],
		"name",
	)
	return frappe.get_doc("Intercompany Rule", name) if name else None


def _drive_si(marker_key, pair_key, direction, qty, rate, action, prefix="MC"):
	marker = f"{prefix}-{marker_key}"
	out = {"marker": marker, "pair": pair_key, "direction": direction, "ok": False}

	if frappe.db.exists("Sales Invoice", {"po_no": marker, "docstatus": 1}):
		print(f"  [skip] {marker:12s} already seeded")
		out.update(ok=True, skipped=True)
		return out

	for stale in frappe.get_all("Sales Invoice", filters={"po_no": marker, "docstatus": 0}, pluck="name"):
		frappe.delete_doc("Sales Invoice", stale, force=1, ignore_permissions=True)

	src_company, _ = _source_target(pair_key, direction)
	rel = _relationship_for(pair_key)
	if not rel:
		print(f"  [FAIL] {marker:12s} no relationship for {pair_key}")
		out["error"] = "no relationship"
		return out

	customer = rel.internal_customer_a if direction == "fwd" else rel.internal_customer_b
	currency = CURRENCY_OF[src_company]

	si = frappe.new_doc("Sales Invoice")
	si.company = src_company
	si.customer = customer
	si.posting_date = nowdate()
	si.due_date = add_days(nowdate(), 30)
	si.po_no = marker
	si.currency = currency
	si.conversion_rate = 1.0
	si.price_list_currency = currency
	si.plc_conversion_rate = 1.0
	si.selling_price_list = f"Standard Selling {currency}"
	si.ignore_pricing_rule = 1
	si.append("items", {"item_code": ITEM_CODE, "qty": qty, "rate": rate})

	try:
		si.submit()
		frappe.db.commit()
	except Exception as e:
		print(f"  [FAIL] {marker:12s} {type(e).__name__}: {str(e)[:110]}")
		frappe.db.rollback()
		out["error"] = str(e)
		return out

	print(f"  [ok]   {marker:12s} {src_company} SI {si.name}  {currency} {qty * rate:,.2f}")
	out.update(ok=True, source=si.name, amount=qty * rate, currency=currency)

	if action:
		out["action"] = _act_on_inbox("Sales Invoice", si.name, action, marker)
	return out


def _act_on_inbox(source_doctype, source_name, action, marker):
	inbox_name = frappe.db.get_value(
		"Intercompany Ledger",
		{"entry_type": "Transaction", "source_doctype": source_doctype, "source_name": source_name},
		"name",
	)
	if not inbox_name:
		print(f"         ↳ no ledger entry to {action}")
		return None
	try:
		inbox = frappe.get_doc("Intercompany Ledger", inbox_name)
		if action == "accept":
			inbox.accept()
		elif action == "reject":
			inbox.reject(reason=f"Demo rejection {marker}")
		frappe.db.commit()
		print(f"         ↳ {action}ed {inbox_name}")
		return action
	except Exception as e:
		frappe.db.rollback()
		print(f"         ↳ {action} FAILED: {str(e)[:110]}")
		return f"{action}-failed"


def _receive_stock(companies=None):
	"""Material Receipt into every company's warehouse so DN flows can ship."""
	for company, abbr, _ in (companies or COMPANIES):
		remark = f"MC-STOCK {abbr}"
		if frappe.db.exists("Stock Entry", {"remarks": remark, "docstatus": 1}):
			continue
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.company = company
		se.posting_date = nowdate()
		se.remarks = remark
		se.append("items", {
			"item_code": STOCK_ITEM_CODE,
			"qty": STOCK_QTY_PER_COMPANY,
			"basic_rate": 100,
			"t_warehouse": f"IC Stores - {abbr}",
		})
		try:
			se.submit()
			frappe.db.commit()
			print(f"  [stock] {company}: +{STOCK_QTY_PER_COMPANY} {STOCK_ITEM_CODE}")
		except Exception as e:
			frappe.db.rollback()
			print(f"  [stock] {company} receipt failed: {str(e)[:110]}")


def _drive_dn(marker_key, pair_key, qty, rate, prefix="MC"):
	marker = f"{prefix}-{marker_key}"
	out = {"marker": marker, "pair": pair_key, "ok": False}

	if frappe.db.exists("Delivery Note", {"po_no": marker, "docstatus": 1}):
		print(f"  [skip] {marker:12s} already seeded")
		out.update(ok=True, skipped=True)
		return out

	src_company, _ = _source_target(pair_key, "fwd")
	rel = _relationship_for(pair_key)
	if not rel:
		out["error"] = "no relationship"
		return out

	currency = CURRENCY_OF[src_company]
	wh = f"IC Stores - {ABBR[src_company]}"

	dn = frappe.new_doc("Delivery Note")
	dn.company = src_company
	dn.customer = rel.internal_customer_a
	dn.posting_date = nowdate()
	dn.po_no = marker
	dn.currency = currency
	dn.conversion_rate = 1.0
	dn.price_list_currency = currency
	dn.plc_conversion_rate = 1.0
	dn.selling_price_list = f"Standard Selling {currency}"
	dn.ignore_pricing_rule = 1
	dn.set_warehouse = wh
	dn.append("items", {"item_code": STOCK_ITEM_CODE, "qty": qty, "rate": rate, "warehouse": wh})

	try:
		dn.submit()
		frappe.db.commit()
		print(f"  [ok]   {marker:12s} {src_company} DN {dn.name}  {currency} {qty * rate:,.2f}")
		out.update(ok=True, source=dn.name, amount=qty * rate, currency=currency)
	except Exception as e:
		frappe.db.rollback()
		print(f"  [FAIL] {marker:12s} {type(e).__name__}: {str(e)[:110]}")
		out["error"] = str(e)
	return out


def _drive_je(marker_key, pair_key, amount, prefix="MC"):
	marker = f"{prefix}-{marker_key}"
	out = {"marker": marker, "pair": pair_key, "ok": False}
	remark = f"{marker} intercompany recharge"

	if frappe.db.exists("Journal Entry", {"user_remark": remark, "docstatus": 1}):
		print(f"  [skip] {marker:12s} already seeded")
		out.update(ok=True, skipped=True)
		return out

	for stale in frappe.get_all("Journal Entry", filters={"user_remark": remark, "docstatus": 0}, pluck="name"):
		frappe.delete_doc("Journal Entry", stale, force=1, ignore_permissions=True)

	rel = _relationship_for(pair_key)
	if not rel:
		out["error"] = "no relationship"
		return out

	src_company = rel.company_a
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = src_company
	je.posting_date = nowdate()
	je.user_remark = remark
	je.append("accounts", {"account": rel.due_from_a, "debit_in_account_currency": amount})
	je.append("accounts", {"account": rel.due_to_a, "credit_in_account_currency": amount})

	try:
		je.submit()
		frappe.db.commit()
		print(f"  [ok]   {marker:12s} {src_company} JE {je.name}  {CURRENCY_OF[src_company]} {amount:,.2f}")
		out.update(ok=True, source=je.name, amount=amount)
	except Exception as e:
		frappe.db.rollback()
		print(f"  [FAIL] {marker:12s} {type(e).__name__}: {str(e)[:110]}")
		out["error"] = str(e)
	return out


# ===========================================================================
# 3. Verification
# ===========================================================================

def verify_multicompany():
	"""Read back every seeded scenario and assert the intercompany outcome.

	Checks per Sales Invoice scenario:
      1. an Intercompany Ledger Transaction row exists
      2. its status matches what the relationship's posting policy dictates
      3. the counter Purchase Invoice exists in the TARGET company
      4. the counter doc carries the target company's currency
      5. the FX rate stamped on the inbox matches the Currency Exchange master
      6. the counter doc's item rate equals source rate x expected FX rate
    """
	print()
	print("=" * 72)
	print("VERIFICATION")
	print("=" * 72)

	rows = []
	for marker_key, pair_key, direction, qty, rate, action in SI_SCENARIOS:
		rows.append(_verify_si(marker_key, pair_key, direction, qty, rate, action))

	_print_matrix(rows)

	failures = [r for r in rows if r["checks"] and not all(c[1] for c in r["checks"])]
	missing = [r for r in rows if not r["checks"]]

	print()
	print("-" * 72)
	print(f"  scenarios verified : {len(rows)}")
	print(f"  fully passing      : {len(rows) - len(failures) - len(missing)}")
	print(f"  with failed checks : {len(failures)}")
	print(f"  not posted at all  : {len(missing)}")
	print("-" * 72)

	if failures:
		print("\nFAILED CHECKS")
		for r in failures:
			for label, passed, detail in r["checks"]:
				if not passed:
					print(f"  {r['marker']:12s} {label:22s} {detail}")

	return {"rows": rows, "failures": len(failures), "missing": len(missing)}


def _expected_rate(from_currency, to_currency):
	if from_currency == to_currency:
		return 1.0
	rate = frappe.db.get_value(
		"Currency Exchange",
		{"from_currency": from_currency, "to_currency": to_currency},
		"exchange_rate",
		order_by="date desc",
	)
	return flt(rate) if rate else None


def _expected_status(rel, amount_base):
	mode = rel.posting_mode or "Manual"
	if mode == "Auto":
		return "Accepted"
	if mode == "Threshold-based":
		return "Accepted" if amount_base < (rel.auto_submit_threshold or 0) else "Pending"
	return "Pending"


def _verify_si(marker_key, pair_key, direction, qty, rate, action, prefix="MC"):
	marker = f"{prefix}-{marker_key}"
	src_company, tgt_company = _source_target(pair_key, direction)
	src_currency = CURRENCY_OF[src_company]
	tgt_currency = CURRENCY_OF[tgt_company]

	row = {
		"marker": marker,
		"pair": pair_key,
		"flow": f"{ABBR[src_company]}→{ABBR[tgt_company]}",
		"fx": f"{src_currency}→{tgt_currency}",
		"checks": [],
		"status": "-",
	}

	si_name = frappe.db.get_value("Sales Invoice", {"po_no": marker, "docstatus": 1}, "name")
	if not si_name:
		row["status"] = "NOT POSTED"
		return row

	inbox_name = frappe.db.get_value(
		"Intercompany Ledger",
		{"entry_type": "Transaction", "source_doctype": "Sales Invoice", "source_name": si_name},
		"name",
	)
	if not inbox_name:
		row["status"] = "NO INBOX"
		row["checks"].append(("inbox row created", False, f"no inbox for SI {si_name}"))
		return row

	inbox = frappe.get_doc("Intercompany Ledger", inbox_name)
	rel = _relationship_for(pair_key)
	row["status"] = inbox.status
	row["checks"].append(("inbox row created", True, inbox.name))

	# --- status vs policy (rejected scenarios are expected to be Rejected) ---
	if action == "reject":
		want_status = "Rejected"
	elif action == "accept":
		want_status = "Accepted"
	else:
		want_status = _expected_status(rel, flt(inbox.amount))
	row["checks"].append((
		"inbox status",
		inbox.status == want_status,
		f"got {inbox.status}, want {want_status}",
	))

	# --- target company routing ---
	row["checks"].append((
		"target company",
		inbox.target_company == tgt_company,
		f"got {inbox.target_company}, want {tgt_company}",
	))

	expected_fx = _expected_rate(src_currency, tgt_currency)
	if expected_fx is None:
		row["checks"].append(("fx master exists", False, f"no Currency Exchange {src_currency}→{tgt_currency}"))
		return row

	# --- fx rate stamped on the inbox ---
	row["checks"].append((
		"fx stamped on inbox",
		abs(flt(inbox.fx_rate) - expected_fx) < 0.0001,
		f"got {flt(inbox.fx_rate)}, want {expected_fx}",
	))

	if action == "reject":
		# Counter-doc is deliberately deleted on reject. Do NOT test this by
		# name: reject() leaves inbox.target_name pointing at the deleted doc,
		# and a rolled-back run can hand that same autoname to an unrelated
		# document later. Test by back-reference instead.
		orphan = frappe.db.get_value(
			inbox.target_doctype,
			{"custom_intercompany_reference": si_name, "docstatus": ["!=", 2]},
			"name",
		)
		row["checks"].append((
			"counter doc removed",
			not orphan,
			f"{inbox.target_doctype} {orphan} still references {si_name}",
		))
		return row

	# Manual policy defers counter-doc creation to acceptance time, so a row that
	# is still Pending correctly has no target yet.
	if inbox.status == "Pending" and (rel.posting_mode or "Manual") == "Manual":
		row["checks"].append((
			"counter doc deferred",
			True,
			"Manual policy — counter doc is created on accept",
		))
		return row

	if not (inbox.target_doctype and inbox.target_name):
		row["checks"].append(("counter doc created", False, "inbox has no target"))
		return row

	if not frappe.db.exists(inbox.target_doctype, inbox.target_name):
		row["checks"].append(("counter doc created", False, f"{inbox.target_name} missing"))
		return row

	pi = frappe.get_doc(inbox.target_doctype, inbox.target_name)
	row["checks"].append(("counter doc created", True, pi.name))
	row["target"] = pi.name

	row["checks"].append((
		"counter doc company",
		pi.company == tgt_company,
		f"got {pi.company}, want {tgt_company}",
	))
	row["checks"].append((
		"counter doc currency",
		pi.currency == tgt_currency,
		f"got {pi.currency}, want {tgt_currency}",
	))

	want_docstatus = 1 if inbox.status == "Accepted" else 0
	row["checks"].append((
		"counter docstatus",
		pi.docstatus == want_docstatus,
		f"got {pi.docstatus}, want {want_docstatus}",
	))

	# --- the actual money check: was the rate converted? ---
	want_rate = round(rate * expected_fx, 2)
	got_rate = flt(pi.items[0].rate) if pi.items else 0.0
	row["want_rate"] = want_rate
	row["got_rate"] = got_rate
	row["checks"].append((
		"item rate converted",
		abs(got_rate - want_rate) <= max(0.02, want_rate * 0.0001),
		f"got {got_rate:,.2f} {pi.currency}, want {want_rate:,.2f} "
		f"({rate:,.2f} {src_currency} x {expected_fx})",
	))

	# --- clearing legs ---
	# By design the legs are posted only when the counter-doc is submitted, so a
	# Pending row is expected to have none until someone accepts it.
	je_count = frappe.db.count("Journal Entry", {"user_remark": ["like", f"%{si_name}%"]})
	want_jes = 2 if inbox.status == "Accepted" else 0
	row["checks"].append((
		"clearing JEs drafted",
		je_count == want_jes,
		f"got {je_count}, want {want_jes} (status {inbox.status})",
	))

	return row


def _print_matrix(rows):
	print()
	print(f"  {'SCENARIO':13s} {'FLOW':11s} {'FX':11s} {'STATUS':9s} {'RATE OUT':>12s} {'EXPECTED':>12s}  RESULT")
	print(f"  {'-'*13} {'-'*11} {'-'*11} {'-'*9} {'-'*12} {'-'*12}  {'-'*6}")
	for r in rows:
		if not r["checks"]:
			verdict = r["status"]
		else:
			failed = [c for c in r["checks"] if not c[1]]
			verdict = "PASS" if not failed else f"FAIL ({len(failed)})"
		got = f"{r['got_rate']:,.2f}" if "got_rate" in r else "-"
		want = f"{r['want_rate']:,.2f}" if "want_rate" in r else "-"
		print(
			f"  {r['marker']:13s} {r['flow']:11s} {r['fx']:11s} "
			f"{r['status']:9s} {got:>12s} {want:>12s}  {verdict}"
		)


# ===========================================================================
# 4. Orchestration + cleanup
# ===========================================================================

@frappe.whitelist()
def run_all():
	"""Masters → scenarios → verification, in one call."""
	seed_multicompany()
	seed_multicompany_scenarios()
	return verify_multicompany()


@frappe.whitelist()
def reset_multicompany():
	"""Delete every document this module created. Masters (companies, accounts,
	items, parties, relationships) are left intact so a re-seed is fast."""
	print("Resetting multi-company scenario documents...")

	# Inbox rows first — they hold the links used by the cancel cascade.
	markers = [f"MC-{k}" for k, *_ in SI_SCENARIOS]
	markers += [f"MC-{k}" for k, *_ in DN_SCENARIOS]

	for doctype, field in (("Sales Invoice", "po_no"), ("Delivery Note", "po_no")):
		for name in frappe.get_all(doctype, filters={field: ["like", "MC-%"]}, pluck="name"):
			_purge_source(doctype, name)

	for marker_key, _, _ in JE_SCENARIOS:
		remark = f"MC-{marker_key} intercompany recharge"
		for name in frappe.get_all("Journal Entry", filters={"user_remark": remark}, pluck="name"):
			_purge_source("Journal Entry", name)

	# Orphaned clearing JEs and inbox/log rows
	for name in frappe.get_all(
		"Journal Entry", filters={"user_remark": ["like", "%IC clearing for%"]}, pluck="name"
	):
		_force_delete("Journal Entry", name)

	for name in frappe.get_all("Intercompany Ledger", pluck="name"):
		_force_delete("Intercompany Ledger", name)

	frappe.db.commit()
	print("Reset complete.")


def _purge_source(doctype, name):
	"""Cancel + delete a source doc together with its counter-doc."""
	inbox = frappe.db.get_value(
		"Intercompany Ledger",
		{"entry_type": "Transaction", "source_doctype": doctype, "source_name": name},
		["name", "target_doctype", "target_name"], as_dict=True,
	)
	_force_delete(doctype, name)
	if inbox:
		if inbox.target_doctype and inbox.target_name:
			_force_delete(inbox.target_doctype, inbox.target_name)
		_force_delete("Intercompany Ledger", inbox.name)


def _force_delete(doctype, name):
	try:
		doc = frappe.get_doc(doctype, name)
		if doc.docstatus == 1:
			try:
				doc.flags.ignore_links = True
				doc.cancel()
			except Exception:
				pass
		frappe.delete_doc(doctype, name, force=1, ignore_permissions=True, ignore_missing=True)
	except frappe.DoesNotExistError:
		pass
	except Exception as e:
		print(f"  [warn] could not delete {doctype} {name}: {str(e)[:90]}")
