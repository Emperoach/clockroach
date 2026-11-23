// Headmaster's daily register. Works offline: registers wait on the phone until they can be sent.
// shortcut: localStorage is not encrypted by the app (relies on Android's device encryption); move to IndexedDB + WebCrypto if SUBEB requires app-level encryption.
const QUEUE = "register-queue";
const REJECTED = "register-rejected";
const SCHOOL = "school-data";
const SENT = "last-sent-date";

const $ = id => document.getElementById(id);
const load = (key, fallback) => JSON.parse(localStorage.getItem(key) || "null") ?? fallback;
const save = (key, value) => localStorage.setItem(key, JSON.stringify(value));
const csrf = () => (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";
const lagosDate = d => d.toLocaleDateString("en-CA", { timeZone: "Africa/Lagos" });
const say = text => { $("status").textContent = text; };

let school = null;

async function loadSchool() {
  try {
    const r = await fetch("/api/school", { credentials: "same-origin" });
    if (r.redirected || !r.headers.get("content-type")?.includes("json")) {
      location.href = "/login/?next=/app/";
      return;
    }
    school = await r.json();
    save(SCHOOL, school);
  } catch {
    school = load(SCHOOL, null);
    if (!school) return say("No connection. Connect once to download your school's teacher list.");
    say("No connection. Your register will be saved on the phone and sent later.");
  }
  render();
}

function alreadyDone(today) {
  return (school.submitted_today && school.today === today) || load(SENT, null) === today || load(QUEUE, []).some(r => r.date === today);
}

function render() {
  const today = lagosDate(new Date());
  $("school").textContent = school.school;
  $("today").textContent = `${school.user} · ${school.role} · ${today}`;
  const form = $("register");
  if (school.closed && school.today === today) {
    form.hidden = true;
    return say("The school is closed today. No register is needed.");
  }
  if (alreadyDone(today)) {
    form.hidden = true;
    renderQueue();
    return say(load(QUEUE, []).length ? "Today's register is saved and waiting to send." : "Today's register has been submitted.");
  }
  const options = school.reasons.map(r => `<option value="${r.id}">${escape(r.label)}</option>`).join("");
  $("teachers").innerHTML = school.teachers.map(t => `
    <fieldset class="teacher" data-id="${t.id}">
      <legend>${escape(t.name)}</legend>
      <label><input type="radio" name="t${t.id}" value="1" required> Present</label>
      <label><input type="radio" name="t${t.id}" value="0"> Absent</label>
      <div class="absent" hidden>
        <label>Reason <select><option value="">Choose a reason</option>${options}</select></label>
        <label>Note <input type="text"></label>
      </div>
    </fieldset>`).join("");
  form.hidden = false;
  renderQueue();
}

function escape(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

$("register").addEventListener("change", e => {
  if (e.target.type !== "radio") return;
  const box = e.target.closest("fieldset").querySelector(".absent");
  box.hidden = e.target.value === "1";
  box.querySelector("select").required = e.target.value === "0";
});

function readEntries() {
  const entries = [];
  for (const fs of document.querySelectorAll("fieldset.teacher")) {
    const present = fs.querySelector("input[type=radio]:checked").value === "1";
    const reason = Number(fs.querySelector("select").value) || null;
    const note = fs.querySelector(".absent input").value.trim();
    const r = school.reasons.find(x => x.id === reason);
    if (!present && r && r.needs_note && !note) throw new Error(`Add a note for ${fs.querySelector("legend").textContent}`);
    entries.push({ teacher: Number(fs.dataset.id), present, reason: present ? null : reason, note: present ? "" : note });
  }
  return entries;
}

function position() {
  return new Promise(resolve => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(p => resolve(p.coords), () => resolve(null),
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 });
  });
}

$("register").addEventListener("submit", async e => {
  e.preventDefault();
  let entries;
  try { entries = readEntries(); } catch (err) { return say(err.message); }
  if (!confirm("Submit the register? It cannot be changed after this.")) return;
  $("submit").disabled = true;
  say("Getting your location…");
  const now = new Date();
  const gps = await position();
  const record = {
    id: crypto.randomUUID(),
    date: lagosDate(now),
    captured_at: now.toISOString(),
    lat: gps ? gps.latitude : null,
    lng: gps ? gps.longitude : null,
    accuracy: gps ? gps.accuracy : null,
    entries,
  };
  save(QUEUE, [...load(QUEUE, []), record]);
  $("register").hidden = true;
  say(gps ? "Saved on the phone. Sending…" : "Saved on the phone without a location. Sending…");
  await sync();
});

let syncing = false;
async function sync() {
  if (syncing) return;
  syncing = true;
  try {
    for (const record of load(QUEUE, [])) {
      let r;
      try {
        r = await fetch("/api/registers", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ ...record, device_now: new Date().toISOString() }),
        });
      } catch {
        say("No connection. The register is saved on the phone and will be sent automatically.");
        break;
      }
      if (r.redirected || r.status === 401 || r.status === 403) {
        say("Please sign in again to send the saved register. It is still on the phone.");
        break;
      }
      if (r.ok) {
        save(SENT, record.date);
        say("Register received by SUBEB.");
      } else if (r.status >= 500) {
        say("The server had a problem. The register is still on the phone and will be retried.");
        break;
      } else {
        const body = await r.json().catch(() => ({}));
        save(REJECTED, [...load(REJECTED, []), { ...record, error: body.error || `Error ${r.status}` }]);
        say(`The register was not accepted: ${body.error || r.status}`);
      }
      save(QUEUE, load(QUEUE, []).filter(x => x.id !== record.id));
    }
  } finally {
    syncing = false;
    renderQueue();
  }
}

function renderQueue() {
  const waiting = load(QUEUE, []);
  const rejected = load(REJECTED, []);
  $("queue").innerHTML =
    (waiting.length ? `<p>${waiting.length} register(s) waiting to send.</p>` : "") +
    rejected.map(r => `<p class="error">Register for ${r.date} was not accepted: ${escape(r.error)}. Call your Ward Supervisor.</p>`).join("");
}

window.addEventListener("online", sync);
setInterval(sync, 60000);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/app/sw.js");
loadSchool().then(sync);
