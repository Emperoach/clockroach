from django.db import migrations

LOCKED = ["attendance_register", "attendance_registerentry", "attendance_correction", "attendance_auditlog"]


def lock(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(
            "CREATE OR REPLACE FUNCTION attendance_locked() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'locked: %% is append-only', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql"
        )
    for table in LOCKED:
        if vendor == "sqlite":
            for op in ("UPDATE", "DELETE"):
                schema_editor.execute(
                    f"CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, 'locked: {table} is append-only'); END"
                )
        elif vendor == "postgresql":
            schema_editor.execute(
                f"CREATE TRIGGER {table}_locked BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION attendance_locked()"
            )
        else:
            raise RuntimeError(f"No lock triggers written for {vendor}")


class Migration(migrations.Migration):
    dependencies = [("attendance", "0001_initial")]
    # No reverse: removing the lock must be a deliberate, separate migration.
    operations = [migrations.RunPython(lock)]
