/* 各管理页渲染逻辑 */
const Pages = {};
let CHARTS = [];
function disposeCharts() { CHARTS.forEach(c => c.dispose()); CHARTS = []; }
function mkChart(el) { const c = echarts.init(el); CHARTS.push(c); return c; }

/* ================= 渠道管理 ================= */
Pages.channels = {
  async render(main) {
    main.innerHTML = `
      <div class="page-head"><h2>渠道管理</h2>
        <div class="actions"><button class="btn ghost" id="btn-probe-all">⟳ 探测全部</button>
          <button class="btn" id="btn-add-chan">＋ 新增渠道</button></div></div>
      <div class="panel" style="padding:6px 10px">
        <table class="gw-table"><thead><tr>
          <th>状态</th><th>探测</th><th>名称</th><th>预设</th><th>适配器</th><th>模型</th>
          <th>优先级/权重</th><th>代理</th><th>操作</th>
        </tr></thead><tbody id="chan-tbody"></tbody></table>
      </div>`;
    $('#btn-add-chan').onclick = () => this.edit(null);
    $('#btn-probe-all').onclick = async () => {
      toast('探测中…(免费,不消耗 token)');
      try {
        const r = await apiPost('/admin/api/probe/all', {});
        const okc = r.filter(x => x.ok).length;
        toast(`探测完成:${okc}/${r.length} 个渠道在线`, okc === r.length ? 'ok' : '');
      } catch (e) { toast(e.message, 'err'); }
      this.refresh();
    };
    await this.refresh();
  },
  async refresh() {
    const list = await api('/admin/api/channels');
    const tb = $('#chan-tbody');
    if (!list.length) { tb.innerHTML = `<tr><td colspan="9"><div class="empty-tip">暂无渠道,点击右上角新增</div></td></tr>`; return; }
    tb.innerHTML = list.map(c => {
      const dot = !c.enabled ? 'off' : (c.breaker_state === 'open' ? 'breaker'
        : c.breaker_state === 'half_open' ? 'warn' : 'on');
      const stateTxt = !c.enabled ? '停用' : c.breaker_state === 'open'
        ? `熔断 ${Math.ceil(c.breaker_cooldown)}s` : c.breaker_state === 'half_open' ? '半开' : '正常';
      // 探测列
      let probeCell;
      if (c.probe_ok === null || c.probe_ok === undefined) {
        probeCell = '<span class="dim" style="font-size:11px">未探测</span>';
      } else if (c.probe_ok) {
        probeCell = `<span class="tag ok" title="${esc(c.probe_error || '')}">在线 ${c.probe_latency}ms</span>`;
      } else {
        probeCell = `<span class="tag err" title="${esc(c.probe_error || '')}">离线</span>`;
      }
      const probeAt = c.probe_at ? `<div class="dim" style="font-size:10px">${fmtTime(c.probe_at)}</div>` : '';
      return `<tr>
        <td><span class="chan-status-dot ${dot}"></span><span class="dim" style="font-size:12px">${stateTxt}</span></td>
        <td>${probeCell}${probeAt}</td>
        <td><b>${esc(c.name)}</b>${c.note ? `<div class="dim" style="font-size:11px">${esc(c.note)}</div>` : ''}</td>
        <td><span class="tag info">${esc(c.preset)}</span></td>
        <td class="mono" style="font-size:12px">${esc(c.adapter)}</td>
        <td style="max-width:220px"><span class="dim" style="font-size:12px">${esc((c.models || []).join(', ')) || '-'}</span></td>
        <td class="mono">P${c.priority} / W${c.weight}</td>
        <td>${c.proxy_url ? '<span class="proxy-badge">proxy</span>' : '<span class="dim">-</span>'}</td>
        <td style="white-space:nowrap">
          <button class="btn ghost" style="padding:3px 9px;font-size:12px" data-act="probe" data-id="${c.id}">探测</button>
          <button class="btn ghost" style="padding:3px 9px;font-size:12px" data-act="test" data-id="${c.id}">深度测试</button>
          <button class="btn ghost" style="padding:3px 9px;font-size:12px" data-act="edit" data-id="${c.id}">编辑</button>
          <button class="btn danger" style="padding:3px 9px;font-size:12px" data-act="del" data-id="${c.id}">删除</button>
        </td></tr>`;
    }).join('');
    $$('#chan-tbody button').forEach(b => b.onclick = () => {
      const id = +b.dataset.id, act = b.dataset.act;
      if (act === 'edit') { const c = list.find(x => x.id === id); this.edit(c); }
      else if (act === 'del') this.del(id, list.find(x => x.id === id));
      else if (act === 'test') this.test(id, b);
      else if (act === 'probe') this.probe(id, b);
    });
  },
  async probe(id, btn) {
    btn.disabled = true; btn.textContent = '探测中';
    try {
      const r = await apiPost(`/admin/api/channels/${id}/probe`, {});
      toast(r.ok ? `在线,延迟 ${r.latency_ms}ms` : `离线: ${r.error}`, r.ok ? 'ok' : 'err');
    } catch (e) { toast(e.message, 'err'); }
    btn.disabled = false; btn.textContent = '探测';
    this.refresh();
  },
  async edit(c) {
    let presets = [];
    try { presets = await api('/admin/api/presets'); } catch (e) {}
    const isEdit = !!c;
    if (isEdit) { try { c = await api('/admin/api/channels'); c = c.find(x => x.id === c.id) || c; } catch (e) {} }
    const p = c || {name: '', preset: 'custom', adapter: 'openai_compat', base_url: '',
      api_key: '', models: [], model_mapping: {}, weight: 1, priority: 0, enabled: true,
      proxy_url: '', pricing_override: {}, timeout: 0, note: '', azure_api_version: '2024-10-21'};
    openModal(isEdit ? '编辑渠道' : '新增渠道', `
      <div class="form-row">
        <div class="field"><label>渠道名称 *</label><input id="f-name" value="${esc(p.name)}"></div>
        <div class="field"><label>预设厂商</label>
          <select id="f-preset">${presets.map(x =>
            `<option value="${x.id}" ${x.id === p.preset ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}
            <option value="custom" ${p.preset === 'custom' ? 'selected' : ''}>自定义 (OpenAI 兼容)</option></select></div>
      </div>
      <div class="form-row">
        <div class="field" style="flex:2"><label>Base URL *</label><input id="f-base" value="${esc(p.base_url)}" placeholder="https://api.deepseek.com"></div>
        <div class="field"><label>适配器</label>
          <select id="f-adapter">${['openai_compat','anthropic','gemini','azure'].map(a =>
            `<option ${a === p.adapter ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      </div>
      <div class="field"><label>API Key(多个用英文逗号分隔,自动轮询)</label>
        <input id="f-key" value="${esc(p.api_key || '')}" placeholder="sk-..." ${isEdit ? '' : ''}></div>
      <div class="field"><label>支持模型(逗号分隔,对外暴露的模型名)</label>
        <div style="display:flex;gap:8px">
          <input id="f-models" value="${esc((p.models || []).join(', '))}" placeholder="gpt-4o, deepseek-chat">
          <button class="btn" style="white-space:nowrap" id="btn-fetch-models">⟳ 自动获取</button>
        </div>
        <div class="hint" id="fetch-hint">从上游模型列表端点拉取(不消耗 token)</div>
        <div id="fetch-result" style="max-height:130px;overflow-y:auto;margin-top:6px"></div></div>
      <div class="field"><label>模型映射(JSON,对外名→上游真实名,可选)</label>
        <textarea id="f-mapping" rows="2" class="mono" style="resize:vertical">${esc(JSON.stringify(p.model_mapping || {}, null, 0))}</textarea></div>
      <div class="form-row">
        <div class="field"><label>优先级(大者优先)</label><input id="f-pri" type="number" value="${p.priority}"></div>
        <div class="field"><label>权重(同级分流)</label><input id="f-w" type="number" min="1" value="${p.weight}"></div>
        <div class="field"><label>超时秒(0=默认)</label><input id="f-timeout" type="number" min="0" value="${p.timeout || 0}"></div>
      </div>
      <div class="field"><label>专属代理(解决无全局代理访问外网,可选)</label>
        <input id="f-proxy" value="${esc(p.proxy_url || '')}" class="mono" placeholder="http://127.0.0.1:7890 或 socks5://user:pass@host:1080"></div>
      <div class="form-row">
        <div class="field"><label>Azure api-version(仅 Azure)</label><input id="f-azver" value="${esc(p.azure_api_version)}"></div>
        <div class="field"><label>健康探测方式</label>
          <select id="f-probemode">
            <option value="models" ${(p.probe_mode || 'models') === 'models' ? 'selected' : ''}>模型列表端点(免费,推荐)</option>
            <option value="chat" ${p.probe_mode === 'chat' ? 'selected' : ''}>聊天端点(max_tokens=1,极少消耗)</option>
            <option value="off" ${p.probe_mode === 'off' ? 'selected' : ''}>不探测</option>
          </select>
          <div class="hint">站点禁用 /v1/models 时(如 AgentRouter)选聊天端点</div></div>
      </div>
      <div class="form-row">
        <div class="field" style="display:flex;align-items:end;padding-bottom:2px">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="f-enabled" ${p.enabled ? 'checked' : ''}> 启用渠道</label></div>
      </div>
      <div class="field"><label>备注</label><input id="f-note" value="${esc(p.note || '')}"></div>
    `, `
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" id="btn-save-chan">保存</button>`);
    // 预设联动
    $('#f-preset').onchange = () => {
      const pr = presets.find(x => x.id === $('#f-preset').value);
      if (pr && !isEdit) {
        $('#f-base').value = pr.base_url;
        $('#f-adapter').value = pr.adapter;
        $('#f-models').value = pr.models.join(', ');
      }
    };
    // 自动获取模型(L1 免费探测,直连上游模型列表端点,附带元数据)
    let fetchedMeta = [];
    $('#btn-fetch-models').onclick = async () => {
      const hint = $('#fetch-hint'), box = $('#fetch-result');
      hint.style.color = '';
      hint.textContent = '探测中…';
      box.innerHTML = '';
      try {
        const r = await apiPost('/admin/api/channels/fetch_models', {
          channel_id: isEdit ? p.id : null,
          base_url: $('#f-base').value.trim(),
          api_key: $('#f-key').value.trim(),
          proxy_url: $('#f-proxy').value.trim(),
          adapter: $('#f-adapter').value,
        });
        if (!r.ok) { hint.textContent = '失败: ' + (r.error || ''); hint.style.color = 'var(--red)'; return; }
        hint.style.color = r.warning ? 'var(--amber)' : '';
        hint.textContent = r.warning
          ? `⚠ ${r.warning};已获取 ${r.models.length} 个模型,延迟 ${r.latency_ms}ms`
          : `获取成功(${r.models.length} 个模型,延迟 ${r.latency_ms}ms);勾选后保存,元数据与参考价将写入模型单价表`;
        if (!r.models.length) { box.innerHTML = '<span class="dim" style="font-size:12px">上游未返回模型列表</span>'; return; }
        fetchedMeta = r.models;
        const withMeta = r.models.filter(m => m.source);
        const metaNote = withMeta.length
          ? `<div class="dim" style="font-size:11px;margin-bottom:6px">${withMeta.length} 个模型带元数据(${r.models.some(m => m.source === 'upstream') ? '上游返回' : ''}${r.models.some(m => m.source === 'builtin') ? ' + 知识库匹配' : ''})</div>` : '';
        box.innerHTML = metaNote + `<table class="gw-table" style="font-size:12px"><thead><tr>
            <th style="width:32px"></th><th>模型</th><th>上下文</th><th>最大输出</th>
            <th>输入价</th><th>输出价</th><th>来源</th></tr></thead><tbody>` +
          r.models.map((m, i) => {
            const selected = i < 50;
            return `<tr data-i="${i}" class="fm-row" style="cursor:pointer">
              <td><input type="checkbox" class="fm-ck" ${selected ? 'checked' : ''}></td>
              <td class="mono">${esc(m.id)}</td>
              <td class="mono">${m.context ? fmtTokens(m.context) : '-'}</td>
              <td class="mono">${m.max_output ? fmtTokens(m.max_output) : '-'}</td>
              <td class="mono">${m.input_price != null ? '¥' + m.input_price : '-'}</td>
              <td class="mono">${m.output_price != null ? '¥' + m.output_price : '-'}</td>
              <td>${m.source === 'upstream' ? '<span class="tag ok">上游</span>'
                  : m.source === 'builtin' ? '<span class="tag info">知识库</span>'
                  : '<span class="dim">-</span>'}</td></tr>`;
          }).join('') + '</tbody></table>' +
          `<div style="margin-top:8px;display:flex;gap:8px">
             <button class="btn ghost" style="padding:4px 12px;font-size:12px" id="fm-all">全选</button>
             <button class="btn ghost" style="padding:4px 12px;font-size:12px" id="fm-none">全不选</button>
             <button class="btn" style="padding:4px 12px;font-size:12px" id="fm-apply">填入已选 (N)</button></div>`;
        const syncCount = () => {
          const n = $$('.fm-ck').filter(c => c.checked).length;
          $('#fm-apply').textContent = `填入已选 (${n})`;
        };
        $$('.fm-row').forEach(tr => tr.onclick = e => {
          if (e.target.tagName === 'INPUT') return;
          const ck = tr.querySelector('.fm-ck');
          ck.checked = !ck.checked;
        });
        $$('.fm-ck').forEach(c => c.onclick = e => e.stopPropagation());
        $$('.fm-ck').forEach(c => c.onchange = syncCount);
        $('#fm-all').onclick = () => { $$('.fm-ck').forEach(c => c.checked = true); syncCount(); };
        $('#fm-none').onclick = () => { $$('.fm-ck').forEach(c => c.checked = false); syncCount(); };
        syncCount();
        $('#fm-apply').onclick = () => {
          const sel = $$('.fm-ck').map((c, i) => c.checked ? fetchedMeta[i] : null).filter(Boolean);
          $('#f-models').value = sel.map(m => m.id).join(', ');
          // 元数据 + 参考价写入单价表(仅补缺失,不覆盖已有配置)
          const items = sel.filter(m => m.source).map(m => ({
            model: m.id,
            ...(m.context ? {context_window: m.context} : {}),
            ...(m.max_output ? {max_output: m.max_output} : {}),
            ...(m.input_price != null ? {input_price: m.input_price, output_price: m.output_price || 0} : {}),
          }));
          if (items.length) {
            apiPost('/admin/api/prices', {items, only_missing: true})
              .then(() => toast(`已同步 ${items.length} 个模型的元数据/参考价`, 'ok'))
              .catch(e => toast('元数据同步失败: ' + e.message, 'err'));
          }
        };
        if (!$('#f-models').value.trim()) {
          $$('.fm-ck').slice(0, 50).forEach(c => c.checked = true);
          $('#f-models').value = r.models.slice(0, 50).map(m => m.id).join(', ');
          syncCount();
        }
      } catch (e) { hint.textContent = '失败: ' + e.message; hint.style.color = 'var(--red)'; }
    };
    $('#btn-save-chan').onclick = async () => {
      const body = {
        name: $('#f-name').value.trim(), preset: $('#f-preset').value,
        adapter: $('#f-adapter').value, base_url: $('#f-base').value.trim(),
        api_key: $('#f-key').value.trim(), models: $('#f-models').value.split(',').map(s => s.trim()).filter(Boolean),
        model_mapping: JSON.parse($('#f-mapping').value || '{}'),
        weight: +$('#f-w').value || 1, priority: +$('#f-pri').value || 0,
        enabled: $('#f-enabled').checked, proxy_url: $('#f-proxy').value.trim(),
        timeout: +$('#f-timeout').value || 0, note: $('#f-note').value.trim(),
        probe_mode: $('#f-probemode').value,
        azure_api_version: $('#f-azver').value.trim(),
      };
      if (!body.name || !body.base_url) return toast('名称和 Base URL 必填', 'err');
      try {
        if (isEdit) await apiPut('/admin/api/channels/' + p.id, body);
        else await apiPost('/admin/api/channels', body);
        closeModal(); toast('已保存', 'ok'); this.refresh();
      } catch (e) { toast(e.message, 'err'); }
    };
  },
  async del(id, c) {
    if (!confirm(`确定删除渠道「${c.name}」?`)) return;
    await apiDelete('/admin/api/channels/' + id);
    toast('已删除', 'ok'); this.refresh();
  },
  async test(id, btn) {
    btn.disabled = true; btn.textContent = '测试中…';
    try {
      const r = await apiPost(`/admin/api/channels/${id}/test`, {});
      if (r.ok) toast(`连通正常 (${r.status})`, 'ok');
      else toast(`失败: ${r.error || r.detail || r.status}`, 'err');
    } catch (e) { toast(e.message, 'err'); }
    btn.disabled = false; btn.textContent = '测试';
    this.refresh();
  },
};

/* ================= API Key ================= */
Pages.keys = {
  async render(main) {
    main.innerHTML = `
      <div class="page-head"><h2>API Key 管理</h2>
        <div class="actions"><button class="btn" id="btn-add-key">＋ 签发 Key</button></div></div>
      <div class="panel" style="padding:6px 10px">
        <table class="gw-table"><thead><tr>
          <th>名称</th><th>Key</th><th>额度(已用/总量)</th><th>模型白名单</th>
          <th>过期时间</th><th>状态</th><th>操作</th>
        </tr></thead><tbody id="key-tbody"></tbody></table>
      </div>`;
    $('#btn-add-key').onclick = () => this.edit(null);
    await this.refresh();
  },
  async refresh() {
    const list = await api('/admin/api/keys');
    const tb = $('#key-tbody');
    if (!list.length) { tb.innerHTML = `<tr><td colspan="7"><div class="empty-tip">暂无 Key</div></td></tr>`; return; }
    tb.innerHTML = list.map(k => {
      const quota = k.quota_tokens < 0 ? '无限' :
        `${fmtTokens(k.used_tokens)} / ${fmtTokens(k.quota_tokens)}`;
      const pct = k.quota_tokens > 0 ? Math.min(100, k.used_tokens / k.quota_tokens * 100) : 0;
      return `<tr>
        <td>${esc(k.name || '-')}<div class="dim" style="font-size:11px">${fmtTime(k.created_at)}</div></td>
        <td class="mono" style="font-size:12px">${esc(k.key)}</td>
        <td>${quota}${k.quota_tokens > 0 && pct >= 80 ? ' <span class="tag err">' + pct.toFixed(0) + '%</span>' : ''}</td>
        <td class="dim" style="font-size:12px;max-width:180px">${esc((k.allowed_models || []).join(', ')) || '不限'}</td>
        <td class="dim" style="font-size:12px">${k.expires_at ? fmtTime(k.expires_at) : '永不过期'}</td>
        <td>${k.enabled ? '<span class="tag ok">启用</span>' : '<span class="tag dim">停用</span>'}</td>
        <td style="white-space:nowrap">
          <button class="btn ghost" style="padding:3px 9px;font-size:12px" data-act="copy" data-key="${esc(k.key)}">复制</button>
          <button class="btn ghost" style="padding:3px 9px;font-size:12px" data-act="edit" data-id="${k.id}">编辑</button>
          <button class="btn danger" style="padding:3px 9px;font-size:12px" data-act="del" data-id="${k.id}">删除</button>
        </td></tr>`;
    }).join('');
    $$('#key-tbody button').forEach(b => b.onclick = async () => {
      const act = b.dataset.act;
      if (act === 'copy') {
        navigator.clipboard.writeText(b.dataset.key).then(() => toast('已复制', 'ok'));
      } else if (act === 'edit') {
        this.edit(list.find(x => x.id === +b.dataset.id));
      } else if (act === 'del') {
        const k = list.find(x => x.id === +b.dataset.id);
        if (confirm(`确定删除 Key「${k.name || k.key}」?`)) {
          await apiDelete('/admin/api/keys/' + k.id); toast('已删除', 'ok'); this.refresh();
        }
      }
    });
  },
  edit(k) {
    const isEdit = !!k;
    const p = k || {name: '', quota_tokens: -1, allowed_models: [], expires_at: null};
    openModal(isEdit ? '编辑 API Key' : '签发 API Key', `
      <div class="field"><label>名称</label><input id="k-name" value="${esc(p.name)}" placeholder="例如:我的客户端"></div>
      <div class="form-row">
        <div class="field"><label>令牌额度(-1 = 无限)</label><input id="k-quota" type="number" value="${p.quota_tokens}"></div>
        <div class="field"><label>过期时间(留空 = 永不过期)</label><input id="k-exp" type="datetime-local"
          value="${p.expires_at ? p.expires_at.slice(0, 19) : ''}"></div>
      </div>
      <div class="field"><label>允许模型(逗号分隔,留空 = 不限制)</label>
        <input id="k-models" value="${esc((p.allowed_models || []).join(', '))}"></div>
      ${isEdit ? `<div class="field"><label>已用 tokens(可手动重置)</label>
        <input id="k-used" type="number" value="${p.used_tokens}"></div>` : ''}
    `, `
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" id="btn-save-key">保存</button>`);
    $('#btn-save-key').onclick = async () => {
      const body = {
        name: $('#k-name').value.trim(), quota_tokens: +$('#k-quota').value,
        allowed_models: $('#k-models').value.split(',').map(s => s.trim()).filter(Boolean),
        expires_at: $('#k-exp').value ? new Date($('#k-exp').value).toISOString() : null,
      };
      if (isEdit) body.used_tokens = +$('#k-used').value || 0;
      try {
        const r = isEdit ? await apiPut('/admin/api/keys/' + p.id, body)
          : await apiPost('/admin/api/keys', body);
        closeModal();
        if (!isEdit) {
          openModal('Key 已签发', `
            <div class="field"><label>请立即复制,列表中只显示掩码</label>
              <input class="mono" id="newkey" value="${esc(r.key)}" readonly></div>`,
            `<button class="btn" onclick="navigator.clipboard.writeText('${esc(r.key)}').then(()=>toast('已复制','ok'))">复制</button>
             <button class="btn ghost" onclick="closeModal()">关闭</button>`);
        } else toast('已保存', 'ok');
        this.refresh();
      } catch (e) { toast(e.message, 'err'); }
    };
  },
};

/* ================= 模型单价 ================= */
Pages.prices = {
  async render(main) {
    main.innerHTML = `
      <div class="page-head"><h2>模型单价与元数据(元 / 百万 tokens)</h2>
        <div class="actions">
          <button class="btn ghost" id="btn-import">导入预设参考价</button>
          <button class="btn" id="btn-add-price">＋ 新增</button></div></div>
      <div class="panel" style="padding:6px 10px">
        <table class="gw-table"><thead><tr>
          <th>模型</th><th>上下文窗口</th><th>最大输出</th><th>输入价</th><th>输出价</th><th>币种</th><th>操作</th>
        </tr></thead><tbody id="price-tbody"></tbody></table>
      </div>`;
    $('#btn-import').onclick = async () => {
      const r = await apiPost('/admin/api/prices/import_presets', {});
      toast(`已导入 ${r.imported} 条参考单价`, 'ok'); this.refresh();
    };
    $('#btn-add-price').onclick = () => this.edit(null);
    await this.refresh();
  },
  async refresh() {
    const list = await api('/admin/api/prices');
    const tb = $('#price-tbody');
    if (!list.length) { tb.innerHTML = `<tr><td colspan="7"><div class="empty-tip">暂无记录;在渠道表单"自动获取模型"可一键带入元数据与参考价</div></td></tr>`; return; }
    tb.innerHTML = list.map(p => `<tr>
      <td class="mono">${esc(p.model)}</td>
      <td class="mono">${p.context_window ? fmtTokens(p.context_window) : '<span class="dim">-</span>'}</td>
      <td class="mono">${p.max_output ? fmtTokens(p.max_output) : '<span class="dim">-</span>'}</td>
      <td>¥ ${p.input_price}</td><td>¥ ${p.output_price}</td>
      <td class="dim">${esc(p.currency)}</td>
      <td><button class="btn ghost" style="padding:3px 9px;font-size:12px" data-m="${esc(p.model)}">编辑</button>
          <button class="btn danger" style="padding:3px 9px;font-size:12px" data-d="${esc(p.model)}">删除</button></td></tr>`).join('');
    $$('#price-tbody button').forEach(b => b.onclick = async () => {
      if (b.dataset.m) this.edit(list.find(x => x.model === b.dataset.m));
      else if (confirm('删除该记录?')) {
        await apiDelete('/admin/api/prices/' + encodeURIComponent(b.dataset.d));
        toast('已删除', 'ok'); this.refresh();
      }
    });
  },
  edit(p) {
    const isEdit = !!p;
    const d = p || {model: '', input_price: 0, output_price: 0, context_window: null, max_output: null};
    openModal(isEdit ? '编辑模型单价' : '新增模型单价', `
      <div class="field"><label>模型名 *</label><input id="p-model" value="${esc(d.model)}" ${isEdit ? 'readonly' : ''}></div>
      <div class="form-row">
        <div class="field"><label>输入单价(元/百万tokens)</label><input id="p-in" type="number" step="0.01" value="${d.input_price}"></div>
        <div class="field"><label>输出单价(元/百万tokens)</label><input id="p-out" type="number" step="0.01" value="${d.output_price}"></div>
      </div>
      <div class="form-row">
        <div class="field"><label>上下文窗口(tokens,可选)</label><input id="p-ctx" type="number" value="${d.context_window ?? ''}" placeholder="如 128000"></div>
        <div class="field"><label>最大输出(tokens,可选)</label><input id="p-max" type="number" value="${d.max_output ?? ''}" placeholder="如 8192"></div>
      </div>`, `
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" id="btn-save-price">保存</button>`);
    $('#btn-save-price').onclick = async () => {
      const model = $('#p-model').value.trim();
      if (!model) return toast('模型名必填', 'err');
      try {
        await apiPost('/admin/api/prices', {
          model,
          input_price: +$('#p-in').value || 0,
          output_price: +$('#p-out').value || 0,
          context_window: $('#p-ctx').value ? +$('#p-ctx').value : null,
          max_output: $('#p-max').value ? +$('#p-max').value : null,
        });
        closeModal(); toast('已保存', 'ok'); this.refresh();
      } catch (e) { toast(e.message, 'err'); }
    };
  },
};

/* ================= 用量统计 ================= */
Pages.usage = {
  async render(main) {
    main.innerHTML = `
      <div class="page-head"><h2>用量统计</h2>
        <div class="actions">
          <select id="u-days" style="width:auto">
            <option value="1">今日</option><option value="7" selected>近7天</option>
            <option value="30">近30天</option>
          </select></div></div>
      <div class="stat-grid" id="u-stats"></div>
      <div class="chart-flex" style="margin-bottom:18px">
        <div class="panel chart-panel"><h3>模型用量排行</h3><div id="c-model" style="height:300px"></div></div>
        <div class="panel chart-panel"><h3>渠道调用量</h3><div id="c-chan" style="height:300px"></div></div>
      </div>
      <div class="panel chart-panel" style="margin-bottom:18px">
        <h3>每日用量(近14天:Tokens / 模型调用 / 总调用)</h3>
        <div id="u-daily" style="height:280px"></div>
      </div>
      <div class="panel chart-panel" style="margin-bottom:18px">
        <h3>调用时段热点 · 近7天(周 × 24小时,颜色越亮调用越集中)</h3>
        <div id="u-heat" style="height:260px"></div>
      </div>
      <div class="panel" style="padding:6px 10px;margin-bottom:18px">
        <table class="gw-table"><thead><tr>
          <th>时间</th><th>Key</th><th>渠道</th><th>模型</th><th>输入tk</th><th>输出tk</th>
          <th>缓存命中</th><th>总tk</th><th>费用</th><th>延迟</th><th>状态</th><th>错误</th>
        </tr></thead><tbody id="log-tbody"></tbody></table>
      </div>`;
    $('#u-days').onchange = () => this.refresh();
    await this.refresh();
  },
  async refresh() {
    const days = $('#u-days') ? +$('#u-days').value : 7;
    const [ov, byModel, byChan, logs, heat, daily] = await Promise.all([
      api(`/admin/api/stats/overview?days=${days}`),
      api(`/admin/api/stats/by_model?days=${days}`),
      api(`/admin/api/stats/by_channel?days=${days}`),
      api(`/admin/api/stats/logs?limit=100`),
      api(`/admin/api/stats/hourly_heatmap?days=7`),
      api(`/admin/api/stats/daily?days=14`),
    ]);
    $('#u-stats').innerHTML = [
      ['总调用', ov.total_calls, 'cyan'], ['成功率', ov.success_rate + '%', 'green'],
      ['总 Tokens', fmtTokens(ov.total_tokens), 'purple'], ['总费用', fmtCost(ov.cost), 'amber'],
    ].map(([l, v, c]) => `<div class="panel stat-card">
      <div class="label"><span>${l}</span></div><div class="value ${c}">${v}</div></div>`).join('');

    disposeCharts();
    const cm = mkChart($('#c-model'));
    byModel.sort((a, b) => b.tokens - a.tokens);
    cm.setOption({
      tooltip: {trigger: 'axis', backgroundColor: '#0d1630', borderColor: 'rgba(0,229,255,.4)', textStyle: {color: '#d7e6ff', fontSize: 11}},
      grid: {left: 10, right: 30, top: 10, bottom: 10, containLabel: true},
      xAxis: {type: 'value', ...CHART_AXIS},
      yAxis: {type: 'category', data: byModel.map(x => x.name).reverse(),
        axisLabel: {...CHART_TEXT, width: 110, overflow: 'truncate'}},
      series: [{type: 'bar', data: byModel.map(x => x.tokens).reverse(),
        itemStyle: {color: {type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
          colorStops: [{offset: 0, color: '#3b82f6'}, {offset: 1, color: '#00e5ff'}]}},
        barWidth: 12}]});
    const cc = mkChart($('#c-chan'));
    cc.setOption({
      tooltip: {trigger: 'item', backgroundColor: '#0d1630', borderColor: 'rgba(0,229,255,.4)', textStyle: {color: '#d7e6ff', fontSize: 11}},
      legend: {bottom: 0, textStyle: CHART_TEXT, itemWidth: 10, itemHeight: 10},
      series: [{type: 'pie', radius: ['45%', '70%'], center: ['50%', '45%'],
        data: byChan.map(x => ({name: x.name, value: x.calls})),
        label: {color: '#6b83a8', fontSize: 11},
        itemStyle: {borderColor: '#060b18', borderWidth: 2}}]});
    const hmc = mkChart($('#u-heat'));
    const dlabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
    const hmData = [];
    let maxV = 0;
    (heat || []).forEach((row, di) => (row || []).forEach((v, hi) => {
      if (v) { hmData.push([hi, di, v]); maxV = Math.max(maxV, v); }
    }));
    hmc.setOption({
      tooltip: {backgroundColor: '#0d1630', borderColor: 'rgba(0,229,255,.4)',
        textStyle: {color: '#d7e6ff', fontSize: 11},
        formatter: p => `${dlabels[p.data[1]]} ${String(p.data[0]).padStart(2, '0')}:00<br/>调用 ${p.data[2]} 次`},
      grid: {left: 10, right: 16, top: 10, bottom: 56, containLabel: true},
      xAxis: {type: 'category', data: Array.from({length: 24}, (_, i) => String(i).padStart(2, '0')), ...CHART_AXIS},
      yAxis: {type: 'category', data: dlabels, ...CHART_AXIS},
      visualMap: {min: 0, max: Math.max(1, maxV), orient: 'horizontal', left: 'center', bottom: 0,
        itemWidth: 10, itemHeight: 90, textStyle: CHART_TEXT,
        inRange: {color: ['rgba(13,22,44,0.5)', '#3b82f6', '#00e5ff', '#10e0a0']}},
      series: [{type: 'heatmap', data: hmData,
        itemStyle: {borderColor: '#060b18', borderWidth: 1, borderRadius: 2},
        emphasis: {itemStyle: {shadowBlur: 8, shadowColor: 'rgba(0,229,255,.5)'}}}]});
    /* 每日用量图:tokens 柱状 + 调用折线 + 模型数次折线 */
    const dc = mkChart($('#u-daily'));
    const dd = daily.daily || [];
    if (dd.length) {
      dc.setOption({
        tooltip: {trigger: 'axis', backgroundColor: '#0d1630', borderColor: 'rgba(0,229,255,.4)',
          textStyle: {color: '#d7e6ff', fontSize: 11}},
        grid: {left: 10, right: 14, top: 34, bottom: 10, containLabel: true},
        legend: {data: ['Tokens', '缓存命中', '总调用', '模型数'], textStyle: CHART_TEXT,
          top: 0, right: 0, itemWidth: 12, itemHeight: 8},
        xAxis: {type: 'category', data: dd.map(x => x.date.slice(5)), ...CHART_AXIS},
        yAxis: [{type: 'value', ...CHART_AXIS},
                {type: 'value', ...CHART_AXIS, splitLine: {show: false}}],
        series: [
          {name: 'Tokens', type: 'bar', data: dd.map(x => x.total_tokens), barWidth: 12,
            itemStyle: {color: {type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [{offset: 0, color: '#00e5ff'}, {offset: 1, color: '#3b82f6'}]}}},
          {name: '缓存命中', type: 'bar', data: dd.map(x => x.cache_tokens), barWidth: 12,
            itemStyle: {color: '#8b5cf6'}},
          {name: '总调用', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 5,
            data: dd.map(x => x.calls), lineStyle: {color: '#10e0a0', width: 2},
            itemStyle: {color: '#10e0a0'}},
          {name: '模型数', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none',
            data: dd.map(x => x.models), lineStyle: {color: '#ffb020', width: 1.5, type: 'dashed'},
            itemStyle: {color: '#ffb020'}},
        ]});
    } else {
      dc.setOption({title: {text: '暂无数据', left: 'center', top: 'middle', textStyle: CHART_TEXT}});
    }
    /* 每日模型调用明细表格式化数据备用 */
    this._dailyModelCalls = daily.model_calls || {};

    const tb = $('#log-tbody');
    tb.innerHTML = logs.length ? logs.map(l => `<tr>
      <td class="dim" style="font-size:12px;white-space:nowrap">${fmtTime(l.created_at)}</td>
      <td>${esc(l.key_name || '-')}</td>
      <td>${esc(l.channel_name || '-')}</td>
      <td class="mono" style="font-size:12px">${esc(l.model)}</td>
      <td class="mono">${fmtTokens(l.prompt_tokens)}</td>
      <td class="mono">${fmtTokens(l.completion_tokens)}</td>
      <td class="mono">${l.cache_read_tokens || l.cache_creation_tokens
        ? `<span style="color:var(--purple)" title="读 ${l.cache_read_tokens || 0} / 写 ${l.cache_creation_tokens || 0}">⚡${fmtTokens((l.cache_read_tokens || 0) + (l.cache_creation_tokens || 0))}</span>`
        : '<span class="dim">-</span>'}</td>
      <td class="mono">${fmtTokens(l.total_tokens)}${l.estimated ? ' <span class="dim" title="估算">≈</span>' : ''}</td>
      <td>${fmtCost(l.cost)}</td>
      <td class="mono">${fmtMs(l.latency_ms)}</td>
      <td>${l.success ? '<span class="tag ok">' + l.status_code + '</span>'
        : `<span class="tag err">${l.status_code || 'ERR'}</span>${l.retries ? ' <span class="tag warn">重试' + l.retries + '</span>' : ''}`}</td>
      <td class="dim" style="font-size:11px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(l.error)}">${esc(l.error || '-')}</td>
    </tr>`).join('') : `<tr><td colspan="12"><div class="empty-tip">暂无调用记录</div></td></tr>`;
  },
};

/* ================= 系统设置 ================= */
Pages.settings = {
  async render(main) {
    main.innerHTML = `
      <div class="page-head"><h2>系统设置</h2></div>
      <div class="panel" style="padding:20px;max-width:560px">
        <div class="form-row">
          <div class="field"><label>渠道默认超时(秒)</label><input id="s-timeout" type="number"></div>
          <div class="field"><label>故障转移最大尝试渠道数</label><input id="s-retry" type="number"></div>
        </div>
        <div class="form-row">
          <div class="field"><label>熔断阈值(连续失败次数)</label><input id="s-threshold" type="number"></div>
          <div class="field"><label>熔断冷却(秒)</label><input id="s-cooldown" type="number"></div>
        </div>
        <div class="form-row">
          <div class="field"><label>渠道定时探测间隔(秒,0=关闭)</label><input id="s-probe" type="number">
            <div class="hint">探测使用上游模型列表端点,不消耗 token 额度</div></div>
        </div>
        <div class="field"><label>auto 模型偏好(逗号分隔,留空 = 自动扫描全部渠道模型)</label>
          <input id="s-auto" placeholder="如 deepseek-chat, gpt-4o-mini, claude-sonnet-4-5">
          <div class="hint">model=auto 时按此顺序尝试;未命中偏好时回退到 Key 白名单/全部模型</div></div>
        <div class="form-row">
          <div class="field"><label>auto 总超时(秒)</label><input id="s-auto-to" type="number">
            <div class="hint">auto 依次尝试候选模型的总时间预算,超时后返回失败</div></div>
          <div class="field"><label>auto 最大候选数</label><input id="s-auto-max" type="number">
            <div class="hint">未配偏好时最多尝试的模型数量(1-20)</div></div>
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end">
          <button class="btn" id="btn-save-set">保存设置</button></div>
      </div>
      <div class="panel" style="padding:20px;max-width:560px;margin-top:18px">
        <div class="field"><label>修改管理员密码</label></div>
        <div class="field"><input id="s-old" type="password" placeholder="原密码"></div>
        <div class="field"><input id="s-new" type="password" placeholder="新密码(至少6位)"></div>
        <div style="margin-top:14px;display:flex;justify-content:flex-end">
          <button class="btn" id="btn-pwd">修改密码</button></div>
      </div>`;
    const s = await api('/admin/api/settings');
    $('#s-timeout').value = s.default_timeout; $('#s-retry').value = s.max_retry;
    $('#s-threshold').value = s.breaker_threshold; $('#s-cooldown').value = s.breaker_cooldown;
    $('#s-probe').value = s.probe_interval;
    $('#s-auto').value = s.auto_models || '';
    $('#s-auto-to').value = s.auto_timeout || 120;
    $('#s-auto-max').value = s.auto_max_models || 5;
    $('#btn-save-set').onclick = async () => {
      try {
        await apiPost('/admin/api/settings', {
          default_timeout: $('#s-timeout').value, max_retry: $('#s-retry').value,
          breaker_threshold: $('#s-threshold').value, breaker_cooldown: $('#s-cooldown').value,
          probe_interval: $('#s-probe').value, auto_models: $('#s-auto').value,
          auto_timeout: $('#s-auto-to').value, auto_max_models: $('#s-auto-max').value});
        toast('已保存', 'ok');
      } catch (e) { toast(e.message, 'err'); }
    };
    $('#btn-pwd').onclick = async () => {
      try {
        await apiPost('/admin/api/password', {old: $('#s-old').value, new: $('#s-new').value});
        toast('密码已修改', 'ok'); $('#s-old').value = $('#s-new').value = '';
      } catch (e) { toast(e.message, 'err'); }
    };
  },
};

/* ================= 总览 ================= */
Pages.dashboard = {
  async render(main) {
    main.innerHTML = `
      <div class="page-head"><h2>总览</h2>
        <div class="actions">
          <button class="btn ghost" onclick="location.href='/docs'">接口文档</button>
          <button class="btn ghost" onclick="location.href='/screen'">打开大屏 →</button></div></div>
      <div class="stat-grid" id="d-stats"></div>
      <div class="chart-flex">
        <div class="panel chart-panel"><h3>24 小时调用趋势</h3><div id="d-trend" style="height:280px"></div></div>
        <div class="panel chart-panel"><h3>渠道健康(定时探测)</h3><div id="d-channels" style="height:280px;overflow-y:auto"></div></div>
      </div>
      <div class="chart-flex" style="margin-top:18px">
        <div class="panel chart-panel"><h3>热点模型排名(近7天)</h3><div id="d-hot" style="height:300px"></div></div>
        <div class="panel chart-panel"><h3>调用时段热点(近7天 · 周x24h)</h3><div id="d-heat" style="height:300px"></div></div>
      </div>`;
    await this.refresh();
  },
  async refresh() {
    const [ov, trend, channels, hot, heat] = await Promise.all([
      api('/admin/api/stats/overview?days=1'),
      api('/admin/api/stats/trend?days=1'),
      api('/admin/api/channels'),
      api('/admin/api/stats/hot_models?days=7&limit=10'),
      api('/admin/api/stats/hourly_heatmap?days=7'),
    ]);
    $('#d-stats').innerHTML = [
      ['今日调用', ov.total_calls, 'cyan'], ['今日 Tokens', fmtTokens(ov.today_tokens), 'purple'],
      ['平均延迟', fmtMs(ov.avg_latency_ms), 'amber'], ['在线渠道', `${ov.online_channels} / ${ov.total_channels}`, 'green'],
    ].map(([l, v, c]) => `<div class="panel stat-card">
      <div class="label"><span>${l}</span></div><div class="value ${c}">${v}</div></div>`).join('');

    disposeCharts();
    const chart = mkChart($('#d-trend'));
    chart.setOption({
      tooltip: {trigger: 'axis', backgroundColor: '#0d1630', borderColor: 'rgba(0,229,255,.4)',
        textStyle: {color: '#d7e6ff', fontSize: 11}},
      grid: {left: 10, right: 16, top: 30, bottom: 10, containLabel: true},
      legend: {data: ['调用量', 'Tokens'], textStyle: CHART_TEXT, top: 0, right: 0},
      xAxis: {type: 'category', data: trend.map(t => t.hour.slice(11)), ...CHART_AXIS},
      yAxis: [{type: 'value', ...CHART_AXIS}, {type: 'value', ...CHART_AXIS, splitLine: {show: false}}],
      series: [
        {name: '调用量', type: 'bar', data: trend.map(t => t.calls), barWidth: 8,
          itemStyle: {color: '#3b82f6'}},
        {name: 'Tokens', type: 'line', yAxisIndex: 1, smooth: true, data: trend.map(t => t.tokens),
          lineStyle: {color: '#00e5ff', width: 2}, itemStyle: {color: '#00e5ff'},
          areaStyle: {color: {type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{offset: 0, color: 'rgba(0,229,255,.25)'}, {offset: 1, color: 'rgba(0,229,255,0)'}]}}},
      ]});
    $('#d-channels').innerHTML = channels.length ? channels.map(c => {
      const dot = !c.enabled ? 'off' : (c.breaker_state === 'open' ? 'breaker' : c.breaker_state === 'half_open' ? 'warn' : 'on');
      const state = !c.enabled ? '停用' : c.breaker_state === 'open' ? '熔断中' : c.breaker_state === 'half_open' ? '半开探测' : '正常';
      let probe = '';
      if (c.probe_ok === true) probe = `<span class="tag ok" style="margin-left:6px">${c.probe_latency}ms</span>`;
      else if (c.probe_ok === false) probe = '<span class="tag err" style="margin-left:6px">离线</span>';
      return `<div style="display:flex;align-items:center;gap:10px;padding:10px 6px;border-bottom:1px solid rgba(0,229,255,.07)">
        <span class="chan-status-dot ${dot}"></span>
        <div style="flex:1">
          <div>${esc(c.name)} <span class="dim" style="font-size:11px">${esc(c.preset)}</span>${probe}</div>
          <div class="dim" style="font-size:11px">${(c.models || []).length} 个模型 · P${c.priority}/W${c.weight}${c.proxy_url ? ' · 代理' : ''}</div>
        </div>
        <span class="dim" style="font-size:12px">${state}</span></div>`;
    }).join('') : `<div class="empty-tip">暂无渠道</div>`;

    /* 热点模型排名 */
    const hc = mkChart($('#d-hot'));
    if (hot.length) {
      hc.setOption({
        tooltip: {trigger: 'axis', backgroundColor: '#0d1630', borderColor: 'rgba(0,229,255,.4)',
          textStyle: {color: '#d7e6ff', fontSize: 11}},
        grid: {left: 10, right: 40, top: 10, bottom: 10, containLabel: true},
        xAxis: {type: 'value', ...CHART_AXIS},
        yAxis: {type: 'category', data: hot.map(x => x.name).reverse(),
          axisLabel: {...CHART_TEXT, width: 120, overflow: 'truncate'}},
        series: [
          {type: 'bar', name: '调用次数', data: hot.map(x => x.calls).reverse(), barWidth: 10,
            itemStyle: {color: {type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
              colorStops: [{offset: 0, color: '#8b5cf6'}, {offset: 1, color: '#00e5ff'}]}}},
          {type: 'bar', name: '费用', xAxisIndex: 0, data: [], barWidth: 10},
        ]});
    } else { hc.setOption({title: {text: '暂无数据', left: 'center', top: 'middle', textStyle: CHART_TEXT}}); }

    /* 调用时段热力图(周 x 24h) */
    const hmc = mkChart($('#d-heat'));
    const days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
    const hmData = [];
    let maxV = 0;
    heat.forEach((row, di) => row.forEach((v, hi) => {
      if (v) { hmData.push([hi, di, v]); maxV = Math.max(maxV, v); }
    }));
    hmc.setOption({
      tooltip: {backgroundColor: '#0d1630', borderColor: 'rgba(0,229,255,.4)',
        textStyle: {color: '#d7e6ff', fontSize: 11},
        formatter: p => `${days[p.data[1]]} ${String(p.data[0]).padStart(2, '0')}:00<br/>调用 ${p.data[2]} 次`},
      grid: {left: 10, right: 14, top: 10, bottom: 40, containLabel: true},
      xAxis: {type: 'category', data: Array.from({length: 24}, (_, i) => String(i).padStart(2, '0')), ...CHART_AXIS},
      yAxis: {type: 'category', data: days, ...CHART_AXIS},
      visualMap: {min: 0, max: Math.max(1, maxV), calculable: false, orient: 'horizontal',
        left: 'center', bottom: 0, itemWidth: 10, itemHeight: 80,
        textStyle: CHART_TEXT,
        inRange: {color: ['rgba(13,22,44,0.6)', '#3b82f6', '#00e5ff', '#10e0a0']}},
      series: [{type: 'heatmap', data: hmData,
        itemStyle: {borderColor: '#060b18', borderWidth: 1, borderRadius: 2},
        emphasis: {itemStyle: {shadowBlur: 8, shadowColor: 'rgba(0,229,255,.5)'}}}]});
  },
};
