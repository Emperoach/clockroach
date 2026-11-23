import json
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import DatabaseError, transaction
from django.test import TestCase

from .models import LGA, AbsenceReason, Closure, Correction, Profile, Register, School, Teacher, Ward
from .rules import point_in_polygon

LAGOS = ZoneInfo("Africa/Lagos")
DAY = date(2026, 11, 23)


def at(hh, mm, day=DAY):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=LAGOS)


def square(lng, lat, d=0.002):
    return [[lng - d, lat - d], [lng + d, lat - d], [lng + d, lat + d], [lng - d, lat + d]]


class Base(TestCase):
    def setUp(self):
        lga = LGA.objects.create(name="L")
        self.ward = Ward.objects.create(name="W1", lga=lga)
        ward2 = Ward.objects.create(name="W2", lga=lga)
        self.school = School.objects.create(code="S1", name="School 1", ward=self.ward, boundary=square(8.89, 9.90))
        self.school2 = School.objects.create(code="S2", name="School 2", ward=ward2, boundary=square(8.95, 9.95))
        self.t1 = Teacher.objects.create(staff_id="T1", name="A", school=self.school)
        self.t2 = Teacher.objects.create(staff_id="T2", name="B", school=self.school)
        self.elsewhere = Teacher.objects.create(staff_id="T3", name="C", school=self.school2)
        self.sick = AbsenceReason.objects.get(label="Sick leave")
        self.other = AbsenceReason.objects.get(label="Other")
        self.head = self.user("h", Profile.HEADMASTER, school=self.school)
        self.assistant = self.user("a", Profile.ASSISTANT, school=self.school)
        self.sup = self.user("s", Profile.WARD_SUPERVISOR, ward=self.ward)
        self.sup2 = self.user("s2", Profile.WARD_SUPERVISOR, ward=ward2)

    def user(self, name, role, **scope):
        u = User.objects.create_user(name, password="x")
        Profile.objects.create(user=u, role=role, **scope)
        return u

    def payload(self, captured, device_now=None, lat=9.90, lng=8.89, entries=None, **extra):
        return {
            "id": str(uuid.uuid4()),
            "date": DAY.isoformat(),
            "captured_at": captured.isoformat(),
            "device_now": (device_now or captured).isoformat(),
            "lat": lat,
            "lng": lng,
            "accuracy": 10,
            "entries": entries
            if entries is not None
            else [
                {"teacher": self.t1.id, "present": True},
                {"teacher": self.t2.id, "present": False, "reason": self.sick.id},
            ],
            **extra,
        }

    def submit(self, body, user=None, now=None):
        self.client.force_login(user or self.head)
        now = now or datetime.fromisoformat(body["device_now"])
        with patch("django.utils.timezone.now", return_value=now):
            return self.client.post("/api/registers", json.dumps(body), content_type="application/json")


class GeoTests(TestCase):
    def test_point_in_polygon(self):
        poly = square(8.89, 9.90)
        self.assertTrue(point_in_polygon(8.89, 9.90, poly))
        self.assertFalse(point_in_polygon(8.90, 9.90, poly))


class SubmitTests(Base):
    def test_on_time_on_site(self):
        r = self.submit(self.payload(at(7, 30)))
        self.assertEqual(r.status_code, 201, r.content)
        reg = Register.objects.get()
        self.assertEqual(reg.flags, [])
        self.assertFalse(reg.is_alternate)
        self.assertFalse(reg.captured_offline)
        self.assertEqual(reg.entries.count(), 2)

    def test_late_and_off_site(self):
        self.submit(self.payload(at(9, 30), lat=9.95, lng=8.95))
        reg = Register.objects.get()
        self.assertEqual(sorted(reg.flags), ["late", "off_site"])
        self.assertEqual(sorted(reg.flag_labels()), ["Sent after the deadline", "Sent from outside the school"])

    def test_offline_capture_keeps_its_morning_time(self):
        body = self.payload(at(7, 30), device_now=at(13, 0))
        self.submit(body, now=at(13, 0))
        reg = Register.objects.get()
        self.assertTrue(reg.captured_offline)
        self.assertEqual(reg.captured_at, at(7, 30))
        self.assertNotIn("late", reg.flags)

    def test_wrong_phone_clock_is_corrected_and_flagged(self):
        # Phone clock is one hour behind: it says 06:30 when it is really 07:30.
        body = self.payload(at(6, 30))
        self.submit(body, now=at(7, 30))
        reg = Register.objects.get()
        self.assertIn("clock_skew", reg.flags)
        self.assertEqual(reg.captured_at, at(7, 30))

    def test_resend_is_not_saved_twice(self):
        body = self.payload(at(7, 30))
        self.assertEqual(self.submit(body).status_code, 201)
        self.assertEqual(self.submit(body).status_code, 200)
        self.assertEqual(Register.objects.count(), 1)

    def test_one_register_per_school_per_day(self):
        self.submit(self.payload(at(7, 30)))
        r = self.submit(self.payload(at(7, 40)), user=self.assistant)
        self.assertEqual(r.status_code, 409)

    def test_assistant_submits_as_alternate(self):
        self.submit(self.payload(at(7, 30)), user=self.assistant)
        self.assertTrue(Register.objects.get().is_alternate)

    def test_bad_registers_are_refused(self):
        cases = {
            "absent without reason": [{"teacher": self.t1.id, "present": True}, {"teacher": self.t2.id, "present": False}],
            "other without note": [
                {"teacher": self.t1.id, "present": True},
                {"teacher": self.t2.id, "present": False, "reason": self.other.id},
            ],
            "teacher missing": [{"teacher": self.t1.id, "present": True}],
            "teacher listed twice": [{"teacher": self.t1.id, "present": True}] * 2,
            "teacher from another school": [
                {"teacher": self.t1.id, "present": True},
                {"teacher": self.t2.id, "present": True},
                {"teacher": self.elsewhere.id, "present": True},
            ],
        }
        for name, entries in cases.items():
            with self.subTest(name):
                self.assertEqual(self.submit(self.payload(at(7, 30), entries=entries)).status_code, 400)
        self.assertEqual(Register.objects.count(), 0)

    def test_supervisor_cannot_submit(self):
        self.assertEqual(self.submit(self.payload(at(7, 30)), user=self.sup).status_code, 403)

    def test_closed_school_needs_no_register(self):
        Closure.objects.create(start=DAY, end=DAY, reason="Holiday")
        self.assertEqual(self.submit(self.payload(at(7, 30))).status_code, 400)


class LockTests(Base):
    def test_register_cannot_be_changed_or_deleted(self):
        self.submit(self.payload(at(7, 30)))
        reg = Register.objects.get()
        entry = reg.entries.first()
        for action in (reg.save, reg.delete, entry.save):
            with self.assertRaises(PermissionError):
                action()
        # Bulk changes bypass the model, so the database triggers must catch them.
        for action in (
            lambda: Register.objects.update(flags=[]),
            lambda: reg.entries.update(present=True),
            lambda: Register.objects.all()._raw_delete(Register.objects.db),
        ):
            with self.assertRaises(DatabaseError), transaction.atomic():
                action()
        self.assertEqual(Register.objects.get().flags, [])
        self.assertEqual(reg.entries.filter(present=False).count(), 1)


class CorrectionTests(Base):
    def setUp(self):
        super().setUp()
        self.submit(self.payload(at(7, 30)))
        self.entry = Register.objects.get().entries.get(teacher=self.t2)
        self.url = f"/entries/{self.entry.id}/correct"

    def test_supervisor_corrects_with_reason_and_original_is_kept(self):
        self.client.force_login(self.sup)
        self.client.post(self.url, {"present": "1", "why": "Saw her in class on my visit"})
        self.entry.refresh_from_db()
        self.assertFalse(self.entry.present)  # original kept
        self.assertEqual(self.entry.effective()[0], True)
        c = Correction.objects.get()
        self.assertEqual((c.old_present, c.old_reason, c.new_present), (False, self.sick, True))

    def test_correction_needs_a_written_reason(self):
        self.client.force_login(self.sup)
        r = self.client.post(self.url, {"present": "1", "why": " "})
        self.assertIn("error=", r["Location"])
        self.assertFalse(Correction.objects.exists())

    def test_only_this_wards_supervisor_can_correct(self):
        for user in (self.sup2, self.head, self.assistant):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url, {"present": "1", "why": "x"}).status_code, 403)
        self.assertFalse(Correction.objects.exists())


class ScopeTests(Base):
    def test_supervisor_sees_only_their_ward(self):
        self.client.force_login(self.sup)
        r = self.client.get(f"/dashboard/?date={DAY}")
        self.assertContains(r, "School 1")
        self.assertNotContains(r, "School 2")
        self.assertEqual(self.client.get(f"/dashboard/school/{self.school2.id}/{DAY}/").status_code, 404)

    def test_dashboard_statuses(self):
        Closure.objects.create(start=DAY, end=DAY, school=self.school2, reason="Flood")
        self.submit(self.payload(at(9, 30)))
        admin = self.user("adm", Profile.ADMIN)
        self.client.force_login(admin)
        r = self.client.get(f"/dashboard/?date={DAY}")
        self.assertContains(r, '<li class="s-late">Late: 1</li>', html=True)
        self.assertContains(r, '<li class="s-closed">Closed: 1</li>', html=True)
