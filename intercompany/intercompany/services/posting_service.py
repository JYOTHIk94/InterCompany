import frappe
from frappe.utils import nowdate

from intercompany.intercompany.services.fx_service import get_rate
from intercompany.intercompany.services.inbox_service import create_inbox
from intercompany.intercompany.services.log_service import log_ic


def process_ic_event(doc, method=None):
	"""Dispatcher called from on_submit hook of Sales Invoice / Delivery Note / Journal Entry."""
	# Re-entrancy guard: skip docs that were themselves created by the dispatcher.
	if _is_dispatcher_generated(doc):
		return

	rel = _find_relationship(doc)
	if not rel:
		return

	try:
		mapping = _resolve_mapping(rel, doc.doctype)
		if not mapping:
			log_ic(doc, f"No mapping for {doc.doctype}", "Failed", "Mapping missing")
			return

		fx_rate = _resolve_fx(doc, rel)
		amount_base = _amount_in_base(doc)
		mode = rel.posting_mode or "Manual"

		if mode == "Auto":
			target = _create_target(doc, rel, mapping, submit=True, fx_rate=fx_rate)
			_post_gl_legs(doc, rel, target, amount_base, fx_rate=fx_rate)
			create_inbox(
				doc, rel,
				status="Accepted",
				target_doctype=mapping.target_doctype,
				target_name=target.name,
				policy_reason="Auto",
				fx_rate=fx_rate,
			)
			log_ic(doc, f"Auto-posted {mapping.target_doctype}", "Success", target.name)

		elif mode == "Threshold-based":
			threshold = rel.auto_submit_threshold or 0
			if amount_base < threshold:
				target = _create_target(doc, rel, mapping, submit=True)
				_post_gl_legs(doc, rel, target, amount_base)
				create_inbox(
					doc, rel,
					status="Accepted",
					target_doctype=mapping.target_doctype,
					target_name=target.name,
					policy_reason=f"Auto (under {threshold})",
					fx_rate=fx_rate,
				)
				log_ic(doc, "Auto-posted under threshold", "Success", target.name)
			else:
				target = _create_target(doc, rel, mapping, submit=False, fx_rate=fx_rate)
				create_inbox(
					doc, rel,
					status="Pending",
					target_doctype=mapping.target_doctype,
					target_name=target.name,
					policy_reason=f"Threshold > {threshold}",
					fx_rate=fx_rate,
				)
				log_ic(doc, "Parked in inbox", "Queued", target.name)

		elif mode == "Manual":
			target = _create_target(doc, rel, mapping, submit=False, fx_rate=fx_rate)
			create_inbox(
				doc, rel,
				status="Pending",
				target_doctype=mapping.target_doctype,
				target_name=target.name,
				policy_reason="Manual review",
				fx_rate=fx_rate,
			)
			log_ic(doc, "Manual review required", "Queued", target.name)

	except Exception as e:
		log_ic(doc, "IC posting error", "Failed", str(e))
		frappe.log_error(frappe.get_traceback(), "Intercompany Error")
		if (rel.on_error or "Block") == "Block":
			raise


def cascade_cancel(doc, method=None):
	"""on_cancel hook — cancel the linked counter-doc."""
	inbox_name = frappe.db.get_value(
		"Intercompany Inbox",
		{"source_doctype": doc.doctype, "source_name": doc.name},
		"name",
	)
	if not inbox_name:
		return

	inbox = frappe.get_doc("Intercompany Inbox", inbox_name)
	if inbox.target_doctype and inbox.target_name:
		try:
			tgt = frappe.get_doc(inbox.target_doctype, inbox.target_name)
			if tgt.docstatus == 1:
				tgt.cancel()
			elif tgt.docstatus == 0:
				frappe.delete_doc(inbox.target_doctype, inbox.target_name, force=1)
		except frappe.DoesNotExistError:
			pass

	inbox.db_set("status", "Rejected")
	log_ic(doc, "Cancelled — counter-doc reversed", "Success", inbox.target_name or "")


# ---------- helpers ----------

def _find_relationship(doc):
	"""Detect IC relationship from source-side fields. Returns Document or None."""
	if doc.doctype == "Sales Invoice":
		customer = getattr(doc, "customer", None)
		if not customer:
			return None
		represents = frappe.db.get_value("Customer", customer, "represents_company")
		if not represents:
			return None
		name = frappe.db.get_value(
			"Intercompany Relationship",
			[
				["company_a", "in", [doc.company, represents]],
				["company_b", "in", [doc.company, represents]],
			],
			"name",
		)
		return frappe.get_doc("Intercompany Relationship", name) if name else None

	if doc.doctype == "Delivery Note":
		customer = getattr(doc, "customer", None)
		represents = frappe.db.get_value("Customer", customer, "represents_company") if customer else None
		if not represents:
			return None
		name = frappe.db.get_value(
			"Intercompany Relationship",
			[
				["company_a", "in", [doc.company, represents]],
				["company_b", "in", [doc.company, represents]],
			],
			"name",
		)
		return frappe.get_doc("Intercompany Relationship", name) if name else None

	if doc.doctype == "Journal Entry":
		# JE is detected by an account belonging to the IC due-to/due-from list
		accounts = [r.account for r in doc.accounts]
		name = frappe.db.sql(
			"""select name from `tabIntercompany Relationship`
			   where due_to_a in %(a)s or due_from_a in %(a)s
			      or due_to_b in %(a)s or due_from_b in %(a)s
			   limit 1""",
			{"a": tuple(accounts) or ("",)},
		)
		return frappe.get_doc("Intercompany Relationship", name[0][0]) if name else None

	return None


def _resolve_mapping(rel, source_doctype):
	for row in rel.document_mapping or []:
		if row.source_doctype == source_doctype and (row.status or "Active") == "Active":
			return row
	return None


def _resolve_fx(doc, rel):
	src_currency = getattr(doc, "currency", None)
	tgt_company_currency = frappe.db.get_value(
		"Company",
		rel.company_b if doc.company == rel.company_a else rel.company_a,
		"default_currency",
	)
	posting_date = getattr(doc, "posting_date", None) or nowdate()
	return get_rate(src_currency, tgt_company_currency, posting_date, rel.fx_policy or "Posting Date")


def _amount_in_base(doc):
	if doc.doctype == "Journal Entry":
		return getattr(doc, "total_debit", 0) or 0
	return getattr(doc, "base_grand_total", None) or getattr(doc, "grand_total", None) or 0


def _create_target(doc, rel, mapping, submit=False, fx_rate=1.0):
	if frappe.db.exists(
		"Intercompany Inbox",
		{"source_doctype": doc.doctype, "source_name": doc.name},
	):
		existing_target = frappe.db.get_value(
			"Intercompany Inbox",
			{"source_doctype": doc.doctype, "source_name": doc.name},
			"target_name",
		)
		if existing_target:
			return frappe.get_doc(mapping.target_doctype, existing_target)

	is_a_to_b = doc.company == rel.company_a
	target_company = rel.company_b if is_a_to_b else rel.company_a
	internal_supplier = rel.internal_supplier_b if is_a_to_b else rel.internal_supplier_a

	target_currency = frappe.db.get_value("Company", target_company, "default_currency")
	source_currency = getattr(doc, "currency", None)

	# Multicurrency strategy: counter-doc lives in the TARGET company's currency.
	# Item rates and amounts are converted from source→target currency using fx_rate.
	# This avoids party-account currency mismatches in ERPNext.
	cross_currency = bool(source_currency and source_currency != target_currency)
	rate_multiplier = float(fx_rate) if (cross_currency and fx_rate) else 1.0
	target_expense_account = (
		frappe.db.get_value("Company", target_company, "default_expense_account")
		or frappe.db.get_value(
			"Account",
			{"company": target_company, "account_type": "Expense Account", "is_group": 0},
			"name",
		)
	)

	if mapping.target_doctype == "Purchase Invoice":
		tgt = frappe.new_doc("Purchase Invoice")
		tgt.company = target_company
		tgt.supplier = internal_supplier
		tgt.custom_intercompany_reference = doc.name
		tgt.bill_no = doc.name
		tgt.currency = target_currency
		tgt.conversion_rate = 1.0
		# credit_to omitted — let ERPNext use the supplier's default_payable_account.
		# IC clearing on Due-to/Due-from is recorded separately by _post_gl_legs.
		_apply_buying_price_list(tgt, target_currency)
		if cross_currency:
			# Without this, ERPNext re-fetches item rate from the price list at submit
			# time, undoing our SAR→AED conversion.
			tgt.ignore_pricing_rule = 1
			tgt.buying_price_list = ""
		tgt.posting_date = getattr(doc, "posting_date", None)
		tgt.bill_date = getattr(doc, "posting_date", None)
		tgt.due_date = getattr(doc, "posting_date", None)
		for item in doc.items:
			rate = item.rate * rate_multiplier
			if mapping.pricing_rule == "Cost Plus Markup" and mapping.markup_pct:
				rate = rate * (1 + (mapping.markup_pct / 100.0))
			amount = rate * item.qty
			tgt.append("items", {
				"item_code": item.item_code,
				"qty": item.qty,
				"rate": rate,
				# Fully populate price_list_rate / amounts so ERPNext's set_missing_item_details
				# treats this row as complete and doesn't re-fetch from price list / item defaults.
				"price_list_rate": rate,
				"base_rate": rate,
				"base_price_list_rate": rate,
				"amount": amount,
				"base_amount": amount,
				"net_rate": rate,
				"base_net_rate": rate,
				"net_amount": amount,
				"base_net_amount": amount,
				"conversion_factor": 1,
				"expense_account": target_expense_account,
			})
	elif mapping.target_doctype == "Purchase Receipt":
		tgt = frappe.new_doc("Purchase Receipt")
		tgt.company = target_company
		tgt.supplier = internal_supplier
		tgt.custom_intercompany_reference = doc.name
		tgt.currency = target_currency
		tgt.conversion_rate = 1.0
		_apply_buying_price_list(tgt, target_currency)
		if cross_currency:
			tgt.ignore_pricing_rule = 1
			tgt.buying_price_list = ""
		tgt.posting_date = getattr(doc, "posting_date", None)
		target_warehouse = _default_warehouse(target_company)
		for item in doc.items:
			rate = item.rate * rate_multiplier
			amount = rate * item.qty
			tgt.append("items", {
				"item_code": item.item_code,
				"qty": item.qty,
				"rate": rate,
				"price_list_rate": rate,
				"base_rate": rate,
				"base_price_list_rate": rate,
				"amount": amount,
				"base_amount": amount,
				"net_rate": rate,
				"base_net_rate": rate,
				"net_amount": amount,
				"base_net_amount": amount,
				"conversion_factor": 1,
				"warehouse": target_warehouse,
			})
	elif mapping.target_doctype == "Journal Entry":
		tgt = frappe.new_doc("Journal Entry")
		tgt.company = target_company
		tgt.posting_date = doc.posting_date
		tgt.user_remark = f"Mirror of {doc.doctype} {doc.name}"
		account_map = _ic_account_map(rel, is_a_to_b)
		for r in doc.accounts:
			translated = account_map.get(r.account)
			if not translated:
				# Non-IC accounts belong to the source company only — they don't mirror.
				continue
			tgt.append("accounts", {
				"account": translated,
				"debit_in_account_currency": r.credit_in_account_currency,
				"credit_in_account_currency": r.debit_in_account_currency,
			})
		# If only one IC line was found, self-balance it (memo entry).
		if len(tgt.accounts) == 1:
			only = tgt.accounts[0]
			tgt.append("accounts", {
				"account": only.account,
				"debit_in_account_currency": only.credit_in_account_currency,
				"credit_in_account_currency": only.debit_in_account_currency,
			})
		if not tgt.accounts:
			frappe.throw(
				f"Source JE {doc.name} has no IC clearing accounts — nothing to mirror"
			)
	else:
		frappe.throw(f"Unsupported target doctype {mapping.target_doctype}")

	# Suppress ERPNext's price-list re-fetch during insert/submit on a cross-currency
	# counter-doc (otherwise it overwrites our converted item rates).
	if cross_currency and mapping.target_doctype in ("Purchase Invoice", "Purchase Receipt"):
		tgt.flags.ignore_pricing_rule = True
		tgt.flags.ignore_account_permission = True

	import sys
	print(f"DBG _create_target: cross_currency={cross_currency} fx_rate={fx_rate} rate_multiplier={rate_multiplier}", file=sys.stderr)
	if tgt.get("items"):
		print(f"DBG before insert: items[0].rate={tgt.items[0].rate}", file=sys.stderr)
	tgt.insert(ignore_permissions=True)
	if tgt.get("items"):
		print(f"DBG after insert: items[0].rate={tgt.items[0].rate}", file=sys.stderr)
	if submit:
		# Reload from DB so the in-memory copy can't trigger set_missing_values again.
		tgt = frappe.get_doc(tgt.doctype, tgt.name)
		if cross_currency and mapping.target_doctype in ("Purchase Invoice", "Purchase Receipt"):
			tgt.flags.ignore_pricing_rule = True
		print(f"DBG before submit: items[0].rate={tgt.items[0].rate}", file=sys.stderr)
		tgt.submit()
		tgt.reload()
		print(f"DBG after submit: items[0].rate={tgt.items[0].rate}", file=sys.stderr)
	return tgt


def _is_dispatcher_generated(doc):
	"""Detect docs created by `_create_target` so the on_submit hook doesn't re-fire."""
	if doc.doctype == "Journal Entry":
		remark = (doc.user_remark or "")
		if remark.startswith("Mirror of ") or "IC clearing for" in remark:
			return True
	if doc.doctype in ("Purchase Invoice", "Purchase Receipt"):
		# These are target-side docs; the dispatcher hooks SI/DN/JE only, so no recursion risk.
		# Custom field marks them as IC-generated.
		if getattr(doc, "custom_intercompany_reference", None):
			return True
	return False


def _ic_account_map(rel, is_a_to_b):
	"""Translation table for mirroring an IC Journal Entry between companies.
	Each IC clearing account in the source company maps to its counterpart in the target."""
	if is_a_to_b:
		return {
			rel.due_to_a: rel.due_from_b,
			rel.due_from_a: rel.due_to_b,
		}
	return {
		rel.due_to_b: rel.due_from_a,
		rel.due_from_b: rel.due_to_a,
	}


def _default_warehouse(company):
	wh = frappe.db.get_value("Company", company, "default_inventory_account")
	# The Company has no direct default-warehouse field on v14; pick the first non-group warehouse.
	return frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")


def _apply_buying_price_list(tgt, target_currency):
	"""Set buying_price_list to a list whose currency matches the company,
	avoiding ERPNext's AED↔SAR validation when the site default mismatches."""
	pl = frappe.db.get_value(
		"Price List",
		{"buying": 1, "currency": target_currency, "enabled": 1},
		"name",
	)
	if pl:
		tgt.buying_price_list = pl
		tgt.price_list_currency = target_currency
		tgt.plc_conversion_rate = 1.0
	else:
		tgt.buying_price_list = ""
		tgt.ignore_pricing_rule = 1


def _post_gl_legs(doc, rel, target, amount_base, fx_rate=1.0):
	"""Post intercompany clearing entries.

	v14-safe: post one same-company JE per side rather than a single
	cross-company "Inter Company Journal Entry" (which v14 requires to be
	bidirectionally linked via inter_company_journal_entry_reference).

	Source side (A): Dr Due-from B / Cr <bank/clearing>  -- balanced by source doc's own GL
	Target side (B): Dr Expense / Cr Due-to A             -- balanced by counter-doc's GL

	The Sales Invoice / Purchase Invoice flows already create the income/expense legs
	via standard ERPNext posting. The JEs below only record the *clearing* movement
	on the Due-to/Due-from accounts so the unmatched-balance report has data to read.
	"""
	if not amount_base:
		return
	is_a_to_b = doc.company == rel.company_a
	src_company = rel.company_a if is_a_to_b else rel.company_b
	tgt_company = rel.company_b if is_a_to_b else rel.company_a
	src_due_from = rel.due_from_a if is_a_to_b else rel.due_from_b
	tgt_due_to = rel.due_to_b if is_a_to_b else rel.due_to_a

	posting_date = getattr(doc, "posting_date", None) or nowdate()
	remark = f"IC clearing for {doc.doctype} {doc.name}"

	# Multicurrency: the source amount is in the source company's base currency;
	# the target memo JE must use the target company's base currency.
	src_amount = amount_base
	tgt_amount = round(amount_base * (float(fx_rate) if fx_rate else 1.0), 2)

	# Source-side memo JE: balance Due-from with the customer receivable cleared by SI
	je_src = frappe.new_doc("Journal Entry")
	je_src.voucher_type = "Journal Entry"
	je_src.company = src_company
	je_src.posting_date = posting_date
	je_src.user_remark = f"{remark} (source)"
	je_src.append("accounts", {
		"account": src_due_from,
		"debit_in_account_currency": src_amount,
	})
	je_src.append("accounts", {
		"account": src_due_from,
		"credit_in_account_currency": src_amount,
	})
	je_src.insert(ignore_permissions=True)

	# Target-side memo JE
	je_tgt = frappe.new_doc("Journal Entry")
	je_tgt.voucher_type = "Journal Entry"
	je_tgt.company = tgt_company
	je_tgt.posting_date = posting_date
	je_tgt.user_remark = f"{remark} (target)"
	je_tgt.append("accounts", {
		"account": tgt_due_to,
		"debit_in_account_currency": tgt_amount,
	})
	je_tgt.append("accounts", {
		"account": tgt_due_to,
		"credit_in_account_currency": tgt_amount,
	})
	je_tgt.insert(ignore_permissions=True)
	# Drafts only — sites with their own approval flow can submit them manually.
