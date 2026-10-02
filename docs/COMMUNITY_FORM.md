# Community job submissions: setup

Visitors submit jobs through a Google Form, with no sign-in or GitHub account needed. Every hour,
`.github/workflows/community-intake.yml` reads the form's response sheet (published as CSV) and
checks each new row. Passing rows go live with a **community-added** tag.

Checks on each row:
- Required fields are filled in.
- The URL is http(s) and loads. Bot-blocking 403/429 responses from known job boards are accepted with a note.
- The job passes the `relevance.py` EECS filter.
- It isn't a duplicate of a scraped listing or an earlier submission (same normalized URL, or same title and institution).
- Spam guards: junk or spam keywords are rejected, at most 20 rows are processed per run, and at most 25 jobs can be active per posting host.

Rejected rows are listed in the run summary and in `community_rejections.log`. The log holds a row id, the form timestamp, and the reason only. Submitted text and URLs are not written to the repo.

Community jobs expire on their application deadline, or 90 days after submission if there is none. If the scraper later finds the same job, the scraped listing is shown instead, without the tag.

## 1. Create the Google Form

1. Go to <https://forms.google.com> and click **Blank form**. Title: `Submit an EECS faculty job`.
   Description (suggested): *"Know of an EE / CS / Computer Engineering faculty or teaching opening? Share it here. It appears on the EECS Faculty Job Finder within about an hour if it passes automatic checks. Please don't include personal information. Responses are public."*
2. Add these questions **in this order, with exactly these titles**. The parser matches on how each title starts, case-insensitively, so you can add text after a title, like "(optional)", but don't rename the start of it.

   | # | Question title | Type | Required | Settings |
   |---|---|---|---|---|
   | 1 | `Job title` | Short answer | Yes | |
   | 2 | `Institution` | Short answer | Yes | |
   | 3 | `Department` | Short answer | No | |
   | 4 | `Posting URL` | Short answer | Yes | ⋮ → **Response validation** → *Text* → *URL* |
   | 5 | `Location / remote` | Short answer | No | Help text: `City, State, or "Remote"` |
   | 6 | `Application deadline` | **Date** | No | ⋮ → make sure "Include year" is on |
   | 7 | `Field / area` | Dropdown | Yes | Options (one per line): `Computer Science`, `Electrical Engineering`, `Computer Engineering`, `Artificial Intelligence / Machine Learning`, `Data Science`, `Cybersecurity`, `Robotics`, `Semiconductors / Electronics`, `Information Technology`, `Other EECS-related` |
   | 8 | `Notes` | Paragraph | No | Help text: `Optional: rank, remote/on-campus, anything else useful` |

3. Click **Settings** (the tab at the top of the form):
   - **Responses → Collect email addresses: _Do not collect_.**
   - **Responses → Limit to 1 response: _Off_.** Turning this on forces sign-in.
   - **Responses → Allow response editing: _Off_.**
   - If you're using an Oregon State (Google Workspace) account, go to **Responses** (or **General**) and turn **off "Restrict to users in Oregon State University and its trusted organizations"**. Otherwise outside visitors will be asked to sign in. If your admin won't let you turn this off, create the form from a personal Gmail account instead.
   - **Presentation → Confirmation message** (suggested): `Thanks! If it passes automatic checks it will appear on the site within about an hour.`
4. Click **Send → the link (🔗) tab → Shorten URL → Copy**. This is the public form link for section 4 below.
5. Test it in a private or incognito browser window. It should open without asking you to sign in.

## 2. Publish the response sheet as CSV

1. In the form, open the **Responses** tab and click **Link to Sheets → Create a new spreadsheet → Create**.
2. In the spreadsheet, go to **File → Settings → Locale: _United States_** and click **Save**. This keeps dates and timestamps in M/D/YYYY, which the parser expects.
3. Go to **File → Share → Publish to web**:
   - In the first dropdown, choose the **`Form Responses 1`** sheet, not "Entire document".
   - In the second dropdown, choose **Comma-separated values (.csv)**.
   - Expand **Published content & settings** and make sure **"Automatically republish when changes are made"** is checked.
   - Click **Publish → OK**, then copy the URL. It looks like
     `https://docs.google.com/spreadsheets/d/e/2PACX-…/pub?gid=…&single=true&output=csv`.
4. Optional check: open that URL in a private window. You should download a CSV whose first row is
   `Timestamp,Job title,Institution,Department,Posting URL,Location / remote,Application deadline,Field / area,Notes`.
   Google can take up to about 5 minutes to republish after a new response.

> Privacy: anyone with that CSV link can read all responses, and the form doesn't collect emails. Keep the link in a GitHub **secret** (below) so it isn't printed on the site or in the repo.

## 3. Add the CSV URL to GitHub

1. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `COMMUNITY_FORM_CSV_URL`. Value: the published CSV URL from section 2.
   (A repository *variable* with the same name also works, but a secret keeps the link hidden in logs.)
2. Workflow permissions: no change is needed. The repo default can stay at *Read*, because `community-intake.yml` and `refresh.yml` each request `contents: write` themselves.
3. Go to **Actions → "Community job intake" → Run workflow** to test it now. After that it runs every hour at :41.
   The run summary lists every added and rejected row.

## 4. Turn on the site button

Edit `site-config.js` and set the form link from step 1.4:

```js
window.SITE_CONFIG = { submitFormUrl: "https://forms.gle/XXXXXXXXXXXX" };
```

Commit the change. Once GitHub Pages redeploys, the header shows **Submit a job** instead of "Submit a job (coming soon)".

## Operating notes
- **Removing a bad listing:** delete its entry from `community_jobs.json`, then run `python3 community.py merge` (or wait for the next run) and commit. Its row id is already marked processed in `community_processed.json`, so it won't come back.
- **Editing a response in the sheet:** this changes its row id, so the row is treated as new. Duplicate detection stops a second copy of a job that's already listed.
- **Don't rename the question titles.** The CSV headers come from them. You can add questions; extra columns are ignored.
- If a posting URL is temporarily unreachable (timeout or DNS), the row is retried on the next 2 runs before it's rejected.
- Dry run on a downloaded CSV: `python3 community.py intake --csv-file responses.csv`. This modifies the data files, so use a scratch copy.
