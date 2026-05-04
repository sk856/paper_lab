"""
参考文献管理模块
从桌面端 paper_write_page.py 移植的参考文献处理函数
"""
import re


# 支持数字参考文献编号的格式
NUMERIC_REFERENCE_STYLES = frozenset({'GB/T 7714', 'IEEE'})

# Outline heading patterns (from paper_write_page.py)
OUTLINE_EMPHASIS_MARKERS = ('***', '___', '**', '__', '*', '_')
OUTLINE_BULLET_PREFIX_RE = re.compile(r'^\s*[-*•]\s+(.+)$')
OUTLINE_MARKDOWN_RE = re.compile(r'^(#{1,6})\s+(.+)$')
OUTLINE_CHAPTER_RE = re.compile(r'^(第[一二三四五六七八九十百千万\d]+(章|节|部分|篇))\s*[:：]?\s*(.+)$')
OUTLINE_DECIMAL_RE = re.compile(r'^((?:\d+\.)+\d+)\s*[:：]?\s*(.+)$')
OUTLINE_SINGLE_NUMBER_RE = re.compile(r'^(\d+)(?:([、．.])\s*|\s+)(.+)$')
OUTLINE_CN_ENUM_RE = re.compile(r'^([一二三四五六七八九十百千万]+[、．.])\s*(.+)$')
OUTLINE_CN_PAREN_RE = re.compile(r'^(（[一二三四五六七八九十百千万]+）)\s*(.+)$')
OUTLINE_ARABIC_PAREN_RE = re.compile(r'^(\(\d+\))\s*(.+)$')

# Special section titles
OUTLINE_CN_ABSTRACT_TITLES = frozenset({'摘要', '中文摘要', '内容摘要'})
OUTLINE_EN_ABSTRACT_TITLES = frozenset({'abstract'})
OUTLINE_CN_KEYWORDS_TITLES = frozenset({'关键词', '关键字'})
OUTLINE_EN_KEYWORDS_TITLES = frozenset({'keywords', 'key words'})
OUTLINE_REFERENCE_TITLES = frozenset({'参考文献', 'references'})
OUTLINE_APPENDIX_TITLES = frozenset({'附录', 'appendix', 'appendices'})


def _strip_outline_emphasis(text):
    """Strip emphasis markers from outline text."""
    normalized = str(text or '').strip()
    if not normalized:
        return ''
    while True:
        changed = False
        for marker in OUTLINE_EMPHASIS_MARKERS:
            if normalized.startswith(marker) and normalized.endswith(marker) and len(normalized) > len(marker) * 2:
                inner = normalized[len(marker):-len(marker)].strip()
                if inner:
                    normalized = inner
                    changed = True
                    break
        if not changed:
            return normalized


def _normalize_special_heading_plain_text(text):
    """Normalize special heading text for classification."""
    normalized = re.sub(r'\s+', ' ', str(text or '').strip())
    normalized = normalized.strip('：:').strip()
    return normalized.lower()


def _classify_plain_special_heading(text):
    """Classify special heading types."""
    plain = _normalize_special_heading_plain_text(text)
    if plain in OUTLINE_CN_ABSTRACT_TITLES:
        return 'cn_abstract'
    if plain in OUTLINE_EN_ABSTRACT_TITLES:
        return 'en_abstract'
    if plain in OUTLINE_CN_KEYWORDS_TITLES:
        return 'cn_keywords'
    if plain in OUTLINE_EN_KEYWORDS_TITLES:
        return 'en_keywords'
    if plain in OUTLINE_REFERENCE_TITLES:
        return 'reference'
    if plain in OUTLINE_APPENDIX_TITLES:
        return 'appendix'
    return None


def _analyze_outline_heading(line):
    """Analyze if a line is an outline heading and return its metadata."""
    text = _strip_outline_emphasis(line)
    bullet_match = OUTLINE_BULLET_PREFIX_RE.match(text)
    if bullet_match:
        text = _strip_outline_emphasis(bullet_match.group(1).strip())
    if not text or len(text) > 160:
        return None

    # Markdown heading
    markdown = OUTLINE_MARKDOWN_RE.match(text)
    if markdown:
        hashes = markdown.group(1)
        label_text = markdown.group(2).strip()
        if not label_text:
            return None
        return {
            'title': f'{hashes} {label_text}',
            'level': min(len(hashes), 3),
            'prefix': hashes,
            'body': label_text,
            'style': 'markdown',
        }

    # Chapter heading
    chapter = OUTLINE_CHAPTER_RE.match(text)
    if chapter:
        prefix = chapter.group(1).strip()
        label_text = chapter.group(3).strip()
        if not label_text:
            return None
        return {
            'title': f'{prefix} {label_text}',
            'level': 1,
            'prefix': prefix,
            'body': label_text,
            'style': 'chapter',
        }

    # Decimal heading (e.g., 1.1.1)
    decimal = OUTLINE_DECIMAL_RE.match(text)
    if decimal:
        prefix = decimal.group(1).strip()
        label_text = decimal.group(2).strip()
        if not label_text:
            return None
        depth = prefix.count('.')
        return {
            'title': f'{prefix} {label_text}',
            'level': min(depth + 1, 3),
            'prefix': prefix,
            'body': label_text,
            'style': 'decimal',
        }

    # Single number heading
    single_number = OUTLINE_SINGLE_NUMBER_RE.match(text)
    if single_number:
        prefix = single_number.group(1).strip()
        label_text = single_number.group(3).strip()
        if not label_text:
            return None
        return {
            'title': f'{prefix} {label_text}',
            'level': 1,
            'prefix': prefix,
            'body': label_text,
            'style': 'single_number',
        }

    # Chinese enumeration
    cn_enum = OUTLINE_CN_ENUM_RE.match(text)
    if cn_enum:
        prefix = cn_enum.group(1).strip()
        label_text = cn_enum.group(2).strip()
        if not label_text:
            return None
        return {
            'title': f'{prefix} {label_text}',
            'level': 1,
            'prefix': prefix,
            'body': label_text,
            'style': 'cn_enum',
        }

    # Chinese parenthesis
    cn_paren = OUTLINE_CN_PAREN_RE.match(text)
    if cn_paren:
        prefix = cn_paren.group(1).strip()
        label_text = cn_paren.group(2).strip()
        if not label_text:
            return None
        return {
            'title': f'{prefix} {label_text}',
            'level': 2,
            'prefix': prefix,
            'body': label_text,
            'style': 'cn_paren',
        }

    # Arabic parenthesis
    arabic_paren = OUTLINE_ARABIC_PAREN_RE.match(text)
    if arabic_paren:
        prefix = arabic_paren.group(1).strip()
        label_text = arabic_paren.group(2).strip()
        if not label_text:
            return None
        return {
            'title': f'{prefix} {label_text}',
            'level': 3,
            'prefix': prefix,
            'body': label_text,
            'style': 'arabic_paren',
        }

    # Plain special heading
    plain_special_kind = _classify_plain_special_heading(text)
    if plain_special_kind:
        level = 2 if plain_special_kind in {'cn_keywords', 'en_keywords'} else 1
        return {
            'title': text,
            'level': level,
            'prefix': '',
            'body': text,
            'style': 'plain_special',
        }

    return None


def normalize_section_body(text):
    """
    规范化章节内容，移除所有大纲标题行
    这是移除生成内容中章节标题的关键函数
    """
    lines = []
    for raw_line in (text or '').splitlines():
        if _analyze_outline_heading(raw_line):
            continue
        lines.append(raw_line)
    # 保留首行缩进，同时去除空白行
    return '\n'.join(lines).strip('\n')


def normalize_reference_entry_text(text):
    """规范化参考文献条目文本"""
    normalized = normalize_section_body(text or '')
    # 移除编号前缀 [1] 或 1. 或 1、
    normalized = re.sub(r'^\s*(?:\[(\d+)\]|(\d+)[\.、])\s*', '', normalized)
    # 合并多个空格为单个空格
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def reference_entry_key(text):
    """生成参考文献去重键"""
    return normalize_reference_entry_text(text)


def parse_reference_entries(text):
    """
    解析参考文献条目

    返回格式: [{'number': 1, 'text': '...', 'key': '...'}, ...]
    """
    normalized = str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not normalized:
        return []

    lines = normalized.split('\n')
    entries = []
    current = None
    numbered_found = False
    start_re = re.compile(r'^\s*(?:\[(\d+)\]|(\d+)[\.、])\s*(.*)$')

    def flush_current():
        if not current:
            return
        entry_text = normalize_reference_entry_text('\n'.join(current['parts']))
        if not entry_text:
            return
        entries.append(
            {
                'number': current['number'],
                'text': entry_text,
                'key': reference_entry_key(entry_text),
            }
        )

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current and current['parts'] and current['parts'][-1] != '':
                current['parts'].append('')
            continue

        match = start_re.match(stripped)
        if match:
            numbered_found = True
            flush_current()
            current = {
                'number': int(match.group(1) or match.group(2)),
                'parts': [match.group(3).strip()],
            }
            continue

        if current:
            current['parts'].append(stripped)
            continue

        entry_text = normalize_reference_entry_text(stripped)
        if entry_text:
            entries.append({'number': None, 'text': entry_text, 'key': reference_entry_key(entry_text)})

    flush_current()
    if numbered_found:
        return [entry for entry in entries if entry.get('key')]

    # 回退方案：按段落分割
    fallback_entries = []
    for block in re.split(r'\n\s*\n', normalized):
        entry_text = normalize_reference_entry_text(block)
        if entry_text:
            fallback_entries.append({'number': None, 'text': entry_text, 'key': reference_entry_key(entry_text)})
    return fallback_entries or [entry for entry in entries if entry.get('key')]


def parse_citation_numbers(content):
    """
    解析引用编号，支持 [1], [1,2], [1-3], [1,3-5,7] 等格式

    返回: [1, 2, 3, ...]
    """
    numbers = []
    for part in re.split(r'[,，、]', str(content or '').strip()):
        token = part.strip()
        if not token:
            continue
        range_match = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', token)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start <= end:
                numbers.extend(range(start, end + 1))
            else:
                numbers.extend(range(end, start + 1))
            continue
        if token.isdigit():
            numbers.append(int(token))
    return numbers


def format_citation_numbers(numbers):
    """
    格式化引用编号列表为字符串

    输入: [1, 2, 3, 5, 7, 8, 9]
    输出: "1-3,5,7-9"
    """
    ordered = sorted(dict.fromkeys(int(number) for number in numbers if int(number) > 0))
    if not ordered:
        return ''
    parts = []
    start = ordered[0]
    prev = ordered[0]
    for number in ordered[1:]:
        if number == prev + 1:
            prev = number
            continue
        parts.append(f'{start}-{prev}' if start != prev else str(start))
        start = prev = number
    parts.append(f'{start}-{prev}' if start != prev else str(start))
    return ','.join(parts)


def collect_citation_reference_keys(text, number_to_entry):
    """
    从文本中收集引用的参考文献键

    参数:
        text: 文本内容
        number_to_entry: 编号到条目的映射 {1: {'key': '...', ...}, ...}

    返回: ['key1', 'key2', ...]
    """
    if not text or not number_to_entry:
        return []

    keys = []
    seen = set()
    for match in re.finditer(r'\[([^\[\]]+)\]', text):
        for number in parse_citation_numbers(match.group(1)):
            entry = number_to_entry.get(number)
            key = entry.get('key', '') if entry else ''
            if not key or key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


def rewrite_citations_with_entry_map(text, number_to_entry):
    """
    重写文本中的引用编号

    参数:
        text: 文本内容
        number_to_entry: 编号映射，每个条目包含 'new_number' 字段

    返回: 重写后的文本
    """
    if not text or not number_to_entry:
        return text

    def replace(match):
        source_numbers = parse_citation_numbers(match.group(1))
        if not source_numbers:
            return match.group(0)
        target_numbers = []
        for number in source_numbers:
            entry = number_to_entry.get(number)
            target_number = entry.get('new_number') if entry else None
            target_numbers.append(target_number if target_number else number)
        formatted = format_citation_numbers(target_numbers)
        return f'[{formatted}]' if formatted else match.group(0)

    return re.sub(r'\[([^\[\]]+)\]', replace, text)


def build_reference_body_from_entries(entries):
    """
    从参考文献条目列表生成参考文献章节内容

    输入: [{'text': '...', 'key': '...', 'new_number': 1}, ...]
    输出: "[1] ...\n[2] ...\n[3] ..."
    """
    lines = []
    for index, entry in enumerate(entries, start=1):
        entry_text = normalize_reference_entry_text(entry.get('text', ''))
        if not entry_text:
            continue
        lines.append(f'[{index}] {entry_text}')
    return '\n'.join(lines).strip()


def merge_reference_entry_lists(*groups):
    """
    合并多个参考文献列表并去重

    返回: [{'text': '...', 'key': '...'}, ...]
    """
    merged = []
    seen = set()
    for group in groups:
        for entry in group or []:
            entry_text = normalize_reference_entry_text(entry.get('text', ''))
            entry_key = reference_entry_key(entry_text)
            if not entry_key or entry_key in seen:
                continue
            seen.add(entry_key)
            merged.append({'text': entry_text, 'key': entry_key})
    return merged


def build_reference_number_map(entries):
    """
    构建参考文献编号映射表

    输入: [{'number': 1, 'text': '...', 'key': '...'}, ...]
    输出: {1: {'number': 1, 'text': '...', 'key': '...'}, ...}
    """
    number_map = {}
    next_auto_number = 1
    for entry in entries or []:
        entry_text = normalize_reference_entry_text(entry.get('text', ''))
        entry_key = reference_entry_key(entry_text)
        if not entry_key:
            continue
        number = entry.get('number')
        if not isinstance(number, int) or number <= 0:
            # 自动分配编号
            while next_auto_number in number_map:
                next_auto_number += 1
            number = next_auto_number
        number_map[number] = {
            'number': number,
            'text': entry_text,
            'key': entry_key,
        }
        next_auto_number = max(next_auto_number, number + 1)
    return number_map


def is_markdown_rule_line(line):
    """检查是否是 Markdown 分隔线"""
    stripped = str(line or '').strip()
    if not stripped:
        return False
    # 匹配 ---, ***, ___ 等
    return bool(re.match(r'^[-*_]{3,}$', stripped))


def split_reference_heading_line(line):
    """
    检查并分割参考文献标题行

    返回: {'inline_rest': '...'} 或 None
    """
    text = str(line or '').strip()
    if not text:
        return None

    # 移除 Markdown 标题前缀
    candidate = re.sub(r'^#{1,6}\s+', '', text).strip()

    # 构建强调标记模式
    emphasis_markers = r'(?:\*{1,3}|_{1,3})'

    # 匹配参考文献标题
    match = re.match(
        rf'^(?:{emphasis_markers}\s*)?(?:[【\[]\s*)?'
        rf'(?P<label>参考文献|references|bibliography)'
        rf'(?:\s*[】\]])?(?:\s*{emphasis_markers})?\s*(?P<trailing>.*)$',
        candidate,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    trailing = (match.group('trailing') or '').strip()
    if not trailing:
        return {'inline_rest': ''}

    if trailing[:1] not in ':：（(':
        return None

    remainder = trailing
    if remainder[:1] in ':：':
        remainder = remainder[1:].lstrip()

    # 移除括号注释
    while remainder:
        note_match = re.match(r'^[（(][^()（）\n]{0,200}[)）]\s*', remainder)
        if not note_match:
            break
        remainder = remainder[note_match.end():].lstrip()
        if remainder[:1] in ':：':
            remainder = remainder[1:].lstrip()

    return {'inline_rest': remainder}


def find_reference_block_start(lines):
    """
    查找参考文献块的起始位置

    返回: (block_start, heading_index) 或 (None, None)
    """
    if not lines:
        return None, None

    for heading_index, line in enumerate(lines):
        if split_reference_heading_line(line) is None:
            continue

        # 向上回溯，跳过空行和分隔线
        block_start = heading_index
        while block_start > 0 and not str(lines[block_start - 1] or '').strip():
            block_start -= 1
        if block_start > 0 and is_markdown_rule_line(lines[block_start - 1]):
            block_start -= 1
            while block_start > 0 and not str(lines[block_start - 1] or '').strip():
                block_start -= 1
        return block_start, heading_index

    return None, None


def find_trailing_reference_entries_start(lines):
    """
    查找尾部参考文献条目的起始位置

    返回: index 或 None
    """
    if not lines:
        return None

    start_re = re.compile(r'^\s*(?:\[(\d+)\]|(\d+)[\.、])\s+\S')
    candidates = []
    for index, line in enumerate(lines):
        if not start_re.match(str(line or '')):
            continue
        # 前一行必须是空行或分隔线
        if index > 0:
            previous = str(lines[index - 1] or '')
            if previous.strip() and not is_markdown_rule_line(previous):
                continue
        candidates.append(index)

    # 验证候选位置
    for index in candidates:
        suffix = '\n'.join(lines[index:]).strip()
        if not suffix:
            continue
        entries = parse_reference_entries(suffix)
        if not entries:
            continue
        # 检查是否有编号条目
        numbered_starts = [
            line for line in lines[index:]
            if start_re.match(str(line or ''))
        ]
        if numbered_starts:
            return index
    return None


def strip_reference_heading(text):
    """
    移除参考文献标题，保留条目内容

    输入: "# 参考文献\n[1] ...\n[2] ..."
    输出: "[1] ...\n[2] ..."
    """
    normalized = (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not normalized:
        return ''

    lines = normalized.split('\n')
    _block_start, heading_index = find_reference_block_start(lines)
    if heading_index is None:
        return normalized

    heading_meta = split_reference_heading_line(lines[heading_index]) or {}
    entry_lines = []
    inline_rest = (heading_meta.get('inline_rest') or '').strip()
    if inline_rest:
        entry_lines.append(inline_rest)
    entry_lines.extend(lines[heading_index + 1:])

    # 移除开头的分隔线和空行
    while entry_lines and is_markdown_rule_line(entry_lines[0]):
        entry_lines.pop(0)
    while entry_lines and not str(entry_lines[0]).strip():
        entry_lines.pop(0)
    # 移除结尾的空行
    while entry_lines and not str(entry_lines[-1]).strip():
        entry_lines.pop()

    if entry_lines:
        return '\n'.join(entry_lines).strip()
    return normalized


def strip_leading_section_title(text, section_title=''):
    """
    移除文本开头的章节标题（如果存在）

    参数:
        text: 文本内容
        section_title: 期望的章节标题（用于匹配）

    返回: 移除标题后的文本
    """
    normalized = (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not normalized:
        return ''

    lines = normalized.split('\n')
    if not lines:
        return ''

    first_line = lines[0].strip()

    # 检查第一行是否是 Markdown 标题格式
    heading_match = re.match(r'^(#{1,6})\s+(.+)$', first_line)
    if not heading_match:
        return normalized

    heading_text = heading_match.group(2).strip()

    # 调试输出
    print(f"[DEBUG] strip_leading_section_title:")
    print(f"  first_line: {first_line}")
    print(f"  heading_text: {heading_text}")
    print(f"  section_title: {section_title}")

    # 如果提供了 section_title，检查是否匹配
    if section_title:
        # 移除 section_title 中的 # 前缀
        clean_section_title = re.sub(r'^#{1,6}\s+', '', section_title.strip()).strip()
        print(f"  clean_section_title: {clean_section_title}")
        print(f"  match: {heading_text == clean_section_title}")
        # 如果标题匹配，移除第一行
        if heading_text == clean_section_title:
            remaining = '\n'.join(lines[1:]).strip()
            print(f"  [REMOVED] Title matched and removed")
            return remaining
    else:
        # 如果没有提供 section_title，移除任何看起来像章节标题的第一行
        # （通常是数字编号的标题，如 "1.1.1 xxx"）
        if re.match(r'^[\d\.]+\s+', heading_text):
            remaining = '\n'.join(lines[1:]).strip()
            print(f"  [REMOVED] Numbered title removed")
            return remaining

    print(f"  [KEPT] Title not removed")
    return normalized


def extract_references_from_section_result(text):
    """
    从章节生成结果中提取参考文献

    返回: (clean_content, references_text)
    """
    normalized = (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not normalized:
        return '', ''

    lines = normalized.split('\n')

    # 方法1：查找参考文献标题块
    block_start, heading_index = find_reference_block_start(lines)
    if heading_index is not None:
        body_part = '\n'.join(lines[:block_start]).strip()
        references_part = '\n'.join(lines[block_start:]).strip()
        references_text = strip_reference_heading(references_part)
        return normalize_section_body(body_part), normalize_section_body(references_text)

    # 方法2：查找尾部参考文献条目
    trailing_start = find_trailing_reference_entries_start(lines)
    if trailing_start is not None:
        body_part = '\n'.join(lines[:trailing_start]).strip()
        references_text = '\n'.join(lines[trailing_start:]).strip()
        return normalize_section_body(body_part), normalize_section_body(references_text)

    # 方法3：没有参考文献
    return normalize_section_body(normalized), ''


def collect_citation_reference_keys(text, number_to_entry):
    """
    收集文本中引用的参考文献key列表（按首次引用顺序）

    参数:
        text: 章节内容文本
        number_to_entry: 编号到参考文献条目的映射 {1: {'key': 'xxx', 'text': '...', ...}, ...}

    返回:
        引用的参考文献key列表（按首次引用顺序）
    """
    if not text or not number_to_entry:
        return []

    keys = []
    seen = set()

    # 查找所有引用 [1], [2,3], [1-3] 等格式
    for match in re.finditer(r'\[([^\[\]]+)\]', text):
        citation_text = match.group(1)
        numbers = parse_citation_numbers(citation_text)

        for number in numbers:
            entry = number_to_entry.get(number)
            key = entry.get('key', '') if entry else ''
            if not key or key in seen:
                continue
            seen.add(key)
            keys.append(key)

    return keys
