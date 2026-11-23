from django.contrib import admin

from .models import LGA, AbsenceReason, AuditLog, Closure, Correction, Profile, Register, RegisterEntry, School, Teacher, Ward

admin.site.site_header = "Plateau SUBEB teacher attendance"

admin.site.register(LGA)
admin.site.register(Ward, list_display=["name", "lga"], list_filter=["lga"])
admin.site.register(School, list_display=["code", "name", "ward"], list_filter=["ward__lga"], search_fields=["code", "name"])
admin.site.register(Teacher, list_display=["staff_id", "name", "school", "active"], list_filter=["active", "school__ward__lga"], search_fields=["staff_id", "name"])
admin.site.register(Profile, list_display=["user", "role", "school", "ward", "lga"], list_filter=["role"])
admin.site.register(AbsenceReason, list_display=["label", "needs_note", "active"])
admin.site.register(Closure, list_display=["reason", "start", "end", "lga", "school"])


class ReadOnly(admin.ModelAdmin):
    """Locked records: visible for audit review, never editable here."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Register, ReadOnly, list_display=["school", "date", "submitted_by", "captured_at", "flags"], list_filter=["date"])
admin.site.register(RegisterEntry, ReadOnly, list_display=["register", "teacher", "present", "reason"])
admin.site.register(Correction, ReadOnly, list_display=["entry", "officer", "at", "why"])
admin.site.register(AuditLog, ReadOnly, list_display=["at", "user", "action", "target"], list_filter=["action"])
