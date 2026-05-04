# -*- coding: utf-8 -*-
"""
学术润色模块。
"""

from modules.prompt_center import PromptCenter


class AcademicPolisher:
    """学术论文润色。"""

    TEMPERATURE_MAP = {
        '标准模式': 0.4,
        '学术强化': 0.45,
        '结构重组': 0.55,
        '精炼压缩': 0.35,
    }

    def __init__(self, api_client, prompt_center=None):
        self.api = api_client
        self.prompt_center = prompt_center or PromptCenter(getattr(api_client, 'config', None))

    @staticmethod
    def _usage_context(action, scene_id='polish.run_task'):
        return {
            'page_id': 'polish',
            'scene_id': scene_id,
            'action': action,
        }

    def run_task(
        self,
        text: str,
        task_type: str = '章节正文',
        polish_type: str = 'full',
        execution_mode: str = '标准模式',
        topic: str = '',
        notes: str = '',
    ) -> str:
        """统一任务入口。"""
        text = (text or '').strip()
        if not text:
            raise ValueError('待处理文本不能为空')

        task_type = task_type or '章节正文'
        polish_type = polish_type or 'full'
        execution_mode = execution_mode or '标准模式'
        topic = (topic or '').strip()
        notes = (notes or '').strip()

        rendered = self.prompt_center.render_scene(
            'polish.run_task',
            {
                'text': text,
                'task_type': task_type,
                'polish_type': polish_type,
                'execution_mode': execution_mode,
                'topic': topic,
                'notes': notes,
            },
        )
        temperature = self.TEMPERATURE_MAP.get(execution_mode, 0.4)
        return self.api.call_sync(
            rendered['prompt'],
            rendered['system'],
            temperature=temperature,
            usage_context=self._usage_context('run_task'),
        )

    def polish_grammar(self, text: str) -> str:
        """语法和标点修正。"""
        system = '你是一位专业的中文学术文本校对专家。'
        prompt = f'''请对以下文本进行语法和标点校对：

{text}

校对要求：
1. 修正语法错误。
2. 规范中文标点使用。
3. 修正错别字。
4. 修正不完整句子。
5. 统一数字格式。
6. 不改变原文含义和风格。

请直接输出校对后的文本，并在末尾用【修改说明】列出主要改动。'''
        return self.api.call_sync(
            prompt,
            system,
            temperature=0.3,
            usage_context=self._usage_context('polish_grammar'),
        )

    def polish_academic_vocab(self, text: str) -> str:
        """学术词汇替换。"""
        system = '你是一位学术写作专家，精通学术词汇和表达。'
        prompt = f'''请对以下文本进行学术词汇优化：
{text}

优化要求：
1. 将口语化表达替换为学术规范表达。
2. 使用更精确的专业术语。
3. 避免模糊、笼统的表述。
4. 增强客观性。
5. 规范量词和单位使用。
6. 保持原有论证逻辑。

请直接输出优化后的文本。'''
        return self.api.call_sync(
            prompt,
            system,
            temperature=0.4,
            usage_context=self._usage_context('polish_academic_vocab'),
        )

    def polish_logic(self, text: str) -> str:
        """逻辑和段落优化。"""
        system = '你是一位擅长学术写作的逻辑思维专家。'
        prompt = f'''请对以下文本进行逻辑和结构优化：

{text}

优化要求：
1. 理顺论证逻辑，确保因果关系清晰。
2. 优化段落间的过渡和衔接。
3. 调整句子顺序，使论证更有力。
4. 增加必要的承上启下过渡句。
5. 确保每段只有一个中心论点。
6. 加强结论与论据的对应关系。

请直接输出优化后的文本。'''
        return self.api.call_sync(
            prompt,
            system,
            temperature=0.5,
            usage_context=self._usage_context('polish_logic'),
        )

    def polish_full(self, text: str) -> str:
        """全面润色。"""
        system = '你是一位顶级学术论文编辑，能够全面提升论文质量。'
        prompt = f'''请对以下论文内容进行全面学术润色：
{text}

润色要求：
1. 语法和标点规范化。
2. 学术词汇精确化。
3. 句式多样化。
4. 逻辑连贯性增强。
5. 段落结构优化。
6. 表达更加简洁有力。
7. 保留所有数据、公式、引用信息。
8. 整体风格保持学术严谨。

请直接输出润色后的文本。'''
        return self.api.call_sync(
            prompt,
            system,
            temperature=0.5,
            usage_context=self._usage_context('polish_full'),
        )

    def translate_polish(self, text: str, target_lang: str = '英文') -> str:
        """翻译润色。"""
        system = '你是一位专业的学术翻译专家，精通中英文等多语种学术写作。'
        prompt = f'''请将以下学术文本翻译为{target_lang}，并进行学术润色：
{text}

要求：
1. 准确传达原文学术含义。
2. 使用目标语言的学术写作规范。
3. 专业术语翻译准确。
4. 语言流畅自然。

请直接输出翻译后的文本。'''
        return self.api.call_sync(
            prompt,
            system,
            temperature=0.4,
            usage_context=self._usage_context('translate_polish', scene_id='polish.translate'),
        )

    def check_format(self, text: str, style: str = '学术论文') -> dict:
        """格式规范检查。"""
        import re

        issues = []

        if ',' in text and '，' not in text:
            issues.append('建议使用中文逗号（，）替代英文逗号（,）。')
        if '.' in text.replace('...', '') and '。' not in text:
            issues.append('建议使用中文句号（。）替代英文句号（.）。')

        cn_nums = re.findall(r'[一二三四五六七八九十百千万]+', text)
        if cn_nums:
            issues.append(f'发现 {len(cn_nums)} 处中文数字，学术论文建议优先使用阿拉伯数字。')

        paragraphs = [p for p in text.split('\n') if len(p.strip()) > 0]
        short_paras = [p for p in paragraphs if 0 < len(p.strip()) < 50]
        if short_paras:
            issues.append(f'发现 {len(short_paras)} 个过短段落，建议合并或扩充。')

        has_ref = bool(re.search(r'\[\d+\]', text))
        if not has_ref and len(text) > 500:
            issues.append('未发现参考文献引用标记，建议补充文献引用。')

        return {
            'issues': issues,
            'issue_count': len(issues),
            'word_count': len(text),
            'para_count': len(paragraphs),
            'sentence_count': len(re.split(r'[。！？?!]', text)),
        }
