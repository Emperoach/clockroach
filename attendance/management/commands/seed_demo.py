from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from attendance.models import LGA, Profile, School, Teacher, Ward

PIN = "1234"


def square(lng, lat, d=0.002):
    return [[lng - d, lat - d], [lng + d, lat - d], [lng + d, lat + d], [lng - d, lat + d]]


class Command(BaseCommand):
    help = "Create fictional sample data for trying the app. Development only."

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DEBUG on")
        lga, _ = LGA.objects.get_or_create(name="Sample LGA")
        ward, _ = Ward.objects.get_or_create(name="Sample Ward A", lga=lga)
        other, _ = Ward.objects.get_or_create(name="Sample Ward B", lga=lga)
        s1, _ = School.objects.get_or_create(code="DEMO-001", defaults=dict(name="Demo Primary School 1", ward=ward, boundary=square(8.89, 9.90)))
        School.objects.get_or_create(code="DEMO-002", defaults=dict(name="Demo Primary School 2", ward=ward, boundary=square(8.90, 9.91)))
        School.objects.get_or_create(code="DEMO-003", defaults=dict(name="Demo Primary School 3", ward=other, boundary=square(8.91, 9.92)))
        for i, name in enumerate(["Teacher One", "Teacher Two", "Teacher Three", "Teacher Four"], 1):
            Teacher.objects.get_or_create(staff_id=f"DEMO-T{i}", defaults=dict(name=name, school=s1))

        users = [
            ("08000000001", "Head Teacher", dict(role=Profile.HEADMASTER, school=s1)),
            ("08000000002", "Assistant Head", dict(role=Profile.ASSISTANT, school=s1)),
            ("08000000003", "Ward Supervisor", dict(role=Profile.WARD_SUPERVISOR, ward=ward)),
            ("08000000004", "Education Secretary", dict(role=Profile.EDUCATION_SECRETARY, lga=lga)),
            ("08000000005", "SUBEB Admin", dict(role=Profile.ADMIN)),
        ]
        for phone, full_name, profile in users:
            first, last = full_name.split(" ", 1)
            user, created = User.objects.get_or_create(username=phone, defaults=dict(first_name=first, last_name=last))
            if created:
                user.set_password(PIN)
                if profile["role"] == Profile.ADMIN:
                    user.is_staff = user.is_superuser = True
                user.save()
            Profile.objects.update_or_create(user=user, defaults=profile)
            self.stdout.write(f"{phone}  PIN {PIN}  {full_name}")
