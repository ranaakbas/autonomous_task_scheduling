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
const pendingUndoByChunkId = new Map(); // chunkId -> undoFn, for calendar-level undo
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
  /* ms=0 means no auto-dismiss (indefinite, user must manually close) */
  if (ms > 0) {
    completeUndoTimer = setTimeout(hideToast, ms);
  }
}

function hideToast() {
  toast.classList.add("hidden");
  toast.innerHTML = "";
  clearTimeout(completeUndoTimer);
}

/**
 * Themed confirm dialog — replaces browser confirm().
 * Returns a Promise<boolean>.
 */
function showConfirm({
  eyebrow = "Confirm action",
  title = "Are you sure?",
  badge = null,
  message = "",
  okLabel = "Delete",
  cancelLabel = "Cancel",
} = {}) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "confirmBackdrop";
    backdrop.innerHTML = `
      <div class="confirmPanel" role="dialog" aria-modal="true" aria-label="${title}">
        <div class="confirmHeader">
          <div class="confirmHeaderLeft">
            <div class="confirmEyebrow">${eyebrow}</div>
            <div class="confirmTitle">${title}</div>
          </div>
          <button type="button" class="confirmCloseBtn" aria-label="${cancelLabel}">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <path d="M1 1l8 8M9 1L1 9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
        ${
          badge
            ? `
        <div class="confirmMeta">
          <span class="confirmDangerBadge">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
              <path d="M10 11v6M14 11v6"/>
            </svg>
            ${badge}
          </span>
        </div>`
            : ""
        }
        ${
          message
            ? `
        <div class="confirmBody">
          <p class="confirmMessage">${message}</p>
        </div>`
            : ""
        }
        <div class="confirmFooter">
          <button type="button" class="confirmCancelBtn">${cancelLabel}</button>
          <button type="button" class="confirmOkBtn">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
              <path d="M10 11v6M14 11v6"/>
            </svg>
            ${okLabel}
          </button>
        </div>
      </div>
    `;
    document.body.appendChild(backdrop);

    const cancelBtn = backdrop.querySelector(".confirmCancelBtn");
    const closeBtn = backdrop.querySelector(".confirmCloseBtn");
    const okBtn = backdrop.querySelector(".confirmOkBtn");

    function close(result) {
      backdrop.style.opacity = "0";
      backdrop.style.transition = "opacity .15s ease";
      setTimeout(() => backdrop.remove(), 160);
      resolve(result);
    }

    okBtn.addEventListener("click", () => close(true));
    cancelBtn.addEventListener("click", () => close(false));
    closeBtn.addEventListener("click", () => close(false));
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) close(false);
    });
    document.addEventListener("keydown", function onKey(e) {
      if (e.key === "Escape") {
        document.removeEventListener("keydown", onKey);
        close(false);
      }
      if (e.key === "Enter") {
        document.removeEventListener("keydown", onKey);
        close(true);
      }
    });

    requestAnimationFrame(() => okBtn.focus());
  });
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

/** Sum of (logged − planned) hours for completed chunks where logged exceeded the block (overstudy). */
function overstudyExtraHoursForISO(iso) {
  const tasks = lastPlanByDate.get(iso) || [];
  let extra = 0;
  for (const t of tasks) {
    if (t.status !== "completed") continue;
    const completed = Number(t.completed_duration);
    const assigned = Number(t.assigned_duration);
    if (!Number.isFinite(completed) || !Number.isFinite(assigned)) continue;
    if (completed > assigned + 1e-9) extra += completed - assigned;
  }
  return extra;
}

/** Upper bound for hours logged in one completion (typo guard). */
const MAX_COMPLETE_SESSION_HOURS = 24;

function showCompleteModal(chunk) {
  return new Promise((resolve) => {
    const assigned = Number(chunk.assigned_duration);
    const remainingOnTask = Number(chunk.task_remaining_duration);
    const remainingSafe = Number.isFinite(remainingOnTask)
      ? Math.max(0, remainingOnTask)
      : 0;
    const isBalanced =
      chunk.daily_target_hours != null &&
      Number.isFinite(Number(chunk.daily_target_hours));
    const dailyTarget = isBalanced
      ? Number(chunk.daily_target_hours)
      : assigned;

    // For balanced: quick options are 0% (Missed) and the daily target (Done)
    // For others: 0% / 50% / 100% of assigned
    const opts = isBalanced
      ? [
          { pct: "0%", label: "Missed", value: 0, cls: "cm-zero" },
          { pct: "100%", label: "Done", value: dailyTarget, cls: "cm-full" },
        ]
      : [
          { pct: "0%", label: "Missed", value: 0, cls: "cm-zero" },
          { pct: "50%", label: "", value: assigned * 0.5, cls: "" },
          { pct: "100%", label: "Done", value: assigned, cls: "cm-full" },
        ];

    const backdrop = document.createElement("div");
    backdrop.className = "completeModalBackdrop";
    backdrop.innerHTML = `
      <div class="completeModalPanel" role="dialog" aria-modal="true" aria-label="Complete block">
        <div class="cmHeader">
          <div class="cmHeaderLeft">
            <div class="cmEyebrow">Mark as complete</div>
            <div class="cmTitle">${chunk.task}</div>
          </div>
          <button type="button" class="cmCloseBtn" aria-label="Cancel">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <path d="M1 1l8 8M9 1L1 9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
        <div class="cmMeta">
          <span class="cmAssignedBadge">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>
            </svg>
            Assigned: ${formatHours(assigned)}h
          </span>
          ${
            !isBalanced && remainingSafe > 1e-9
              ? `<span class="cmRemainingBadge">Task remaining: ${formatHours(remainingSafe)}h</span>`
              : ""
          }
        </div>
        <p class="cmHint muted">${
          isBalanced
            ? `Target: <strong>${formatHours(dailyTarget)}h</strong> daily. If you reach this amount, the day counts as Done and the task moves to the next day.`
            : "You can log more than assigned (overstudy). Extra hours reduce this task&rsquo;s remaining time and the schedule rebuilds from what is left."
        }</p>
        <div class="cmBody">
          <div class="cmQuickLabel">Quick select</div>
          <div class="cmQuickGrid">
            ${opts
              .map(
                (o, i) => `
              <button type="button" class="cmQuickBtn ${o.cls}" data-idx="${i}" data-val="${o.value}">
                <span class="cmQuickPct">${o.pct}</span>
                <span class="cmQuickHrs">${o.label || formatHours(o.value) + "h"}</span>
              </button>
            `,
              )
              .join("")}
          </div>
          <div class="cmInputLabel">Or enter hours manually</div>
          <div class="cmInputRow">
            <div class="cmInputWrap">
              <input type="number" class="cmNumberInput" min="0" max="${MAX_COMPLETE_SESSION_HOURS}" step="0.25"
                value="${formatHours(isBalanced ? dailyTarget : assigned)}" inputmode="decimal" autocomplete="off">
              <span class="cmInputUnit">h</span>
            </div>
          </div>
        </div>
        <div class="cmFooter">
          <button type="button" class="cmCancelBtn">Cancel</button>
          <button type="button" class="cmConfirmBtn">
            <svg width="13" height="13" viewBox="0 0 20 20" fill="none">
              <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="1.6"/>
              <path d="M6.5 10.5l2.5 2.5L14 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            Confirm
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(backdrop);

    const numberInput = backdrop.querySelector(".cmNumberInput");
    const quickBtns = backdrop.querySelectorAll(".cmQuickBtn");
    const confirmBtn = backdrop.querySelector(".cmConfirmBtn");
    const cancelBtn = backdrop.querySelector(".cmCancelBtn");
    const closeBtn = backdrop.querySelector(".cmCloseBtn");

    // Sync quick buttons with input value
    function syncQuickBtns(val) {
      quickBtns.forEach((btn) => {
        const bVal = Number(btn.dataset.val);
        btn.classList.toggle("cm-selected", Math.abs(bVal - val) < 1e-9);
      });
    }
    syncQuickBtns(isBalanced ? dailyTarget : assigned);

    quickBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = Number(btn.dataset.val);
        numberInput.value = formatHours(v);
        syncQuickBtns(v);
        numberInput.focus();
      });
    });

    numberInput.addEventListener("input", () => {
      syncQuickBtns(Number(numberInput.value));
    });

    function close(result) {
      backdrop.style.opacity = "0";
      backdrop.style.transition = "opacity .15s ease";
      setTimeout(() => backdrop.remove(), 160);
      resolve(result);
    }

    confirmBtn.addEventListener("click", () => close(numberInput.value));
    cancelBtn.addEventListener("click", () => close(null));
    closeBtn.addEventListener("click", () => close(null));
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) close(null);
    });
    document.addEventListener("keydown", function onKey(e) {
      if (e.key === "Escape") {
        document.removeEventListener("keydown", onKey);
        close(null);
      }
      if (e.key === "Enter") {
        document.removeEventListener("keydown", onKey);
        close(numberInput.value);
      }
    });

    // Focus input
    requestAnimationFrame(() => {
      numberInput.focus();
      numberInput.select();
    });
  });
}

async function completeChunkWithPrompt(chunk) {
  const raw = await showCompleteModal(chunk);
  if (raw === null) return null;
  const val = Number(raw);
  if (
    !Number.isFinite(val) ||
    val < 0 ||
    val > MAX_COMPLETE_SESSION_HOURS + 1e-9
  ) {
    showToast(
      `Enter hours between 0 and ${MAX_COMPLETE_SESSION_HOURS} (extra study counts toward remaining time).`,
    );
    return null;
  }
  const res = await fetch(`/schedule-items/${chunk.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ completed_hours: val }),
  });
  const payload = await res.json();

  /* Store undo callback in map — renderCalendar will inject the button into the chunk */
  const chunkId = chunk.id;
  pendingUndoByChunkId.set(chunkId, async function () {
    pendingUndoByChunkId.delete(chunkId);
    await fetch("/schedule-items/" + chunkId + "/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    await refreshPlanAndTasksStrip();
  });

  return payload;
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
    const availMain = document.createElement("span");
    availMain.className = "calAvailMain";
    availMain.textContent = `${formatHours(effectiveAvailabilityForISO(iso))}h available`;
    avail.appendChild(availMain);
    const overExtra = overstudyExtraHoursForISO(iso);
    if (overExtra > 1e-9) {
      const note = document.createElement("span");
      note.className = "calOverstudyNote";
      note.textContent = `+${formatHours(overExtra)}h overstudy`;
      note.title =
        "You logged more than planned on this block; extra time counts toward the task.";
      avail.appendChild(note);
    }
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
      const hoursMismatch =
        t.status === "completed" &&
        Number.isFinite(completedH) &&
        (completedH + 1e-9 < assignedH || completedH > assignedH + 1e-9);
      const hoursLabel = hoursMismatch
        ? `${formatHours(completedH)}h / ${formatHours(assignedH)}h planned`
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
      }

      const canUndoFromStatus =
        (t.status === "completed" || t.status === "missed") &&
        !t.task_done_locked &&
        (Boolean(t.undoable) || pendingUndoByChunkId.has(t.id));
      if (canUndoFromStatus) {
        const undoFn = pendingUndoByChunkId.get(t.id);
        const undoBtn = document.createElement("button");
        undoBtn.type = "button";
        undoBtn.textContent = "Undo";
        undoBtn.className = "calChunkUndo";
        undoBtn.onclick = async () => {
          if (undoFn) {
            await undoFn();
            return;
          }
          const res = await fetch("/schedule-items/" + t.id + "/undo", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          });
          if (!res.ok) {
            showToast("Undo not available for this item.");
            return;
          }
          await refreshPlanAndTasksStrip();
        };
        actions.appendChild(undoBtn);
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
  const todayISO = toISODate(new Date());
  const sortedTasks = [...tasks].sort((a, b) => {
    const aOverdue = !a.completed && a.deadline < todayISO;
    const bOverdue = !b.completed && b.deadline < todayISO;
    if (aOverdue !== bOverdue) return aOverdue ? -1 : 1;
    return String(a.deadline).localeCompare(String(b.deadline));
  });
  sortedTasks.forEach((task) => {
    const isOverdue = !task.completed && task.deadline < todayISO;
    const card = document.createElement("div");
    card.className = "taskCard";
    if (isOverdue) card.classList.add("taskCardOverdue");
    const doneLabel = task.completed ? " (completed)" : "";
    const overdueBanner = isOverdue
      ? `<span class="taskDeadlineBanner">Past deadline</span>`
      : "";
    card.innerHTML = `
      <div class="taskHead">
        <strong>${task.title}</strong>
        <div class="taskMeta">
          <span class="taskDeadline">${task.deadline}</span>
          ${overdueBanner}
        </div>
      </div>
      <p class="muted">${
        task.work_style === "balanced" && task.daily_target_hours
          ? `${task.daily_target_hours}h/day | difficulty ${task.difficulty}${doneLabel}`
          : `${task.total_duration}h planned | ${task.remaining_duration}h remaining | difficulty ${task.difficulty}${doneLabel}`
      }</p>
    `;

    const actions = document.createElement("div");
    actions.className = "rowActions";

    if (!task.completed) {
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.textContent = "Edit";
      editBtn.className = "ghost";
      editBtn.onclick = () => openEditModal(task);
      actions.appendChild(editBtn);
    }

    if (!task.completed) {
      const doneBtn = document.createElement("button");
      doneBtn.type = "button";
      doneBtn.textContent = "Complete Task";
      doneBtn.className = "success";
      doneBtn.onclick = async () => {
        // Task-level completion should not expose chunk-level undo for this task.
        for (const dayTasks of lastPlanByDate.values()) {
          for (const chunk of dayTasks || []) {
            if (chunk.task_id === task.id)
              pendingUndoByChunkId.delete(chunk.id);
          }
        }
        await fetch(`/tasks/${task.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ completed: true }),
        });
        setModal(false, "tasks");
        await refreshPlanAndTasksStrip();
      };
      actions.appendChild(doneBtn);
    } else {
      const undoBtn = document.createElement("button");
      undoBtn.type = "button";
      undoBtn.textContent = "Undo Complete";
      undoBtn.className = "ghost";
      undoBtn.onclick = async () => {
        setModal(false, "tasks");
        await fetch(`/tasks/${task.id}/undo-complete`, { method: "POST" });
        await refreshPlanAndTasksStrip();
      };
      actions.appendChild(undoBtn);
    }

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.textContent = "Delete";
    delBtn.className = "danger";
    delBtn.onclick = async () => {
      const ok = await showConfirm({
        eyebrow: "Delete task",
        title: task.title,
        badge: "This action cannot be undone",
        message:
          "The task and all its scheduled blocks will be permanently removed from your plan.",
        okLabel: "Delete",
        cancelLabel: "Cancel",
      });
      if (!ok) return;
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

  const todayStr = toISODate(new Date());
  const availableOnly = (slots || []).filter(
    (s) => (!s.type || s.type === "available") && s.date >= todayStr,
  );

  if (!availableOnly.length) {
    availabilityList.innerHTML = `<p class="avEmpty">No upcoming slots — days without a slot use the default capacity.</p>`;
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

    const hrsLabel =
      hrs === null ? `${s.start_time}–${s.end_time}` : `${formatHours(hrs)}h`;

    const card = document.createElement("div");
    card.className = "avSlotCard";
    card.innerHTML = `
      <div class="avSlotCardLeft">
        <span class="avSlotDot"></span>
        <span class="avSlotDate">${s.date}</span>
        <span class="avSlotHours">available</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <span class="avSlotHoursBadge">${hrsLabel}</span>
        <button type="button" class="avSlotDeleteBtn" aria-label="Delete slot">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
            <path d="M10 11v6M14 11v6"/>
          </svg>
          Delete
        </button>
      </div>
    `;

    card.querySelector(".avSlotDeleteBtn").onclick = async () => {
      const ok = await showConfirm({
        eyebrow: "Delete availability slot",
        title: s.date,
        badge: "This action cannot be undone",
        message:
          "This availability slot will be removed and the schedule will be recalculated.",
        okLabel: "Delete",
        cancelLabel: "Cancel",
      });
      if (!ok) return;
      await fetch(`/availability/${s.id}`, { method: "DELETE" });
      await fetchAvailabilityForModal();
      await refreshPlanAndTasksStrip();
    };

    availabilityList.appendChild(card);
  });
}

openAvailabilityBtn.addEventListener("click", async () => {
  availabilityForm.reset();

  // Set today as the minimum selectable date for both date inputs
  const todayStr = toISODate(new Date());
  const avSingleDate = document.getElementById("avSingleDate");
  const avRepeatFrom = document.getElementById("avRepeatFrom");
  const avRepeatTo = document.getElementById("avRepeatTo");
  if (avSingleDate) {
    avSingleDate.min = todayStr;
    avSingleDate.value = "";
  }
  if (avRepeatFrom) {
    avRepeatFrom.min = todayStr;
    avRepeatFrom.value = "";
  }
  if (avRepeatTo) {
    avRepeatTo.min = todayStr;
    avRepeatTo.value = "";
  }

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
    // Allow 0 — only reject truly non-numeric
    const raw = payload.available_hours;
    payload.available_hours = raw !== "" && raw != null ? Number(raw) : null;
    await fetchJson("/availability", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    availabilityForm.reset();
    // re-apply min after reset
    const todayStr = toISODate(new Date());
    const avSingleDate = document.getElementById("avSingleDate");
    if (avSingleDate) avSingleDate.min = todayStr;
    await fetchAvailabilityForModal();
    await refreshPlanAndTasksStrip();
    showToast("Availability slot added.");
  } catch (err) {
    showToast(`Could not add slot. ${err.message}`);
  }
});

/* ── Tab switcher ── */
(function initAvTabs() {
  const tabs = document.querySelectorAll(".avTab");
  const panes = document.querySelectorAll(".avTabPane");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      panes.forEach((p) => p.classList.add("hidden"));
      tab.classList.add("active");
      const target = tab.dataset.tab;
      document
        .querySelector(`.avTabPane[data-pane="${target}"]`)
        ?.classList.remove("hidden");
    });
  });
})();

/* ── Day-of-week picker (repeat form) ── */
let avSelectedDay = null;
document.querySelectorAll(".avDayBtn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document
      .querySelectorAll(".avDayBtn")
      .forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    avSelectedDay = Number(btn.dataset.day);
    updateRepeatPreview();
  });
});

function updateRepeatPreview() {
  const preview = document.getElementById("avRepeatPreview");
  if (!preview) return;
  const fromVal = document.getElementById("avRepeatFrom")?.value;
  const toVal = document.getElementById("avRepeatTo")?.value;
  const hoursVal = document.getElementById("avRepeatHours")?.value;
  if (avSelectedDay === null || !fromVal || !toVal) {
    preview.textContent = "";
    return;
  }
  const dates = getRepeatDates(avSelectedDay, fromVal, toVal);
  if (!dates.length) {
    preview.textContent = "No matching dates in range.";
    return;
  }
  const hrs = hoursVal !== "" && hoursVal != null ? Number(hoursVal) : "?";
  preview.textContent = `${dates.length} slot${dates.length > 1 ? "s" : ""} will be added (${hrs}h each): ${dates.slice(0, 4).join(", ")}${dates.length > 4 ? ` … +${dates.length - 4} more` : ""}`;
}

["avRepeatFrom", "avRepeatTo", "avRepeatHours"].forEach((id) => {
  document.getElementById(id)?.addEventListener("input", updateRepeatPreview);
});

function getRepeatDates(dayOfWeek, fromStr, toStr) {
  const result = [];
  const from = new Date(fromStr + "T00:00:00");
  const to = new Date(toStr + "T00:00:00");
  if (isNaN(from) || isNaN(to) || from > to) return result;
  const cur = new Date(from);
  while (cur <= to) {
    if (cur.getDay() === dayOfWeek) {
      result.push(toISODate(cur));
    }
    cur.setDate(cur.getDate() + 1);
  }
  return result;
}

const availabilityRepeatForm = document.getElementById(
  "availabilityRepeatForm",
);
if (availabilityRepeatForm) {
  availabilityRepeatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (avSelectedDay === null) {
      showToast("Please select a day of the week.");
      return;
    }
    const fromVal = document.getElementById("avRepeatFrom").value;
    const toVal = document.getElementById("avRepeatTo").value;
    const hoursRaw = document.getElementById("avRepeatHours").value;
    const hrs = hoursRaw !== "" ? Number(hoursRaw) : 0;

    const todayStr = toISODate(new Date());
    if (fromVal < todayStr || toVal < todayStr) {
      showToast("Dates cannot be in the past.");
      return;
    }
    const dates = getRepeatDates(avSelectedDay, fromVal, toVal);
    if (!dates.length) {
      showToast("No matching dates in the selected range.");
      return;
    }

    const submitBtn = document.getElementById("avRepeatSubmitBtn");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Adding…";
    }

    try {
      let added = 0;
      for (const date of dates) {
        await fetchJson("/availability", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            date,
            available_hours: hrs,
            type: "available",
          }),
        });
        added++;
      }
      availabilityRepeatForm.reset();
      document
        .querySelectorAll(".avDayBtn")
        .forEach((b) => b.classList.remove("active"));
      avSelectedDay = null;
      const preview = document.getElementById("avRepeatPreview");
      if (preview) preview.textContent = "";
      // re-apply min
      const todayStrNow = toISODate(new Date());
      const avRepeatFrom = document.getElementById("avRepeatFrom");
      const avRepeatTo = document.getElementById("avRepeatTo");
      if (avRepeatFrom) avRepeatFrom.min = todayStrNow;
      if (avRepeatTo) avRepeatTo.min = todayStrNow;

      await fetchAvailabilityForModal();
      await refreshPlanAndTasksStrip();
      showToast(`${added} recurring slot${added > 1 ? "s" : ""} added.`);
    } catch (err) {
      showToast(`Could not add slots. ${err.message}`);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Add recurring slots";
      }
    }
  });
}

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
  setModal(false, "create");
  try {
    const formData = new FormData(taskForm);
    const payload = Object.fromEntries(formData.entries());
    payload.difficulty = Number(payload.difficulty);
    payload.work_style = payload.work_style || "intensive";

    if (payload.work_style === "balanced") {
      // Balanced: send daily_target_hours, not total_duration
      payload.daily_target_hours = Number(payload.daily_target_hours);
      delete payload.total_duration;
    } else {
      payload.total_duration = Number(payload.total_duration);
      delete payload.daily_target_hours;
    }

    await fetchJson("/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    taskForm.reset();
    // Reset work style picker to default (intensive)
    document
      .querySelectorAll(".workStyleBtn")
      .forEach((b) => b.classList.remove("active"));
    const defaultStyleBtn = document.querySelector(
      '.workStyleBtn[data-style="intensive"]',
    );
    if (defaultStyleBtn) defaultStyleBtn.classList.add("active");
    if (document.getElementById("workStyleValue"))
      document.getElementById("workStyleValue").value = "intensive";
    // Reset hours field visibility
    const totalField = document.getElementById("cf-total-hours-field");
    const dailyField = document.getElementById("cf-daily-hours-field");
    if (totalField) {
      totalField.style.display = "";
      document.getElementById("cf-hours").required = true;
    }
    if (dailyField) {
      dailyField.style.display = "none";
      document.getElementById("cf-daily-hours").required = false;
    }
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
