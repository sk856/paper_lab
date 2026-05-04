# 参考文献管理系统设计文档

## 需求分析

### 场景1：新增章节（追加模式）
- 用户在文档末尾或中间新增一个章节
- AI生成的内容包含新的参考文献引用
- **期望行为**：
  - 查找该章节之前所有章节的最后一个参考文献编号（例如 [15]）
  - 新章节的参考文献从 [16] 开始编号
  - 不影响前面章节的参考文献编号
  - 将新参考文献追加到参考文献章节

### 场景2：修改章节（重排模式）
- 用户修改已有章节的内容
- AI重新生成内容，可能添加、删除或修改参考文献引用
- **期望行为**：
  - 从当前章节开始，重新收集所有引用的参考文献
  - 按文档顺序重新编号（保持引用顺序）
  - 更新当前及后续所有章节的引用编号
  - 重新生成参考文献章节

## 技术设计

### 1. 模式判断
```python
def determine_reference_mode(section_title, all_sections):
    """
    判断参考文献处理模式
    
    返回:
    - 'append': 新增章节，追加参考文献
    - 'reorder': 修改章节，重新排序参考文献
    """
    # 检查该章节是否已存在内容
    for section in all_sections:
        if section.get('title') == section_title:
            content = section.get('content', '').strip()
            if content:
                return 'reorder'  # 已有内容，是修改操作
    return 'append'  # 无内容，是新增操作
```

### 2. 追加模式实现
```python
def process_references_append_mode(section_title, new_content, all_sections, reference_style):
    """
    追加模式：新增章节时续写参考文献编号
    
    步骤：
    1. 找到当前章节在文档中的位置
    2. 收集该章节之前所有章节的参考文献，找到最大编号
    3. 解析新章节的参考文献，从最大编号+1开始重新编号
    4. 更新新章节的引用编号
    5. 将新参考文献追加到参考文献章节
    
    返回：
    - cleaned_content: 清理后的章节内容
    - references_to_append: 需要追加的参考文献列表
    - updated_sections: 空列表（追加模式不更新其他章节）
    """
    pass
```

### 3. 重排模式实现
```python
def process_references_reorder_mode(section_title, new_content, all_sections, reference_style):
    """
    重排模式：修改章节时重新排序参考文献
    
    步骤：
    1. 找到当前章节在文档中的位置
    2. 收集该章节之前所有章节的参考文献（保持原编号）
    3. 从当前章节开始，重新收集所有引用的参考文献
    4. 按引用顺序重新编号（从前面章节的最大编号+1开始）
    5. 更新当前及后续所有章节的引用编号
    6. 重新生成完整的参考文献章节
    
    返回：
    - cleaned_content: 清理后的章节内容
    - full_references: 完整的参考文献章节内容
    - updated_sections: 需要更新的其他章节列表
    """
    pass
```

### 4. 数据结构

#### 参考文献条目
```python
{
    'text': '作者. 标题[J]. 期刊, 年份, 卷(期): 页码.',
    'key': 'hash_of_normalized_text',  # 用于去重
    'number': 15  # 当前编号
}
```

#### 章节信息
```python
{
    'title': '1.1 研究背景',
    'content': '章节正文内容...',
    'position': 0  # 在文档中的位置索引
}
```

#### 返回给前端的数据
```python
{
    'content': '清理后的章节内容',
    'references': {
        'mode': 'append' | 'reorder',
        'content': '参考文献章节内容',  # reorder模式返回完整内容
        'append': [...],  # append模式返回追加的条目
    },
    'updatedSections': [  # reorder模式返回需要更新的章节
        {'title': '1.2 研究现状', 'content': '更新后的内容'},
        ...
    ]
}
```

## 实现细节

### 关键函数

1. **find_max_reference_number(sections, before_position)**
   - 查找指定位置之前所有章节的最大参考文献编号

2. **collect_references_from_sections(sections, start_position, end_position)**
   - 收集指定范围内章节的所有参考文献引用

3. **renumber_references(content, old_to_new_map)**
   - 根据编号映射更新章节中的引用编号

4. **build_references_section(entries, style)**
   - 根据参考文献条目列表生成参考文献章节内容

### 边界情况处理

1. **第一个章节**：最大编号为0，从1开始编号
2. **参考文献章节不存在**：自动创建
3. **重复引用**：使用key去重，保持首次引用的编号
4. **引用格式**：支持 [1], [1,2], [1-3], [1,3-5,7] 等格式

## 测试用例

### 测试1：新增章节（追加模式）
- 前提：已有章节1.1（引用[1][2]）、1.2（引用[3][4]）
- 操作：新增章节1.3，AI生成内容引用2个新文献
- 预期：章节1.3的引用编号为[5][6]，参考文献章节追加2条

### 测试2：修改章节（重排模式）
- 前提：已有章节1.1（引用[1][2]）、1.2（引用[3][4]）、1.3（引用[5][6]）
- 操作：修改章节1.2，AI重新生成内容，删除[3]，新增1个文献
- 预期：
  - 章节1.2的引用重新编号为[3][4]
  - 章节1.3的引用重新编号为[5][6]
  - 参考文献章节重新排序

### 测试3：修改第一个章节
- 前提：已有章节1.1（引用[1][2]）、1.2（引用[3][4]）
- 操作：修改章节1.1，AI重新生成内容，新增1个文献
- 预期：
  - 章节1.1的引用重新编号为[1][2][3]
  - 章节1.2的引用重新编号为[4][5]
  - 参考文献章节完全重新生成

## 实现优先级

1. ✅ 设计文档（当前）
2. 🔄 实现核心函数（reference_manager.py）
3. 🔄 更新服务器端逻辑（web_server.py）
4. 🔄 更新前端处理（web/app.js）
5. ⏳ 测试验证
