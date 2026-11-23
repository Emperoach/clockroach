import uuid

from django.conf import settings
from django.db import models


class LGA(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "LGA"

    def __str__(self):
        return self.name


class Ward(models.Model):
    name = models.CharField(max_length=100)
    lga = models.ForeignKey(LGA, on_delete=models.PROTECT, related_name="wards")

    def __str__(self):
        return f"{self.name} ({self.lga})"


class School(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, related_name="schools")
    # Boundary as a list of [longitude, latitude] points, from the field survey.
    boundary = models.JSONField(null=True, blank=True)

    def __str__(self):
        return self.name


class Teacher(models.Model):
    staff_id = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    # Current posting. Past postings stay visible through old registers.
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="teachers", null=True, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Profile(models.Model):
    HEADMASTER = "headmaster"
    ASSISTANT = "assistant"
    WARD_SUPERVISOR = "ward_supervisor"
    EDUCATION_SECRETARY = "education_secretary"
    ADMIN = "admin"
    MANAGEMENT = "management"
    HELPDESK = "helpdesk"
    ROLES = [
        (HEADMASTER, "Headmaster"),
        (ASSISTANT, "Assistant Headmaster"),
        (WARD_SUPERVISOR, "Ward Supervisor"),
        (EDUCATION_SECRETARY, "Education Secretary"),
        (ADMIN, "SUBEB administrator"),
        (MANAGEMENT, "SUBEB management / M&E"),
        (HELPDESK, "Helpdesk officer"),
    ]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=30, choices=ROLES)
    school = models.ForeignKey(School, on_delete=models.PROTECT, null=True, blank=True)
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True)
    lga = models.ForeignKey(LGA, on_delete=models.PROTECT, null=True, blank=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user} ({self.get_role_display()})"


class AbsenceReason(models.Model):
    label = models.CharField(max_length=100, unique=True)
    needs_note = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.label


class Closure(models.Model):
    """A holiday or closure. No LGA and no school means state-wide."""

    start = models.DateField()
    end = models.DateField()
    lga = models.ForeignKey(LGA, on_delete=models.CASCADE, null=True, blank=True)
    school = models.ForeignKey(School, on_delete=models.CASCADE, null=True, blank=True)
    reason = models.CharField(max_length=200)

    @classmethod
    def covering(cls, school, day):
        return cls.objects.filter(start__lte=day, end__gte=day).filter(
            models.Q(lga__isnull=True, school__isnull=True)
            | models.Q(lga_id=school.ward.lga_id)
            | models.Q(school=school)
        )

    def __str__(self):
        return f"{self.reason} {self.start}–{self.end}"


class AppendOnly(models.Model):
    """Rows can be added, never changed or deleted. Database triggers enforce the same."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError(f"{type(self).__name__} records are locked")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(f"{type(self).__name__} records are locked")


FLAG_LABELS = {
    "late": "Sent after the deadline",
    "off_site": "Sent from outside the school",
    "no_gps": "No location on the register",
    "no_boundary": "School boundary not yet mapped",
    "clock_skew": "Phone clock was wrong",
    "late_sync": "Reached the server days later",
    "date_mismatch": "Date does not match capture time",
}


class Register(AppendOnly):
    # The phone creates the id, so a register re-sent after a dropped connection is not saved twice.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="registers")
    date = models.DateField()
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    is_alternate = models.BooleanField(default=False)
    captured_at = models.DateTimeField(help_text="Phone clock, corrected by the skew measured at upload")
    received_at = models.DateTimeField(auto_now_add=True)
    clock_skew_seconds = models.IntegerField(default=0)
    captured_offline = models.BooleanField(default=False)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    gps_accuracy = models.FloatField(null=True, blank=True)
    flags = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["school", "date"], name="one_register_per_school_per_day")]

    def __str__(self):
        return f"{self.school} {self.date}"

    def flag_labels(self):
        return [FLAG_LABELS.get(f, f) for f in self.flags]


class RegisterEntry(AppendOnly):
    register = models.ForeignKey(Register, on_delete=models.PROTECT, related_name="entries")
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT)
    present = models.BooleanField()
    reason = models.ForeignKey(AbsenceReason, on_delete=models.PROTECT, null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "register entries"

    def effective(self):
        """The value in force: the latest correction, or the original entry."""
        last = self.corrections.order_by("-at", "-id").first()
        if last:
            return last.new_present, last.new_reason, last.new_note
        return self.present, self.reason, self.note


class Correction(AppendOnly):
    entry = models.ForeignKey(RegisterEntry, on_delete=models.PROTECT, related_name="corrections")
    officer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    old_present = models.BooleanField()
    old_reason = models.ForeignKey(AbsenceReason, on_delete=models.PROTECT, null=True, related_name="+")
    old_note = models.TextField(blank=True)
    new_present = models.BooleanField()
    new_reason = models.ForeignKey(AbsenceReason, on_delete=models.PROTECT, null=True, related_name="+")
    new_note = models.TextField(blank=True)
    why = models.TextField()
    at = models.DateTimeField(auto_now_add=True)


class AuditLog(AppendOnly):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=50)
    target = models.CharField(max_length=100)
    detail = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def record(cls, user, action, target, **detail):
        return cls.objects.create(user=user, action=action, target=str(target), detail=detail)
