document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.mini-progress span').forEach(el => {
    const width = el.style.width;
    el.style.width = '0%';
    requestAnimationFrame(() => {
      requestAnimationFrame(() => { el.style.width = width; });
    });
  });
});

async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

function showMiniToast(message) {
  let toast = document.querySelector('.mini-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'mini-toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(window.__miniToastTimer);
  window.__miniToastTimer = setTimeout(() => toast.classList.remove('visible'), 1800);
}


// ── Skill helpers ─────────────────────────────────────────────────────────

function showResult() {
  const r = document.getElementById('result');
  if (r) r.style.display = '';
}

async function postForm(url, formData) {
  const res = await fetch(url, { method: 'POST', body: formData });
  return res.json();
}

// ── Exposure Request ──────────────────────────────────────────────────────
async function runExposure() {
  const client = document.getElementById('client_name')?.value;
  if (!client) { showMiniToast('Select a client first'); return; }
  const lines = [...document.querySelectorAll('input[name="line"]:checked')].map(x => x.value);
  const d = await postJson('/skills/exposure-request/build', { client_name: client, lines });
  if (!d.ok) { showMiniToast('Error: ' + d.error); return; }
  document.getElementById('email_draft').value = d.email_draft;
  const cw = document.getElementById('checklist_wrap');
  if (cw) cw.innerHTML = '<strong style="font-size:13px;color:var(--text-strong)">Checklist Items</strong><ul style="margin:8px 0 0 16px;font-size:13px;color:var(--text-soft);">' +
    d.checklist_items.map(i => `<li>${i}</li>`).join('') + '</ul>';
  showResult(); showMiniToast('Done');
}

// ── ISM Deck Builder ─────────────────────────────────────────────────────
async function runStrategyDeck() {
  const file = document.getElementById('deck_file')?.files[0];
  const client = document.getElementById('client_name')?.value;
  const newYear = document.getElementById('new_year')?.value;
  const oldYear = document.getElementById('old_year')?.value || '';
  if (!file || !client || !newYear) { showMiniToast('Deck file, client, and year required'); return; }
  const fd = new FormData();
  fd.append('deck', file); fd.append('client_name', client);
  fd.append('new_year', newYear); fd.append('old_year', oldYear);
  const d = await postForm('/skills/strategy-deck/build', fd);
  if (!d.ok) { showMiniToast('Error: ' + d.error); return; }
  document.getElementById('deck_info').innerHTML =
    `<strong>${d.filename}</strong> &nbsp;·&nbsp; ${d.slides} slides`;
  const dl = document.getElementById('download_link');
  dl.href = `/skills/strategy-deck/download?key=${d.key}&filename=${d.filename}`;
  dl.style.display = 'block';
  showResult(); showMiniToast('Deck ready');
}

// ── ECP Reviewer ──────────────────────────────────────────────────────────
async function runECP() {
  const file = document.getElementById('ecp_file')?.files[0];
  const client = document.getElementById('client_name')?.value;
  if (!file || !client) { showMiniToast('File and client required'); return; }
  const fd = new FormData();
  fd.append('ecp_file', file); fd.append('client_name', client);
  const d = await postForm('/skills/ecp-reviewer/review', fd);
  if (!d.ok) { showMiniToast('Error: ' + d.error); return; }
  document.getElementById('ecp_meta').innerHTML =
    `<strong>Carrier:</strong> ${d.carrier} &nbsp;·&nbsp; <strong>State:</strong> ${d.state} &nbsp;·&nbsp; ` +
    `<strong>Surplus Lines:</strong> ${d.surplus_confirmed ? '✓ Yes' : '✗ Not detected'}`;
  document.getElementById('ecp_bullet').value = d.email_bullet;
  showResult(); showMiniToast('Done');
}

// ── Home State Assigner ───────────────────────────────────────────────────
async function runHSA() {
  const file = document.getElementById('policy_file')?.files[0];
  const client = document.getElementById('client_name')?.value;
  const priorState = document.getElementById('prior_state')?.value || '';
  if (!file || !client) { showMiniToast('File and client required'); return; }
  const fd = new FormData();
  fd.append('policy_file', file); fd.append('client_name', client);
  fd.append('prior_state', priorState);
  const d = await postForm('/skills/home-state-assigner/assign', fd);
  if (!d.ok) { showMiniToast('Error: ' + d.error); return; }
  document.getElementById('hsa_meta').innerHTML =
    `<strong>Detected State:</strong> ${d.detected_state || 'Not detected'} &nbsp;·&nbsp; <strong>Confidence:</strong> ${d.confidence}`;
  document.getElementById('hsa_draft').value = d.email_draft;
  showResult(); showMiniToast('Done');
}

// ── Renewal Kickoff ───────────────────────────────────────────────────────
async function runKickoff() {
  const client = document.getElementById('client_name')?.value;
  if (!client) { showMiniToast('Select a client first'); return; }
  const d = await postJson('/skills/renewal-kickoff/build', { client_name: client });
  if (!d.ok) { showMiniToast('Error: ' + d.error); return; }
  document.getElementById('kickoff_draft').value = d.email_draft;
  const td = document.getElementById('team_display');
  if (td) td.innerHTML = d.team_members.map(m =>
    `<strong>${m.role}:</strong> ${m.name}`).join(' &nbsp;·&nbsp; ');
  showResult(); showMiniToast('Done');
}

// ── Header Updates ────────────────────────────────────────────────────────
async function runHeaders() {
  const client = document.getElementById('client_name')?.value;
  const year = document.getElementById('renewal_year')?.value;
  const lines = [...document.querySelectorAll('input[name="line"]:checked')].map(x => x.value);
  if (!client || !year) { showMiniToast('Client and year required'); return; }
  const d = await postJson('/skills/header-updates/build', { client_name: client, renewal_year: year, lines });
  if (!d.ok) { showMiniToast('Error: ' + d.error); return; }
  document.getElementById('header_note').value = d.note;
  showResult(); showMiniToast('Done');
}

// ── Team Confirmer ────────────────────────────────────────────────────────
async function runConfirm() {
  const client = document.getElementById('client_name')?.value;
  if (!client) { showMiniToast('Select a client first'); return; }
  const d = await postJson('/skills/team-confirmer/build', { client_name: client });
  if (!d.ok) { showMiniToast('Error: ' + d.error); return; }
  const wrap = document.getElementById('confirm_emails');
  wrap.innerHTML = d.emails.map((e, i) => `
    <div class="skill-email-card">
      <div class="skill-email-card-header">
        <strong>${e.role} — ${e.name}</strong>
        <button class="skill-copy-btn" onclick="copyText('confirm_${i}')">Copy</button>
      </div>
      <textarea id="confirm_${i}" class="skill-draft-box" rows="10">${e.draft}</textarea>
    </div>`).join('');
  showResult(); showMiniToast('Done');
}

// ── ISM Scheduler ────────────────────────────────────────────────────────
async function runScheduler() {
  const client = document.getElementById('client_name')?.value;
  if (!client) { showMiniToast('Select a client first'); return; }
  const d = await postJson('/skills/strategy-scheduler/build', { client_name: client, preferred_dates: [] });
  if (!d.ok) { showMiniToast('Error: ' + d.error); return; }
  const sd = document.getElementById('slots_display');
  if (sd) sd.innerHTML = '<strong>Suggested Slots</strong><br>' +
    d.suggested_slots.map(s => `<div style="margin-top:4px">• ${s}</div>`).join('');
  document.getElementById('invite_draft').value = d.invite_draft;
  showResult(); showMiniToast('Done');
}
