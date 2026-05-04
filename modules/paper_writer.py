# -*- coding: utf-8 -*-
"""
论文写作模块。
"""

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
        template_prompt = self._template_structure_prompt(template_structure)
        if template_prompt:
            prompt = f'{prompt}\n\n{template_prompt}'
        if str(total_word_count or '').strip():
            prompt = (
                f'{prompt}\n\n'
                '【全文目标字数约束】\n'
                f'本次用户明确指定全文目标字数约 {total_word_count} 字，请从大纲阶段控制总篇幅。\n'
                f'章节数量建议：{outline_section_limit or "按目标字数减少可写小节数量，避免机械拆分"}。\n'
                '必须根据全文目标字数减少或合并章节/小节，不要按默认每章目标字数推导出大量章节。\n'
                '不要生成几十个可写小节来再平均压缩字数；优先合并相近主题，使可写叶子章节数量与全文目标字数匹配。\n'
                '可以在正文一级章节下写“建议字数：约 X 字”，但所有建议字数合计必须围绕全文目标字数规划。\n'
            )
        return self.api.call_sync(
            prompt,
            system,
            usage_context=self._usage_context('paper_write.outline', 'generate_outline'),
        )

    @staticmethod
    def _template_structure_prompt(template_structure):
        if not isinstance(template_structure, dict):
            return ''
        headings = template_structure.get('headings') or []
        if not isinstance(headings, list) or not headings:
            return ''
        lines = []
        title_keys = []
        for item in headings[:80]:
            if not isinstance(item, dict):
                continue
            title = str(item.get('title', '') or '').strip()
            if not title:
                continue
            title_keys.append(''.join(title.lower().split()))
            display_title = '英文摘要' if ''.join(title.lower().split()) in {'abstract', 'englishabstract', '英文摘要'} else title
            try:
                level = max(1, min(6, int(item.get('level') or 1)))
            except Exception:
                level = 1
            lines.append(f'{"  " * (level - 1)}- H{level} {display_title}')
        if not lines:
            return ''
        filename = str(template_structure.get('filename', '') or '论文模板').strip()
        has_chinese_abstract = any(key in {'摘要', '中文摘要', '内容摘要'} or '中文摘要' in key for key in title_keys)
        has_english_abstract = any(key in {'abstract', '英文摘要', 'englishabstract'} or '英文摘要' in key for key in title_keys)
        abstract_instructions = []
        if not has_chinese_abstract:
            abstract_instructions.append('模板目录中未包含中文摘要时，仍必须在大纲开头自动补充“# 中文摘要”。')
        if not has_english_abstract:
            abstract_instructions.append('模板目录中未包含英文摘要/Abstract 时，仍必须紧接中文摘要自动补充“# 英文摘要”。')
        abstract_prompt = ''.join(f'{item}\n' for item in abstract_instructions)
        return (
            '【论文模板结构约束】\n'
            f'用户上传了论文模板：{filename}。\n'
            '请参考下列模板标题层级生成新的论文大纲，保留其章节组织方式、层级深度和前后顺序，但标题内容必须围绕当前论文题目重写，不要照抄模板原论文的具体研究对象。\n'
            '若模板包含中文摘要、英文摘要、引言/绪论、正文各章、结论、参考文献等固定板块，请在生成结果中保持相同板块位置。\n'
            '输出大纲时摘要标题统一使用“中文摘要”和“英文摘要”，不要输出“Abstract”。\n'
            f'{abstract_prompt}'
            '模板标题结构：\n'
            + '\n'.join(lines)
        )

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
            '若需要参考文献，参考文献条目不计入正文目标，但也要精简。'
        )
        return self.api.call_sync(
            prompt,
            system,
            max_tokens=self._section_token_budget(target_words),
            usage_context=self._usage_context('paper_write.section', 'write_section'),
        )

    def write_abstract(self, full_text, language='中文'):
        """生成摘要。"""
        system, prompt = self._render_scene(
            'paper_write.abstract',
            {
                'full_text': full_text[:12000],
                'language': language,
            },
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
3. 信息补全时明确标注待补充项。
4. 保持格式统一规范。

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
