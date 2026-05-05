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
  paperReferenceSnapshot: '',
  paperEditorDirty: false,
  paperRunning: false,
  paperSectionFilter: '',
  paperPushTargets: {},
  paperTemplate: null,
  dataChartTargets: [],
  selectedDataChartTargetId: '',
  dataChartResult: null,
  dataChartSearchResult: null,
  dataChartApproved: false,
  promptTemplates: {},
  activePromptScope: '',
  selectedPromptTemplateId: '',
  selectedHistoryId: '',
  currentCorrectionIssues: [],
  currentCorrectionLabels: {},
  selectedCorrectionIssueId: '',
  restoring: false,
};

const DEFAULT_REQUEST_TIMEOUT_MS = 120000;
const PAPER_REQUEST_TIMEOUT_MS = 210000;
const BATCH_PAPER_REQUEST_TIMEOUT_MS = 420000;
const BATCH_WRITE_MAX_ATTEMPTS = 3;

const STORAGE_KEYS = {
  draft: 'paperlab-web-draft-v3',
  history: 'paperlab-web-history-v2',
};

const FIELD_SELECTORS = [
  '#paperTopic', '#paperSubject', '#paperStyle', '#referenceStyle', '#sectionTitle', '#totalWordCountAuto', '#totalWordCount', '#wordCountAuto', '#wordCount', '#paperOutline', '#paperContext', '#paperResult',
  '#dataChartFullText', '#dataChartQuery', '#dataChartType', '#dataChartUnit', '#dataChartTitle', '#dataChartDataTable', '#dataChartSourceList', '#dataChartResultText', '#dataChartDiff',
  '#aiInput', '#aiOutput', '#aiReview', '#aiDiff',
  '#plagiarismInput', '#plagiarismOutput', '#plagiarismOutputEditor', '#plagiarismSource', '#plagiarismReview', '#plagiarismDiff',
  '#polishTaskType', '#polishExecutionMode', '#polishTopic', '#polishNotes', '#polishInput', '#polishOutput', '#polishOutputEditor', '#polishReview', '#polishDiff',
  '#correctionCitationStyle', '#correctionInput', '#correctionOutput', '#correctionDiff', '#correctionReport',
];

const PAGE_LABELS = {
  paper: '论文写作',
  datachart: '数据图表',
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

const DEFAULT_PROMPT_TEMPLATES = {
  ai: [
    {
      id: 'default',
      name: '默认模板',
      content: '请在保留原文事实、数据、公式、专业术语和引用编号的前提下，降低机械化表达，减少模板化连接词，调整句式节奏，使文本更像真实学术写作。输出时只给处理后的正文。',
      builtin: true,
    },
  ],
  plagiarism: [
    {
      id: 'default',
      name: '默认模板',
      content: '请在不改变原意、数据、引文编号和关键术语的前提下，重组句式和段落表达，降低与重复源的连续相似表达，避免简单同义词替换。输出时只给降重后的正文。',
      builtin: true,
    },
  ],
  polish: [
    {
      id: 'default',
      name: '默认模板',
      content: '请保持原文事实、数据、公式、参考文献编号和专业术语不变，增强学术语气、逻辑衔接和论证清晰度，避免扩写无依据内容。输出时只给润色后的正文。',
      builtin: true,
    },
  ],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function textValue(selector) {
  const node = $(selector);
  return readNodeText(node).trim();
}

function setText(selector, value) {
  const node = $(selector);
  writeNodeText(node, value);
}

function readNodeText(node) {
  if (!node) return '';
  if (node.type === 'checkbox') return node.checked ? '1' : '0';
  if (isPaperEditorNode(node)) return readPaperEditorText(node);
  if (node.isContentEditable) {
    return (node.innerText || node.textContent || '').replace(/\u00a0/g, ' ');
  }
  return 'value' in node ? (node.value || '') : (node.textContent || '');
}

function writeNodeText(node, value) {
  if (!node) return;
  if (node.type === 'checkbox') {
    node.checked = value === true || value === '1' || value === 'true' || value === 'on';
    return;
  }
  if (isPaperEditorNode(node)) {
    writePaperEditorText(node, value);
    return;
  }
  if (node.isContentEditable) node.textContent = value || '';
  else if ('value' in node) node.value = value || '';
  else node.textContent = value || '';
}

function isPaperEditorNode(node) {
  return Boolean(node && node.id === 'paperContext');
}

function normalizeEditorPlainText(value) {
  return String(value || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(/\u00a0/g, ' ');
}

let mathJaxLoader = null;
let paperMathRenderSeq = 0;
const PAPER_MATH_RENDERED_STATES = new Set(['true', 'pending', 'fallback']);
const PAPER_DISPLAY_MATH_BEGIN_RE = /\\begin\{(equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|flalign\*?)\}/;
const PAPER_DISPLAY_MATH_ENV_RE = /\\begin\{(?:equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|flalign\*?)\}[\s\S]*?\\end\{(?:equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|flalign\*?)\}/;

function escapeRegExpLiteral(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function paperEditorUsesStoredMathSource(editor) {
  return PAPER_MATH_RENDERED_STATES.has(editor?.dataset?.mathRendered || '');
}

function paperEditorBlockSource(block, options = {}) {
  const source = block?.dataset?.sourceText;
  if (source !== undefined && block?.dataset?.sourceKind === 'image') return normalizeEditorPlainText(source).trim();
  if (options.preferStored && source !== undefined) return normalizeEditorPlainText(source).trim();
  return normalizeEditorPlainText(block?.innerText || block?.textContent || '').trim();
}

function readPaperEditorText(editor) {
  if (!editor) return '';
  const blockSelector = 'p, div, h1, h2, h3, h4, h5, h6, blockquote, li';
  const blocks = Array.from(editor.children || []).filter((child) => child.matches(blockSelector));
  if (!blocks.length) return normalizeEditorPlainText(editor.innerText || editor.textContent || '');

  const preferStored = paperEditorUsesStoredMathSource(editor);
  return blocks
    .map((block) => paperEditorBlockSource(block, { preferStored }))
    .filter(Boolean)
    .join('\n\n');
}

function appendTextWithLineBreaks(parent, text) {
  String(text || '').split('\n').forEach((line, index) => {
    if (index > 0) parent.appendChild(document.createElement('br'));
    parent.appendChild(document.createTextNode(line));
  });
}

function parseMarkdownImageLine(text) {
  const match = String(text || '').trim().match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
  if (!match) return null;
  return {
    alt: match[1] || '论文图表',
    src: match[2] || '',
  };
}

function updatePaperEditorFormatMode(title = state.paperEditorSection) {
  const editor = $('#paperContext');
  if (!editor) return;
  const isReference = paperSpecialKind(title) === 'reference';
  editor.classList.toggle('reference-editor-text', isReference);
  editor.classList.toggle('article-editor-text', !isReference);
}

function containsLatexMath(text) {
  const value = String(text || '');
  return /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)/.test(value)
    || PAPER_DISPLAY_MATH_ENV_RE.test(value)
    || /(^|[^\\])\$[^$\n]*?[^\\\s$]\$/.test(value);
}

function containsDisplayLatexMath(text) {
  const value = String(text || '');
  return /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]/.test(value) || PAPER_DISPLAY_MATH_ENV_RE.test(value);
}

function isDisplayLatexMathOnly(text) {
  const value = String(text || '').trim();
  if (!value) return false;
  const displayPatterns = [
    /\$\$[\s\S]+?\$\$/,
    /\\\[[\s\S]+?\\\]/,
    PAPER_DISPLAY_MATH_ENV_RE,
  ];
  const withoutMath = displayPatterns
    .reduce((current, pattern) => current.replace(pattern, ''), value)
    .replace(/^[（(]\d+[）)]$/g, '')
    .trim();
  return !withoutMath;
}

function countUnescapedToken(text, token) {
  const value = String(text || '');
  let count = 0;
  let index = 0;
  while ((index = value.indexOf(token, index)) !== -1) {
    let slashCount = 0;
    for (let pos = index - 1; pos >= 0 && value[pos] === '\\'; pos -= 1) slashCount += 1;
    if (slashCount % 2 === 0) count += 1;
    index += token.length;
  }
  return count;
}

function findOpenDisplayMathFence(line) {
  const value = String(line || '');
  const bracketOpen = value.indexOf('\\[');
  if (bracketOpen >= 0 && value.indexOf('\\]', bracketOpen + 2) < 0) return { type: 'bracket' };
  if (countUnescapedToken(value, '$$') % 2 === 1) return { type: 'dollar' };
  const envMatch = value.match(PAPER_DISPLAY_MATH_BEGIN_RE);
  if (envMatch) {
    const envName = envMatch[1];
    const endRe = new RegExp(`\\\\end\\{${escapeRegExpLiteral(envName)}\\}`);
    if (!endRe.test(value.slice(envMatch.index + envMatch[0].length))) return { type: 'env', name: envName };
  }
  return null;
}

function lineClosesDisplayMathFence(line, fence) {
  const value = String(line || '');
  if (!fence) return false;
  if (fence.type === 'bracket') return value.includes('\\]');
  if (fence.type === 'dollar') return countUnescapedToken(value, '$$') > 0;
  if (fence.type === 'env') {
    return new RegExp(`\\\\end\\{${escapeRegExpLiteral(fence.name)}\\}`).test(value);
  }
  return false;
}

function splitPaperArticleBlocks(text) {
  const blocks = [];
  const lines = String(text || '').split('\n');
  let buffer = [];
  let displayFence = null;

  const flush = () => {
    const part = buffer.join('\n').trim();
    if (part) blocks.push(part);
    buffer = [];
  };

  lines.forEach((line) => {
    if (!line.trim() && !displayFence) {
      flush();
      return;
    }
    buffer.push(line);
    if (displayFence) {
      if (lineClosesDisplayMathFence(line, displayFence)) {
        displayFence = null;
        flush();
      }
      return;
    }
    displayFence = findOpenDisplayMathFence(line);
    if (!displayFence) flush();
  });
  flush();
  return blocks;
}

function setPaperEditorBlockMathFlags(block, source) {
  const hasMath = containsLatexMath(source);
  const hasDisplayMath = hasMath && containsDisplayLatexMath(source);
  block.toggleAttribute('data-has-math', hasMath);
  block.toggleAttribute('data-has-display-math', hasDisplayMath);
  block.toggleAttribute('data-display-math-only', hasDisplayMath && isDisplayLatexMathOnly(source));
  return hasMath;
}

function invalidatePaperEditorMathRender(editor = $('#paperContext')) {
  paperMathRenderSeq += 1;
  if (!editor) return String(paperMathRenderSeq);
  editor.dataset.mathRenderId = String(paperMathRenderSeq);
  editor.dataset.mathRendered = 'false';
  return editor.dataset.mathRenderId;
}

function syncPaperEditorSourcesFromDom(editor = $('#paperContext')) {
  if (!editor) return;
  const isReference = paperSpecialKind(state.paperEditorSection) === 'reference';
  const blockSelector = 'p, div, h1, h2, h3, h4, h5, h6, blockquote, li';
  Array.from(editor.children || []).filter((child) => child.matches(blockSelector)).forEach((block) => {
    if (isReference) block.classList.add('chapter-editor-reference-line');
    else block.classList.add('chapter-editor-paragraph');
    if (block.dataset.sourceKind === 'image' && block.dataset.sourceText) return;
    const source = normalizeEditorPlainText(block.innerText || block.textContent || '').trim();
    if (source) block.dataset.sourceText = source;
    else delete block.dataset.sourceText;
    if (!isReference) setPaperEditorBlockMathFlags(block, source);
  });
}

function ensureMathJax() {
  if (window.MathJax?.typesetPromise) return Promise.resolve(window.MathJax);
  if (mathJaxLoader) return mathJaxLoader;

  window.MathJax = window.MathJax || {
    tex: {
      inlineMath: [['\\(', '\\)'], ['$', '$']],
      displayMath: [['\\[', '\\]'], ['$$', '$$']],
      processEscapes: true,
      tags: 'ams',
    },
    options: {
      skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
    },
  };

  mathJaxLoader = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js';
    script.async = true;
    script.onload = () => {
      const ready = window.MathJax?.startup?.promise || Promise.resolve();
      ready.then(() => resolve(window.MathJax)).catch(reject);
    };
    script.onerror = () => reject(new Error('MathJax 加载失败'));
    document.head.appendChild(script);
  });
  return mathJaxLoader;
}

function renderPaperEditorMath(editor = $('#paperContext')) {
  if (!editor || paperSpecialKind(state.paperEditorSection) === 'reference') return;
  const mathBlocks = Array.from(editor.querySelectorAll('.chapter-editor-paragraph')).filter((block) => {
    const source = paperEditorBlockSource(block, { preferStored: true });
    return setPaperEditorBlockMathFlags(block, source);
  });
  if (!mathBlocks.length) {
    invalidatePaperEditorMathRender(editor);
    return;
  }
  const renderId = String(++paperMathRenderSeq);
  editor.dataset.mathRenderId = renderId;
  editor.dataset.mathRendered = 'pending';
  ensureMathJax()
    .then((mathJax) => {
      if (editor.dataset.mathRenderId !== renderId) return;
      if (!mathJax?.typesetPromise) return;
      mathJax.typesetClear?.(mathBlocks);
      return mathJax.typesetPromise(mathBlocks);
    })
    .then(() => {
      if (editor.dataset.mathRenderId !== renderId) return;
      editor.dataset.mathRendered = 'true';
    })
    .catch(() => {
      if (editor.dataset.mathRenderId !== renderId) return;
      editor.dataset.mathRendered = 'fallback';
    });
}

function restorePaperEditorMathSource(editor = $('#paperContext')) {
  if (!editor || !paperEditorUsesStoredMathSource(editor)) return;
  const source = readPaperEditorText(editor);
  invalidatePaperEditorMathRender(editor);
  writePaperEditorText(editor, source, { renderMath: false });
}

function writePaperEditorText(editor, value, options = {}) {
  updatePaperEditorFormatMode();
  const normalized = normalizeEditorPlainText(value).replace(/^\n+|\n+$/g, '');
  editor.replaceChildren();
  invalidatePaperEditorMathRender(editor);
  if (!normalized.trim()) return;

  const isReference = paperSpecialKind(state.paperEditorSection) === 'reference';
  const parts = isReference
    ? normalized.split('\n').map((line) => line.trim()).filter(Boolean)
    : splitPaperArticleBlocks(normalized);
  const blocks = parts.length ? parts : [normalized.trim()];

  blocks.forEach((part) => {
    const block = document.createElement(isReference ? 'div' : 'p');
    block.className = isReference ? 'chapter-editor-reference-line' : 'chapter-editor-paragraph';
    block.dataset.sourceText = part;
    const image = !isReference ? parseMarkdownImageLine(part) : null;
    if (image?.src) {
      block.classList.add('chapter-editor-image-block');
      block.dataset.sourceKind = 'image';
      const img = document.createElement('img');
      img.src = image.src;
      img.alt = image.alt;
      block.appendChild(img);
    } else {
      if (!isReference) setPaperEditorBlockMathFlags(block, part);
      appendTextWithLineBreaks(block, part);
    }
    editor.appendChild(block);
  });

  if (!isReference && options.renderMath !== false) {
    renderPaperEditorMath(editor);
  }
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

function createPromptTemplateId() {
  return `tpl-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function normalizePromptTemplates(raw = {}) {
  const normalized = {};
  Object.entries(DEFAULT_PROMPT_TEMPLATES).forEach(([scope, defaults]) => {
    const storedScope = raw?.[scope] || {};
    const storedTemplates = Array.isArray(storedScope.templates) ? storedScope.templates : [];
    const templatesById = new Map();
    defaults.forEach((template) => templatesById.set(template.id, { ...template }));
    storedTemplates.forEach((template) => {
      const id = String(template?.id || '').trim() || createPromptTemplateId();
      const name = String(template?.name || '').trim() || '未命名模板';
      const content = String(template?.content || '').trim();
      if (!content) return;
      templatesById.set(id, {
        id,
        name,
        content,
        builtin: Boolean(template?.builtin && id === 'default'),
      });
    });
    const templates = Array.from(templatesById.values());
    const activeId = templates.some((template) => template.id === storedScope.activeId)
      ? storedScope.activeId
      : defaults[0]?.id || templates[0]?.id || '';
    normalized[scope] = { activeId, templates };
  });
  return normalized;
}

function ensurePromptTemplates() {
  state.promptTemplates = normalizePromptTemplates(state.promptTemplates);
  return state.promptTemplates;
}

function promptTemplatesForScope(scope) {
  ensurePromptTemplates();
  return state.promptTemplates[scope]?.templates || [];
}

function activePromptTemplate(scope) {
  ensurePromptTemplates();
  const group = state.promptTemplates[scope];
  return (group?.templates || []).find((template) => template.id === group.activeId) || group?.templates?.[0] || null;
}

function selectedPromptTemplate(scope = state.activePromptScope) {
  const templates = promptTemplatesForScope(scope);
  return templates.find((template) => template.id === state.selectedPromptTemplateId) || activePromptTemplate(scope);
}

function promptTemplatePreview(content) {
  return previewText(String(content || '').replace(/\s+/g, ' '), 80);
}

function renderPromptTemplateDialog() {
  const scope = state.activePromptScope;
  const dialog = $('#promptTemplateDialog');
  if (!dialog || !scope) return;
  ensurePromptTemplates();
  const title = $('#promptTemplateTitle');
  if (title) title.textContent = `${PAGE_LABELS[scope] || '功能区'}提示词`;
  const group = state.promptTemplates[scope];
  if (!state.selectedPromptTemplateId || !group.templates.some((template) => template.id === state.selectedPromptTemplateId)) {
    state.selectedPromptTemplateId = group.activeId || group.templates[0]?.id || '';
  }
  const list = $('#promptTemplateList');
  if (list) {
    list.innerHTML = group.templates.map((template) => `
      <button class="prompt-template-item ${template.id === state.selectedPromptTemplateId ? 'selected' : ''}" data-prompt-template-id="${escapeHtml(template.id)}" type="button">
        <strong>${escapeHtml(template.name)}</strong>
        <span>${escapeHtml(promptTemplatePreview(template.content))}</span>
        ${template.id === group.activeId ? '<mark>已启用</mark>' : ''}
      </button>
    `).join('');
    $$('.prompt-template-item').forEach((button) => {
      button.addEventListener('click', () => {
        state.selectedPromptTemplateId = button.dataset.promptTemplateId || '';
        renderPromptTemplateDialog();
      });
    });
  }
  const selected = selectedPromptTemplate(scope);
  if ($('#promptTemplateName')) $('#promptTemplateName').value = selected?.name || '';
  if ($('#promptTemplateContent')) $('#promptTemplateContent').value = selected?.content || '';
  const deleteButton = $('#promptTemplateDelete');
  if (deleteButton) deleteButton.disabled = selected?.builtin || group.templates.length <= 1;
}

function openPromptTemplateDialog(scope) {
  state.activePromptScope = scope;
  ensurePromptTemplates();
  state.selectedPromptTemplateId = state.promptTemplates[scope]?.activeId || 'default';
  renderPromptTemplateDialog();
  const dialog = $('#promptTemplateDialog');
  if (dialog) dialog.hidden = false;
  $('#promptTemplateName')?.focus();
}

function closePromptTemplateDialog() {
  const dialog = $('#promptTemplateDialog');
  if (dialog) dialog.hidden = true;
}

function saveSelectedPromptTemplate() {
  const scope = state.activePromptScope;
  if (!scope) return;
  ensurePromptTemplates();
  const group = state.promptTemplates[scope];
  const selected = selectedPromptTemplate(scope);
  if (!selected) return;
  const name = textValue('#promptTemplateName') || '未命名模板';
  const content = textValue('#promptTemplateContent');
  if (!content) {
    setState(scope, '提示词内容不能为空', 'error');
    return;
  }
  if (selected.builtin) {
    const copy = {
      id: createPromptTemplateId(),
      name: name === selected.name ? `${name} 副本` : name,
      content,
      builtin: false,
    };
    group.templates.push(copy);
    state.selectedPromptTemplateId = copy.id;
  } else {
    selected.name = name;
    selected.content = content;
  }
  saveDraft();
  renderPromptTemplateDialog();
  setState(scope, '提示词模板已保存', 'done');
}

function addPromptTemplate() {
  const scope = state.activePromptScope;
  if (!scope) return;
  ensurePromptTemplates();
  const group = state.promptTemplates[scope];
  const template = {
    id: createPromptTemplateId(),
    name: `新模板 ${group.templates.length + 1}`,
    content: activePromptTemplate(scope)?.content || '',
    builtin: false,
  };
  group.templates.push(template);
  state.selectedPromptTemplateId = template.id;
  saveDraft();
  renderPromptTemplateDialog();
  $('#promptTemplateName')?.select();
}

function enableSelectedPromptTemplate() {
  const scope = state.activePromptScope;
  if (!scope) return;
  ensurePromptTemplates();
  const group = state.promptTemplates[scope];
  const selected = selectedPromptTemplate(scope);
  if (!selected) return;
  const name = textValue('#promptTemplateName') || selected.name || '未命名模板';
  const content = textValue('#promptTemplateContent');
  if (!content) {
    setState(scope, '提示词内容不能为空', 'error');
    return;
  }
  if (selected.builtin && (name !== selected.name || content !== selected.content)) {
    const copy = {
      id: createPromptTemplateId(),
      name: name === selected.name ? `${name} 副本` : name,
      content,
      builtin: false,
    };
    group.templates.push(copy);
    state.selectedPromptTemplateId = copy.id;
    group.activeId = copy.id;
  } else {
    if (!selected.builtin) {
      selected.name = name;
      selected.content = content;
    }
    group.activeId = selected.id;
  }
  saveDraft();
  renderPromptTemplateDialog();
  setState(scope, '提示词模板已启用', 'done');
}

function deleteSelectedPromptTemplate() {
  const scope = state.activePromptScope;
  if (!scope) return;
  ensurePromptTemplates();
  const group = state.promptTemplates[scope];
  const selected = selectedPromptTemplate(scope);
  if (!selected || selected.builtin || group.templates.length <= 1) return;
  group.templates = group.templates.filter((template) => template.id !== selected.id);
  if (group.activeId === selected.id) group.activeId = group.templates[0]?.id || 'default';
  state.selectedPromptTemplateId = group.activeId;
  saveDraft();
  renderPromptTemplateDialog();
  setState(scope, '提示词模板已删除', 'done');
}

function activePromptPayload(scope) {
  const template = activePromptTemplate(scope);
  const customPrompt = String(template?.content || '').trim();
  if (!customPrompt) return {};
  return {
    customPrompt,
    promptTemplateName: template.name || '',
  };
}

function captureFields() {
  const fields = {};
  FIELD_SELECTORS.forEach((selector) => {
    const node = $(selector);
    if (!node) return;
    if ((selector === '#totalWordCount' || selector === '#wordCount') && node.disabled) {
      fields[selector] = node.dataset.lastValue || readNodeText(node);
      return;
    }
    fields[selector] = readNodeText(node);
  });
  return fields;
}

function applyFields(fields = {}) {
  state.restoring = true;
  Object.entries(fields).forEach(([selector, value]) => {
    const node = $(selector);
    if (!node) return;
    writeNodeText(node, value);
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
    paperReferenceSnapshot: state.paperReferenceSnapshot,
    paperPushTargets: state.paperPushTargets,
    paperTemplate: state.paperTemplate,
    dataChartTargets: state.dataChartTargets,
    selectedDataChartTargetId: state.selectedDataChartTargetId,
    dataChartResult: state.dataChartResult,
    dataChartSearchResult: state.dataChartSearchResult,
    dataChartApproved: state.dataChartApproved,
    promptTemplates: state.promptTemplates,
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
  state.paperReferenceSnapshot = draft.paperReferenceSnapshot || '';
  state.paperPushTargets = draft.paperPushTargets || {};
  state.paperTemplate = draft.paperTemplate || null;
  state.dataChartTargets = Array.isArray(draft.dataChartTargets) ? draft.dataChartTargets : [];
  state.selectedDataChartTargetId = draft.selectedDataChartTargetId || '';
  state.dataChartResult = draft.dataChartResult || null;
  state.dataChartSearchResult = draft.dataChartSearchResult || null;
  state.dataChartApproved = Boolean(draft.dataChartApproved);
  state.promptTemplates = normalizePromptTemplates(draft.promptTemplates || {});
  applyFields(draft.fields);
  syncModeSelections();
  syncWordLimitControls();
  renderPaperTemplate();
  setDataChartTableText(textValue('#dataChartDataTable'));
  renderDataChartTargets();
  renderDataChartSourceList();
  renderDataChartResult();
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
    paperReferenceSnapshot: state.paperReferenceSnapshot,
    paperTemplate: state.paperTemplate,
    dataChartTargets: state.dataChartTargets,
    selectedDataChartTargetId: state.selectedDataChartTargetId,
    dataChartResult: state.dataChartResult,
    dataChartSearchResult: state.dataChartSearchResult,
    dataChartApproved: state.dataChartApproved,
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

function requestTimeoutMessage(timeoutMs) {
  const seconds = Math.max(1, Math.round(Number(timeoutMs || 0) / 1000));
  return `请求超过 ${seconds} 秒仍未返回。请检查网络状态，或到「配置管理」切换接口/模型后重试。`;
}

async function requestJson(url, options = {}, requestOptions = {}) {
  const timeoutMs = Number(requestOptions.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS);
  const timeoutMessage = requestOptions.timeoutMessage || requestTimeoutMessage(timeoutMs);
  const fetchOptions = { ...options };
  const controller = timeoutMs > 0 ? new AbortController() : null;
  let timeoutId = null;

  if (controller) {
    if (options.signal) {
      options.signal.addEventListener('abort', () => controller.abort(), { once: true });
    }
    fetchOptions.signal = controller.signal;
    timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  }

  try {
    const response = await fetch(url, fetchOptions);
    const raw = await response.text();
    let payload = {};
    try {
      payload = raw ? JSON.parse(raw) : {};
    } catch (error) {
      throw new Error(raw ? `服务返回内容不是有效 JSON：${raw.slice(0, 160)}` : '服务没有返回内容');
    }
    if (!response.ok || !payload.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
    return payload.data;
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error(timeoutMessage);
    throw error;
  } finally {
    if (timeoutId) window.clearTimeout(timeoutId);
  }
}

function isFetchConnectionError(error) {
  const message = String(error?.message || error || '').toLowerCase();
  return message.includes('failed to fetch') || message.includes('networkerror') || message.includes('load failed');
}

async function isLocalServerReachable() {
  try {
    await requestJson('/api/status', { method: 'GET' }, { timeoutMs: 5000 });
    return true;
  } catch (error) {
    return false;
  }
}

function startRunningStateTimer(scope, baseText) {
  const startedAt = Date.now();
  return window.setInterval(() => {
    const elapsed = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
    setState(scope, `${baseText}（已等待 ${elapsed} 秒，模型仍在处理）`, 'running');
  }, 15000);
}

async function withRunningState(scope, baseText, work) {
  setState(scope, baseText, 'running');
  const timer = startRunningStateTimer(scope, baseText);
  try {
    return await work();
  } finally {
    window.clearInterval(timer);
  }
}

function setPaperActionsDisabled(disabled, activeAction = '') {
  $$('[data-paper-action]').forEach((button) => {
    button.disabled = Boolean(disabled);
    button.dataset.running = disabled && button.dataset.paperAction === activeAction ? 'true' : 'false';
  });
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

function renderAnalysisSummary(analysis, scope = '') {
  if (!analysis) return '暂无分析数据。';
  if (analysis.ai && scope === 'ai') {
    const ai = analysis.ai;
    const score = Number(ai.score ?? 0);
    const features = ai.features || [];
    const flagged = ai.sentences_flagged || [];
    const currentProbability = estimateAiProbability(score);
    const currentRisk = ai.risk_level || '未完成核验';
    return [
      '检测对象：去痕处理结果',
      '差异基准：原文输入',
      `基准 AI 风险分：${state.lastBaseAiScore ?? '未提供基准'}`,
      `当前结果 AI 风险分：${score}/100`,
      `风险分变化值：${formatAiDelta(state.lastBaseAiScore, score)}`,
      `当前结果估算 AI 生成概率：${currentProbability}%`,
      `当前风险等级：${currentRisk}`,
      `去痕效果评估：${describeAiImprovement(state.lastBaseAiScore, score)}`,
      `原文字数与结果字数：${textValue('#aiInput').length || '未提供原文'} / ${textValue('#aiOutput').length || '未生成结果'}`,
      `保留 / 新增 / 删除字符数：${formatDiffCounts(textValue('#aiInput'), textValue('#aiOutput'))}`,
      `命中 AI 痕迹数量：${features.length}`,
      `重点句段数量：${flagged.length}`,
      `简短处理建议：${buildAiSummaryRecommendation(ai, state.lastBaseAiScore)}`,
    ].join('\n');
  }
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

function estimateAiProbability(score) {
  return Math.max(6, Math.min(98, Math.round(Number(score || 0) * 2.6)));
}

function formatAiDelta(baseScore, currentScore) {
  if (baseScore === undefined || baseScore === null || Number.isNaN(Number(baseScore))) return '未提供基准';
  const delta = Number(baseScore) - Number(currentScore || 0);
  if (delta > 0) return `下降 ${delta} 分`;
  if (delta < 0) return `上升 ${Math.abs(delta)} 分`;
  return '无变化';
}

function describeAiImprovement(baseScore, currentScore) {
  if (baseScore === undefined || baseScore === null || Number.isNaN(Number(baseScore))) return '已生成结果，但缺少基准对比。';
  const delta = Number(baseScore) - Number(currentScore || 0);
  if (delta >= 12) return '去痕效果明显';
  if (delta >= 5) return '去痕效果较好';
  if (delta >= 1) return '去痕效果有限';
  if (delta === 0) return '风险分无变化';
  return '风险分反而上升';
}

function buildAiSummaryRecommendation(ai, baseScore) {
  const score = Number(ai.score || 0);
  if (score >= 30) return '风险仍高，建议改用更高强度模式并重写重点句段。';
  if (baseScore !== undefined && baseScore !== null && score >= Number(baseScore)) {
    return '风险未下降，建议切换更高强度模式后重新处理。';
  }
  if (score >= 15) return '风险已有下降，建议继续人工打散模板化表达。';
  return '风险已降至可控范围，建议通读校正术语和引用。';
}

function formatDiffCounts(before, after) {
  const counts = calculateDiffCounts(before || '', after || '');
  return `${counts.equal} / ${counts.insert} / ${counts.delete}`;
}

function calculateDiffCounts(before, after) {
  const oldText = before || '';
  const newText = after || '';
  const oldChars = Array.from(oldText);
  const newChars = Array.from(newText);
  const oldCounts = new Map();
  const newCounts = new Map();
  oldChars.forEach((char) => oldCounts.set(char, (oldCounts.get(char) || 0) + 1));
  newChars.forEach((char) => newCounts.set(char, (newCounts.get(char) || 0) + 1));
  let equal = 0;
  new Set([...oldCounts.keys(), ...newCounts.keys()]).forEach((char) => {
    equal += Math.min(oldCounts.get(char) || 0, newCounts.get(char) || 0);
  });
  return {
    equal,
    insert: Math.max(0, newChars.length - equal),
    delete: Math.max(0, oldChars.length - equal),
  };
}

function diffText(before, after) {
  const oldText = before || '';
  const newText = after || '';
  if (!oldText || !newText) return '请先准备原文与处理结果。';
  if (oldText === newText) return '原文与结果暂未检测到差异。';
  return diffTextBySegments(oldText, newText);
}

function plainDiffText(before, after) {
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

function panelDiffConfig(scope) {
  return {
    ai: { input: '#aiInput', output: '#aiOutput', diff: '#aiDiff', status: '#aiDiffStatus' },
    plagiarism: { input: '#plagiarismInput', output: '#plagiarismOutput', diff: '#plagiarismDiff', status: '#plagiarismDiffStatus', editor: '#plagiarismOutputEditor' },
    polish: { input: '#polishInput', output: '#polishOutput', diff: '#polishDiff', status: '#polishDiffStatus', editor: '#polishOutputEditor' },
    correction: { input: '#correctionInput', output: '#correctionOutput', diff: '#correctionDiff', status: '#correctionDiffStatus' },
  }[scope] || null;
}

function refreshPanelDiff(scope) {
  const config = panelDiffConfig(scope);
  if (!config) return;
  syncResultEditorToOutput(scope);
  const diffNode = $(config.diff);
  if (!diffNode) return;
  const statusNode = $(config.status);
  if (statusNode) statusNode.textContent = '差异视图已刷新。';
  diffNode.innerHTML = diffText(textValue(config.input), textValue(config.output));
}

function editableResultConfig(scope) {
  const config = panelDiffConfig(scope);
  if (!config || !config.editor) return null;
  return {
    ...config,
    editButton: `[data-result-edit="${scope}"]`,
    doneButton: `[data-result-done="${scope}"]`,
  };
}

function syncResultOutputToEditor(scope) {
  const config = editableResultConfig(scope);
  if (!config) return;
  const editor = $(config.editor);
  if (!editor) return;
  const output = textValue(config.output);
  if (editor.value !== output) editor.value = output;
}

function syncResultEditorToOutput(scope) {
  const config = editableResultConfig(scope);
  if (!config) return;
  const editor = $(config.editor);
  if (!editor || editor.hidden) return;
  setText(config.output, readNodeText(editor));
}

function setResultEditMode(scope, editing) {
  const config = editableResultConfig(scope);
  if (!config) return;
  const editor = $(config.editor);
  const diffNode = $(config.diff);
  const editButton = $(config.editButton);
  const doneButton = $(config.doneButton);
  const statusNode = $(config.status);
  if (!editor || !diffNode) return;

  if (editing) {
    syncResultOutputToEditor(scope);
    editor.hidden = false;
    diffNode.hidden = true;
    if (editButton) editButton.hidden = true;
    if (doneButton) doneButton.hidden = false;
    if (statusNode) statusNode.textContent = '正在编辑处理结果；完成后会刷新差异预览。';
    editor.focus();
    return;
  }

  setText(config.output, readNodeText(editor));
  editor.hidden = true;
  diffNode.hidden = false;
  if (editButton) editButton.hidden = false;
  if (doneButton) doneButton.hidden = true;
  refreshPanelDiff(scope);
  saveDraft();
}

function resetResultEditMode(scope) {
  const config = editableResultConfig(scope);
  if (!config) return;
  const editor = $(config.editor);
  const diffNode = $(config.diff);
  const editButton = $(config.editButton);
  const doneButton = $(config.doneButton);
  if (editor) editor.hidden = true;
  if (diffNode) diffNode.hidden = false;
  if (editButton) editButton.hidden = false;
  if (doneButton) doneButton.hidden = true;
}

function diffTextBySegments(oldText, newText) {
  const oldSegments = splitDiffSegments(oldText);
  const newSegments = splitDiffSegments(newText);
  if (oldSegments.length * newSegments.length > 250000) {
    return diffTextByParagraphs(oldText, newText);
  }
  const chunks = diffSequence(oldSegments, newSegments);
  return renderSegmentDiffChunks(chunks);
}

function splitDiffSegments(text) {
  const parts = String(text || '').match(/[\s\S]*?(?:[。！？!?；;，,\n]+|$)/g) || [];
  return parts.filter((part) => part.length > 0);
}

function diffTextByParagraphs(oldText, newText) {
  const oldParagraphs = splitParagraphUnits(oldText);
  const newParagraphs = splitParagraphUnits(newText);
  const chunks = diffSequence(oldParagraphs, newParagraphs);
  return renderSegmentDiffChunks(chunks);
}

function splitParagraphUnits(text) {
  return String(text || '').split(/(\n{2,})/).filter((part) => part.length > 0);
}

function diffSequence(oldParts, newParts) {
  const width = newParts.length + 1;
  const maxCellValue = Math.max(oldParts.length, newParts.length);
  const TableType = maxCellValue > 65535 ? Uint32Array : Uint16Array;
  const table = new TableType((oldParts.length + 1) * width);
  for (let i = oldParts.length - 1; i >= 0; i -= 1) {
    for (let j = newParts.length - 1; j >= 0; j -= 1) {
      const offset = i * width + j;
      table[offset] = oldParts[i] === newParts[j]
        ? table[(i + 1) * width + j + 1] + 1
        : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const chunks = [];
  let i = 0;
  let j = 0;
  while (i < oldParts.length && j < newParts.length) {
    if (oldParts[i] === newParts[j]) {
      chunks.push({ type: 'equal', text: oldParts[i] });
      i += 1;
      j += 1;
    } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) {
      chunks.push({ type: 'delete', text: oldParts[i] });
      i += 1;
    } else {
      chunks.push({ type: 'insert', text: newParts[j] });
      j += 1;
    }
  }
  while (i < oldParts.length) {
    chunks.push({ type: 'delete', text: oldParts[i] });
    i += 1;
  }
  while (j < newParts.length) {
    chunks.push({ type: 'insert', text: newParts[j] });
    j += 1;
  }
  return chunks;
}

function renderSegmentDiffChunks(chunks) {
  const merged = mergeDiffChunks(chunks);
  const rendered = [];
  for (let index = 0; index < merged.length; index += 1) {
    const chunk = merged[index];
    const next = merged[index + 1];
    if (chunk.type === 'delete' && next?.type === 'insert') {
      rendered.push(renderChangedSegmentPair(chunk.text, next.text));
      index += 1;
    } else if (chunk.type === 'insert') {
      rendered.push(`<span class="diff-added">${escapeHtml(chunk.text)}</span>`);
    } else if (chunk.type === 'delete') {
      rendered.push(`<span class="diff-removed">${escapeHtml(chunk.text)}</span>`);
    } else {
      rendered.push(escapeHtml(chunk.text));
    }
  }
  return rendered.join('');
}

function renderChangedSegmentPair(removedText, addedText) {
  if ((removedText.length + addedText.length) <= 4500) {
    return diffTextByLcs(removedText, addedText);
  }
  return diffTextBySharedEdges(removedText, addedText);
}

function diffTextBySharedEdges(oldText, newText) {
  const oldParts = splitDiffUnits(oldText);
  const newParts = splitDiffUnits(newText);
  const commonPrefix = sharedPrefixLength(oldParts, newParts);
  const commonSuffix = sharedSuffixLength(oldParts, newParts, commonPrefix);
  const prefix = oldParts.slice(0, commonPrefix).join('');
  const suffix = oldParts.slice(oldParts.length - commonSuffix).join('');
  const removed = oldParts.slice(commonPrefix, oldParts.length - commonSuffix).join('');
  const added = newParts.slice(commonPrefix, newParts.length - commonSuffix).join('');
  return [
    escapeHtml(prefix),
    removed ? `<span class="diff-removed">${escapeHtml(removed)}</span>` : '',
    added ? `<span class="diff-added">${escapeHtml(added)}</span>` : '',
    escapeHtml(suffix),
  ].join('');
}

function diffTextByLcs(oldText, newText) {
  const oldParts = splitDiffUnits(oldText);
  const newParts = splitDiffUnits(newText);
  const width = newParts.length + 1;
  const table = new Uint16Array((oldParts.length + 1) * width);
  for (let i = oldParts.length - 1; i >= 0; i -= 1) {
    for (let j = newParts.length - 1; j >= 0; j -= 1) {
      const offset = i * width + j;
      table[offset] = oldParts[i] === newParts[j]
        ? table[(i + 1) * width + j + 1] + 1
        : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const chunks = [];
  let i = 0;
  let j = 0;
  while (i < oldParts.length && j < newParts.length) {
    if (oldParts[i] === newParts[j]) {
      chunks.push({ type: 'equal', text: oldParts[i] });
      i += 1;
      j += 1;
    } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) {
      chunks.push({ type: 'delete', text: oldParts[i] });
      i += 1;
    } else {
      chunks.push({ type: 'insert', text: newParts[j] });
      j += 1;
    }
  }
  while (i < oldParts.length) {
    chunks.push({ type: 'delete', text: oldParts[i] });
    i += 1;
  }
  while (j < newParts.length) {
    chunks.push({ type: 'insert', text: newParts[j] });
    j += 1;
  }
  return renderDiffChunks(chunks);
}

function renderDiffChunks(chunks) {
  const merged = mergeDiffChunks(chunks);
  return merged.map((chunk) => {
    if (chunk.type === 'delete') return `<span class="diff-removed">${escapeHtml(chunk.text)}</span>`;
    if (chunk.type === 'insert') return `<span class="diff-added">${escapeHtml(chunk.text)}</span>`;
    return escapeHtml(chunk.text);
  }).join('');
}

function mergeDiffChunks(chunks) {
  const merged = [];
  chunks.forEach((chunk) => {
    const last = merged[merged.length - 1];
    if (last && last.type === chunk.type) last.text += chunk.text;
    else merged.push({ ...chunk });
  });
  return merged;
}

function splitDiffUnits(text) {
  return Array.from(String(text || ''));
}

function sharedPrefixLength(left, right) {
  const max = Math.min(left.length, right.length);
  let index = 0;
  while (index < max && left[index] === right[index]) {
    index += 1;
  }
  return index;
}

function sharedSuffixLength(left, right, prefixLength) {
  const max = Math.min(left.length, right.length) - prefixLength;
  let index = 0;
  while (index < max && left[left.length - 1 - index] === right[right.length - 1 - index]) {
    index += 1;
  }
  return index;
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

function sectionDisplayText(section) {
  return [section?.title, section?.raw, section?.parent].filter(Boolean).join(' ');
}

function sectionSearchKey(section) {
  return sectionDisplayText(section).replace(/\s+/g, '').toLowerCase();
}

function outlineSectionNumbers(sections) {
  const counters = [];
  return sections.map((section) => {
    const kind = paperSpecialKind(section?.title);
    if (['cn_abstract', 'en_abstract', 'intro', 'reference'].includes(kind)) return '';
    const level = Math.min(Math.max(Number(section?.level || 1), 1), 4);
    for (let index = 0; index < level - 1; index += 1) {
      if (!counters[index]) counters[index] = 1;
    }
    counters[level - 1] = (counters[level - 1] || 0) + 1;
    counters.length = level;
    return counters.slice(0, level).join('.');
  });
}

function formatOutlineSectionNumber(number) {
  const value = String(number || '').trim();
  if (!value) return '';
  return value.includes('.') ? value : `${value}.`;
}

function renderPaperSectionJump(sections) {
  const jump = $('#paperSectionJump');
  if (!jump) return;
  const selectedTitle = state.selectedPaperSection || state.paperEditorSection || '';
  const numbers = outlineSectionNumbers(sections);
  jump.innerHTML = [
    '<option value="">快速跳转章节</option>',
    ...sections.map((section, index) => {
      const level = Math.min(Math.max(Number(section.level || 1), 1), 4);
      const indent = '\u00a0'.repeat(Math.max(0, level - 1) * 3);
      const number = formatOutlineSectionNumber(numbers[index] ?? String(index + 1));
      const label = number ? `${indent}${number} ${section.title}` : `${indent}${section.title}`;
      return `<option value="${escapeHtml(section.title)}" ${section.title === selectedTitle ? 'selected' : ''}>${escapeHtml(label)}</option>`;
    }),
  ].join('');
}

function sectionNumberLabel(section, index, numbers = outlineSectionNumbers(state.paperSections || [])) {
  const number = formatOutlineSectionNumber(numbers[index] ?? String(index + 1));
  return number ? `${number} ${section.title}` : section.title;
}

function paperSectionNumberedTitle(title) {
  const sections = state.paperSections || [];
  const index = sections.findIndex((section) => section.title === title);
  if (index < 0) return title;
  return sectionNumberLabel(sections[index], index, outlineSectionNumbers(sections));
}

function paperSectionHasChildren(title) {
  return (state.paperSections || []).some((section) => section.parent === title);
}

function isPaperSectionWritable(title) {
  if (!title) return false;
  return !paperSectionHasChildren(title);
}

function firstWritableDescendantTitle(title) {
  const children = (state.paperSections || []).filter((section) => section.parent === title);
  for (const child of children) {
    if (isPaperSectionWritable(child.title)) return child.title;
    const nested = firstWritableDescendantTitle(child.title);
    if (nested) return nested;
  }
  return '';
}

function nearestWritablePaperSection(title) {
  if (isPaperSectionWritable(title)) return title;
  return firstWritableDescendantTitle(title);
}

function descendantPaperSectionTitles(title) {
  const result = [];
  const visit = (parentTitle) => {
    (state.paperSections || []).forEach((section) => {
      if (section.parent !== parentTitle) return;
      result.push(section.title);
      visit(section.title);
    });
  };
  visit(title);
  return result;
}

function descendantPaperSectionIndexes(parentIndex) {
  const sections = state.paperSections || [];
  const parent = sections[Number(parentIndex)];
  if (!parent) return [];
  const result = [];
  const visit = (parentTitle) => {
    sections.forEach((section, index) => {
      if (section.parent !== parentTitle) return;
      result.push(index);
      visit(section.title);
    });
  };
  visit(parent.title);
  return result;
}

function pushScopeSectionTitles(scopeValue) {
  const value = String(scopeValue || 'all');
  if (value === 'all') {
    return (state.paperSections || [])
      .map((section) => section.title)
      .filter((title) => isPaperSectionWritable(title) && paperSpecialKind(title) !== 'reference');
  }
  if (!value.startsWith('section-index:')) return [];
  const sections = state.paperSections || [];
  const sectionIndex = Number(value.slice('section-index:'.length));
  const section = sections[sectionIndex];
  if (!section) return [];
  const indexes = isPaperSectionWritable(section.title)
    ? [sectionIndex]
    : descendantPaperSectionIndexes(sectionIndex);
  return indexes
    .map((index) => sections[index]?.title || '')
    .filter((title) => title && isPaperSectionWritable(title) && paperSpecialKind(title) !== 'reference');
}

function paperSectionTextBlock(title) {
  const body = paperSectionBodyContent(title);
  if (!body) return '';
  return `${paperSectionNumberedTitle(title)}\n${body}`;
}

function collectPaperPushText(scopeValue) {
  storeCurrentPaperEditor({ skipEmptyOverwrite: true });
  const titles = pushScopeSectionTitles(scopeValue);
  return titles.map(paperSectionTextBlock).filter(Boolean).join('\n\n').trim();
}

function collectPaperPushPayload(scopeValue) {
  storeCurrentPaperEditor({ skipEmptyOverwrite: true });
  const titles = pushScopeSectionTitles(scopeValue);
  return {
    scope: String(scopeValue || 'all'),
    titles,
    text: titles.map(paperSectionTextBlock).filter(Boolean).join('\n\n').trim(),
  };
}

function collectPaperSectionsPayload(includeReference = false) {
  storeCurrentPaperEditor({ skipEmptyOverwrite: true });
  return (state.paperSections || [])
    .map((section) => ({
      title: section.title,
      content: state.paperSectionContents?.[section.title] || '',
    }))
    .filter((section) => section.content && (includeReference || paperSpecialKind(section.title) !== 'reference'));
}

function collectFullPaperText(includeReference = false) {
  const sections = collectPaperSectionsPayload(includeReference);
  return sections.map((section) => `${section.title}\n${section.content}`).join('\n\n').trim();
}

function renderPaperPushScopes() {
  const select = $('#paperPushScope');
  if (!select) return;
  const currentValue = select.value || 'all';
  const sections = state.paperSections || [];
  const numbers = outlineSectionNumbers(sections);
  const options = ['<option value="all">整篇文章</option>'];
  sections.forEach((section, index) => {
    if (paperSpecialKind(section.title) === 'reference') return;
    const level = Math.min(Math.max(Number(section.level || 1), 1), 4);
    const indent = '\u00a0'.repeat(Math.max(0, level - 1) * 3);
    const label = `${indent}${sectionNumberLabel(section, index, numbers)}`;
    options.push(`<option value="section-index:${index}">${escapeHtml(label)}</option>`);
  });
  select.innerHTML = options.join('');
  if (Array.from(select.options).some((option) => option.value === currentValue)) {
    select.value = currentValue;
  }
}

function paperPushTargetConfig(target) {
  return {
    datachart: { page: 'datachart', input: '#dataChartFullText', output: '#dataChartResultText', stateScope: 'datachart', label: '数据图表' },
    ai: { page: 'ai', input: '#aiInput', output: '#aiOutput', stateScope: 'ai', label: '降 AI 检测' },
    plagiarism: { page: 'plagiarism', input: '#plagiarismInput', output: '#plagiarismOutput', stateScope: 'plagiarism', label: '降查重率' },
    polish: { page: 'polish', input: '#polishInput', output: '#polishOutput', stateScope: 'polish', label: '学术润色' },
    correction: { page: 'correction', input: '#correctionInput', output: '#correctionOutput', stateScope: 'correction', label: '智能纠错' },
  }[target] || null;
}

function pushPaperSelectionToWorkspace() {
  const scope = $('#paperPushScope')?.value || 'all';
  const target = $('#paperPushTarget')?.value || 'ai';
  const config = paperPushTargetConfig(target);
  if (!config) return;
  const payload = collectPaperPushPayload(scope);
  if (!payload.text) {
    setState('paper', '所选范围没有可推送的正文内容。', 'error');
    return;
  }
  setText(config.input, payload.text);
  setText(config.output, '');
  resetPushedTargetResults(target);
  state.paperPushTargets[target] = {
    scope: payload.scope,
    titles: payload.titles,
    pushedAt: new Date().toISOString(),
  };
  setState('paper', `已推送到${config.label}`, 'done');
  setState(config.stateScope, '已接收论文内容');
  setPage(config.page);
  saveDraft();
}

function resetPushedTargetResults(target) {
  ({
    datachart: resetDataChartResultsForNewInput,
    ai: resetAiResultsForNewInput,
    plagiarism: resetPlagiarismResultsForNewInput,
    polish: resetPolishResultsForNewInput,
    correction: resetCorrectionResultsForNewInput,
  }[target] || (() => {}))();
}

function resetDataChartResultsForNewInput() {
  state.dataChartTargets = [];
  state.selectedDataChartTargetId = '';
  state.dataChartResult = null;
  state.dataChartSearchResult = null;
  state.dataChartApproved = false;
  setText('#dataChartQuery', '');
  setDataChartTableText('');
  renderDataChartSourceList();
  setText('#dataChartResultText', '');
  resetDataChartDiff();
  renderDataChartTargets();
  renderDataChartResult();
  updateDataChartStep(1);
}

function resetAiResultsForNewInput() {
  setText('#aiOutput', '');
  setText('#aiReview', '完成去痕效果复核后，此处将展示 AI 生成概率、去痕效果评估与核心问题汇总。');
  setText('#aiDiff', '差异预览会显示在这里。');
  const status = $('#aiDiffStatus');
  if (status) status.textContent = '点击“刷新差异”，即可查看原文与处理结果的逐句差异对比。';
}

function resetPlagiarismResultsForNewInput() {
  setText('#plagiarismOutput', '');
  setText('#plagiarismOutputEditor', '');
  setText('#plagiarismSource', '');
  setText('#plagiarismReview', '');
  setText('#plagiarismDiff', '降重处理结果会显示在这里。');
  resetResultEditMode('plagiarism');
  const status = $('#plagiarismDiffStatus');
  if (status) status.textContent = '红色为删除，绿色为新增。降重完成后，这里会直接显示处理差异。';
}

function resetPolishResultsForNewInput() {
  setText('#polishOutput', '');
  setText('#polishOutputEditor', '');
  setText('#polishReview', '');
  setText('#polishDiff', '润色处理结果会显示在这里。');
  resetResultEditMode('polish');
  const status = $('#polishDiffStatus');
  if (status) status.textContent = '红色为删除，绿色为新增。润色完成后，这里会直接显示处理差异。';
}

function resetCorrectionResultsForNewInput() {
  setText('#correctionOutput', '');
  const stats = $('#correctionStats');
  if (stats) stats.textContent = '';
  const issues = $('#correctionIssues');
  if (issues) issues.textContent = '运行智能纠错后，这里会列出问题。';
  const report = $('#correctionReport');
  if (report) report.textContent = '请选择问题或运行智能纠错查看详情。';
  const diff = $('#correctionDiff');
  if (diff) diff.textContent = '修正预览会显示在这里。';
  const status = $('#correctionDiffStatus');
  if (status) status.textContent = '红色为删除，绿色为新增。运行智能纠错后，这里会直接显示修正差异。';
  state.currentCorrectionIssues = [];
  state.currentCorrectionLabels = {};
  state.selectedCorrectionIssueId = '';
  syncCorrectionAutoFixButton();
}

function paperBackfillHeadingTitle(line, titles) {
  const text = stripOutlineEmphasis(String(line || '').trim());
  if (!text || text.length > 180) return '';
  const parsed = analyzeOutlineHeading(text);
  const candidates = parsed
    ? [parsed.title, parsed.raw, normalizeOutlineTitle(parsed.raw)]
    : [text, normalizeOutlineTitle(text)];
  return titles.find((title) => {
    const keys = paperSectionTitleKeys(title);
    return candidates.some((candidate) => keys.has(paperTitleMatchKey(candidate)));
  }) || '';
}

function splitPaperBackfillSections(text, titles) {
  const result = {};
  const validTitles = new Set(titles);
  let currentTitle = '';
  let currentLines = [];
  const saveCurrent = () => {
    if (!currentTitle || !validTitles.has(currentTitle)) return;
    const body = currentLines.join('\n').replace(/^\n+|\n+$/g, '');
    if (body.trim()) result[currentTitle] = body;
  };

  String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').forEach((line) => {
    const matchedTitle = paperBackfillHeadingTitle(line, titles);
    if (matchedTitle) {
      saveCurrent();
      currentTitle = matchedTitle;
      currentLines = [];
      return;
    }
    if (currentTitle) currentLines.push(line);
  });
  saveCurrent();
  return result;
}

function backfillProcessedPanel(target) {
  if (target === 'datachart') {
    backfillDataChartResult();
    return;
  }
  const config = paperPushTargetConfig(target);
  if (!config) return;
  const output = textValue(config.output);
  if (!output) {
    setState(config.stateScope, '请先生成处理结果再回填。', 'error');
    return;
  }

  const pushed = state.paperPushTargets?.[target];
  const titles = (pushed?.titles || []).filter((title) => state.paperSectionContents && title in state.paperSectionContents);
  if (!titles.length) {
    setState(config.stateScope, '没有找到这次推送对应的论文章节，请先从论文写作页推送章节。', 'error');
    return;
  }

  const updates = {};
  if (titles.length === 1) {
    updates[titles[0]] = output;
  } else {
    Object.assign(updates, splitPaperBackfillSections(output, titles));
  }

  const updatedTitles = titles.filter((title) => String(updates[title] || '').trim());
  if (!updatedTitles.length) {
    setState(config.stateScope, '没有从结果中识别到对应章节标题，无法自动拆分回填。', 'error');
    return;
  }

  updatedTitles.forEach((title) => {
    writePaperContentToSection(title, updates[title], { loadEditor: false });
  });
  loadPaperSection(updatedTitles[0]);
  renderPaperSections();
  setState(config.stateScope, `已回填 ${updatedTitles.length} 个章节`, 'done');
  setState('paper', `已从${config.label}回填 ${updatedTitles.length} 个章节`, 'done');
  setPage('paper');
  saveDraft();
}

function renderPaperSections() {
  const list = $('#paperSectionList');
  if (!list) return;
  const sections = state.paperSections;
  const count = $('#paperSectionCount');
  if (count) count.textContent = sections.length ? `${sections.length} 节` : '0 节';
  renderPaperSectionJump(sections);
  renderPaperPushScopes();
  if (!sections.length) {
    list.textContent = '暂未解析到章节。建议大纲使用“第一章 绪论”或“1.1 研究背景”这样的标题格式。';
    return;
  }
  const numbers = outlineSectionNumbers(sections);
  const filter = String(state.paperSectionFilter || '').replace(/\s+/g, '').toLowerCase();
  const visibleSections = filter
    ? sections.map((section, index) => ({ section, index })).filter(({ section }) => sectionSearchKey(section).includes(filter))
    : sections.map((section, index) => ({ section, index }));
  if (!visibleSections.length) {
    list.innerHTML = '<div class="outline-section-empty">没有匹配的章节。</div>';
    return;
  }
  list.innerHTML = visibleSections.map(({ section, index }) => {
    const level = Math.min(Math.max(Number(section.level || 1), 1), 4);
    const kind = paperSpecialKind(section.title) || 'body';
    const writable = isPaperSectionWritable(section.title);
    const hasContent = isPaperSectionWritten(section.title);
    const number = formatOutlineSectionNumber(numbers[index] ?? String(index + 1));
    const titleText = number ? `<em>${escapeHtml(number)}</em>${escapeHtml(section.title)}` : escapeHtml(section.title);
    return `
    <button class="outline-section-item ${section.title === state.selectedPaperSection ? 'selected' : ''}" data-section-index="${index}" data-level="${level}" data-kind="${escapeHtml(kind)}" data-writable="${writable ? 'true' : 'false'}" type="button">
      <span class="outline-section-main">
        <strong>${titleText}</strong>
        ${writable && hasContent ? '<small>已写</small>' : ''}
        ${!writable ? '<small class="outline-section-label-muted">标题</small>' : ''}
      </span>
    </button>
  `;
  }).join('');
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

function updatePaperReferenceSnapshot(title, content) {
  if (paperSpecialKind(title) !== 'reference') return;
  state.paperReferenceSnapshot = String(content || '').trim();
}

function hydratePaperReferenceSnapshotFromSections() {
  for (const section of state.paperSections || []) {
    if (paperSpecialKind(section.title) !== 'reference') continue;
    updatePaperReferenceSnapshot(section.title, state.paperSectionContents?.[section.title] || '');
    return;
  }
  state.paperReferenceSnapshot = '';
}

function storeCurrentPaperEditor(options = {}) {
  const title = state.paperEditorSection || textValue('#sectionTitle');
  if (!title || !state.paperSectionContents || !(title in state.paperSectionContents)) return;
  if (!isPaperSectionWritable(title)) return;
  const content = getCurrentPaperContent();
  if (options.skipEmptyOverwrite && !content && state.paperSectionContents[title]) return;
  state.paperSectionContents[title] = content;
  updatePaperReferenceSnapshot(title, content);
  if (state.restoring) return;
  if (options.markUser !== false) state.paperSectionContentSources[title] = 'user';
  state.paperEditorDirty = false;
}

function loadPaperSection(title) {
  const content = state.paperSectionContents[title] || '';
  state.paperEditorSection = title;
  state.selectedPaperSection = title;
  updatePaperEditorFormatMode(title);
  setText('#sectionTitle', title);
  setText('#paperContext', content);
  setText('#paperResult', content);
  const editor = $('#paperContext');
  if (editor) {
    const writable = isPaperSectionWritable(title);
    editor.contentEditable = writable ? 'true' : 'false';
    editor.classList.toggle('chapter-editor-locked', !writable);
    editor.dataset.placeholder = writable
      ? '这里对应桌面端右侧正文编辑区，可放已有章节上下文、全文正文或参考文献。生成结果也会直接写回这里。'
      : '这是章节标题节点，请选择下面的具体小节后再撰写正文。';
  }
  state.paperEditorDirty = false;
}

function selectPaperSection(title, options = {}) {
  if (!title) return;
  const targetTitle = options.allowTitleNode ? title : (nearestWritablePaperSection(title) || title);
  if (options.storeCurrent !== false) storeCurrentPaperEditor();
  loadPaperSection(targetTitle);
  renderPaperSections();
  saveDraft();
}

function findPaperSectionByKind(kind) {
  return (state.paperSections || []).find((section) => paperSpecialKind(section.title) === kind)?.title || '';
}

function findPaperSectionByTitle(title) {
  return (state.paperSections || []).find((section) => section.title === title);
}

function paperSectionContentByTitle(title) {
  const key = String(title || '').trim();
  if (!key) return '';
  if (Object.prototype.hasOwnProperty.call(state.paperSectionContents || {}, key)) {
    return state.paperSectionContents[key] || '';
  }
  const matchKey = paperTitleMatchKey(key);
  const section = (state.paperSections || []).find((item) => paperTitleMatchKey(item.title) === matchKey);
  return section ? (state.paperSectionContents[section.title] || '') : '';
}

function appendPaperReferences(refTitle, appendContent, options = {}) {
  const extra = String(appendContent || '').trim();
  if (!extra) return refTitle;
  const targetTitle = ensurePaperSection(refTitle, paperSpecialKind(refTitle) || 'reference') || refTitle;
  const existingContent = String(paperSectionContentByTitle(targetTitle) || '').trim();
  const newContent = existingContent ? `${existingContent}\n${extra}` : extra;
  return writePaperContentToSection(targetTitle, newContent, options) || targetTitle;
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

function looksLikeOutlineSectionAnalysis(title, content, source = '') {
  const text = String(content || '').trim();
  if (!text) return false;
  if (source === 'outline') return true;
  if (source === 'user') return false;
  if (paperSpecialKind(title)) return false;
  if (text.length > 360) return false;
  if (/\[\d+(?:[-,，、]\d+)*\]/.test(text)) return false;
  if (text.split(/\r?\n/).filter((line) => line.trim()).length > 2) return false;
  const hints = [
    '章节分析', '章节说明', '本章主要', '本节主要', '本章重点', '本节重点',
    '本章将', '本节将', '主要阐述', '主要分析', '主要讨论', '围绕',
    '用于说明', '写作要点', '写作思路', '可从', '需要从',
  ];
  return hints.some((hint) => text.includes(hint));
}

function paperSectionBodyContent(title) {
  const content = String(state.paperSectionContents?.[title] || '').trim();
  if (!content) return '';
  const source = state.paperSectionContentSources?.[title] || '';
  if (looksLikeOutlineSectionAnalysis(title, content, source)) return '';
  return content;
}

function isPaperSectionWritten(title) {
  return Boolean(paperSectionBodyContent(title));
}

function refreshPaperSections(selectFirst = false, options = {}) {
  const preserveExisting = options.preserveExisting !== false;
  if (!preserveExisting) state.paperReferenceSnapshot = '';
  if (preserveExisting) {
    storeCurrentPaperEditor({ skipEmptyOverwrite: true, markUser: state.paperEditorDirty });
  }
  const previousContents = preserveExisting ? { ...(state.paperSectionContents || {}) } : {};
  const previousSources = preserveExisting ? { ...(state.paperSectionContentSources || {}) } : {};
  const previousEditor = preserveExisting ? state.paperEditorSection : '';
  const requestedTitle = textValue('#sectionTitle') || state.selectedPaperSection || previousEditor;
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
  hydratePaperReferenceSnapshotFromSections();
  const requestedKey = requestedTitle ? paperTitleMatchKey(requestedTitle) : '';
  const requestedMatch = requestedKey
    ? state.paperSections.find((section) => paperTitleMatchKey(section.title) === requestedKey || paperTitleMatchKey(section.raw) === requestedKey)?.title
    : '';
  const nextTitle = requestedMatch
    || (previousEditor && state.paperSectionContents[previousEditor] !== undefined ? previousEditor : '')
    || (selectFirst ? state.paperSections[0]?.title : '');
  if (nextTitle && state.paperSectionContents[nextTitle] !== undefined) {
    loadPaperSection(nextTitle);
  } else if (!state.paperSections.length) {
    state.paperEditorSection = '';
    state.selectedPaperSection = '';
    setText('#sectionTitle', '');
    setText('#paperContext', '');
    setText('#paperResult', '');
  } else {
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
  const requestedTitle = textValue('#sectionTitle') || state.selectedPaperSection || state.paperEditorSection;
  if (action === 'section' && findPaperSectionByTitle(requestedTitle)) return requestedTitle;
  const targetTitle = nearestWritablePaperSection(requestedTitle) || requestedTitle;
  return ensurePaperSection(targetTitle);
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

function findPaperSectionTitleLike(title) {
  const key = paperTitleMatchKey(title);
  if (!key) return '';
  const parsed = analyzeOutlineHeading(title);
  const candidates = new Set([key, paperTitleMatchKey(normalizeOutlineTitle(title))]);
  if (parsed) {
    candidates.add(paperTitleMatchKey(parsed.title));
    candidates.add(paperTitleMatchKey(parsed.raw));
    candidates.add(paperTitleMatchKey(normalizeOutlineTitle(parsed.raw)));
  }
  for (const section of state.paperSections || []) {
    const sectionKeys = [
      section.title,
      section.raw,
      normalizeOutlineTitle(section.title),
      normalizeOutlineTitle(section.raw),
      analyzeOutlineHeading(section.raw)?.title,
    ].map(paperTitleMatchKey).filter(Boolean);
    if (sectionKeys.some((item) => candidates.has(item))) return section.title;
  }
  return '';
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
  const target = findPaperSectionTitleLike(title) || ensurePaperSection(title, paperSpecialKind(title));
  if (!target) return;
  const normalizedContent = normalizeGeneratedSectionBody(target, content);
  state.paperSectionContents[target] = normalizedContent;
  updatePaperReferenceSnapshot(target, normalizedContent);
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

function replacePaperSectionParagraph(sectionTitle, originalText, replacementText) {
  const target = findPaperSectionTitleLike(sectionTitle) || ensurePaperSection(sectionTitle);
  if (!target) return { ok: false, message: '没有找到可回填的章节' };
  const existing = state.paperSectionContents?.[target] || '';
  const original = String(originalText || '').trim();
  const replacement = String(replacementText || '').trim();
  if (!replacement) return { ok: false, message: '没有可回填的处理结果' };
  let next = '';
  if (original && existing.includes(original)) {
    next = existing.replace(original, replacement);
  } else if (existing.includes(replacement)) {
    return { ok: true, title: target, alreadyFilled: true };
  } else if (existing) {
    next = `${existing}\n\n${replacement}`;
  } else {
    next = replacement;
  }
  writePaperContentToSection(target, next);
  return { ok: true, title: target };
}

function applyDataChartReferenceResult(result) {
  if (!result) return '';
  const references = result.references;
  if (references?.mode === 'reorder' && references.content) {
    const targetRefTitle = ensurePaperSection(references.title || '参考文献', paperSpecialKind(references.title) || 'reference');
    const refTitle = writePaperContentToSection(targetRefTitle || references.title || '参考文献', references.content, { loadEditor: false });
    (result.updatedSections || []).forEach((section) => {
      if (section?.title) writePaperContentToSection(section.title, section.content || '', { loadEditor: false });
    });
    syncPaperOutlineFromSections();
    return refTitle || references.title || '';
  }
  if (references?.mode === 'append' && references.append) {
    return appendPaperReferences(references.title || '# 参考文献', references.append, { loadEditor: false });
  }
  return '';
}

function updateDataChartStep(step) {
  const current = Number(step || 1);
  $$('[data-data-step]').forEach((card) => {
    const value = Number(card.dataset.dataStep || 0);
    card.classList.toggle('active', value === current);
    card.classList.toggle('done', value < current);
  });
}

function currentDataChartTarget() {
  return (state.dataChartTargets || []).find((target) => target.id === state.selectedDataChartTargetId) || null;
}

function defaultDataChartTitle(target = currentDataChartTarget()) {
  if (!target) return '论文数据图表';
  const title = String(target.chartTitle || target.title || '').trim();
  if (title) return title;
  const need = String(target.dataNeed || '').replace(/^补充/, '').replace(/数据$/, '').trim();
  return need ? `${need}图` : '论文数据图表';
}

function renderDataChartTargets() {
  const list = $('#dataChartTargets');
  if (!list) return;
  const targets = state.dataChartTargets || [];
  const count = $('#dataChartTargetCount');
  if (count) count.textContent = `${targets.length} 项`;
  if (!targets.length) {
    list.textContent = '点击“AI 阅读全文”后，这里会列出适合插入数据图表的位置。';
    return;
  }
  if (!targets.some((target) => target.id === state.selectedDataChartTargetId)) {
    state.selectedDataChartTargetId = targets[0].id;
  }
  list.innerHTML = targets.map((target, index) => `
    <button class="data-target-card ${target.id === state.selectedDataChartTargetId ? 'selected' : ''}" data-data-target-id="${escapeHtml(target.id)}" type="button">
      <span class="data-target-meta">${index + 1}. ${escapeHtml(target.sectionTitle || '未命名章节')} · ${escapeHtml(target.dataNeed || '数据需求')}</span>
      <strong>${escapeHtml(target.reason || '建议补充数据图表')}</strong>
      <span>${escapeHtml(target.excerpt || '')}</span>
      <small>${escapeHtml(target.query || '')}</small>
    </button>
  `).join('');
  $$('.data-target-card').forEach((button) => {
    button.addEventListener('click', () => selectDataChartTarget(button.dataset.dataTargetId));
  });
}

function selectDataChartTarget(targetId) {
  const target = (state.dataChartTargets || []).find((item) => item.id === targetId);
  if (!target) return;
  const previousTargetId = state.selectedDataChartTargetId;
  state.selectedDataChartTargetId = target.id;
  setText('#dataChartQuery', target.query || '');
  const type = $('#dataChartType');
  if (type && target.chartType) type.value = target.chartType;
  setText('#dataChartTitle', defaultDataChartTitle(target));
  state.dataChartApproved = false;
  if (previousTargetId && previousTargetId !== target.id) {
    state.dataChartResult = null;
    state.dataChartSearchResult = null;
    setText('#dataChartResultText', '');
    setDataChartTableText('');
    renderDataChartSourceList();
    resetDataChartDiff();
  }
  renderDataChartTargets();
  renderDataChartResult();
  setState('datachart', `已选择：${target.sectionTitle || '候选段落'}`);
  saveDraft();
}

function dataChartFigureMarkdown(result = state.dataChartResult) {
  return String(result?.figureMarkdown || '').trim();
}

function dataChartComposedBackfillText(result = state.dataChartResult) {
  const replacement = String(result?.replacementText || '').trim();
  const figure = dataChartFigureMarkdown(result);
  if (!replacement) return figure;
  if (!figure) return replacement;
  const paragraphs = replacement.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  const figureIndex = paragraphs.findIndex((part) => /图\s*1|图一|如图|见图|figure\s*1/i.test(part));
  if (figureIndex >= 0) {
    paragraphs.splice(figureIndex + 1, 0, figure);
    return paragraphs.join('\n\n').trim();
  }
  return [replacement, figure].filter(Boolean).join('\n\n').trim();
}

function dataChartBackfillText() {
  return textValue('#dataChartResultText');
}

function dataChartReplacementText(result = state.dataChartResult) {
  const stored = textValue('#dataChartResultText');
  if (stored) return stored;
  return dataChartComposedBackfillText(result);
}

function dataChartTextWithoutFigures(text) {
  return String(text || '')
    .split(/\r?\n/)
    .filter((line) => !parseMarkdownImageLine(line))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function dataChartFigureParts(result = state.dataChartResult) {
  const figure = dataChartFigureMarkdown(result);
  if (!figure) return { image: null, caption: '' };
  const lines = figure.split(/\r?\n/);
  const imageLine = lines.find((line) => parseMarkdownImageLine(line));
  return {
    image: parseMarkdownImageLine(imageLine || ''),
    caption: lines.filter((line) => line.trim() && line !== imageLine).join('\n').trim(),
  };
}

function splitDataChartTextAroundFigure(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  const imageIndex = lines.findIndex((line) => parseMarkdownImageLine(line));
  if (imageIndex < 0) return { found: false, before: String(text || '').trim(), after: '' };
  let afterStart = imageIndex + 1;
  while (afterStart < lines.length && !lines[afterStart].trim()) afterStart += 1;
  if (/^\s*(?:图\s*\d+|Figure\s*\d+)/i.test(lines[afterStart] || '')) afterStart += 1;
  while (afterStart < lines.length && !lines[afterStart].trim()) afterStart += 1;
  return {
    found: true,
    before: lines.slice(0, imageIndex).join('\n').trim(),
    after: lines.slice(afterStart).join('\n').trim(),
  };
}

function dataChartOriginalTextForDiff() {
  const target = currentDataChartTarget();
  if (!target) return '';
  return String(target.originalText || target.excerpt || '').trim();
}

function resetDataChartDiff() {
  const status = $('#dataChartDiffStatus');
  if (status) status.textContent = '红色为删除，绿色为新增。图表会直接显示在差异区域内。';
  setText('#dataChartDiff', '差异预览会显示在这里。');
}

function refreshDataChartDiff() {
  const diffNode = $('#dataChartDiff');
  if (!diffNode) return;
  const before = dataChartOriginalTextForDiff();
  const after = dataChartReplacementText();
  const status = $('#dataChartDiffStatus');
  if (status) {
    if (before && after) status.textContent = '差异视图已刷新。红色为删除，绿色为新增；图表已嵌入下方。';
    else if (before) status.textContent = '已选中原候选段落，生成图表后会显示差异。';
    else status.textContent = '请先定位并选择候选段落。';
  }
  const figure = dataChartFigureParts();
  const figureHtml = figure.image?.src
    ? `<figure class="data-chart-diff-figure"><img src="${escapeHtml(figure.image.src)}" alt="${escapeHtml(figure.image.alt || '数据图表')}" /><figcaption>${escapeHtml(figure.caption || figure.image.alt || '')}</figcaption></figure>`
    : '';
  if (figureHtml) {
    const split = splitDataChartTextAroundFigure(after);
    if (split.found) {
      const tailHtml = split.after ? diffText('', split.after) : '';
      diffNode.innerHTML = `${diffText(before, split.before)}${figureHtml}${tailHtml}`;
      return;
    }
  }
  diffNode.innerHTML = `${diffText(before, dataChartTextWithoutFigures(after))}${figureHtml}`;
}

function renderDataChartResult() {
  const result = state.dataChartResult || null;
  if (!result?.chart?.dataUrl) {
    refreshDataChartDiff();
    return;
  }
  const currentResultText = textValue('#dataChartResultText');
  const replacementText = String(result.replacementText || '').trim();
  const backfillText = dataChartComposedBackfillText(result);
  if ((!currentResultText || currentResultText === replacementText) && backfillText) {
    setText('#dataChartResultText', backfillText);
  }
  refreshDataChartDiff();
}

const DATA_CHART_TABLE_COLUMNS = [
  { key: 'label', label: '标签', fallback: 0 },
  { key: 'value', label: '数值', fallback: 1 },
  { key: 'sourceName', label: '来源名称', fallback: 2 },
  { key: 'publisher', label: '发布机构', fallback: 3 },
  { key: 'url', label: '链接', fallback: 4 },
  { key: 'note', label: '备注', fallback: 5 },
];

function csvEscape(value) {
  const text = String(value ?? '');
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function dataChartRowsFromSearch(data = state.dataChartSearchResult) {
  const rows = Array.isArray(data?.dataRows) ? data.dataRows : [];
  return rows.map((row) => ({
    label: String(row?.label || '').trim(),
    value: String(row?.rawValue || row?.value || '').trim(),
    sourceName: String(row?.sourceName || '').trim(),
    publisher: String(row?.publisher || '').trim(),
    url: String(row?.url || '').trim(),
    note: String(row?.note || row?.source || '').trim(),
  })).filter((row) => row.label || row.value || row.sourceName || row.publisher || row.url || row.note);
}

function parseDataChartTableText(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').filter((line) => line.trim());
  if (!lines.length) return [];
  const parseLine = (line) => {
    const cells = [];
    let cell = '';
    let quoted = false;
    for (let index = 0; index < line.length; index += 1) {
      const char = line[index];
      if (char === '"' && quoted && line[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else if (char === '"') {
        quoted = !quoted;
      } else if (char === ',' && !quoted) {
        cells.push(cell.trim());
        cell = '';
      } else {
        cell += char;
      }
    }
    cells.push(cell.trim());
    return cells;
  };
  const header = parseLine(lines[0]).map((cell) => cell.trim());
  const hasHeader = header.some((cell) => ['标签', '数值', '来源名称', '发布机构', '链接', '备注', '来源/备注'].includes(cell));
  const dataLines = hasHeader ? lines.slice(1) : lines;
  return dataLines.map((line) => {
    const cells = parseLine(line);
    return {
      label: cells[0] || '',
      value: cells[1] || '',
      sourceName: cells[2] || '',
      publisher: cells[3] || '',
      url: cells[4] || '',
      note: cells[5] || cells[2] || '',
    };
  }).filter((row) => row.label || row.value || row.sourceName || row.publisher || row.url || row.note);
}

function dataChartRowsToCsv(rows) {
  if (!Array.isArray(rows) || !rows.length) return '';
  const output = [DATA_CHART_TABLE_COLUMNS.map((column) => csvEscape(column.label)).join(',')];
  (rows || []).forEach((row) => {
    output.push(DATA_CHART_TABLE_COLUMNS.map((column) => csvEscape(row?.[column.key] || '')).join(','));
  });
  return output.join('\n');
}

function dataChartEditableRows() {
  return $$('#dataChartEditableTable .data-chart-table-row').map((rowNode) => {
    const row = {};
    DATA_CHART_TABLE_COLUMNS.forEach((column) => {
      row[column.key] = rowNode.querySelector(`[data-data-table-cell="${column.key}"]`)?.value || '';
    });
    return row;
  }).filter((row) => DATA_CHART_TABLE_COLUMNS.some((column) => String(row[column.key] || '').trim()));
}

function syncDataChartTableFromEditable() {
  setText('#dataChartDataTable', dataChartRowsToCsv(dataChartEditableRows()));
}

function renderDataChartEditableTable(rows = []) {
  const table = $('#dataChartEditableTable');
  if (!table) return;
  const normalizedRows = rows.length ? rows : [{ label: '', value: '', sourceName: '', publisher: '', url: '', note: '' }];
  table.innerHTML = `
    <div class="data-chart-table-header">
      ${DATA_CHART_TABLE_COLUMNS.map((column) => `<div>${escapeHtml(column.label)}</div>`).join('')}
      <div>操作</div>
    </div>
    <div class="data-chart-table-body">
      ${normalizedRows.map((row, index) => `
        <div class="data-chart-table-row" data-data-table-row="${index}">
          ${DATA_CHART_TABLE_COLUMNS.map((column) => `
            <input class="data-chart-table-input" data-data-table-cell="${column.key}" value="${escapeHtml(row?.[column.key] || '')}" />
          `).join('')}
          <button class="secondary-button compact data-chart-row-remove" type="button" title="删除行">删除</button>
        </div>
      `).join('')}
    </div>
  `;
  $$('#dataChartEditableTable .data-chart-table-input').forEach((input) => {
    input.addEventListener('input', () => {
      syncDataChartTableFromEditable();
      saveDraft();
    });
  });
  $$('#dataChartEditableTable .data-chart-row-remove').forEach((button) => {
    button.addEventListener('click', () => {
      button.closest('.data-chart-table-row')?.remove();
      if (!$('#dataChartEditableTable .data-chart-table-row')) {
        renderDataChartEditableTable([]);
      }
      syncDataChartTableFromEditable();
      saveDraft();
    });
  });
  syncDataChartTableFromEditable();
}

function setDataChartTableText(text) {
  setText('#dataChartDataTable', text || '');
  renderDataChartEditableTable(parseDataChartTableText(text));
}

function setDataChartRows(rows) {
  renderDataChartEditableTable(rows || []);
}

function addDataChartEditableRow(row = {}) {
  const rows = dataChartEditableRows();
  rows.push({
    label: row.label || '',
    value: row.value || '',
    sourceName: row.sourceName || '',
    publisher: row.publisher || '',
    url: row.url || '',
    note: row.note || '',
  });
  renderDataChartEditableTable(rows);
  saveDraft();
}

function dataChartRowSummary(row) {
  const label = String(row?.label || '').trim();
  const value = String(row?.rawValue || row?.value || '').trim();
  const sourceName = String(row?.sourceName || '').trim();
  const publisher = String(row?.publisher || '').trim();
  const source = String(row?.source || '').trim();
  const note = String(row?.note || '').trim();
  const sourcePart = [sourceName || source, publisher, note].filter(Boolean).join('；');
  return [label, value, sourcePart].filter(Boolean).join(',');
}

function dataChartSourceItemsFromSearch(data = state.dataChartSearchResult) {
  if (!data) return [];
  if (Array.isArray(data.sourceItems) && data.sourceItems.length) return data.sourceItems;
  const rows = Array.isArray(data.dataRows) ? data.dataRows : [];
  const sources = Array.isArray(data.dataSources) ? data.dataSources : [];
  const rowItems = rows.map((row) => ({
    title: row.sourceName || row.publisher || row.source || '数据来源',
    url: row.url || '',
    publisher: row.publisher || '',
    summary: dataChartRowSummary(row),
    rows: [row],
  }));
  const known = new Set(rowItems.map((item) => item.url || item.title || item.summary));
  const sourceItems = sources.map((source) => {
    const text = String(source || '').trim();
    const urlMatch = text.match(/https?:\/\/\S+/);
    return {
      title: text.split('；')[0] || '候选来源',
      url: urlMatch ? urlMatch[0] : '',
      publisher: '',
      summary: text,
      rows: [],
    };
  }).filter((item) => {
    const key = item.url || item.title || item.summary;
    if (!key || known.has(key)) return false;
    known.add(key);
    return true;
  });
  return [...rowItems, ...sourceItems];
}

function renderDataChartSourceList(data = state.dataChartSearchResult) {
  const list = $('#dataChartSourceList');
  if (!list) return;
  const items = dataChartSourceItemsFromSearch(data);
  if (!items.length) {
    list.textContent = 'AI 搜索数据后，这里会列出可点击核验的来源。';
    return;
  }
  list.innerHTML = items.map((item, index) => {
    const title = item.title || item.sourceName || '数据来源';
    const url = item.url || '';
    const rows = Array.isArray(item.rows) ? item.rows : [];
    const rowText = rows.length ? rows.map(dataChartRowSummary).filter(Boolean).join('\n') : (item.summary || item.snippet || '');
    const meta = [item.publisher, item.sourceName].filter(Boolean).join('；');
    const titleHtml = url
      ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>`
      : `<strong>${escapeHtml(title)}</strong>`;
    return `
      <article class="data-source-card">
        <div class="data-source-index">${index + 1}</div>
        <div class="data-source-body">
          <div class="data-source-title">${titleHtml}</div>
          ${meta ? `<div class="data-source-meta">${escapeHtml(meta)}</div>` : ''}
          <div class="data-source-summary">${escapeHtml(rowText || '请打开来源核验统计口径和原始数据。')}</div>
        </div>
      </article>
    `;
  }).join('');
}

function dataChartTableSummary(data = state.dataChartSearchResult) {
  const rows = Array.isArray(data?.dataRows) ? data.dataRows : [];
  const tableText = textValue('#dataChartDataTable');
  const lines = tableText.split(/\r?\n/).filter((line) => line.trim());
  const rowCount = rows.length || Math.max(0, lines.length - 1);
  const sourceNote = String(data?.sourceNote || '').trim();
  return [
    sourceNote || '请审核下方数据表，确认每行数据、单位和来源后再生成图表。',
    rowCount ? `已汇总 ${rowCount} 行候选数据。` : '尚未汇总到可作图数据。',
    '最终用于绘图的数据以“可编辑数据表”为准，格式：标签,数值,来源/备注。',
  ].join('\n');
}

function dataChartSectionsForPayload() {
  const sections = collectPaperSectionsPayload(false);
  if (sections.length) return sections;
  const text = textValue('#dataChartFullText');
  return text ? [{ title: '全文内容', content: text }] : [];
}

function ensureDataChartFullText() {
  const existing = textValue('#dataChartFullText');
  if (existing) return existing;
  const pushed = state.paperPushTargets?.datachart;
  if (pushed?.titles?.length) {
    const pushedText = (pushed.titles || []).map(paperSectionTextBlock).filter(Boolean).join('\n\n').trim();
    if (pushedText) {
      setText('#dataChartFullText', pushedText);
      return pushedText;
    }
  }
  const fullText = collectFullPaperText(false);
  if (fullText) {
    setText('#dataChartFullText', fullText);
    return fullText;
  }
  const current = textValue('#paperContext');
  if (current) {
    setText('#dataChartFullText', current);
    return current;
  }
  return '';
}

function loadDataChartFullText() {
  const fullText = collectFullPaperText(false) || textValue('#paperContext') || textValue('#dataChartFullText');
  if (!fullText) {
    setState('datachart', '论文写作页暂无可查看的正文内容。', 'error');
    return;
  }
  setText('#dataChartFullText', fullText);
  setState('datachart', '已汇总全文内容', 'done');
  updateDataChartStep(1);
  saveDraft();
}

async function findDataChartTargets() {
  const fullText = ensureDataChartFullText();
  if (!fullText) {
    setState('datachart', '请先查看或粘贴全文内容。', 'error');
    return;
  }
  try {
    const data = await withRunningState('datachart', 'AI 正在阅读全文并定位图表插入位置...', async () => requestJson('/api/data-chart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'find',
        fullText,
        topic: textValue('#paperTopic'),
        outline: textValue('#paperOutline'),
        sections: dataChartSectionsForPayload(),
        limit: 8,
      }),
    }));
    state.dataChartTargets = Array.isArray(data.targets) ? data.targets : [];
    state.selectedDataChartTargetId = state.dataChartTargets[0]?.id || '';
    state.dataChartResult = null;
    state.dataChartSearchResult = null;
    state.dataChartApproved = false;
    setText('#dataChartResultText', '');
    setDataChartTableText('');
    renderDataChartSourceList();
    resetDataChartDiff();
    const selected = currentDataChartTarget();
    if (selected) {
      setText('#dataChartQuery', selected.query || '');
      setText('#dataChartTitle', defaultDataChartTitle(selected));
      if ($('#dataChartType')) $('#dataChartType').value = selected.chartType || 'bar';
    }
    renderDataChartTargets();
    updateDataChartStep(2);
    setState('datachart', data.summary || `已定位 ${state.dataChartTargets.length} 个候选位置`, 'done');
    saveDraft();
  } catch (error) {
    setState('datachart', `定位失败：${error.message}`, 'error');
  }
}

async function searchDataChartData() {
  const target = currentDataChartTarget();
  if (!target) {
    setState('datachart', '请先定位并选择一个候选段落。', 'error');
    return;
  }
  try {
    const data = await withRunningState('datachart', 'AI 正在检索数据并整理来源...', async () => requestJson('/api/data-chart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'search',
        query: textValue('#dataChartQuery') || target.query || '',
        target,
        fullText: textValue('#dataChartFullText'),
      }),
    }));
    state.dataChartSearchResult = data;
    const rows = dataChartRowsFromSearch(data);
    if (rows.length) setDataChartRows(rows);
    else if (data.tableText) setDataChartTableText(data.tableText);
    if (data.title) setText('#dataChartTitle', data.title);
    if (data.unit && !textValue('#dataChartUnit')) setText('#dataChartUnit', data.unit);
    if (data.chartType && $('#dataChartType')) $('#dataChartType').value = data.chartType;
    renderDataChartSourceList(data);
    updateDataChartStep(3);
    setState('datachart', data.needsManualData ? '请补充真实数据后审核' : '数据已整理，请审核', data.needsManualData ? 'running' : 'done');
    saveDraft();
  } catch (error) {
    setState('datachart', `搜索数据失败：${error.message}`, 'error');
  }
}

function fillDataChartSampleTable() {
  if (textValue('#dataChartDataTable')) return;
  const sampleRows = [
    { label: '2020年城镇地区', value: 79.8, rawValue: '79.8', source: '第47次《中国互联网络发展状况统计报告》；中国互联网络信息中心（CNNIC）', sourceName: '第47次《中国互联网络发展状况统计报告》', publisher: '中国互联网络信息中心（CNNIC）', url: 'https://www.cnnic.net.cn/' },
    { label: '2020年农村地区', value: 55.9, rawValue: '55.9', source: '第47次《中国互联网络发展状况统计报告》；中国互联网络信息中心（CNNIC）', sourceName: '第47次《中国互联网络发展状况统计报告》', publisher: '中国互联网络信息中心（CNNIC）', url: 'https://www.cnnic.net.cn/' },
  ];
  state.dataChartSearchResult = {
    sourceNote: '已填入示例表头，请替换为已核验的真实数据。',
    dataRows: sampleRows,
    sourceItems: [{
      title: '中国互联网络信息中心（CNNIC）',
      url: 'https://www.cnnic.net.cn/',
      publisher: 'CNNIC',
      summary: '2020年城镇地区,79.8,第47次《中国互联网络发展状况统计报告》；中国互联网络信息中心（CNNIC）',
      rows: sampleRows,
    }],
  };
  setDataChartRows(dataChartRowsFromSearch(state.dataChartSearchResult));
  renderDataChartSourceList();
  updateDataChartStep(3);
  saveDraft();
}

async function generateDataChart() {
  const target = currentDataChartTarget();
  if (!target) {
    setState('datachart', '请先选择候选段落。', 'error');
    return;
  }
  syncDataChartTableFromEditable();
  if (!textValue('#dataChartDataTable')) {
    setState('datachart', '请先搜索或填写已审核的数据表。', 'error');
    return;
  }
  storeCurrentPaperEditor({ skipEmptyOverwrite: true });
  try {
    const data = await withRunningState('datachart', '正在通过 Python 生成图表...', async () => requestJson('/api/data-chart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'generate',
        target,
        tableText: textValue('#dataChartDataTable'),
        chartType: $('#dataChartType')?.value || target.chartType || 'bar',
        title: textValue('#dataChartTitle'),
        unit: textValue('#dataChartUnit'),
        referenceStyle: textValue('#referenceStyle') || 'GB/T 7714',
        allSections: paperSectionsForReferenceSync(),
      }),
    }));
    state.dataChartResult = data;
    state.dataChartApproved = false;
    const backfillText = dataChartComposedBackfillText(data);
    setText('#dataChartResultText', backfillText);
    $('#dataChartResultText')?.setAttribute('hidden', '');
    renderDataChartResult();
    updateDataChartStep(4);
    setState('datachart', '图表已生成，正在回填到论文对应位置...', 'running');
    addHistoryRecord('datachart', '生成数据图表', {
      inputSelector: '#dataChartDataTable',
      outputSelector: '#dataChartResultText',
      output: backfillText,
    });
    backfillDataChartResult({ stayOnDataChart: true });
    saveDraft();
  } catch (error) {
    setState('datachart', `生成图表失败：${error.message}`, 'error');
  }
}

function approveDataChartResult() {
  if (!state.dataChartResult?.chart?.dataUrl || !textValue('#dataChartResultText')) {
    setState('datachart', '请先生成图表和改写内容。', 'error');
    return;
  }
  state.dataChartApproved = true;
  updateDataChartStep(5);
  backfillDataChartResult();
  saveDraft();
}

function backfillDataChartResult(options = {}) {
  const target = currentDataChartTarget();
  if (!target) {
    setState('datachart', '没有可回填的候选段落。', 'error');
    return;
  }
  state.dataChartApproved = true;
  const replacement = dataChartBackfillText();
  const refTitle = applyDataChartReferenceResult(state.dataChartResult);
  const outcome = replacePaperSectionParagraph(target.sectionTitle, target.originalText, replacement);
  if (!outcome.ok) {
    setState('datachart', outcome.message || '回填失败', 'error');
    return;
  }
  loadPaperSection(outcome.title);
  renderPaperSections();
  const refHint = refTitle ? `，并更新 ${refTitle}` : '';
  setState('datachart', `已回填到 ${outcome.title}${refHint}`, 'done');
  setState('paper', `已从数据图表回填到 ${outcome.title}${refHint}`, 'done');
  if (!options.stayOnDataChart) setPage('paper');
  saveDraft();
}

function bindDataChartActions() {
  $('#dataChartLoadFullText')?.addEventListener('click', loadDataChartFullText);
  $('#dataChartFindTargets')?.addEventListener('click', findDataChartTargets);
  $('#dataChartSearchData')?.addEventListener('click', searchDataChartData);
  $('#dataChartGenerate')?.addEventListener('click', generateDataChart);
  $('#dataChartBackfill')?.addEventListener('click', backfillDataChartResult);
  $('#dataChartUseSample')?.addEventListener('click', fillDataChartSampleTable);
  $('#dataChartAddRow')?.addEventListener('click', () => addDataChartEditableRow());
  $('#dataChartApproveResult')?.addEventListener('click', approveDataChartResult);
  $('#dataChartEditResult')?.addEventListener('click', () => {
    const editor = $('#dataChartResultText');
    if (editor) {
      editor.hidden = false;
      editor.focus();
    }
    state.dataChartApproved = false;
    setState('datachart', '正在编辑回填文字，图片仍会在差异区预览。', 'running');
    refreshDataChartDiff();
    saveDraft();
  });
  $('#dataChartResultText')?.addEventListener('input', () => {
    state.dataChartApproved = false;
    refreshDataChartDiff();
    saveDraft();
  });
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
    const body = paperSectionBodyContent(section.title);
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

    const rawContent = String(state.paperSectionContents[title] || '').trim();
    const existingContent = paperSectionBodyContent(title);

    if (!isPaperSectionWritable(title)) return;

    // 如果是"只写空白"模式，跳过已有内容的章节
    if (emptyOnly && existingContent) return;

    targets.push({
      title,
      context: existingContent || rawContent,
    });
  });

  return targets;
}

function targetWordCountValue(selector, fallback) {
  const value = parseInt($(selector)?.value, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function isChecked(selector) {
  const node = $(selector);
  return Boolean(node?.checked);
}

function customTotalWordCount() {
  return isChecked('#totalWordCountAuto') ? 0 : targetWordCountValue('#totalWordCount', 0);
}

function customSectionWordCount() {
  return isChecked('#wordCountAuto') ? 0 : targetWordCountValue('#wordCount', 0);
}

function isPaperBodySection(section) {
  const kind = paperSpecialKind(section?.title);
  return !['reference', 'cn_abstract', 'cn_keywords', 'en_abstract', 'en_keywords'].includes(kind);
}

function deepestBodyWritingSections() {
  const sections = (state.paperSections || []).filter((section) => isPaperBodySection(section) && isPaperSectionWritable(section.title));
  if (!sections.length) return [];
  const deepestLevel = Math.max(...sections.map((section) => Math.max(1, Number(section.level || 1))));
  return sections.filter((section) => Math.max(1, Number(section.level || 1)) === deepestLevel);
}

function autoSectionWordCount() {
  const totalTarget = customTotalWordCount();
  if (!totalTarget) return 1000;
  const basisCount = deepestBodyWritingSections().length || 1;
  return Math.max(300, Math.round(totalTarget / basisCount));
}

function autoWordCountForSection(title) {
  const perLeafCount = autoSectionWordCount();
  return perLeafCount;
}

function recommendedOutlineSectionLimit(totalWordCount) {
  const total = Number(totalWordCount || 0);
  if (!total) return '';
  if (total <= 5000) return '4-6 个正文叶子章节，尽量只保留二级标题';
  if (total <= 10000) return '6-10 个正文叶子章节，谨慎使用三级标题';
  if (total <= 20000) return '10-16 个正文叶子章节，避免超过 18 个可写小节';
  if (total <= 40000) return '16-28 个正文叶子章节，按研究主线合并相近小节';
  return '28-40 个正文叶子章节，仍需避免机械拆分为过多小节';
}

function requestedSectionWordCount(title = '') {
  return customSectionWordCount() || autoWordCountForSection(title);
}

function perSectionWordCountForBatch() {
  return requestedSectionWordCount();
}

function paperTemplateForPayload() {
  const template = state.paperTemplate;
  if (!template || !Array.isArray(template.headings) || !template.headings.length) return null;
  return {
    filename: template.filename || '',
    fileType: template.fileType || '',
    summary: template.summary || '',
    headings: template.headings.slice(0, 80),
  };
}

function renderPaperTemplate() {
  const status = $('#paperTemplateStatus');
  const summary = $('#paperTemplateSummary');
  const preview = $('#paperTemplatePreview');
  const template = state.paperTemplate;
  if (!template || !Array.isArray(template.headings) || !template.headings.length) {
    if (status) status.textContent = '未上传模板';
    if (summary) summary.textContent = '支持 DOC、DOCX、PDF、TXT、Markdown。上传后生成大纲会参考模板目录结构。';
    if (preview) preview.textContent = '暂无目录结构';
    return;
  }
  const headingCount = template.headings.length;
  if (status) status.textContent = `${template.filename || '论文模板'} · ${headingCount} 个标题`;
  if (summary) summary.textContent = template.summary || `已读取目录结构：${headingCount} 个标题，生成大纲时会参考模板层级。`;
  if (preview) {
    preview.textContent = template.headings
      .slice(0, 60)
      .map((heading) => `${'  '.repeat(Math.max(0, Number(heading.level || 1) - 1))}${heading.title}`)
      .join('\n') || '暂无目录结构';
  }
}

function clearPaperTemplate() {
  state.paperTemplate = null;
  const input = $('#paperTemplateFile');
  if (input) input.value = '';
  renderPaperTemplate();
  saveDraft();
}

async function uploadPaperTemplateFile(file) {
  if (!file) return;
  const status = $('#paperTemplateStatus');
  if (status) status.textContent = '正在读取目录...';
  try {
    const dataUrl = await readFileAsDataUrl(file);
    const data = await requestJson('/api/template/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: file.name,
        mimeType: file.type || '',
        dataUrl,
      }),
    });
    state.paperTemplate = data;
    renderPaperTemplate();
    saveDraft();
    setState('paper', `已读取论文模板目录：${data.filename || file.name}`, 'done');
  } catch (error) {
    state.paperTemplate = null;
    renderPaperTemplate();
    if (status) status.textContent = '模板读取失败';
    setState('paper', `模板读取失败：${error.message}`, 'error');
  }
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('文件读取失败'));
    reader.readAsDataURL(file);
  });
}

function syncWordLimitControls() {
  syncWordLimitInput('#totalWordCountAuto', '#totalWordCount', '20000');
  syncWordLimitInput('#wordCountAuto', '#wordCount', '1000');
}

function syncWordLimitInput(autoSelector, inputSelector, fallbackValue) {
  const input = $(inputSelector);
  if (!input) return;
  const auto = isChecked(autoSelector);
  if (auto) {
    const current = String(input.value || '').trim();
    if (current && current !== '0') input.dataset.lastValue = current;
    if (!input.dataset.lastValue) input.dataset.lastValue = fallbackValue;
    input.value = '0';
    input.disabled = true;
  } else {
    input.disabled = false;
    if (!input.value || input.value === '0') input.value = input.dataset.lastValue || fallbackValue;
    input.focus();
  }
}

function paperSectionsForReferenceSync() {
  return state.paperSections.map((section) => {
    const content = state.paperSectionContents[section.title] || '';
    return { title: section.title, content };
  });
}

async function runBatchWriteAllSections() {
  if (state.paperRunning) {
    setState('paper', '已有论文生成任务正在运行，请等待完成后再试。', 'running');
    return;
  }
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

  const wordCount = perSectionWordCountForBatch();
  const totalWords = targets.length * wordCount;
  const totalTarget = customTotalWordCount();

  // 警告确认
  if (targets.length > 8 || wordCount > 1200 || totalWords > 12000) {
    const targetText = totalTarget ? `全文目标字数 ${totalTarget} 字，` : '';
    const warningMsg = `即将批量写作 ${targets.length} 个章节，${targetText}按每节约 ${wordCount} 字预计生成约 ${totalWords} 字。\n\n这可能需要较长时间并消耗较多 API 额度。\n\n是否继续？`;
    if (!confirm(warningMsg)) {
      setState('paper', '已取消批量写作', 'error');
      return;
    }
  }

  state.paperRunning = true;
  setPaperActionsDisabled(true, 'batch-write');
  const runningTimer = startRunningStateTimer('paper', `准备批量写作 ${targets.length} 个章节...`);
  setState('paper', `准备批量写作 ${targets.length} 个章节...`, 'running');

  const referenceStyle = $('#referenceStyle').value;
  const completedSections = [];
  const failedSections = [];
  let referenceTitle = findPaperSectionByKind('reference') || '';

  try {
    for (let i = 0; i < targets.length; i++) {
      const target = targets[i];
      const progress = `${i + 1}/${targets.length}`;
      let sectionDone = false;
      let lastError = '';

      for (let attempt = 1; attempt <= BATCH_WRITE_MAX_ATTEMPTS; attempt += 1) {
        const retryText = attempt > 1 ? `（重试 ${attempt - 1}/${BATCH_WRITE_MAX_ATTEMPTS - 1}）` : '';
        setState('paper', `正在写作 ${progress}：${target.title}${retryText}`, 'running');

        try {
          const payload = {
            action: 'section',
            text: outline,
            outline,
            sectionTitle: target.title,
            context: target.context,
            wordCount,
            referenceStyle,
            allSections: paperSectionsForReferenceSync(),
            referenceSnapshot: state.paperReferenceSnapshot || '',
          };

          const data = await requestPaperRun(payload, { timeoutMs: BATCH_PAPER_REQUEST_TIMEOUT_MS });
          const body = data.content || data.result || '';
          if (!body) throw new Error('返回结果为空');

          writePaperContentToSection(target.title, body, { loadEditor: false });
          completedSections.push(target.title);

          if (data.references) {
            const refTitle = data.references.title || referenceTitle || '# 参考文献';
            const mode = data.references.mode;

            if (mode === 'append' && data.references.append) {
              referenceTitle = appendPaperReferences(refTitle, data.references.append, { loadEditor: false }) || referenceTitle;
            } else if (mode === 'reorder' && data.references.content) {
              referenceTitle = writePaperContentToSection(refTitle, data.references.content, { loadEditor: false }) || referenceTitle;
            }
          }

          if (data.updatedSections && Array.isArray(data.updatedSections)) {
            for (const section of data.updatedSections) {
              if (section.title) {
                writePaperContentToSection(section.title, section.content, { loadEditor: false });
              }
            }
          }

          saveDraft();
          sectionDone = true;
          break;
        } catch (error) {
          lastError = error.message || String(error);
          const errorMsg = String(lastError || '').toLowerCase();
          const shouldStopImmediately = errorMsg.includes('quota') || errorMsg.includes('rate limit') || errorMsg.includes('额度') || errorMsg.includes('频率');
          if (isFetchConnectionError(error) && attempt < BATCH_WRITE_MAX_ATTEMPTS) {
            const reachable = await isLocalServerReachable();
            const waitMs = reachable ? 2500 : 6000;
            setState('paper', `本地连接短暂中断，正在重试 ${target.title}（${attempt}/${BATCH_WRITE_MAX_ATTEMPTS - 1}）`, 'running');
            await new Promise((resolve) => window.setTimeout(resolve, waitMs));
            continue;
          }
          if (shouldStopImmediately || attempt >= BATCH_WRITE_MAX_ATTEMPTS) {
            failedSections.push({ title: target.title, error: lastError });
            break;
          }
        }
      }

      if (!sectionDone) {
        setState('paper', `批量写作已暂停在 ${progress}：${target.title}。错误：${lastError}`, 'error');
        alert(`批量写作已暂停\n\n已完成：${completedSections.length} 个\n失败章节：${target.title}\n错误：${lastError}\n\n后续章节尚未继续写，避免跳过。修复后可选择“只写空白章节”继续。`);
        break;
      }

      if (i + 1 < targets.length) {
        await new Promise((resolve) => window.setTimeout(resolve, 350));
      }

      if (failedSections.length) {
          break;
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
      const failed = failedSections[0];
      setState('paper', `批量写作已暂停：成功 ${completedSections.length} 个，停在 ${failed.title}`, 'error');
    }
  } catch (error) {
    setState('paper', `批量写作失败：${error.message}`, 'error');
  } finally {
    window.clearInterval(runningTimer);
    state.paperRunning = false;
    setPaperActionsDisabled(false);
  }
}

function paperRequestPayload(action, payloadText, targetSection, topic, outline, context) {
  const allSectionsPayload = ['section', 'references'].includes(action) ? paperSectionsForReferenceSync() : null;
  const payload = {
    action,
    text: payloadText,
    topic,
    subject: textValue('#paperSubject'),
    paperStyle: $('#paperStyle').value,
    referenceStyle: $('#referenceStyle').value,
    sectionTitle: targetSection,
    wordCount: action === 'section' ? requestedSectionWordCount(targetSection) : (customSectionWordCount() || ''),
    totalWordCount: customTotalWordCount() || '',
    outlineSectionLimit: recommendedOutlineSectionLimit(customTotalWordCount()),
    templateStructure: paperTemplateForPayload(),
    outline,
    context,
    language: '中文',
  };

  // Include all sections for reference management
  if (allSectionsPayload) {
    payload.allSections = allSectionsPayload;
    payload.referenceSnapshot = state.paperReferenceSnapshot || '';
  }

  return payload;
}

function applyReferenceResult(data, fallbackTitle = '# 参考文献') {
  if (data.updatedSections && Array.isArray(data.updatedSections)) {
    for (const section of data.updatedSections) {
      if (section.title) {
        writePaperContentToSection(section.title, section.content, { loadEditor: false });
      }
    }
  }

  if (!data.references) return '';
  const refTitle = data.references.title || fallbackTitle;
  const mode = data.references.mode;

  if (mode === 'append' && data.references.append) {
    return appendPaperReferences(refTitle, data.references.append, { loadEditor: false }) || refTitle;
  }
  if (mode === 'reorder' && data.references.content) {
    return writePaperContentToSection(refTitle, data.references.content, { loadEditor: false }) || refTitle;
  }
  return '';
}

async function requestPaperRun(payload, options = {}) {
  const timeoutMs = Number(options.timeoutMs || PAPER_REQUEST_TIMEOUT_MS);
  return requestJson('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, {
    timeoutMs,
    timeoutMessage: `论文生成请求等待超过 ${Math.round(timeoutMs / 1000)} 秒仍未返回。请检查网络状态，或到「配置管理」切换接口/模型后重试。`,
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
  if (state.paperRunning) {
    setState('paper', '已有论文生成任务正在运行，请等待完成后再试。', 'running');
    return;
  }
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
    : (action === 'abstract'
      ? collectPaperTextForAbstract()
      : (action === 'section'
        ? outline
        : (action === 'references' ? state.paperReferenceSnapshot || context || outline || topic : context || outline || topic)));
  if (!payloadText) {
    setState('paper', action === 'abstract' ? '请先完善论文正文内容' : '请先填写内容', 'error');
    return;
  }
  if (action === 'section' && !isPaperSectionWritable(targetSection)) {
    const childTitle = nearestWritablePaperSection(targetSection);
    setState('paper', childTitle ? `该章节是标题节点，请选择具体小节：${childTitle}` : '该章节是标题节点，不能写入正文。', 'error');
    if (childTitle) selectPaperSection(childTitle, { storeCurrent: false });
    return;
  }

  state.paperRunning = true;
  setPaperActionsDisabled(true, action);
  const runningText = action === 'section' ? `正在撰写章节：${targetSection}` : '论文生成中...';
  try {
    const data = await withRunningState('paper', runningText, async () => {
      const payload = paperRequestPayload(action, payloadText, targetSection, topic, outline, context);
      return action === 'abstract'
        ? await runPaperAbstract(payload)
        : await requestPaperRun(payload);
    });
    if (action === 'outline' && data.result) {
      setText('#paperOutline', data.result);
      refreshPaperSections(false, { preserveExisting: false });
    } else if (action === 'abstract') {
      loadPaperSection(findPaperSectionByKind('cn_abstract') || targetSection);
    } else if (action === 'references') {
      const refTitle = applyReferenceResult(data, targetSection || '# 参考文献');
      if (refTitle) loadPaperSection(refTitle);
      syncPaperOutlineFromSections();
      const entryCount = data.references?.entryCount;
      const citationCount = data.references?.citationCount;
      const detail = Number.isFinite(entryCount) ? `，共 ${entryCount} 条参考文献` : '';
      const citationDetail = Number.isFinite(citationCount) ? `，扫描到 ${citationCount} 处正文引用` : '';
      setState('paper', `参考文献已按整篇文章引用顺序整理${detail}${citationDetail}`, 'done');
    } else {
      const generatedContent = data.content || data.result || '';
      writePaperContentToSection(targetSection, generatedContent);

      const refTitle = applyReferenceResult(data);
      if (refTitle && data.references?.mode === 'append') {
        setState('paper', `章节写作完成，参考文献已追加到 ${refTitle}`, 'done');
      } else if (refTitle && data.references?.mode === 'reorder') {
        setState('paper', '章节写作完成，参考文献已重新排序', 'done');
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
    setState('paper', `论文生成失败：${error.message}`, 'error');
    if (action !== 'abstract') {
      setText('#paperResult', error.message);
    }
  } finally {
    state.paperRunning = false;
    setPaperActionsDisabled(false);
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
    if (scope === 'ai') {
      const baseData = await requestJson('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'analyze', text }),
      });
      state.lastBaseAiScore = baseData.analysis?.ai?.score;
    }
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
  if (reviewSelector === '#aiReview' && textValue(inputSelector)) {
    const baseData = await requestJson('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'analyze', text: textValue(inputSelector) }),
    });
    state.lastBaseAiScore = baseData.analysis?.ai?.score;
  }
  const data = await requestJson('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'analyze', text: target, sourceText }),
  });
  state.lastAnalysis = data.analysis;
  $(reviewSelector).textContent = renderAnalysisSummary(data.analysis, reviewSelector === '#aiReview' ? 'ai' : '');
  saveDraft();
}

async function runPanel(kind) {
  if (kind === 'ai') {
    const data = await runTransformPanel('ai', '#aiInput', '#aiOutput', {
      action: selectedAction('ai'),
      ...activePromptPayload('ai'),
    });
    if (data) {
      $('#aiReview').textContent = renderAnalysisSummary(data.analysis, 'ai');
      refreshPanelDiff('ai');
      addHistoryRecord('ai', 'AI 痕迹消除', { inputSelector: '#aiInput', outputSelector: '#aiOutput', analysis: data.analysis });
      saveDraft();
    }
  }
  if (kind === 'plagiarism') {
    const sourceText = textValue('#plagiarismSource');
    const data = await runTransformPanel('plagiarism', '#plagiarismInput', '#plagiarismOutput', {
      action: selectedAction('plagiarism'),
      sourceText,
      ...activePromptPayload('plagiarism'),
    });
    if (data) {
      setText('#plagiarismReview', '');
      syncResultOutputToEditor('plagiarism');
      refreshPanelDiff('plagiarism');
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
      ...activePromptPayload('polish'),
    });
    if (data) {
      setText('#polishReview', '');
      syncResultOutputToEditor('polish');
      refreshPanelDiff('polish');
      addHistoryRecord('polish', '学术润色', { inputSelector: '#polishInput', outputSelector: '#polishOutput', analysis: data.analysis });
      saveDraft();
    }
  }
  if (kind === 'translate') {
    const data = await runTransformPanel('polish', '#polishInput', '#polishOutput', {
      action: 'polish',
      polishMode: 'full',
      taskType: $('#polishTaskType').value,
      executionMode: $('#polishExecutionMode').value,
      topic: textValue('#polishTopic'),
      notes: textValue('#polishNotes'),
      ...activePromptPayload('polish'),
    });
    if (data) {
      setText('#polishReview', '');
      syncResultOutputToEditor('polish');
      refreshPanelDiff('polish');
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
      refreshPanelDiff('correction');
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
  state.currentCorrectionIssues = issues;
  state.currentCorrectionLabels = labels;
  state.selectedCorrectionIssueId = '';
  $('#correctionIssues').innerHTML = issues.length ? issues.slice(0, 40).map((issue) => `
    <button class="issue-item" type="button" data-issue-id="${escapeHtml(issue.id)}">
      <strong>${escapeHtml(issue.title || '未命名问题')}</strong>
      <span data-issue-status>${escapeHtml(correctionIssueStatusText(issue, labels))}</span>
    </button>
  `).join('') : '暂未发现待处理问题。';
  $('#correctionReport').textContent = correction.report || '暂无报告。';
  syncCorrectionAutoFixButton();
  $$('#correctionIssues .issue-item[data-issue-id]').forEach((button) => {
    button.addEventListener('click', () => {
      const issue = issues.find((item) => item.id === button.dataset.issueId);
      if (!issue) return;
      state.selectedCorrectionIssueId = issue.id;
      updateCorrectionIssueListState(issues, labels, state.selectedCorrectionIssueId);
      syncCorrectionAutoFixButton();
      renderCorrectionIssueReport(issue, labels);
    });
  });
}

function correctionIssueStatusText(issue, labels = {}) {
  const status = issue?.status === 'fixed' ? ' / 已修复' : '';
  return `${labels[issue?.category] || issue?.category || '-'} / ${issue?.severity || '-'}${status}`;
}

function issueHasReplacement(issue) {
  return Boolean(issue) && Object.prototype.hasOwnProperty.call(issue, 'replacement') && issue.replacement !== null && issue.replacement !== undefined;
}

function issueCanAutoFix(issue) {
  if (!issue || issue.status === 'fixed' || !issue.auto_fixable || !issueHasReplacement(issue)) return false;
  const start = Number(issue.start);
  const end = Number(issue.end);
  const hasUsableSpan = Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end >= start;
  return hasUsableSpan || Boolean(issue.original);
}

function currentCorrectionOutputText() {
  const outputText = readNodeText($('#correctionOutput'));
  return outputText || textValue('#correctionInput');
}

function findCorrectionIssueSpan(text, issue) {
  const content = String(text || '');
  const original = String(issue?.original || '');
  const start = Number(issue?.start);
  const end = Number(issue?.end);
  if (Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end >= start && end <= content.length) {
    if (!original || content.slice(start, end) === original) {
      return { start, end };
    }
  }
  if (!original) return null;

  const matches = [];
  let cursor = content.indexOf(original);
  while (cursor >= 0) {
    matches.push({ start: cursor, end: cursor + original.length });
    cursor = content.indexOf(original, cursor + Math.max(original.length, 1));
  }
  if (!matches.length) return null;
  const preferredStart = Number.isFinite(start) && start >= 0 ? start : 0;
  return matches.sort((left, right) => Math.abs(left.start - preferredStart) - Math.abs(right.start - preferredStart))[0];
}

function applySingleCorrectionIssue(issue) {
  if (!issueCanAutoFix(issue)) {
    return { ok: false, message: issue?.status === 'fixed' ? '该问题已经修复过。' : '该问题没有可直接应用的自动修复内容。' };
  }
  const currentText = currentCorrectionOutputText();
  if (!currentText) return { ok: false, message: '请先输入原文或运行智能纠错。' };
  const span = findCorrectionIssueSpan(currentText, issue);
  if (!span) {
    return { ok: false, message: '没有在当前修正内容中找到对应原文片段，可能已经被手动改过。' };
  }
  const replacement = String(issue.replacement ?? '');
  const nextText = currentText.slice(0, span.start) + replacement + currentText.slice(span.end);
  setText('#correctionOutput', nextText);
  issue.status = 'fixed';
  issue.appliedStart = span.start;
  issue.appliedEnd = span.start + replacement.length;
  refreshPanelDiff('correction');
  setState('correction', '已自动修复 1 处问题', 'done');
  saveDraft();
  return { ok: true, message: '已写入修正预览；回填时会使用修正后的纯文本内容。' };
}

function selectedCorrectionIssue() {
  return (state.currentCorrectionIssues || []).find((issue) => issue.id === state.selectedCorrectionIssueId) || null;
}

function syncCorrectionAutoFixButton() {
  const button = $('#correctionAutoFixButton');
  if (!button) return;
  const issue = selectedCorrectionIssue();
  const hasSelection = Boolean(issue);
  button.hidden = !hasSelection;
  button.disabled = !issueCanAutoFix(issue);
  button.textContent = issue?.status === 'fixed' ? '已修复' : '自动修复';
}

function handleSelectedCorrectionAutoFix() {
  const issue = selectedCorrectionIssue();
  if (!issue) return;
  const labels = state.currentCorrectionLabels || {};
  const result = applySingleCorrectionIssue(issue);
  updateCorrectionIssueListState(state.currentCorrectionIssues, labels, issue.id);
  syncCorrectionAutoFixButton();
  renderCorrectionIssueReport(issue, labels, result);
}

function updateCorrectionIssueListState(issues, labels = {}, selectedId = '') {
  $$('#correctionIssues .issue-item[data-issue-id]').forEach((button) => {
    const issue = issues.find((item) => item.id === button.dataset.issueId);
    button.classList.toggle('selected', button.dataset.issueId === selectedId);
    button.classList.toggle('fixed', issue?.status === 'fixed');
    const statusNode = button.querySelector('[data-issue-status]');
    if (statusNode && issue) statusNode.textContent = correctionIssueStatusText(issue, labels);
  });
}

function correctionReportRow(label, value) {
  if (!value) return '';
  return `
    <div class="correction-report-row">
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(value)}</span>
    </div>
  `;
}

function renderCorrectionIssueReport(issue, labels = {}, feedback = null) {
  const report = $('#correctionReport');
  if (!report) return;
  const fixed = issue.status === 'fixed';
  const canFix = issueCanAutoFix(issue);
  const replacementText = issueHasReplacement(issue) ? String(issue.replacement ?? '') : '';
  const fixPreview = replacementText ? replacementText : (issueHasReplacement(issue) ? '删除该片段' : '');
  report.innerHTML = `
    <div class="correction-report">
      ${correctionReportRow('问题', issue.title || '未命名问题')}
      ${correctionReportRow('分类', labels[issue.category] || issue.category || '-')}
      ${correctionReportRow('级别', issue.severity || '-')}
      ${correctionReportRow('说明', issue.message || '')}
      ${correctionReportRow('原文片段', issue.original || '')}
      ${correctionReportRow('修改建议', issue.suggestion || '')}
      ${correctionReportRow('自动修复', fixPreview)}
      ${fixed ? '<p class="correction-report-hint">该问题已修复。</p>' : ''}
      ${!fixed && !canFix ? '<p class="correction-report-hint">该问题需要人工确认后修改。</p>' : ''}
      ${feedback ? `<p class="correction-report-feedback ${feedback.ok ? 'is-done' : 'is-error'}">${escapeHtml(feedback.message)}</p>` : ''}
    </div>
  `;
  syncCorrectionAutoFixButton();
}

function renderCorrectionReviewAsIssues(analysis, title) {
  $('#correctionStats').innerHTML = '';
  state.currentCorrectionIssues = [];
  state.currentCorrectionLabels = {};
  state.selectedCorrectionIssueId = '';
  syncCorrectionAutoFixButton();
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
  state.paperTemplate = record.paperTemplate || null;
  state.dataChartTargets = Array.isArray(record.dataChartTargets) ? record.dataChartTargets : [];
  state.selectedDataChartTargetId = record.selectedDataChartTargetId || '';
  state.dataChartResult = record.dataChartResult || null;
  state.dataChartSearchResult = record.dataChartSearchResult || null;
  state.dataChartApproved = Boolean(record.dataChartApproved);
  applyFields(record.fields || {});
  syncModeSelections();
  renderPaperTemplate();
  renderDataChartTargets();
  renderDataChartSourceList();
  renderDataChartResult();
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
  bindDataChartActions();
  $$('[data-paper-action]').forEach((button) => button.addEventListener('click', () => runPaperAction(button.dataset.paperAction)));
  ['#totalWordCountAuto', '#wordCountAuto'].forEach((selector) => {
    $(selector)?.addEventListener('change', () => {
      syncWordLimitControls();
      saveDraft();
    });
  });
  ['#totalWordCount', '#wordCount'].forEach((selector) => {
    $(selector)?.addEventListener('input', () => {
      const node = $(selector);
      if (node && !node.disabled && node.value && node.value !== '0') node.dataset.lastValue = node.value;
    });
  });
  $('#paperTemplateFile')?.addEventListener('change', (event) => {
    uploadPaperTemplateFile(event.target.files?.[0]);
  });
  $('#clearPaperTemplate')?.addEventListener('click', clearPaperTemplate);
  $('#refreshOutlineSections').addEventListener('click', () => refreshPaperSections(true));
  $('#paperOutline').addEventListener('input', () => refreshPaperSections(false));
  $('#paperSectionJump')?.addEventListener('change', (event) => {
    const title = event.target.value;
    if (title) selectPaperSection(title);
  });
  $('#paperSectionSearch')?.addEventListener('input', (event) => {
    state.paperSectionFilter = event.target.value || '';
    renderPaperSections();
  });
  $('#paperPushButton')?.addEventListener('click', pushPaperSelectionToWorkspace);
  $('#correctionAutoFixButton')?.addEventListener('click', handleSelectedCorrectionAutoFix);
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
  $$('[data-prompt-panel]').forEach((button) => button.addEventListener('click', () => openPromptTemplateDialog(button.dataset.promptPanel)));
  $('#promptTemplateClose')?.addEventListener('click', closePromptTemplateDialog);
  $('#promptTemplateAdd')?.addEventListener('click', addPromptTemplate);
  $('#promptTemplateSave')?.addEventListener('click', saveSelectedPromptTemplate);
  $('#promptTemplateEnable')?.addEventListener('click', enableSelectedPromptTemplate);
  $('#promptTemplateDelete')?.addEventListener('click', deleteSelectedPromptTemplate);
  $('#promptTemplateDialog')?.addEventListener('click', (event) => {
    if (event.target?.id === 'promptTemplateDialog') closePromptTemplateDialog();
  });
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('#promptTemplateDialog')?.hidden) closePromptTemplateDialog();
  });
  $$('[data-review-panel="ai"]').forEach((button) => button.addEventListener('click', () => reviewText('#aiInput', '#aiOutput', '#aiReview')));
  $$('[data-review-panel="plagiarism"]').forEach((button) => button.addEventListener('click', () => reviewText('#plagiarismInput', '#plagiarismOutput', '#plagiarismReview', textValue('#plagiarismSource'))));
  $$('[data-review-panel="polish"]').forEach((button) => button.addEventListener('click', () => reviewText('#polishInput', '#polishOutput', '#polishReview')));
  $$('[data-diff-panel]').forEach((button) => button.addEventListener('click', () => refreshPanelDiff(button.dataset.diffPanel)));
  $$('[data-result-edit]').forEach((button) => button.addEventListener('click', () => setResultEditMode(button.dataset.resultEdit, true)));
  $$('[data-result-done]').forEach((button) => button.addEventListener('click', () => setResultEditMode(button.dataset.resultDone, false)));
  $$('[data-clear-panel]').forEach((button) => button.addEventListener('click', () => clearPanel(button.dataset.clearPanel)));
  FIELD_SELECTORS.forEach((selector) => {
    const node = $(selector);
    if (node) node.addEventListener('input', saveDraft);
  });
  $$('[data-copy-from]').forEach((button) => button.addEventListener('click', async () => {
    const node = document.getElementById(button.dataset.copyFrom);
    const value = readNodeText(node).trim();
    if (value) await navigator.clipboard.writeText(value);
  }));
  $$('[data-backfill-panel]').forEach((button) => button.addEventListener('click', () => {
    backfillProcessedPanel(button.dataset.backfillPanel);
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
    ai: ['#aiInput'],
    plagiarism: ['#plagiarismInput', '#plagiarismSource'],
    polish: ['#polishInput', '#polishNotes'],
    correction: ['#correctionInput'],
  }[scope] || [];
  fields.forEach((selector) => setText(selector, ''));
  resetPushedTargetResults(scope);
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
setDataChartTableText(textValue('#dataChartDataTable'));
syncWordLimitControls();
routeFromHash();
renderHistory();
loadStatus().catch((error) => {
  if ($('#modelStatus')) $('#modelStatus').textContent = error.message;
  renderProviders(null);
});

// Rich text editor functionality
function initRichTextEditor() {
  const editor = $('#paperContext');
  if (!editor || !editor.hasAttribute('contenteditable')) return;
  let mathRenderTimer = null;
  const restoreMathForEditing = () => restorePaperEditorMathSource(editor);
  const syncEditorAfterInput = () => {
    invalidatePaperEditorMathRender(editor);
    syncPaperEditorSourcesFromDom(editor);
  };
  const scheduleMathRender = () => {
    window.clearTimeout(mathRenderTimer);
    mathRenderTimer = window.setTimeout(() => {
      if (document.activeElement !== editor) renderPaperEditorMath(editor);
    }, 250);
  };

  // Handle toolbar button clicks
  $$('.toolbar-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      restoreMathForEditing();
      const format = btn.dataset.format;
      const action = btn.dataset.action;

      if (format) {
        // Handle format commands
        if (format === 'undo' || format === 'redo') {
          document.execCommand(format, false, null);
        } else if (format === 'indent') {
          document.execCommand('indent', false, null);
        } else {
          document.execCommand(format, false, null);
        }
      } else if (action) {
        // Handle special actions
        if (action === 'foreColor') {
          const color = prompt('请输入颜色（如 #ff0000 或 red）：', '#000000');
          if (color) {
            document.execCommand('foreColor', false, color);
          }
        } else if (action === 'hiliteColor') {
          const color = prompt('请输入背景色（如 #ffff00 或 yellow）：', '#ffff00');
          if (color) {
            document.execCommand('hiliteColor', false, color);
          }
        } else if (action === 'citation') {
          // Insert citation template
          const selection = window.getSelection();
          if (selection.rangeCount > 0) {
            const range = selection.getRangeAt(0);
            const citationNode = document.createTextNode('[?]');
            range.insertNode(citationNode);
            range.setStartAfter(citationNode);
            range.collapse(true);
            selection.removeAllRanges();
            selection.addRange(range);
          }
        } else if (action === 'find') {
          // Open find/replace dialog
          openFindReplaceDialog();
        }
      }

      editor.focus();
      updateToolbarState();
    });
  });

  // Update toolbar button states based on current selection
  function updateToolbarState() {
    $$('.toolbar-btn').forEach((btn) => {
      const format = btn.dataset.format;
      if (format === 'undo' || format === 'redo' || format === 'indent') {
        return; // 这些命令不需要状态高亮
      }
      const isActive = document.queryCommandState(format);
      btn.classList.toggle('active', isActive);
    });
  }

  // Update toolbar state on selection change
  editor.addEventListener('mouseup', updateToolbarState);
  editor.addEventListener('keyup', updateToolbarState);
  editor.addEventListener('focus', updateToolbarState);
  editor.addEventListener('beforeinput', restoreMathForEditing);
  editor.addEventListener('paste', restoreMathForEditing);
  editor.addEventListener('blur', () => renderPaperEditorMath(editor));

  // Handle keyboard shortcuts
  editor.addEventListener('keydown', (e) => {
    const key = e.key || '';
    if (key.length === 1 || ['Backspace', 'Delete', 'Enter'].includes(key)) {
      restoreMathForEditing();
    }
    if (e.ctrlKey || e.metaKey) {
      switch(e.key.toLowerCase()) {
        case 'b':
          e.preventDefault();
          document.execCommand('bold', false, null);
          updateToolbarState();
          break;
        case 'i':
          e.preventDefault();
          document.execCommand('italic', false, null);
          updateToolbarState();
          break;
        case 'u':
          e.preventDefault();
          document.execCommand('underline', false, null);
          updateToolbarState();
          break;
        case 'f':
          e.preventDefault();
          openFindReplaceDialog();
          break;
        case 'z':
          if (e.shiftKey) {
            e.preventDefault();
            document.execCommand('redo', false, null);
          } else {
            e.preventDefault();
            document.execCommand('undo', false, null);
          }
          break;
        case 'y':
          e.preventDefault();
          document.execCommand('redo', false, null);
          break;
      }
    }
  });

  // Save draft on content change
  editor.addEventListener('input', () => {
    syncEditorAfterInput();
    scheduleMathRender();
    saveDraft();
  });
}

initRichTextEditor();
loadConfig().catch((error) => {
  if ($('#providerList')) $('#providerList').textContent = error.message;
});

// Find and Replace functionality
let findReplaceState = {
  searchText: '',
  matches: [],
  currentIndex: -1,
  originalRanges: []
};

function openFindReplaceDialog() {
  const dialog = $('#findReplaceDialog');
  if (dialog) {
    dialog.style.display = 'flex';
    const findInput = $('#findInput');
    if (findInput) {
      findInput.focus();
      findInput.value = findReplaceState.searchText;
    }
  }
}

function closeFindReplaceDialog() {
  const dialog = $('#findReplaceDialog');
  if (dialog) dialog.style.display = 'none';
  // Keep highlights visible after closing dialog
  // User can manually clear them by opening dialog again and searching for something else
}

function clearHighlights() {
  const editor = $('#paperContext');
  if (!editor) return;

  // Remove all highlight spans
  const highlights = editor.querySelectorAll('.find-highlight, .find-current');
  highlights.forEach(span => {
    const parent = span.parentNode;
    while (span.firstChild) {
      parent.insertBefore(span.firstChild, span);
    }
    parent.removeChild(span);
  });

  // Normalize text nodes
  editor.normalize();
  findReplaceState.matches = [];
  findReplaceState.currentIndex = -1;
}

function highlightMatches(searchText) {
  const editor = $('#paperContext');
  if (!editor || !searchText) return;

  clearHighlights();
  findReplaceState.searchText = searchText;

  const text = editor.textContent;
  const regex = new RegExp(searchText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
  let match;
  const matches = [];

  while ((match = regex.exec(text)) !== null) {
    matches.push({ start: match.index, end: match.index + match[0].length });
  }

  if (matches.length === 0) {
    alert('未找到匹配项');
    return;
  }

  // Highlight all matches
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, null, false);
  const textNodes = [];
  let node;
  while (node = walker.nextNode()) {
    textNodes.push(node);
  }

  let offset = 0;
  textNodes.forEach(textNode => {
    const nodeStart = offset;
    const nodeEnd = offset + textNode.textContent.length;

    const nodeMatches = matches.filter(m => m.start < nodeEnd && m.end > nodeStart);

    if (nodeMatches.length > 0) {
      const parent = textNode.parentNode;
      const frag = document.createDocumentFragment();
      let lastIndex = 0;

      nodeMatches.forEach(match => {
        const matchStart = Math.max(0, match.start - nodeStart);
        const matchEnd = Math.min(textNode.textContent.length, match.end - nodeStart);

        // Add text before match
        if (matchStart > lastIndex) {
          frag.appendChild(document.createTextNode(textNode.textContent.substring(lastIndex, matchStart)));
        }

        // Add highlighted match
        const span = document.createElement('span');
        span.className = 'find-highlight';
        span.textContent = textNode.textContent.substring(matchStart, matchEnd);
        frag.appendChild(span);
        findReplaceState.matches.push(span);

        lastIndex = matchEnd;
      });

      // Add remaining text
      if (lastIndex < textNode.textContent.length) {
        frag.appendChild(document.createTextNode(textNode.textContent.substring(lastIndex)));
      }

      parent.replaceChild(frag, textNode);
    }

    offset = nodeEnd;
  });

  if (findReplaceState.matches.length > 0) {
    findReplaceState.currentIndex = 0;
    updateCurrentHighlight();
  }
}

function updateCurrentHighlight() {
  findReplaceState.matches.forEach((span, index) => {
    if (index === findReplaceState.currentIndex) {
      span.className = 'find-current';
      span.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      span.className = 'find-highlight';
    }
  });
}

function findNext() {
  const searchText = $('#findInput')?.value;
  if (!searchText) return;

  if (searchText !== findReplaceState.searchText) {
    highlightMatches(searchText);
    return;
  }

  if (findReplaceState.matches.length === 0) {
    highlightMatches(searchText);
    return;
  }

  findReplaceState.currentIndex = (findReplaceState.currentIndex + 1) % findReplaceState.matches.length;
  updateCurrentHighlight();
}

function findPrevious() {
  const searchText = $('#findInput')?.value;
  if (!searchText) return;

  if (searchText !== findReplaceState.searchText) {
    highlightMatches(searchText);
    return;
  }

  if (findReplaceState.matches.length === 0) {
    highlightMatches(searchText);
    return;
  }

  findReplaceState.currentIndex = (findReplaceState.currentIndex - 1 + findReplaceState.matches.length) % findReplaceState.matches.length;
  updateCurrentHighlight();
}

function replaceOne() {
  const replaceText = $('#replaceInput')?.value || '';

  if (findReplaceState.currentIndex < 0 || findReplaceState.currentIndex >= findReplaceState.matches.length) {
    return;
  }

  const currentSpan = findReplaceState.matches[findReplaceState.currentIndex];
  const textNode = document.createTextNode(replaceText);
  currentSpan.parentNode.replaceChild(textNode, currentSpan);

  // Remove from matches array
  findReplaceState.matches.splice(findReplaceState.currentIndex, 1);

  if (findReplaceState.matches.length === 0) {
    clearHighlights();
    alert('已完成所有替换');
  } else {
    if (findReplaceState.currentIndex >= findReplaceState.matches.length) {
      findReplaceState.currentIndex = 0;
    }
    updateCurrentHighlight();
  }

  saveDraft();
}

function replaceAll() {
  const replaceText = $('#replaceInput')?.value || '';
  const count = findReplaceState.matches.length;

  if (count === 0) return;

  findReplaceState.matches.forEach(span => {
    const textNode = document.createTextNode(replaceText);
    span.parentNode.replaceChild(textNode, span);
  });

  clearHighlights();
  alert(`已替换 ${count} 处`);
  saveDraft();
}

// Make functions global for onclick handlers
window.openFindReplaceDialog = openFindReplaceDialog;
window.closeFindReplaceDialog = closeFindReplaceDialog;
window.findNext = findNext;
window.findPrevious = findPrevious;
window.replaceOne = replaceOne;
window.replaceAll = replaceAll;
window.clearHighlights = clearHighlights;
