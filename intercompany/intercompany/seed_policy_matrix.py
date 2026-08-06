"""Policy-matrix seed: every posting mode × every source doctype.

`seed_samecurrency.py` proves the *amounts* are right with FX removed. This
module proves the *policy branch* is right: it drives the same three SAR
relationships (one per posting mode) through all three document mappings
(SI→PI, DN→PR, JE→JE) and asserts what the dispatcher did with each — inbox
status, whether the inbox row itself was submitted, and whether a counter-doc
exists and in what docstatus.

It reuses the same-currency companies and pairs on purpose: `Intercompany
Relationship` permits only one record per company pair, so the three SC pairs
are the only place all three modes coexist. Markers are prefixed `PM-` so these
documents never collide with the `SC-` or `MC-` suites.

Coverage — 10 policy cases × 3 source doctypes = 30 scenarios:

    Auto        below and far above any threshold      → both post straight through
    Threshold   under / exactly AT / over              → the boundary case is explicit
    Threshold   over + accept, over + reject           → approval round-trip
    Manual      park / park+accept / park+reject       → deferred counter-doc creation

Run from bench:

    bench --site <site> execute intercompany.intercompany.seed_policy_matrix.seed_policy_matrix
    bench --site <site> execute intercompany.intercompany.seed_policy_matrix.verify_policy_matrix

    # or both in order:
    bench --site <site> execute intercompany.intercompany.seed_policy_matrix.run_all

    # wipe only the documents this module created (masters are kept):
    bench --site <site> execute intercompany.intercompany.seed_policy_matrix.reset_policy_matrix

Idempotent: every scenario is keyed on its marker, so re-runs skip what exists.
"""

import frappe
from frappe.utils import add_days, flt, nowdate

from intercompany.intercompany.seed import ITEM_CODE
from intercompany.intercompany.seed_multicompany import (
	_act_on_inbox,
	_drive_dn,
	_drive_je,
	_drive_si,
	_force_delete,
	_purge_source,
	_receive_stock,
	_relationship_for,
)
from intercompany.intercompany.seed_samecurrency import COMPANIES, seed_samecurrency

PREFIX = "PM"

# Sentinel amount: resolved at run time to the pair's own auto_submit_threshold.
# The engine reads the threshold from the Intercompany Rule, so the test
# must too — hardcoding a number here would silently stop testing the boundary
# the moment someone retunes the relationship.
AT_THRESHOLD = "AT_THRESHOLD"

# ---------------------------------------------------------------------------
# The policy grid.
#
# (case, pair, amount, action, want_status, want_inbox_submitted, want_target)
#
#   want_target : "submitted" — counter-doc exists at docstatus 1
#                 "draft"     — counter-doc exists at docstatus 0, awaiting accept
#                 "none"      — no live counter-doc (never created, or removed)
#
# want_inbox_submitted encodes CURRENT behaviour, which is deliberately not
# uniform — see THR-UNDER below.
# ---------------------------------------------------------------------------
CASES = [
	# --- Auto: mode wins regardless of size --------------------------------
	("AUTO-SMALL",  "RS",  1200, None,     "Accepted", True,  "submitted"),
	("AUTO-LARGE",  "RS", 60000, None,     "Accepted", True,  "submitted"),

	# --- Threshold-based: auto-post at or under, hard-reject above ---------
	# At or under the threshold the row is Accepted AND submitted, and the
	# counter-doc is created and submitted.
	("THR-UNDER",   "RT",  3000, None,     "Accepted", True,  "submitted"),
	# The boundary is inclusive (`amount_base <= threshold`), so an amount
	# exactly EQUAL to the threshold still auto-posts.
	("THR-EDGE",    "RT", AT_THRESHOLD, None, "Accepted", True, "submitted"),
	# Above the threshold there is no approval queue: the row is closed as
	# Rejected, stays unsubmitted, and NO counter-doc is created.
	("THR-OVER",    "RT", 26000, None,     "Rejected", False, "none"),

	# --- Manual: counter-doc deferred to acceptance ------------------------
	("MAN-PARK",    "TS",  2200, None,     "Pending",  False, "none"),
	("MAN-ACCEPT",  "TS",  1600, "accept", "Accepted", True,  "submitted"),
	("MAN-REJECT",  "TS",  7300, "reject", "Rejected", False, "none"),
]

# Cases dropped from the grid. Threshold mode no longer parks anything, so
# there is nothing to accept or reject at that tier. Kept here purely so reset
# still purges documents an earlier version of this module seeded.
RETIRED_CASES = ["THR-ACCEPT", "THR-REJECT"]

# (doctype, suffix, expected target doctype)
FLOWS = [
	("Sales Order",    "SO", "Purchase Order"),
	("Sales Invoice",  "SI", "Purchase Invoice"),
	("Delivery Note",  "DN", "Purchase Receipt"),
	("Journal Entry",  "JE", "Journal Entry"),
]


# ===========================================================================
# 1. Drive
# ===========================================================================

def seed_policy_matrix():
	"""Post every case across every source doctype."""
	seed_samecurrency()
	_ensure_po_reference_field()

	print()
	print("=" * 78)
	print(f"DRIVING POLICY MATRIX — {len(CASES)} cases x {len(FLOWS)} source doctypes")
	print("=" * 78)

	_receive_stock(COMPANIES)

	results = []
	for doctype, suffix, _ in FLOWS:
		print(f"\n  --- {doctype} ---")
		for case, pair, amount, action, *_rest in CASES:
			results.append(
				_drive(doctype, suffix, case, pair, _amount_for(pair, amount), action)
			)

	frappe.db.commit()

	ok = sum(1 for r in results if r.get("ok"))
	print(f"\nScenarios posted: {ok}/{len(results)}")
	return results


def _drive(doctype, suffix, case, pair, amount, action):
	"""Dispatch to the right driver. Amount is always qty 1 x rate, so the
	document total lands exactly on `amount` — required for the edge case."""
	marker_key = f"{case}-{suffix}"

	if doctype == "Sales Invoice":
		# _drive_si applies the inbox action itself.
		return _drive_si(marker_key, pair, "fwd", 1, amount, action, prefix=PREFIX)

	if doctype == "Sales Order":
		out = _drive_so(marker_key, pair, amount)
	elif doctype == "Delivery Note":
		out = _drive_dn(marker_key, pair, 1, amount, prefix=PREFIX)
	else:
		out = _drive_je(marker_key, pair, amount, prefix=PREFIX)

	# _drive_dn / _drive_je have no action parameter — apply it here.
	if action and out.get("ok") and out.get("source"):
		out["action"] = _act_on_inbox(doctype, out["source"], action, f"{PREFIX}-{marker_key}")
	return out


def _amount_for(pair_key, amount):
	"""Resolve AT_THRESHOLD against the relationship's own configured value.

	Only the Threshold-based branch of the dispatcher consults
	auto_submit_threshold — Auto and Manual ignore it entirely — so this is
	meaningful for threshold pairs alone.
	"""
	if amount != AT_THRESHOLD:
		return amount
	rel = _relationship_for(pair_key)
	return flt(rel.auto_submit_threshold) if rel else 0


def _ensure_po_reference_field():
	"""Purchase Order needs custom_intercompany_reference for the re-entrancy
	guard and back-reference lookups. It ships as a fixture, but fixtures only
	land on install/migrate — create it here so the seed is self-sufficient.
	"""
	name = "Purchase Order-custom_intercompany_reference"
	if frappe.db.exists("Custom Field", name):
		return
	frappe.get_doc({
		"doctype": "Custom Field",
		"dt": "Purchase Order",
		"fieldname": "custom_intercompany_reference",
		"fieldtype": "Data",
		"label": "Intercompany Reference",
		"insert_after": "supplier",
		"read_only": 1,
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"  [field]     created {name}")


def _drive_so(marker_key, pair_key, amount):
	"""Post one intercompany Sales Order. seed_multicompany has no SO driver,
	so this mirrors _drive_si: qty 1 x rate, marker carried on po_no."""
	marker = f"{PREFIX}-{marker_key}"
	out = {"marker": marker, "pair": pair_key, "ok": False}

	if frappe.db.exists("Sales Order", {"po_no": marker, "docstatus": 1}):
		print(f"  [skip] {marker:16s} already seeded")
		out.update(ok=True, skipped=True)
		return out

	for stale in frappe.get_all("Sales Order", filters={"po_no": marker, "docstatus": 0}, pluck="name"):
		frappe.delete_doc("Sales Order", stale, force=1, ignore_permissions=True)

	rel = _relationship_for(pair_key)
	if not rel:
		out["error"] = "no relationship"
		return out

	src_company = rel.company_a
	currency = frappe.db.get_value("Company", src_company, "default_currency")
	delivery_date = add_days(nowdate(), 7)

	so = frappe.new_doc("Sales Order")
	so.company = src_company
	so.customer = rel.internal_customer_a
	so.transaction_date = nowdate()
	so.delivery_date = delivery_date
	so.po_no = marker
	so.currency = currency
	so.conversion_rate = 1.0
	so.price_list_currency = currency
	so.plc_conversion_rate = 1.0
	so.selling_price_list = f"Standard Selling {currency}"
	so.ignore_pricing_rule = 1
	so.append("items", {
		"item_code": ITEM_CODE,
		"qty": 1,
		"rate": amount,
		"delivery_date": delivery_date,
	})

	try:
		so.submit()
		frappe.db.commit()
		print(f"  [ok]   {marker:16s} {src_company} SO {so.name}  {currency} {amount:,.2f}")
		out.update(ok=True, source=so.name, amount=amount, currency=currency)
	except Exception as e:
		frappe.db.rollback()
		print(f"  [FAIL] {marker:16s} {type(e).__name__}: {str(e)[:110]}")
		out["error"] = str(e)
	return out


# ===========================================================================
# 2. Verify
# ===========================================================================

def verify_policy_matrix():
	"""Assert the policy outcome of every case, for every source doctype."""
	print()
	print("=" * 78)
	print("VERIFICATION — POLICY MATRIX")
	print("=" * 78)

	rows = []
	for doctype, suffix, want_target_doctype in FLOWS:
		for case, pair, amount, action, want_status, want_submitted, want_target in CASES:
			rows.append(_verify(
				doctype, suffix, want_target_doctype,
				case, pair, _amount_for(pair, amount), action,
				want_status, want_submitted, want_target,
			))

	_print_grid(rows)

	failures = [r for r in rows if r["checks"] and not all(c[1] for c in r["checks"])]
	missing = [r for r in rows if not r["checks"]]

	print()
	print("-" * 78)
	print(f"  scenarios verified : {len(rows)}")
	print(f"  fully passing      : {len(rows) - len(failures) - len(missing)}")
	print(f"  with failed checks : {len(failures)}")
	print(f"  not posted at all  : {len(missing)}")
	print("-" * 78)

	if failures:
		print("\nFAILED CHECKS")
		for r in failures:
			for label, passed, detail in r["checks"]:
				if not passed:
					print(f"  {r['marker']:20s} {label:24s} {detail}")

	_report_edge(rows)
	_verify_rejection_is_final()
	return {"rows": rows, "failures": len(failures), "missing": len(missing)}


def _verify_rejection_is_final():
	"""An over-threshold row must be closed: accept() refuses, and no
	counter-doc appears. Probes live and rolls back."""
	print()
	print("  POLICY REJECTION IS FINAL — accept() on an over-threshold row")
	name = frappe.db.get_value(
		"Intercompany Ledger",
		{
			"entry_type": "Transaction",
			"policy_reason": ["like", "Rejected — exceeds threshold%"],
			"status": "Rejected",
		},
		"name",
	)
	if not name:
		print("    [skip] no policy-rejected row to probe")
		return None
	try:
		frappe.get_doc("Intercompany Ledger", name).accept()
		frappe.db.rollback()
		print(f"    [FAIL] accept() was allowed on {name}")
		return False
	except Exception as e:
		frappe.db.rollback()
		print(f"    [ok]   {name} refused: {str(e)[:70]}")
		return True


def _verify(doctype, suffix, want_target_doctype, case, pair, amount, action,
            want_status, want_submitted, want_target):
	marker = f"{PREFIX}-{case}-{suffix}"
	row = {
		"marker": marker,
		"case": case,
		"doctype": suffix,
		"pair": pair,
		"amount": amount,
		"want_status": want_status,
		"status": "-",
		"checks": [],
	}

	source_name = _find_source(doctype, marker)
	if not source_name:
		row["status"] = "NOT POSTED"
		return row

	inbox_name = frappe.db.get_value(
		"Intercompany Ledger",
		{"entry_type": "Transaction", "source_doctype": doctype, "source_name": source_name},
		"name",
	)
	if not inbox_name:
		row["status"] = "NO INBOX"
		row["checks"].append(("inbox row created", False, f"no inbox for {doctype} {source_name}"))
		return row

	inbox = frappe.get_doc("Intercompany Ledger", inbox_name)
	row["status"] = inbox.status
	row["checks"].append(("inbox row created", True, inbox.name))

	# --- the policy decision itself ---
	row["checks"].append((
		"inbox status",
		inbox.status == want_status,
		f"got {inbox.status}, want {want_status}",
	))

	row["checks"].append((
		"inbox docstatus",
		inbox.docstatus == (1 if want_submitted else 0),
		f"got {inbox.docstatus}, want {1 if want_submitted else 0}",
	))

	# --- the counter-doc ---
	state, detail = _target_state(inbox, doctype, source_name)
	row["target_state"] = state
	row["checks"].append((
		"counter doc state",
		state == want_target,
		f"got {state} ({detail}), want {want_target}",
	))

	if state != "none":
		row["checks"].append((
			"counter doc type",
			inbox.target_doctype == want_target_doctype,
			f"got {inbox.target_doctype}, want {want_target_doctype}",
		))

	# --- audit trail: an accepted row must name who accepted it, unless the
	# dispatcher posted it with no human involved (policy_reason starts "Auto").
	if inbox.status == "Accepted" and action == "accept":
		row["checks"].append((
			"accepted_by stamped",
			bool(inbox.accepted_by),
			"accepted_by is empty",
		))

	# --- a log row must exist for the source document ---
	log_count = frappe.db.count(
		"Intercompany Ledger",
		{"entry_type": "Event", "source_doctype": doctype, "source_name": source_name},
	)
	row["checks"].append((
		"event row written",
		log_count > 0,
		f"{log_count} event rows for {source_name}",
	))

	return row


def _find_source(doctype, marker):
	if doctype == "Journal Entry":
		return frappe.db.get_value(
			"Journal Entry",
			{"user_remark": f"{marker} intercompany recharge", "docstatus": 1},
			"name",
		)
	return frappe.db.get_value(doctype, {"po_no": marker, "docstatus": 1}, "name")


def _target_state(inbox, source_doctype, source_name):
	"""Classify the live counter-doc: submitted / draft / none.

	Resolved by BACK-REFERENCE, never by inbox.target_name. reject() deletes the
	counter-doc but leaves target_name populated, and Frappe hands the freed
	autoname to the next document created — so a lookup by name reports a later,
	unrelated document as this row's target.
	"""
	if not inbox.target_doctype:
		return "none", "no target doctype recorded"

	if inbox.target_doctype == "Journal Entry":
		back_ref = {"user_remark": f"Mirror of {source_doctype} {source_name}"}
	else:
		back_ref = {"custom_intercompany_reference": source_name}

	name = frappe.db.get_value(
		inbox.target_doctype, {**back_ref, "docstatus": ["!=", 2]}, "name"
	)
	if not name:
		return "none", "no live counter-doc references the source"

	docstatus = frappe.db.get_value(inbox.target_doctype, name, "docstatus")
	return ("submitted" if docstatus == 1 else "draft"), name


# ===========================================================================
# 3. Reporting
# ===========================================================================

def _print_grid(rows):
	print()
	print(f"  {'CASE':12s} {'DOC':4s} {'PAIR':5s} {'AMOUNT':>10s} "
	      f"{'STATUS':9s} {'WANT':9s} {'TARGET':10s}  RESULT")
	print(f"  {'-'*12} {'-'*4} {'-'*5} {'-'*10} {'-'*9} {'-'*9} {'-'*10}  {'-'*6}")
	for r in rows:
		if not r["checks"]:
			verdict = r["status"]
		else:
			failed = [c for c in r["checks"] if not c[1]]
			verdict = "PASS" if not failed else f"FAIL ({len(failed)})"
		print(
			f"  {r['case']:12s} {r['doctype']:4s} {r['pair']:5s} {r['amount']:>10,.0f} "
			f"{r['status']:9s} {r['want_status']:9s} {r.get('target_state','-'):10s}  {verdict}"
		)


def _report_edge(rows):
	"""Call out the threshold boundary explicitly — it is the case most likely
	to be a business-rule decision rather than a bug."""
	edge = [r for r in rows if r["case"] == "THR-EDGE"]
	if not edge:
		return
	configured = edge[0]["amount"]
	print()
	print(f"  THRESHOLD BOUNDARY — amount exactly {configured:,.0f}, read from the "
	      f"relationship's auto_submit_threshold")
	for r in edge:
		print(f"    {r['doctype']:4s} status={r['status']:9s} target={r.get('target_state','-')}")
	print("    posting_service.py uses `amount_base <= threshold` (inclusive), and only")
	print("    in the Threshold-based branch — Auto and Manual never read the field.")


# ===========================================================================
# 4. Orchestration + cleanup
# ===========================================================================

@frappe.whitelist()
def run_all():
	"""Drive then verify, in one call."""
	seed_policy_matrix()
	return verify_policy_matrix()


@frappe.whitelist()
def reset_policy_matrix(case=None):
	"""Delete the documents this module created, keeping all masters.

	Pass `case` to purge a single row of the grid, e.g. after retuning a
	threshold:  --kwargs "{'case': 'THR-EDGE'}"
	"""
	print(f"Resetting policy-matrix scenario documents{f' for {case}' if case else ''}...")

	all_cases = [c for c, *_ in CASES] + RETIRED_CASES
	markers = {
		f"{PREFIX}-{c}-{suffix}"
		for c in all_cases
		if case is None or c == case
		for _, suffix, _ in FLOWS
	}

	for doctype in ("Sales Order", "Sales Invoice", "Delivery Note"):
		for name in frappe.get_all(doctype, filters={"po_no": ["in", list(markers)]}, pluck="name"):
			_purge_clearing(doctype, name)
			_purge_source(doctype, name)

	for marker in markers:
		remark = f"{marker} intercompany recharge"
		for name in frappe.get_all("Journal Entry", filters={"user_remark": remark}, pluck="name"):
			_purge_clearing("Journal Entry", name)
			_purge_source("Journal Entry", name)

	frappe.db.commit()
	print(f"Reset complete ({len(markers)} markers).")


def _purge_clearing(source_doctype, source_name):
	"""Drop the clearing JEs _post_gl_legs raised for one source document.

	These companies are shared with the SC suite, so this matches on the source
	document name embedded in the remark rather than sweeping every
	"IC clearing for" entry in the company.
	"""
	for name in frappe.get_all(
		"Journal Entry",
		filters={"user_remark": ["like", f"%IC clearing for {source_doctype} {source_name}%"]},
		pluck="name",
	):
		_force_delete("Journal Entry", name)
