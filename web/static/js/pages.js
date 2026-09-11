/* 各管理页渲染逻辑 */
const Pages = {};
let CHARTS = [];
function disposeCharts() { CHARTS.forEach(c => c.dispose()); CHARTS = []; }
function mkChart(el) { const c = echarts.init(el); CHARTS.push(c); return c; }

/* ================= 渠道管理 ================= */
function _modelCells(c) {
  const models = c.models || [];
  if (!models.length) return '<span class="dim">-</span>';
  const status = c.model_status || {};
  const items = models.map(m => {
    const s = status[m];
    if (!s) return `<span style="font-size:12px">${esc(m)} <span class="dim" style="font-size:10px">未测</span></span>`;
    if (s.ok) return `<span style="font-size:12px">${esc(m)} <span class="tag ok" style="padding:1px 4px;font-size:10px" title="${esc(s.error || '')}">✓ ${s.latency_ms}ms</span></span>`;
    return `<span style="font-size:12px">${esc(m)} <span class="tag err" style="padding:1px 4px;font-size:10px" title="${esc(s.error || '')}">✗ ${s.status || 'err'}</span></span>`;
  });
  return `<div style="max-width:220px;line-height:1.6">${items.join('<br>')}</div>`;
}

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
        <td style="max-width:220px">${_modelCells(c)}</td>
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
  async edit(row) {
    let presets = [];
    try { presets = await api('/admin/api/presets'); } catch (e) {}
    const isEdit = !!(row && row.id);
    // 注意:不要用 c 覆盖入参(reassign 后再用 c.id 会导致取到列表数组,字段全部回落默认值)
    let c = row;
    if (isEdit) {
      try {
        const list = await api('/admin/api/channels');
        c = list.find(x => x.id === row.id) || row;
      } catch (e) { c = row; }
    }
    const p = c || {name: '', preset: 'custom', adapter: 'openai_compat', base_url: '',
      api_key: '', models: [], model_mapping: {}, weight: 1, priority: 0, enabled: true,
      proxy_url: '', pricing_override: {}, timeout: 0, note: '', azure_api_version: '2024-10-21',
      probe_mode: 'models'};
    // 编辑态 Key 为掩码(如 sk-ab***cd):留空保存则保持原 Key 不变
    const keyMasked = isEdit && (p.api_key || '').includes('***');
    openModal(isEdit ? '编辑渠道' : '新增渠道', `
      <div class="form-row">
        <div class="field"><label>渠道名称 *</label><input id="f-name" value="${esc(p.name)}"></div>
        <div class="field"><label>预设厂商</label>
          <select id="f-preset">${presets.map(x =>
            `<option value="${x.id}" ${x.id === p.preset ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></div>
      </div>
      <div class="form-row">
        <div class="field" style="flex:2"><label>Base URL *</label><input id="f-base" value="${esc(p.base_url)}" placeholder="https://api.deepseek.com"></div>
        <div class="field"><label>适配器</label>
          <select id="f-adapter">${['openai_compat','anthropic','gemini','azure'].map(a =>
            `<option ${a === p.adapter ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      </div>
      <div class="field"><label>API Key(多个用英文逗号分隔,自动轮询)</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="f-key" value="${esc(p.api_key || '')}" placeholder="sk-..." style="flex:1">
          <a id="f-keylink" class="btn ghost" style="padding:4px 10px;font-size:12px;white-space:nowrap;display:none" target="_blank" rel="noopener">获取 Key →</a>
        </div>
        ${keyMasked ? '<div class="hint">当前为掩码显示;留空保存则保持原 Key 不变,需更换请直接粘贴新 Key</div>' : ''}</div>
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
        <div class="field"><label>自定义 User-Agent(可选)</label>
          <input id="f-ua" value="${esc(p.user_agent || '')}" class="mono" placeholder="如 codex_cli_rs/0.20.0">
          <div class="hint">部分中转站校验客户端指纹,只放行官方客户端 UA,否则返回 401 unauthorized client</div></div>
      </div>
      <div class="field"><label>额外请求头(JSON,可选)</label>
        <textarea id="f-headers" rows="2" class="mono" style="resize:vertical" placeholder='{"X-Custom": "value"}'>${esc(JSON.stringify(p.extra_headers || {}))}</textarea></div>
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
      <div id="f-custom-fields" style="display:none">
        <div class="dim" style="font-size:12px;margin-bottom:6px;color:var(--cyan)">预设自定义字段(可选,由预设厂商定义)</div>
        <div id="f-custom-rows"></div>
      </div>
      <div class="field"><label>备注</label><input id="f-note" value="${esc(p.note || '')}"></div>
    `, `
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" id="btn-save-chan">保存</button>`);
    // 预设联动:填充基础信息 + 渲染自定义字段 + 获取 Key 链接
    const applyPreset = () => {
      const pr = presets.find(x => x.id === $('#f-preset').value);
      // 获取 Key 链接
      const keylink = $('#f-keylink');
      if (pr && pr.key_url) {
        keylink.href = pr.key_url;
        keylink.style.display = '';
      } else {
        keylink.style.display = 'none';
      }
      // 自定义字段
      const wrap = $('#f-custom-fields'), rows = $('#f-custom-rows');
      rows.innerHTML = '';
      const cf = isEdit ? (p.custom_fields || {}) : {};
      const hasCustom = pr && (pr.custom_1_key || pr.custom_2_key);
      if (hasCustom) {
        wrap.style.display = '';
        [1, 2].forEach(n => {
          const label = pr['custom_' + n + '_label'];
          const key = pr['custom_' + n + '_key'];
          const ph = pr['custom_' + n + '_placeholder'] || '';
          if (!key) return;
          rows.innerHTML += `<div class="field"><label>${esc(label || key)}</label>
            <input id="f-custom-${key}" value="${esc(cf[key] || '')}" placeholder="${esc(ph)}"></div>`;
        });
      } else {
        wrap.style.display = 'none';
      }
      // 新增态才自动填充基础信息
      if (pr && !isEdit) {
        $('#f-base').value = pr.base_url;
        $('#f-adapter').value = pr.adapter;
        $('#f-models').value = pr.models.join(', ');
        if (pr.probe_mode) $('#f-probemode').value = pr.probe_mode;
        if (pr.user_agent) $('#f-ua').value = pr.user_agent;
      }
    };
    $('#f-preset').onchange = applyPreset;
    applyPreset();  // 初始渲染(编辑态也要显示自定义字段与链接)
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
      // 收集预设自定义字段值
      const customFields = {};
      const pr = presets.find(x => x.id === $('#f-preset').value);
      [1, 2].forEach(n => {
        const key = pr && pr['custom_' + n + '_key'];
        if (!key) return;
        const el = $('#f-custom-' + key);
        if (el && el.value.trim()) customFields[key] = el.value.trim();
      });
      const body = {
        name: $('#f-name').value.trim(), preset: $('#f-preset').value,
        adapter: $('#f-adapter').value, base_url: $('#f-base').value.trim(),
        api_key: $('#f-key').value.trim(), models: $('#f-models').value.split(',').map(s => s.trim()).filter(Boolean),
        model_mapping: JSON.parse($('#f-mapping').value || '{}'),
        weight: +$('#f-w').value || 1, priority: +$('#f-pri').value || 0,
        enabled: $('#f-enabled').checked, proxy_url: $('#f-proxy').value.trim(),
        timeout: +$('#f-timeout').value || 0, note: $('#f-note').value.trim(),
        probe_mode: $('#f-probemode').value,
        user_agent: $('#f-ua').value.trim(),
        extra_headers: $('#f-headers').value.trim() ? JSON.parse($('#f-headers').value) : {},
        custom_fields: customFields,
        azure_api_version: $('#f-azver').value.trim(),
      };
      if (!body.name || !body.base_url) return toast('名称和 Base URL 必填', 'err');
      if (isEdit && body.api_key.includes('***')) delete body.api_key;  // 未修改则不发,保持原 Key
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
      if (r.models && Object.keys(r.models).length > 0) {
        // 多模型结果用弹窗展示
        const rows = Object.entries(r.models).map(([m, s]) =>
          `<tr><td class="mono" style="font-size:12px">${esc(m)}</td>` +
          `<td>${s.ok ? '<span class="tag ok">可用</span>' : '<span class="tag err">不可用</span>'}</td>` +
          `<td class="mono">${s.latency_ms || '-'} ms</td>` +
          `<td style="font-size:11px;color:#aaa;max-width:300px;word-break:break-all">${esc(s.error || '-')}</td>` +
          `<td class="dim" style="font-size:10px">${s.tested_at ? fmtTime(s.tested_at) : '-'}</td></tr>`
        ).join('');
        openModal(`深度测试结果 — ${esc(r.summary || '')}`, `
          <table class="gw-table"><thead><tr>
            <th>模型</th><th>状态</th><th>延迟</th><th>信息</th><th>测试时间</th>
          </tr></thead><tbody>${rows}</tbody></table>`);
      } else if (r.ok) {
        toast(`连通正常 (${r.status})`, 'ok');
      } else {
        toast(`失败: ${r.error || r.detail || r.status}`, 'err');
      }
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

/* ================= 调用日志 ================= */
Pages.logs = {
  state: {page: 1, size: 20},
  async render(main) {
    main.innerHTML = `
      <div class="page-head"><h2>调用日志</h2>
        <div class="actions">
          <button class="btn ghost" id="lg-clean">清理旧日志</button>
          <button class="btn danger" id="lg-clear">清空全部</button></div></div>
      <div class="panel" style="padding:14px 16px;margin-bottom:16px">
        <div class="form-row" style="margin-bottom:10px">
          <div class="field"><label>模型(模糊)</label><input id="lg-model" placeholder="如 gpt-4o / auto"></div>
          <div class="field"><label>渠道</label><select id="lg-channel"><option value="">全部</option></select></div>
          <div class="field"><label>API Key</label><select id="lg-key"><option value="">全部</option></select></div>
          <div class="field"><label>状态</label><select id="lg-success">
            <option value="">全部</option><option value="true">成功</option><option value="false">失败</option></select></div>
        </div>
        <div class="form-row">
          <div class="field"><label>关键词(请求/响应/错误/IP)</label><input id="lg-q" placeholder="全文搜索"></div>
          <div class="field"><label>请求 ID</label><input id="lg-rid" placeholder="X-Request-Id"></div>
          <div class="field"><label>类型</label><select id="lg-stream">
            <option value="">全部</option><option value="true">流式</option><option value="false">非流式</option></select></div>
          <div class="field" style="display:flex;align-items:flex-end;gap:8px">
            <button class="btn" id="lg-search">查询</button>
            <button class="btn ghost" id="lg-reset">重置</button></div>
        </div>
      </div>
      <div class="panel" style="padding:6px 10px;margin-bottom:12px">
        <table class="gw-table"><thead><tr>
          <th>时间</th><th>请求ID</th><th>模型</th><th>渠道</th><th>Key</th>
          <th>类型</th><th>Tokens(入/出)</th><th>费用</th><th>延迟</th><th>状态</th><th>客户端</th><th>操作</th>
        </tr></thead><tbody id="lg-tbody"></tbody></table>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="dim" id="lg-total">-</span>
        <span style="display:flex;gap:8px;align-items:center">
          <select id="lg-size" style="width:auto"><option>20</option><option>50</option><option>100</option></select>
          <button class="btn ghost" id="lg-prev">上一页</button>
          <span class="mono dim" id="lg-page">1</span>
          <button class="btn ghost" id="lg-next">下一页</button>
        </span>
      </div>`;
    // 填充渠道/Key 下拉
    try {
      const chans = await api('/admin/api/channels');
      $('#lg-channel').innerHTML = '<option value="">全部</option>' +
        chans.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
      const keys = await api('/admin/api/keys');
      $('#lg-key').innerHTML = '<option value="">全部</option>' +
        keys.map(k => `<option value="${k.id}">${esc(k.name || k.key)}</option>`).join('');
    } catch (e) { /* ignore */ }

    $('#lg-search').onclick = () => { this.state.page = 1; this.refresh(); };
    $('#lg-reset').onclick = () => {
      ['lg-model', 'lg-q', 'lg-rid'].forEach(id => $('#' + id).value = '');
      ['lg-channel', 'lg-key', 'lg-success', 'lg-stream'].forEach(id => $('#' + id).value = '');
      this.state.page = 1; this.refresh();
    };
    $('#lg-size').value = this.state.size;
    $('#lg-size').onchange = () => { this.state.size = +$('#lg-size').value; this.state.page = 1; this.refresh(); };
    $('#lg-prev').onclick = () => { if (this.state.page > 1) { this.state.page--; this.refresh(); } };
    $('#lg-next').onclick = () => { this.state.page++; this.refresh(); };
    $('#lg-clean').onclick = async () => {
      const d = prompt('清理多少天前的日志?(输入天数,如 7)', '7');
      if (!d) return;
      const r = await apiDelete(`/admin/api/logs?days=${+d}`);
      toast(`已清理 ${r.deleted} 条`, 'ok'); this.refresh();
    };
    $('#lg-clear').onclick = async () => {
      if (!confirm('确定清空全部调用日志?此操作不可恢复')) return;
      const r = await apiDelete('/admin/api/logs?days=0');
      toast(`已清空 ${r.deleted} 条`, 'ok'); this.state.page = 1; this.refresh();
    };
    await this.refresh();
  },
  async refresh() {
    const p = new URLSearchParams({page: this.state.page, page_size: this.state.size});
    const get = (id) => ($('#' + id) ? $('#' + id).value.trim() : '');
    ['lg-model', 'lg-q', 'lg-rid'].forEach(id => { const v = get(id); if (v) p.set(id.replace('lg-', ''), v); });
    if (get('lg-channel')) p.set('channel_id', get('lg-channel'));
    if (get('lg-key')) p.set('key_id', get('lg-key'));
    if (get('lg-success')) p.set('success', get('lg-success'));
    if (get('lg-stream')) p.set('stream', get('lg-stream'));

    const r = await api('/admin/api/logs?' + p.toString());
    this.state.page = r.page;
    $('#lg-total').textContent = `共 ${r.total} 条 · 第 ${r.page}/${r.pages || 1} 页`;
    $('#lg-page').textContent = r.page;
    $('#lg-prev').disabled = r.page <= 1;
    $('#lg-next').disabled = r.page >= (r.pages || 1);
    const tb = $('#lg-tbody');
    tb.innerHTML = r.items.length ? r.items.map(l => `<tr>
      <td class="dim" style="font-size:12px;white-space:nowrap">${fmtTime(l.created_at)}</td>
      <td class="mono" style="font-size:11px">${esc(l.request_id || '-')}</td>
      <td class="mono" style="font-size:12px">${esc(l.model_actual || l.model_requested || '-')}
        ${l.model_requested === 'auto' ? '<span class="tag info">auto</span>' : ''}</td>
      <td>${esc(l.channel_name || '-')}</td>
      <td>${esc(l.key_name || '-')}</td>
      <td>${l.is_stream ? '<span class="tag info">流式</span>' : '<span class="dim">普通</span>'}</td>
      <td class="mono">${fmtTokens(l.prompt_tokens)} / ${fmtTokens(l.completion_tokens)}${l.cache_read_tokens ? ` <span style="color:var(--purple)">⚡${fmtTokens(l.cache_read_tokens)}</span>` : ''}</td>
      <td>${fmtCost(l.cost)}</td>
      <td class="mono">${fmtMs(l.latency_ms)}</td>
      <td>${l.success ? '<span class="tag ok">' + l.status_code + '</span>'
        : `<span class="tag err">${l.status_code || 'ERR'}</span>${l.retries ? ' <span class="tag warn">重试' + l.retries + '</span>' : ''}`}</td>
      <td class="dim" style="font-size:11px">${esc(l.client_ip || '-')}</td>
      <td><button class="btn ghost" style="padding:3px 9px;font-size:12px" data-id="${l.id}">详情</button></td>
    </tr>`).join('') : `<tr><td colspan="12"><div class="empty-tip">暂无调用日志</div></td></tr>`;
    $$('#lg-tbody button').forEach(b => b.onclick = () => this.detail(+b.dataset.id));
  },
  async detail(id) {
    const d = await api('/admin/api/logs/' + id);
    const pretty = (s) => {
      if (!s) return '<span class="dim">(未记录内容)</span>';
      try { return esc(JSON.stringify(JSON.parse(s), null, 2)); } catch (e) { return esc(s); }
    };
    openModal(`调用日志 #${d.id}`, `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 16px;font-size:12.5px">
        <div><span class="dim">时间:</span> ${fmtTime(d.created_at)}</div>
        <div><span class="dim">请求ID:</span> <span class="mono">${esc(d.request_id || '-')}</span></div>
        <div><span class="dim">客户端:</span> ${esc(d.client_ip || '-')}</div>
        <div><span class="dim">模型:</span> ${esc(d.model_requested || '-')} → ${esc(d.model_actual || '-')}</div>
        <div><span class="dim">渠道:</span> ${esc(d.channel_name || '-')}</div>
        <div><span class="dim">Key:</span> ${esc(d.key_name || '-')}</div>
        <div><span class="dim">状态:</span> ${d.success ? '<span class="tag ok">' + d.status_code + ' 成功</span>' : '<span class="tag err">' + (d.status_code || 'ERR') + ' 失败</span>'}</div>
        <div><span class="dim">延迟:</span> ${fmtMs(d.latency_ms)} · 重试 ${d.retries || 0} 次</div>
        <div><span class="dim">Tokens:</span> 入 ${d.prompt_tokens} / 出 ${d.completion_tokens} / 缓存 ${d.cache_read_tokens}</div>
        <div><span class="dim">费用:</span> ${fmtCost(d.cost)}</div>
      </div>
      ${d.error ? `<div class="field"><label>错误信息</label><pre class="code" style="margin:0;color:var(--red)">${esc(d.error)}</pre></div>` : ''}
      <div class="field"><label>请求体</label><pre class="code" style="margin:0;max-height:220px;overflow:auto">${pretty(d.request_body)}</pre></div>
      <div class="field"><label>响应内容</label><pre class="code" style="margin:0;max-height:220px;overflow:auto">${pretty(d.response_body)}</pre></div>
      <div class="dim" style="font-size:11px">User-Agent: ${esc(d.user_agent || '-')}</div>
    `, `<button class="btn ghost" onclick="closeModal()">关闭</button>`);
  },
};

/* ================= 系统设置 ================= */
Pages.settings = {
  subPage: 'basic',
  async render(main) {
    main.innerHTML = `
      <div class="page-head"><h2>系统设置</h2></div>
      <div class="sub-nav" style="display:flex;gap:4px;margin-bottom:16px">
        <button class="btn ghost sub-nav-btn ${this.subPage === 'basic' ? 'active' : ''}" data-sub="basic">基本设置</button>
        <button class="btn ghost sub-nav-btn ${this.subPage === 'presets' ? 'active' : ''}" data-sub="presets">预设厂商</button>
      </div>
      <div id="settings-content"></div>`;
    $$('.sub-nav-btn').forEach(b => b.onclick = () => {
      this.subPage = b.dataset.sub;
      $$('.sub-nav-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      this.renderSub();
      // 写入 hash,刷新/前进后退可保留子页
      if (b.dataset.sub === 'presets') history.replaceState(null, '', '#settings:presets');
      else history.replaceState(null, '', window.location.pathname);
    });
    this.renderSub();
  },
  async renderSub() {
    if (this.subPage === 'presets') await this.renderPresets();
    else await this.renderBasic();
  },
  async renderBasic() {
    const c = $('#settings-content');
    c.innerHTML = `
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
        <h3 style="font-size:14px;color:var(--text-dim);margin-bottom:14px">调用日志</h3>
        <div class="form-row">
          <div class="field"><label>记录请求/响应内容</label>
            <select id="s-logbodies">
              <option value="1">记录(便于排查)</option>
              <option value="0">不记录(仅元数据)</option></select></div>
          <div class="field"><label>内容截断上限(字符)</label><input id="s-logmax" type="number"></div>
        </div>
        <div class="field"><label>日志保留天数(0 = 永久保留)</label><input id="s-logdays" type="number">
          <div class="hint">过期日志由后台定时任务自动清理</div></div>
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
    $('#s-logbodies').value = s.log_bodies === '0' || s.log_bodies === 0 ? '0' : '1';
    $('#s-logmax').value = s.log_body_max ?? 2000;
    $('#s-logdays').value = s.log_retention_days ?? 7;
    $('#btn-save-set').onclick = async () => {
      try {
        await apiPost('/admin/api/settings', {
          default_timeout: $('#s-timeout').value, max_retry: $('#s-retry').value,
          breaker_threshold: $('#s-threshold').value, breaker_cooldown: $('#s-cooldown').value,
          probe_interval: $('#s-probe').value, auto_models: $('#s-auto').value,
          auto_timeout: $('#s-auto-to').value, auto_max_models: $('#s-auto-max').value,
          log_bodies: $('#s-logbodies').value, log_body_max: $('#s-logmax').value,
          log_retention_days: $('#s-logdays').value});
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
  /* ---------- 预设厂商子页 ---------- */
  async renderPresets() {
    const c = $('#settings-content');
    c.innerHTML = `
      <div class="panel" style="padding:6px 10px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div class="dim" style="font-size:12px">管理预设厂商,新增渠道时可选;支持两个自定义字段与快速获取 Key 链接</div>
          <button class="btn" id="btn-add-preset">＋ 新增预设</button>
        </div>
        <table class="gw-table"><thead><tr>
          <th>ID</th><th>名称</th><th>适配器</th><th>Base URL</th><th>模型数</th>
          <th>自定义字段</th><th>获取Key</th><th>内置</th><th>操作</th>
        </tr></thead><tbody id="preset-tbody"></tbody></table>
      </div>`;
    $('#btn-add-preset').onclick = () => this.presetEdit(null);
    await this.presetRefresh();
  },
  async presetRefresh() {
    const list = await api('/admin/api/presets');
    const tb = $('#preset-tbody');
    if (!list.length) { tb.innerHTML = `<tr><td colspan="9"><div class="empty-tip">暂无预设</div></td></tr>`; return; }
    tb.innerHTML = list.map(p => {
      const customs = [p.custom_1_key, p.custom_2_key].filter(Boolean);
      return `<tr>
        <td class="mono" style="font-size:12px">${esc(p.id)}</td>
        <td><b>${esc(p.name)}</b>${p.note ? `<div class="dim" style="font-size:11px">${esc(p.note)}</div>` : ''}</td>
        <td class="mono" style="font-size:12px">${esc(p.adapter)}</td>
        <td class="mono" style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(p.base_url)}">${esc(p.base_url || '-')}</td>
        <td class="mono">${(p.models || []).length}</td>
        <td class="dim" style="font-size:11px">${customs.length ? customs.map(k => esc(k)).join(', ') : '-'}</td>
        <td>${p.key_url ? `<a href="${esc(p.key_url)}" target="_blank" rel="noopener" style="color:var(--cyan);font-size:11px">链接 ↗</a>` : '<span class="dim">-</span>'}</td>
        <td>${p.is_built_in ? '<span class="tag info">内置</span>' : '<span class="dim">自定义</span>'}</td>
        <td style="white-space:nowrap">
          <button class="btn ghost" style="padding:3px 9px;font-size:12px" data-act="edit" data-id="${esc(p.id)}">编辑</button>
          <button class="btn danger" style="padding:3px 9px;font-size:12px" data-act="del" data-id="${esc(p.id)}" ${p.is_built_in ? 'disabled title="内置不可删除"' : ''}>删除</button></td></tr>`;
    }).join('');
    $$('#preset-tbody button').forEach(b => b.onclick = () => {
      const act = b.dataset.act, id = b.dataset.id;
      if (act === 'edit') this.presetEdit(list.find(x => x.id === id));
      else if (act === 'del') this.presetDel(id);
    });
  },
  presetEdit(p) {
    const isEdit = !!p;
    const d = p || {id: '', name: '', adapter: 'openai_compat', base_url: '', models: [],
      probe_mode: 'models', user_agent: '', needs_proxy: false, local: false, note: '',
      key_url: '', custom_1_label: '', custom_1_key: '', custom_1_placeholder: '',
      custom_2_label: '', custom_2_key: '', custom_2_placeholder: ''};
    const escM = (s) => esc(JSON.stringify(s || [], null, 0));
    openModal(isEdit ? '编辑预设' : '新增预设', `
      <div class="form-row">
        <div class="field"><label>预设 ID *</label><input id="pr-id" value="${esc(d.id)}" ${isEdit ? 'readonly' : ''} placeholder="如 agnes(英文,唯一标识)"></div>
        <div class="field"><label>显示名称 *</label><input id="pr-name" value="${esc(d.name)}" placeholder="如 Agnes"></div>
      </div>
      <div class="form-row">
        <div class="field" style="flex:2"><label>Base URL</label><input id="pr-base" value="${esc(d.base_url)}" placeholder="https://..."></div>
        <div class="field"><label>适配器</label>
          <select id="pr-adapter">${['openai_compat','anthropic','gemini','azure'].map(a =>
            `<option ${a === d.adapter ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      </div>
      <div class="form-row">
        <div class="field"><label>探测方式</label>
          <select id="pr-probe">
            <option value="models" ${d.probe_mode === 'models' ? 'selected' : ''}>模型列表端点(免费)</option>
            <option value="chat" ${d.probe_mode === 'chat' ? 'selected' : ''}>聊天端点(极少消耗)</option>
            <option value="off" ${d.probe_mode === 'off' ? 'selected' : ''}>不探测</option>
          </select></div>
        <div class="field"><label>自定义 User-Agent</label><input id="pr-ua" value="${esc(d.user_agent)}" placeholder="可选"></div>
      </div>
      <div class="form-row">
        <div class="field"><label>常用模型(逗号分隔)</label>
          <input id="pr-models" value="${esc((d.models || []).join(', '))}" placeholder="model-a, model-b"></div>
        <div class="field"><label>快速获取 Key 链接</label>
          <input id="pr-keyurl" value="${esc(d.key_url)}" placeholder="https://.../api-keys"></div>
      </div>
      <div class="form-row">
        <div class="field"><label>自定义字段1 名称</label><input id="pr-c1l" value="${esc(d.custom_1_label)}" placeholder="如 Organization ID"></div>
        <div class="field"><label>键名</label><input id="pr-c1k" value="${esc(d.custom_1_key)}" placeholder="如 openai-organization"></div>
        <div class="field"><label>占位提示</label><input id="pr-c1p" value="${esc(d.custom_1_placeholder)}" placeholder="可选"></div>
      </div>
      <div class="form-row">
        <div class="field"><label>自定义字段2 名称</label><input id="pr-c2l" value="${esc(d.custom_2_label)}" placeholder="如 Project ID"></div>
        <div class="field"><label>键名</label><input id="pr-c2k" value="${esc(d.custom_2_key)}" placeholder="如 project-id"></div>
        <div class="field"><label>占位提示</label><input id="pr-c2p" value="${esc(d.custom_2_placeholder)}" placeholder="可选"></div>
      </div>
      <div class="form-row">
        <div class="field" style="display:flex;align-items:center;gap:18px">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="pr-proxy" ${d.needs_proxy ? 'checked' : ''}> 需代理访问</label>
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="pr-local" ${d.local ? 'checked' : ''}> 本地服务</label>
        </div>
      </div>
      <div class="field"><label>备注</label><input id="pr-note" value="${esc(d.note)}" placeholder="可选说明"></div>
    `, `
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" id="btn-save-preset">保存</button>`);
    $('#btn-save-preset').onclick = async () => {
      const id = $('#pr-id').value.trim();
      if (!id || !$('#pr-name').value.trim()) return toast('ID 和名称必填', 'err');
      const body = {
        id, name: $('#pr-name').value.trim(),
        adapter: $('#pr-adapter').value, base_url: $('#pr-base').value.trim(),
        models: $('#pr-models').value.split(',').map(s => s.trim()).filter(Boolean),
        probe_mode: $('#pr-probe').value, user_agent: $('#pr-ua').value.trim(),
        key_url: $('#pr-keyurl').value.trim(),
        custom_1_label: $('#pr-c1l').value.trim(), custom_1_key: $('#pr-c1k').value.trim(),
        custom_1_placeholder: $('#pr-c1p').value.trim(),
        custom_2_label: $('#pr-c2l').value.trim(), custom_2_key: $('#pr-c2k').value.trim(),
        custom_2_placeholder: $('#pr-c2p').value.trim(),
        needs_proxy: $('#pr-proxy').checked, local: $('#pr-local').checked,
        note: $('#pr-note').value.trim(),
      };
      try {
        if (isEdit) await apiPut('/admin/api/presets/' + id, body);
        else await apiPost('/admin/api/presets', body);
        closeModal(); toast('已保存', 'ok'); this.presetRefresh();
      } catch (e) { toast(e.message, 'err'); }
    };
  },
  async presetDel(id) {
    if (!confirm(`确定删除预设「${id}」?`)) return;
    try {
      await apiDelete('/admin/api/presets/' + encodeURIComponent(id));
      toast('已删除', 'ok'); this.presetRefresh();
    } catch (e) { toast(e.message, 'err'); }
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
