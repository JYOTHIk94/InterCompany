"""Same-currency intercompany seed + verification harness.

Every company here shares one currency (SAR), so `fx_rate` is always `1.0` and
every counter-document must carry the *exact* source amount. That makes this the
clean-room regression suite for the dispatcher itself: with FX removed from the
picture, any failure is a genuine posting-logic bug rather than a conversion
problem.

It uses a dedicated set of companies rather than the ones in
`seed_multicompany.py`, because `Intercompany Rule` allows only one
record per company pair — reusing those pairs would mean overwriting their
posting mode and breaking the multicurrency suite.

Coverage: three SAR companies, three relationships (one per posting mode), both
flow directions, and all three document mappings (SI→PI, DN→PR, JE→JE).

Run from bench:

    bench --site <site> execute intercompany.intercompany.seed_samecurrency.seed_samecurrency
    bench --site <site> execute intercompany.intercompany.seed_samecurrency.seed_samecurrency_scenarios
    bench --site <site> execute intercompany.intercompany.seed_samecurrency.verify_samecurrency

    # or all three in order:
    bench --site <site> execute intercompany.intercompany.seed_samecurrency.run_all

    # wipe only the documents this module created (masters are kept):
    bench --site <site> execute intercompany.intercompany.seed_samecurrency.reset_samecurrency

Idempotent throughout: masters are created only when absent, and each scenario is
keyed on an `SC-<KEY>` marker so re-runs skip what already exists.
"""

import frappe

from intercompany.intercompany.seed import (
	ITEM_CODE,
	_ensure_company_defaults,
	_ensure_item,
	_ensure_price_list,
)
from intercompany.intercompany.seed_multicompany import (
	_drive_dn,
	_drive_je,
	_drive_si,
	_ensure_company_cur,
	_ensure_internal_party,
	_ensure_pair,
	_ensure_stock_everywhere,
	_force_delete,
	_print_matrix,
	_purge_source,
	_receive_stock,
	_verify_si,
	register_companies,
	register_pairs,
)

CURRENCY = "SAR"
PREFIX = "SC"   # marker prefix, keeps these documents distinct from the MC- suite

CO_R = "QCS Retail"
CO_T = "QCS Trading"
CO_S = "QCS Services"

# (name, abbr, currency) — all identical currency, by design
COMPANIES = [
	(CO_R, "QCSR", CURRENCY),
	(CO_T, "QCST", CURRENCY),
	(CO_S, "QCSS", CURRENCY),
]

# (key, company_a, company_b, posting_mode, auto_submit_threshold)
#   RT — Threshold-based: exercises both sides of the threshold
#   RS — Auto:            everything posts straight through
#   TS — Manual:          everything parks for approval
PAIRS = [
	("RT", CO_R, CO_T, "Threshold-based", 10000),
	("RS", CO_R, CO_S, "Auto", 0),
	("TS", CO_T, CO_S, "Manual", 0),
]

# Share the engine's lookup tables without joining what it provisions.
register_companies(COMPANIES)
register_pairs(PAIRS)

# (marker_key, pair_key, direction, qty, rate, inbox_action)
SI_SCENARIOS = [
	# --- RT : Threshold-based, 10 000 -------------------------------------
	("RT-UNDER",   "RT", "fwd", 1,  3000, None),      # auto-post below threshold
	("RT-EDGE",    "RT", "fwd", 1, 10000, None),      # exactly AT threshold → parks
	("RT-OVER",    "RT", "fwd", 1, 26000, None),      # parks Pending
	("RT-ACCEPT",  "RT", "fwd", 2,  9000, "accept"),  # 18k → park then accept
	("RT-REJECT",  "RT", "fwd", 1, 41000, "reject"),  # park then reject
	("RT-REV",     "RT", "rev", 1,  5500, None),      # T → R, auto-post

	# --- RS : Auto mode ----------------------------------------------------
	("RS-SMALL",   "RS", "fwd", 1,  1200, None),      # auto regardless of size
	("RS-LARGE",   "RS", "fwd", 3, 20000, None),      # auto even at 60k
	("RS-REV",     "RS", "rev", 1,  4400, None),      # S → R, auto

	# --- TS : Manual mode --------------------------------------------------
	("TS-MANUAL",  "TS", "fwd", 1,  2200, None),      # parks regardless of size
	("TS-ACCEPT",  "TS", "fwd", 2,  1600, "accept"),  # park then accept
	("TS-REJECT",  "TS", "fwd", 1,  7300, "reject"),  # park then reject
	("TS-REV",     "TS", "rev", 1,   980, None),      # S → T, parks
]

# (marker_key, pair_key, qty, rate)
DN_SCENARIOS = [
	("DN-RT", "RT", 5, 300),
	("DN-RS", "RS", 4, 450),
]

# (marker_key, pair_key, amount)
JE_SCENARIOS = [
	("JE-RT", "RT", 5200),
	("JE-RS", "RS", 3300),
	("JE-TS", "TS", 1450),
]


# ===========================================================================
# 1. Masters
# ===========================================================================

def seed_samecurrency():
	"""Create the three SAR companies and their three relationships."""
	print("=" * 72)
	print(f"SEEDING SAME-CURRENCY MASTERS ({CURRENCY})")
	print("=" * 72)

	for name, abbr, currency in COMPANIES:
		_ensure_company_cur(name, abbr, currency)
		_ensure_company_defaults(name, abbr)
		print(f"  [company]   {name:16s} {abbr:6s} {currency}")

	_ensure_price_list(f"Standard Selling {CURRENCY}", CURRENCY, selling=1)
	_ensure_price_list(f"Standard Buying {CURRENCY}", CURRENCY, buying=1)
	print(f"  [pricelist] Standard Selling/Buying {CURRENCY}")

	# No Currency Exchange rows are needed: get_rate() short-circuits to 1.0
	# whenever from_currency == to_currency.
	print("  [fx]        none required — single currency, rate is always 1.0")

	_ensure_item(ITEM_CODE)
	_ensure_stock_everywhere(COMPANIES)

	links = _counterparties_local()
	parties = {}
	for company, _, _ in COMPANIES:
		parties[company] = {
			"customer": _ensure_internal_party("Customer", company, links[company]),
			"supplier": _ensure_internal_party("Supplier", company, links[company]),
		}
		print(f"  [parties]   {company:16s} customer+supplier representing it")

	created = []
	for key, co_a, co_b, mode, threshold in PAIRS:
		rel = _ensure_pair(key, co_a, co_b, mode, threshold, parties)
		created.append(rel)
		print(
			f"  [relation]  {key}  {co_a} <-> {co_b}  mode={mode}"
			+ (f" threshold={threshold:,.0f}" if mode == "Threshold-based" else "")
			+ f"  -> {rel}"
		)

	frappe.db.commit()
	print(f"\nMasters ready: {len(COMPANIES)} companies (all {CURRENCY}), "
	      f"{len(created)} relationships.")
	return created


def _counterparties_local():
	"""Counterparty map for THIS module's pairs only."""
	links = {name: set() for name, _, _ in COMPANIES}
	for _, co_a, co_b, _, _ in PAIRS:
		links[co_a].add(co_b)
		links[co_b].add(co_a)
	return links


# ===========================================================================
# 2. Scenarios
# ===========================================================================

def seed_samecurrency_scenarios():
	"""Drive documents through the dispatcher across all three pairs."""
	seed_samecurrency()

	print()
	print("=" * 72)
	print("DRIVING SAME-CURRENCY SCENARIOS")
	print("=" * 72)

	results = {"si": [], "dn": [], "je": []}

	for marker_key, pair_key, direction, qty, rate, action in SI_SCENARIOS:
		results["si"].append(
			_drive_si(marker_key, pair_key, direction, qty, rate, action, prefix=PREFIX)
		)

	_receive_stock(COMPANIES)
	for marker_key, pair_key, qty, rate in DN_SCENARIOS:
		results["dn"].append(_drive_dn(marker_key, pair_key, qty, rate, prefix=PREFIX))

	for marker_key, pair_key, amount in JE_SCENARIOS:
		results["je"].append(_drive_je(marker_key, pair_key, amount, prefix=PREFIX))

	frappe.db.commit()

	ok = sum(1 for group in results.values() for r in group if r.get("ok"))
	total = sum(len(group) for group in results.values())
	print(f"\nScenarios posted: {ok}/{total}")
	return results


# ===========================================================================
# 3. Verification
# ===========================================================================

def verify_samecurrency():
	"""Every counter-document must carry the source amount unchanged.

	Same currency means the expected FX rate is exactly 1.0, so `want_rate`
	equals the source rate. Any deviation is a posting bug, not a rounding or
	conversion artefact.
	"""
	print()
	print("=" * 72)
	print(f"VERIFICATION — SAME CURRENCY ({CURRENCY}), EXPECTED FX = 1.0")
	print("=" * 72)

	rows = [
		_verify_si(marker_key, pair_key, direction, qty, rate, action, prefix=PREFIX)
		for marker_key, pair_key, direction, qty, rate, action in SI_SCENARIOS
	]

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


# ===========================================================================
# 4. Orchestration + cleanup
# ===========================================================================

@frappe.whitelist()
def run_all():
	"""Masters → scenarios → verification, in one call."""
	seed_samecurrency()
	seed_samecurrency_scenarios()
	return verify_samecurrency()


@frappe.whitelist()
def reset_samecurrency():
	"""Delete the documents this module created, keeping all masters."""
	print("Resetting same-currency scenario documents...")

	markers = {f"{PREFIX}-{k}" for k, *_ in SI_SCENARIOS} | {f"{PREFIX}-{k}" for k, *_ in DN_SCENARIOS}

	for doctype in ("Sales Invoice", "Delivery Note"):
		for name in frappe.get_all(doctype, filters={"po_no": ["in", list(markers)]}, pluck="name"):
			_purge_source(doctype, name)

	for marker_key, _, _ in JE_SCENARIOS:
		remark = f"{PREFIX}-{marker_key} intercompany recharge"
		for name in frappe.get_all("Journal Entry", filters={"user_remark": remark}, pluck="name"):
			_purge_source("Journal Entry", name)

	# Clearing JEs belonging to this module's companies only.
	our_companies = [c[0] for c in COMPANIES]
	for name in frappe.get_all(
		"Journal Entry",
		filters={"user_remark": ["like", "%IC clearing for%"], "company": ["in", our_companies]},
		pluck="name",
	):
		_force_delete("Journal Entry", name)

	for name in frappe.get_all(
		"Intercompany Ledger", filters={"source_company": ["in", our_companies]}, pluck="name"
	):
		_force_delete("Intercompany Ledger", name)
	frappe.db.commit()
	print("Reset complete.")
