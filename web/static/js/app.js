/* 控制台入口:路由切换 + 自动刷新 */
let currentPage = 'dashboard';
let refreshTimer = null;

async function boot() {
  const me = await api('/admin/api/me').catch(() => ({logged_in: false}));
  if (!me.logged_in) { location.href = '/'; return; }
  $('#who').textContent = me.username;
  $$('.nav-item').forEach(item => item.onclick = () => switchPage(item.dataset.page));
  switchPage('dashboard');
}

async function switchPage(name) {
  currentPage = name;
  $$('.nav-item').forEach(i => i.classList.toggle('active', i.dataset.page === name));
  if (refreshTimer) clearInterval(refreshTimer);
  disposeCharts();
  if (name === 'settings') Pages.settings.subPage = 'basic';
  await Pages[name].render($('#main'));
  // 总览/统计/日志页自动刷新
  if (name === 'dashboard' || name === 'usage' || name === 'logs') {
    refreshTimer = setInterval(async () => {
      if (currentPage === name) await Pages[name].refresh();
    }, 30000);
  }
}

$('#btn-logout').onclick = async () => {
  await apiPost('/admin/api/logout', {});
  location.href = '/';
};

boot();
