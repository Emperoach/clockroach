from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Profile, School


def point_in_polygon(lng, lat, polygon):
    """Ray casting. polygon is a list of [lng, lat] points."""
    # shortcut: ignores GPS accuracy and the Earth's curvature (fine at school scale); move to PostGIS ST_DWithin if pilot shows false flags.
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def schools_in_scope(user):
    """The only place that decides which schools a user may see."""
    profile = getattr(user, "profile", None)
    qs = School.objects.select_related("ward__lga")
    if profile is None:
        return qs.none()
    if profile.role in (Profile.HEADMASTER, Profile.ASSISTANT):
        return qs.filter(pk=profile.school_id)
    if profile.role == Profile.WARD_SUPERVISOR:
        return qs.filter(ward_id=profile.ward_id)
    if profile.role == Profile.EDUCATION_SECRETARY:
        return qs.filter(ward__lga_id=profile.lga_id)
    if profile.role in (Profile.ADMIN, Profile.MANAGEMENT):
        return qs
    return qs.none()


def deadline_for(day):
    h, m = map(int, settings.SUBMISSION_DEADLINE.split(":"))
    return timezone.make_aware(datetime.combine(day, time(h, m)))


def assess(school, day, captured_at_device, device_now, received_at, lat, lng):
    """Work out the trustworthy capture time and the flags for a register.

    Both phone timestamps come from the same clock, so their difference is how long the
    register waited on the phone, whatever the phone's clock is set to.
    """
    skew = received_at - device_now
    captured_at = captured_at_device + skew
    waited = device_now - captured_at_device
    offline = waited > timedelta(minutes=2)

    flags = []
    if abs(skew.total_seconds()) > settings.MAX_CLOCK_SKEW_SECONDS:
        flags.append("clock_skew")
    if captured_at > deadline_for(day):
        flags.append("late")
    if lat is None or lng is None:
        flags.append("no_gps")
    elif school.boundary and not point_in_polygon(lng, lat, school.boundary):
        flags.append("off_site")
    if not school.boundary:
        flags.append("no_boundary")
    if waited > timedelta(hours=settings.LATE_SYNC_HOURS):
        flags.append("late_sync")
    if timezone.localdate(captured_at) != day:
        flags.append("date_mismatch")
    return captured_at, int(skew.total_seconds()), offline, flags


# The colour for a school on a given day. The same scale is used at every level.
STATUS_LABELS = {"on_time": "On time", "late": "Late", "flagged": "Flagged", "missing": "Missing", "closed": "Closed"}


def school_status(register, closed):
    if closed:
        return "closed"
    if register is None:
        return "missing"
    serious = set(register.flags) & {"off_site", "no_gps", "clock_skew", "date_mismatch", "late_sync"}
    if serious:
        return "flagged"
    if "late" in register.flags:
        return "late"
    return "on_time"
