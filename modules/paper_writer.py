# -*- coding: utf-8 -*-
"""
论文写作模块。
"""

import re

from modules.prompt_center import PromptCenter


class PaperWriter:
    SECTION_MAX_TOKENS = 3000

    def __init__(self, api_client, prompt_center=None):
        self.api = api_client
        self.prompt_center = prompt_center or PromptCenter(getattr(api_client, 'config', None))

    @staticmethod
    def _usage_context(scene_id='', action=''):
        return {
            'page_id': 'paper_write',
            'scene_id': scene_id,
            'action': action,
        }

    def _render_scene(self, scene_id, values):
        rendered = self.prompt_center.render_scene(scene_id, values)
        return rendered['system'], rendered['prompt']

    def generate_outline(self, topic, style='学术论文', reference_style='GB/T 7714', subject='', total_word_count='', outline_section_limit='', template_structure=None):
        """生成论文大纲。"""
        template_prompt = self._template_structure_prompt(template_structure)
        if template_prompt:
            system = '你是一位严格按论文模板目录生成大纲的学术写作助手。'
            prompt = (
                '## 任务\n'
                '根据用户论文信息和上传的论文模板目录，生成一份新的论文大纲。\n\n'
                '## 论文信息\n'
                f'- 论文标题：{topic}\n'
                f'- 论文类型：{style}\n'
                f'- 学科/方向：{subject or "未指定"}\n'
                f'- 引用格式：{reference_style}\n\n'
                f'{template_prompt}\n\n'
                '## 强制输出规则\n'
                '1. 只输出最终大纲正文，不要输出说明、分析、提示语或代码围栏。\n'
                '2. 输出标题必须与模板标题一一对应：数量一致、层级一致、顺序一致、编号样式一致。\n'
                '3. 模板 H1 输出为 `# ...`，H2 输出为 `## ...`，H3 输出为 `### ...`，以此类推。\n'
                '4. 标题内容围绕论文标题改写，但必须保留模板章节功能；例如模板是“前言”，结果仍应是前言/绪论功能，不得改成摘要。\n'
                '5. 模板没有“中文摘要、英文摘要、关键词”等标题时，严禁新增这些标题。\n'
                '6. “参考文献”只输出标题，不得生成具体文献条目。\n'
            )
        else:
            system, prompt = self._render_scene(
                'paper_write.outline',
                {
                    'topic': topic,
                    'style': style,
                    'reference_style': reference_style,
                    'subject': subject,
                    'total_word_count': total_word_count,
                    'outline_section_limit': outline_section_limit,
                },
            )
        if str(total_word_count or '').strip():
            if template_prompt:
                prompt = (
                    f'{prompt}\n\n'
                    '【全文目标字数约束】\n'
                    f'本次用户明确指定全文目标字数约 {total_word_count} 字，请在保持模板章节结构不变的前提下控制总篇幅。\n'
                    '不得因为字数目标而删除、合并或新增模板中的标题；如需体现篇幅控制，只能在各正文章节下标注“建议字数：约 X 字”。\n'
                    '所有建议字数合计必须围绕全文目标字数规划，并与模板中可写正文叶子章节数量匹配。\n'
                )
            else:
                prompt = (
                    f'{prompt}\n\n'
                    '【全文目标字数约束】\n'
                    f'本次用户明确指定全文目标字数约 {total_word_count} 字，请从大纲阶段控制总篇幅。\n'
                    f'章节数量建议：{outline_section_limit or "按目标字数减少可写小节数量，避免机械拆分"}。\n'
                    '必须根据全文目标字数减少或合并章节/小节，不要按默认每章目标字数推导出大量章节。\n'
                    '不要生成几十个可写小节来再平均压缩字数；优先合并相近主题，使可写叶子章节数量与全文目标字数匹配。\n'
                    '可以在正文一级章节下写“建议字数：约 X 字”，但所有建议字数合计必须围绕全文目标字数规划。\n'
                )
        result = self.api.call_sync(
            prompt,
            system,
            usage_context=self._usage_context('paper_write.outline', 'generate_outline'),
        )
        if template_prompt:
            result = self._ensure_template_outline(
                result,
                template_structure,
                topic=topic,
                system=system,
                reference_style=reference_style,
                total_word_count=total_word_count,
            )
        return result

    def _ensure_template_outline(self, result, template_structure, *, topic='', system='', reference_style='', total_word_count=''):
        template_headings = self._template_heading_items(template_structure)
        if not template_headings:
            return result
        if self._outline_matches_template(result, template_headings):
            return self._normalize_template_outline_markdown(result)

        repair_prompt = self._template_repair_prompt(
            result,
            template_headings,
            topic=topic,
            reference_style=reference_style,
            total_word_count=total_word_count,
        )
        try:
            repaired = self.api.call_sync(
                repair_prompt,
                system or '你是一位严格按论文模板目录修复大纲结构的学术写作助手。',
                usage_context=self._usage_context('paper_write.outline', 'repair_template_outline'),
            )
            if self._outline_matches_template(repaired, template_headings):
                return self._normalize_template_outline_markdown(repaired)
        except Exception:
            pass
        return self._fallback_template_outline(template_headings, topic=topic)

    @staticmethod
    def _template_heading_items(template_structure):
        if not isinstance(template_structure, dict):
            return []
        headings = template_structure.get('headings') or []
        if not isinstance(headings, list) or not headings:
            return []
        result = []
        for item in headings[:80]:
            if not isinstance(item, dict):
                continue
            title = str(item.get('title', '') or '').strip()
            if not title:
                continue
            try:
                level = max(1, min(6, int(item.get('level') or 1)))
            except Exception:
                level = 1
            result.append({'level': level, 'title': title})
        return result

    @classmethod
    def _template_structure_prompt(cls, template_structure):
        template_headings = cls._template_heading_items(template_structure)
        if not template_headings:
            return ''
        lines = []
        for item in template_headings:
            title = item['title']
            display_title = '英文摘要' if ''.join(title.lower().split()) in {'abstract', 'englishabstract', '英文摘要'} else title
            level = item['level']
            lines.append(f'{"  " * (level - 1)}- H{level} {display_title}')
        if not lines:
            return ''
        filename = str(template_structure.get('filename', '') or '论文模板').strip()
        return (
            '【论文模板结构约束】\n'
            f'用户上传了论文模板：{filename}。\n'
            '本约束优先级高于前文默认大纲顺序。生成大纲时必须以模板标题结构为准，不要再套用默认的“中文摘要、英文摘要、引言、第一章……”固定结构。\n'
            '必须保留模板的一、二、三级标题数量、层级深度、前后顺序和编号样式；模板中没有的摘要、关键词、附录等板块不要自行新增。\n'
            '模板中的“前言、国内外研究现状、结论、参考文献”等固定板块必须保持同级位置；正文各章标题可围绕当前论文题目适度改写，但不得改变其章节功能。\n'
            '输出仍使用 Markdown 标题语法表达层级：模板 H1 用 `# 标题`，H2 用 `## 标题`，H3 用 `### 标题`。标题文字中保留模板原有编号样式，例如 `# 1 前言`、`## 1.1 研究背景及意义`。\n'
            '参考文献只输出标题，不要生成具体文献条目。\n'
            '模板标题结构：\n'
            + '\n'.join(lines)
        )

    @staticmethod
    def _template_repair_prompt(bad_outline, template_headings, *, topic='', reference_style='', total_word_count=''):
        template_lines = '\n'.join(PaperWriter._format_template_heading(item['level'], item['title']) for item in template_headings)
        return (
            '上一轮生成的大纲没有严格匹配论文模板。现在请重新输出大纲。\n\n'
            f'论文标题：{topic}\n'
            f'引用格式：{reference_style or "GB/T 7714"}\n'
            f'全文目标字数：{total_word_count or "未指定"}\n\n'
            '【必须逐行对应的模板结构】\n'
            f'{template_lines}\n\n'
            '【硬性规则】\n'
            '1. 只输出大纲标题行，不要输出说明文字。\n'
            '2. 必须与上方模板逐行对应：不新增、不删除、不合并、不拆分标题。\n'
            '3. 每一行的 Markdown 层级、编号样式和章节功能必须保持一致。\n'
            '4. 模板没有中文摘要、英文摘要、关键词时，严禁新增这些标题。\n'
            '5. 参考文献只输出标题，不生成条目。\n\n'
            '【错误输出，仅用于避免重复犯错】\n'
            f'{str(bad_outline or "").strip()[:3000]}\n\n'
            '现在重新输出严格符合模板的大纲。'
        )

    @staticmethod
    def _format_template_heading(level, title):
        level = max(1, min(6, int(level or 1)))
        return f'{"#" * level} {str(title or "").strip()}'.strip()

    @classmethod
    def _fallback_template_outline(cls, template_headings, *, topic=''):
        template_subject = cls._template_subject_phrase(template_headings)
        topic_core = cls._topic_core(topic)
        lines = []
        for item in template_headings:
            title = item['title']
            if template_subject and topic_core:
                title = title.replace(template_subject, topic_core)
            lines.append(cls._format_template_heading(item['level'], title))
        return '\n'.join(lines)

    @staticmethod
    def _topic_core(topic):
        text = re.sub(r'\s+', '', str(topic or '')).strip('：:;；,.，。')
        text = re.sub(r'的?(相关)?(研究基础|基础研究|研究|分析|探究)$', '', text)
        return text or str(topic or '').strip()

    @staticmethod
    def _template_subject_phrase(template_headings):
        counts = {}
        for item in template_headings:
            title = re.sub(r'^\s*\d+(?:\.\d+)*\s*', '', str(item.get('title', '') or '')).strip()
            match = re.match(r'(.{4,}?)的(?:机理研究|影响因素|机理分析|实证分析|途径选择|应用分析|优化路径)', title)
            if not match:
                continue
            phrase = match.group(1).strip()
            counts[phrase] = counts.get(phrase, 0) + 1
        if not counts:
            return ''
        return max(counts.items(), key=lambda item: (item[1], len(item[0])))[0]

    @classmethod
    def _outline_matches_template(cls, outline, template_headings):
        parsed = cls._parse_outline_headings(outline)
        if len(parsed) != len(template_headings):
            return False
        template_specials = {cls._special_heading_key(item['title']) for item in template_headings}
        template_specials.discard('')
        for generated, template in zip(parsed, template_headings):
            if int(generated['level']) != int(template['level']):
                return False
            special_key = cls._special_heading_key(generated['title'])
            if special_key and special_key not in template_specials:
                return False
            anchor = cls._template_anchor(template['title'])
            if anchor and not cls._generated_keeps_anchor(generated['title'], anchor):
                return False
        return True

    @classmethod
    def _normalize_template_outline_markdown(cls, outline):
        parsed = cls._parse_outline_headings(outline)
        if not parsed:
            return str(outline or '').strip()
        return '\n'.join(cls._format_template_heading(item['level'], item['title']) for item in parsed)

    @staticmethod
    def _parse_outline_headings(outline):
        headings = []
        for raw_line in str(outline or '').splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if match:
                headings.append({'level': len(match.group(1)), 'title': match.group(2).strip()})
                continue
            match = re.match(r'^(\d+(?:\.\d+)*)(?:[.．、])?\s+(.+)$', line)
            if match:
                headings.append({'level': match.group(1).count('.') + 1, 'title': line})
                continue
            if re.fullmatch(r'(参考文献|结论|前言|绪论|引言)', line, flags=re.IGNORECASE):
                headings.append({'level': 1, 'title': line})
        return headings

    @staticmethod
    def _strip_heading_number(title):
        return re.sub(r'^\s*\d+(?:\.\d+)*(?:[.．、])?\s*', '', str(title or '')).strip()

    @classmethod
    def _template_anchor(cls, title):
        text = cls._strip_heading_number(title)
        anchors = (
            '国内外研究现状',
            '国外研究现状',
            '国内研究现状',
            '研究背景及意义',
            '本文研究思路与结构',
            '建立回归模型及参数估计',
            '变量相关分析',
            '变量选取',
            '机理研究',
            '影响因素',
            '机理分析',
            '实证分析',
            '途径选择',
            '参考文献',
            '前言',
            '绪论',
            '引言',
            '结论',
            '结果',
        )
        for anchor in anchors:
            if anchor in text:
                return anchor
        return ''

    @staticmethod
    def _special_heading_key(title):
        text = re.sub(r'\s+', '', str(title or '')).lower()
        text = re.sub(r'^\d+(?:\.\d+)*(?:[.．、])?', '', text)
        if text in {'中文摘要', '摘要'}:
            return 'cn_abstract'
        if text in {'英文摘要', 'abstract'}:
            return 'en_abstract'
        if text in {'关键词', '关键字', '中文关键词', '英文关键词', 'keywords'}:
            return 'keywords'
        if text in {'参考文献', 'references', 'bibliography'}:
            return 'reference'
        return ''

    @staticmethod
    def _generated_keeps_anchor(title, anchor):
        text = re.sub(r'\s+', '', str(title or ''))
        equivalents = {
            '前言': ('前言', '绪论', '引言'),
            '绪论': ('绪论', '前言', '引言'),
            '引言': ('引言', '前言', '绪论'),
        }
        candidates = equivalents.get(anchor, (anchor,))
        return any(candidate in text for candidate in candidates)

    @staticmethod
    def _section_token_budget(word_count):
        try:
            target_words = max(300, int(word_count or 1000))
        except Exception:
            target_words = 1000
        # Keep this close to the requested Chinese character count; a loose token
        # budget lets many models drift from 1200 characters to 1800+ characters.
        return max(650, min(2200, int(target_words * 1.22) + 120))

    def write_section(self, outline, section_title, context='', word_count=1000, reference_style='GB/T 7714'):
        """按章节写作。"""
        system, prompt = self._render_scene(
            'paper_write.section',
            {
                'outline': outline,
                'section_title': section_title,
                'context': context[:500] if context else '',
                'word_count': word_count,
                'reference_style': reference_style,
            },
        )
        try:
            target_words = max(300, int(word_count or 1000))
        except Exception:
            target_words = 1000
        upper_words = int(target_words * 1.12)
        lower_words = int(target_words * 0.88)
        prompt = (
            f'{prompt}\n\n'
            '【字数硬约束】\n'
            f'本次目标字数为约 {target_words} 字。请把正文严格控制在 {lower_words}-{upper_words} 字之间，宁可略少，不要超过上限。\n'
            f'当内容接近 {upper_words} 字时必须立即收束，不要继续扩展概念、分类、阶段或政策背景。\n'
            '输出 3-5 个自然段即可，每段围绕一个核心论点展开；不要写成长篇综述，不要为了凑内容罗列过多类型、阶段和例子。\n'
            '若需要参考文献，参考文献条目不计入正文目标，但也要精简。\n\n'
            '【参考文献语种硬约束】\n'
            '撰写章节时如需引用或检索文献，只能使用中文文献；优先选择中文期刊论文、学位论文、中文专著、中文政策/统计报告等中文来源。\n'
            '所有参考文献必须真实存在且可检索核验，严禁编造作者、题名、期刊、年份、卷期、页码、出版社、DOI 或网址；不得把不同文献的信息拼接成一条。\n'
            '参考文献条目应提供可核验线索，例如期刊/出版社/发布机构、年份、卷期页码，能确认 DOI、CNKI、万方、维普或官网链接时优先写明。\n'
            '不得使用英文论文、英文网页、英文书籍或将英文文献翻译成中文后冒充中文来源；找不到真实可靠的中文来源时使用 `[待核验中文文献]`，不要输出伪造条目。\n'
            '参考文献条目中的题名、来源、出版单位等信息应保持中文，避免出现英文题名或英文期刊名。'
        )
        return self.api.call_sync(
            prompt,
            system,
            max_tokens=self._section_token_budget(target_words),
            usage_context=self._usage_context('paper_write.section', 'write_section'),
        )

    def write_abstract(self, full_text, language='中文'):
        """生成摘要。"""
        language = str(language or '中文').strip()
        system, prompt = self._render_scene(
            'paper_write.abstract',
            {
                'full_text': full_text[:12000],
                'language': language,
            },
        )
        if language in {'中英文', '中文+英文', '中英双语', '双语', 'bilingual', 'Bilingual'}:
            prompt = (
                f'{prompt}\n\n'
                '【双语摘要强制要求】\n'
                '本次必须同时输出中文摘要和英文摘要，且英文摘要不得省略。\n'
                '严格使用以下四个块，顺序不可改变：\n'
                '【摘要】<中文摘要正文，200-300字>\n'
                '【关键词】词1；词2；词3；词4；词5\n'
                '[Abstract] <English abstract, 150-250 words>\n'
                '[Keywords] keyword1; keyword2; keyword3; keyword4; keyword5\n'
            )
        elif language in {'英文', 'English', 'english', 'en'}:
            prompt = (
                f'{prompt}\n\n'
                '【英文摘要强制要求】\n'
                '本次只输出英文摘要和英文关键词，严格使用 [Abstract] 与 [Keywords] 两个块，不要输出中文。'
            )
        return self.api.call_sync(
            prompt,
            system,
            usage_context=self._usage_context('paper_write.abstract', 'write_abstract'),
        )

    def format_references(self, refs_text, style='GB/T 7714'):
        """格式化参考文献。"""
        system = '你是一位专业的学术规范助手，精通各类参考文献格式。'
        prompt = f'''请将以下参考文献整理为{style}格式：
{refs_text}

要求：
1. 严格按照{style}标准格式。
2. 按照引用顺序编号。
3. 仅允许使用中文文献信息；不得新增输入中没有、且无法确认真实存在的参考文献。
4. 所有条目必须真实可检索，严禁编造作者、题名、期刊、年份、卷期、页码、出版社、DOI 或网址；不得把不同文献的信息拼接成一条。
5. 信息补全只能补充确定可靠的中文来源；无法确认时标注“[待核验]”或“[信息待补充]”，不要猜测补全。
6. 不得新增英文论文、英文网页、英文书籍或英文期刊来源，也不得把英文文献翻译后冒充中文文献。
7. 保持格式统一规范。

请直接输出格式化后的参考文献列表。'''
        return self.api.call_sync(
            prompt,
            system,
            usage_context=self._usage_context('', 'format_references'),
        )

    def improve_paragraph(self, paragraph, direction='学术化'):
        """改进段落。"""
        system = '你是一位专业的学术写作助手。'
        prompt = f'''请对以下段落进行{direction}改进：
{paragraph}

要求：
1. 保持原有观点和信息。
2. 提升学术表达水平。
3. 增强逻辑连贯性。
4. 使用更专业的学术词汇。

请直接输出改进后的段落。'''
        return self.api.call_sync(
            prompt,
            system,
            usage_context=self._usage_context('', 'improve_paragraph'),
        )
