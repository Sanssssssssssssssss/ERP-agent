/* Local-only interaction prototype. It deliberately has no network or ERP calls. */
(function () {
  'use strict';

  var modeMeta = {
    ready: { label: '待执行', tone: 'neutral', icon: '○' },
    running: { label: '执行中', tone: 'blue', icon: '◌' },
    approval: { label: '待审批', tone: 'amber', icon: '!' },
    success: { label: '成功', tone: 'green', icon: '✓' },
    failed: { label: '失败', tone: 'red', icon: '×' },
    uncertain: { label: '写入待核对', tone: 'purple', icon: '?' }
  };

  var state = {
    mode: 'ready',
    business: 'sales',
    page: 'execution',
    selectedStep: 0,
    selectedResource: 'so',
    selectedTool: 'tool-read-customer',
    approvalGranted: false,
    railCollapsed: false,
    assistantOpen: window.innerWidth > 1440,
    rejectOpen: false,
    rejectionReason: '',
    actionLocked: false,
    messages: [
      { kind: 'assistant', text: '这是一个本地交互原型。切换左侧状态或页面，可查看业务负责人和技术评估所需的不同证据。', time: '09:30' },
      { kind: 'assistant', text: '示例目标：为 Nimbus Bureau 准备销售单与一张关联客户发票。', time: '09:31' }
    ]
  };

  var stepNames = ['读取约束', '生成报价', '审批确认', '开票过账', '回读核验'];
  var stepNotes = ['客户、商品与付款条件', '销售单草稿与价格', '订单确认写入', '客户发票过账', '订单与发票独立回读'];
  var refs = {
    shell: document.getElementById('appShell'), rail: document.getElementById('sessionRail'), content: document.getElementById('content'),
    modeLabel: document.getElementById('modeLabel'), title: document.getElementById('businessTitle'), goal: document.getElementById('businessGoal'),
    messageList: document.getElementById('messageList'), toast: document.getElementById('toast'), assistant: document.getElementById('assistantPanel')
  };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char];
    });
  }
  function mode() { return modeMeta[state.mode]; }
  function pill(extra) { return '<span class="status-pill ' + mode().tone + '"><span class="status-icon ' + mode().tone + '">' + mode().icon + '</span>' + mode().label + (extra || '') + '</span>'; }
  function statusLabel(label, tone) { return '<span class="table-status ' + (tone || '') + '"><i></i>' + esc(label) + '</span>'; }
  function showToast(text, error) {
    refs.toast.textContent = text;
    refs.toast.className = 'toast' + (error ? ' error' : '');
    refs.toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(function () { refs.toast.hidden = true; }, 3000);
  }

  function factsFor(modeName) {
    if (modeName === 'ready' || (modeName === 'running' && !state.approvalGranted)) return [];
    var rows = [];
    if (modeName === 'running' && state.approvalGranted) rows.push({ object: 'sale.order', name: 'SO-示例-001', status: '草稿（已获准确认）', tone: 'info', source: '审批回执', observed: '2026-09-08 09:33' });
    if (modeName === 'approval' || modeName === 'failed') rows.push({ object: 'sale.order', name: 'SO-示例-001', status: modeName === 'failed' ? '草稿（确认被拒）' : '草稿', tone: modeName === 'failed' ? 'error' : 'info', source: modeName === 'failed' ? '审批拒绝回执' : '生成报价', observed: '2026-09-08 09:32' });
    if (modeName === 'uncertain') {
      rows.push({ object: 'sale.order', name: 'SO-示例-001', status: '已确认（最后已知）', tone: 'success', source: '订单确认回执', observed: '2026-09-08 09:34' });
      rows.push({ object: 'account.move', name: 'INV-示例-001', status: '草稿（过账结果待核对）', tone: 'warn', source: '发票创建回执', observed: '2026-09-08 09:35' });
    }
    if (modeName === 'success') {
      rows.push({ object: 'sale.order', name: 'SO-示例-001', status: '已确认', tone: 'success', source: '订单确认回执', observed: '2026-09-08 09:34' });
      rows.push({ object: 'account.move', name: 'INV-示例-001', status: '已过账', tone: 'success', source: '发票过账回执', observed: '2026-09-08 09:35' });
    }
    return rows;
  }

  function traceFor(modeName) {
    if (modeName === 'ready') return [];
    var read = { label: '读取约束', tools: [
      { id: 'tool-read-customer', stage: 0, name: '读取客户约束', method: 'read_record', elapsed: '420 ms', input: '{model: "res.partner", record_id: 10}', output: 'Nimbus Bureau；客户 notes 已读取' },
      { id: 'tool-read-product', stage: 0, name: '读取商品与价格', method: 'read_record', elapsed: '388 ms', input: '{model: "product.product", record_id: 20}', output: 'Open-Plan Noise Barrier Wall；list price=695.22 USD' },
      { id: 'tool-read-payment', stage: 0, name: '读取付款条件', method: 'read_record', elapsed: '302 ms', input: '{model: "account.payment.term", record_id: 2}', output: '30 Days' }
    ] };
    if (modeName === 'running' && !state.approvalGranted) return [read];
    var preview = { label: '预览报价', tools: [{ id: 'tool-preview', stage: 1, name: '预览销售单草稿', method: 'preview_write', elapsed: '188 ms', input: '{model: "sale.order", operation: "create"}', output: '仅预览，尚未写入；动作 action-create-sale-001 待审批' }] };
    var create = { label: '创建销售草稿', tools: [{ id: 'tool-create-sale', stage: 1, name: '创建销售单草稿', method: 'execute_approved_write', elapsed: '610 ms', input: '{action_id: "action-create-sale-001"}', output: '审批 approval-create-sale-001 已批准；创建 SO-示例-001，state=draft，数量=1，单价=695.22 USD' }] };
    var gate = { label: '审批订单确认', tools: [{ id: modeName === 'failed' ? 'tool-reject' : 'tool-validate', stage: 2, name: '预检订单确认', method: 'validate_write', elapsed: '206 ms', input: '{model: "sale.order", operation: "action_confirm", record_id: 42}', output: modeName === 'failed' ? '预检已返回；附审批决定：拒绝。订单未确认，原草稿保留。' : state.approvalGranted ? '附审批决定：approval-confirm-002 已批准，动作尚未执行。' : '动作 action-confirm-002 可审批；此回执不代表订单已确认。' }] };
    var prefix = [read, preview, create, gate];
    if (modeName === 'approval' || modeName === 'failed' || modeName === 'running') return prefix;
    var confirm = { label: '确认销售订单', tools: [{ id: 'tool-confirm', stage: 2, name: '执行订单确认', method: 'execute_approved_write', elapsed: '610 ms', input: '{action_id: "action-confirm-002"}', output: '审批 approval-confirm-002 已批准；SO-示例-001，state=sale' }] };
    var invoice = { label: '创建客户发票', tools: [{ id: 'tool-create-invoice', stage: 3, name: '创建客户发票草稿', method: 'execute_approved_write', elapsed: '704 ms', input: '{action_id: "action-invoice-create-003"}', output: '审批 approval-invoice-create-003 已批准；INV-示例-001，state=draft，关联 SO-示例-001' }] };
    var post = { label: '过账客户发票', tools: [{ id: modeName === 'uncertain' ? 'tool-invoice' : 'tool-post', stage: 3, name: '提交已批准的发票过账', method: 'execute_approved_write', elapsed: '820 ms', input: '{action_id: "action-invoice-post-004"}', output: modeName === 'uncertain' ? '审批 approval-invoice-post-004 已批准；请求已发出，响应丢失，结果未知。禁止重复写入。' : '审批 approval-invoice-post-004 已批准；INV-示例-001，state=posted' }] };
    if (modeName === 'uncertain') return prefix.concat([confirm, invoice, { label: '过账前最后已知状态', tools: [
      { id: 'tool-read-known', stage: 2, name: '读取已确认订单', method: 'read_record', elapsed: '274 ms', input: '{model: "sale.order", record_id: 42}', output: 'SO-示例-001，state=sale，invoice_ids=[84]' },
      { id: 'tool-read-known-invoice', stage: 3, name: '读取发票草稿', method: 'read_record', elapsed: '281 ms', input: '{model: "account.move", record_id: 84}', output: 'INV-示例-001，state=draft；这是过账请求之前的观察值。' }
    ] }, post]);
    return prefix.concat([confirm, invoice, post, { label: '独立回读核验', tools: [
      { id: 'tool-read-sale', stage: 4, name: '回读销售单', method: 'read_record', elapsed: '274 ms', input: '{model: "sale.order", record_id: 42}', output: 'SO-示例-001，state=sale，invoice_ids=[84]，quantity=1，amount_total=695.22 USD' },
      { id: 'tool-read-invoice', stage: 4, name: '回读客户发票', method: 'read_record', elapsed: '281 ms', input: '{model: "account.move", record_id: 84}', output: 'INV-示例-001，state=posted，invoice_origin=SO-示例-001，amount_total=695.22 USD；与订单回读结果一致。' }
    ] }]);
  }

  function runFor(modeName) {
    var traces = traceFor(modeName); if (!traces.length) return null;
    return { id: 'run-demo-001', rounds: traces.length, tools: traces.reduce(function (total, round) { return total + round.tools.length; }, 0), started: '2026-09-08 09:30', elapsed: modeName === 'success' ? '00:05:12' : '00:02:18' };
  }

  function actionFor(modeName) {
    var actions = {
      ready: '尚未读取业务资料；点击“开始示例执行”查看预期流程。',
      running: state.approvalGranted ? '订单确认已获准，但尚未执行；后续发票动作仍未开始。' : '正在读取客户与商品约束，尚未产生订单或发票单据。',
      approval: '订单确认即将写入：当前拟将销售单 SO-示例-001 从草稿改为已确认。',
      success: '本轮示例已结束，销售单已确认、发票已过账，并有独立回读证据。',
      failed: '订单确认已拒绝；原销售单草稿保留，没有执行后续开票。',
      uncertain: '发票过账请求已发出，但结果不确定；订单已确认、发票草稿是最后已知事实。'
    };
    return actions[modeName];
  }

  function renderShell() {
    refs.shell.classList.toggle('assistant-hidden', !state.assistantOpen);
    refs.shell.classList.toggle('rail-hidden', state.railCollapsed);
    refs.rail.classList.toggle('collapsed', state.railCollapsed);
    refs.assistant.classList.toggle('collapsed', !state.assistantOpen);
    document.getElementById('assistantToggle').setAttribute('aria-label', state.assistantOpen ? '收起会话辅助面板' : '展开会话辅助面板');
    document.getElementById('assistantToggle').title = state.assistantOpen ? '收起会话辅助面板' : '展开会话辅助面板';
    refs.modeLabel.textContent = state.business === 'sales' ? mode().label : '只读示例';
    refs.title.textContent = state.business === 'sales' ? 'Nimbus Bureau' : '月度费用核对';
    refs.goal.textContent = state.business === 'sales' ? '为客户创建 Open-Plan Noise Barrier Wall 销售单并关联一张客户发票。' : '查看一组脱敏的费用资料，不进行任何写入操作。';
    document.querySelectorAll('[data-state]').forEach(function (button) {
      var selected = state.business === 'sales' && button.dataset.state === state.mode;
      button.setAttribute('aria-selected', String(selected));
    });
    document.querySelectorAll('[data-business]').forEach(function (button) { button.classList.toggle('active', button.dataset.business === state.business); button.setAttribute('aria-selected', String(button.dataset.business === state.business)); });
    document.querySelectorAll('[data-page]').forEach(function (button) { button.classList.toggle('active', button.dataset.page === state.page); });
    document.getElementById('rawInstruction').hidden = document.getElementById('instructionToggle').getAttribute('aria-expanded') !== 'true' || state.business !== 'sales';
    refs.messageList.innerHTML = state.messages.map(function (message) { return '<article class="message ' + (message.kind === 'user' ? 'user' : '') + '"><span class="message-label">' + (message.kind === 'user' ? '本地示例消息' : '业务助手 · 示例') + '</span><p>' + esc(message.text) + '</p><time>' + esc(message.time) + '</time></article>'; }).join('');
    refs.messageList.scrollTop = refs.messageList.scrollHeight;
  }

  function stepState(index) {
    if (state.mode === 'ready') return index === 0 ? 'active' : '';
    if (state.mode === 'running') return state.approvalGranted ? (index < 2 ? 'done' : index === 2 ? 'active' : '') : index === 0 ? 'active' : '';
    if (state.mode === 'approval') return index < 2 ? 'done' : index === 2 ? 'active' : '';
    if (state.mode === 'success') return 'done';
    if (state.mode === 'failed') return index < 2 ? 'done' : index === 2 ? 'active' : '';
    return index < 3 ? 'done' : index === 3 ? 'active' : '';
  }
  function stepToolId(index) {
    if (state.mode === 'success') return ['tool-read-customer', 'tool-create-sale', 'tool-confirm', 'tool-post', 'tool-read-invoice'][index] || null;
    if (state.mode === 'approval') return ['tool-read-customer', 'tool-create-sale', 'tool-validate'][index] || null;
    if (state.mode === 'running') return state.approvalGranted ? ['tool-read-customer', 'tool-create-sale', 'tool-validate'][index] || null : index === 0 ? 'tool-read-customer' : null;
    if (state.mode === 'failed') return ['tool-read-customer', 'tool-create-sale', 'tool-reject'][index] || null;
    if (state.mode === 'uncertain') return ['tool-read-customer', 'tool-create-sale', 'tool-confirm', 'tool-invoice'][index] || null;
    return null;
  }
  function stepDetail(index) {
    var details = [
      ['读取客户与商品约束', '只读示例证据', '客户 Nimbus Bureau、商品 Open-Plan Noise Barrier Wall、30 Days 付款条件；notes 读取结果已脱敏展示。', 'customer=示例客户；product=示例商品；payment_term=30 Days'],
      ['生成销售单草稿', 'sale.order · SO-示例-001', '1 件，list price 695.22 USD。草稿是业务事实的观察结果，尚未代表订单确认。', 'quantity=1 · unit_price=695.22 · commitment=2026-09-30'],
      ['订单确认写入', '需要明确审批', '审批对象是销售单从草稿到已确认这一项动作，审批前不显示已执行。', 'before: draft → after: sale'],
      ['创建并过账客户发票', 'account.move · INV-示例-001', '仅在完整成功示例中展示已过账；待审批只显示后续计划，不把它混入当前批准。', 'invoice_policy=order · payment_term=30 Days'],
      ['独立回读核验', '只读核验回执', '重新读取订单与发票状态，确认对象链接、金额和数量；缺失时显示未提供。', 'sale.order=sale · account.move=posted · link=唯一']
    ][index];
    var toolId = stepToolId(index);
    if (!toolId) return '<div class="detail-head"><div><h3>' + details[0] + '</h3><p>预期步骤 · 尚无证据</p></div></div><div class="evidence-line"><span class="evidence-key">预期</span><span class="evidence-value">该阶段尚未发生，当前不展示执行回执或已完成状态。</span></div><div class="evidence-line"><span class="evidence-key">时间</span><span class="evidence-value">尚未到达</span></div>';
    var receipt = '<button class="inline-link" type="button" data-jump-tool="' + toolId + '">查看具体回执</button>';
    return '<div class="detail-head"><div><h3>' + details[0] + '</h3><p>' + details[1] + '</p></div>' + (index === 2 && state.mode === 'approval' ? '<span class="status-pill amber"><span class="status-icon amber">!</span>待审批</span>' : '') + '</div><div class="evidence-line"><span class="evidence-key">说明</span><span class="evidence-value">' + details[2] + '</span></div><div class="evidence-line"><span class="evidence-key">示例回执</span><span class="evidence-value">' + receipt + '</span></div>' + (index === 2 && state.mode === 'approval' ? approvalContext() : '') + '<div class="evidence-line"><span class="evidence-key">时间</span><span class="evidence-value">2026-09-08 09:' + (30 + index) + '</span></div>';
  }

  function approvalContext() {
    return '<div class="evidence-line"><span class="evidence-key">动作边界</span><span class="evidence-value">本次确认只影响销售单 SO-示例-001。后续开票与过账仍需单独回执，不会因点击同意而直接变成成功。 <button class="inline-link" type="button" data-jump-page="approval">审核订单确认</button></span></div>';
  }

  function renderExecution() {
    if (state.business !== 'sales') return renderReadonly();
    var rows = factsFor(state.mode);
    var table = rows.length ? '<div class="data-table-wrap"><table class="data-table"><thead><tr><th>对象</th><th>示例单据</th><th>状态</th><th>来源</th><th>观测时间</th></tr></thead><tbody>' + rows.map(function (row) { return '<tr><td><strong>' + row.object + '</strong></td><td>' + row.name + '</td><td>' + statusLabel(row.status, row.tone) + '</td><td>' + row.source + '</td><td>' + row.observed + '</td></tr>'; }).join('') + '</tbody></table></div>' : '<div class="notice"><span class="notice-mark">—</span><div><strong>尚未观察到订单或发票</strong><br>当前尚未创建销售单；读取资料不等于已产生单据。</div></div>';
    var reconcileAction = state.mode === 'uncertain' ? '<button class="secondary-button small" id="reconcile" type="button">模拟只读核对</button>' : '';
    var startAction = state.mode === 'ready' ? '<button class="primary-button small" id="startDemo" type="button">开始示例执行</button>' : '';
    return '<div class="section-title"><div><h2>执行台</h2><p>按业务步骤查看对象、动作边界与示例回执。</p></div></div><div class="notice ' + mode().tone + '"><span class="notice-mark">' + mode().icon + '</span><div><strong>' + mode().label + ' · 当前动作</strong><br>' + actionFor(state.mode) + '<br><span class="muted-line">最后示例回执：' + (state.mode === 'ready' ? '未开始' : '2026-09-08 09:' + (state.mode === 'success' ? '36' : state.mode === 'uncertain' ? '35' : state.mode === 'running' && !state.approvalGranted ? '31' : '33')) + '</span><div class="approval-actions">' + startAction + reconcileAction + '</div></div></div><div class="step-layout"><div class="step-list" role="list" aria-label="业务进度">' + stepNames.map(function (name, index) { var status = stepState(index); return '<button type="button" class="step-button ' + status + (state.selectedStep === index ? ' active' : '') + '" data-step="' + index + '" aria-current="' + (state.selectedStep === index ? 'step' : 'false') + '"><span class="step-number">' + (status === 'done' ? '✓' : index + 1) + '</span><span><strong>' + name + '</strong><small>' + stepNotes[index] + '</small></span></button>'; }).join('') + '</div><div class="step-detail">' + stepDetail(state.selectedStep) + '</div></div><div class="facts-block"><h3>已观察业务对象</h3>' + table + '</div><div class="change-summary"><strong>变化摘要</strong><p>' + (state.mode === 'ready' ? '尚无变化。' : state.mode === 'success' ? '销售单从草稿变为已确认，并关联一张已过账客户发票；独立回读记录保持一致。' : state.mode === 'uncertain' ? '订单已确认、发票草稿已创建；只有发票过账的结果尚未确认，禁止重复过账。' : state.mode === 'failed' ? '订单确认被拒绝；原销售单草稿保留，未执行后续开票。' : '已读取或生成部分示例事实，未将后续未完成动作提前标为成功。') + '</p></div>';
  }

  function resourceData() {
    var data = [{ id: 'sop', group: '输入与 SOP', title: '销售与开票 SOP', type: '输入文件', desc: '流程边界与审批规则' }];
    if (state.mode !== 'ready' && !(state.mode === 'running' && !state.approvalGranted)) data.unshift({ id: 'so', group: 'Odoo 单据', title: '销售单 · SO-示例-001', type: 'sale.order', desc: '1 件 · 695.22 USD' });
    if (state.mode === 'success' || state.mode === 'uncertain') data.splice(state.mode === 'success' ? 1 : 1, 0, { id: 'invoice', group: 'Odoo 单据', title: '客户发票 · INV-示例-001', type: 'account.move', desc: state.mode === 'success' ? '已过账 · 695.22 USD' : '草稿 · 过账结果待核对' });
    if (state.mode === 'success') data.push({ id: 'result', group: '生成文件', title: '核验结果 · Markdown', type: '本地示例', desc: '可下载示例结果' });
    return data;
  }
  function iconForResource(id) {
    if (id === 'sop') return '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 4h10v12H5z"/><path d="M8 8h4M8 11h4M8 14h3"/></svg>';
    if (id === 'result') return '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v9M6.5 9.5 10 13l3.5-3.5M5 16h10"/></svg>';
    return '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 3.5h7l3 3v10H5z"/><path d="M12 3.5v3h3"/></svg>';
  }
  function resourceTraceId(id) {
    if (id === 'so') return state.mode === 'approval' ? 'tool-validate' : state.mode === 'failed' ? 'tool-reject' : state.mode === 'success' ? 'tool-confirm' : state.mode === 'uncertain' ? 'tool-read-known' : null;
    if (id === 'invoice') return state.mode === 'success' ? 'tool-post' : state.mode === 'uncertain' ? 'tool-invoice' : null;
    if (id === 'result') return state.mode === 'success' ? 'tool-read-invoice' : null;
    return null;
  }
  function renderDocuments() {
    if (state.business !== 'sales') return renderReadonly();
    var resources = resourceData();
    var groups = ['Odoo 单据', '输入与 SOP', '生成文件'];
    var list = groups.map(function (group) { var members = resources.filter(function (item) { return item.group === group; }); return '<div class="resource-group">' + group + '</div>' + members.map(function (item) { return '<button class="resource-row ' + (state.selectedResource === item.id ? 'active' : '') + '" type="button" data-resource="' + item.id + '"><span class="resource-icon" aria-hidden="true">' + iconForResource(item.id) + '</span><span><strong>' + item.title + '</strong><small>' + item.desc + '</small></span><span class="resource-type">' + item.type + '</span></button>'; }).join(''); }).join('');
    var selected = resources.find(function (item) { return item.id === state.selectedResource; }) || resources[0];
    var preview = selected.id === 'so' ? '<dl class="preview-meta"><dt>业务对象</dt><dd>sale.order</dd><dt>示例状态</dt><dd>' + (state.mode === 'success' || state.mode === 'uncertain' ? '已确认' : state.mode === 'failed' ? '草稿（确认被拒）' : '草稿') + '</dd><dt>客户</dt><dd>Nimbus Bureau</dd><dt>明细</dt><dd>Open-Plan Noise Barrier Wall × 1 · 695.22 USD</dd></dl>' : selected.id === 'invoice' ? '<dl class="preview-meta"><dt>业务对象</dt><dd>account.move</dd><dt>示例状态</dt><dd>' + (state.mode === 'success' ? '已过账' : '草稿（过账结果待核对）') + '</dd><dt>关联销售单</dt><dd>SO-示例-001（示例链接）</dd><dt>付款条件</dt><dd>30 Days</dd></dl>' : selected.id === 'sop' ? '<div class="markdown-preview">销售与开票 SOP\n\n1. 读取客户、商品与付款条件\n2. 生成报价并保留草稿回执\n3. 订单确认需要单独审批\n4. 发票过账与回读分别记录\n\n边界：不做采购、制造、付款或交付。</div>' : '<div class="markdown-preview"># Nimbus Bureau · 核验结果\n\n- 销售单：SO-示例-001 · 已确认\n- 客户发票：INV-示例-001 · 已过账\n- 独立回读：通过\n\n这是可下载的本地 Markdown 示例，不是 Odoo 导出的 PDF。</div>';
    var traceId = resourceTraceId(selected.id);
    var traceAction = traceId ? '<button class="secondary-button small" type="button" data-jump-tool="' + traceId + '">查看具体回执</button>' : '';
    var action = selected.id === 'result' && state.mode === 'success' ? '<a class="primary-button small download-link" id="downloadResult" download="nimbus-bureau-verification.md" href="#">下载 Markdown 示例</a> ' + traceAction : selected.id === 'result' ? '<button class="secondary-button small" type="button" disabled title="成功示例完成后可用">PDF 未生成，Markdown 也未开放</button>' : selected.id === 'sop' ? '<span class="resource-source-note">输入材料，尚无 Odoo 运行回执</span>' : '<button class="secondary-button small" type="button" disabled title="原型不连接 Odoo">打开 Odoo 原始链接（不可用）</button> ' + traceAction;
    return '<div class="section-title"><div><h2>单据与文件</h2><p>资源列表与预览保持业务上下文；原始 Odoo 链接在原型中明确不可用。</p></div><span class="status-pill neutral"><span class="status-icon neutral">□</span>' + resources.length + ' 个示例资源</span></div><div class="resource-layout"><div class="resource-list">' + list + '</div><article class="resource-preview"><div class="preview-head"><div><h3>' + selected.title + '</h3><p>' + selected.desc + '</p></div>' + action + '</div>' + preview + (selected.id === 'result' ? '' : '<div class="disabled-link">这是本地示例预览。点击 Odoo 原始链接不会连接外部系统，也不会发起网络请求。</div>') + '</article></div>';
  }

  function renderApproval() {
    if (state.business !== 'sales') return renderReadonly();
    if (state.mode === 'ready' || (state.mode === 'running' && !state.approvalGranted)) return '<div class="section-title"><div><h2>变更与审批</h2><p>当前没有可审批的写入动作。</p></div>' + pill() + '</div><div class="notice"><span class="notice-mark">—</span><div><strong>尚无审批对象</strong><br>待执行或读取阶段不会预先显示销售单批准记录。</div></div>';
    if (state.mode === 'uncertain') return '<div class="section-title"><div><h2>变更与审批</h2><p>当前需要核对发票过账结果，不能重复提交。</p></div>' + pill() + '</div><div class="approval-layout"><article class="approval-card"><h3>发票过账 · INV-示例-001</h3><p>account.move · 请求结果未知</p><div class="field-diff"><div class="field-diff-row field-diff-head"><span>字段</span><span>最后已知</span><span>待核对</span></div><div class="field-diff-row"><span>发票状态</span><span class="diff-value">草稿</span><span class="diff-value after">过账结果未知</span></div><div class="field-diff-row"><span>关联订单</span><span class="diff-value">SO-示例-001 · sale</span><span class="diff-value after">保持关联</span></div></div><div class="approval-actions"><small>这是只读核对边界，不是新的审批或写入按钮。</small><button class="secondary-button" type="button" id="reconcile">模拟只读核对</button></div></article></div>';
    var completed = state.mode === 'success';
    var rejected = state.mode === 'failed';
    var canApprove = state.mode === 'approval' && !state.actionLocked;
    var currentText = completed ? '已核验' : rejected ? '失败后保留' : state.mode === 'approval' ? '拟执行' : '未进入审批';
    var buttons = state.mode === 'approval' ? '<div class="approval-actions"><small>这是订单确认这一项动作的示例审批，不会自动执行后续开票。</small><button class="primary-button" type="button" id="approveAction" ' + (!canApprove ? 'disabled' : '') + '>同意订单确认</button><button class="danger-button" type="button" id="rejectAction" ' + (!canApprove ? 'disabled' : '') + '>拒绝</button><div class="reject-reason ' + (state.rejectOpen ? 'visible' : '') + '"><label for="rejectInput">拒绝理由（必填）</label><textarea id="rejectInput" rows="2" placeholder="例如：需要先核对付款条件"></textarea><div class="action-error" id="actionError"></div><button class="danger-button" type="button" id="submitReject">确认拒绝</button></div></div>' : '<div class="approval-actions"><small>' + (completed ? '该固定成功快照包含此前已记录的审批与执行回执。' : state.approvalGranted ? '订单确认已批准，尚未执行；后续发票动作仍需独立审批和回执。' : '当前没有可点击的待审批动作。') + '</small></div>';
    return '<div class="section-title"><div><h2>变更与审批</h2><p>把拟执行、已审批、已执行和已核验分开，避免把计划当成事实。</p></div>' + pill() + '</div><div class="approval-layout"><article class="approval-card"><h3>订单确认 · SO-示例-001</h3><p>sale.order · 订单状态变更</p><div class="field-diff"><div class="field-diff-row field-diff-head"><span>字段</span><span>执行前</span><span>拟提交</span></div><div class="field-diff-row"><span>订单状态</span><span class="diff-value">草稿</span><span class="diff-value after">已确认</span></div><div class="field-diff-row"><span>客户</span><span class="diff-value">Nimbus Bureau</span><span class="diff-value after">保持不变</span></div><div class="field-diff-row"><span>总额</span><span class="diff-value">695.22 USD</span><span class="diff-value after">保持不变</span></div></div>' + buttons + '<div class="evidence-line"><span class="evidence-key">来源步骤</span><span class="evidence-value"><button class="inline-link" type="button" data-jump-step="2">审批确认</button> · <button class="inline-link" type="button" data-jump-tool="tool-validate">预检回执</button></span></div></article><aside class="history-card"><h3>动作边界</h3><div class="boundary-list"><div class="boundary ' + (state.mode === 'approval' ? 'current' : '') + '"><span class="boundary-mark">1</span>拟执行</div><div class="boundary ' + (state.mode === 'running' ? 'current' : '') + '"><span class="boundary-mark">2</span>已审批</div><div class="boundary ' + (completed ? 'current' : '') + '"><span class="boundary-mark">3</span>已执行</div><div class="boundary ' + (completed ? 'current' : '') + '"><span class="boundary-mark">4</span>已核验</div></div><div class="history-row approval-history"><strong>' + (state.mode === 'approval' ? '当前等待你的决定' : state.mode === 'failed' ? '订单确认已拒绝' : state.approvalGranted ? '订单确认已批准，尚未执行' : '示例审批记录') + '</strong><span>' + (state.mode === 'approval' ? '批准只推进当前订单确认动作。' : state.mode === 'failed' ? '拒绝理由：' + esc(state.rejectionReason || '未提供') + '；回执：action_confirm · 原销售单草稿保留。' : state.approvalGranted ? '批准回执：approval-confirm-002；后续发票动作仍需独立审批和回执。' : '已批准动作：订单确认 · approval-confirm-002；后续发票动作另有回执。') + '</span></div></aside></div>';
  }

  function usage(value) { return value == null ? '未提供' : esc(value); }
  function renderTrace() {
    if (state.business !== 'sales') return renderReadonly();
    var run = runFor(state.mode);
    if (!run) return '<div class="section-title"><div><h2>运行详情</h2><p>待执行状态尚无运行、轮次或工具回执。</p></div><span class="status-pill neutral"><span class="status-icon neutral">○</span>未开始</span></div><div class="notice"><span class="notice-mark">—</span><div><strong>暂无运行详情</strong><br>切换到执行中或成功，可查看脱敏的示例树与回执。</div></div>';
    var rounds = traceFor(state.mode);
    var tools = rounds.reduce(function (all, round) { return all.concat(round.tools); }, []);
    var current = tools.find(function (tool) { return tool.id === state.selectedTool; }) || tools[0];
    var tree = rounds.map(function (round, index) { return '<div class="trace-round"><div class="tree-round-label">第 ' + (index + 1) + ' 轮 · ' + round.label + '</div>' + round.tools.map(function (tool) { return '<button type="button" class="tree-node ' + (tool.id === current.id ? 'active' : '') + '" data-tool="' + tool.id + '"><span class="tree-line"><span>›</span><strong>' + tool.name + '</strong></span><small>' + tool.method + ' · ' + tool.elapsed + '</small></button>'; }).join('') + '</div>'; }).join('');
    var targetStep = current.stage;
    return '<div class="section-title"><div><h2>运行详情</h2><p>运行 → 轮次 → 工具。只展示脱敏示例输入、输出与回执。</p></div><span class="status-pill ' + mode().tone + '"><span class="status-icon ' + mode().tone + '">' + mode().icon + '</span>' + run.rounds + ' 轮 · ' + run.tools + ' 工具</span></div><div class="trace-layout"><div class="run-tree"><div class="tree-head"><strong>' + run.id + '</strong><span>' + run.started + '</span></div>' + tree + '</div><article class="trace-detail"><h3>' + current.name + '</h3><p>' + current.method + ' · ' + current.elapsed + ' · 示例回执</p><div class="trace-stats"><div class="trace-stat"><span>输入</span><strong>' + usage(null) + '</strong></div><div class="trace-stat"><span>缓存命中</span><strong>' + usage(null) + '</strong></div><div class="trace-stat"><span>输出</span><strong>' + usage(null) + '</strong></div><div class="trace-stat"><span>推理</span><strong>' + usage(null) + '</strong></div></div><div class="trace-label">示例输入（已脱敏）</div><div class="code-block">' + esc(current.input) + '</div><div class="trace-label">示例输出 / 回执</div><div class="code-block">' + esc(current.output) + '</div><div class="evidence-line"><span class="evidence-key">定位</span><span class="evidence-value"><button class="inline-link" type="button" data-jump-step="' + targetStep + '">回到执行步骤</button></span></div></article></div>';
  }

  function renderReadonly() {
    return '<div class="section-title"><div><h2>月度费用核对</h2><p>这是同一会话中的另一个业务标签，用于验证切换时状态不会串页。</p></div><span class="status-pill neutral"><span class="status-icon neutral">□</span>只读业务</span></div><article class="readonly-panel"><h2>只读资料尚未读取</h2><p>本原型不调用 Odoo，也没有为这个业务注入假单据。你可以切回“销售与开票”查看六种固定状态。</p><div class="notice"><span class="notice-mark">i</span><div><strong>写入能力：关闭</strong><br>此业务只保留工作区标签与空态，用来验证多业务切换的上下文边界。</div></div></article>';
  }

  function renderPage() {
    if (state.page === 'documents') refs.content.innerHTML = renderDocuments();
    else if (state.page === 'approval') refs.content.innerHTML = renderApproval();
    else if (state.page === 'trace') refs.content.innerHTML = renderTrace();
    else refs.content.innerHTML = renderExecution();
    refs.content.scrollTop = 0;
    var selectedReceipt = refs.content.querySelector('.run-tree .active');
    if (selectedReceipt) selectedReceipt.scrollIntoView({ block: 'nearest' });
  }
  function render() { renderShell(); renderPage(); }

  function selectState(next) {
    if (!modeMeta[next] || state.business !== 'sales') return;
    state.mode = next; state.approvalGranted = false; state.rejectionReason = ''; state.selectedStep = next === 'approval' ? 2 : next === 'success' ? 4 : next === 'failed' ? 2 : next === 'uncertain' ? 3 : 0; state.selectedTool = 'tool-read-customer'; state.rejectOpen = false; state.actionLocked = false; render();
    showToast('已切换为“' + modeMeta[next].label + '”示例');
  }
  function selectBusiness(next) { if (next !== 'sales' && next !== 'readonly') return; state.business = next; if (next === 'readonly') state.page = 'execution'; render(); }
  function approve() {
    if (state.actionLocked || state.mode !== 'approval') return;
    state.actionLocked = true; render();
    state.mode = 'running'; state.approvalGranted = true; state.selectedStep = 2; state.messages.push({ kind: 'assistant', text: '示例审批已记录：订单确认获准。后续发票仍未执行，页面保持“执行中”以避免把审批当成完成。', time: '09:33' }); state.actionLocked = false; render(); showToast('已记录订单确认审批，后续步骤仍待执行');
  }
  function openReject() { if (state.mode !== 'approval' || state.actionLocked) return; state.rejectOpen = true; render(); var input = document.getElementById('rejectInput'); if (input) input.focus(); }
  function submitReject() {
    if (state.actionLocked || state.mode !== 'approval') return;
    var input = document.getElementById('rejectInput'); var reason = input ? input.value.trim() : '';
    if (!reason) { var error = document.getElementById('actionError'); if (error) error.textContent = '请填写拒绝理由，避免留下无法解释的审批结果。'; return; }
    state.actionLocked = true; state.mode = 'failed'; state.approvalGranted = false; state.rejectionReason = reason; state.rejectOpen = false; state.messages.push({ kind: 'assistant', text: '示例审批已拒绝：' + reason + '。没有执行订单确认，原销售单草稿保留。', time: '09:33' }); state.actionLocked = false; render(); showToast('已记录拒绝结果');
  }
  function reconcile() {
    if (state.actionLocked || state.mode !== 'uncertain') return;
    state.actionLocked = true; state.mode = 'success'; state.selectedTool = 'tool-read-invoice'; state.selectedStep = 4; state.messages.push({ kind: 'assistant', text: '模拟只读核对完成：示例订单与发票状态一致，现在可以查看成功快照。', time: '09:36' }); state.actionLocked = false; render(); showToast('模拟核对完成，已切换为成功示例');
  }
  function startDemo() {
    if (state.mode !== 'ready' || state.actionLocked) return;
    state.actionLocked = true; state.mode = 'running'; state.selectedStep = 0; state.selectedTool = 'tool-read-customer'; state.actionLocked = false; state.messages.push({ kind: 'assistant', text: '已开始固定示例：当前只展示读取约束阶段，不会连接模型或 Odoo。', time: '09:30' }); render(); showToast('已开始示例执行');
  }
  function downloadResult(event) {
    var markdown = '# Nimbus Bureau · 核验结果\n\n- 销售单：SO-示例-001 · 已确认\n- 客户发票：INV-示例-001 · 已过账\n- 独立回读：通过\n\n固定本地交互原型示例，不连接 Odoo。';
    var blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' }); var url = URL.createObjectURL(blob); var link = event.target.closest('#downloadResult'); if (!link) return; link.href = url; window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  document.getElementById('railToggle').addEventListener('click', function () { state.railCollapsed = !state.railCollapsed; renderShell(); });
  document.getElementById('assistantToggle').addEventListener('click', function () { state.assistantOpen = !state.assistantOpen; renderShell(); });
  document.getElementById('assistantClose').addEventListener('click', function () { state.assistantOpen = false; renderShell(); });
  document.getElementById('instructionToggle').addEventListener('click', function () { var button = document.getElementById('instructionToggle'); var open = button.getAttribute('aria-expanded') !== 'true'; button.setAttribute('aria-expanded', String(open)); button.innerHTML = open ? '收起原始指令 <span aria-hidden="true">⌃</span>' : '查看原始指令 <span aria-hidden="true">⌄</span>'; document.getElementById('rawInstruction').hidden = !open; });
  document.getElementById('resetDemo').addEventListener('click', function () { state.mode = 'ready'; state.approvalGranted = false; state.rejectionReason = ''; state.business = 'sales'; state.page = 'execution'; state.selectedStep = 0; state.selectedTool = 'tool-read-customer'; state.selectedResource = 'so'; state.messages = [{ kind: 'assistant', text: '这是一个本地交互原型。切换左侧状态或页面，可查看业务负责人和技术评估所需的不同证据。', time: '09:30' }]; render(); showToast('已重置示例'); });
  document.querySelectorAll('[data-state]').forEach(function (button, index, buttons) {
    button.addEventListener('click', function () { selectState(button.dataset.state); });
    button.addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowRight' && event.key !== 'ArrowUp' && event.key !== 'ArrowLeft') return;
      event.preventDefault();
      var direction = event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1 : -1;
      buttons[(index + direction + buttons.length) % buttons.length].focus();
    });
  });
  document.querySelectorAll('[data-business]').forEach(function (button) { button.addEventListener('click', function () { selectBusiness(button.dataset.business); }); });
  document.querySelectorAll('[data-page]').forEach(function (button) { button.addEventListener('click', function () { state.page = button.dataset.page; render(); }); });
  document.getElementById('composer').addEventListener('submit', function (event) { event.preventDefault(); var input = document.getElementById('messageInput'); var text = input.value.trim(); if (!text) { input.focus(); return; } state.messages.push({ kind: 'user', text: text, time: '09:36' }); input.value = ''; renderShell(); showToast('已追加本地示例消息，不会调用模型'); input.focus(); });
  document.addEventListener('click', function (event) {
    var step = event.target.closest('[data-step]'); if (step) { state.selectedStep = Number(step.dataset.step); renderPage(); return; }
    var jump = event.target.closest('[data-jump-step]'); if (jump) { state.page = 'execution'; state.selectedStep = Number(jump.dataset.jumpStep); render(); return; }
    var jumpTool = event.target.closest('[data-jump-tool]'); if (jumpTool) { state.page = 'trace'; state.selectedTool = jumpTool.dataset.jumpTool; render(); return; }
    var jumpPage = event.target.closest('[data-jump-page]'); if (jumpPage) { state.page = jumpPage.dataset.jumpPage; render(); return; }
    var resource = event.target.closest('[data-resource]'); if (resource) { state.selectedResource = resource.dataset.resource; renderPage(); return; }
    var tool = event.target.closest('[data-tool]'); if (tool) { state.selectedTool = tool.dataset.tool; renderPage(); return; }
    if (event.target.closest('#approveAction')) { approve(); return; }
    if (event.target.closest('#rejectAction')) { openReject(); return; }
    if (event.target.closest('#submitReject')) { submitReject(); return; }
    if (event.target.closest('#reconcile')) { reconcile(); return; }
    if (event.target.closest('#startDemo')) { startDemo(); return; }
    if (event.target.closest('#downloadResult')) { downloadResult(event); }
  });

  render();
}());
