# Hosting STOCKSENSE on Alternate Clouds (GitHub Repo option)

Do one step at a time. Move on only when the **Checkpoint** matches.

Alternate Clouds is in beta, so its screens may use slightly different words than this guide. If a screen asks for something not covered here, take a screenshot and ask.

## What you need first

* A **GitHub account** (free): https://github.com
* **GitHub Desktop** (free, no command line): https://desktop.github.com
* Access to the Alternate Clouds organisation you were given
* A **new** Gemini API key from Google AI Studio. Do not reuse a key that was ever pasted into a chat or email.
* A strong admin password you will use on the hosted site. It needs 10+ characters with letters and digits, and must not contain the username.

## Step 1: Put the project on GitHub (private)

1. Open GitHub Desktop and sign in.
2. Choose **File → Add local repository** and select your `C:\stocksense` folder.
3. If it says this is not a repository, click **create a repository** there, then **Create repository**.
4. Click **Publish repository**. **Keep "Keep this code private" ticked.**

The project's `.gitignore` already keeps your `.env` file (with the key) and your database off GitHub.

**Checkpoint:**
* On github.com, the repository exists and shows **Private**.
* It contains `Dockerfile`, `app.py` and the `stocksense` folder.
* It does **not** contain a `.env` file or anything inside `data`.

## Step 2: Create the deployment

1. In Alternate Clouds, click **New → GitHub Repo**.
2. Connect your GitHub account when asked, and allow access to the STOCKSENSE repository.
3. Choose the repository and the `main` branch.
4. If it asks how to build, choose **Dockerfile**. The file is at the root of the project.
5. If it asks for a **port**, enter `8501`. That is the port inside the container; the public site uses HTTPS.

**Checkpoint:** the deployment form shows your repository, Dockerfile build and port 8501. Don't press deploy yet.

## Step 3: Add the settings (environment variables / secrets)

Add these in the deployment's **Environment variables** (or **Secrets**) section. Use the secret/hidden option for the key and the password if there is one.

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | your new Gemini key (optional; without it, everything except AI review works) |
| `STOCKSENSE_ADMIN_USERNAME` | e.g. `owner` |
| `STOCKSENSE_ADMIN_PASSWORD` | your strong admin password |
| `STOCKSENSE_LOAD_DEMO` | `true` to load the fictional Demo Burger House data |

These are already set inside the Dockerfile; leave them out:
* `STOCKSENSE_ALLOW_WEB_SETUP=false` hides the public "create administrator" screen so a stranger can't create the first admin.
* The database location, `/app/data`.

**If the platform offers persistent storage (a "volume" or "disk"),** add one of 1 GB mounted at `/app/data`. That keeps your data when the app restarts.

**If it doesn't offer storage,** the app still works. A restart, though, wipes anything you added. It then recreates the admin and the demo data automatically, which is fine for a demo, not for real business use.

**Checkpoint:** the variables are listed, with the password and key shown hidden.

## Step 4: Deploy

Click **Deploy**. The first build takes a few minutes, mostly installing Python packages.

**Checkpoint:**
* The build log ends with a line like `Uvicorn server started on 0.0.0.0:8501`.
* The deployment shows as running or healthy.
* You get a public address starting with `https://`.

## Step 5: Open the site and sign in

1. Open the `https://` address.
2. Sign in with the admin username and password from Step 3.

**Checkpoint:**
* The dashboard shows "Demo Burger House" with 5 products.
* The browser shows a padlock, which means HTTPS is working.

If Gemini is set, go to **Settings → AI review → Test Gemini connection**. **Checkpoint:** it says "Connected".

## Step 6: Before you share the link

* Under **Settings → Users**, create separate accounts for teammates, for example `manager1` (manager) and `cook1` (staff). Don't share the admin password.
* Share the link only with the people who need it. This is a demo login, not a hardened public system.
* Check credits in **Billing**. The trial gives limited credit; stop or delete the deployment when you no longer need it.

## Updating the site later

1. Change the files on your laptop.
2. In GitHub Desktop, write a short summary, click **Commit to main**, then **Push origin**.
3. In Alternate Clouds, redeploy (or it redeploys automatically if that is switched on).

**Checkpoint:** the site shows your change.

## If something goes wrong

| What you see | What to do |
|---|---|
| Build fails while installing packages | Copy the last 30 lines of the build log and ask for help |
| "STOCKSENSE is not set up yet" | `STOCKSENSE_ADMIN_USERNAME` / `STOCKSENSE_ADMIN_PASSWORD` are missing or the password is too weak (the page says which). Fix them and redeploy. |
| The page stays on "Please wait..." or keeps reconnecting | The platform's proxy must allow WebSocket connections, which Streamlit needs. Ask the platform owner or support. |
| Page not found / 502 | Check the port is `8501` and the deployment is running |
| Data disappears after a restart | No persistent storage at `/app/data`. Add a volume, or accept it for a demo. |
| AI test says `AUTH_ERROR` | Check `GEMINI_API_KEY` has no spaces or quotes; create a fresh key if needed |

## Security summary

* The Gemini key and admin password live only in the platform's secret settings, never in GitHub.
* Keep the repository private.
* The public "create administrator" screen is switched off on the hosted version.
* All purchase orders remain simulated. Nothing is sent to real suppliers.
