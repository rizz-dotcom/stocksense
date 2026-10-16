# STOCKSENSE: The Autonomous Business Guardian

> **Presenting a demo?** Start with [DEMO_GUIDE.md](DEMO_GUIDE.md). It covers laptop setup, a 10-minute demo script with expected results, and daily use.
>
> **Hosting it online?** Follow [DEPLOY_CLOUD.md](DEPLOY_CLOUD.md) (Alternate Clouds, "GitHub Repo" option).

STOCKSENSE is an AI-assisted inventory, procurement and business-risk system for a small fast-food restaurant. It is a hackathon MVP built with production-conscious engineering.

It answers the owner's daily questions:

* Which products will run out soon?
* How much should we buy for the planning period?
* Which supplier fits on price, delivery time, minimum order and availability?
* Does the purchase fit the budget?
* What risks need attention?

A human approves, rejects or requests changes to every purchase, and every step is written to an audit trail.

> **Simulated orders only.** STOCKSENSE never contacts suppliers, places real orders, spends money or changes any external system. Every purchase order has `demo_only = true` and `real_supplier_order_placed = false`, and the database refuses any other value.

## Features

| Area | What it does |
|---|---|
| Dashboard | Risks and decisions first: products at risk, stockouts before delivery, pending approvals, budget use, open simulated orders, recent activity |
| Inventory | Add, edit, deactivate, search; safety stock, reorder threshold, expiry; CSV import and export |
| Suppliers | Supplier directory and product quotes (price, MOQ, availability, lead time, quote date); stale-quote flags |
| Sales and demand | Record daily sales, audited corrections, CSV import, history chart, daily-demand baseline |
| Forecasts | Horizon slider, risk rating with plain-language reasons, full assumptions |
| Purchase proposals | Deterministic generation, supplier alternatives, budget allocation, versioned revisions |
| Approval center | Re-runs the full audit, optional AI review, approve, reject or request revision |
| Simulated orders | Created only after a valid human approval; "record delivery received" updates STOCKSENSE stock |
| Audit log | Append-only, filterable, exportable |
| Settings | Business settings, AI status, users and roles, demo data, backup guidance |

## Architecture

```text
app.py                      Streamlit entry point: login gate and navigation
stocksense/
  config.py                 .env / environment configuration; persisted-setting defaults
  database.py               Engine, transactional sessions, non-destructive init, append-only triggers
  models.py                 SQLAlchemy ORM (money in integer minor units)
  schemas.py                Pydantic validation for forms, CSV rows, settings, AI output
  exceptions.py             Typed domain errors with safe user messages
  logging_setup.py          Logging with secret redaction
  security/
    authentication.py       scrypt password hashing, lockout, first-admin flow, user admin
    authorization.py        Roles and permissions (checked in services, not just the UI)
  services/                 ALL business rules live here (no Streamlit imports)
    forecast_service.py     Demand estimate, replenishment, risk classification
    supplier_service.py     Supplier/quote CRUD and deterministic supplier selection
    validation_service.py   Deterministic proposal audit (recomputes every cost)
    proposal_service.py     Generation, versioned revision, submission
    approval_service.py     State machine, atomic decisions, idempotency
    purchase_order_service.py  Simulated purchase orders
    ai_service.py           Optional Gemini review (advisory only)
    import_service.py       Whole-file CSV validation, all-or-nothing commit
    audit_service.py        Append-only audit trail
    inventory_service.py, sales_service.py, settings_service.py,
    demo_service.py, dashboard_service.py
  ui/                       Streamlit pages (presentation only)
  utils/                    money.py (exact Decimal), dates.py, csv_utils.py
scripts/  seed_demo.py, create_admin.py, check_setup.py (pre-flight check), fresh_demo.py
sample_data/  inventory.csv, suppliers.csv, sales.csv
tests/    pytest suite (temporary databases, Gemini mocked)
```

The UI never contains business rules. Services take a `Database` and a `UserContext`, check permissions, and manage their own transactions. That makes every rule testable without Streamlit.

**Additions beyond the suggested structure** (each one is essential): `logging_setup.py`, plus several services and UI modules:

* `services/settings_service.py`, `import_service.py`, `demo_service.py`, `dashboard_service.py`
* `ui/common.py`, `auth_ui.py`, `importer.py`, `proposal_view.py`
* `.streamlit/config.toml`, which binds to localhost and sets the theme

## Requirements

* Windows 10/11 (macOS and Linux also work)
* Python 3.11 or newer. Tested on Python 3.12.
* Internet access once, to install packages
* Optional: a Google Gemini API key for AI review

Dependencies are listed in `requirements.txt`. These versions were verified together:

* streamlit 1.66
* SQLAlchemy 2.1
* pydantic 2.14
* google-genai 2.31
* pandas 3.0
* plotly 7.1
* python-dotenv 1.2
* pytest 9.1

No substitutions were made to the requested stack.

## Windows installation

### Easiest: the launcher

Double-click `run_windows.bat` (or run it from Command Prompt in the project folder). On first run it does three things:

1. Creates `.venv`.
2. Installs the requirements once.
3. Starts the app at http://localhost:8501.

Later runs start immediately. The launcher checks for Python 3.11+, runs `scripts\check_setup.py` before starting, and never loops or reinstalls repeatedly.

To check a laptop without starting the app, run `python scripts\check_setup.py`. Add `--test-ai` to also make one tiny Gemini call.

### Manual setup (Command Prompt)

```bat
cd path\to\stocksense
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

In PowerShell, activate with `.venv\Scripts\Activate.ps1`. If scripts are blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### First administrator (there is no default password)

Choose one of these:

* **In the browser:** start the app. When no account exists, it shows a "Create administrator" screen.
* **From the command line:** `python scripts\create_admin.py`

Passwords need at least 10 characters with letters and digits, and must not contain the username. Five wrong attempts lock an account for 15 minutes.

### Demo data (optional, fictional)

Choose one of these:

* Run `python scripts\seed_demo.py`.
* Use Settings → Demo data and backup → "Load demo data" (admin only).

This loads **Demo Burger House** with:

* 5 products: buns, chicken patties, sauce packets, cheese slices, fries
* 3 fictional suppliers with different prices, MOQs, availability and lead times
* 7 days of sales ending yesterday

Use the fictional PKR 12,000 budget when generating a proposal. Seeding is idempotent and never deletes or overwrites your records.

The demo deliberately includes these situations:

* A cheaper patty supplier whose 4-day lead time would cause a stockout. The system does not choose it.
* A supplier with zero sauce availability.
* Items that do not fit the budget. They are shown as unfunded, never silently dropped.

Demo sales and quote dates are relative to the day you seed. After about 2 weeks, quotes become stale (the default limit is 14 days). Refresh them on the Suppliers page, or seed into a fresh database.

### Run

```bat
.venv\Scripts\activate
streamlit run app.py
```

## Configuring Gemini securely (optional)

1. Copy the example file: `copy .env.example .env`
2. Edit `.env` and set `GEMINI_API_KEY=` to your key from Google AI Studio.
   * New keys start with `AQ.` (Google's new default auth keys); older keys start with `AIza`. Both are supported.
   * The app never checks the key's format, because Google owns that format.
3. Optionally set `GEMINI_MODEL`. The default, `gemini-3.5-flash`, is listed by Google as a stable model in October 2026. Check Google's model page if it is retired later.
4. Restart the app, then click **Settings → AI review → Test Gemini connection**.

Rules for the key:

* Put the key only in `.env`.
* Never paste it into the app, chat, source code or a commit. `.env` is git-ignored.
* The key is never stored in SQLite, shown in the UI, or written to logs (log redaction removes key-shaped strings).

**Without a key, everything except AI review works.** The proposal page says AI review is unavailable and never pretends a review happened.

What the AI does:

* Receives the already-calculated proposal and returns structured JSON: summary, overall risk, warnings, questions for the owner, questionable assumptions and missing evidence.
* Its output is schema-validated and treated as untrusted.
* It cannot approve, change quantities, prices or budgets, or place orders. No such code path exists.
* If its stated total contradicts the calculated total, or it tries to supply a "decision", the review is flagged and the calculated figures stay authoritative.

When it runs:

* Only when a user presses the review button, never on page reruns.
* Each review is stored with the proposal version it examined, so a review of version 1 is never shown as a review of version 2.

How errors are handled:

* Invalid key, unknown model and quota exhaustion are reported and not retried.
* Timeouts, rate limits and server errors get up to 3 attempts with backoff.
* Blocked content, empty responses and malformed JSON are reported and discarded.

## How the calculations work

All of this is deterministic and AI-independent. See `forecast_service.py` and `supplier_service.py` docstrings for the full specification.

### Demand estimate

* **Average daily demand:** the mean of recorded daily sales in the look-back window (default 28 days). With 7 or more recorded days, the most recent 7 are used.
* Days with no record count as **unknown**, not zero.
* This is a simple average, not machine learning, and the app labels it that way.

### Data quality

| History | Label |
|---|---|
| 0 days | `NO_HISTORY` (only safety stock is suggested) |
| 1-2 days | `WEAK` |
| 3-6 days | `LIMITED` |
| 7+ days | `OK` |

Invalid observations (negative, fractional, NaN, infinite, non-numeric) are ignored and reported.

### Replenishment formulas

* **Forecast demand** = ceil(average × horizon)
* **Usable stock** = on-hand stock, or 0 when the batch has expired
* **Effective available stock** = usable stock + units on *open simulated* purchase orders. You can switch this off in Settings.
* No double counting: when an order is marked received, its units move into on-hand stock and stop counting as on-order.
* **Target stock** = forecast demand + safety stock
* **Suggested order** = max(0, target − effective available)

### Stockout before delivery

Days of cover (on-hand only) ≤ fastest valid supplier lead time. If the two are equal, that counts as a risk.

### Risk levels

Thresholds are configurable.

**High** when any of these is true:
* no usable stock
* a stockout before delivery is likely
* cover is at or below the high threshold (default 2 days)
* expired stock is on hand

**Medium** when any of these is true:
* stock is at or below safety stock or the reorder threshold
* cover is at or below the medium threshold (default 5 days)
* stock is near expiry with likely waste
* there is no valid supplier quote

**Low** otherwise.

### Supplier selection

A quote is eligible only if all of these hold:
* the supplier is active
* price > 0 and MOQ ≥ 1
* the quote is not stale
* availability > 0 and covers the order
* there are no conflicting duplicate quotes

The order quantity is max(need, MOQ).

Ranking:
* **Urgent (high-risk) products:** not-late first, then lead time, then cost.
* **Other products:** not-late first, then cost, then lead time.
* A late supplier is never chosen over an on-time one, even if cheaper. If all suppliers are late, the line is flagged for a human.

### Budget allocation

* Lines are funded in risk order (most urgent first) while they fit.
* Lines that don't fit are kept and **disclosed as unfunded**.
* A cheaper, lower-priority line may still be funded if it fits the remaining budget.

### Money

* Stored as integer minor units (paisa).
* Parsed with `Decimal` and rounded half-up to 2 decimals once, at input time.
* Binary floats are never authoritative.

## Proposal workflow and state machine

```text
DRAFT ──submit──▶ PENDING_APPROVAL ──approve──▶ APPROVED (creates simulated POs)
  │                  │   │   └──reject──────▶ REJECTED
  │                  │   └──request revision▶ NEEDS_REVISION ──revise──▶ PENDING_APPROVAL / BLOCKED
  └─(fails audit)─▶ BLOCKED ◀──(audit fails at approval time)
                     └──revise──▶ PENDING_APPROVAL (valid) or BLOCKED (still invalid)
```

What the approval service enforces (not just the UI):

* **Who:** an authenticated user with the `approve_proposals` permission (manager or admin).
* **Version:** the submitted version must be the latest. Older versions are refused as *stale*.
* **Re-validation:** at approval time the system re-checks every quantity, MOQ, availability, price (it must still match the stored price), quote freshness, supplier status, currency, line cost and budget.
* **Atomicity:** the decision, status change, simulated purchase orders and audit events commit in one transaction. If any part fails, including the audit write, nothing changes.
* **Duplicates are stopped three ways:**
  * a unique database constraint on (proposal, version)
  * optimistic locking on the proposal row
  * a service-level check
* **Edits restart approval:** editing creates a new version, recalculates everything and returns to `PENDING_APPROVAL` (or `BLOCKED`). Earlier versions are preserved.

## Roles

| Role | Can do |
|---|---|
| admin | Everything, including users and settings |
| manager | Operations, approve proposals, view the audit log |
| staff | Inventory, suppliers, sales, forecasts, draft and revise proposals, receive deliveries. Cannot approve. |
| viewer | Read-only |

## CSV import

Templates are in `sample_data/` and can be downloaded on each import tab. Column names must match **exactly**. Extra or missing columns are rejected.

| File | Columns |
|---|---|
| inventory | `sku,name,category,unit,stock_qty,safety_stock,reorder_threshold,expiry_date` |
| suppliers | `supplier_code,supplier_name,contact_name,phone,email,sku,unit_price,min_order_qty,available_qty,lead_time_days,quoted_at` |
| sales | `sale_date,sku,quantity` |

Format rules:

* **Dates:** `YYYY-MM-DD`, `YYYY/MM/DD` or `05-Oct-2026`. Ambiguous `05/10/2026` is rejected on purpose.
* **Decimals:** use a dot (`12.50`). Thousands separators like `1,250.50` are accepted. A decimal comma like `12,5` is rejected.
* **Quantities:** whole units, 0 or more.
* **Encoding:** UTF-8 (Unicode names are fine).
* **Size:** default limit 2 MB.

How an import works:

* Upload → preview (accepted rows, rejected rows with reasons, duplicates, updates to existing records) → explicit import button.
* **All-or-nothing:** if any row is invalid, nothing is written. Partial import is deliberately not supported.
* Re-importing identical data changes nothing.
* Re-importing a sales day with a different quantity is an audited correction.
* Text starting with `= + - @` is rejected on import.
* Exports (inventory, proposals, orders, audit) prefix such cells with `'` to prevent formula injection, and include a header line noting demo/simulated status.

## Database, backup and restore

* **Location:** `data\stocksense.db`. Override with `STOCKSENSE_DB_PATH` in `.env`.
* **Log file:** `data\stocksense.log` (secrets redacted).
* **Startup is safe:** it creates missing tables only and never deletes data.
* **Backup:** stop the app, then copy `stocksense.db` and any `stocksense.db-wal` / `stocksense.db-shm` files next to it.
* **Restore:** stop the app, copy the files back, then start the app.
* **Exports:** CSV exports are for reporting. Use the database file for a full backup.
* **Append-only history:** audit events and approval decisions are protected by SQLite triggers that refuse UPDATE and DELETE.

## Running the tests

```bat
.venv\Scripts\activate
python -m pytest
```

Tests use temporary databases and never call the real Gemini service, so no API key or paid call is needed. Final run: **184 passed, 0 failed**.

How the test suite covers the hard-to-reach parts:

* `tests/test_gemini_sdk_path.py` runs the **real google-genai SDK** against a simulated Gemini server. It checks:
  * the exact HTTP request (endpoint, `x-goog-api-key` header with an `AQ.` key, JSON schema)
  * Google's real error formats, including `ACCESS_TOKEN_TYPE_UNSUPPORTED`
* `tests/test_ui.py` drives the **real Streamlit app** headlessly:
  * first-run admin creation and sign-in
  * every page
  * generate → submit → approve → receive
  * CSV upload, both valid and invalid
  * role restrictions
* `test_demo_guide_script_numbers_and_guardian_moment` replays the presenter script in DEMO_GUIDE.md, including the expected totals.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `'py' is not recognized` / `python not found` | Install Python 3.11+ from python.org with "Add python.exe to PATH" ticked |
| "A required package is missing" | `.venv\Scripts\activate` then `pip install -r requirements.txt` |
| "The database is busy or locked" | Close other programs using the .db file (e.g. DB Browser); retry |
| Port 8501 in use | `streamlit run app.py --server.port 8502` |
| AI review unavailable | Set `GEMINI_API_KEY` in `.env`, ensure AI review is on in Settings, restart |
| "configured Gemini model is not available" | Update `GEMINI_MODEL` to a current model ID |
| Approval blocked: quote stale | Refresh the quote on the Suppliers page, then revise the proposal |
| Approval blocked: price changed | Revise the proposal to accept the new price (creates a new version) |
| Forgot admin password | Another admin can reset it in Settings → Users. If none exists, back up the database and use a fresh one. |

## Security notes and limitations

What is in place:

* Passwords are hashed with scrypt (Python standard library), with per-user salt and constant-time comparison.
* Roles are enforced in the service layer.
* Sessions expire after inactivity (default 30 minutes).
* All database access goes through the SQLAlchemy ORM with parameterised queries.
* Imports are validated for size, type, schema and content.
* Tracebacks, password hashes and keys never appear in the UI (`showErrorDetails = false`).

Honest limitations:

* **Local sign-in only.** This is a local-use login, not hardened internet authentication. Streamlit keeps session state in server memory per browser tab.
* **Localhost by default.** `.streamlit/config.toml` binds to `localhost`. Do not expose the app on a network or the internet without all of the following:
  * HTTPS termination (a reverse proxy)
  * a real identity provider or network access control
  * secret management
  * backups and monitoring
* **First-run screen.** The "create administrator" screen is shown only while no user exists. Create the admin before making the app reachable by anyone else.
* **No load testing.** SQLite with WAL suits one small business on one machine. It has not been load-tested for many concurrent users.
* **Cloud hosting.** The `Dockerfile` is built for hosts like Alternate Clouds (see DEPLOY_CLOUD.md):
  * It reads the port from `$PORT`, has a health check, and stores data in `/app/data`.
  * It switches off the public "create administrator" screen (`STOCKSENSE_ALLOW_WEB_SETUP=false`).
  * It creates the first admin from the `STOCKSENSE_ADMIN_USERNAME` / `STOCKSENSE_ADMIN_PASSWORD` secrets, and loads demo data when `STOCKSENSE_LOAD_DEMO=true`.
  * The image itself was not built in the development sandbox (no Docker there). Its start command, health check and start-up setup were run and tested directly.
