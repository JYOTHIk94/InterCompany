# Changelog

All notable changes to the **Intercompany** app are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

| | |
|---|---|
| **App name** | `intercompany` |
| **Current version** | `14.0.1` |
| **Module** | `Intercompany` |
| **Requires** | Frappe v14, ERPNext v14 (Accounts + Stock) |
| **License** | MIT |

---

## [Unreleased]

Targeted at `14.0.2`. See [Known Issues / Technical Debt](#known-issues--technical-debt)
for what remains.

### Fixed

* **Journal Entry counter-documents crashed on every auto-submitted flow.**
  `services/posting_service._create_target()` ended with an unguarded debug
  statement, `print(f"DBG before submit: items[0].rate={tgt.items[0].rate}")`.
  A `Journal Entry` has no `items` table, so any JE→JE mapping reaching the
  submit path raised `AttributeError: 'JournalEntry' object has no attribute
  'items'` and — because the default `on_error` is `Block` — aborted the source
  JE's submission entirely. The two preceding debug lines were guarded by
  `if tgt.get("items")`; this one was not. All five `DBG` statements and the
  inlined `import sys` have been removed (was Known Issue #1).

* **The Intercompany workspace rendered completely blank.** Three faults
  compounded (was Known Issue #4):
  1. `fixtures/number_card.json` was an empty array, so **no** Number Card was
     ever created on a site — even though `hooks.fixtures` exports the doctype.
  2. The four card definitions under `intercompany/intercompany/number_card/`
     are never loaded: Frappe v14's `IMPORTABLE_DOCTYPES` (`frappe/model/sync.py`)
     has no `number_card` entry, so module-folder cards are not synced by
     `bench migrate`. Fixtures are the only supported delivery path.
  3. `fixtures/workspace.json` referenced cards by *label* (`PENDING IN INBOX`,
     `ACTIVE RELATIONSHIPS`, `AUTO POSTED DOCS`) and its `number_cards` child
     rows named cards (`INbox Count`, `Intercompany Relationships`,
     `Autopost in Inbox`) that never existed under any name.

  The workspace also shipped with `links: []` and `shortcuts: []`, so even a
  correct render had no navigation. Now fixed: the four cards ship as real
  fixtures, the workspace references them by name, and it gains four shortcuts
  (Inbox filtered to Pending, Relationship, Log, Unmatched IC Balance) plus two
  link cards — *Masters & Transactions* and *Reports*.

---

## [14.0.1] — 2026-05-29

Initial release. Everything below ships in this version.

### Added — Core concept

An intercompany automation layer for ERPNext v14. When a document is submitted in
one company, the app detects that the counterparty is a *sister company*, creates
the mirrored counter-document in that company, records the Due-to/Due-from clearing
movement, and routes the result through an approval inbox with a full audit log.

```
Company A                        Company B
─────────                        ─────────
Sales Invoice   ──on_submit──▶  Purchase Invoice   (+ Inbox row + Log row)
Delivery Note   ──on_submit──▶  Purchase Receipt   (+ Inbox row + Log row)
Journal Entry   ──on_submit──▶  Journal Entry      (mirrored, sign-flipped)
        │
        └── clearing legs drafted on Due-from (A) / Due-to (B)
```

### Added — DocTypes

#### `Intercompany Relationship` (`IC-REL-.YYYY.-.#####`, track changes)

The master record that pairs two companies and holds all posting policy.

* **Companies section** — `company_a`, `company_b` (both mandatory), plus the four
  internal party links: `internal_customer_a` / `internal_supplier_a` (represent B)
  and `internal_customer_b` / `internal_supplier_b` (represent A).
* **Posting Policy section**
  * `posting_mode` — `Auto` / `Threshold-based` (default) / `Manual`.
  * `auto_submit_threshold` — currency cut-off used by `Threshold-based` mode.
  * `approver_role` — Role required to accept/reject inbox rows for this pair.
  * `fx_policy` — `Posting Date` (default) / `Manual` / `Block Missing`.
  * `on_error` — `Block` (default) / `Queue` / `Ignore`; controls whether a posting
    exception re-raises and aborts the source submission.
* **Due-To / Due-From Accounts section** — `due_to_a`, `due_from_a`, `due_to_b`,
  `due_from_b`, all mandatory.
* **Document Mapping section** — child table of `Intercompany Document Mapping` rows.
* **Server-side validation** (`intercompany_relationship.py`)
  * Company A and Company B cannot be the same.
  * All four Due-to/Due-from accounts are mandatory.
  * Each clearing account must belong to the company it is filed under.
  * Only one relationship may exist per company pair (checked in both directions).
  * An internal customer must actually `represents_company` the opposite company.
* **Permissions** — System Manager (rwcd), Intercompany Approver (rwc),
  Intercompany User (read-only).

#### `Intercompany Document Mapping` (child table)

One row per source→target document translation on a relationship.

* `source_doctype`, `target_doctype` (both mandatory links to DocType).
* `mapping_rule` — `Item to Item` (default) / `Warehouse to Warehouse` /
  `Mirror with Sign Flip` / `Allocation`.
* `pricing_rule` — `1:1` (default) / `Cost Plus Markup` / `Custom`.
* `markup_pct` — percent uplift applied when `pricing_rule = Cost Plus Markup`.
* `status` — `Active` (default) / `Draft` / `Disabled`; only `Active` rows resolve.

#### `Intercompany Inbox` (`IC-INBOX-.YYYY.-.#####`, submittable, track changes)

The approval queue and the single source of truth linking a source document to its
counter-document.

* **Source section** — `source_company`, `source_doctype`, `source_name`
  (Dynamic Link), `target_company`, `target_doctype`, `target_name` (Dynamic Link
  to the draft counter-doc).
* **Amount section** — `amount` (base currency), `currency`, `fx_rate`.
* **Status section** — `status` (`Pending` / `Accepted` / `Rejected` / `Failed`),
  `policy_reason` (human-readable explanation of why the row landed where it did),
  `retry_count`, `last_attempt`.
* **Audit section** — `accepted_by`, `accepted_on`, `error_message`.
* **`accept()`** (whitelisted) — authorizes the caller against the relationship's
  `approver_role`, submits the linked draft counter-document, posts the clearing GL
  legs, stamps `accepted_by` / `accepted_on`, sets status `Accepted`, submits the
  inbox row, and writes a log entry. Idempotent: re-accepting returns immediately.
* **`reject(reason)`** (whitelisted) — deletes the draft counter-doc (or cancels it
  if already submitted), sets status `Rejected`, stores the reason in
  `error_message`, and writes a log entry. Idempotent.
* **`bulk_accept(names)`** (module-level whitelisted) — accepts a JSON list of inbox
  names, returning a per-row `{name, ok, error}` result so one failure does not
  abort the batch.
* **Permissions** — System Manager (full incl. submit/cancel/amend), Intercompany
  Approver (rwc + submit/cancel), Intercompany User (read-only).

#### `Intercompany Log` (`IC-LOG-.YYYY.-.#####`)

Append-only audit trail written by every dispatcher path.

* `reference_doctype` / `reference_name` (Dynamic Link), `company`, `action`,
  `status` (`Success` / `Failed` / `Queued`), `timestamp` (defaults to Now),
  `message`.
* **Permissions** — System Manager (rwcd), Approver and User read-only.

### Added — Posting engine (`services/posting_service.py`)

* **`process_ic_event(doc, method)`** — the dispatcher wired to `on_submit`.
  1. Re-entrancy guard — skips documents the dispatcher itself generated.
  2. Relationship detection (see below); silently returns for non-IC documents.
  3. Mapping resolution; logs `Failed / Mapping missing` when no active row matches.
  4. FX resolution and base-amount extraction.
  5. Branches on `posting_mode`:
     * **`Auto`** — creates *and submits* the counter-doc, posts clearing legs,
       creates an `Accepted` inbox row, logs `Success`.
     * **`Threshold-based`** — under threshold behaves like `Auto` (policy reason
       `Auto (under N)`); at or over threshold the counter-doc stays a draft, inbox
       is `Pending` with reason `Threshold > N`, log status `Queued`.
     * **`Manual`** — always drafts the counter-doc and parks a `Pending` inbox row
       with reason `Manual review`.
  6. On exception — writes a `Failed` log, calls `frappe.log_error`, and re-raises
     only when the relationship's `on_error` is `Block`.
* **Relationship detection (`_find_relationship`)**
  * *Sales Invoice / Delivery Note* — resolves the customer's `represents_company`
    and looks up the relationship for the `{doc.company, represents}` pair.
  * *Journal Entry* — matches any JE account against the four Due-to/Due-from
    accounts on any relationship.
* **Counter-document builders (`_create_target`)**
  * *Purchase Invoice* — sets target company, internal supplier, `bill_no`,
    `custom_intercompany_reference`, dates carried from the source, and item rows
    with fully populated `rate` / `price_list_rate` / `base_*` / `net_*` fields so
    ERPNext's `set_missing_item_details` cannot re-fetch from the price list.
    `credit_to` is deliberately omitted so the supplier's default payable account is
    used. Cost-Plus-Markup uplift is applied here.
  * *Purchase Receipt* — same shape, plus target-company warehouse resolution.
  * *Journal Entry* — mirrors the source lines through `_ic_account_map`, flipping
    debit↔credit; drops non-IC accounts (they belong to the source company only);
    self-balances a single-line mirror as a memo entry; throws when the source JE
    contains no IC clearing accounts at all.
  * *Anything else* — throws `Unsupported target doctype`.
  * **Idempotency** — if an inbox row already exists for the source document, the
    existing counter-doc is returned instead of creating a second one.
* **Multicurrency handling** — the counter-doc is created in the **target company's**
  currency, with item rates converted at `fx_rate`, `conversion_rate = 1.0`, and a
  buying price list whose currency matches the target company
  (`_apply_buying_price_list`). On cross-currency documents the pricing-rule re-fetch
  is suppressed via `ignore_pricing_rule` and `flags.ignore_pricing_rule` at both
  insert and submit, and the document is reloaded from the DB before submit so the
  in-memory copy cannot re-trigger `set_missing_values`. This avoids ERPNext v14's
  party-account currency-mismatch validation.
* **Clearing GL legs (`_post_gl_legs`)** — posts one **same-company Journal Entry
  per side** (source: Due-from; target: Due-to) rather than a single cross-company
  *Inter Company Journal Entry*, which v14 requires to be bidirectionally linked.
  Target-side amounts are FX-converted and rounded to 2 dp. Both JEs are left as
  **drafts** so sites can route them through their own approval flow. These legs
  give the *Unmatched IC Balance* report its data.
* **`cascade_cancel(doc, method)`** — wired to `on_cancel`. Looks up the inbox row
  for the source document, cancels the submitted counter-doc (or force-deletes it
  while still draft), flips the inbox row to `Rejected`, and logs the reversal.

### Added — Supporting services

* **`services/fx_service.py`** — `get_rate(from, to, posting_date, fx_policy)` reads
  the latest `Currency Exchange` record on or before the posting date, returns `1.0`
  for same/absent currencies, throws when `fx_policy = "Block Missing"` and no rate
  exists, and memoizes results in a process-local `_cache` (clearable via
  `reset_cache()`).
* **`services/inbox_service.py`** — `create_inbox(...)` builds the inbox row,
  deriving the target company from the relationship and the amount from
  `base_grand_total` → `grand_total` → `total_debit`. Returns the existing row name
  when one already exists (idempotent).
* **`services/log_service.py`** — `log_ic(doc, action, status, message)` one-liner
  for writing `Intercompany Log` rows.

### Added — Hooks (`hooks.py`)

* `doc_events` on `Sales Invoice`, `Delivery Note`, and `Journal Entry` for both
  `on_submit` (→ `create_ic_transaction`) and `on_cancel` (→ `reverse_ic_transaction`),
  each routed through a thin per-doctype module in `overrides/`.
* `permission_query_conditions` for `Intercompany Inbox`, `Intercompany Log`, and
  `Intercompany Relationship`.
* `fixtures` export list covering Custom Field, Role, Number Card, Workspace, and
  Custom HTML Block records.

### Added — Permissions (`permissions.py`)

Company-scoped row-level filtering driven by each user's `User Permission` records
for `Company`:

* Administrator sees everything (no clause emitted).
* A user with no company permissions sees nothing (`field = '__none__'`).
* Inbox and Relationship match on **either** side of the pair (`source`/`target`,
  `company_a`/`company_b`); Log matches on its single `company` field.
* Company names are escaped through `frappe.db.escape`.

### Added — Roles

* **Intercompany User** — desk access, read-only across all three DocTypes.
* **Intercompany Approver** — desk access, can create/edit relationships and act on
  the inbox (accept/reject).

Both ship as module role fixtures (`intercompany/role/*.json`) and as export
fixtures in `hooks.py`.

### Added — Custom Fields (fixtures)

* `Purchase Invoice.custom_intercompany_reference` (Data, after `represents_company`).
* `Purchase Receipt.custom_intercompany_reference` (Data, read-only, after `supplier`).
* `Delivery Note.custom_intercompany_reference` (Data, read-only, after `customer`).

These also serve as the dispatcher's marker for "this document was generated by
Intercompany", preventing recursive hook firing.

### Added — Reports

* **Unmatched IC Balance** (Script Report, ref DocType `Intercompany Relationship`,
  total row enabled). For every relationship it sums `debit - credit` on
  `tabGL Entry` for A's Due-from and B's Due-to accounts (`is_cancelled = 0`) and
  lists only pairs whose variance exceeds 0.01. Visible to System Manager,
  Intercompany User, and Intercompany Approver.

### Added — Desk UI

* **`intercompany_inbox.js`** — form view adds **Accept** (green) and **Reject**
  (with a reason prompt) buttons while status is `Pending`, plus an **Open
  counter-doc** button whenever a target document is linked.
* **`intercompany_inbox_list.js`** — colour-coded list indicators
  (Pending → orange, Accepted → green, Rejected/Failed → red) and a **Bulk accept**
  action-menu item that posts the checked rows to `bulk_accept`.
* **Workspace `Intercompany`** (public, sequence 23) — header, three number cards,
  and the *Intercompany Inbox Activity* custom block.
* **Number Cards** — *Active Relationships* (count of relationships, monthly),
  *Pending Inbox* (inbox where status = Pending, daily), *Auto-Posted Today*
  (logs with status Success in today's timespan, daily), *Failed Postings*
  (logs with status Failed, weekly).
* **Custom HTML Block `Intercompany Inbox Activity`** — renders the five most recent
  inbox rows (name, source document, target doctype, colour-coded status, amount)
  in a bordered table, linking each row to its form.
* **`config/desktop.py`** — registers the `Intercompany` module tile.

### Added — Tests

* **`tests/test_intercompany_e2e.py`** — end-to-end suite that seeds demo data and
  asserts: under-threshold auto-post creates a submitted PI, an `Accepted` inbox row
  and exactly two clearing JEs (one per company); over-threshold parks a draft PI
  with a `Pending` inbox row; `accept()` submits the parked PI and stamps the audit
  fields; `reject()` deletes the draft PI; cancelling the source SI cascades a cancel
  to the counter PI and flips the inbox to `Rejected`; and re-firing the hook does
  not create a duplicate inbox row.
  ```
  bench --site <site> run-tests --app intercompany \
        --module intercompany.intercompany.tests.test_intercompany_e2e
  ```
* **DocType unit tests** — import-resolution regression test plus same-company
  rejection (`Intercompany Relationship`), submittable-meta and field-presence checks
  (`Intercompany Inbox`), and field-presence checks (`Intercompany Log`).

---

## Known Issues / Technical Debt

Findings from the review of `14.0.1`, to be addressed in a future release:

1. **Under-threshold multicurrency posts unconverted amounts** *(confirmed by
   testing across a four-company, three-currency matrix)*. In
   `process_ic_event()`, the `Threshold-based` *under-threshold* branch calls
   `_create_target(doc, rel, mapping, submit=True)` and
   `_post_gl_legs(doc, rel, target, amount_base)` **without** `fx_rate`, so both
   fall back to `1.0`. The `Auto` branch, the over-threshold branch and the
   `Manual` branch all pass it correctly, so this one path is the only leak —
   and it is the path most transactions take:

   | Flow | Posted on counter-doc | Should be |
   |---|---|---|
   | SAR→AED | 900.00 AED | 881.37 (900 × 0.9793) |
   | AED→SAR | 3,000.00 SAR | 3,063.30 (3,000 × 1.0211) |
   | AED→EUR | 1,800.00 EUR | 450.36 (1,800 × 0.2502) |
   | EUR→AED | 2,200.00 AED | 8,792.96 (2,200 × 3.9968) |

   The inbox row stamps the *correct* FX rate in every case, so the counter-doc
   silently disagrees with its own audit record. Fix is to pass `fx_rate=fx_rate`
   to both calls in that branch.
2. **The clearing legs net to zero, so *Unmatched IC Balance* can never report.**
   `_post_gl_legs()` debits *and* credits the **same** account on each side
   (`src_due_from` twice, `tgt_due_to` twice), so every clearing JE nets to zero
   — and they are left as drafts, which produce no `GL Entry` rows at all. The
   report reads `tabGL Entry`, so every variance computes as `0` and no row ever
   passes its `abs(variance) > 0.01` filter. The legs need a genuine contra
   account (`Dr Due-from / Cr <clearing>`), and something must submit them.
3. **`reject()` leaves `target_name` pointing at a deleted document.** The draft
   counter-doc is removed but the inbox row keeps its name. If that autoname is
   later reused, the inbox row silently links to an unrelated document — observed
   during testing, where a rejected row ended up pointing at a Purchase Invoice
   belonging to a different source invoice in a different company. `target_name`
   should be cleared on reject.
4. **The four `intercompany/intercompany/number_card/*.json` files are dead code**
   — Frappe v14 does not sync module-folder Number Cards (see the Fixed entry
   above). They now duplicate `fixtures/number_card.json` and should be deleted
   to avoid two diverging definitions of the same cards.
5. **`fx_service.get_rate()` returns `0.0`** when no rate is found and the policy is
   not `Block Missing`, which silently zeroes converted amounts rather than falling
   back to `1.0` or raising.
6. **`_default_warehouse()` has a dead first statement** — it fetches
   `default_inventory_account` into `wh` and then discards it, returning the first
   non-group warehouse regardless.
7. **Fixture role definitions disagree with module role definitions** —
   `fixtures/role.json` has `is_custom: 0` while `intercompany/role/*.json` has
   `is_custom: 1`; the same roles are shipped twice by two mechanisms.
8. **`Purchase Invoice.custom_intercompany_reference` is not read-only**, unlike its
   Purchase Receipt and Delivery Note counterparts, even though it is the marker the
   re-entrancy guard trusts to prevent recursive posting.

---

[Unreleased]: #unreleased
[14.0.1]: #1401--2026-05-29
