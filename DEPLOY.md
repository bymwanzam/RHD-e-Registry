# Installing RHD e-Registry (Django) at https://e-registry.rhduer.online

This guide installs the Django version of the e-Registry on the Namecheap shared hosting for **rhduer.online**, using cPanel's **Setup Python App** and a **MySQL database**. It takes about 30 minutes.

**You need:**
- The release file `rhd-e-registry-2.0.0.zip`. It includes Django and the MySQL driver, so no "pip install" is needed.
- Your cPanel login.
- **Python 3.12 or newer** in cPanel → Setup Python App, because Django 6 requires it. Check the **Python version** list before you start. If 3.12 isn't offered, ask Namecheap support to enable it.

In the steps below, `machdzpp` is your cPanel username.

---

## 1. Remove the old Node.js app

The Node.js version currently runs on `e-registry.rhduer.online`, and an address can only have one app.

1. In cPanel, open **Setup Node.js App**.
2. On the `e-registry.rhduer.online` app, click **STOP APP**, then the **trash-can (Destroy)** icon.
3. Optional: in File Manager, delete the old folders `rhd-e-registry` and `rhd-e-registry-data`. The new version starts with a fresh database.

## 2. Create the MySQL database

You can skip this if you already created one for the Node.js version: the same database and user work.

1. In cPanel, open **MySQL® Databases**.
2. Under **Create New Database**, type `eregistry` and click **Create Database**. The full name is `machdzpp_eregistry`.
3. Under **MySQL Users → Add New User**, create a user such as `eregadmin` with a strong password. The full name is `machdzpp_eregadmin`. **Save the password.**
4. Under **Add User To Database**, choose that user and database, then click **Add**. Tick **ALL PRIVILEGES** and click **Make Changes**.

The e-Registry creates its own tables. If you reuse the database from the Node.js version, its old tables are ignored, because the Django tables have different names.

## 3. Upload the app

1. Open **File Manager** and go to your home folder (`/home/machdzpp`). Don't use `public_html` or the `e-registry` web folder.
2. **Upload** `rhd-e-registry-2.0.0.zip`.
3. Right-click it and choose **Extract** into `/home/machdzpp`. This creates `/home/machdzpp/rhd-e-registry`.
4. Delete the zip.

## 4. Create the Python app

1. In cPanel, open **Setup Python App** and click **Create Application**.
2. Fill in:

| Field | Value |
|---|---|
| Python version | **3.12** or higher |
| Application root | `rhd-e-registry` |
| Application URL | `e-registry.rhduer.online` (leave the path box empty) |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

3. Under **Environment variables**, add `COOKIE_SECURE` = `1`. Optionally also add `SETUP_KEY` = a key you choose, e.g. `Ghana-RHD-2026` (step 5).
4. Click **Create**, then **Restart**.

You don't need **Run Pip Install**, because Django is bundled. If you do click it, that's fine.

## 5. Connect the database on the set-up page

1. Open `https://e-registry.rhduer.online`. The **Connect the MySQL database** page appears.
2. Enter the **set-up key**. This proves you manage the hosting.
   - If you added `SETUP_KEY` in step 4, type that.
   - Otherwise, in File Manager open `/home/machdzpp/rhd-e-registry-data/SETUP-KEY.txt` and copy the key.
3. Enter the database name, user and password from step 2. Keep the host as `localhost` and the port as `3306`.
4. Click **Test connection**, then **Save and start**. The e-Registry creates its tables and shows **The e-Registry is ready**.

To check it's running, open `https://e-registry.rhduer.online/api/health`. It should show `"ok": true`, `"version": "2.0.0"` and `"database": "mysql"`.

## 6. First sign-in

1. Sign in as **admin** with password **admin1234**, then choose a new password.
2. In **Administration**:
   - **Settings:** enter the region's name.
   - **Directorates & hospitals:** add each directorate and hospital. Each code appears in its reference numbers, e.g. `BOLDHD/26/0001`.
   - **Users:** add every officer, with their role and mobile number.

System administrators can also open **`https://e-registry.rhduer.online/django-admin/`** (Django admin) to view and correct records directly. The audit trail is read-only there too.

## 7. Keep SMS alerts and reminders running (cron job)

Shared hosting stops idle apps, which pauses SMS sending and the weekday overdue reminder.

1. In the e-Registry, open **Administration → SMS alerts** and click **Copy command**.
2. In cPanel → **Cron Jobs**, set **Once Per Five Minutes**, paste the command and click **Add New Cron Job**.

## 8. Turn on SMS (Fish Africa / LetsFish)

In **Administration → SMS alerts**:
1. Tick **Send SMS alerts** and choose **Fish Africa (LetsFish)**.
2. Enter the **App ID**, **App Secret** and approved **Sender ID**. They're in the Fish Africa dashboard → Settings → Applications → Default App.
3. Save, then use **Send a test SMS**.

## Backups

- **Database:** cPanel → **Backup** → **Download a MySQL Database Backup** → `machdzpp_eregistry`.
- **Scanned letters and settings:** the folder `/home/machdzpp/rhd-e-registry-data`. Compress and download it in File Manager.

## Updating to a new version

1. Back up the database.
2. Upload the new zip to `/home/machdzpp` and **Extract** it, overwriting existing files. Your data folder isn't inside the app folder, so it's kept.
3. In **Setup Python App**, click **Restart**.

Database changes are applied automatically when the app restarts.

## Troubleshooting

| What you see | What to do |
|---|---|
| The **Connect the MySQL database** page every time | Setup hasn't been completed. Do step 5. |
| "The set-up key is not correct" | Type the key from `SETUP-KEY.txt` or from your `SETUP_KEY` variable. If you just added the variable, click **Restart** first. |
| "MySQL refused the username or password" | Check the user and password, and that the user was added to the database with ALL PRIVILEGES. |
| The setup page reappears with "the saved database details no longer work" | The database password changed. Enter the new details. |
| Still shows the old Node.js site or "It works!" | The Node.js app is still attached to the address. Do step 1, then restart the Python app. |
| An error mentioning Python or Django version | Choose Python **3.12+** in Setup Python App. |
| Other errors | Open `stderr.log` in the `rhd-e-registry` folder. Setup Python App also shows a log file path. |
