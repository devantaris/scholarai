/**
 * Scholar Pulse — Interactive Frontend Application Logic
 * Pure ES6 Vanilla JavaScript (No Frameworks, No External Dependencies)
 */

'use strict';

// ============================================================================
// 1. Central Application State
// ============================================================================
const AppState = {
  // Navigation
  selectedStudentId: 'aarav',
  currentView: 'student', // 'student' | 'cohort'

  // Server & Environment
  currentMode: { llm: 'template', sim_now: '2026-11-18T10:00:00+00:00' },

  // Data Stores
  studentsList: [],
  studentDetail: null,    // { student, roadmap, events, signals }
  interventions: [],      // list[Intervention]
  latestIntervention: null,
  cohortData: [],         // list[CohortStudentItem]
  metricsData: null,      // MetricsResponse

  // Async In-Flight Flags
  isLoading: false,
  isEvaluating: false,
  isAdvancing: false,
  isResetting: false
};

// ============================================================================
// 2. API Service Layer
// ============================================================================
const API = {
  async getMode() {
    const res = await fetch('/api/mode');
    if (!res.ok) throw new Error(`Mode fetch failed: ${res.status}`);
    return res.json();
  },

  async getStudents() {
    const res = await fetch('/api/students');
    if (!res.ok) throw new Error(`Students list fetch failed: ${res.status}`);
    return res.json();
  },

  async getStudentDetail(studentId) {
    const res = await fetch(`/api/students/${encodeURIComponent(studentId)}`);
    if (!res.ok) throw new Error(`Student detail fetch failed: ${res.status}`);
    return res.json();
  },

  async evaluateStudent(studentId) {
    const res = await fetch(`/api/students/${encodeURIComponent(studentId)}/evaluate`, {
      method: 'POST'
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      const msg = typeof errData.detail === 'string' ? errData.detail : `Evaluation failed: ${res.status}`;
      throw new Error(msg);
    }
    return res.json();
  },

  async getInterventions(studentId) {
    const res = await fetch(`/api/students/${encodeURIComponent(studentId)}/interventions`);
    if (!res.ok) throw new Error(`Interventions fetch failed: ${res.status}`);
    return res.json();
  },

  async advanceSim(days) {
    const res = await fetch('/api/sim/advance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days: parseInt(days, 10) })
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      const msg = typeof errData.detail === 'string' ? errData.detail : `Advance failed: ${res.status}`;
      throw new Error(msg);
    }
    return res.json();
  },

  async resetSim() {
    const res = await fetch('/api/sim/reset', { method: 'POST' });
    if (!res.ok) throw new Error(`Reset failed: ${res.status}`);
    return res.json();
  },

  async getCohort() {
    const res = await fetch('/api/cohort');
    if (!res.ok) throw new Error(`Cohort fetch failed: ${res.status}`);
    return res.json();
  },

  async getMetrics() {
    const res = await fetch('/api/metrics');
    if (!res.ok) throw new Error(`Metrics fetch failed: ${res.status}`);
    return res.json();
  }
};

// ============================================================================
// 3. UI Helpers & Formatters
// ============================================================================
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatSimDate(isoString) {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = months[d.getUTCMonth()];
    const day = d.getUTCDate();
    const year = d.getUTCFullYear();
    const hours = String(d.getUTCHours()).padStart(2, '0');
    const mins = String(d.getUTCMinutes()).padStart(2, '0');
    return `${month} ${day}, ${year} • ${hours}:${mins} UTC`;
  } catch {
    return isoString;
  }
}

function formatShortDate(isoString) {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${months[d.getUTCMonth()]} ${d.getUTCDate()}`;
  } catch {
    return isoString;
  }
}

function showToast(message, type = 'info', duration = 3500) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${type === 'success' ? '✓' : type === 'danger' ? '✕' : type === 'warning' ? '⚠' : 'ℹ'}</span>
    <span class="toast-text">${escapeHtml(message)}</span>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

function showModal(title, bodyHtml) {
  const modal = document.getElementById('app-modal');
  const titleEl = document.getElementById('modal-title');
  const bodyEl = document.getElementById('modal-body');
  if (!modal || !titleEl || !bodyEl) return;

  titleEl.textContent = title;
  bodyEl.innerHTML = bodyHtml;
  modal.style.display = 'flex';
}

function closeModal() {
  const modal = document.getElementById('app-modal');
  if (modal) modal.style.display = 'none';
}

// ============================================================================
// 4. Rendering Pipelines
// ============================================================================

/** Render Top Navigation Bar & Action State */
function renderTopBar() {
  // 1. Student Selector Dropdown
  const selectEl = document.getElementById('student-select');
  if (selectEl && AppState.studentsList.length > 0) {
    const currentVal = AppState.selectedStudentId;
    selectEl.innerHTML = AppState.studentsList.map(s => {
      const riskText = s.risk_level.toUpperCase();
      return `<option value="${escapeHtml(s.id)}" ${s.id === currentVal ? 'selected' : ''}>${escapeHtml(s.name)} [${riskText}]</option>`;
    }).join('');
  }

  // 2. Risk Badge next to Dropdown
  const currentSummary = AppState.studentsList.find(s => s.id === AppState.selectedStudentId);
  const riskBadge = document.getElementById('student-risk-badge');
  if (riskBadge && currentSummary) {
    const r = currentSummary.risk_level.toLowerCase();
    riskBadge.className = `badge badge-risk-${r === 'high' ? 'high' : r === 'medium' ? 'medium' : r === 'low' ? 'low' : 'healthy'}`;
    riskBadge.textContent = `${currentSummary.risk_level} Risk`;
  }

  // 3. Mode Badge
  const modeBadge = document.getElementById('llm-mode-badge');
  const modeText = document.getElementById('llm-mode-text');
  const statusDot = modeBadge ? modeBadge.querySelector('.status-dot') : null;
  if (modeBadge && modeText) {
    const isLive = AppState.currentMode.llm === 'live';
    modeText.textContent = isLive ? 'LLM: live (Claude)' : 'LLM: template';
    if (statusDot) {
      statusDot.className = isLive ? 'status-dot live-pulse' : 'status-dot';
    }
  }

  // 4. Simulated Clock Display
  const clockEl = document.getElementById('sim-date-display');
  if (clockEl) {
    clockEl.textContent = formatSimDate(AppState.currentMode.sim_now);
  }

  // 5. Buttons Loading States
  const btnEval = document.getElementById('btn-evaluate');
  const btnAdv1 = document.getElementById('btn-advance-1');
  const btnAdv3 = document.getElementById('btn-advance-3');
  const btnReset = document.getElementById('btn-reset');

  if (btnEval) btnEval.disabled = AppState.isEvaluating || AppState.isAdvancing || AppState.isResetting;
  if (btnAdv1) btnAdv1.disabled = AppState.isEvaluating || AppState.isAdvancing || AppState.isResetting;
  if (btnAdv3) btnAdv3.disabled = AppState.isEvaluating || AppState.isAdvancing || AppState.isResetting;
  if (btnReset) btnReset.disabled = AppState.isEvaluating || AppState.isAdvancing || AppState.isResetting;

  // 6. View Switcher Tabs
  const tabStudent = document.getElementById('tab-student');
  const tabCohort = document.getElementById('tab-cohort');
  const studentView = document.getElementById('student-view-container');
  const cohortView = document.getElementById('cohort-view-container');

  if (tabStudent && tabCohort && studentView && cohortView) {
    const isStudent = AppState.currentView === 'student';
    tabStudent.classList.toggle('active', isStudent);
    tabCohort.classList.toggle('active', !isStudent);
    tabStudent.setAttribute('aria-selected', isStudent ? 'true' : 'false');
    tabCohort.setAttribute('aria-selected', !isStudent ? 'true' : 'false');
    studentView.style.display = isStudent ? 'flex' : 'none';
    cohortView.style.display = !isStudent ? 'flex' : 'none';
  }
}

/** Render Student Profile Header */
function renderStudentProfile() {
  if (!AppState.studentDetail || !AppState.studentDetail.student) return;
  const s = AppState.studentDetail.student;

  const nameEl = document.getElementById('student-name');
  const avatarLetter = document.getElementById('student-avatar-letter');
  const trackEl = document.getElementById('student-track');
  const consentEl = document.getElementById('student-consent-badge');
  const goalEl = document.getElementById('student-goal');
  const hoursEl = document.getElementById('student-hours');
  const capacityEl = document.getElementById('student-capacity');
  const nudgesEl = document.getElementById('student-nudge-count');

  if (nameEl) nameEl.textContent = s.name;
  if (avatarLetter) avatarLetter.textContent = s.name.charAt(0).toUpperCase();
  if (trackEl) trackEl.textContent = s.roadmap_goal;

  if (consentEl) {
    if (s.consent_nudges) {
      consentEl.className = 'badge badge-consent';
      consentEl.textContent = 'Consent: Granted';
    } else {
      consentEl.className = 'badge badge-consent opted-out';
      consentEl.textContent = 'Consent: Opted Out';
    }
  }

  if (goalEl) goalEl.textContent = `"${s.goal_prompt}"`;
  if (hoursEl) {
    const h0 = s.active_hours && s.active_hours.length > 0 ? s.active_hours[0] : 9;
    const h1 = s.active_hours && s.active_hours.length > 1 ? s.active_hours[1] : 22;
    hoursEl.textContent = `${h0}:00 - ${h1}:00`;
  }
  if (capacityEl) capacityEl.textContent = `${s.weekly_capacity_min} min/wk`;

  // Compute delivered nudges in the rolling 7-day window
  const now = new Date(AppState.simNow || (AppState.currentMode && AppState.currentMode.sim_now) || Date.now());
  const deliveredLast7Days = (AppState.interventions || []).filter(i => {
    if (!i.message || i.status === 'blocked' || i.status === 'deferred') return false;
    const ts = new Date(i.ts);
    const diffDays = (now - ts) / (1000 * 60 * 60 * 24);
    return diffDays >= 0 && diffDays <= 7;
  }).length;
  const nudgeCount = (AppState.interventions && AppState.interventions.length > 0)
    ? deliveredLast7Days
    : (s.weekly_nudge_count || 0);
  if (nudgesEl) nudgesEl.textContent = `${nudgeCount} / 3`;
}

/** Render 30-Day Activity Timeline */
function renderTimeline() {
  if (!AppState.studentDetail) return;
  const events = AppState.studentDetail.events || [];
  const simNow = new Date(AppState.currentMode.sim_now);
  const barsContainer = document.getElementById('timeline-bars-container');
  const inspectionBox = document.getElementById('timeline-inspection-box');
  const windowLabel = document.getElementById('timeline-window-label');

  if (windowLabel) {
    const startDate = new Date(simNow.getTime() - 29 * 24 * 3600 * 1000);
    windowLabel.textContent = `${formatShortDate(startDate)} – ${formatShortDate(simNow)}`;
  }

  // Summary counters
  let totalSessions = 0;
  let totalPasses = 0;
  let totalFails = 0;
  let totalMinutes = 0;

  // Build 30 discrete 24h buckets (Day -29 to Day 0)
  const buckets = [];
  for (let i = 29; i >= 0; i--) {
    const bucketStart = new Date(simNow.getTime() - (i + 1) * 24 * 3600 * 1000);
    const bucketEnd = new Date(simNow.getTime() - i * 24 * 3600 * 1000);
    buckets.push({
      index: 29 - i,
      dayOffset: -i,
      start: bucketStart,
      end: bucketEnd,
      sessions: 0,
      durationMin: 0,
      passes: 0,
      fails: 0,
      errors: [],
      problems: new Set()
    });
  }

  // Place events into corresponding bucket
  events.forEach(evt => {
    const evtTime = new Date(evt.ts);
    if (isNaN(evtTime.getTime())) return;

    if (evt.type === 'session') {
      totalSessions++;
      totalMinutes += (evt.duration_min || 0);
    } else if (evt.type === 'attempt_pass') {
      totalPasses++;
    } else if (evt.type === 'attempt_fail') {
      totalFails++;
    }

    for (const b of buckets) {
      if (evtTime >= b.start && evtTime < b.end) {
        if (evt.type === 'session') {
          b.sessions++;
          b.durationMin += (evt.duration_min || 0);
        } else if (evt.type === 'attempt_pass') {
          b.passes++;
          if (evt.problem_id) b.problems.add(evt.problem_id);
        } else if (evt.type === 'attempt_fail') {
          b.fails++;
          if (evt.problem_id) b.problems.add(evt.problem_id);
          if (evt.error_signature && !b.errors.includes(evt.error_signature)) {
            b.errors.push(evt.error_signature);
          }
        }
        break;
      }
    }
  });

  // Update summary stats in DOM
  const elSess = document.getElementById('timeline-total-sessions');
  const elPass = document.getElementById('timeline-total-passes');
  const elFail = document.getElementById('timeline-total-fails');
  const elMins = document.getElementById('timeline-total-minutes');
  if (elSess) elSess.textContent = totalSessions;
  if (elPass) elPass.textContent = totalPasses;
  if (elFail) elFail.textContent = totalFails;
  if (elMins) elMins.textContent = `${totalMinutes}m`;

  if (!barsContainer) return;
  barsContainer.innerHTML = '';

  // Find max activity for normalization
  const maxActivity = Math.max(1, ...buckets.map(b => b.passes + b.fails + b.sessions));

  buckets.forEach(b => {
    const col = document.createElement('div');
    col.className = 'day-bar-column';
    if (b.dayOffset >= -3 && b.fails > 0) {
      col.classList.add('stuck-window');
    }

    const totalAct = b.passes + b.fails + b.sessions;
    const heightPct = totalAct > 0 ? Math.min(100, Math.max(16, (totalAct / maxActivity) * 100)) : 6;

    const track = document.createElement('div');
    track.className = 'day-bar-track';
    track.style.height = `${heightPct}%`;

    if (totalAct === 0) {
      const seg = document.createElement('div');
      seg.className = 'bar-segment';
      seg.style.height = '100%';
      track.appendChild(seg);
    } else {
      if (b.sessions > 0) {
        const seg = document.createElement('div');
        seg.className = 'bar-segment bar-segment-session';
        seg.style.height = `${(b.sessions / totalAct) * 100}%`;
        track.appendChild(seg);
      }
      if (b.fails > 0) {
        const seg = document.createElement('div');
        seg.className = 'bar-segment bar-segment-fail';
        seg.style.height = `${(b.fails / totalAct) * 100}%`;
        track.appendChild(seg);
      }
      if (b.passes > 0) {
        const seg = document.createElement('div');
        seg.className = 'bar-segment bar-segment-pass';
        seg.style.height = `${(b.passes / totalAct) * 100}%`;
        track.appendChild(seg);
      }
    }

    const label = document.createElement('span');
    label.className = 'day-bar-label';
    label.textContent = b.dayOffset === 0 ? 'Today' : `${b.dayOffset}d`;

    col.appendChild(track);
    col.appendChild(label);

    // Hover & Click Inspection
    const inspectContent = () => {
      const dateStr = formatShortDate(b.end);
      const dayName = b.dayOffset === 0 ? 'Today' : `Day ${b.dayOffset}`;
      let errText = '';
      if (b.errors.length > 0) {
        errText = `<div style="margin-top: 4px; color: #fca5a5;"><strong>Errors:</strong> ${escapeHtml(b.errors.join(' | '))}</div>`;
      }
      let probText = '';
      if (b.problems.size > 0) {
        probText = `<span style="margin-left: 8px; color: #cbd5e1;">Target: ${escapeHtml(Array.from(b.problems).join(', '))}</span>`;
      }

      if (inspectionBox) {
        inspectionBox.innerHTML = `
          <div>
            <strong>📅 ${dateStr} (${dayName})</strong>:
            <span style="color: #38bdf8; margin-left: 6px;">${b.sessions} sessions (${b.durationMin}m)</span> •
            <span style="color: #10b981; margin-left: 4px;">${b.passes} passes</span> •
            <span style="color: #ef4444; margin-left: 4px;">${b.fails} fails</span>
            ${probText}
            ${errText}
          </div>
        `;
      }
    };

    col.addEventListener('mouseenter', inspectContent);
    col.addEventListener('click', inspectContent);

    barsContainer.appendChild(col);
  });
}

/** Render Current Learning Roadmap */
function renderRoadmap() {
  if (!AppState.studentDetail) return;
  const roadmap = AppState.studentDetail.roadmap || [];
  const listEl = document.getElementById('roadmap-list');
  const countBadge = document.getElementById('roadmap-count-badge');
  if (!listEl) return;

  if (countBadge) countBadge.textContent = `${roadmap.length} items`;

  // Identify target problem if student is stuck
  let targetProblemId = null;
  const signals = AppState.studentDetail.signals || [];
  const stuckSignal = signals.find(s => s.name === 'stuck' || s.name === 'repeated_failure');
  if (stuckSignal && stuckSignal.triggered) {
    // Check if evidence mentions a problem ID
    const match = stuckSignal.evidence && stuckSignal.evidence.match(/P-\d+/);
    if (match) targetProblemId = match[0];
  }
  if (!targetProblemId && AppState.latestIntervention && AppState.latestIntervention.target_problem_id) {
    targetProblemId = AppState.latestIntervention.target_problem_id;
  }

  // Group by week
  const weeks = {};
  roadmap.forEach(item => {
    const w = item.week || 1;
    if (!weeks[w]) weeks[w] = [];
    weeks[w].push(item);
  });

  const sortedWeeks = Object.keys(weeks).map(Number).sort((a, b) => a - b);

  if (sortedWeeks.length === 0) {
    listEl.innerHTML = '<div class="diff-empty-notice">No roadmap items scheduled.</div>';
    return;
  }

  listEl.innerHTML = sortedWeeks.map(w => {
    const items = weeks[w];
    // Sort items within week so prerequisite concepts are placed before the problems they are prerequisites for
    items.sort((a, b) => {
      if (b.prereq_concept_ids && b.prereq_concept_ids.includes(a.id)) return -1;
      if (a.prereq_concept_ids && a.prereq_concept_ids.includes(b.id)) return 1;
      const typePriority = { concept: 0, problem: 1, project: 2 };
      const pA = typePriority[a.type] !== undefined ? typePriority[a.type] : 1;
      const pB = typePriority[b.type] !== undefined ? typePriority[b.type] : 1;
      if (pA !== pB) return pA - pB;
      return String(a.id).localeCompare(String(b.id));
    });

    const itemsHtml = items.map(item => {
      const isTarget = Boolean(targetProblemId && item.id === targetProblemId && item.status !== 'done');
      const typeTagClass = `tag-${item.type}`;
      const statusTagClass = `tag-status-${item.status}`;
      const statusLabel = item.status === 'done' ? '✓ Completed' : item.status === 'in_progress' ? '⏳ In Progress' : '○ Planned';

      let prereqsHtml = '';
      if (item.prereq_concept_ids && item.prereq_concept_ids.length > 0) {
        prereqsHtml = `
          <div class="roadmap-prereqs">
            <span>Prereqs:</span>
            <span>${escapeHtml(item.prereq_concept_ids.join(', '))}</span>
          </div>
        `;
      }

      return `
        <div class="roadmap-item-card ${isTarget ? 'is-target-stuck' : ''}">
          <div class="roadmap-item-header">
            <div class="roadmap-item-title-wrap">
              <span class="roadmap-item-id">${escapeHtml(item.id)}</span>
              <span class="roadmap-item-title">${escapeHtml(item.title)}</span>
            </div>
            <span class="tag ${statusTagClass}">${statusLabel}</span>
          </div>
          <div class="roadmap-tags">
            <span class="tag ${typeTagClass}">${item.type}</span>
            <span class="tag" style="background-color: rgba(255,255,255,0.06); color: #cbd5e1;">${item.est_minutes} min</span>
            ${item.graded ? '<span class="tag tag-graded">🛡️ GRADED</span>' : ''}
            ${isTarget ? '<span class="tag tag-graded" style="background-color: #ef4444; color: #fff;">STUCK TARGET</span>' : ''}
          </div>
          ${prereqsHtml}
        </div>
      `;
    }).join('');

    return `
      <div class="roadmap-week-group">
        <span class="roadmap-week-heading">Week ${w}</span>
        ${itemsHtml}
      </div>
    `;
  }).join('');
}

/** Render Real-Time Signal Gauges */
function renderSignals() {
  if (!AppState.studentDetail) return;
  const signals = AppState.studentDetail.signals || [];
  const container = document.getElementById('signals-container');
  const summaryBadge = document.getElementById('signals-trigger-summary');
  if (!container) return;

  const thresholds = {
    stuck: 0.60,
    disengaged: 0.60,
    repeated_failure: 0.50,
    stall_time: 0.60,
    inactivity: 0.60,
    pace_drift: 0.30,
    pace_ahead: 0.25,
    goal_change: 1.00
  };

  const signalOrder = [
    'stuck',
    'disengaged',
    'repeated_failure',
    'stall_time',
    'inactivity',
    'pace_drift',
    'pace_ahead',
    'goal_change'
  ];

  // Sort according to preferred signal order
  const sortedSignals = [...signals].sort((a, b) => {
    const idxA = signalOrder.indexOf(a.name);
    const idxB = signalOrder.indexOf(b.name);
    return (idxA !== -1 ? idxA : 99) - (idxB !== -1 ? idxB : 99);
  });

  const triggeredCount = signals.filter(s => s.triggered).length;
  if (summaryBadge) {
    if (triggeredCount === 0) {
      summaryBadge.className = 'badge badge-risk-healthy';
      summaryBadge.textContent = 'All Healthy';
    } else {
      summaryBadge.className = 'badge badge-risk-high';
      summaryBadge.textContent = `${triggeredCount} Triggered`;
    }
  }

  container.innerHTML = sortedSignals.map(sig => {
    const th = thresholds[sig.name] !== undefined ? thresholds[sig.name] : 0.50;
    const scoreClamped = Math.max(0, Math.min(1, sig.score));
    const widthPct = (scoreClamped * 100).toFixed(1);
    const thPct = (th * 100).toFixed(1);

    const isComposite = sig.name === 'stuck' || sig.name === 'disengaged';
    const isTriggered = sig.triggered;

    let fillClass = 'fill-healthy';
    if (isTriggered) {
      fillClass = 'fill-triggered';
    } else if (scoreClamped >= th * 0.75) {
      fillClass = 'fill-warning';
    }

    const displayName = sig.name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

    return `
      <div class="signal-row ${isTriggered ? 'is-triggered' : ''}">
        <div class="signal-header">
          <div class="signal-name-wrap">
            <span class="signal-name">${displayName}</span>
            ${isComposite ? '<span class="signal-composite-tag">Composite</span>' : ''}
          </div>
          <span class="signal-score-badge ${isTriggered ? 'text-danger' : 'text-main'}">
            ${sig.score.toFixed(2)} ${isTriggered ? '🚨' : ''}
          </span>
        </div>

        <div class="gauge-track">
          <div class="gauge-fill ${fillClass}" style="width: ${widthPct}%;"></div>
          <div class="gauge-threshold-line" style="left: ${thPct}%;" title="Threshold: ${th}"></div>
        </div>

        <div class="signal-evidence-text">
          ${escapeHtml(sig.evidence || 'No evidence string available.')}
        </div>
      </div>
    `;
  }).join('');
}

/** Render Decision Card & Guardrails */
function renderDecisionCard() {
  const latest = AppState.latestIntervention || (AppState.interventions.length > 0 ? AppState.interventions[0] : null);

  const bannerEl = document.getElementById('decision-kind-banner');
  const bannerKind = document.getElementById('decision-banner-kind');
  const bannerIcon = document.getElementById('decision-banner-icon');
  const tsEl = document.getElementById('decision-timestamp');
  const rationaleEl = document.getElementById('decision-rationale-text');
  const auditTextEl = document.getElementById('guardrail-audit-log-text');
  const guardrailPill = document.getElementById('guardrail-summary-pill');

  if (!latest) {
    if (bannerEl) bannerEl.className = 'decision-kind-banner banner-none';
    if (bannerKind) bannerKind.textContent = 'NONE';
    if (bannerIcon) bannerIcon.textContent = '✓';
    if (tsEl) tsEl.textContent = 'Awaiting evaluation';
    if (rationaleEl) rationaleEl.textContent = 'Click "Evaluate now" to run detection and decision policies.';
    if (auditTextEl) auditTextEl.textContent = 'No recent evaluation performed.';
    return;
  }

  // Kind Banner styling
  const kind = latest.kind || 'NONE';
  const kindClass = `banner-${kind.toLowerCase()}`;
  if (bannerEl) bannerEl.className = `decision-kind-banner ${kindClass}`;
  if (bannerKind) bannerKind.textContent = kind;

  const iconMap = {
    CONCEPT_NUDGE: '💡',
    REENGAGE: '👋',
    HINT_LADDER: '🪜',
    REPLAN_DOWN: '📉',
    REPLAN_UP: '🚀',
    GOAL_REPLAN: '🎯',
    ESCALATE_MENTOR: '🛡️',
    NONE: '✓'
  };
  if (bannerIcon) bannerIcon.textContent = iconMap[kind] || '⚖️';
  if (tsEl) tsEl.textContent = formatSimDate(latest.ts);
  if (rationaleEl) rationaleEl.textContent = latest.rationale || 'No rationale recorded.';

  // Guardrail Checklist audit
  const logs = latest.guardrail_log || [];
  if (auditTextEl) auditTextEl.textContent = logs.length > 0 ? logs.join('\n') : 'No audit log available.';

  // Parse logs for checklist status (case-insensitive)
  const isConsentBlocked = logs.some(l => {
    const s = l.toLowerCase();
    return s.includes('consent') && (s.includes('blocked') || s.includes('opted out') || s.includes('disabled'));
  });
  const isQuietDeferred = logs.some(l => {
    const s = l.toLowerCase();
    return s.includes('quiet') && (s.includes('deferred') || s.includes('outside'));
  });
  const isCapBlocked = logs.some(l => {
    const s = l.toLowerCase();
    return s.includes('cap') && s.includes('blocked');
  });
  const isCooldownBlocked = logs.some(l => {
    const s = l.toLowerCase();
    return s.includes('cooldown') && s.includes('blocked');
  });
  const isGradedBlocked = logs.some(l => {
    const s = l.toLowerCase();
    return s.includes('graded') && s.includes('blocked');
  });

  const chkConsent = document.getElementById('chk-consent');
  const chkQuiet = document.getElementById('chk-quiet-hours');
  const chkCap = document.getElementById('chk-weekly-cap');
  const chkCooldown = document.getElementById('chk-cooldown');
  const chkGraded = document.getElementById('chk-graded');

  const updateCheckItem = (el, blocked, deferred, passDetail, blockDetail) => {
    if (!el) return;
    const icon = el.querySelector('.guardrail-status-icon');
    const detail = el.querySelector('.guardrail-detail');
    el.classList.remove('is-blocked', 'is-deferred');

    if (blocked) {
      el.classList.add('is-blocked');
      if (icon) icon.textContent = '✕';
      if (detail) detail.textContent = blockDetail;
    } else if (deferred) {
      el.classList.add('is-deferred');
      if (icon) icon.textContent = '⏱';
      if (detail) detail.textContent = 'Deferred until active hours';
    } else {
      if (icon) icon.textContent = '✓';
      if (detail) detail.textContent = passDetail;
    }
  };

  updateCheckItem(chkConsent, isConsentBlocked, false, 'Consent granted for proactive interventions', 'BLOCKED: Student consent_nudges is disabled');
  updateCheckItem(chkQuiet, false, isQuietDeferred, 'Within active study hours', 'DEFERRED: Outside active study window');
  updateCheckItem(chkCap, isCapBlocked, false, 'Within weekly nudge allowance (<= 3/wk)', 'BLOCKED: Weekly nudge cap exceeded');
  updateCheckItem(chkCooldown, isCooldownBlocked, false, 'No recent nudges in last 24h', 'BLOCKED: 24h cooldown active');

  // Graded work check
  if (chkGraded) {
    const isGradedProblem = latest.target_problem_id && latest.target_problem_id.startsWith('P-');
    updateCheckItem(
      chkGraded,
      isGradedBlocked,
      false,
      isGradedProblem ? 'Graded work: hints and solution code strictly blocked' : 'Practice problem: progressive hints permitted',
      'BLOCKED: Solution guidance forbidden on graded work'
    );
  }

  // Summary Pill
  if (guardrailPill) {
    const blockedCount = (isConsentBlocked ? 1 : 0) + (isCapBlocked ? 1 : 0) + (isCooldownBlocked ? 1 : 0) + (isGradedBlocked ? 1 : 0);
    if (blockedCount > 0) {
      guardrailPill.className = 'badge badge-danger';
      guardrailPill.textContent = `${blockedCount} Guardrail Blocked`;
    } else if (isQuietDeferred) {
      guardrailPill.className = 'badge badge-warning';
      guardrailPill.textContent = '1 Guardrail Deferred';
    } else {
      guardrailPill.className = 'badge badge-success';
      guardrailPill.textContent = '5 / 5 Verified';
    }
  }
}

/** Render Scholar AI Nudge Chat Bubble */
function renderNudge() {
  const latest = AppState.latestIntervention || (AppState.interventions.length > 0 ? AppState.interventions[0] : null);

  const statusBadge = document.getElementById('nudge-status-badge');
  const chatTime = document.getElementById('chat-timestamp');
  const messageBody = document.getElementById('chat-message-body');
  const ctaWrapper = document.getElementById('chat-cta-wrapper');
  const ctaBtn = document.getElementById('btn-nudge-cta');
  const ctaText = document.getElementById('nudge-cta-text');

  if (!latest) {
    if (statusBadge) { statusBadge.className = 'badge badge-neutral'; statusBadge.textContent = 'Ready'; }
    if (chatTime) chatTime.textContent = 'Just now';
    if (messageBody) {
      messageBody.className = 'chat-message';
      messageBody.innerHTML = 'Select a student and click <strong>Evaluate now</strong> to generate tailored proactive nudges and curriculum re-plans.';
    }
    if (ctaWrapper) ctaWrapper.style.display = 'none';
    return;
  }

  if (chatTime) chatTime.textContent = formatSimDate(latest.ts);

  // Check if message was suppressed or deferred by guardrails
  const isNone = latest.kind === 'NONE';
  const isDeferred = latest.status === 'deferred';
  const isSuppressed = !isNone && (latest.status === 'blocked' || !latest.message);

  if (statusBadge) {
    if (isNone) {
      statusBadge.className = 'badge badge-risk-healthy';
      statusBadge.textContent = 'On Track';
    } else if (isDeferred) {
      statusBadge.className = 'badge badge-warning';
      statusBadge.textContent = 'Message Deferred';
    } else if (isSuppressed) {
      statusBadge.className = 'badge badge-risk-high';
      statusBadge.textContent = 'Message Suppressed';
    } else {
      statusBadge.className = 'badge badge-success';
      statusBadge.textContent = 'Nudge Delivered';
    }
  }

  if (messageBody) {
    if (isNone) {
      messageBody.className = 'chat-message';
      messageBody.textContent = 'Student is progressing smoothly according to their learning schedule. No intervention necessary.';
    } else if (isDeferred) {
      messageBody.className = 'chat-message is-deferred-notice';
      messageBody.innerHTML = `
        <strong>⏱️ Proactive Message Deferred</strong><br>
        Outside active study hours. Nudge preserved and deferred until next active study window.<br>
        <small style="margin-top: 4px; display: block; opacity: 0.85;">Telemetry logged; roadmap intact.</small>
      `;
    } else if (isSuppressed) {
      messageBody.className = 'chat-message is-blocked-notice';
      const guardrailReason = (latest.guardrail_log || []).find(l => l.includes('BLOCKED') || l.includes('DEFERRED')) || 'Proactive message suppressed by guardrail policy.';
      messageBody.innerHTML = `
        <strong>🛡️ Proactive Message Suppressed</strong><br>
        ${escapeHtml(guardrailReason)}<br>
        <small style="margin-top: 4px; display: block; opacity: 0.85;">Intervention logged and visible in Mentor & Cohort dashboards.</small>
      `;
    } else {
      messageBody.className = 'chat-message';
      messageBody.textContent = latest.message;
    }
  }

  if (ctaWrapper && ctaBtn && ctaText) {
    if (!isNone && !isSuppressed && !isDeferred && latest.cta) {
      ctaWrapper.style.display = 'block';
      ctaText.textContent = latest.cta;
      ctaBtn.onclick = () => {
        const studentObj = (AppState.studentDetail && AppState.studentDetail.student) || (AppState.studentsList && AppState.studentsList.find(s => s.id === AppState.selectedStudentId));
        const studentDisplayName = (studentObj && studentObj.name) || AppState.selectedStudentId;
        showModal(
          `Curriculum Action: ${latest.cta}`,
          `
            <p><strong>Intervention Type:</strong> ${escapeHtml(latest.kind)}</p>
            <p style="margin-top: 8px;"><strong>Student:</strong> ${escapeHtml(studentDisplayName)}</p>
            <p style="margin-top: 8px;"><strong>Pedagogical Context:</strong> ${escapeHtml(latest.rationale)}</p>
            <div style="margin-top: 14px; padding: 10px; background: #0f172a; border-radius: 6px; border: 1px solid #1e293b;">
              <span style="color: #6366f1; font-weight: 600;">Scholar AI Copilot Prompt:</span>
              <p style="margin-top: 4px; font-size: 12px;">"${escapeHtml(latest.message)}"</p>
            </div>
          `
        );
      };
    } else {
      ctaWrapper.style.display = 'none';
    }
  }
}

/** Render Roadmap Diff Visualizer */
function renderRoadmapDiff() {
  const latest = AppState.latestIntervention || (AppState.interventions.length > 0 ? AppState.interventions[0] : null);

  const cntAdded = document.getElementById('diff-cnt-added');
  const cntMoved = document.getElementById('diff-cnt-moved');
  const cntDel = document.getElementById('diff-cnt-removed');
  const cntRetime = document.getElementById('diff-cnt-retimed');
  const itemsContainer = document.getElementById('diff-items-container');
  if (!itemsContainer) return;

  const diff = latest ? latest.roadmap_diff : null;

  const added = (diff && diff.added) || [];
  const moved = (diff && diff.moved) || [];
  const removed = (diff && diff.removed) || [];
  const retimed = (diff && diff.retimed) || [];

  const existingRoadmap = (AppState.studentDetail && AppState.studentDetail.roadmap) || [];
  const newlyAdded = added.filter(item => {
    const cleanTitle = (item.title || item.id).toLowerCase().replace(/^micro-lesson:\s*/i, '').trim();
    return !existingRoadmap.some(r =>
      r.id.toLowerCase() === item.id.toLowerCase() ||
      (r.title && cleanTitle && (r.title.toLowerCase().includes(cleanTitle) || cleanTitle.includes(r.title.toLowerCase())))
    );
  });

  if (cntAdded) cntAdded.textContent = `+${newlyAdded.length} added`;
  if (cntMoved) cntMoved.textContent = `~${moved.length} moved`;
  if (cntDel) cntDel.textContent = `-${removed.length} removed`;
  if (cntRetime) cntRetime.textContent = `±${retimed.length} retimed`;

  const totalChanges = added.length + moved.length + removed.length + retimed.length;

  if (totalChanges === 0) {
    itemsContainer.innerHTML = `
      <div class="diff-empty-notice">
        <span class="empty-icon">✓</span>
        <p>Roadmap unchanged; pace is aligned with schedule.</p>
      </div>
    `;
    return;
  }

  let html = '';

  added.forEach(item => {
    // Check if concept already exists on the roadmap
    const cleanTitle = (item.title || item.id).toLowerCase().replace(/^micro-lesson:\s*/i, '').trim();
    const existing = existingRoadmap.find(r =>
      r.id.toLowerCase() === item.id.toLowerCase() ||
      (r.title && cleanTitle && (r.title.toLowerCase().includes(cleanTitle) || cleanTitle.includes(r.title.toLowerCase())))
    );

    if (existing) {
      html += `
        <div class="diff-item diff-moved">
          <div class="diff-item-header">
            <span class="diff-item-action-badge">ℹ️ Referenced Existing Concept</span>
            <span class="tag tag-concept">Week ${existing.week}</span>
          </div>
          <div class="diff-item-title">${escapeHtml(existing.title)} (${escapeHtml(existing.id)})</div>
          <div class="diff-item-details">Concept already on roadmap; referenced as prerequisite instead of inserting duplicate micro-lesson.</div>
        </div>
      `;
    } else {
      html += `
        <div class="diff-item diff-added">
          <div class="diff-item-header">
            <span class="diff-item-action-badge">+ Added to Week ${item.week || 1}</span>
            <span class="tag tag-concept">${escapeHtml(item.type || 'concept')}</span>
          </div>
          <div class="diff-item-title">${escapeHtml(item.title || item.id)}</div>
          <div class="diff-item-details">${item.est_minutes || 30} min estimated • ID: ${escapeHtml(item.id)}</div>
        </div>
      `;
    }
  });

  moved.forEach(item => {
    html += `
      <div class="diff-item diff-moved">
        <div class="diff-item-header">
          <span class="diff-item-action-badge">~ Moved: Week ${item.from_week || '?'} → Week ${item.to_week || '?'}</span>
        </div>
        <div class="diff-item-title">${escapeHtml(item.title || item.id || 'Curriculum Item')}</div>
        <div class="diff-item-details">Adjusted to relieve capacity constraints</div>
      </div>
    `;
  });

  removed.forEach(item => {
    html += `
      <div class="diff-item diff-removed">
        <div class="diff-item-header">
          <span class="diff-item-action-badge">- Removed</span>
        </div>
        <div class="diff-item-title">${escapeHtml(item.title || item.id || 'Curriculum Item')}</div>
        <div class="diff-item-details">Superseded or dropped due to goal realignment</div>
      </div>
    `;
  });

  retimed.forEach(item => {
    html += `
      <div class="diff-item diff-retimed">
        <div class="diff-item-header">
          <span class="diff-item-action-badge">± Retimed Duration</span>
        </div>
        <div class="diff-item-title">${escapeHtml(item.title || item.id || 'Curriculum Item')}</div>
        <div class="diff-item-details">Duration adjusted to match student pace</div>
      </div>
    `;
  });

  itemsContainer.innerHTML = html;
}

/** Render Follow-up Status History */
function renderFollowupHistory() {
  const currentChip = document.getElementById('followup-current-chip');
  const listEl = document.getElementById('interventions-history-list');
  if (!listEl) return;

  // Exclude blocked and deferred interventions from Outcome History
  const outcomeInterventions = (AppState.interventions || []).filter(
    intv => intv.status !== 'blocked' && intv.status !== 'deferred' && intv.kind !== 'NONE'
  );

  if (outcomeInterventions.length === 0) {
    if (currentChip) {
      currentChip.className = 'chip chip-pending';
      currentChip.textContent = 'No Active Follow-up';
    }
    listEl.innerHTML = '<div class="history-empty-notice">No recorded outcome history yet.</div>';
    return;
  }

  const latest = outcomeInterventions[0];

  // Map chip classes
  const chipClasses = {
    pending_followup: 'chip-pending',
    recovered: 'chip-recovered',
    partial: 'chip-partial',
    no_response: 'chip-no-response',
    escalated: 'chip-escalated'
  };

  const statusLabels = {
    pending_followup: '⏱ Pending (+48h outcome check)',
    recovered: '✓ Recovered (Pass recorded)',
    partial: '○ Partial Activity',
    no_response: '⚠ No Response (+48h)',
    escalated: '🛡️ Escalated to Mentor'
  };

  if (currentChip) {
    const s = latest.status || 'pending_followup';
    currentChip.className = `chip ${chipClasses[s] || 'chip-pending'}`;
    currentChip.textContent = statusLabels[s] || s;
  }

  listEl.innerHTML = outcomeInterventions.map(intv => {
    const s = intv.status || 'pending_followup';
    const chipCls = chipClasses[s] || 'chip-pending';
    const label = statusLabels[s] || s;

    let targetInfo = '';
    if (intv.target_problem_id) targetInfo += `Target: ${intv.target_problem_id}`;
    if (intv.concept_id) targetInfo += ` • Concept: ${intv.concept_id}`;

    let outcomeStr = '';
    if (intv.outcome_ts) {
      outcomeStr = ` • Resolved at ${formatSimDate(intv.outcome_ts)}`;
    } else if (intv.followup_due) {
      outcomeStr = ` • Due: ${formatSimDate(intv.followup_due)}`;
    }

    return `
      <div class="history-item">
        <div class="history-item-top">
          <span class="history-kind">${escapeHtml(intv.kind)}</span>
          <span class="chip ${chipCls}" style="font-size: 10px; padding: 2px 6px;">${label}</span>
        </div>
        <div class="history-time">${formatSimDate(intv.ts)}${outcomeStr}</div>
        <div class="history-details">${escapeHtml(targetInfo || intv.rationale || '')}</div>
      </div>
    `;
  }).join('');
}

/** Render Cohort View and Aggregate Metrics Strip */
function renderCohortView() {
  // 1. Metrics Strip
  const m = AppState.metricsData;
  const recEl = document.getElementById('metric-recovery-rate');
  const avgEl = document.getElementById('metric-avg-hours');
  const nudgesEl = document.getElementById('metric-nudges-ratio');
  const escEl = document.getElementById('metric-escalations');

  if (m) {
    if (recEl) recEl.textContent = `${(m.recovery_rate * 100).toFixed(1)}%`;
    if (avgEl) avgEl.textContent = `${m.avg_hours_to_recover.toFixed(1)}h`;
    if (nudgesEl) nudgesEl.textContent = `${m.nudges_sent} Sent / ${m.nudges_blocked} Blocked`;
    if (escEl) escEl.textContent = `${m.escalations}`;
  }

  // 2. Cohort Table
  const tbody = document.getElementById('cohort-table-body');
  const badgeEl = document.getElementById('cohort-count-badge');
  const atRisk = (AppState.cohortData && AppState.cohortData.at_risk_students) || [];

  if (badgeEl) badgeEl.textContent = `${atRisk.length} At Risk`;
  if (!tbody) return;

  if (atRisk.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align: center; padding: 30px; color: #94a3b8;">
          ✓ All students are currently progressing smoothly without active risk triggers.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = atRisk.map(s => {
    let riskBadgeClass = 'badge-risk-high';
    if (s.status === 'disengaged') riskBadgeClass = 'badge-risk-medium';
    if (s.status === 'escalated') riskBadgeClass = 'badge-risk-high';
    if (s.status === 'consent_blocked') riskBadgeClass = 'badge-mode';

    const consentPill = s.consent_nudges
      ? '<span class="badge badge-risk-healthy">Opted In</span>'
      : '<span class="badge badge-risk-high">Opted Out</span>';

    return `
      <tr>
        <td>
          <div class="table-student-cell">
            <div class="table-avatar">${s.name.charAt(0).toUpperCase()}</div>
            <div>
              <div class="table-student-name">${escapeHtml(s.name)}</div>
              <div class="table-student-id">ID: ${escapeHtml(s.id)}</div>
            </div>
          </div>
        </td>
        <td>
          <span class="badge ${riskBadgeClass}">${escapeHtml(s.status)}</span>
        </td>
        <td>
          <strong style="font-family: var(--font-mono);">${s.days_at_risk.toFixed(1)}</strong> days
        </td>
        <td>
          <div class="table-signal-text">${escapeHtml(s.primary_signal)}</div>
        </td>
        <td>
          <div class="table-action-text">${escapeHtml(s.suggested_action)}</div>
        </td>
        <td>
          ${consentPill}
        </td>
        <td class="text-right">
          <button class="btn btn-secondary btn-inspect" data-student-id="${escapeHtml(s.id)}" style="padding: 4px 10px; font-size: 11px;">
            Inspect →
          </button>
        </td>
      </tr>
    `;
  }).join('');

  // Attach inspection handlers
  tbody.querySelectorAll('.btn-inspect').forEach(btn => {
    btn.addEventListener('click', e => {
      const studentId = btn.getAttribute('data-student-id');
      if (studentId) {
        switchStudent(studentId);
        switchView('student');
      }
    });
  });
}

/** Master Re-render of Active View */
function render() {
  renderTopBar();
  if (AppState.currentView === 'student') {
    renderStudentProfile();
    renderTimeline();
    renderRoadmap();
    renderSignals();
    renderDecisionCard();
    renderNudge();
    renderRoadmapDiff();
    renderFollowupHistory();
  } else {
    renderCohortView();
  }
}

// ============================================================================
// 5. State Actions & Workflow Routines
// ============================================================================

/** Switch active student and reload their data */
async function switchStudent(studentId) {
  AppState.selectedStudentId = studentId;
  AppState.isLoading = true;
  renderTopBar();

  try {
    const [detail, interventions] = await Promise.all([
      API.getStudentDetail(studentId),
      API.getInterventions(studentId)
    ]);
    AppState.studentDetail = detail;
    AppState.interventions = interventions;
    AppState.latestIntervention = interventions.length > 0 ? interventions[0] : null;
  } catch (err) {
    showToast(`Failed loading student ${studentId}: ${err.message}`, 'danger');
  } finally {
    AppState.isLoading = false;
    render();
  }
}

/** Switch active tab view */
async function switchView(viewName) {
  AppState.currentView = viewName;
  if (viewName === 'cohort') {
    try {
      const [cohort, metrics] = await Promise.all([
        API.getCohort(),
        API.getMetrics()
      ]);
      AppState.cohortData = cohort;
      AppState.metricsData = metrics;
    } catch (err) {
      showToast(`Failed refreshing cohort: ${err.message}`, 'danger');
    }
  }
  render();
}

/** Trigger Evaluate Now */
async function handleEvaluate() {
  if (AppState.isEvaluating || AppState.isAdvancing || AppState.isResetting) return;

  AppState.isEvaluating = true;
  renderTopBar();
  showToast(`Running proactive evaluation for ${AppState.selectedStudentId}...`, 'info', 2000);

  try {
    const res = await API.evaluateStudent(AppState.selectedStudentId);

    // Update state with evaluation payload
    AppState.latestIntervention = res.intervention;
    if (AppState.studentDetail) {
      AppState.studentDetail.signals = res.signals;
      AppState.studentDetail.roadmap = res.roadmap;
    }

    // Refresh student detail, interventions and background metrics
    const [detail, interventions, students, cohort, metrics] = await Promise.all([
      API.getStudentDetail(AppState.selectedStudentId),
      API.getInterventions(AppState.selectedStudentId),
      API.getStudents(),
      API.getCohort(),
      API.getMetrics()
    ]);

    AppState.studentDetail = detail;
    AppState.interventions = interventions;
    AppState.studentsList = students;
    AppState.cohortData = cohort;
    AppState.metricsData = metrics;

    const kind = res.intervention ? res.intervention.kind : 'NONE';
    showToast(`Evaluated: ${kind} intervention recorded.`, 'success');
  } catch (err) {
    showToast(`Evaluation failed: ${err.message}`, 'danger');
  } finally {
    AppState.isEvaluating = false;
    render();
  }
}

/** Advance Simulated Clock */
async function handleAdvance(days) {
  if (AppState.isEvaluating || AppState.isAdvancing || AppState.isResetting) return;

  AppState.isAdvancing = true;
  renderTopBar();
  showToast(`Advancing clock by ${days} day(s)...`, 'info', 2000);

  try {
    const res = await API.advanceSim(days);
    AppState.currentMode.sim_now = res.sim_now;

    // Refresh student detail, interventions, cohort, and metrics
    const [detail, interventions, students, cohort, metrics] = await Promise.all([
      API.getStudentDetail(AppState.selectedStudentId),
      API.getInterventions(AppState.selectedStudentId),
      API.getStudents(),
      API.getCohort(),
      API.getMetrics()
    ]);

    AppState.studentDetail = detail;
    AppState.interventions = interventions;
    AppState.latestIntervention = interventions.length > 0 ? interventions[0] : null;
    AppState.studentsList = students;
    AppState.cohortData = cohort;
    AppState.metricsData = metrics;

    showToast(`Advanced ${res.days_advanced} days (${res.events_generated} events generated). Follow-ups updated.`, 'success');
  } catch (err) {
    showToast(`Advance failed: ${err.message}`, 'danger');
  } finally {
    AppState.isAdvancing = false;
    render();
  }
}

/** Reset Simulation State */
async function handleReset() {
  if (AppState.isEvaluating || AppState.isAdvancing || AppState.isResetting) return;

  AppState.isResetting = true;
  renderTopBar();
  showToast('Resetting simulation to pristine seed state...', 'warning', 2000);

  try {
    const res = await API.resetSim();
    AppState.currentMode.sim_now = res.sim_now;

    // Reload all stores
    const [mode, students, detail, interventions, cohort, metrics] = await Promise.all([
      API.getMode(),
      API.getStudents(),
      API.getStudentDetail(AppState.selectedStudentId),
      API.getInterventions(AppState.selectedStudentId),
      API.getCohort(),
      API.getMetrics()
    ]);

    AppState.currentMode = mode;
    AppState.studentsList = students;
    AppState.studentDetail = detail;
    AppState.interventions = interventions;
    AppState.latestIntervention = interventions.length > 0 ? interventions[0] : null;
    AppState.cohortData = cohort;
    AppState.metricsData = metrics;

    showToast('Simulation successfully reset to Nov 18, 2026.', 'success');
  } catch (err) {
    showToast(`Reset failed: ${err.message}`, 'danger');
  } finally {
    AppState.isResetting = false;
    render();
  }
}

// ============================================================================
// 6. Application Initialization
// ============================================================================
async function initApp() {
  // Bind Top Controls
  const studentSelect = document.getElementById('student-select');
  if (studentSelect) {
    studentSelect.addEventListener('change', e => {
      switchStudent(e.target.value);
    });
  }

  const btnEval = document.getElementById('btn-evaluate');
  if (btnEval) btnEval.addEventListener('click', handleEvaluate);

  const btnAdv1 = document.getElementById('btn-advance-1');
  if (btnAdv1) btnAdv1.addEventListener('click', () => handleAdvance(1));

  const btnAdv3 = document.getElementById('btn-advance-3');
  if (btnAdv3) btnAdv3.addEventListener('click', () => handleAdvance(3));

  const btnReset = document.getElementById('btn-reset');
  if (btnReset) btnReset.addEventListener('click', handleReset);

  const tabStudent = document.getElementById('tab-student');
  if (tabStudent) tabStudent.addEventListener('click', () => switchView('student'));

  const tabCohort = document.getElementById('tab-cohort');
  if (tabCohort) tabCohort.addEventListener('click', () => switchView('cohort'));

  // Bind Modal Controls
  const modalClose = document.getElementById('modal-close');
  const modalOk = document.getElementById('modal-ok');
  const modalOverlay = document.getElementById('app-modal');
  if (modalClose) modalClose.addEventListener('click', closeModal);
  if (modalOk) modalOk.addEventListener('click', closeModal);
  if (modalOverlay) {
    modalOverlay.addEventListener('click', e => {
      if (e.target === modalOverlay) closeModal();
    });
  }

  // Initial Data Fetch
  try {
    const [mode, students, detail, interventions, cohort, metrics] = await Promise.all([
      API.getMode(),
      API.getStudents(),
      API.getStudentDetail(AppState.selectedStudentId),
      API.getInterventions(AppState.selectedStudentId),
      API.getCohort(),
      API.getMetrics()
    ]);

    AppState.currentMode = mode;
    AppState.studentsList = students;
    AppState.studentDetail = detail;
    AppState.interventions = interventions;
    AppState.latestIntervention = interventions.length > 0 ? interventions[0] : null;
    AppState.cohortData = cohort;
    AppState.metricsData = metrics;

    render();
  } catch (err) {
    showToast(`Initialization failed: ${err.message}`, 'danger', 5000);
  }
}

// Start application when DOM is ready
document.addEventListener('DOMContentLoaded', initApp);
