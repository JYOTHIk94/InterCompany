# Intercompany

Intercompany transaction automation for ERPNext v14.

Submit a document in one company and this app creates the mirrored
counter-document in the sister company, records the Due-to/Due-from clearing
movement, and routes the result through an approval queue with a full audit trail — both
held in a single **Intercompany Ledger**.

```
Company A                            Company B
─────────                            ─────────
Sales Invoice   ──on_submit──▶      Purchase Invoice     + Ledger Transaction + Event
Delivery Note   ──on_submit──▶      Purchase Receipt     + Ledger Transaction + Event
Journal Entry   ──on_submit──▶      Journal Entry        (mirrored, sign-flipped)
       │
       └── clearing legs drafted on Due-from (A) / Due-to (B)
```

| | |
|---|---|
| Version | `14.0.1` |
| Requires | Frappe v14, ERPNext v14 (Accounts + Stock) |
| Python | `>=3.10` |
| License | MIT |

---

## 1. Install

```bash
cd ~/your-bench
bench get-app intercompany <repo-url>
bench --site <site> install-app intercompany
bench --site <site> migrate
```

`migrate` also imports the app's fixtures: the two roles, the four number cards,
the Intercompany workspace, the ledger-activity block, and the
`custom_intercompany_reference` custom fields on Purchase Invoice, Purchase
Receipt and Delivery Note.

Verify the install:

```bash
bench --site <site> execute frappe.get_installed_apps
```

---

## 2. Setup — do these in order

Nothing fires until all five steps are complete. The dispatcher silently ignores
any document it cannot match to a relationship, so a missed step looks like
"nothing happened" rather than an error.

### Step 1 — Two companies

Create both companies normally (**Company** list). Note each one's abbreviation;
account names are suffixed with it.

### Step 2 — Four Due-to / Due-from clearing accounts

Two per company, under any group you like (Current Assets / Current Liabilities
are conventional):

| Account | In company | Root type |
|---|---|---|
| `Due to <Company B>` | A | Liability |
| `Due from <Company B>` | A | Asset |
| `Due to <Company A>` | B | Liability |
| `Due from <Company A>` | B | Asset |

> **Leave `Account Type` blank.** Setting it to *Receivable* or *Payable* forces
> ERPNext to demand a Party on every Journal Entry line, which is wrong for an
> intercompany clearing ledger and will block the clearing legs.

Each account must belong to the company it is filed under — the relationship
validates this and throws otherwise.

### Step 3 — Internal Customer and Internal Supplier

This is the detection key. For a flow **A → B** you need:

* an **internal Customer** used in A's books that represents B
  (`Is Internal Customer` ✓, `Represents Company` = B, and A listed under
  *Allowed To Transact With*)
* an **internal Supplier** used in B's books that represents A
  (`Is Internal Supplier` ✓, `Represents Company` = A, and B listed under
  *Allowed To Transact With*)

For the reverse direction (B → A), create the mirror image.

> **ERPNext allows only ONE internal Customer and ONE internal Supplier per
> represented company, site-wide.** You cannot create a separate internal
> customer per company pair — add every counterparty to the single record's
> *Allowed To Transact With* table instead.

### Step 4 — Intercompany Rule

**Intercompany > Intercompany Rule > New**. One record per company pair
(a second record for the same pair is rejected in either direction).

| Section | Field | Notes |
|---|---|---|
| Companies | `Company A`, `Company B` | must differ |
| | `Internal Customer in A`, `Internal Supplier in B` | drives A → B |
| | `Internal Customer in B`, `Internal Supplier in A` | drives B → A |
| Account Payable / Receivable | `Account Payable in A`, `Account Receivable in A` | all mandatory |
| | `Account Payable in B`, `Account Receivable in B` | all mandatory |
| Posting Policy | `Posting Mode` | `Auto` / `Threshold-based` / `Manual` |
| | `Auto Submit Threshold` | used by `Threshold-based` only |
| | `Approver Role in Target` | role required to accept/reject ledger entries |
| | `FX Policy` | `Posting Date` / `Manual` / `Block Missing` |
| | `On Error` | `Block` / `Queue` / `Ignore` |

### Step 5 — Document Mapping rows

**Intercompany > Intercompany Settings**. Mappings are global — they are defined
once and apply to every Intercompany Rule. Add at least one **Active** row,
otherwise the source document is logged as `Failed / Mapping missing`:

| Source DocType | Target DocType | Mapping Rule | Pricing |
|---|---|---|---|
| Sales Invoice | Purchase Invoice | Item to Item | `1:1` or `Cost Plus Markup` |
| Delivery Note | Purchase Receipt | Warehouse to Warehouse | `1:1` |
| Journal Entry | Journal Entry | Mirror with Sign Flip | `1:1` |

Choosing `Cost Plus Markup` applies `rate x (1 + Markup % / 100)` to every item
on the counter-document. Only one **Active** row per Source DocType is allowed —
resolution takes the first match, so a second would never apply.

### Step 6 (optional) — Assign roles

* **Intercompany User** — read-only across all the app's doctypes.
* **Intercompany Approver** — can maintain relationships and accept/reject ledger entries.

To scope users to their own companies, add **User Permission** records for
`Company`. Ledger and Relationship rows match on *either* side of the pair
(Event entries carry only a source company, so they match on that). A user with
no company permission sees nothing; Administrator sees everything.

---

## 3. How it works, step by step

What happens the moment you press **Submit** on a Sales Invoice, Delivery Note
or Journal Entry:

1. **Re-entrancy guard.** Documents the app itself generated are skipped — a JE
   whose remark starts with `Mirror of` / contains `IC clearing for`, or a
   PI/PR carrying `custom_intercompany_reference`. This stops mirrors from
   triggering mirrors.
2. **Relationship lookup.**
   * *Sales Invoice / Delivery Note* — reads the customer's `Represents Company`
     and finds the relationship for that company pair.
   * *Journal Entry* — matches any line's account against the four clearing
     accounts on any relationship.
   * No match → the app returns silently and the document submits as normal.
3. **Mapping resolution.** The first Active mapping row for this source doctype.
   None → `Failed / Mapping missing` in the log, and nothing else happens.
4. **FX and amount.** The rate is looked up from source currency to the *target
   company's* currency at the posting date; the amount is `base_grand_total`
   (or `total_debit` for a JE).
5. **Posting mode decides the outcome:**

   | Mode | Counter-document | Ledger status | Clearing legs |
   |---|---|---|---|
   | `Auto` | created **and submitted** | `Accepted` | posted immediately |
   | `Threshold-based`, amount **<** threshold | created **and submitted** | `Accepted` | posted immediately |
   | `Threshold-based`, amount **>=** threshold | created as **draft** | `Pending` | deferred to accept |
   | `Manual` | created as **draft** | `Pending` | deferred to accept |

6. **Clearing legs.** Two same-company Journal Entries are drafted — one on the
   source side's Due-from, one on the target side's Due-to, with the target
   amount FX-converted. They are left as **drafts** so you can route them
   through your own approval flow.
7. **Audit.** Every outcome is stamped onto that document's ledger row —
   `Action`, `Message` and `Timestamp` always show the most recent event, and
   each event is appended to the row's comment trail.

### Working the ledger

**Intercompany > Intercompany Ledger**. **One row per source document**, so
submitting a Sales Invoice creates exactly one entry, and everything that happens
to it afterwards (auto-post, accept, reject, cancel) updates that same row rather
than adding new ones. Open a row and the **Latest Event** section shows the most
recent action; the comment sidebar holds the full chronological trail.

**Entry Type** records how the row came to exist:

* **Transaction** — the normal case. Carries the amount, the counter-document and
  the approval workflow. Statuses: `Pending`, `Accepted`, `Rejected`, `Failed`.
* **Event** — a document the dispatcher could not open a transaction for, such as
  one with no active mapping or one that errored before the row existed. Statuses:
  `Success`, `Queued`, `Failed`.

The row's `Status` tracks the approval workflow and is not overwritten by later
events — with one exception: a failure sets it to `Failed`, which is terminal and
valid for both entry types.

Transaction rows sitting at `Pending` need a decision:

* **Accept** — checks you hold the relationship's approver role, submits the
  draft counter-document, posts the clearing legs, stamps `Accepted By` /
  `Accepted On`, and sets the row to `Accepted`.
* **Reject** — prompts for a reason, deletes the draft counter-document (or
  cancels it if already submitted), and sets the row to `Rejected`.
* **Open counter-doc** — jumps straight to the linked document.
* **Bulk accept** — select rows in the list view, then *Actions > Bulk accept*.
  One failure does not abort the batch; each row reports its own result.

Both actions are idempotent — accepting an already-accepted row is a no-op.

### Cancelling

Cancel the source document and the app cascades: the counter-document is
cancelled (or deleted while still a draft), the Transaction row flips to
`Rejected`, and the reversal is logged as an Event.

---

## 4. Multicurrency

When the two companies use different currencies, the counter-document is created
**in the target company's currency** with `conversion_rate = 1.0`, and item rates
are converted at the looked-up FX rate. This avoids ERPNext v14's party-account
currency-mismatch validation.

Requirements:

* a **Currency Exchange** record for the direction you need — lookups are
  one-way and are never inverted, so seed `SAR → AED` *and* `AED → SAR`.
* a buying **Price List** whose currency matches the target company.

`FX Policy` controls the missing-rate behaviour: `Block Missing` throws;
otherwise posting continues.

> **Known defect in `14.0.1`:** in `Threshold-based` mode, documents **below**
> the threshold post the counter-document **without** applying the FX rate — a
> 900 SAR invoice becomes 900 AED instead of 881.37. `Auto` mode, above-threshold
> and `Manual` are unaffected. The Transaction row still records the correct rate, so
> the counter-document disagrees with its own audit trail. See `CHANGELOG.md`.

---

## 5. Reporting

* **Unmatched IC Balance** (*Intercompany > Reports*) — compares A's Due-from
  balance against B's Due-to balance per relationship and lists any variance
  over 0.01.
* **Workspace** — four KPI cards (Pending Inbox, Active Relationships,
  Auto-Posted Today, Failed Postings), shortcuts to each doctype and the report,
  and a recent-activity table of the last five ledger Transaction rows.

> The report reads submitted `GL Entry` rows. Because the clearing legs are left
> as drafts by design, it stays empty until those Journal Entries are submitted.



## 6. Troubleshooting

| Symptom | Cause |
|---|---|
| Nothing happens on submit | Customer has no `Represents Company`, or no relationship exists for the pair |
| `Failed / Mapping missing` in the log | No **Active** Document Mapping row for that source doctype |
| Submit blocked by an intercompany error | `On Error` is `Block` (the default) — set it to `Queue` or `Ignore` to let the source document through |
| `Internal Supplier for company X already exists` | ERPNext permits one internal supplier per represented company — extend the existing record's allowed companies |
| Party account currency errors | The target company has no buying Price List in its own currency |
| Counter-document amount not converted | The under-threshold FX defect above |
| Workspace looks empty | Hard-reload the browser (Ctrl+Shift+R); workspaces are cached client-side |

---

## License

MIT
