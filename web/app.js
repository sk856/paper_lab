const state = {
  page: 'paper',
  status: null,
  modes: {
    ai: 'ai-light',
    plagiarism: 'repeat-light',
    polish: 'full',
  },
  lastAnalysis: null,
  config: null,
  editingApiId: '',
  paperSections: [],
  selectedPaperSection: '',
  paperEditorSection: '',
  paperSectionContents: {},
  paperSectionContentSources: {},
  paperEditorDirty: false,
  selectedHistoryId: '',
  restoring: false,
};

const STORAGE_KEYS = {
  draft: 'paperlab-web-draft-v3',
  history: 'paperlab-web-history-v2',
};

const FIELD_SELECTORS = [
  '#paperTopic', '#paperSubject', '#paperStyle', '#referenceStyle', '#sectionTitle', '#wordCount', '#paperOutline', '#paperContext', '#paperResult',
  '#aiInput', '#aiOutput', '#aiReview', '#aiDiff',
  '#plagiarismInput', '#plagiarismOutput', '#plagiarismSource', '#plagiarismReview', '#plagiarismDiff',
  '#polishTaskType', '#polishExecutionMode', '#polishTopic', '#polishNotes', '#polishInput', '#polishOutput', '#polishReview',
  '#correctionCitationStyle', '#correctionInput', '#correctionOutput', '#correctionReport',
];

const PAGE_LABELS = {
  paper: '论文写作',
  ai: '降 AI 检测',
  plagiarism: '降查重率',
  polish: '学术润色',
  correction: '智能纠错',
  config: '配置管理',
  history: '历史记录',
};

const PAPER_ACTION_LABELS = {
  outline: '生成大纲',
  section: '撰写当前章节',
  'batch-write': '撰写全部章节',
  abstract: '生成摘要',
  references: '整理参考文献',
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function textValue(selector) {
  const node = $(selector);
  return node ? node.value.trim() : '';
}

function setText(selector, value) {
  const node = $(selector);
  if (node) node.value = value || '';
}

function readStoredJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (error) {
    return fallback;
  }
}

function writeStoredJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn('保存本地数据失败', error);
  }
}

function captureFields() {
  const fields = {};
  FIELD_SELECTORS.forEach((selector) => {
    const node = $(selector);
    if (node) fields[selector] = 'value' in node ? node.value : (node.textContent || '');
  });
  return fields;
}

function applyFields(fields = {}) {
  state.restoring = true;
  Object.entries(fields).forEach(([selector, value]) => {
    const node = $(selector);
    if (!node) return;
    if ('value' in node) node.value = value || '';
    else node.textContent = value || '';
  });
  refreshPaperSections(false);
  state.restoring = false;
}

function saveDraft() {
  if (state.restoring) return;
  writeStoredJson(STORAGE_KEYS.draft, {
    page: state.page,
    modes: state.modes,
    selectedPaperSection: state.selectedPaperSection,
    paperEditorSection: state.paperEditorSection,
    paperSectionContents: state.paperSectionContents,
    paperSectionContentSources: state.paperSectionContentSources,
    fields: captureFields(),
    savedAt: new Date().toISOString(),
  });
}

function restoreDraft() {
  const draft = readStoredJson(STORAGE_KEYS.draft, null);
  if (!draft || !draft.fields) return;
  state.modes = { ...state.modes, ...(draft.modes || {}) };
  state.selectedPaperSection = draft.selectedPaperSection || '';
  state.paperEditorSection = draft.paperEditorSection || state.selectedPaperSection || '';
  state.paperSectionContents = draft.paperSectionContents || {};
  state.paperSectionContentSources = draft.paperSectionContentSources || {};
  applyFields(draft.fields);
  syncModeSelections();
}

function getHistoryRecords() {
  const records = readStoredJson(STORAGE_KEYS.history, []);
  return Array.isArray(records) ? records : [];
}

function setHistoryRecords(records) {
  writeStoredJson(STORAGE_KEYS.history, records.slice(0, 80));
}

function previewText(value, limit = 140) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function createHistoryRecord(page, operation, data = {}) {
  const fields = captureFields();
  const topic = textValue('#paperTopic') || textValue('#polishTopic') || textValue('#sectionTitle');
  const output = data.output || fields[data.outputSelector] || '';
  const input = data.input || fields[data.inputSelector] || '';
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    page,
    module: PAGE_LABELS[page] || page,
    operation,
    title: topic || data.title || previewText(input || output, 42) || operation,
    time: new Date().toISOString(),
    input,
    output,
    fields,
    modes: { ...state.modes },
    selectedPaperSection: state.selectedPaperSection,
    paperEditorSection: state.paperEditorSection,
    paperSectionContents: { ...state.paperSectionContents },
    paperSectionContentSources: { ...state.paperSectionContentSources },
    analysis: data.analysis || state.lastAnalysis || null,
  };
}

function addHistoryRecord(page, operation, data = {}) {
  const record = createHistoryRecord(page, operation, data);
  setHistoryRecords([record, ...getHistoryRecords()]);
  state.selectedHistoryId = record.id;
  renderHistory();
  return record;
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false });
  } catch (error) {
    return iso || '';
  }
}

function setState(scope, text, kind = '') {
  const node = $(`[data-state-for="${scope}"]`);
  if (!node) return;
  node.textContent = text;
  node.dataset.kind = kind;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!payload.ok) throw new Error(payload.error || '请求失败');
  return payload.data;
}

function setPage(page) {
  const nextPage = page || 'paper';
  state.page = nextPage;
  $$('.page-view').forEach((view) => view.classList.toggle('active', view.dataset.pageView === nextPage));
  $$('.feature-item').forEach((item) => item.classList.toggle('active', item.dataset.page === nextPage));
  if (location.hash !== `#/${nextPage}`) history.replaceState(null, '', `#/${nextPage}`);
  saveDraft();
  if (nextPage === 'history') renderHistory();
}

function routeFromHash() {
  const page = (location.hash || '#/paper').replace(/^#\/?/, '') || 'paper';
  setPage($(`[data-page-view="${page}"]`) ? page : 'paper');
}

function renderProviders(status) {
  const list = $('#providerList');
  if (!list) return;
  if (!status || !status.providers || !status.providers.length) {
    list.textContent = '尚未读取到已保存接口。请先在桌面端完成 API 配置。';
    return;
  }
  list.innerHTML = status.providers.map((provider) => `
    <article class="provider-card">
      <div>
        <strong>${escapeHtml(provider.name || provider.id)}</strong>
        <p>${escapeHtml(provider.model || provider.modelDisplayName || '未填写模型')}${provider.configured ? '' : ' / 未填写密钥'}</p>
      </div>
      <div class="provider-actions">
        <span class="state-pill">${provider.active ? '当前启用' : '已保存'}</span>
        <button class="secondary-button compact" data-edit-api="${escapeHtml(provider.id)}" type="button">编辑</button>
        <button class="secondary-button compact" data-activate-api="${escapeHtml(provider.id)}" type="button">启用</button>
      </div>
    </article>
  `).join('');
  bindProviderButtons();
}

function bindProviderButtons() {
  $$('[data-edit-api]').forEach((button) => {
    button.addEventListener('click', () => {
      const record = (state.config?.providers || []).find((item) => item.id === button.dataset.editApi);
      if (record) fillConfigForm(record);
    });
  });
  $$('[data-activate-api]').forEach((button) => {
    button.addEventListener('click', async () => {
      setState('config', '正在切换...', 'running');
      try {
        const config = await requestJson('/api/config/activate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: button.dataset.activateApi }),
        });
        renderConfig(config);
        await loadStatus();
        setState('config', '已启用', 'done');
      } catch (error) {
        setState('config', '启用失败', 'error');
        $('#configMessage').textContent = error.message;
      }
    });
  });
}

function configFormPayload() {
  return {
    id: state.editingApiId,
    providerType: $('#configProviderType').value,
    name: textValue('#configName'),
    baseUrl: textValue('#configBaseUrl'),
    key: textValue('#configKey'),
    model: textValue('#configModel'),
    modelDisplayName: textValue('#configModelDisplayName'),
    timeout: textValue('#configTimeout'),
    activate: true,
  };
}

function fillConfigForm(record = null) {
  state.editingApiId = record ? record.id : '';
  $('#configProviderType').value = record ? record.providerType : ($('#configProviderType').value || 'openai');
  $('#configName').value = record ? record.name : '';
  $('#configBaseUrl').value = record ? record.baseUrl : '';
  $('#configKey').value = '';
  $('#configModel').value = record ? record.model : '';
  $('#configModelDisplayName').value = record ? record.modelDisplayName : '';
  $('#configTimeout').value = record ? (record.timeout || '') : '';
  $('#configMessage').textContent = record
    ? `正在编辑：${record.name}。API Key 留空表示不修改原密钥。`
    : '正在新建接口。保存后会自动设为当前启用。';
}

function applyPresetToForm() {
  const preset = (state.config?.presets || []).find((item) => item.id === $('#configProviderType').value);
  if (!preset) return;
  const defaults = preset.defaults || {};
  if (!textValue('#configName')) $('#configName').value = preset.label || '';
  if (!textValue('#configBaseUrl')) $('#configBaseUrl').value = defaults.base_url || '';
  if (!textValue('#configModel')) $('#configModel').value = defaults.model || '';
  renderModelOptions(preset.staticModels || []);
}

function renderModelOptions(models) {
  const list = $('#configModelList');
  if (!list) return;
  list.innerHTML = (models || []).map((model) => `<option value="${escapeHtml(model)}"></option>`).join('');
}

function renderConfig(config) {
  state.config = config;
  const presetSelect = $('#configProviderType');
  if (presetSelect && !presetSelect.options.length) {
    presetSelect.innerHTML = (config.presets || []).map((preset) => (
      `<option value="${escapeHtml(preset.id)}">${escapeHtml(preset.label)}</option>`
    )).join('');
  }
  renderProviders({ providers: config.providers || [] });
  const active = (config.providers || []).find((item) => item.active) || (config.providers || [])[0];
  if (!state.editingApiId && active) fillConfigForm(active);
}

async function loadConfig() {
  const config = await requestJson('/api/config');
  renderConfig(config);
  return config;
}

async function loadStatus() {
  const status = await requestJson('/api/status');
  state.status = status;
  const label = status.configured
    ? `当前模型：${status.activeName || status.activeApi} ${status.activeModel ? ' / ' + status.activeModel : ''}`
    : '尚未检测到可用模型配置；本地分析可用，AI 改写需要先在桌面端配置模型。';
  if ($('#modelStatus')) $('#modelStatus').textContent = label;
  renderProviders(status);
  if (state.config) {
    await loadConfig();
  }
}

function renderAnalysisSummary(analysis) {
  if (!analysis) return '暂无分析数据。';
  const lines = [];
  if (analysis.ai) {
    lines.push(`AI 风险：${analysis.ai.risk_level || '-'} / 特征分 ${analysis.ai.score ?? '-'}`);
    if ((analysis.ai.features || []).length) lines.push(`AI 痕迹：${analysis.ai.features.slice(0, 4).join('；')}`);
  }
  if (analysis.repeat) {
    lines.push(`重复风险：${analysis.repeat.risk_level || '-'} / 模拟重复率 ${analysis.repeat.simulated_rate ?? '-'}%`);
    lines.push(`重复短语：${(analysis.repeat.repeated_phrases || []).length} 项`);
  }
  if (analysis.citation) {
    const issues = analysis.citation.issues || [];
    lines.push(`引用检查：${issues.length ? `${issues.length} 项问题` : '通过'}`);
    if (issues.length) lines.push(`首项问题：${issues[0]}`);
  }
  return lines.join('\n') || '暂无分析数据。';
}

function diffText(before, after) {
  const oldText = before || '';
  const newText = after || '';
  if (!oldText || !newText) return '请先准备原文与处理结果。';
  if (oldText === newText) return '原文与结果暂未检测到差异。';
  return [
    `原文字数：${oldText.length}`,
    `结果字数：${newText.length}`,
    `字数变化：${newText.length - oldText.length}`,
    '',
    '原文片段：',
    oldText.slice(0, 420),
    '',
    '结果片段：',
    newText.slice(0, 420),
  ].join('\n');
}

function selectedAction(group) {
  return state.modes[group];
}

function syncModeSelections() {
  $$('[data-mode-group]').forEach((button) => {
    const group = button.dataset.modeGroup;
    const value = group === 'polish' ? button.dataset.polishMode : button.dataset.action;
    button.classList.toggle('selected', value === state.modes[group]);
  });
}

function bindModeCards() {
  $$('[data-mode-group]').forEach((button) => {
    button.addEventListener('click', () => {
      const group = button.dataset.modeGroup;
      if (group === 'polish') {
        state.modes.polish = button.dataset.polishMode || 'full';
      } else {
        state.modes[group] = button.dataset.action;
      }
      $$(`[data-mode-group="${group}"]`).forEach((item) => item.classList.remove('selected'));
      button.classList.add('selected');
      saveDraft();
    });
  });
}

function normalizeOutlineTitle(line) {
  return String(line || '')
    .replace(/^\s*[-*•]\s+/, '')
    .replace(/^\s{0,8}(#{1,6}|\d+(?:\.\d+)*[、.．]?|[一二三四五六七八九十]+[、.．]?|第[一二三四五六七八九十百千万\d]+[章节篇部分]?|（[一二三四五六七八九十百千万]+）|\(\d+\))[\s-]*/u, '')
    .replace(/[：:]\s*$/, '')
    .trim();
}

function stripOutlineEmphasis(text) {
  let normalized = String(text || '').trim();
  const markers = ['***', '___', '**', '__', '*', '_'];
  while (normalized) {
    const marker = markers.find((item) => normalized.startsWith(item) && normalized.endsWith(item) && normalized.length > item.length * 2);
    if (!marker) return normalized;
    const inner = normalized.slice(marker.length, -marker.length).trim();
    if (!inner) return normalized;
    normalized = inner;
  }
  return normalized;
}

function paperPlainTitle(title) {
  return normalizeOutlineTitle(title).replace(/\s+/g, ' ').trim().toLowerCase();
}

function paperSpecialKind(title) {
  const plain = paperPlainTitle(title);
  if (['摘要', '中文摘要', '摘要与关键词'].includes(plain)) return 'cn_abstract';
  if (['关键词', '关键字', '中文关键词', '中文关键字'].includes(plain)) return 'cn_keywords';
  if (['abstract', '英文摘要', 'abstract and keywords'].includes(plain)) return 'en_abstract';
  if (['keywords', '英文关键词', '英文关键字'].includes(plain)) return 'en_keywords';
  if (['引言', '绪论'].includes(plain)) return 'intro';
  if (['参考文献', 'references', 'bibliography'].includes(plain)) return 'reference';
  if (['附录', 'appendix'].includes(plain)) return 'appendix';
  return '';
}

function canonicalPaperTitle(kind) {
  return {
    cn_abstract: '中文摘要',
    en_abstract: '英文摘要',
    intro: '绪论',
    reference: '参考文献',
  }[kind] || '';
}

function analyzeOutlineHeading(line) {
  const raw = String(line || '').trim();
  let text = stripOutlineEmphasis(raw);
  const bullet = text.match(/^\s*[-*•]\s+(.+)$/);
  if (bullet) text = stripOutlineEmphasis(bullet[1].trim());
  if (!text || text.length > 160) return null;
  const markdown = text.match(/^(#{1,6})\s+(.+)$/);
  if (markdown) return { raw, title: normalizeOutlineTitle(markdown[2]), level: Math.min(markdown[1].length, 3) };
  const chapter = text.match(/^(第[一二三四五六七八九十百千万\d]+(章|节|部分|篇))\s*[:：]?\s*(.+)$/u);
  if (chapter) return { raw, title: normalizeOutlineTitle(`${chapter[1]} ${chapter[3]}`), level: chapter[2] === '节' ? 2 : 1 };
  const decimal = text.match(/^((?:\d+\.)+\d+)\s*[:：]?\s*(.+)$/);
  if (decimal) {
    const level = Math.min(decimal[1].split('.').filter(Boolean).length, 3);
    return { raw, title: normalizeOutlineTitle(`${decimal[1]} ${decimal[2]}`), level: Math.max(1, level) };
  }
  const singleNumber = text.match(/^(\d+)(?:([、．.])\s*|\s+)(.+)$/);
  if (singleNumber) return { raw, title: normalizeOutlineTitle(`${singleNumber[1]} ${singleNumber[3]}`), level: 1 };
  const chineseEnum = text.match(/^([一二三四五六七八九十百千万]+[、．.])\s*(.+)$/u);
  if (chineseEnum) return { raw, title: normalizeOutlineTitle(`${chineseEnum[1]} ${chineseEnum[2]}`), level: 1 };
  const chineseParen = text.match(/^(（[一二三四五六七八九十百千万]+）)\s*(.+)$/u);
  if (chineseParen) return { raw, title: normalizeOutlineTitle(chineseParen[2]), level: 2 };
  const arabicParen = text.match(/^(\(\d+\)|（\d+）)\s*(.+)$/);
  if (arabicParen) return { raw, title: normalizeOutlineTitle(arabicParen[2]), level: 3 };
  const specialKind = paperSpecialKind(text);
  if (specialKind) return { raw, title: canonicalPaperTitle(specialKind) || normalizeOutlineTitle(text), level: specialKind.includes('keywords') ? 2 : 1 };
  return null;
}

function buildPaperOutlineStructure(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  const headings = [];
  const stack = [];
  lines.forEach((line, index) => {
    const parsed = analyzeOutlineHeading(line);
    if (!parsed || !parsed.title) return;
    while (stack.length && stack[stack.length - 1].level >= parsed.level) stack.pop();
    const parent = stack.length ? stack[stack.length - 1].title : '';
    const heading = { ...parsed, index, parent };
    headings.push(heading);
    stack.push(heading);
  });
  const sections = {};
  const order = [];
  const levels = {};
  const parents = {};
  const raws = {};
  headings.forEach((heading, index) => {
    const nextStart = index + 1 < headings.length ? headings[index + 1].index : lines.length;
    const body = lines.slice(heading.index + 1, nextStart)
      .filter((line) => !analyzeOutlineHeading(line))
      .join('\n')
      .trim();
    if (!order.includes(heading.title)) {
      order.push(heading.title);
      sections[heading.title] = body;
      levels[heading.title] = heading.level;
      parents[heading.title] = heading.parent;
      raws[heading.title] = heading.raw;
    }
  });
  return { sections, order, levels, parents, raws };
}

function renderPaperSections() {
  const list = $('#paperSectionList');
  if (!list) return;
  const sections = state.paperSections;
  if (!sections.length) {
    list.textContent = '暂未解析到章节。建议大纲使用“第一章 绪论”或“1.1 研究背景”这样的标题格式。';
    return;
  }
  list.innerHTML = sections.map((section, index) => `
    <button class="outline-section-item ${section.title === state.selectedPaperSection ? 'selected' : ''}" data-section-index="${index}" type="button">
      <strong>${escapeHtml(section.title)}</strong>
      <span>${escapeHtml(section.raw || section.title)}</span>
    </button>
  `).join('');
  $$('.outline-section-item').forEach((button) => {
    button.addEventListener('click', () => {
      const section = state.paperSections[Number(button.dataset.sectionIndex)];
      if (!section) return;
      selectPaperSection(section.title);
    });
  });
}

function getCurrentPaperContent() {
  return textValue('#paperContext');
}

function storeCurrentPaperEditor(options = {}) {
  const title = state.paperEditorSection || textValue('#sectionTitle');
  if (!title || !state.paperSectionContents || !(title in state.paperSectionContents)) return;
  const content = getCurrentPaperContent();
  if (options.skipEmptyOverwrite && !content && state.paperSectionContents[title]) return;
  state.paperSectionContents[title] = content;
  if (state.restoring) return;
  if (options.markUser !== false) state.paperSectionContentSources[title] = 'user';
  state.paperEditorDirty = false;
}

function loadPaperSection(title) {
  const content = state.paperSectionContents[title] || '';
  state.paperEditorSection = title;
  state.selectedPaperSection = title;
  setText('#sectionTitle', title);
  setText('#paperContext', content);
  setText('#paperResult', content);
  state.paperEditorDirty = false;
}

function selectPaperSection(title, options = {}) {
  if (!title) return;
  if (options.storeCurrent !== false) storeCurrentPaperEditor();
  loadPaperSection(title);
  renderPaperSections();
  saveDraft();
}

function findPaperSectionByKind(kind) {
  return (state.paperSections || []).find((section) => paperSpecialKind(section.title) === kind)?.title || '';
}

function ensurePaperSection(title, kind = '') {
  const rawTitle = String(title || '').trim();
  const parsedTitle = rawTitle ? analyzeOutlineHeading(rawTitle)?.title : '';
  const normalizedTitle = parsedTitle || rawTitle || canonicalPaperTitle(kind);
  if (!normalizedTitle) return '';
  const resolvedKind = kind || paperSpecialKind(normalizedTitle);
  const existingByKind = resolvedKind ? findPaperSectionByKind(resolvedKind) : '';
  const target = existingByKind || normalizedTitle;
  if (!(target in state.paperSectionContents)) state.paperSectionContents[target] = '';
  if (!(target in state.paperSectionContentSources)) state.paperSectionContentSources[target] = state.paperSectionContents[target] ? 'user' : 'outline';
  if (!state.paperSections.some((section) => section.title === target)) {
    const referenceIndex = state.paperSections.findIndex((section) => paperSpecialKind(section.title) === 'reference');
    const item = { title: target, raw: target, level: 1, parent: '' };
    if (resolvedKind && resolvedKind !== 'reference' && referenceIndex >= 0) state.paperSections.splice(referenceIndex, 0, item);
    else state.paperSections.push(item);
  }
  return target;
}

function formatPaperOutlineHeading(section) {
  const raw = String(section?.raw || '').trim();
  const parsedRaw = raw ? analyzeOutlineHeading(raw) : null;
  if (parsedRaw && parsedRaw.title === section?.title) return raw;
  const level = Math.min(Math.max(Number(section?.level || 1), 1), 3);
  const title = String(section?.title || section?.raw || '').trim();
  return title ? `${'#'.repeat(level)} ${title}` : '';
}

function syncPaperOutlineFromSections() {
  const text = state.paperSections.map(formatPaperOutlineHeading).filter(Boolean).join('\n').trim();
  if (text) setText('#paperOutline', text);
}

function historyHasPaperOutput(operation, content) {
  const body = String(content || '').trim();
  if (!body) return false;
  return getHistoryRecords().some((record) => (
    record.page === 'paper' &&
    record.operation === operation &&
    String(record.output || '').trim() === body
  ));
}

function inferPaperContentSource(title, content, source = '') {
  if (source) return source;
  const kind = paperSpecialKind(title);
  if (kind === 'cn_abstract' && historyHasPaperOutput('生成摘要', content)) return 'user';
  if (kind === 'reference' && historyHasPaperOutput('整理参考文献', content)) return 'user';
  return content ? 'user' : 'outline';
}

function looksLikeOutlineOnlySectionContent(title, content, source = '') {
  if (source === 'user') return false;
  const kind = paperSpecialKind(title);
  if (!['cn_abstract', 'cn_keywords', 'en_abstract', 'en_keywords', 'reference'].includes(kind)) return false;
  const text = String(content || '').trim();
  if (!text) return false;
  if (analyzeOutlineHeading(text.split(/\r?\n/)[0])) return true;
  if (/^(摘要|abstract)[:：\s]/i.test(text) || /(?:^|\n)\s*(关键词|关键字|keywords)\s*[:：]/i.test(text)) return false;
  if (/[。！？.!?]/.test(text)) return false;
  const outlineHints = [
    '研究背景', '研究目的', '研究意义', '研究方法', '研究内容',
    '包括', '概述', '说明', '列出', '梳理',
    'research background', 'research purpose', 'research objective', 'research method',
    'research content', 'include', 'overview', 'outline',
  ];
  const lowered = text.toLowerCase();
  const hintCount = outlineHints.filter((hint) => lowered.includes(hint)).length;
  const hasSentencePunctuation = /[。！？.!?]/.test(text);
  const looksLikeList = /[、；;]|\s*,\s*/.test(text) || text.split(/\r?\n/).filter(Boolean).length > 1;
  if (['cn_abstract', 'en_abstract'].includes(kind) && text.length <= 220 && hintCount >= 2 && !hasSentencePunctuation && looksLikeList) {
    return true;
  }
  return false;
}

function refreshPaperSections(selectFirst = false, options = {}) {
  const preserveExisting = options.preserveExisting !== false;
  if (preserveExisting) {
    storeCurrentPaperEditor({ skipEmptyOverwrite: true, markUser: state.paperEditorDirty });
  }
  const previousContents = preserveExisting ? { ...(state.paperSectionContents || {}) } : {};
  const previousSources = preserveExisting ? { ...(state.paperSectionContentSources || {}) } : {};
  const previousEditor = preserveExisting ? state.paperEditorSection : '';
  const structure = buildPaperOutlineStructure(textValue('#paperOutline'));
  state.paperSections = structure.order.map((title) => ({
    title,
    raw: structure.raws?.[title] || title,
    level: structure.levels[title] || 1,
    parent: structure.parents[title] || '',
  }));
  state.paperSectionContents = {};
  state.paperSectionContentSources = {};
  structure.order.forEach((title) => {
    const kind = paperSpecialKind(title);
    const parsedBody = structure.sections[title] ?? '';
    const previousBody = previousContents[title] || '';
    const previousSource = previousSources[title] || (
      looksLikeOutlineOnlySectionContent(title, previousBody, '') ? 'outline' : inferPaperContentSource(title, previousBody, '')
    );
    const keepPrevious = previousBody && previousSource === 'user' && !looksLikeOutlineOnlySectionContent(title, previousBody, previousSource);
    state.paperSectionContents[title] = keepPrevious ? previousBody : parsedBody;
    state.paperSectionContentSources[title] = keepPrevious ? 'user' : (parsedBody ? 'outline' : 'outline');
  });
  const nextTitle = selectFirst
    ? state.paperSections[0]?.title
    : (previousEditor && state.paperSectionContents[previousEditor] !== undefined ? previousEditor : textValue('#sectionTitle'));
  if (nextTitle && state.paperSectionContents[nextTitle] !== undefined) {
    loadPaperSection(nextTitle);
  } else if (!state.paperSections.length) {
    state.paperEditorSection = '';
    state.selectedPaperSection = '';
    setText('#sectionTitle', '');
    setText('#paperContext', '');
    setText('#paperResult', '');
  }
  renderPaperSections();
}

function paperTargetSectionForAction(action) {
  if (action === 'abstract') return ensurePaperSection('中文摘要', 'cn_abstract');
  if (action === 'references') return ensurePaperSection('参考文献', 'reference');
  return ensurePaperSection(textValue('#sectionTitle') || state.selectedPaperSection || state.paperEditorSection);
}

function paperTitleMatchKey(title) {
  return normalizeOutlineTitle(stripOutlineEmphasis(title)).replace(/\s+/g, '').trim().toLowerCase();
}

function paperSectionTitleKeys(title) {
  const keys = new Set();
  const targetKey = paperTitleMatchKey(title);
  [title, normalizeOutlineTitle(title)].forEach((item) => {
    const key = paperTitleMatchKey(item);
    if (key) keys.add(key);
  });
  (state.paperSections || []).forEach((section) => {
    if (paperTitleMatchKey(section.title) !== targetKey) return;
    [section.title, section.raw, normalizeOutlineTitle(section.raw)].forEach((item) => {
      const key = paperTitleMatchKey(item);
      if (key) keys.add(key);
    });
  });
  return keys;
}

function isGeneratedHeadingForSection(line, title) {
  const text = stripOutlineEmphasis(line);
  if (!text || text.length > 160) return false;
  const titleKeys = paperSectionTitleKeys(title);
  if (!titleKeys.size) return false;
  const parsed = analyzeOutlineHeading(text);
  const candidates = parsed
    ? [parsed.title, parsed.raw, normalizeOutlineTitle(parsed.raw)]
    : [text, normalizeOutlineTitle(text)];
  return candidates.some((candidate) => titleKeys.has(paperTitleMatchKey(candidate)));
}

function normalizeGeneratedSectionBody(title, content) {
  const normalized = String(content || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = normalized.split('\n');
  let start = 0;
  while (start < lines.length && !lines[start].trim()) start += 1;
  if (start < lines.length && isGeneratedHeadingForSection(lines[start], title)) {
    start += 1;
    while (start < lines.length && !lines[start].trim()) start += 1;
    return lines.slice(start).join('\n').replace(/\n+$/g, '');
  }
  return normalized.replace(/^\n+|\n+$/g, '');
}

function writePaperContentToSection(title, content, options = {}) {
  const target = ensurePaperSection(title, paperSpecialKind(title));
  if (!target) return;
  const normalizedContent = normalizeGeneratedSectionBody(target, content);
  state.paperSectionContents[target] = normalizedContent;
  state.paperSectionContentSources[target] = 'user';
  if (options.loadEditor === false) {
    if (state.paperEditorSection === target) {
      setText('#paperContext', normalizedContent);
      setText('#paperResult', normalizedContent);
      state.paperEditorDirty = false;
    }
  } else {
    loadPaperSection(target);
  }
  renderPaperSections();
  return target;
}

function parseGeneratedAbstractBlock(text, language = 'cn') {
  const normalized = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  if (!normalized) return { abstractText: '', keywordText: '' };
  const keywordPattern = language === 'en'
    ? /(?:^|\n)\s*(?:#{1,6}\s*)?(?:(?:\[\s*keywords\s*\]\s*(?:[:：]|\s)?)|(?:(?:keywords|英文关键词|英文关键字)\s*(?:[:：]|\s)))\s*(.+)$/i
    : /(?:^|\n)\s*(?:#{1,6}\s*)?(?:(?:【\s*(?:关键词|关键字|中文关键词|中文关键字)\s*】\s*(?:[:：]|\s)?)|(?:(?:关键词|关键字|中文关键词|中文关键字)\s*(?:[:：]|\s)))\s*(.+)$/i;
  const keywordMatch = normalized.match(keywordPattern);
  let abstractPart = normalized;
  let keywordText = '';
  if (keywordMatch) {
    abstractPart = normalized.slice(0, keywordMatch.index).trim();
    keywordText = String(keywordMatch[1] || '').replace(/\s+/g, ' ').replace(/[；;,.，。]+$/g, '').trim();
  }
  const abstractPattern = language === 'en'
    ? /^\s*(?:#{1,6}\s*)?(?:(?:\[\s*abstract\s*\]\s*(?:[:：]|\s)?)|(?:(?:abstract|英文摘要)\s*(?:[:：]|\s)))\s*/i
    : /^\s*(?:#{1,6}\s*)?(?:(?:【\s*(?:摘要|中文摘要)\s*】\s*(?:[:：]|\s)?)|(?:(?:摘要|中文摘要)\s*(?:[:：]|\s)))\s*/i;
  const abstractText = abstractPart.replace(abstractPattern, '').trim();
  return { abstractText, keywordText };
}

function splitGeneratedAbstractSections(content) {
  const normalized = String(content || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  const fallback = { cn: normalized, en: '', hasMarkers: false };
  if (!normalized) return fallback;
  const markers = [];
  const markerPattern = /(?:^|\n)\s*(?:#{1,6}\s*)?(?:【?\s*(中文摘要|摘要|英文摘要)\s*】?|\[?\s*(abstract)\s*\]?)(?:\s*[:：])?/gi;
  let match;
  while ((match = markerPattern.exec(normalized)) !== null) {
    const label = String(match[1] || match[2] || '').toLowerCase();
    const kind = label === '英文摘要' || label === 'abstract' ? 'en' : 'cn';
    markers.push({ kind, index: match.index, bodyStart: markerPattern.lastIndex });
  }
  if (!markers.length) return fallback;
  const sections = { cn: '', en: '' };
  markers.forEach((marker, index) => {
    const end = index + 1 < markers.length ? markers[index + 1].index : normalized.length;
    const body = normalized.slice(marker.bodyStart, end).trim();
    if (body) sections[marker.kind] = body;
  });
  if (!sections.cn && !sections.en) return fallback;
  return { ...sections, hasMarkers: true };
}

function formatGeneratedAbstract(text, language = 'cn') {
  const { abstractText, keywordText } = parseGeneratedAbstractBlock(text, language);
  const parts = [];
  if (abstractText) parts.push(abstractText);
  if (keywordText) parts.push(`${language === 'en' ? 'Keywords' : '关键词'}：${keywordText}`);
  return parts.join('\n\n').trim() || String(text || '').trim();
}

function writeAbstractResultToSections(content) {
  const result = String(content || '').trim();
  const sections = splitGeneratedAbstractSections(result);
  const cnTitle = ensurePaperSection('中文摘要', 'cn_abstract');
  const cnSource = sections.cn || (sections.hasMarkers ? '' : result);
  const cnContent = formatGeneratedAbstract(cnSource, 'cn');
  if (cnTitle && cnContent) {
    state.paperSectionContents[cnTitle] = cnContent;
    state.paperSectionContentSources[cnTitle] = 'user';
  }
  const enTitle = findPaperSectionByKind('en_abstract') || ensurePaperSection('英文摘要', 'en_abstract');
  if (enTitle && paperSpecialKind(enTitle) === 'en_abstract') {
    const enContent = sections.en ? formatGeneratedAbstract(sections.en, 'en') : '';
    if (enContent) {
      state.paperSectionContents[enTitle] = enContent;
      state.paperSectionContentSources[enTitle] = 'user';
    } else if (!state.paperSectionContents[enTitle]) {
      state.paperSectionContentSources[enTitle] = state.paperSectionContentSources[enTitle] || 'outline';
    }
  }
  if (cnTitle) loadPaperSection(cnTitle);
  renderPaperSections();
}

function collectPaperTextForAbstract() {
  storeCurrentPaperEditor();
  const parts = [];
  const topic = textValue('#paperTopic');
  const outline = textValue('#paperOutline');
  if (topic) parts.push(`# ${topic}`);
  state.paperSections.forEach((section) => {
    const kind = paperSpecialKind(section.title);
    if (['cn_abstract', 'cn_keywords', 'en_abstract', 'en_keywords', 'reference'].includes(kind)) return;
    const body = String(state.paperSectionContents[section.title] || '').trim();
    if (body) parts.push(`${section.title}\n${body}`);
  });
  if (!parts.length && getCurrentPaperContent()) {
    parts.push(`${state.paperEditorSection || textValue('#sectionTitle') || '正文'}\n${getCurrentPaperContent()}`);
  }
  if (outline && !parts.some((part) => part.includes('\n'))) {
    parts.push(`论文大纲\n${outline}`);
  }
  return parts.join('\n\n').trim();
}

function collectBatchWriteTargets(emptyOnly = false) {
  storeCurrentPaperEditor();
  const targets = [];

  state.paperSections.forEach((section) => {
    const title = section.title;
    const kind = paperSpecialKind(title);

    // 跳过参考文献章节
    if (kind === 'reference') return;

    // 跳过摘要和关键词章节
    if (['cn_abstract', 'cn_keywords', 'en_abstract', 'en_keywords'].includes(kind)) return;

    // 检查是否有子章节（只处理叶子章节）
    const hasChildren = state.paperSections.some((s) => s.parent === title);
    if (hasChildren) return;

    const existingContent = String(state.paperSectionContents[title] || '').trim();

    // 如果是"只写空白"模式，跳过已有内容的章节
    if (emptyOnly && existingContent) return;

    targets.push({
      title,
      context: existingContent,
    });
  });

  return targets;
}

async function runBatchWriteAllSections() {
  const outline = textValue('#paperOutline');
  if (!outline) {
    setState('paper', '请先生成或输入论文大纲', 'error');
    return;
  }

  if (!state.paperSections || state.paperSections.length === 0) {
    setState('paper', '没有可写作的章节', 'error');
    return;
  }

  // 询问用户是否只写空白章节
  const emptyOnly = confirm('是否只撰写空白章节？\n\n点击"确定"只写空白章节\n点击"取消"撰写全部章节（会覆盖已有内容）');

  const targets = collectBatchWriteTargets(emptyOnly);

  if (targets.length === 0) {
    setState('paper', emptyOnly ? '所有章节都已有内容' : '没有可写作的章节', 'error');
    return;
  }

  const wordCount = parseInt($('#wordCount').value) || 1000;
  const totalWords = targets.length * wordCount;

  // 警告确认
  if (targets.length > 8 || wordCount > 1200 || totalWords > 12000) {
    const warningMsg = `即将批量写作 ${targets.length} 个章节，预计生成约 ${totalWords} 字。\n\n这可能需要较长时间并消耗较多 API 额度。\n\n是否继续？`;
    if (!confirm(warningMsg)) {
      setState('paper', '已取消批量写作', 'error');
      return;
    }
  }

  setState('paper', `准备批量写作 ${targets.length} 个章节...`, 'running');

  const referenceStyle = $('#referenceStyle').value;
  const completedSections = [];
  const failedSections = [];
  let referenceTitle = findPaperSectionByKind('reference') || '';

  try {
    for (let i = 0; i < targets.length; i++) {
      const target = targets[i];
      const progress = `${i + 1}/${targets.length}`;

      setState('paper', `正在写作 ${progress}：${target.title}`, 'running');

      try {
        const payload = {
          action: 'section',
          outline,
          sectionTitle: target.title,
          context: target.context,
          wordCount,
          referenceStyle,
          allSections: state.paperSections.map(section => ({
            title: section.title,
            content: state.paperSectionContents[section.title] || ''
          })),
        };

        const data = await requestPaperRun(payload);

        if (data.result) {
          writePaperContentToSection(target.title, data.result, { loadEditor: false });
          completedSections.push(target.title);

          if (data.references && data.references.content) {
            referenceTitle = writePaperContentToSection(
              data.references.title || referenceTitle || '# 参考文献',
              data.references.content,
              { loadEditor: false }
            ) || referenceTitle;
          }

          if (data.updatedSections && Array.isArray(data.updatedSections)) {
            for (const section of data.updatedSections) {
              if (section.title && section.content) {
                writePaperContentToSection(section.title, section.content, { loadEditor: false });
              }
            }
          }
        } else {
          failedSections.push({ title: target.title, error: '返回结果为空' });
        }
      } catch (error) {
        failedSections.push({ title: target.title, error: error.message });

        // 如果是额度或频率限制错误，停止继续写作
        const errorMsg = String(error.message || '').toLowerCase();
        if (errorMsg.includes('quota') || errorMsg.includes('rate limit') || errorMsg.includes('额度') || errorMsg.includes('频率')) {
          setState('paper', `批量写作中止：${error.message}`, 'error');
          break;
        }
      }
    }

    syncPaperOutlineFromSections();
    if (completedSections.length) loadPaperSection(completedSections[completedSections.length - 1]);
    saveDraft();

    // 显示结果摘要
    if (failedSections.length === 0) {
      setState('paper', `批量写作完成！成功写作 ${completedSections.length} 个章节`, 'done');
      addHistoryRecord('paper', '批量写作全部章节', {
        output: `成功写作 ${completedSections.length} 个章节：\n${completedSections.join('\n')}`,
      });
    } else {
      const successMsg = completedSections.length > 0 ? `成功 ${completedSections.length} 个，` : '';
      setState('paper', `批量写作部分完成：${successMsg}失败 ${failedSections.length} 个`, 'error');

      const failedList = failedSections.slice(0, 5).map((f) => `${f.title}: ${f.error}`).join('\n');
      const moreMsg = failedSections.length > 5 ? `\n...还有 ${failedSections.length - 5} 个失败` : '';
      alert(`批量写作完成\n\n成功：${completedSections.length} 个\n失败：${failedSections.length} 个\n\n失败章节：\n${failedList}${moreMsg}`);
    }
  } catch (error) {
    setState('paper', `批量写作失败：${error.message}`, 'error');
  }
}

function paperRequestPayload(action, payloadText, targetSection, topic, outline, context) {
  const payload = {
    action,
    text: payloadText,
    topic,
    subject: textValue('#paperSubject'),
    paperStyle: $('#paperStyle').value,
    referenceStyle: $('#referenceStyle').value,
    sectionTitle: targetSection,
    wordCount: $('#wordCount').value,
    outline,
    context,
    language: '中文',
  };

  // Include all sections for reference management
  if (action === 'section') {
    payload.allSections = state.paperSections.map(section => ({
      title: section.title,
      content: state.paperSectionContents[section.title] || ''
    }));
  }

  return payload;
}

async function requestPaperRun(payload) {
  return requestJson('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

async function runPaperAbstract(payload) {
  const cn = await requestPaperRun({ ...payload, language: '中文' });
  const output = cn?.result ? `# 中文摘要\n${cn.result}` : '';
  if (!output) throw new Error('摘要生成结果为空');
  writeAbstractResultToSections(output);
  syncPaperOutlineFromSections();
  loadPaperSection(findPaperSectionByKind('cn_abstract') || '中文摘要');
  state.lastAnalysis = cn?.analysis || null;
  return { result: output, analysis: state.lastAnalysis, failed: '' };
}

async function runPaperAction(action) {
  if (action === 'batch-write') {
    await runBatchWriteAllSections();
    return;
  }

  const topic = textValue('#paperTopic');
  const outline = textValue('#paperOutline');
  const context = textValue('#paperContext');
  storeCurrentPaperEditor();
  const targetSection = paperTargetSectionForAction(action);
  const payloadText = action === 'outline'
    ? topic
    : (action === 'abstract' ? collectPaperTextForAbstract() : (action === 'section' ? outline : context || outline || topic));
  if (!payloadText) {
    setState('paper', action === 'abstract' ? '请先完善论文正文内容' : '请先填写内容', 'error');
    return;
  }

  setState('paper', '论文生成中...', 'running');
  try {
    const payload = paperRequestPayload(action, payloadText, targetSection, topic, outline, context);
    const data = action === 'abstract'
      ? await runPaperAbstract(payload)
      : await requestPaperRun(payload);
    if (action === 'outline' && data.result) {
      setText('#paperOutline', data.result);
      refreshPaperSections(true, { preserveExisting: false });
    } else if (action === 'abstract') {
      loadPaperSection(findPaperSectionByKind('cn_abstract') || targetSection);
    } else {
      writePaperContentToSection(targetSection, data.result || '');

      // Handle references if returned
      if (data.references && data.references.content) {
        const refTitle = data.references.title || '# 参考文献';
        writePaperContentToSection(refTitle, data.references.content, { loadEditor: false });
        if (data.references.content.trim()) {
          setState('paper', `章节写作完成，参考文献已写入 ${refTitle}`, 'done');
        }
      }

      // Handle updated sections (citation renumbering)
      if (data.updatedSections && Array.isArray(data.updatedSections)) {
        for (const section of data.updatedSections) {
          if (section.title && section.content) {
            writePaperContentToSection(section.title, section.content, { loadEditor: false });
          }
        }
      }

      syncPaperOutlineFromSections();
    }
    state.lastAnalysis = data.analysis || null;
    addHistoryRecord('paper', PAPER_ACTION_LABELS[action] || action, {
      inputSelector: action === 'outline' ? '#paperTopic' : '#paperContext',
      outputSelector: '#paperResult',
      output: data.result || '',
      analysis: data.analysis || null,
    });
    saveDraft();
    if (!data.references || !data.references.content) {
      setState('paper', data.failed || '论文生成完成', data.failed ? 'error' : 'done');
    }
  } catch (error) {
    setState('paper', '论文生成失败', 'error');
    if (action !== 'abstract') {
      setText('#paperResult', error.message);
      setText('#paperContext', error.message);
    }
  }
}

async function runTransformPanel(scope, inputSelector, outputSelector, extra = {}) {
  const text = textValue(inputSelector);
  if (!text) {
    setState(scope, '请输入文本', 'error');
    return null;
  }
  setState(scope, '处理中...', 'running');
  try {
    const data = await requestJson('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, ...extra }),
    });
    setText(outputSelector, data.result || '');
    state.lastAnalysis = data.analysis || null;
    setState(scope, '完成', 'done');
    saveDraft();
    return data;
  } catch (error) {
    setState(scope, '失败', 'error');
    setText(outputSelector, error.message);
    return null;
  }
}

async function reviewText(inputSelector, outputSelector, reviewSelector, sourceText = '') {
  const target = textValue(outputSelector) || textValue(inputSelector);
  if (!target) {
    $(reviewSelector).textContent = '请先输入原文或生成处理结果。';
    return;
  }
  const data = await requestJson('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'analyze', text: target, sourceText }),
  });
  state.lastAnalysis = data.analysis;
  $(reviewSelector).textContent = renderAnalysisSummary(data.analysis);
  saveDraft();
}

async function runPanel(kind) {
  if (kind === 'ai') {
    const data = await runTransformPanel('ai', '#aiInput', '#aiOutput', { action: selectedAction('ai') });
    if (data) {
      $('#aiReview').textContent = renderAnalysisSummary(data.analysis);
      addHistoryRecord('ai', 'AI 痕迹消除', { inputSelector: '#aiInput', outputSelector: '#aiOutput', analysis: data.analysis });
      saveDraft();
    }
  }
  if (kind === 'plagiarism') {
    const sourceText = textValue('#plagiarismSource');
    const data = await runTransformPanel('plagiarism', '#plagiarismInput', '#plagiarismOutput', {
      action: selectedAction('plagiarism'),
      sourceText,
    });
    if (data) {
      $('#plagiarismReview').textContent = renderAnalysisSummary(data.analysis);
      addHistoryRecord('plagiarism', '降查重率', { inputSelector: '#plagiarismInput', outputSelector: '#plagiarismOutput', analysis: data.analysis });
      saveDraft();
    }
  }
  if (kind === 'polish') {
    const data = await runTransformPanel('polish', '#polishInput', '#polishOutput', {
      action: 'polish',
      polishMode: selectedAction('polish'),
      taskType: $('#polishTaskType').value,
      executionMode: $('#polishExecutionMode').value,
      topic: textValue('#polishTopic'),
      notes: textValue('#polishNotes'),
    });
    if (data) {
      $('#polishReview').textContent = renderAnalysisSummary(data.analysis);
      addHistoryRecord('polish', '学术润色', { inputSelector: '#polishInput', outputSelector: '#polishOutput', analysis: data.analysis });
      saveDraft();
    }
  }
  if (kind === 'translate') {
    const data = await runTransformPanel('polish', '#polishInput', '#polishOutput', { action: 'polish', polishMode: 'full' });
    if (data) {
      $('#polishReview').textContent = '翻译润色入口已对应到润色工作台；当前 Web 后端暂使用综合润色能力。\n\n' + renderAnalysisSummary(data.analysis);
      addHistoryRecord('polish', '翻译润色', { inputSelector: '#polishInput', outputSelector: '#polishOutput', analysis: data.analysis });
      saveDraft();
    }
  }
  if (kind === 'correction') {
    const data = await runTransformPanel('correction', '#correctionInput', '#correctionOutput', {
      action: 'correction',
      citationStyle: $('#correctionCitationStyle').value,
    });
    if (data) {
      renderCorrection(data.analysis && data.analysis.correction);
      addHistoryRecord('correction', '智能纠错', { inputSelector: '#correctionInput', outputSelector: '#correctionOutput', analysis: data.analysis });
      saveDraft();
    }
  }
  if (kind === 'correction-ai') {
    await reviewText('#correctionInput', '#correctionOutput', '#correctionReport');
    renderCorrectionReviewAsIssues(state.lastAnalysis, 'AI 风格扫描');
  }
  if (kind === 'correction-citation') {
    const text = textValue('#correctionInput') || textValue('#correctionOutput');
    if (!text) {
      setState('correction', '请输入文本', 'error');
      return;
    }
    const data = await requestJson('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'citation', text }),
    });
    state.lastAnalysis = data.analysis;
    $('#correctionReport').textContent = renderAnalysisSummary(data.analysis);
    renderCorrectionReviewAsIssues(data.analysis, '引用规范检查');
    setState('correction', '专项核验完成', 'done');
    addHistoryRecord('correction', '引用规范检查', { inputSelector: '#correctionInput', outputSelector: '#correctionReport', analysis: data.analysis });
    saveDraft();
  }
}

function renderCorrection(correction) {
  if (!correction) return;
  const counts = correction.counts || {};
  const byCategory = counts.by_category || {};
  const labels = correction.categoryLabels || {};
  const order = correction.categoryOrder || Object.keys(byCategory);
  $('#correctionStats').innerHTML = order.map((key) => `
    <article class="metric-card">
      <span>${escapeHtml(labels[key] || key)}</span>
      <strong>${escapeHtml(byCategory[key] || 0)}</strong>
      <p>分类问题数</p>
    </article>
  `).join('');
  const issues = correction.issues || [];
  $('#correctionIssues').innerHTML = issues.length ? issues.slice(0, 40).map((issue) => `
    <button class="issue-item" type="button" data-issue-id="${escapeHtml(issue.id)}">
      <strong>${escapeHtml(issue.title || '未命名问题')}</strong>
      <span>${escapeHtml((labels[issue.category] || issue.category || '-') + ' / ' + (issue.severity || '-'))}</span>
    </button>
  `).join('') : '暂未发现待处理问题。';
  $('#correctionReport').textContent = correction.report || '暂无报告。';
  $$('.issue-item').forEach((button) => {
    button.addEventListener('click', () => {
      const issue = issues.find((item) => item.id === button.dataset.issueId);
      if (!issue) return;
      $('#correctionReport').textContent = [
        `问题：${issue.title || '未命名问题'}`,
        `分类：${labels[issue.category] || issue.category || '-'}`,
        `级别：${issue.severity || '-'}`,
        '',
        `说明：${issue.message || ''}`,
        issue.original ? `原文片段：${issue.original}` : '',
        issue.suggestion ? `修改建议：${issue.suggestion}` : '',
        issue.replacement ? `自动修复：${issue.replacement}` : '',
      ].filter(Boolean).join('\n');
    });
  });
}

function renderCorrectionReviewAsIssues(analysis, title) {
  $('#correctionStats').innerHTML = '';
  $('#correctionIssues').innerHTML = `<article class="issue-item"><strong>${escapeHtml(title)}</strong><span>专项核验结果</span></article>`;
}

function selectedHistoryRecord() {
  return getHistoryRecords().find((record) => record.id === state.selectedHistoryId) || null;
}

function renderHistory() {
  const list = $('#historyList');
  if (!list) return;
  const records = getHistoryRecords();
  const count = $('#historyCount');
  if (count) count.textContent = `${records.length} 条`;
  if (!records.length) {
    list.textContent = '暂无历史记录。成功生成或处理后会自动出现在这里。';
    $('#historyMeta').textContent = '请选择左侧历史记录。';
    setText('#historyPreview', '');
    state.selectedHistoryId = '';
    return;
  }
  if (!records.some((record) => record.id === state.selectedHistoryId)) {
    state.selectedHistoryId = records[0].id;
  }
  list.innerHTML = records.map((record) => `
    <button class="history-item ${record.id === state.selectedHistoryId ? 'selected' : ''}" data-history-id="${escapeHtml(record.id)}" type="button">
      <strong>${escapeHtml(record.title || record.operation || '未命名版本')}</strong>
      <span>${escapeHtml(record.module || PAGE_LABELS[record.page] || record.page)} / ${escapeHtml(record.operation || '-')}</span>
      <small>${escapeHtml(formatTime(record.time))} · ${escapeHtml(previewText(record.output || record.input, 64))}</small>
    </button>
  `).join('');
  $$('.history-item').forEach((button) => {
    button.addEventListener('click', () => {
      state.selectedHistoryId = button.dataset.historyId;
      renderHistory();
    });
  });
  renderSelectedHistory();
}

function renderSelectedHistory() {
  const record = selectedHistoryRecord();
  if (!record) return;
  $('#historyMeta').textContent = [
    `模块：${record.module || PAGE_LABELS[record.page] || record.page}`,
    `操作：${record.operation || '-'}`,
    `时间：${formatTime(record.time)}`,
    `标题：${record.title || '-'}`,
    '',
    '点击“恢复到工作台”会填回该版本对应页面的输入、输出、大纲、章节选择和复核摘要。',
  ].join('\n');
  setText('#historyPreview', record.output || record.input || '');
}

function restoreSelectedHistory() {
  const record = selectedHistoryRecord();
  if (!record) return;
  state.modes = { ...state.modes, ...(record.modes || {}) };
  state.selectedPaperSection = record.selectedPaperSection || '';
  state.paperEditorSection = record.paperEditorSection || state.selectedPaperSection || '';
  state.paperSectionContents = record.paperSectionContents || {};
  state.paperSectionContentSources = record.paperSectionContentSources || {};
  state.lastAnalysis = record.analysis || null;
  applyFields(record.fields || {});
  syncModeSelections();
  if (record.page === 'correction' && record.analysis?.correction) {
    renderCorrection(record.analysis.correction);
  } else if (record.page === 'correction' && record.analysis) {
    renderCorrectionReviewAsIssues(record.analysis, record.operation || '专项核验结果');
  }
  setPage(record.page || 'paper');
  saveDraft();
}

function clearHistoryRecords() {
  if (!confirm('确定清空 Web 端历史记录吗？当前草稿不会被删除。')) return;
  setHistoryRecords([]);
  state.selectedHistoryId = '';
  renderHistory();
}

function bindActions() {
  $$('.feature-item').forEach((button) => button.addEventListener('click', () => setPage(button.dataset.page)));
  $$('[data-page-link]').forEach((link) => link.addEventListener('click', (event) => {
    event.preventDefault();
    setPage(link.dataset.pageLink);
  }));
  bindModeCards();
  $$('[data-paper-action]').forEach((button) => button.addEventListener('click', () => runPaperAction(button.dataset.paperAction)));
  $('#refreshOutlineSections').addEventListener('click', () => refreshPaperSections(true));
  $('#paperOutline').addEventListener('input', () => refreshPaperSections(false));
  $('#sectionTitle').addEventListener('input', () => {
    const title = textValue('#sectionTitle');
    if (title) {
      state.selectedPaperSection = title;
    }
    renderPaperSections();
    saveDraft();
  });
  $('#paperContext').addEventListener('input', () => {
    storeCurrentPaperEditor({ markUser: true });
    saveDraft();
  });
  $$('[data-run-panel]').forEach((button) => button.addEventListener('click', () => runPanel(button.dataset.runPanel)));
  $$('[data-review-panel="ai"]').forEach((button) => button.addEventListener('click', () => reviewText('#aiInput', '#aiOutput', '#aiReview')));
  $$('[data-review-panel="plagiarism"]').forEach((button) => button.addEventListener('click', () => reviewText('#plagiarismInput', '#plagiarismOutput', '#plagiarismReview', textValue('#plagiarismSource'))));
  $$('[data-diff-panel="ai"]').forEach((button) => button.addEventListener('click', () => { $('#aiDiff').textContent = diffText(textValue('#aiInput'), textValue('#aiOutput')); }));
  $$('[data-diff-panel="plagiarism"]').forEach((button) => button.addEventListener('click', () => { $('#plagiarismDiff').textContent = diffText(textValue('#plagiarismInput'), textValue('#plagiarismOutput')); }));
  $$('[data-clear-panel]').forEach((button) => button.addEventListener('click', () => clearPanel(button.dataset.clearPanel)));
  FIELD_SELECTORS.forEach((selector) => {
    const node = $(selector);
    if (node) node.addEventListener('input', saveDraft);
  });
  $$('[data-copy-from]').forEach((button) => button.addEventListener('click', async () => {
    const node = document.getElementById(button.dataset.copyFrom);
    if (node && node.value) await navigator.clipboard.writeText(node.value);
  }));
  $('#refreshStatus').addEventListener('click', () => loadStatus().catch((error) => {
    if ($('#modelStatus')) $('#modelStatus').textContent = error.message;
  }));
  $('#refreshConfig').addEventListener('click', () => loadStatus().catch((error) => { $('#providerList').textContent = error.message; }));
  $('#configProviderType').addEventListener('change', () => {
    state.editingApiId = '';
    $('#configName').value = '';
    $('#configBaseUrl').value = '';
    $('#configModel').value = '';
    $('#configModelDisplayName').value = '';
    applyPresetToForm();
  });
  $('#newConfigApi').addEventListener('click', () => {
    fillConfigForm(null);
    applyPresetToForm();
    setState('config', '待保存');
  });
  $('#saveConfigApi').addEventListener('click', async () => {
    setState('config', '正在保存...', 'running');
    try {
      const data = await requestJson('/api/config/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configFormPayload()),
      });
      renderConfig(data.config);
      fillConfigForm(data.record);
      await loadStatus();
      setState('config', '已保存并启用', 'done');
      $('#configMessage').textContent = `已保存并启用：${data.record.name} / ${data.record.model || data.record.modelDisplayName}`;
    } catch (error) {
      setState('config', '保存失败', 'error');
      $('#configMessage').textContent = error.message;
    }
  });
  $('#fetchConfigModels').addEventListener('click', async () => {
    setState('config', '正在获取模型...', 'running');
    try {
      const data = await requestJson('/api/config/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configFormPayload()),
      });
      renderModelOptions(data.models || []);
      if (!textValue('#configModel') && data.models && data.models.length) {
        $('#configModel').value = data.models[0];
      }
      setState('config', '模型列表已更新', 'done');
      $('#configMessage').textContent = `获取到 ${data.models.length} 个模型，可在“模型 ID”输入框下拉选择。`;
    } catch (error) {
      setState('config', '获取失败', 'error');
      $('#configMessage').textContent = error.message;
    }
  });
  $('#refreshHistory').addEventListener('click', renderHistory);
  $('#restoreHistory').addEventListener('click', restoreSelectedHistory);
  $('#clearHistory').addEventListener('click', clearHistoryRecords);
  $('#themeToggle').addEventListener('click', () => {
    const root = document.documentElement;
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    localStorage.setItem('paperlab-web-theme', next);
  });
  window.addEventListener('hashchange', routeFromHash);
}

function clearPanel(scope) {
  const fields = {
    ai: ['#aiInput', '#aiOutput'],
    plagiarism: ['#plagiarismInput', '#plagiarismOutput', '#plagiarismSource'],
    polish: ['#polishInput', '#polishOutput', '#polishNotes'],
    correction: ['#correctionInput', '#correctionOutput'],
  }[scope] || [];
  fields.forEach((selector) => setText(selector, ''));
  setState(scope, '待处理');
  saveDraft();
}

function restoreTheme() {
  const saved = localStorage.getItem('paperlab-web-theme');
  document.documentElement.dataset.theme = saved === 'dark' ? 'dark' : 'light';
}

restoreTheme();
bindActions();
restoreDraft();
routeFromHash();
renderHistory();
loadStatus().catch((error) => {
  if ($('#modelStatus')) $('#modelStatus').textContent = error.message;
  renderProviders(null);
});
loadConfig().catch((error) => {
  if ($('#providerList')) $('#providerList').textContent = error.message;
});
