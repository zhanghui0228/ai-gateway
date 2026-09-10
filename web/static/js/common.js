/* 公共工具 */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function toast(msg, type = '') {
  const box = $('#toast-box');
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (r.status === 401 && !path.includes('/login')) { location.href = '/'; throw new Error('未登录'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function apiPost(path, body) {
  return api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
}
function apiPut(path, body) {
  return api(path, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
}
function apiDelete(path) { return api(path, {method: 'DELETE'}); }

/* 弹窗 */
function openModal(title, bodyHTML, footHTML) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = bodyHTML;
  $('#modal-foot').innerHTML = footHTML;
  $('#modal-mask').classList.add('show');
}
function closeModal() { $('#modal-mask').classList.remove('show'); }
$('#modal-mask') && $('#modal-mask').addEventListener('mousedown', e => {
  if (e.target === e.currentTarget) closeModal();
});

function fmtTokens(n) {
  n = Number(n) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n);
}
function fmtCost(n) {
  n = Number(n) || 0;
  return n >= 1 ? '¥' + n.toFixed(2) : '¥' + n.toFixed(4);
}
function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', {hour12: false, month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit'});
}
function fmtMs(ms) { return ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms'; }

/* ECharts 深色通用配置 */
const CHART_TEXT = {color: '#6b83a8', fontSize: 11};
const CHART_AXIS = {axisLine: {lineStyle: {color: 'rgba(0,229,255,.25)'}},
  axisLabel: CHART_TEXT, splitLine: {lineStyle: {color: 'rgba(0,229,255,.08)'}}};
