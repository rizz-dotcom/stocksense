# STOCKSENSE: laptop setup, demo script and daily use

This guide has six parts:

* Part 1: set up once on your Windows laptop (about 10 minutes)
* Part 2: switch on Gemini (optional)
* Part 3: get ready the day before
* Part 4: the 10-minute demo script, with the results you should see
* Part 5: using STOCKSENSE for real every day
* Part 6: if something goes wrong

## Part 1: Set up once (about 10 minutes, needs internet)

1. **Install Python.**
   * Go to https://www.python.org/downloads/ and install Python 3.12 or newer.
   * On the first installer screen, tick **"Add python.exe to PATH"**.
2. **Unzip the project.**
   * Unzip `stocksense.zip` to a simple path, for example `C:\stocksense`.
   * Avoid OneDrive folders if possible; syncing can lock the database file.
3. **Run the launcher.**
   * Open the `stocksense` folder and double-click `run_windows.bat`.
   * The first run creates `.venv` and installs packages. This takes a few minutes and happens only once.
4. **Read the setup check.** It prints lines like `[OK] Python 3.12...`.
   * `[--]` lines are optional, for example "Gemini key not set".
   * `[!!]` lines are real problems. Part 6 explains what to do.
5. **Create the administrator.**
   * Your browser opens http://localhost:8501 and shows **"Welcome to STOCKSENSE"**.
   * Create the administrator, for example username `owner` and a password of 10+ characters with letters and digits.
6. **Stopping and starting.**
   * To stop, close the black command window (or press Ctrl+C in it).
   * To start again later, double-click `run_windows.bat` again.

## Part 2: Switch on Gemini (optional)

1. In the `stocksense` folder, copy `.env.example` and rename the copy to `.env`.
   * In File Explorer, turn on View → Show → File name extensions so the name is exactly `.env`, not `.env.txt`.
2. Open `.env` in Notepad and replace `your-gemini-api-key-here` with your key:
   ```
   GEMINI_API_KEY=AQ.xxxxxxxxxxxxxxxx
   ```
   * Paste the whole key after the `=`, with no quotes or spaces.
   * New keys from Google AI Studio start with `AQ.`. That is the correct new format.
3. Save the file and restart the app (close the window, double-click the launcher).
4. In the app, go to **Settings → AI review** and click **Test Gemini connection**.
   * You should see "Connected to gemini-3.5-flash".
   * From a terminal you can also run `python scripts\check_setup.py --test-ai`.

**Keep the key private.** It lives only in `.env`, never in the app, a chat, an email or a screenshot. If it was ever shared, delete it in Google AI Studio and create a new one.

**If the test fails,** the app still works fully; only AI review is off. Read the message:

| Message | Fix |
|---|---|
| `AUTH_ERROR` | The key is wrong, deleted, or for a project without the Gemini API |
| `ACCESS_TOKEN_TYPE_UNSUPPORTED` | Create a fresh key in Google AI Studio and run `pip install -U google-genai` |
| `MODEL_ERROR` | Change `GEMINI_MODEL` in `.env` to a current model name |
| `RATE_LIMITED` | Wait a minute, or check your quota |

## Part 3: The day before your demo

1. **Rehearse once** with the demo script in Part 4.
2. **Reset to a clean demo.**
   * Close the app.
   * Open a terminal in the folder (click the address bar in File Explorer, type `cmd`, press Enter).
   * Run:
     ```
     .venv\Scripts\activate
     python scripts\fresh_demo.py
     ```
   * This keeps your old database as a dated backup file in `data\`. It then creates a fresh one with today's fictional demo data, so quotes are current and sales history ends yesterday.
3. **Create the demo users.** Start the app, create the administrator, then go to Settings → Users and create:
   * `manager1` (manager)
   * `cook1` (staff)
4. **Test Gemini** again (Settings → AI review), if you will show AI review.
5. **Prepare the screen.** Zoom the browser to 90% if tables look cramped, and turn off notifications.

## Part 4: The 10-minute demo script

Each step says what to click, what you should see, and what to say.

### Step 1: The problem (30 seconds)

**Say:** "Small restaurants run out of buns and patties on busy days, or over-order and waste money. STOCKSENSE watches stock, demand, suppliers and budget. A human approves every purchase."

### Step 2: Dashboard (1 minute)

**Click:** Dashboard (signed in as `owner`).

**You'll see:** 5 products tracked, about 4 needing attention, 3 critical, and a chart of stock cover versus delivery time.

**Say:**
* "Patties last about 1.1 days. Buns about 1.5 days."
* "Cheese lasts 2 days, but the fastest supplier also needs 2 days, so it runs out on delivery day."
* "In the chart, a short bar next to a black marker means we run out before a delivery can arrive."

### Step 3: Forecasts (1 minute)

**Click:** Forecasts, then open any product row's "Why" panel.

**Say:**
* "The demand estimate is an honest average of recorded sales, not magic. Every number shows its working: target equals demand plus safety stock, minus what we already have."
* Open "Calculation method and assumptions" to show the rules.

### Step 4: Suppliers (30 seconds)

**Click:** Suppliers → Product quotes.

**Say:**
* "Patties come from two fictional suppliers. One is cheaper but takes 4 days. The other costs more but delivers tomorrow."
* "One sauce supplier has zero stock."

### Step 5: Generate a proposal (2 minutes)

**Click:** Purchase proposals → Generate a new proposal. Keep budget 12000 and horizon 7, then click **Generate proposal**.

**You'll see:** a draft totalling **PKR 11,208** out of PKR 12,000.

**Say:**
* "It chose the 1-day patty supplier, not the cheaper 4-day one, because the cheaper one would deliver after we run out." Show the **Supplier alternatives** tab.
* "Cheese and fries didn't fit the budget. They're shown as **unfunded**, never silently dropped."
* Show the **Deterministic audit** tab: passed, plus warnings for a human to read.

**Click:** **Submit for approval**.

### Step 6: Role security (1 minute)

**Do:** Sign out, then sign in as `cook1` (staff). Open the Approval center.

**You'll see:** "Your role can review but not decide on proposals."

**Say:** "Staff can prepare purchases, but only a manager can approve. This is enforced in the business logic, not just by hiding a button."

### Step 7: The guardian moment (1.5 minutes)

**Do:**
* Sign in as `owner`. Go to Suppliers → Add or edit, pick Fictional QuickServe + Sauce packets, change the price from 2.50 to 2.75, and save.
* Sign in as `manager1`. Go to the Approval center, tick the confirmation box, choose Approve, and record the decision.

**You'll see:** "Approval blocked. The supplier price changed since the proposal was calculated." The status becomes BLOCKED.

**Say:** "At approval time it re-checks every price, quantity, supplier limit and the budget. It will not approve numbers that are no longer true."

**Then:**
* Go to Purchase proposals → open the proposal → "Revise quantities or suppliers".
* Type a note like "accept new sauce price" and click **Save as new version**. It becomes version 2, pending approval.
* As `manager1`, approve version 2.

### Step 8: AI review, if Gemini is set up (1 minute)

**Do:** Open the proposal → AI review tab → **Request AI review**.

**Say:**
* "Gemini reads the calculated proposal and raises risks and questions for the owner."
* "It is advisory only. It cannot approve, change quantities or prices, or place orders. If it ever contradicts our numbers, the app flags it."

If Gemini isn't set up, say: "AI is optional. Everything you've seen works without it."

### Step 9: Simulated orders and the audit trail (1 minute)

**Click:** Simulated orders.

**Say:** "Approval creates *simulated* purchase orders. No real supplier is contacted and no money is spent."

**Do:** Click **Record delivery received** to show stock going up.

**Click:** Audit log.

**Say:** "Every step is recorded: who did it, when, and why, including the blocked approval. The database itself refuses edits or deletions of this history."

### Step 10: Close (30 seconds)

**Say:** "Deterministic numbers you can check, AI as an advisor, a human in charge, and a full audit trail."

### Expected demo numbers

These are fictional, and the same on any day you seed:

| Product | Risk | Days of cover | What the proposal does |
|---|---|---|---|
| Chicken patties | High | 1.1 | 200 units from Fresh Foods (1-day), PKR 7,600 |
| Burger buns | High | 1.5 | 259 units from Fresh Foods, PKR 3,108 |
| Cheese slices | High | 2.0 (equals the lead time) | Unfunded: needs PKR 3,080 |
| Frozen fries | Medium | 4.7 | Unfunded: needs PKR 5,440 |
| Sauce packets | Low | 6.6 | 200 units (supplier minimum), PKR 500 |

The total is **PKR 11,208** of a PKR 12,000 budget.

## Part 5: Using STOCKSENSE for real

**Set up your business.**
* Settings → Business settings: your name, currency, planning horizon, default budget and risk thresholds.
* Add products under Inventory, either one by one or with the Import CSV tab. Download the example file there first.
* Add suppliers and a quote for each product they supply: price, minimum order, available quantity and delivery days.
* To mark the demo data as not needed, deactivate the demo products and suppliers. They're labelled "demo", and their history is kept.

**Daily (2 minutes).**
* Sales & demand → Record sales: enter yesterday's units sold for each product.
* You can also import a CSV from your till.
* Forecasts need at least 7 days of history to be reliable.

**Weekly.**
1. Update stock counts (Inventory → Edit) and refresh supplier quotes. Quotes older than 14 days are stale and block approval.
2. Generate a proposal and read the warnings.
3. Submit it; the manager approves.
4. When goods arrive, click **Record delivery received** on the simulated order.

**Roles.**

| Role | Can do |
|---|---|
| admin | Everything |
| manager | Approves purchases |
| staff | Prepares purchases, cannot approve |
| viewer | Read only |

**Backup.** Close the app and copy `data\stocksense.db` somewhere safe.

## Part 6: If something goes wrong

| Problem | What to do |
|---|---|
| Launcher says Python not found | Reinstall Python with "Add python.exe to PATH" ticked |
| Package installation fails | Check internet, then run `.venv\Scripts\python -m pip install -r requirements.txt` |
| Install fails on a very new Python | Install Python 3.12 and delete the `.venv` folder, then rerun the launcher |
| Browser doesn't open | Open http://localhost:8501 yourself |
| "Port 8501 is in use" | Close the other STOCKSENSE window, or run `streamlit run app.py --server.port 8502` |
| "The database is busy or locked" | Close other programs that opened the .db file, and avoid OneDrive folders |
| Approval blocked: quote stale | Suppliers → re-save the quote, then revise the proposal |
| Forgot the admin password | Another admin resets it in Settings → Users; for a demo laptop, run `scripts\fresh_demo.py` |
| Something else | Look at `data\stocksense.log` (keys and passwords are removed from it) |

To run the automated tests on your laptop (about 15 seconds):

```
.venv\Scripts\activate
python -m pytest
```
