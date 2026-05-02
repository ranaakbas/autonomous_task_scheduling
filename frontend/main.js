const calendarGrid = document.getElementById("calendarGrid");
const monthLabel = document.getElementById("monthLabel");
const prevMonthBtn = document.getElementById("prevMonthBtn");
const nextMonthBtn = document.getElementById("nextMonthBtn");
const rescheduleBtn = document.getElementById("rescheduleBtn");
const warningsEl = document.getElementById("warnings");
const explanationEl = document.getElementById("explanation");
const taskList = document.getElementById("taskList");
const taskForm = document.getElementById("taskForm");
const editForm = document.getElementById("editForm");
const availabilityForm = document.getElementById("availabilityForm");
const availabilityList = document.getElementById("availabilityList");
const defaultAvailabilityForm = document.getElementById(
  "defaultAvailabilityForm",
);
const defaultDailyCapacityInput = document.getElementById(
  "defaultDailyCapacityInput",
);

const modalBackdrop = document.getElementById("modalBackdrop");
const createModal = document.getElementById("createModal");
const tasksModal = document.getElementById("tasksModal");
const editModal = document.getElementById("editModal");
const availabilityModal = document.getElementById("availabilityModal");

const openTasksBtn = document.getElementById("openTasksBtn");
const openCreateBtn = document.getElementById("openCreateBtn");
const openAvailabilityBtn = document.getElementById("openAvailabilityBtn");
const closeCreateBtn = document.getElementById("closeCreateBtn");
const closeTasksBtn = document.getElementById("closeTasksBtn");
const closeEditBtn = document.getElementById("closeEditBtn");
const closeAvailabilityBtn = document.getElementById("closeAvailabilityBtn");

const toast = document.getElementById("toast");

let viewYear = new Date().getFullYear();
let viewMonth = new Date().getMonth();
let lastPlanByDate = new Map();
let completeUndoTimer = null;
let availabilityHoursByDate = new Map(); // isoDate -> hours (sum of available slots)
let defaultDailyCapacity = 4; // from profile.daily_capacity

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = "";
    try {
      const data = await res.json();
      detail = data?.detail ? `: ${data.detail}` : "";
    } catch (_) {
      // ignore parse errors
    }
    throw new Error(`Request failed (${res.status})${detail}`);
  }
  return res.json();
}

function pad(n) {
  return String(n).padStart(2, "0");
}

function toISODate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function setModal(open, which) {
  modalBackdrop.classList.toggle("hidden", !open);
  modalBackdrop.setAttribute("aria-hidden", open ? "false" : "true");
  if (which === "create") createModal.classList.toggle("hidden", !open);
  if (which === "tasks") tasksModal.classList.toggle("hidden", !open);
  if (which === "edit") editModal.classList.toggle("hidden", !open);
  if (which === "availability")
    availabilityModal.classList.toggle("hidden", !open);
}

function showToast(message, onUndo, ms = 8000) {
  toast.innerHTML = "";
  const span = document.createElement("span");
  span.textContent = message;
  toast.appendChild(span);
  if (onUndo) {
    const u = document.createElement("button");
    u.type = "button";
    u.className = "toastUndo";
    u.textContent = "Undo";
    u.onclick = () => {
      onUndo();
      hideToast();
    };
    toast.appendChild(u);
  }
  toast.classList.remove("hidden");
  clearTimeout(completeUndoTimer);
  completeUndoTimer = setTimeout(hideToast, ms);
}

function hideToast() {
  toast.classList.add("hidden");
  toast.innerHTML = "";
  clearTimeout(completeUndoTimer);
}

function monthMatrix(year, month) {
  const first = new Date(year, month, 1);
  const startWeekday = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < startWeekday; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d));
  while (cells.length % 7 !== 0) cells.push(null);
  while (cells.length < 42) cells.push(null);
  return cells;
}

function applyPlanPayload(plan) {
  warningsEl.innerHTML = "";
  if (plan.warnings?.length) {
    plan.warnings.forEach((warn) => {
      const p = document.createElement("p");
      p.className = "warning";
      p.textContent = warn;
      warningsEl.appendChild(p);
    });
  }
  explanationEl.textContent = plan.explanation || "";

  lastPlanByDate = new Map();
  (plan.schedule || []).forEach((day) => {
    lastPlanByDate.set(day.date, day.tasks || []);
  });
  renderCalendar();
}

async function fetchPlan() {
  const plan = await fetchJson("/plan");
  applyPlanPayload(plan);
}

function statusIcon(status) {
  if (status === "completed")
    return '<span class="badge done" title="Completed">✓</span>';
  if (status === "missed")
    return '<span class="badge missed" title="Missed">✗</span>';
  return "";
}

function formatHours(h) {
  if (Number.isInteger(h)) return `${h}`;
  return `${Math.round(h * 100) / 100}`;
}

function parseHHMMToHours(s) {
  const [hh, mm] = String(s || "").split(":");
  const h = Number(hh);
  const m = Number(mm);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
  return h + m / 60;
}

function recomputeAvailabilityHours(slots) {
  availabilityHoursByDate = new Map();
  (slots || []).forEach((s) => {
    if (s.type && s.type !== "available") return;
    if (s.available_hours != null) {
      const n = Number(s.available_hours);
      if (Number.isFinite(n) && n >= 0) {
        const prev = availabilityHoursByDate.get(s.date) || 0;
        availabilityHoursByDate.set(s.date, prev + n);
        return;
      }
    }
    const start = parseHHMMToHours(s.start_time);
    const end = parseHHMMToHours(s.end_time);
    if (start === null || end === null) return;
    if (end <= start) return;
    const dur = Math.max(0, Math.min(end, 24) - Math.max(start, 0));
    const prev = availabilityHoursByDate.get(s.date) || 0;
    availabilityHoursByDate.set(s.date, prev + dur);
  });
}

function availabilityForISO(iso) {
  if (availabilityHoursByDate.has(iso)) {
    const explicit = availabilityHoursByDate.get(iso);
    if (Number.isFinite(explicit) && explicit >= 0) return explicit;
  }
  return defaultDailyCapacity;
}

function consumedCompletedHoursForISO(iso) {
  const tasks = lastPlanByDate.get(iso) || [];
  return tasks.reduce((sum, t) => {
    if (t.status !== "completed") return sum;
    const completed = Number(t.completed_duration);
    if (Number.isFinite(completed) && completed > 0) return sum + completed;
    const assigned = Number(t.assigned_duration);
    if (Number.isFinite(assigned) && assigned > 0) return sum + assigned;
    return sum;
  }, 0);
}

function effectiveAvailabilityForISO(iso) {
  const base = Number(availabilityForISO(iso));
  const consumed = consumedCompletedHoursForISO(iso);
  if (!Number.isFinite(base)) return 0;
  return Math.max(0, base - consumed);
}

async function completeChunkWithPrompt(chunk) {
  const assigned = Number(chunk.assigned_duration);
  const opts = [
    { label: "0% (Missed)", value: 0 },
    { label: "25%", value: assigned * 0.25 },
    { label: "50%", value: assigned * 0.5 },
    { label: "75%", value: assigned * 0.75 },
    { label: "100% (Completed)", value: assigned },
  ];
  const msg =
    `How many hours did you complete for this block?\n` +
    `Assigned: ${formatHours(assigned)}h\n\n` +
    opts.map((o) => `- ${o.label}: ${formatHours(o.value)}h`).join("\n") +
    `\n\nEnter hours (e.g. 1.5) or leave empty (cancel):`;
  const raw = prompt(msg, `${formatHours(assigned)}`);
  if (raw === null) return null;
  const val = Number(raw);
  if (!Number.isFinite(val) || val < 0 || val > assigned + 1e-9) {
    showToast("You entered an invalid number of hours.");
    return null;
  }
  const res = await fetch(`/schedule-items/${chunk.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ completed_hours: val }),
  });
  return res.json();
}

function renderCalendar() {
  const monthNames = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ];
  monthLabel.textContent = `${monthNames[viewMonth]} ${viewYear}`;

  calendarGrid.innerHTML = "";
  const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const head = document.createElement("div");
  head.className = "calHead";
  weekdays.forEach((w) => {
    const c = document.createElement("div");
    c.className = "calHeadCell";
    c.textContent = w;
    head.appendChild(c);
  });
  calendarGrid.appendChild(head);

  const cells = monthMatrix(viewYear, viewMonth);
  const body = document.createElement("div");
  body.className = "calBody";
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  cells.forEach((cellDate) => {
    const cell = document.createElement("div");
    cell.className = "calCell";
    if (!cellDate) {
      cell.classList.add("calCellMuted");
      body.appendChild(cell);
      return;
    }
    const iso = toISODate(cellDate);
    const cmp = new Date(
      cellDate.getFullYear(),
      cellDate.getMonth(),
      cellDate.getDate(),
    );
    if (cmp < today) cell.classList.add("calPast");
    const dayNum = document.createElement("div");
    dayNum.className = "calDayNum";
    dayNum.textContent = String(cellDate.getDate());
    cell.appendChild(dayNum);

    const avail = document.createElement("div");
    avail.className = "calAvail";
    avail.textContent = `${formatHours(effectiveAvailabilityForISO(iso))}h available`;
    cell.appendChild(avail);

    const tasks = lastPlanByDate.get(iso) || [];
    if (!tasks.length) {
      body.appendChild(cell);
      return;
    }

    tasks.forEach((t) => {
      const row = document.createElement("div");
      row.className = "calChunk";
      const left = document.createElement("div");
      left.className = "calChunkMain";
      const assignedH = Number(t.assigned_duration);
      const completedH = Number(t.completed_duration);
      const hoursLabel =
        t.status === "completed" &&
        Number.isFinite(completedH) &&
        completedH + 1e-9 < assignedH
          ? `${formatHours(completedH)}h / ${formatHours(assignedH)}h`
          : `${formatHours(assignedH)}h`;
      left.innerHTML = `${statusIcon(t.status)} <span class="calChunkTitle">${t.task}</span> <span class="muted">${hoursLabel}</span>`;

      const actions = document.createElement("div");
      actions.className = "calChunkActions";

      if (t.status === "pending") {
        const complete = document.createElement("button");
        complete.type = "button";
        complete.textContent = "Complete...";
        complete.className = "success tiny";
        complete.onclick = async () => {
          const payload = await completeChunkWithPrompt(t);
          if (payload) applyPlanPayload(payload);
        };
        actions.appendChild(complete);
      } else {
        const undo = document.createElement("button");
        undo.type = "button";
        undo.textContent = "Undo";
        undo.className = "ghost tiny";
        undo.onclick = async () => {
          const res = await fetch(`/schedule-items/${t.id}/undo`, {
            method: "POST",
          });
          applyPlanPayload(await res.json());
        };
        actions.appendChild(undo);
      }

      row.appendChild(left);
      row.appendChild(actions);
      cell.appendChild(row);
    });

    body.appendChild(cell);
  });

  calendarGrid.appendChild(body);
}

async function fetchTasksForModal() {
  const tasks = await fetchJson("/tasks?include_completed=true");
  taskList.innerHTML = "";
  if (!tasks.length) {
    taskList.innerHTML = `<p class="empty">No tasks.</p>`;
    return;
  }
  tasks.forEach((task) => {
    const card = document.createElement("div");
    card.className = "taskCard";
    const doneLabel = task.completed ? " (completed)" : "";
    card.innerHTML = `
      <div class="taskHead">
        <strong>${task.title}</strong>
        <span>${task.deadline}</span>
      </div>
      <p class="muted">${task.total_duration}h planned | ${task.remaining_duration}h remaining | difficulty ${task.difficulty}${doneLabel}</p>
    `;

    const actions = document.createElement("div");
    actions.className = "rowActions";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.textContent = "Edit";
    editBtn.className = "ghost";
    editBtn.onclick = () => openEditModal(task);

    actions.appendChild(editBtn);

    if (!task.completed) {
      const doneBtn = document.createElement("button");
      doneBtn.type = "button";
      doneBtn.textContent = "Complete Task";
      doneBtn.className = "success";
      doneBtn.onclick = async () => {
        const prevRemaining = task.remaining_duration;
        await fetch(`/tasks/${task.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ completed: true }),
        });
        setModal(false, "tasks");
        showToast("Task completed.", async () => {
          await fetch(`/tasks/${task.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              completed: false,
              remaining_duration: prevRemaining,
            }),
          });
          await refreshPlanAndTasksStrip();
        });
        await refreshPlanAndTasksStrip();
      };
      actions.appendChild(doneBtn);
    }

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.textContent = "Delete";
    delBtn.className = "danger";
    delBtn.onclick = async () => {
      if (!confirm("Delete this task?")) return;
      await fetch(`/tasks/${task.id}`, { method: "DELETE" });
      await refreshPlanAndTasksStrip();
      await fetchTasksForModal();
    };
    actions.appendChild(delBtn);

    card.appendChild(actions);
    taskList.appendChild(card);
  });
}

async function refreshPlanAndTasksStrip() {
  const [profile, slots] = await Promise.all([
    fetchJson("/profile"),
    fetchJson("/availability"),
  ]);
  defaultDailyCapacity =
    Number(profile?.daily_capacity) || defaultDailyCapacity;
  if (defaultDailyCapacityInput)
    defaultDailyCapacityInput.value = `${defaultDailyCapacity}`;
  recomputeAvailabilityHours(slots);
  await fetchPlan();
}

openCreateBtn.addEventListener("click", () => {
  taskForm.reset();
  setModal(true, "create");
});
closeCreateBtn.addEventListener("click", () => setModal(false, "create"));

openTasksBtn.addEventListener("click", async () => {
  await fetchTasksForModal();
  setModal(true, "tasks");
});
closeTasksBtn.addEventListener("click", () => setModal(false, "tasks"));

async function fetchAvailabilityForModal() {
  const slots = await fetchJson("/availability");
  availabilityList.innerHTML = "";
  const availableOnly = (slots || []).filter(
    (s) => !s.type || s.type === "available",
  );
  if (!availableOnly.length) {
    availabilityList.innerHTML = `<p class="empty">No slots. (If you did not enter available hours for this day, default capacity will be used.)</p>`;
    return;
  }
  availableOnly.forEach((s) => {
    const start = parseHHMMToHours(s.start_time);
    const end = parseHHMMToHours(s.end_time);
    const hrs =
      s.available_hours != null && Number.isFinite(Number(s.available_hours))
        ? Number(s.available_hours)
        : start !== null && end !== null
          ? Math.max(0, end - start)
          : null;
    const card = document.createElement("div");
    card.className = "taskCard";
    card.innerHTML = `
      <div class="taskHead">
        <strong>${s.date}</strong>
        <span>available</span>
      </div>
      <p class="muted">${hrs === null ? `${s.start_time} – ${s.end_time}` : `${formatHours(hrs)}h`}</p>
    `;
    const actions = document.createElement("div");
    actions.className = "rowActions";
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.textContent = "Delete";
    delBtn.className = "danger";
    delBtn.onclick = async () => {
      await fetch(`/availability/${s.id}`, { method: "DELETE" });
      await fetchAvailabilityForModal();
      await refreshPlanAndTasksStrip();
    };
    actions.appendChild(delBtn);
    card.appendChild(actions);
    availabilityList.appendChild(card);
  });
}

openAvailabilityBtn.addEventListener("click", async () => {
  availabilityForm.reset();
  await fetchAvailabilityForModal();
  try {
    const profile = await fetchJson("/profile");
    defaultDailyCapacity =
      Number(profile?.daily_capacity) || defaultDailyCapacity;
    if (defaultDailyCapacityInput)
      defaultDailyCapacityInput.value = `${defaultDailyCapacity}`;
  } catch (_) {
    // ignore; keep last known default
  }
  setModal(true, "availability");
});
closeAvailabilityBtn.addEventListener("click", () =>
  setModal(false, "availability"),
);

if (defaultAvailabilityForm) {
  defaultAvailabilityForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const raw = defaultDailyCapacityInput
        ? defaultDailyCapacityInput.value
        : "";
      const val = Number(raw);
      if (!Number.isFinite(val) || val <= 0 || val > 24) {
        showToast("Default availability must be between 0 and 24 hours.");
        return;
      }
      await fetchJson("/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ daily_capacity: val }),
      });
      defaultDailyCapacity = val;
      await refreshPlanAndTasksStrip();
      showToast("Default availability updated.");
    } catch (err) {
      showToast(`Could not update. ${err.message}`);
    }
  });
}

availabilityForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const formData = new FormData(availabilityForm);
    const payload = Object.fromEntries(formData.entries());
    if (payload.available_hours != null)
      payload.available_hours = Number(payload.available_hours);
    await fetchJson("/availability", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    availabilityForm.reset();
    await fetchAvailabilityForModal();
    await refreshPlanAndTasksStrip();
    showToast("Availability slot added.");
  } catch (err) {
    showToast(`Could not add slot. ${err.message}`);
  }
});

modalBackdrop.addEventListener("click", () => {
  setModal(false, "create");
  setModal(false, "tasks");
  setModal(false, "edit");
  setModal(false, "availability");
});

function openEditModal(task) {
  document.getElementById("editTaskId").value = task.id;
  document.getElementById("editTitleInput").value = task.title;
  document.getElementById("editDeadlineInput").value = task.deadline;
  document.getElementById("editHoursInput").value = task.total_duration;
  document.getElementById("editDifficultyInput").value = task.difficulty;
  setModal(false, "tasks");
  setModal(true, "edit");
}

closeEditBtn.addEventListener("click", () => setModal(false, "edit"));

editForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("editTaskId").value;
  const payload = {
    title: document.getElementById("editTitleInput").value,
    deadline: document.getElementById("editDeadlineInput").value,
    total_duration: Number(document.getElementById("editHoursInput").value),
    difficulty: Number(document.getElementById("editDifficultyInput").value),
  };
  await fetch(`/tasks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setModal(false, "edit");
  await refreshPlanAndTasksStrip();
});

taskForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const formData = new FormData(taskForm);
    const payload = Object.fromEntries(formData.entries());
    payload.total_duration = Number(payload.total_duration);
    payload.difficulty = Number(payload.difficulty);
    await fetchJson("/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    taskForm.reset();
    setModal(false, "create");
    await refreshPlanAndTasksStrip();
    showToast("Task created.");
  } catch (err) {
    showToast(`Could not create task. ${err.message}`);
  }
});

rescheduleBtn.addEventListener("click", async () => {
  await fetch("/reschedule", { method: "POST" });
  await refreshPlanAndTasksStrip();
});

prevMonthBtn.addEventListener("click", () => {
  viewMonth -= 1;
  if (viewMonth < 0) {
    viewMonth = 11;
    viewYear -= 1;
  }
  renderCalendar();
});

nextMonthBtn.addEventListener("click", () => {
  viewMonth += 1;
  if (viewMonth > 11) {
    viewMonth = 0;
    viewYear += 1;
  }
  renderCalendar();
});

refreshPlanAndTasksStrip();
