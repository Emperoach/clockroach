import json
import uuid
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import AbsenceReason, AuditLog, Closure, Correction, Profile, Register, RegisterEntry
from .rules import STATUS_LABELS, assess, school_status, schools_in_scope

SUBMITTERS = (Profile.HEADMASTER, Profile.ASSISTANT)


def _profile(user):
    profile = getattr(user, "profile", None)
    if profile is None:
        raise PermissionDenied("No role assigned")
    return profile


def _clean_entry(present, reason, note):
    """Shared rule for the register and for corrections: an absence needs a reason."""
    if present:
        return True, None, ""
    if reason is None or not reason.active:
        raise ValueError("Every absent teacher needs a reason")
    if reason.needs_note and not note.strip():
        raise ValueError(f'"{reason.label}" needs a written note')
    return False, reason, note.strip()


@login_required
def home(request):
    if _profile(request.user).role in SUBMITTERS:
        return redirect("app")
    return redirect("dashboard")


@login_required
@ensure_csrf_cookie
def app_page(request):
    if _profile(request.user).role not in SUBMITTERS:
        raise PermissionDenied
    return render(request, "attendance/app.html")


def service_worker(request):
    # Served from /app/ so the worker may control the app pages.
    response = render(request, "attendance/sw.js", content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    return response


@login_required
@require_GET
def api_school(request):
    profile = _profile(request.user)
    if profile.role not in SUBMITTERS:
        raise PermissionDenied
    school = profile.school
    today = timezone.localdate()
    return JsonResponse(
        {
            "school": school.name,
            "user": request.user.get_full_name() or request.user.username,
            "role": profile.get_role_display(),
            "today": today.isoformat(),
            "closed": Closure.covering(school, today).exists(),
            "submitted_today": Register.objects.filter(school=school, date=today).exists(),
            "teachers": [{"id": t.id, "name": t.name} for t in school.teachers.filter(active=True).order_by("name")],
            "reasons": [
                {"id": r.id, "label": r.label, "needs_note": r.needs_note}
                for r in AbsenceReason.objects.filter(active=True).order_by("id")
            ],
        }
    )


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


@login_required
@require_POST
def api_submit(request):
    profile = _profile(request.user)
    if profile.role not in SUBMITTERS or profile.school is None:
        return _error("Only a headmaster or assistant headmaster can submit a register", 403)
    school = profile.school
    received_at = timezone.now()

    try:
        data = json.loads(request.body)
        register_id = uuid.UUID(data["id"])
        day = parse_date(data["date"])
        captured_at = parse_datetime(data["captured_at"])
        device_now = parse_datetime(data["device_now"])
        lat, lng, accuracy = data.get("lat"), data.get("lng"), data.get("accuracy")
        entries = data["entries"]
    except (ValueError, KeyError, TypeError):
        return _error("Malformed register")
    if day is None or captured_at is None or device_now is None or timezone.is_naive(captured_at) or timezone.is_naive(device_now):
        return _error("Dates must be ISO 8601 with a time zone")
    for v in (lat, lng, accuracy):
        if v is not None and not isinstance(v, (int, float)):
            return _error("Bad GPS reading")

    existing = Register.objects.filter(pk=register_id).first()
    if existing:
        # The phone is re-sending something already saved, e.g. after a dropped connection.
        if existing.school_id == school.id:
            return JsonResponse({"status": "already_received", "id": str(existing.id)})
        return _error("Register id already used", 409)
    if Closure.covering(school, day).exists():
        return _error("The school is closed on this day")

    teachers = {t.id: t for t in school.teachers.filter(active=True)}
    reasons = {r.id: r for r in AbsenceReason.objects.all()}
    cleaned = []
    try:
        for e in entries:
            teacher = teachers.get(e.get("teacher"))
            if teacher is None:
                raise ValueError("A teacher on the register is not posted to this school")
            reason = reasons.get(e.get("reason"))
            cleaned.append((teacher, *_clean_entry(bool(e.get("present")), reason, str(e.get("note") or ""))))
    except (ValueError, AttributeError) as exc:
        return _error(str(exc))
    if sorted(t.id for t, *_ in cleaned) != sorted(teachers):
        return _error("The register must list every teacher at the school exactly once")

    captured_at, skew, offline, flags = assess(school, day, captured_at, device_now, received_at, lat, lng)
    try:
        with transaction.atomic():
            register = Register.objects.create(
                id=register_id,
                school=school,
                date=day,
                submitted_by=request.user,
                is_alternate=profile.role == Profile.ASSISTANT,
                captured_at=captured_at,
                clock_skew_seconds=skew,
                captured_offline=offline,
                lat=lat,
                lng=lng,
                gps_accuracy=accuracy,
                flags=flags,
            )
            RegisterEntry.objects.bulk_create(
                RegisterEntry(register=register, teacher=t, present=p, reason=r, note=n) for t, p, r, n in cleaned
            )
            AuditLog.record(request.user, "submit_register", register.id, school=school.code, flags=flags)
    except IntegrityError:
        return _error("A register has already been submitted for this school today", 409)
    return JsonResponse({"status": "received", "id": str(register.id), "flags": flags}, status=201)


def _closed_ids(schools, day):
    closures = list(Closure.objects.filter(start__lte=day, end__gte=day))
    return {
        s.id
        for s in schools
        for c in closures
        if (c.lga_id is None and c.school_id is None) or c.lga_id == s.ward.lga_id or c.school_id == s.id
    }


@login_required
@require_GET
def dashboard(request):
    _profile(request.user)
    day = parse_date(request.GET.get("date", "")) or timezone.localdate()
    schools = list(schools_in_scope(request.user).order_by("ward__lga__name", "ward__name", "name"))
    registers = {r.school_id: r for r in Register.objects.filter(date=day, school__in=schools)}
    closed = _closed_ids(schools, day)
    rows = []
    for s in schools:
        status = school_status(registers.get(s.id), s.id in closed)
        rows.append((s, registers.get(s.id), status, STATUS_LABELS[status]))
    counts = [(st, label, sum(1 for r in rows if r[2] == st)) for st, label in STATUS_LABELS.items()]
    return render(request, "attendance/dashboard.html", {"day": day, "rows": rows, "counts": counts})


def _school_in_scope(user, school_id):
    return get_object_or_404(schools_in_scope(user), pk=school_id)


@login_required
@require_GET
def school_day(request, school_id, day):
    school = _school_in_scope(request.user, school_id)
    day = parse_date(day)
    if day is None:
        return HttpResponseBadRequest("Bad date")
    register = Register.objects.filter(school=school, date=day).select_related("submitted_by").first()
    entries = []
    if register:
        for e in register.entries.select_related("teacher", "reason").prefetch_related("corrections__officer"):
            present, reason, note = e.effective()
            entries.append({"entry": e, "present": present, "reason": reason, "note": note})
    profile = _profile(request.user)
    can_correct = profile.role == Profile.WARD_SUPERVISOR and profile.ward_id == school.ward_id
    return render(
        request,
        "attendance/school_day.html",
        {
            "school": school,
            "day": day,
            "register": register,
            "entries": entries,
            "can_correct": can_correct,
            "reasons": AbsenceReason.objects.filter(active=True),
            "error": request.GET.get("error", ""),
        },
    )


@login_required
@require_POST
def correct(request, entry_id):
    entry = get_object_or_404(RegisterEntry.objects.select_related("register__school"), pk=entry_id)
    school = entry.register.school
    profile = _profile(request.user)
    if not (profile.role == Profile.WARD_SUPERVISOR and profile.ward_id == school.ward_id):
        raise PermissionDenied("Only the Ward Supervisor for this school can correct its register")

    back = redirect("school_day", school_id=school.id, day=entry.register.date.isoformat())
    why = request.POST.get("why", "").strip()
    reason = AbsenceReason.objects.filter(pk=request.POST.get("reason") or None).first()
    try:
        if not why:
            raise ValueError("A correction must have a written reason")
        new = _clean_entry(request.POST.get("present") == "1", reason, request.POST.get("note", ""))
    except ValueError as exc:
        back["Location"] += "?" + urlencode({"error": str(exc)})
        return back
    old = entry.effective()
    if new == (old[0], old[1], old[2]):
        return back
    with transaction.atomic():
        c = Correction.objects.create(
            entry=entry,
            officer=request.user,
            old_present=old[0],
            old_reason=old[1],
            old_note=old[2],
            new_present=new[0],
            new_reason=new[1],
            new_note=new[2],
            why=why,
        )
        AuditLog.record(request.user, "correct_entry", entry.id, correction=c.id, school=school.code, why=why)
    return back
