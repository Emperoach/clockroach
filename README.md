# clockroach — Plateau SUBEB teacher attendance

The headmaster marks the staff register each morning on a phone, with or without signal.
Each register is time-stamped, checked against the school's GPS boundary and locked.
Ward Supervisors, Education Secretaries and SUBEB see it on a colour-coded dashboard.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo      # fictional sample data, PIN 1234
.venv/bin/python manage.py runserver
```

Open http://localhost:8000 and sign in with a phone number printed by `seed_demo`:
`08000000001` headmaster (opens the register app), `08000000003` Ward Supervisor (dashboard),
`08000000005` SUBEB admin (also `/admin/` for schools, teachers, users, reasons and closures).

Tests: `.venv/bin/python manage.py test attendance`

Production uses PostgreSQL: set `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, plus
`SECRET_KEY`, `DEBUG=0` and `ALLOWED_HOSTS`. The app must be served over HTTPS for GPS and offline use.

## How records stay true

- Registers, entries, corrections and the audit log can only be added to. The models refuse
  changes, and database triggers (migration `0002_lock_tables`) refuse UPDATE and DELETE even from direct SQL.
- A correction is a new row with the old value, the new value, the officer and a written reason.
- The phone gives each register an id, so a re-sent register is never saved twice.
- Phone clock errors are measured at upload and corrected; large errors are flagged.

## Rules SUBEB still has to confirm

Morning deadline (`SUBMISSION_DEADLINE`, now 09:00), the absence reasons list (editable in admin),
and the five status colours used on the dashboard.
