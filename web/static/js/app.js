/* 控制台入口:hash 路由 + 页面切换 + 自动刷新 */
let currentPage = 'dashboard';
let refreshTimer = null;

const PAGES = ['dashboard', 'channels', 'model_status', 'keys', 'prices', 'usage', 'cache', 'logs', 'settings'];

/* 解析当前路由:返回 {page, sub} */
function parseHash() {
  const hash = (window.location.hash || '').replace(/^#/, '');
  const [page, sub] = hash.split(':');
  if (page && PAGES.includes(page)) return { page, sub: sub || null };
  return { page: 'dashboard', sub: null };
}

/* 写入路由 hash */
function writeHash(page, sub) {
  let h = page;
  if (page === 'settings' && sub && sub !== 'basic') h = 'settings:' + sub;
  if (h === 'dashboard') {
    // 默认页用空 hash,URL 更整洁
    history.replaceState(null, '', window.location.pathname);
  } else {
    history.replaceState(null, '', '#' + h);
  }
}

async function boot() {
  const me = await api('/admin/api/me').catch(() => ({logged_in: false}));
  if (!me.logged_in) { location.href = '/'; return; }
  $('#who').textContent = me.username;
  $$('.nav-item').forEach(item => item.onclick = () => switchPage(item.dataset.page, true));
  // hash 变化(前进/后退)时切页
  window.addEventListener('hashchange', () => {
    const { page, sub } = parseHash();
    switchPage(page, false, sub);
  });
  // 启动时按 hash 切页,无 hash 默认总览
  const { page, sub } = parseHash();
  await switchPage(page, false, sub);
}

async function switchPage(name, write, sub) {
  if (!PAGES.includes(name)) name = 'dashboard';
  currentPage = name;
  $$('.nav-item').forEach(i => i.classList.toggle('active', i.dataset.page === name));
  if (refreshTimer) clearInterval(refreshTimer);
  disposeCharts();
  // 设置页子路由(由 hash sub 参数决定)
  if (name === 'settings') Pages.settings.subPage = sub || 'basic';
  await Pages[name].render($('#main'));
  if (write) writeHash(name, sub);
  // 总览/统计/日志页自动刷新
  if (name === 'dashboard' || name === 'usage' || name === 'logs' || name === 'cache') {
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
