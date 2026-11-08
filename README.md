# RHD e-Registry

A web system that links the **Regional Health Directorate**, **District Health Directorates** and **District Hospitals**. Staff use it to send, route, minute and track letters and communiqués, with a complete audit trail.

- **Built with:** Django 6.1 (Python 3.12+), with MySQL/MariaDB in production and SQLite for development.
- **Live address:** https://e-registry.rhduer.online.
- **Installing or updating on the cPanel hosting:** follow [DEPLOY.md](DEPLOY.md).

## How it is organised

| Part | Where | What |
|---|---|---|
| Browser app | `public/` | Sign-in, dashboard, letters, administration, dark/light themes. Plain HTML/JS/CSS that calls the JSON API. |
| Django project | `eregistry/` | Settings, URLs, WSGI. `envconfig.py` reads the configuration (see below). |
| Registry app | `registry/` | Models, workflow rules, JSON API (`api.py`), SMS (`sms.py`), reminders, the web set-up page, Django admin. |
| Hosting entry point | `passenger_wsgi.py` | Loaded by cPanel's Setup Python App. |

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows (Linux/macOS: .venv/bin/pip)
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py load_sample_data      # optional: test officers (password test1234)
.venv/Scripts/python manage.py runserver             # http://127.0.0.1:8000
```

`runserver` uses SQLite in `data/eregistry.sqlite3`. On first start the only account is **admin** / **admin1234**, and it must be changed at first sign-in.

To run the tests: `.venv/Scripts/python manage.py test registry` (34 tests).

To build the upload package: `.venv/Scripts/python scripts/package.py`. This creates `dist/rhd-e-registry-<version>.zip`, which includes Django and PyMySQL in `vendor/`.

## Configuration

Settings are read from, highest priority first:
1. Environment variables, e.g. cPanel → Setup Python App → Environment variables.
2. A `.env` file in the app folder (copy [.env.example](.env.example)).
3. `settings.env` in the data folder, written by the **web set-up page**.

| Variable | Default | Purpose |
|---|---|---|
| `DB_CLIENT`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | — | MySQL connection (`DB_CLIENT=mysql`). Usually entered on the set-up page instead. |
| `SETUP_KEY` | generated | Your own key for the set-up page (at least 8 characters). |
| `APP_URL` | — | Public address, used for links in SMS alerts. |
| `DATA_DIR` | `~/rhd-e-registry-data` (hosting), `./data` (development) | Uploaded scans, `settings.env`, the generated secret key. |
| `COOKIE_SECURE` | — | `1` when served over HTTPS. |
| `ALLOWED_HOSTS` | `*` | Comma-separated host names, e.g. `e-registry.rhduer.online`. |
| `SECRET_KEY` | generated | Django secret key. If unset, one is created in the data folder. |
| `LETSFISH_APP_ID`, `LETSFISH_APP_SECRET` | — | Fish Africa SMS credentials. These override the values saved in Administration. |
| `SMS_WORKER` | `1` | `0` stops this copy of the app from sending SMS. |

**On hosting,** the site shows a **set-up page** until MySQL is connected. It never falls back to a local SQLite file.

## How letters move

Every letter has one **current desk** (the officer holding it), and only that officer can act on it. At each step the system suggests the next desk on the standard route.

**Region to district or hospital:**
1. The **RDHS** signs the letter.
2. The **RDHS Secretary** logs it and assigns a reference, e.g. `RHD/26/0001`.
3. The **Manager, Registry Unit** dispatches it.
4. The **DHD HR/Admin Manager** acknowledges receipt.
5. The **DDHS** minutes it to an officer, who acts on it and closes it.

**District or hospital to region:**
1. The **DDHS** or Hospital Administrator signs it.
2. Their **Secretary** dispatches it.
3. The **Registry Unit** receives it.
4. The **RDHS Secretary** presents it to the **RDHS**.
5. The RDHS minutes it to the DDHR, DDCC, DDPH, DDF or DDA.

**Statuses:** Drafted → Dispatched → Received → Minuted → Actioned → Closed.

**Incoming paper letters** are recorded at the registry with an `…/IN/…` reference.

## Features

- **Reference numbers and scans.** Letters get automatic reference numbers and can carry scanned copies (PDF, images, Word or Excel, up to 10 MB each).
- **Workflow actions.** Officers can forward, dispatch, acknowledge, minute, record action, close and reply.
- **Locked audit trail.** It can't be edited or deleted, which is enforced by the model and by database triggers.
- **Alerts.** Officers get in-app notifications, plus **SMS via Fish Africa (LetsFish)** when a letter needs them and a weekday reminder for overdue letters.
- **Search, export and dashboards.** Letters can be searched and exported to Excel (CSV). Dashboards show status, where letters are sitting, and overdue officers.
- **Role-based access.**
  - The RDHS, RDHS Secretary and Registry Unit see all letters.
  - Heads, HR/Admin Managers and secretaries see their own facility's letters.
  - Other officers see only the letters they handle.
- **Administration.** Officers, facilities, settings and SMS are managed in the app. **Django admin** at `/django-admin/` gives system administrators direct access to the data.

## SMS alerts

These are set up in **Administration → SMS alerts**. The steps, retry rules and delivery reports are described in [DEPLOY.md](DEPLOY.md).

On shared hosting, add the cron command shown on that page, so SMS sending and reminders keep running while the site is idle.
