from django.db import migrations

# Draft list from the proposal. SUBEB approves the final list and edits it in the admin.
REASONS = [
    ("Sick leave", False),
    ("Annual leave", False),
    ("Maternity or paternity leave", False),
    ("Casual or compassionate leave", False),
    ("Official assignment", False),
    ("Training or workshop", False),
    ("Absent without permission", False),
    ("Other", True),
]


def add(apps, schema_editor):
    AbsenceReason = apps.get_model("attendance", "AbsenceReason")
    for label, needs_note in REASONS:
        AbsenceReason.objects.get_or_create(label=label, defaults={"needs_note": needs_note})


class Migration(migrations.Migration):
    dependencies = [("attendance", "0002_lock_tables")]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
