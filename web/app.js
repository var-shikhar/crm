'use strict';
// CRM web UI. No build step, no dependencies. Talks to the three services through the gateway.

const CLIENT_ID = 'crm-web';
const MFA_GRANT = 'urn:crm:params:oauth:grant-type:mfa-otp';
const AUTH = '/auth/api/v1', CRM = '/crm/api/v1', AUDIT = '/audit/api/v1';

const state = { tokens: null, me: null, view: 'customers', params: {}, notice: null, mfa: null };
const $app = document.getElementById('app');

// ------------------------------------------------------------------ helpers

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const can = (p) => !!state.me && state.me.permissions.includes(p);
const when = (s) => (s ? new Date(s).toLocaleString() : '—');
const dash = (v) => (v === null || v === undefined || v === '' ? '—' : esc(v));

function saveTokens(tokens) {
  state.tokens = tokens;
  try {
    if (tokens) sessionStorage.setItem('crm.tokens', JSON.stringify(tokens));
    else sessionStorage.removeItem('crm.tokens');
  } catch { /* storage unavailable: session lasts until reload */ }
}

function errorText(data, status) {
  const d = data && (data.detail ?? data.error_description);
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((e) => `${(e.loc || []).slice(1).join('.') || 'input'}: ${e.msg}`).join('; ');
  if (status === 429) return 'Too many attempts. Wait a moment and try again.';
  if (status >= 500) return 'The service is unavailable. Try again shortly.';
  return `Request failed (${status})`;
}

async function tokenRequest(params) {
  const res = await fetch('/auth/oauth/token', { method: 'POST', body: new URLSearchParams({ client_id: CLIENT_ID, ...params }) });
  return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
}

let refreshing = null;
function refresh() {
  if (!state.tokens || !state.tokens.refresh_token) return Promise.resolve(false);
  // One refresh at a time: the refresh token is single-use.
  refreshing = refreshing || tokenRequest({ grant_type: 'refresh_token', refresh_token: state.tokens.refresh_token })
    .then((r) => { saveTokens(r.ok ? r.body : null); return r.ok; })
    .catch(() => false)
    .finally(() => { refreshing = null; });
  return refreshing;
}

async function api(path, { method = 'GET', body, retry = true } = {}) {
  const headers = { Authorization: `Bearer ${state.tokens ? state.tokens.access_token : ''}` };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  if (res.status === 401) {
    if (retry && await refresh()) return api(path, { method, body, retry: false });
    signedOut('Your session has ended. Sign in again.');
    throw new Error('Your session has ended. Sign in again.');
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(errorText(data, res.status));
  return data;
}

function signedOut(message) {
  saveTokens(null);
  state.me = null;
  state.mfa = null;
  state.notice = message ? { kind: 'warn', text: message } : null;
  render();
}

function go(view, params = {}) {
  state.view = view;
  state.params = params;
  render();
}

function flash(text, kind = 'success') { state.notice = { kind, text }; }

function formValues(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === 'checkbox') {
      if (el.dataset.list !== undefined) { (out[el.name] = out[el.name] || []); if (el.checked) out[el.name].push(el.value); }
      else out[el.name] = el.checked;
    } else out[el.name] = el.value.trim();
  }
  return out;
}
const dropEmpty = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== ''));
const emptyToNull = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v === '' ? null : v]));

const field = (label, name, value = '', attrs = '') =>
  `<label>${esc(label)}<input name="${name}" value="${esc(value)}" ${attrs}></label>`;
const badge = (text, kind = '') => `<span class="badge ${kind}">${esc(text)}</span>`;
const statusBadge = (s) => badge(s, s === 'active' || s === 'success' ? 'ok' : 'bad');

// --------------------------------------------------------------------- shell

const NAV = [
  ['customers', 'Customers', 'customer:read'],
  ['audit', 'Activity log', 'audit:read'],
  ['users', 'Users', 'user:read'],
  ['roles', 'Roles', 'role:read'],
  ['security', 'My security', null],
];

function shell(content) {
  const notice = state.notice ? `<div class="notice ${state.notice.kind}" role="status">${esc(state.notice.text)}</div>` : '';
  state.notice = null;
  const active = state.view === 'customer' ? 'customers' : state.view;
  return `
    <header class="top">
      <span class="brand">CRM</span>
      <nav>${NAV.filter(([, , p]) => !p || can(p)).map(([v, label]) =>
        `<button class="link ${v === active ? 'active' : ''}" data-action="nav" data-view="${v}">${label}</button>`).join('')}</nav>
      <div class="who small"><div>${esc(state.me.full_name)}</div><div class="muted">${esc(state.me.roles.join(', ') || 'no role')}</div></div>
      <button data-action="logout">Sign out</button>
    </header>
    <main>${notice}${content}</main>`;
}

async function render() {
  if (!state.me) { $app.innerHTML = loginView(); focusFirst(); return; }
  const view = VIEWS[state.view] || VIEWS.customers;
  try {
    $app.innerHTML = shell(await view());
  } catch (err) {
    if (state.me) $app.innerHTML = shell(`<div class="notice error" role="alert">${esc(err.message)}</div>`);
  }
}

function focusFirst() {
  const el = $app.querySelector('input:not([type=hidden])');
  if (el) el.focus();
}

// --------------------------------------------------------------------- login

function loginView() {
  const notice = state.notice ? `<div class="notice ${state.notice.kind}" role="alert">${esc(state.notice.text)}</div>` : '';
  state.notice = null;
  if (state.mfa) {
    const hint = state.mfa.method === 'totp'
      ? 'Enter the 6-digit code from your authenticator app.'
      : 'We emailed you a 6-digit code. Enter it to finish signing in.';
    return `<div class="auth-wrap"><div class="card stack">
      <h1>Verify it’s you</h1><p class="muted">${hint}</p>${notice}
      <form data-form="mfa" class="stack">
        ${field('Verification code', 'otp', '', 'inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required')}
        <button class="primary block" type="submit">Verify</button>
      </form>
      <button class="link" data-action="restart-login">Use a different account</button>
    </div></div>`;
  }
  return `<div class="auth-wrap"><div class="card stack">
    <h1>Sign in to CRM</h1>${notice}
    <form data-form="login" class="stack">
      ${field('Email', 'username', '', 'type="email" autocomplete="username" required')}
      ${field('Password', 'password', '', 'type="password" autocomplete="current-password" required')}
      <button class="primary block" type="submit">Continue</button>
    </form>
  </div></div>`;
}

async function loadMe() { state.me = await api(`${AUTH}/me`); }

// ----------------------------------------------------------------- customers

const CUSTOMER_FIELDS = [
  ['First name', 'first_name', 'required maxlength="100"'], ['Last name', 'last_name', 'required maxlength="100"'],
  ['Email', 'email', 'type="email" required'], ['Phone', 'phone', 'maxlength="40"'],
  ['Date of birth', 'date_of_birth', 'type="date"'], ['Address line 1', 'address_line1', ''],
  ['Address line 2', 'address_line2', ''], ['City', 'city', ''], ['State', 'state', ''],
  ['Postal code', 'postal_code', ''], ['Country', 'country', ''],
];

const customerForm = (c = {}, disabled = false) => `
  <div class="grid">${CUSTOMER_FIELDS.map(([l, n, a]) => field(l, n, c[n] ?? '', `${a} ${disabled ? 'disabled' : ''}`)).join('')}</div>
  <label>Notes<textarea name="notes" rows="2" ${disabled ? 'disabled' : ''}>${esc(c.notes ?? '')}</textarea></label>`;

async function customersView() {
  const p = state.params;
  const qs = new URLSearchParams(dropEmpty({ q: p.q || '', status: p.status || '', page: String(p.page || 1) }));
  const data = await api(`${CRM}/customers?${qs}`);
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const rows = data.items.map((c) => `
    <tr class="clickable" data-action="open-customer" data-id="${c.id}" tabindex="0">
      <td>${esc(c.last_name)}, ${esc(c.first_name)}</td><td>${esc(c.email)}</td><td>${dash(c.phone)}</td>
      <td>${dash(c.city)}</td><td>${statusBadge(c.status)}</td><td class="small muted">${when(c.updated_at)}</td>
    </tr>`).join('');
  return `
    <div class="row spread"><h1>Customers</h1>
      ${can('customer:create') ? `<button class="primary" data-action="toggle-new-customer">${p.creating ? 'Cancel' : 'Add customer'}</button>` : ''}</div>
    ${p.creating ? `<form class="card stack" data-form="create-customer"><h2>New customer</h2>${customerForm()}
      <button class="primary" type="submit">Save customer</button></form>` : ''}
    <form class="card row" data-form="search-customers">
      ${field('Search name, email or phone', 'q', p.q || '')}
      <label>Status<select name="status">${['', 'active', 'blocked'].map((s) =>
        `<option value="${s}" ${s === (p.status || '') ? 'selected' : ''}>${s || 'Any'}</option>`).join('')}</select></label>
      <button type="submit">Search</button>
    </form>
    <div class="card table-wrap">
      ${rows ? `<table><thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>City</th><th>Status</th><th>Updated</th></tr></thead><tbody>${rows}</tbody></table>`
        : `<p class="muted">${p.q || p.status ? 'No customers match this search.' : 'No customers yet.'}</p>`}
      ${pager(data.page, pages, data.total)}
    </div>`;
}

const pager = (page, pages, total) => `
  <div class="row spread small"><span class="muted">${total} total</span>
    <span><button class="small" data-action="page" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>Previous</button>
    Page ${page} of ${pages}
    <button class="small" data-action="page" data-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>Next</button></span></div>`;

const EMPLOYMENT_TYPES = ['full_time', 'part_time', 'self_employed', 'contract', 'unemployed', 'retired', 'other'];

async function customerView() {
  const id = state.params.id;
  const [c, jobs, contacts] = await Promise.all([
    api(`${CRM}/customers/${id}`), api(`${CRM}/customers/${id}/employment`), api(`${CRM}/customers/${id}/contacts`),
  ]);
  const blocked = c.status === 'blocked';
  const editable = can('customer:update') && !blocked;
  const canJobs = can('customer:employment:manage') && !blocked;
  const canContacts = can('customer:contact:manage') && !blocked;

  const jobRows = jobs.map((j) => `<tr>
      <td>${esc(j.employer_name)}</td><td>${dash(j.job_title)}</td><td>${esc(j.employment_type.replace('_', ' '))}</td>
      <td>${dash(j.start_date)} – ${j.is_current ? 'present' : dash(j.end_date)}</td>
      <td>${j.annual_income ? `${esc(j.annual_income)} ${esc(j.currency ?? '')}` : '—'}</td>
      <td class="actions">${canJobs ? `<button class="small danger" data-action="remove-child" data-kind="employment" data-id="${j.id}" data-label="${esc(j.employer_name)}">Remove</button>` : ''}</td></tr>`).join('');
  const contactRows = contacts.map((k) => `<tr>
      <td>${esc(k.full_name)} ${k.is_emergency_contact ? badge('emergency') : ''}</td><td>${esc(k.relationship_type)}</td>
      <td>${dash(k.email)}</td><td>${dash(k.phone)}</td><td>${dash(k.notes)}</td>
      <td class="actions">${canContacts ? `<button class="small danger" data-action="remove-child" data-kind="contacts" data-id="${k.id}" data-label="${esc(k.full_name)}">Remove</button>` : ''}</td></tr>`).join('');

  return `
    <button class="link" data-action="nav" data-view="customers">← All customers</button>
    <div class="row spread"><h1>${esc(c.first_name)} ${esc(c.last_name)} ${statusBadge(c.status)}</h1>
      ${can('customer:block') && blocked ? '<button data-action="unblock-customer">Unblock customer</button>' : ''}</div>
    ${blocked ? `<div class="notice warn">Blocked ${when(c.blocked_at)}. Reason: ${esc(c.block_reason)}. This record is read-only until it is unblocked.</div>` : ''}

    <form class="card stack" data-form="update-customer"><h2>Details</h2>${customerForm(c, !editable)}
      <div class="row spread"><span class="small muted">Created ${when(c.created_at)} · Updated ${when(c.updated_at)}</span>
      ${editable ? '<button class="primary" type="submit">Save changes</button>' : ''}</div></form>

    <div class="card stack"><h2>Employment</h2>
      ${jobRows ? `<div class="table-wrap"><table><thead><tr><th>Employer</th><th>Title</th><th>Type</th><th>Period</th><th>Annual income</th><th></th></tr></thead><tbody>${jobRows}</tbody></table></div>` : '<p class="muted">No employment records.</p>'}
      ${canJobs ? `<form data-form="add-employment" class="stack"><div class="grid">
          ${field('Employer', 'employer_name', '', 'required')}${field('Job title', 'job_title')}
          <label>Type<select name="employment_type">${EMPLOYMENT_TYPES.map((t) => `<option value="${t}">${t.replace('_', ' ')}</option>`).join('')}</select></label>
          ${field('Start date', 'start_date', '', 'type="date"')}${field('End date', 'end_date', '', 'type="date"')}
          ${field('Annual income', 'annual_income', '', 'type="number" min="0" step="0.01"')}
          ${field('Currency (ISO code)', 'currency', '', 'maxlength="3" placeholder="USD"')}</div>
          <label class="check"><input type="checkbox" name="is_current" checked> Current job</label>
          <button type="submit">Add employment</button></form>` : ''}
    </div>

    <div class="card stack"><h2>Related contacts</h2>
      ${contactRows ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Relationship</th><th>Email</th><th>Phone</th><th>Notes</th><th></th></tr></thead><tbody>${contactRows}</tbody></table></div>` : '<p class="muted">No related contacts.</p>'}
      ${canContacts ? `<form data-form="add-contact" class="stack"><div class="grid">
          ${field('Full name', 'full_name', '', 'required')}${field('Relationship', 'relationship_type', '', 'required placeholder="spouse, parent, colleague…"')}
          ${field('Email', 'email', '', 'type="email"')}${field('Phone', 'phone')}${field('Notes', 'notes')}</div>
          <label class="check"><input type="checkbox" name="is_emergency_contact"> Emergency contact</label>
          <button type="submit">Add contact</button></form>` : ''}
    </div>

    ${can('customer:block') && !blocked ? `<form class="card row" data-form="block-customer"><h2>Block customer</h2>
      ${field('Reason (required)', 'reason', '', 'required minlength="3" maxlength="500"')}
      <button class="danger" type="submit">Block customer</button></form>` : ''}`;
}

// ---------------------------------------------------------------------- audit

async function auditView() {
  const p = state.params;
  const qs = new URLSearchParams(dropEmpty({
    actor_email: p.actor_email || '', action: p.action || '', resource_id: p.resource_id || '',
    outcome: p.outcome || '', page: String(p.page || 1), page_size: '50',
  }));
  const data = await api(`${AUDIT}/events?${qs}`);
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const rows = data.items.map((e) => `<tr>
      <td class="small">${when(e.occurred_at)}</td>
      <td>${dash(e.actor_name || e.actor_email || e.actor_id)}<div class="small muted">${esc(e.actor_email ?? e.actor_type)}${e.actor_roles.length ? ` · ${esc(e.actor_roles.join(', '))}` : ''}</div></td>
      <td class="mono">${esc(e.action)}</td>
      <td class="small">${dash(e.resource_type)}<div class="mono muted">${esc(e.resource_id ?? '')}</div></td>
      <td>${statusBadge(e.outcome)}</td>
      <td class="small">${dash(e.ip_address)}</td>
      <td>${Object.keys(e.details).length ? `<details><summary>Details</summary><pre class="details mono">${esc(JSON.stringify(e.details, null, 2))}</pre></details>` : ''}</td>
    </tr>`).join('');
  return `
    <h1>Activity log</h1><p class="muted">Every action across the CRM, with who did it and when. Newest first.</p>
    <form class="card row" data-form="search-audit">
      ${field('User email contains', 'actor_email', p.actor_email || '')}
      ${field('Action (e.g. customer.* )', 'action', p.action || '')}
      ${field('Record ID', 'resource_id', p.resource_id || '')}
      <label>Outcome<select name="outcome">${['', 'success', 'failure', 'denied'].map((o) =>
        `<option value="${o}" ${o === (p.outcome || '') ? 'selected' : ''}>${o || 'Any'}</option>`).join('')}</select></label>
      <button type="submit">Filter</button>
      <button type="button" data-action="export-audit">Download CSV</button>
    </form>
    <div class="card table-wrap">
      ${rows ? `<table><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Outcome</th><th>IP</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : '<p class="muted">No activity matches these filters.</p>'}
      ${pager(data.page, pages, data.total)}
    </div>`;
}

// ---------------------------------------------------------------------- users

const roleChecks = (roles, selected = []) => roles.map((r) =>
  `<label class="check"><input type="checkbox" name="roles" data-list value="${esc(r.name)}" ${selected.includes(r.name) ? 'checked' : ''}> ${esc(r.name)}</label>`).join('');

const MFA_LABEL = { totp: 'authenticator app', email: 'email code', none: 'off' };

async function usersView() {
  const manage = can('user:manage');
  const [users, roles] = await Promise.all([api(`${AUTH}/users`), can('role:read') ? api(`${AUTH}/roles`) : []]);
  const editing = users.find((u) => u.id === state.params.id);
  const rows = users.map((u) => `<tr>
      <td>${esc(u.full_name)}<div class="small muted">${esc(u.email)}</div></td>
      <td>${u.roles.map((r) => badge(r)).join(' ') || '<span class="muted">none</span>'}</td>
      <td>${badge(MFA_LABEL[u.mfa_method] || u.mfa_method, u.mfa_method === 'none' ? 'bad' : '')}
        <div class="small muted">${u.mfa_required ? 'required' : 'optional'}</div></td>
      <td>${u.is_active ? badge('active', 'ok') : badge('disabled', 'bad')} ${u.is_locked ? badge('locked', 'bad') : ''}</td>
      <td class="small muted">${when(u.last_login_at)}</td>
      <td class="actions">${manage ? `<button class="small" data-action="edit-user" data-id="${u.id}">Manage</button>` : ''}</td></tr>`).join('');
  const self = editing && editing.id === state.me.id;
  return `
    <h1>Users</h1>
    ${editing ? `<div class="card stack"><div class="row spread"><h2>Manage ${esc(editing.email)}</h2><button class="small" data-action="edit-user" data-id="">Close</button></div>
      <form data-form="update-user" class="stack">
        ${field('Full name', 'full_name', editing.full_name, 'required')}
        <div>${self ? '<p class="small muted">You cannot change your own roles, status or second-factor requirement.</p>' : `${roleChecks(roles, editing.roles)}
          <label class="check"><input type="checkbox" name="is_active" ${editing.is_active ? 'checked' : ''}> Account active</label>
          <label class="check"><input type="checkbox" name="mfa_required" ${editing.mfa_required ? 'checked' : ''}> Require a second factor at sign-in</label>`}</div>
        <button class="primary" type="submit">Save</button></form>
      <form data-form="set-user-password" class="row">${field('New password (min 10 characters)', 'new_password', '', 'type="password" minlength="10" required autocomplete="new-password"')}
        <button type="submit">Set password</button></form>
      <div class="row"><button data-action="reset-mfa">Reset second factor to email</button>
        ${editing.is_locked ? '<button data-action="unlock-user">Unlock account</button>' : ''}</div></div>` : ''}
    ${manage && !editing ? `<form class="card stack" data-form="create-user"><h2>New user</h2><div class="grid">
        ${field('Full name', 'full_name', '', 'required')}${field('Email', 'email', '', 'type="email" required')}
        ${field('Initial password (min 10 characters)', 'password', '', 'type="password" minlength="10" required autocomplete="new-password"')}</div>
        <div>${roleChecks(roles)}</div>
        <label class="check"><input type="checkbox" name="mfa_required" checked> Require a second factor at sign-in (recommended)</label>
        <button class="primary" type="submit">Create user</button></form>` : ''}
    <div class="card table-wrap"><table><thead><tr><th>User</th><th>Roles</th><th>Second factor</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ---------------------------------------------------------------------- roles

const permChecks = (perms, selected = []) => perms.map((p) =>
  `<label class="check" title="${esc(p.description)}"><input type="checkbox" name="permissions" data-list value="${esc(p.code)}" ${selected.includes(p.code) ? 'checked' : ''}> <span class="mono">${esc(p.code)}</span></label>`).join('');

async function rolesView() {
  const manage = can('role:manage');
  const [roles, perms] = await Promise.all([api(`${AUTH}/roles`), api(`${AUTH}/permissions`)]);
  const editing = roles.find((r) => r.id === state.params.id);
  const rows = roles.map((r) => `<tr>
      <td><strong>${esc(r.name)}</strong> ${r.is_system ? badge('system') : ''}<div class="small muted">${esc(r.description)}</div></td>
      <td>${r.permissions.map((p) => `<span class="badge mono">${esc(p)}</span>`).join(' ') || '<span class="muted">none</span>'}</td>
      <td class="actions">${manage && !r.is_system ? `<button class="small" data-action="edit-role" data-id="${r.id}">Edit</button>
        <button class="small danger" data-action="delete-role" data-id="${r.id}" data-label="${esc(r.name)}">Delete</button>` : ''}</td></tr>`).join('');
  return `
    <h1>Roles</h1><p class="muted">A role is a named set of permissions. Changes apply immediately to everyone holding the role.</p>
    ${editing ? `<form class="card stack" data-form="update-role"><div class="row spread"><h2>Edit role ${esc(editing.name)}</h2><button type="button" class="small" data-action="edit-role" data-id="">Close</button></div>
        ${field('Description', 'description', editing.description)}<div>${permChecks(perms, editing.permissions)}</div>
        <button class="primary" type="submit">Save role</button></form>` : ''}
    ${manage && !editing ? `<form class="card stack" data-form="create-role"><h2>New role</h2><div class="grid">
        ${field('Name (lowercase, e.g. records_officer)', 'name', '', 'required pattern="[a-z][a-z0-9_-]*"')}${field('Description', 'description')}</div>
        <div>${permChecks(perms)}</div><button class="primary" type="submit">Create role</button></form>` : ''}
    <div class="card table-wrap"><table><thead><tr><th>Role</th><th>Permissions</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ------------------------------------------------------------------- security

async function securityView() {
  const me = state.me;
  const setup = state.params.totp;
  return `
    <h1>My security</h1>
    <div class="card stack"><h2>Second factor</h2>
      ${me.mfa_method === 'none'
        ? '<div class="notice warn">Your account signs in with a password only. Adding a second factor protects it if the password leaks.</div>'
        : `<p>Signing in needs your password plus a one-time code. Your codes currently come from:
            <strong>${me.mfa_method === 'totp' ? 'an authenticator app' : `email (${esc(me.email)})`}</strong>.</p>`}
      <p class="small muted">${me.mfa_required ? 'Your administrator requires a second factor on this account.' : 'A second factor is optional for this account.'}</p>
      ${me.mfa_method !== 'totp' && !setup ? '<button class="primary" data-action="totp-setup">Set up an authenticator app</button>' : ''}
      ${setup ? `<p>Scan this QR code with Google Authenticator (or any authenticator app), then enter the code it shows.</p>
        <img class="qr" alt="QR code for authenticator app setup" src="data:image/svg+xml;base64,${btoa(setup.qr_svg)}">
        <p class="small muted">Can’t scan? Enter this key manually: <span class="mono">${esc(setup.secret)}</span></p>
        <form data-form="totp-activate" class="row">${field('6-digit code', 'otp', '', 'inputmode="numeric" maxlength="6" pattern="[0-9]{6}" required autocomplete="one-time-code"')}
          <button class="primary" type="submit">Turn on authenticator app</button></form>` : ''}
      ${me.mfa_method !== 'email' ? `<form data-form="mfa-email" class="row">
          ${field(`Confirm your password to ${me.mfa_method === 'totp' ? 'switch to' : 'turn on'} email codes`, 'password', '', 'type="password" required autocomplete="current-password"')}
          <button type="submit">Use email codes</button></form>` : ''}
      ${!me.mfa_required && me.mfa_method !== 'none' ? `<form data-form="mfa-disable" class="row">
          ${field('Confirm your password to turn the second factor off', 'password', '', 'type="password" required autocomplete="current-password"')}
          <button class="danger" type="submit">Turn off second factor</button></form>` : ''}
    </div>
    <form class="card stack" data-form="change-password"><h2>Change password</h2><div class="grid">
        ${field('Current password', 'current_password', '', 'type="password" required autocomplete="current-password"')}
        ${field('New password (min 10 characters)', 'new_password', '', 'type="password" minlength="10" required autocomplete="new-password"')}</div>
      <p class="small muted">Changing your password signs you out everywhere.</p>
      <button type="submit">Change password</button></form>`;
}

const VIEWS = { customers: customersView, customer: customerView, audit: auditView, users: usersView, roles: rolesView, security: securityView };

// -------------------------------------------------------------- form handlers

const FORMS = {
  async login(v) {
    const r = await tokenRequest({ grant_type: 'password', username: v.username, password: v.password });
    if (r.status === 403 && r.body.error === 'mfa_required') {
      state.mfa = { token: r.body.mfa_token, method: r.body.mfa_method };
      return render();
    }
    if (!r.ok) throw new Error(errorText(r.body, r.status));
    // Account without a second factor (an administrator made MFA optional for it).
    saveTokens(r.body);
    await loadMe();
    go(can('customer:read') ? 'customers' : 'security');
  },
  async mfa(v) {
    const r = await tokenRequest({ grant_type: MFA_GRANT, mfa_token: state.mfa.token, otp: v.otp });
    if (!r.ok) throw new Error(errorText(r.body, r.status));
    saveTokens(r.body);
    state.mfa = null;
    await loadMe();
    go(can('customer:read') ? 'customers' : 'security');
  },
  'search-customers': (v) => go('customers', { q: v.q, status: v.status }),
  async 'create-customer'(v) {
    const c = await api(`${CRM}/customers`, { method: 'POST', body: dropEmpty(v) });
    flash('Customer added.');
    go('customer', { id: c.id });
  },
  async 'update-customer'(v) {
    await api(`${CRM}/customers/${state.params.id}`, { method: 'PATCH', body: emptyToNull(v) });
    flash('Changes saved.');
    render();
  },
  async 'block-customer'(v) {
    await api(`${CRM}/customers/${state.params.id}/block`, { method: 'POST', body: v });
    flash('Customer blocked.');
    render();
  },
  async 'add-employment'(v) {
    await api(`${CRM}/customers/${state.params.id}/employment`, { method: 'POST', body: dropEmpty({ ...v, currency: v.currency.toUpperCase() }) });
    flash('Employment record added.');
    render();
  },
  async 'add-contact'(v) {
    await api(`${CRM}/customers/${state.params.id}/contacts`, { method: 'POST', body: dropEmpty(v) });
    flash('Contact added.');
    render();
  },
  'search-audit': (v) => go('audit', v),
  async 'create-user'(v) {
    await api(`${AUTH}/users`, { method: 'POST', body: v });
    flash(`User ${v.email} created. They sign in with this password${v.mfa_required ? ' and a code sent to their email' : ' only (no second factor)'}.`);
    render();
  },
  async 'update-user'(v) {
    await api(`${AUTH}/users/${state.params.id}`, { method: 'PATCH', body: v });
    flash('User updated.');
    go('users');
  },
  async 'set-user-password'(v) {
    await api(`${AUTH}/users/${state.params.id}/password`, { method: 'POST', body: v });
    flash('Password set. The user has been signed out everywhere.');
    render();
  },
  async 'create-role'(v) {
    await api(`${AUTH}/roles`, { method: 'POST', body: v });
    flash(`Role ${v.name} created.`);
    render();
  },
  async 'update-role'(v) {
    await api(`${AUTH}/roles/${state.params.id}`, { method: 'PATCH', body: v });
    flash('Role updated.');
    go('roles');
  },
  async 'totp-activate'(v) {
    state.me = await api(`${AUTH}/me/mfa/totp/activate`, { method: 'POST', body: v });
    flash('Authenticator app is on. You will use it the next time you sign in.');
    go('security');
  },
  async 'mfa-email'(v) {
    state.me = await api(`${AUTH}/me/mfa/email`, { method: 'POST', body: v });
    flash('Email codes are on.');
    go('security');
  },
  async 'mfa-disable'(v) {
    state.me = await api(`${AUTH}/me/mfa/disable`, { method: 'POST', body: v });
    flash('Second factor turned off. You now sign in with your password only.');
    go('security');
  },
  async 'change-password'(v) {
    await api(`${AUTH}/me/password`, { method: 'POST', body: v });
    signedOut('Password changed. Sign in with your new password.');
  },
};

// ------------------------------------------------------------- click handlers

const ACTIONS = {
  nav: (el) => go(el.dataset.view),
  async logout() {
    const tokens = state.tokens;
    signedOut();
    if (tokens) {
      for (const token of [tokens.refresh_token, tokens.access_token]) {
        fetch('/auth/oauth/revoke', { method: 'POST', body: new URLSearchParams({ client_id: CLIENT_ID, token }) }).catch(() => {});
      }
    }
  },
  'restart-login': () => { state.mfa = null; render(); },
  'open-customer': (el) => go('customer', { id: el.dataset.id }),
  'toggle-new-customer': () => go('customers', { ...state.params, creating: !state.params.creating }),
  page: (el) => go(state.view, { ...state.params, page: Number(el.dataset.page) }),
  async 'unblock-customer'() {
    await api(`${CRM}/customers/${state.params.id}/unblock`, { method: 'POST' });
    flash('Customer unblocked.');
    render();
  },
  async 'remove-child'(el) {
    if (!confirm(`Remove ${el.dataset.label}? This cannot be undone.`)) return;
    await api(`${CRM}/customers/${state.params.id}/${el.dataset.kind}/${el.dataset.id}`, { method: 'DELETE' });
    flash('Record removed.');
    render();
  },
  async 'export-audit'() {
    // Same filters as the table. Fetched with the token, then handed to the browser as a file.
    const p = state.params;
    const qs = new URLSearchParams(dropEmpty({
      actor_email: p.actor_email || '', action: p.action || '', resource_id: p.resource_id || '', outcome: p.outcome || '',
    }));
    const download = async (retry) => {
      const res = await fetch(`${AUDIT}/events/export?${qs}`, { headers: { Authorization: `Bearer ${state.tokens.access_token}` } });
      if (res.status === 401 && retry && await refresh()) return download(false);
      if (!res.ok) throw new Error(errorText(await res.json().catch(() => null), res.status));
      return res;
    };
    const res = await download(true);
    const name = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await res.blob());
    link.download = name ? name[1] : 'activity-log.csv';
    link.click();
    URL.revokeObjectURL(link.href);
    if (res.headers.get('X-Truncated') === 'true') showError('The export was limited to the newest 50,000 events. Narrow the filters to get the rest.');
  },
  'edit-user': (el) => go('users', el.dataset.id ? { id: el.dataset.id } : {}),
  async 'reset-mfa'() {
    if (!confirm('Reset this user’s second factor to email codes and sign them out?')) return;
    await api(`${AUTH}/users/${state.params.id}/mfa/reset`, { method: 'POST' });
    flash('Second factor reset to email.');
    render();
  },
  async 'unlock-user'() {
    await api(`${AUTH}/users/${state.params.id}/unlock`, { method: 'POST' });
    flash('Account unlocked.');
    render();
  },
  'edit-role': (el) => go('roles', el.dataset.id ? { id: el.dataset.id } : {}),
  async 'delete-role'(el) {
    if (!confirm(`Delete role ${el.dataset.label}?`)) return;
    await api(`${AUTH}/roles/${el.dataset.id}`, { method: 'DELETE' });
    flash('Role deleted.');
    render();
  },
  async 'totp-setup'() {
    const totp = await api(`${AUTH}/me/mfa/totp/setup`, { method: 'POST' });
    go('security', { totp });
  },
};

function showError(message) {
  const host = $app.querySelector('main') || $app.querySelector('.card');
  if (!host) return;
  host.querySelectorAll('.notice.error').forEach((n) => n.remove());
  const div = document.createElement('div');
  div.className = 'notice error';
  div.setAttribute('role', 'alert');
  div.textContent = message;
  host.prepend(div);
  div.scrollIntoView({ block: 'nearest' });
}

document.addEventListener('submit', async (event) => {
  const form = event.target.closest('form[data-form]');
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector('button[type=submit]');
  if (button) button.disabled = true;
  try {
    await FORMS[form.dataset.form](formValues(form));
  } catch (err) {
    if (state.me || !state.notice) showError(err.message);
  } finally {
    if (button) button.disabled = false;
  }
});

document.addEventListener('click', async (event) => {
  const el = event.target.closest('[data-action]');
  if (!el || el.disabled) return;
  try {
    await ACTIONS[el.dataset.action](el);
  } catch (err) {
    showError(err.message);
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && event.target.matches('tr[data-action]')) event.target.click();
});

// ----------------------------------------------------------------------- boot

(async () => {
  try { state.tokens = JSON.parse(sessionStorage.getItem('crm.tokens')); } catch { state.tokens = null; }
  if (state.tokens) {
    try { await loadMe(); } catch { /* signedOut() already rendered the login form */ return; }
    state.view = can('customer:read') ? 'customers' : 'security';
  }
  render();
})();
