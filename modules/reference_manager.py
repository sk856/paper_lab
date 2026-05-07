"""
参考文献管理模块
从桌面端 paper_write_page.py 移植的参考文献处理函数
"""
import html
import re
import urllib.error
import urllib.parse
import urllib.request


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
    normalized = REFERENCE_VALIDATION_MARKER_RE.sub('', normalized)
    # 合并多个空格为单个空格
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def reference_entry_key(text):
    """生成参考文献去重键"""
    return normalize_reference_entry_text(text)


REFERENCE_LINK_RE = re.compile(
    r'(?:https?://|www\.)[^\s<>"\'\]\[）)；;，。、]+',
    re.IGNORECASE,
)

REFERENCE_URL_RE = re.compile(
    r'(?:https?://|www\.)[^\s<>"\'\]\[）)；;，。、]+',
    re.IGNORECASE,
)

REFERENCE_VALIDATION_MARKER_RE = re.compile(
    r'\s*链接：\[(?:待补充真实可访问链接|疑似无效：[^\]]+)\]\s*$'
)

REFERENCE_VALIDATION_CACHE = {}
REFERENCE_SEARCH_CACHE = {}


def reference_entry_has_link(text):
    """判断参考文献条目是否包含可核验链接线索。"""
    return bool(REFERENCE_LINK_RE.search(str(text or '')))


def extract_reference_urls(text):
    """提取参考文献条目中的可访问 URL。"""
    urls = []
    seen = set()
    for match in REFERENCE_URL_RE.finditer(str(text or '')):
        url = _normalize_reference_url(match.group(0))
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _normalize_reference_url(url):
    normalized = str(url or '').strip().strip('\'"<>[]()（）')
    normalized = normalized.rstrip('，,。；;、')
    while normalized.endswith('.'):
        normalized = normalized[:-1]
    if normalized.lower().startswith('www.'):
        normalized = 'https://' + normalized
    return normalized


def _compact_reference_match_text(text):
    normalized = html.unescape(str(text or ''))
    normalized = normalized.replace('—', '-').replace('－', '-').replace('–', '-')
    return re.sub(r'[^0-9a-zA-Z\u4e00-\u9fff]+', '', normalized).lower()


def _reference_title_candidate(text):
    entry_text = normalize_reference_entry_text(text)
    entry_text = REFERENCE_URL_RE.sub('', entry_text)
    quoted_titles = re.findall(r'《([^》]{2,120})》', entry_text)
    if quoted_titles:
        return max(quoted_titles, key=len).strip()

    title_match = re.search(
        r'\.\s*(.+?)(?:\[(?:J|M|D|C|N|R|S|P|A|Z|EB/OL|R/OL|OL)[^\]]*\]|(?:\.\s*)?\d{4}\s*[,，(（]|$)',
        entry_text,
        flags=re.IGNORECASE,
    )
    if title_match:
        return title_match.group(1).strip(' .。:：')

    source_match = re.search(r'^(.+?)(?:\[(?:J|M|D|C|N|R|S|P|A|Z|EB/OL|R/OL|OL)[^\]]*\])', entry_text, flags=re.IGNORECASE)
    if source_match:
        return source_match.group(1).strip(' .。:：')

    return entry_text[:120].strip(' .。:：')


def _iter_title_ngrams(text, size):
    if len(text) < size:
        return set()
    return {text[index:index + size] for index in range(0, len(text) - size + 1)}


def _reference_title_matches_page(entry_text, page_title):
    title_candidate = _compact_reference_match_text(_reference_title_candidate(entry_text))
    page_candidate = _compact_reference_match_text(page_title)
    if not title_candidate or not page_candidate:
        return True
    if title_candidate in page_candidate or page_candidate in title_candidate:
        return True
    if len(title_candidate) < 8:
        return title_candidate in page_candidate

    long_matches = _iter_title_ngrams(title_candidate, 12) & _iter_title_ngrams(page_candidate, 12)
    if long_matches:
        return True
    medium_matches = _iter_title_ngrams(title_candidate, 8) & _iter_title_ngrams(page_candidate, 8)
    return len(medium_matches) >= 2


def _fetch_reference_url_metadata(url, timeout=5):
    cache_key = (url, int(timeout))
    if cache_key in REFERENCE_VALIDATION_CACHE:
        return REFERENCE_VALIDATION_CACHE[cache_key]

    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.6',
        },
        method='GET',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(300000)
            charset = response.headers.get_content_charset() or 'utf-8'
            page_text = body.decode(charset, errors='replace')
            title_match = re.search(r'<title[^>]*>(.*?)</title>', page_text, flags=re.IGNORECASE | re.DOTALL)
            title = ''
            if title_match:
                title = re.sub(r'\s+', ' ', html.unescape(title_match.group(1))).strip()
            result = {
                'ok': 200 <= int(response.status) < 400,
                'status': int(response.status),
                'url': response.geturl(),
                'title': title,
                'error': '',
            }
    except urllib.error.HTTPError as exc:
        result = {'ok': False, 'status': int(exc.code), 'url': url, 'title': '', 'error': f'HTTP {exc.code}'}
    except Exception as exc:
        result = {'ok': False, 'status': 0, 'url': url, 'title': '', 'error': exc.__class__.__name__}

    REFERENCE_VALIDATION_CACHE[cache_key] = result
    return result


def _reference_search_terms(entry_text):
    title = _reference_title_candidate(entry_text)
    title = re.sub(r'\s+', ' ', title).strip(' .。:：')
    if not title or len(title) < 4:
        return []
    terms = [title]
    if '金融科技发展规划' in title:
        terms.append('中国人民银行 金融科技发展规划 2022 2025')
    if '供应链金融' in title:
        terms.append('规范发展供应链金融 支持供应链产业链稳定循环和优化升级')
    return list(dict.fromkeys(terms))


def _reference_search_domains(entry_text, urls):
    domains = []
    for url in urls:
        try:
            host = urllib.parse.urlparse(url).netloc.lower()
        except Exception:
            host = ''
        if host:
            domains.append(host)
    lowered = str(entry_text or '').lower()
    if '中国人民银行' in entry_text or '人民银行' in entry_text or 'pbc' in lowered:
        domains.extend(['pbc.gov.cn', 'gov.cn'])
    if '国务院' in entry_text or '工业和信息化部' in entry_text or '工信部' in entry_text or 'gov.cn' in lowered:
        domains.extend(['gov.cn'])
    return list(dict.fromkeys(domain.lstrip('www.') for domain in domains if domain))


def _decode_search_result_url(url):
    value = html.unescape(str(url or '')).strip()
    if not value:
        return ''
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ('uddg', 'u', 'url', 'q'):
        if query.get(key):
            candidate = urllib.parse.unquote(query[key][0])
            if candidate.startswith(('http://', 'https://')):
                return candidate
    if value.startswith(('http://', 'https://')):
        return value
    return ''


def _extract_search_urls(page_text):
    urls = []
    for match in re.finditer(r'href=["\']([^"\']+)["\']', page_text or '', flags=re.I):
        url = _decode_search_result_url(match.group(1))
        if not url:
            continue
        host = urllib.parse.urlparse(url).netloc.lower()
        if not host or any(blocked in host for blocked in ('bing.com', 'duckduckgo.com', 'baidu.com', 'google.com')):
            continue
        if url not in urls:
            urls.append(url)
    return urls


def _search_reference_candidates(query, timeout=6):
    cache_key = query.strip().lower()
    if cache_key in REFERENCE_SEARCH_CACHE:
        return REFERENCE_SEARCH_CACHE[cache_key]

    endpoints = [
        'https://duckduckgo.com/html/?' + urllib.parse.urlencode({'q': query}),
        'https://www.bing.com/search?' + urllib.parse.urlencode({'q': query}),
    ]
    results = []
    for endpoint in endpoints:
        request = urllib.request.Request(
            endpoint,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.6',
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or 'utf-8'
                page_text = response.read(500000).decode(charset, errors='replace')
        except Exception:
            continue
        for url in _extract_search_urls(page_text):
            if url not in results:
                results.append(url)
            if len(results) >= 8:
                break
        if results:
            break

    REFERENCE_SEARCH_CACHE[cache_key] = results
    return results


def _url_host_matches_domain(url, domain):
    try:
        host = urllib.parse.urlparse(url).netloc.lower().lstrip('www.')
    except Exception:
        return False
    expected = str(domain or '').lower().lstrip('www.')
    return bool(host and expected and (host == expected or host.endswith('.' + expected)))


def repair_reference_entry_url(text):
    """按题名搜索并返回修复后的参考文献条目；失败返回空字符串。"""
    entry_text = normalize_reference_entry_text(text)
    if not entry_text:
        return ''

    urls = extract_reference_urls(entry_text)
    valid_urls = [url for url in urls if _reference_url_is_valid_for_entry(entry_text, url)]
    if valid_urls:
        cleaned = entry_text
        for url in urls:
            if url not in valid_urls:
                cleaned = _remove_reference_url(cleaned, url)
        return re.sub(r'\s+', ' ', cleaned).strip()

    terms = _reference_search_terms(entry_text)
    if not terms:
        return ''
    domains = _reference_search_domains(entry_text, urls)
    queries = []
    for term in terms:
        for domain in domains:
            queries.append(f'{term} site:{domain}')
        queries.append(term)

    for query in dict.fromkeys(queries):
        site_match = re.search(r'\bsite:([A-Za-z0-9.-]+)', query)
        site_domain = site_match.group(1) if site_match else ''
        for candidate_url in _search_reference_candidates(query):
            if site_domain and not _url_host_matches_domain(candidate_url, site_domain):
                continue
            if not _reference_url_is_valid_for_entry(entry_text, candidate_url):
                continue
            cleaned = entry_text
            for url in urls:
                cleaned = _remove_reference_url(cleaned, url)
            cleaned = REFERENCE_VALIDATION_MARKER_RE.sub('', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip(' ，,。；;')
            return f'{cleaned} {candidate_url}'.strip()
    return ''


def validate_reference_entry_link(text):
    """校验参考文献 URL 是否能访问且页面标题是否大致匹配条目题名。"""
    urls = extract_reference_urls(text)
    if not urls:
        return '缺少真实可访问链接'

    invalid_reasons = []
    mismatch_reasons = []
    for url in urls:
        metadata = _fetch_reference_url_metadata(url)
        if not metadata.get('ok'):
            status = metadata.get('status')
            reason = f'URL 访问失败（HTTP {status}）' if status else 'URL 无法自动访问'
            invalid_reasons.append(reason)
            continue
        title = metadata.get('title', '')
        if _reference_title_matches_page(text, title):
            return ''
        if title:
            mismatch_reasons.append(f'页面标题不匹配：{title[:80]}')
        else:
            return ''

    reasons = mismatch_reasons or invalid_reasons
    return '；'.join(dict.fromkeys(reasons)) if reasons else ''


def _reference_url_is_valid_for_entry(entry_text, url):
    metadata = _fetch_reference_url_metadata(url)
    if not metadata.get('ok'):
        return False
    title = metadata.get('title', '')
    return not title or _reference_title_matches_page(entry_text, title)


def _remove_reference_url(text, url):
    if not url:
        return text
    variants = {url}
    if url.lower().startswith('https://'):
        variants.add(url[8:])
    if url.lower().startswith('http://'):
        variants.add(url[7:])
    result = str(text or '')
    for variant in sorted(variants, key=len, reverse=True):
        result = re.sub(re.escape(variant), '', result)
    result = re.sub(r'\s+', ' ', result).strip()
    result = re.sub(r'\s+([，,。；;])', r'\1', result)
    return result.strip(' ，,。；;')


def sanitize_reference_entry_for_output(text):
    """修复并仅保留带真实可访问 URL 的参考文献条目。

    无链接、链接不可访问、或链接页面与题名不匹配且无法修复的条目直接丢弃。
    如果一条条目包含多个 URL，仅保留其中有效的 URL。
    """
    entry_text = repair_reference_entry_url(text)
    if not entry_text:
        return ''
    urls = extract_reference_urls(entry_text)
    if not urls:
        return ''

    valid_urls = []
    invalid_urls = []
    for url in urls:
        if _reference_url_is_valid_for_entry(entry_text, url):
            valid_urls.append(url)
        else:
            invalid_urls.append(url)
    if not valid_urls:
        return ''

    cleaned = entry_text
    for url in invalid_urls:
        cleaned = _remove_reference_url(cleaned, url)
    cleaned = REFERENCE_VALIDATION_MARKER_RE.sub('', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def ensure_reference_entry_link_marker(text):
    """兼容旧调用：修复可修复条目，失败返回空，不再输出校验标记。"""
    return sanitize_reference_entry_for_output(text)


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


def rewrite_citations_with_entry_map(text, number_to_entry, drop_missing=False):
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
            if target_number:
                target_numbers.append(target_number)
            elif not drop_missing:
                target_numbers.append(number)
        formatted = format_citation_numbers(target_numbers)
        if formatted:
            return f'[{formatted}]'
        return '' if drop_missing else match.group(0)

    return re.sub(r'\[([^\[\]]+)\]', replace, text)


def build_reference_body_from_entries(entries):
    """
    从参考文献条目列表生成参考文献章节内容

    输入: [{'text': '...', 'key': '...', 'number': 1}, ...] 或 [{'text': '...', 'key': '...', 'new_number': 1}, ...]
    输出: "[1] ...\n[2] ...\n[3] ..."
    """
    lines = []
    for entry in entries or []:
        entry_text = sanitize_reference_entry_for_output(entry.get('text', ''))
        if not entry_text:
            continue
        # 无效条目会被剔除，因此输出时重新连续编号，避免出现编号断档。
        entry_number = len(lines) + 1
        lines.append(f'[{entry_number}]{entry_text}')
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
            entry_text = sanitize_reference_entry_for_output(entry.get('text', ''))
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
        entry_text = sanitize_reference_entry_for_output(entry.get('text', ''))
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


def build_reference_occurrence_runs(entries):
    """
    将参考文献条目按编号递增区间拆成批次。

    当后续补写前面的章节时，旧逻辑可能会追加一段从较小编号重新开始的参考文献。
    例如 [1][2][3][4][3][4] 会被拆成两段，第二段应插回旧编号 3 附近再重排。
    """
    runs = []
    current = []
    previous_number = None
    next_auto_number = 1
    for position, entry in enumerate(entries or []):
        entry_text = sanitize_reference_entry_for_output(entry.get('text', ''))
        entry_key = reference_entry_key(entry_text)
        if not entry_key:
            continue
        number = entry.get('number')
        if not isinstance(number, int) or number <= 0:
            while any(next_auto_number == item.get('number') for run in runs for item in run) or any(
                next_auto_number == item.get('number') for item in current
            ):
                next_auto_number += 1
            number = next_auto_number

        if current and previous_number is not None and number <= previous_number:
            runs.append(current)
            current = []

        current.append({
            'number': number,
            'text': entry_text,
            'key': entry_key,
            'position': position,
            'run_index': len(runs),
        })
        previous_number = number
        next_auto_number = max(next_auto_number, number + 1)

    if current:
        runs.append(current)
    return runs


def order_reference_occurrences_for_repair(entries):
    """
    生成用于修复重复旧编号的参考文献出现顺序。

    第一批按原顺序保留；后续编号回退的批次按其首个旧编号插入到已有列表中，
    这样补写中间章节追加出来的参考文献会回到正文顺序附近。
    """
    ordered = []
    for run in build_reference_occurrence_runs(entries):
        if not ordered:
            ordered.extend(run)
            continue
        start_number = run[0].get('number', 0)
        current_run_index = run[0].get('run_index', 0)
        insert_at = len(ordered)
        for index, occurrence in enumerate(ordered):
            if occurrence.get('run_index', 0) >= current_run_index:
                continue
            if occurrence.get('number', 0) >= start_number:
                insert_at = index
                break
        ordered[insert_at:insert_at] = run
    return ordered


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


def find_max_reference_number(sections, before_position):
    """
    查找指定位置之前所有章节的最大参考文献编号

    参数:
        sections: 章节列表 [{'title': '...', 'content': '...'}, ...]
        before_position: 位置索引（不包含该位置）

    返回:
        最大编号（int），如果没有找到则返回0
    """
    max_number = 0

    for i in range(before_position):
        if i >= len(sections):
            break

        section = sections[i]
        content = section.get('content', '')
        if not content:
            continue

        # 查找所有引用编号
        for match in re.finditer(r'\[([^\[\]]+)\]', content):
            citation_text = match.group(1)
            numbers = parse_citation_numbers(citation_text)
            if numbers:
                max_number = max(max_number, max(numbers))

    return max_number


def collect_references_from_sections(sections, start_position, end_position=None):
    """
    收集指定范围内章节的所有参考文献引用

    参数:
        sections: 章节列表
        start_position: 起始位置（包含）
        end_position: 结束位置（不包含），None表示到末尾

    返回:
        按引用顺序排列的参考文献key列表
    """
    if end_position is None:
        end_position = len(sections)

    all_keys = []
    seen = set()

    for i in range(start_position, end_position):
        if i >= len(sections):
            break

        section = sections[i]
        content = section.get('content', '')
        if not content:
            continue

        # 提取参考文献
        _, references_text = extract_references_from_section_result(content)
        if references_text:
            entries = parse_reference_entries(references_text)
            number_to_entry = build_reference_number_map(entries)

            # 收集该章节引用的参考文献key
            keys = collect_citation_reference_keys(content, number_to_entry)
            for key in keys:
                if key not in seen:
                    seen.add(key)
                    all_keys.append(key)

    return all_keys


def renumber_references(content, old_to_new_map, drop_missing=False):
    """
    根据编号映射更新章节中的引用编号

    参数:
        content: 章节内容
        old_to_new_map: 旧编号到新编号的映射 {1: 3, 2: 4, ...}

    返回:
        更新后的内容
    """
    if not content or not old_to_new_map:
        return content

    def replace_citation(match):
        citation_text = match.group(1)
        numbers = parse_citation_numbers(citation_text)

        # 映射到新编号
        new_numbers = []
        for num in numbers:
            new_num = old_to_new_map.get(num, num)
            if num in old_to_new_map or not drop_missing:
                new_numbers.append(new_num)

        # 格式化新编号
        formatted = format_citation_numbers(new_numbers)
        if formatted:
            return '[' + formatted + ']'
        return '' if drop_missing else match.group(0)

    # 替换所有引用
    updated = re.sub(r'\[([^\[\]]+)\]', replace_citation, content)
    return updated


def rewrite_citations_with_number_map(text, old_to_new_map, drop_missing=False):
    """
    使用旧编号到新编号的映射重写正文引用。
    """
    if not text or not old_to_new_map:
        return text

    def replace(match):
        source_numbers = parse_citation_numbers(match.group(1))
        if not source_numbers:
            return match.group(0)
        target_numbers = [
            old_to_new_map.get(number, number)
            for number in source_numbers
            if number in old_to_new_map or not drop_missing
        ]
        formatted = format_citation_numbers(target_numbers)
        if formatted:
            return f'[{formatted}]'
        return '' if drop_missing else match.group(0)

    return re.sub(r'\[([^\[\]]+)\]', replace, text)


def determine_reference_mode(section_title, all_sections):
    """
    判断参考文献处理模式

    参数:
        section_title: 当前章节标题
        all_sections: 所有章节列表 [{'title': '...', 'content': '...'}, ...]

    返回:
        'append': 新增章节，追加参考文献
        'reorder': 修改章节，重新排序参考文献
    """
    for section in all_sections:
        if section.get('title') == section_title:
            content = section.get('content', '').strip()
            if content:
                return 'reorder'  # 已有内容，是修改操作
    return 'append'  # 无内容，是新增操作


def is_reference_section(section_title):
    """
    判断是否是参考文献章节

    参数:
        section_title: 章节标题

    返回:
        True: 是参考文献章节
        False: 不是参考文献章节
    """
    if not section_title:
        return False

    title_lower = section_title.lower().strip().strip('#').strip()
    return title_lower in {'参考文献', 'references', 'bibliography', 'reference'}


def process_references_append_mode(section_title, new_content, all_sections, reference_style):
    """
    追加模式：新增章节时续写参考文献编号

    参数:
        section_title: 当前章节标题
        new_content: AI生成的新内容
        all_sections: 所有章节列表
        reference_style: 引用格式（如 'GB/T 7714'）

    返回:
        {
            'cleaned_content': 清理后的章节内容,
            'references_to_append': 需要追加的参考文献条目列表,
            'updated_sections': []  # 追加模式不更新其他章节
        }
    """
    # 找到当前章节的位置
    current_position = None
    for i, section in enumerate(all_sections):
        if section.get('title') == section_title:
            current_position = i
            break

    if current_position is None:
        current_position = len(all_sections)

    # 从整篇文章取最大编号。批量写作中断后补写前文小节时，如果只看当前位置之前，
    # 很容易把新参考文献编号写到已有编号上，后续整理就无法唯一映射正文引用。
    max_number = find_max_reference_number(all_sections, len(all_sections))

    # 提取新章节的参考文献
    clean_content, references_text = extract_references_from_section_result(new_content)
    clean_content = normalize_section_body(clean_content)

    if not references_text:
        return {
            'cleaned_content': clean_content,
            'references_to_append': [],
            'updated_sections': []
        }

    # 解析新参考文献条目
    new_entries = parse_reference_entries(references_text)

    # 重新编号：从 max_number + 1 开始
    old_to_new_map = {}
    renumbered_entries = []
    next_number = max_number + 1

    for entry in new_entries:
        old_number = entry.get('number', 0)
        entry_text = sanitize_reference_entry_for_output(entry.get('text', ''))
        entry_key = reference_entry_key(entry_text)

        if not entry_key:
            continue

        old_to_new_map[old_number] = next_number
        renumbered_entries.append({
            'number': next_number,
            'text': entry_text,
            'key': entry_key
        })
        next_number += 1

    # 更新章节内容中的引用编号
    updated_content = renumber_references(clean_content, old_to_new_map, drop_missing=True)

    return {
        'cleaned_content': updated_content,
        'references_to_append': renumbered_entries,
        'updated_sections': []
    }


def process_references_reorder_mode(section_title, new_content, all_sections, reference_style):
    """
    重排模式：修改章节时重新排序参考文献

    参数:
        section_title: 当前章节标题
        new_content: AI生成的新内容
        all_sections: 所有章节列表
        reference_style: 引用格式

    返回:
        {
            'cleaned_content': 清理后的章节内容,
            'full_references': 完整的参考文献条目列表,
            'updated_sections': 需要更新的其他章节列表
        }
    """
    # 找到当前章节的位置
    current_position = None
    for i, section in enumerate(all_sections):
        if section.get('title') == section_title:
            current_position = i
            break

    if current_position is None:
        current_position = len(all_sections)

    # 提取新章节的参考文献
    clean_content, references_text = extract_references_from_section_result(new_content)
    clean_content = normalize_section_body(clean_content)

    # 1. 从统一的"参考文献"章节读取所有现有参考文献
    existing_ref_entries = []
    for section in all_sections:
        title = section.get('title', '')
        if is_reference_section(title):
            content = section.get('content', '')
            if content:
                existing_ref_entries = parse_reference_entries(content)
            break

    # 构建现有参考文献映射。正文中的旧编号只能通过集中参考文献章节解释。
    old_number_map = build_reference_number_map(existing_ref_entries)
    key_to_entry = {}
    for entry in existing_ref_entries:
        entry_text = sanitize_reference_entry_for_output(entry.get('text', ''))
        entry_key = reference_entry_key(entry_text)
        if entry_key:
            key_to_entry[entry_key] = {
                'text': entry_text,
                'key': entry_key,
                'number': entry.get('number', 0)
            }

    local_reference_entries = parse_reference_entries(references_text) if references_text else []
    local_number_map = build_reference_number_map(local_reference_entries)
    for entry in local_reference_entries:
        entry_text = sanitize_reference_entry_for_output(entry.get('text', ''))
        entry_key = reference_entry_key(entry_text)
        if entry_key:
            key_to_entry[entry_key] = {'text': entry_text, 'key': entry_key, 'number': 0}

    ordered_keys = []
    seen_keys = set()

    def append_keys(keys):
        for key in keys:
            if not key or key in seen_keys:
                continue
            if key not in key_to_entry:
                continue
            seen_keys.add(key)
            ordered_keys.append(key)

    # 2. 按整篇文章顺序重新收集实际引用：前文旧引用 -> 当前新引用 -> 后文旧引用。
    for i, section in enumerate(all_sections):
        title = section.get('title', '')
        if is_reference_section(title):
            continue
        if i == current_position:
            append_keys(collect_citation_reference_keys(clean_content, local_number_map))
            continue
        content = normalize_section_body(section.get('content', ''))
        append_keys(collect_citation_reference_keys(content, old_number_map))

    # 3. 当前章节生成了参考文献但正文没显式引用时，也保留这些新条目。
    for entry in local_reference_entries:
        entry_text = sanitize_reference_entry_for_output(entry.get('text', ''))
        entry_key = reference_entry_key(entry_text)
        if entry_key and entry_key not in seen_keys:
            seen_keys.add(entry_key)
            ordered_keys.append(entry_key)

    full_entries = []
    new_number_by_key = {}
    for key in ordered_keys:
        entry = key_to_entry.get(key)
        if not entry:
            continue
        new_number = len(full_entries) + 1
        new_number_by_key[key] = new_number
        full_entries.append({'text': entry['text'], 'key': key, 'number': new_number})

    # 4. 将旧编号映射到新编号，用于重写当前及后续章节正文引用。
    for number_map in (old_number_map, local_number_map):
        for entry in number_map.values():
            entry['new_number'] = new_number_by_key.get(entry.get('key'))

    updated_content = rewrite_citations_with_entry_map(clean_content, local_number_map, drop_missing=True)

    updated_sections = []
    for i, section in enumerate(all_sections):
        if i == current_position:
            continue
        title = section.get('title', '')
        if is_reference_section(title):
            continue
        content = normalize_section_body(section.get('content', ''))
        rewritten_content = rewrite_citations_with_entry_map(content, old_number_map, drop_missing=True)
        if rewritten_content != content:
            updated_sections.append({'title': title, 'content': rewritten_content})

    return {
        'cleaned_content': updated_content,
        'full_references': full_entries,
        'updated_sections': updated_sections
    }


def reorder_references_for_full_paper(all_sections, reference_style='GB/T 7714'):
    """
    整篇文章参考文献整理：按正文首次引用顺序重排编号，并重写参考文献章节。

    返回:
        {
            'reference_title': 参考文献章节标题,
            'reference_content': 新参考文献章节正文,
            'updated_sections': [{'title': ..., 'content': ...}],
            'entry_count': 条目数,
            'citation_count': 正文引用数量,
        }
    """
    sections = list(all_sections or [])
    reference_title = '# 参考文献'
    reference_content = ''
    body_sections = []

    for section in sections:
        title = str(section.get('title', '') or '').strip()
        content = str(section.get('content', '') or '')
        if is_reference_section(title):
            reference_title = title or reference_title
            reference_content = content
        else:
            body_sections.append({'title': title, 'content': content})

    existing_entries = parse_reference_entries(reference_content)
    ordered_occurrences = order_reference_occurrences_for_repair(existing_entries)
    occurrence_keys = []
    key_to_text = {}
    number_to_keys = {}
    for occurrence in ordered_occurrences:
        number = occurrence.get('number')
        key = occurrence.get('key')
        text = occurrence.get('text', '')
        if not key:
            continue
        if key not in key_to_text:
            occurrence_keys.append(key)
            key_to_text[key] = text
        if number:
            number_to_keys.setdefault(number, [])
            if key not in number_to_keys[number]:
                number_to_keys[number].append(key)

    duplicate_numbers = {
        number
        for number, keys in number_to_keys.items()
        if len(keys) > 1
    }
    duplicate_cursors = {number: 0 for number in duplicate_numbers}
    unique_number_to_key = {
        number: keys[0]
        for number, keys in number_to_keys.items()
        if len(keys) == 1
    }

    ordered_keys = []
    seen_keys = set()
    citation_count = 0
    section_citation_maps = []

    def remember_key(key):
        if not key or key in seen_keys:
            return
        if key not in key_to_text:
            return
        seen_keys.add(key)
        ordered_keys.append(key)

    def assign_reference_key(old_number, local_number_to_key):
        if old_number in local_number_to_key:
            return local_number_to_key[old_number]
        if old_number in duplicate_numbers:
            keys = number_to_keys.get(old_number, [])
            cursor = duplicate_cursors.get(old_number, 0)
            key = keys[min(cursor, len(keys) - 1)] if keys else ''
            if cursor < len(keys) - 1:
                duplicate_cursors[old_number] = cursor + 1
        else:
            key = unique_number_to_key.get(old_number, '')
        if key:
            local_number_to_key[old_number] = key
        return key

    for section in body_sections:
        title = section.get('title', '')
        content = normalize_section_body(section.get('content', ''))
        local_number_to_key = {}
        for match in re.finditer(r'\[([^\[\]]+)\]', content):
            for old_number in parse_citation_numbers(match.group(1)):
                key = assign_reference_key(old_number, local_number_to_key)
                if key:
                    citation_count += 1
                    remember_key(key)
        section_citation_maps.append({
            'title': title,
            'content': content,
            'number_to_key': local_number_to_key,
        })

    for key in occurrence_keys:
        remember_key(key)

    new_number_by_key = {
        key: index
        for index, key in enumerate(ordered_keys, start=1)
    }
    full_entries = [
        {'number': new_number_by_key[key], 'text': key_to_text.get(key, ''), 'key': key}
        for key in ordered_keys
        if key_to_text.get(key)
    ]

    updated_sections = []
    for section in section_citation_maps:
        title = section.get('title', '')
        content = section.get('content', '')
        old_to_new = {
            number: new_number_by_key[key]
            for number, key in section.get('number_to_key', {}).items()
            if key in new_number_by_key
        }
        rewritten = rewrite_citations_with_number_map(content, old_to_new, drop_missing=True)
        if rewritten != content:
            updated_sections.append({'title': title, 'content': rewritten})

    return {
        'reference_title': reference_title,
        'reference_content': build_reference_body_from_entries(full_entries),
        'updated_sections': updated_sections,
        'entry_count': len(full_entries),
        'citation_count': citation_count,
    }
