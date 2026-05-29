import frappe
from frappe.utils import getdate

_cache = {}


def get_rate(from_currency, to_currency, posting_date, fx_policy="Posting Date"):
	if not from_currency or not to_currency or from_currency == to_currency:
		return 1.0

	key = (from_currency, to_currency, str(getdate(posting_date)))
	if key in _cache:
		return _cache[key]

	rate = frappe.db.get_value(
		"Currency Exchange",
		{"from_currency": from_currency, "to_currency": to_currency, "date": ["<=", posting_date]},
		"exchange_rate",
		order_by="date desc",
	)

	if not rate and fx_policy == "Block Missing":
		frappe.throw(
			f"FX rate missing for {from_currency} → {to_currency} on {posting_date}"
		)

	rate = float(rate) if rate else 0.0
	_cache[key] = rate
	return rate


def reset_cache():
	_cache.clear()
