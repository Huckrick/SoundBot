# -*- coding: utf-8 -*-
"""
UCS (Universal Category System) 关键词映射模块

从 Excel 文件加载中文-英文音效关键词映射，用于增强中文搜索能力。

特性：
1. 惰性加载 - 首次使用时才加载 Excel 数据
2. 缓存机制 - 加载后缓存到内存，避免重复读取
3. 性能优化 - 使用字典结构，O(1) 查询复杂度
4. 中文分词 - 支持 jieba 中文分词扩展

================================================================================
数据来源 / Data Source
================================================================================

UCS 音效分类中英文对照表来自 Bilibili 用户分享：
- 来源链接：https://www.bilibili.com/read/cv23153650/
- 原作者：Bilibili 音频后期社区用户
- 整理目的：帮助中文用户更好地使用 UCS 通用分类系统

关于 UCS (Universal Category System)：
UCS 是一个开放的、免费的音效分类系统，由音效社区共同维护。
更多信息请访问：https://universalcategoriesystem.com/

================================================================================
免责声明 / Disclaimer
================================================================================

1. 本模块使用的 UCS 分类表仅供学习和研究使用
2. 数据版权归原作者和 UCS 社区所有
3. 本项目不对数据的准确性和完整性承担责任
4. 使用 UCS 分类系统时请遵守其使用条款
5. 如需商业使用 UCS 分类数据，请联系 UCS 官方获取授权

================================================================================
许可证 / License
================================================================================

本模块代码采用 MIT License 开源
UCS 分类数据版权归原作者所有，遵循 UCS 社区使用规范

Copyright (c) 2024 SoundMind Project
"""

import os
import re
import logging
from typing import Dict, List, Optional, Set, Tuple
from functools import lru_cache

logger = logging.getLogger(__name__)

# 全局缓存
_ucs_keywords_cache: Optional[Dict[str, List[str]]] = None
_ucs_loaded = False
_jieba_available = False

# 尝试导入 jieba
try:
    import jieba
    _jieba_available = True
    # 初始化 jieba（预加载词典）
    jieba.initialize()
except ImportError:
    logger.warning("jieba 未安装，中文分词功能受限。请运行: pip install jieba")
    _jieba_available = False


def load_ucs_keywords(excel_path: Optional[str] = None) -> Dict[str, List[str]]:
    """
    从 Excel 文件加载 UCS 关键词映射
    
    Args:
        excel_path: Excel 文件路径，None 时使用默认路径
        
    Returns:
        中文关键词到英文同义词列表的映射字典
    """
    global _ucs_keywords_cache, _ucs_loaded
    
    if _ucs_loaded and _ucs_keywords_cache is not None:
        return _ucs_keywords_cache
    
    if excel_path is None:
        # 默认路径：项目根目录下的 Excel 文件
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        excel_path = os.path.join(base_dir, '..', 'UCS+音效分类中英文对照表.xlsx')
        excel_path = os.path.normpath(excel_path)
    
    if not os.path.exists(excel_path):
        logger.warning(f"UCS 关键词文件不存在: {excel_path}")
        _ucs_loaded = True
        return {}
    
    try:
        import pandas as pd
        
        # 读取 Excel，使用第3行作为 header（索引2）
        df = pd.read_excel(excel_path, header=2)
        
        # 过滤掉空值
        df = df.dropna(subset=['Category_zh', 'Synonyms - Comma Separated'])
        
        # 构建关键词映射
        keywords_map: Dict[str, List[str]] = {}
        
        for _, row in df.iterrows():
            # 从 Category_zh 和 SubCategory_zh 提取中文关键词
            chinese_keywords = []
            
            cat_zh = str(row.get('Category_zh', '')).strip()
            subcat_zh = str(row.get('SubCategory_zh', '')).strip()
            
            if cat_zh and cat_zh != 'nan':
                chinese_keywords.append(cat_zh)
            if subcat_zh and subcat_zh != 'nan' and subcat_zh != cat_zh:
                chinese_keywords.append(subcat_zh)
            
            # 获取英文同义词
            synonyms = str(row.get('Synonyms - Comma Separated', '')).strip()
            if not synonyms or synonyms == 'nan':
                continue
            
            # 解析英文同义词（逗号分隔）
            en_keywords = [s.strip().lower() for s in synonyms.split(',') if s.strip()]
            
            if not en_keywords:
                continue
            
            # 为每个中文关键词添加映射
            for chinese in chinese_keywords:
                if not chinese or chinese == 'nan':
                    continue
                    
                # 如果中文关键词已存在，合并同义词列表
                if chinese in keywords_map:
                    existing = set(keywords_map[chinese])
                    existing.update(en_keywords)
                    keywords_map[chinese] = list(existing)
                else:
                    keywords_map[chinese] = en_keywords
        
        _ucs_keywords_cache = keywords_map
        _ucs_loaded = True
        
        logger.info(f"✅ UCS 关键词加载完成: {len(keywords_map)} 个中文关键词映射")
        
        # 打印一些示例
        sample_items = list(keywords_map.items())[:5]
        for cn, en_list in sample_items:
            logger.debug(f"  {cn} -> {en_list[:3]}...")
        
        return keywords_map
        
    except Exception as e:
        logger.error(f"❌ 加载 UCS 关键词失败: {e}")
        _ucs_loaded = True
        return {}


def get_ucs_keywords() -> Dict[str, List[str]]:
    """获取 UCS 关键词映射（带缓存）"""
    return load_ucs_keywords()


# 常见中文同义词映射（补充 UCS 表中没有的）
# 格式: 用户输入词 -> [(UCS中文词, 权重), ...]
CHINESE_SYNONYMS = {
    "石头": [("岩石", 1.0)],
    "石": [("岩石", 0.8)],
    "石块": [("岩石", 0.9)],
    "石头撞击": [("撞击", 1.0), ("岩石", 0.8)],
    "石头掉落": [("掉落", 1.0), ("岩石", 0.8)],
    "汽车": [("车辆", 1.0), ("车", 0.8)],
    "风声": [("风", 1.0)],
    "雨声": [("雨", 1.0)],
    "雷声": [("雷", 1.0)],
    "水声": [("水", 1.0)],
    "门铃": [("铃", 1.0)],
    "闹钟": [("钟", 1.0)],
    "枪声": [("枪", 1.0)],
}

# 子类关键词到同义词的特定映射（用于精确匹配）
SUBCATEGORY_KEYWORDS = {
    "撞击": ["hit", "impact", "crash", "collision", "strike"],
    "掉落": ["fall", "drop", "impact", "crash", "thud"],
    "破碎": ["break", "shatter", "crack", "smash", "crash"],
}


def expand_query_with_ucs(query: str) -> List[str]:
    """
    使用 UCS 关键词扩展查询
    
    策略：
    1. 优先使用 SUBCATEGORY_KEYWORDS 中的精确映射
    2. 然后使用 CHINESE_SYNONYMS 映射到 UCS 分类
    3. 最后直接匹配 UCS 关键词
    
    Args:
        query: 原始查询（中文）
        
    Returns:
        扩展后的查询列表（包含英文同义词）
    """
    ucs_map = get_ucs_keywords()
    if not ucs_map:
        return [query]
    
    expanded = [query]  # 保留原始查询
    matched_keywords = set()
    
    # 步骤 1: 检查子类关键词（最精确）
    for subcat_keyword, en_list in SUBCATEGORY_KEYWORDS.items():
        if subcat_keyword in query:
            expanded.extend(en_list)
            matched_keywords.add(subcat_keyword)
            logger.debug(f"子类扩展: '{subcat_keyword}' -> {en_list}")
    
    # 步骤 2: 检查同义词映射
    for user_word, synonym_list in CHINESE_SYNONYMS.items():
        if user_word in query:
            for synonym, weight in synonym_list:
                if synonym in ucs_map and synonym not in matched_keywords:
                    matched_keywords.add(synonym)
                    en_keywords = ucs_map[synonym]
                    # 根据权重选择同义词数量
                    num_keywords = max(2, int(5 * weight))
                    expanded.extend(en_keywords[:num_keywords])
                    logger.debug(f"同义词扩展: '{user_word}' -> '{synonym}' (权重{weight}) -> {en_keywords[:num_keywords]}")
    
    # 步骤 3: 直接检查 UCS 关键词
    for cn_keyword, en_keywords in ucs_map.items():
        if cn_keyword in query and cn_keyword not in matched_keywords:
            # 添加英文同义词作为额外查询
            expanded.extend(en_keywords[:3])  # 最多添加 3 个同义词
            logger.debug(f"UCS扩展: '{cn_keyword}' -> {en_keywords[:3]}")
    
    # 去重并保持顺序
    seen = set()
    unique_expanded = []
    for q in expanded:
        q_lower = q.lower()
        if q_lower not in seen and len(q_lower) > 1:  # 过滤单字符
            seen.add(q_lower)
            unique_expanded.append(q)
    
    return unique_expanded


def get_all_keywords() -> Set[str]:
    """获取所有 UCS 中文关键词（用于自动补全等功能）"""
    ucs_map = get_ucs_keywords()
    return set(ucs_map.keys())


def search_ucs_keywords(partial: str, limit: int = 10) -> List[str]:
    """
    根据部分输入搜索匹配的关键词
    
    Args:
        partial: 部分输入
        limit: 返回结果数量限制
        
    Returns:
        匹配的中文关键词列表
    """
    ucs_map = get_ucs_keywords()
    if not ucs_map or not partial:
        return []
    
    partial = partial.lower()
    matches = []
    
    for cn_keyword in ucs_map.keys():
        if partial in cn_keyword.lower():
            matches.append(cn_keyword)
            if len(matches) >= limit:
                break
    
    return matches


# 兼容性：提供与 ChineseTextProcessor 类似的接口
class UCSKeywordProcessor:
    """UCS 关键词处理器"""

    def __init__(self):
        self._keywords = get_ucs_keywords()

    def expand_query(self, query: str) -> List[str]:
        """扩展查询"""
        return expand_query_with_ucs(query)

    def extract_keywords(self, text: str) -> List[str]:
        """提取匹配的 UCS 关键词"""
        matches = []
        for cn_keyword in self._keywords.keys():
            if cn_keyword in text:
                matches.extend(self._keywords[cn_keyword])
        return matches

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total_keywords": len(self._keywords),
            "loaded": _ucs_loaded
        }

    def tokenize(self, text: str) -> List[str]:
        """
        中文分词

        Args:
            text: 输入文本

        Returns:
            分词后的词语列表
        """
        if not text:
            return []

        # 检查是否有中文
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))

        if has_chinese and _jieba_available:
            # 使用 jieba 分词
            tokens = list(jieba.cut(text))
        else:
            # 英文或其他语言，按空格和常见分隔符分词
            tokens = [t.strip() for t in re.split(r'[\s_\-\.,]+', text) if t.strip()]

        # 过滤单字符和空字符串
        return [t for t in tokens if len(t) > 1]

    def expand_query_with_tokenization(self, query: str) -> List[str]:
        """
        使用中文分词扩展查询

        策略：只使用原始查询 + jieba 分词，禁用 UCS 关键词映射
        原因：UCS 映射质量不高，会导致大量误匹配

        Args:
            query: 原始查询

        Returns:
            扩展后的查询列表
        """
        if not query:
            return [query] if query else []

        expanded = [query]  # 保留原始查询
        seen = {query.lower()}

        # 只使用 jieba 分词扩展，禁用 UCS 映射
        if _jieba_available and re.search(r'[\u4e00-\u9fff]', query):
            tokens = self.tokenize(query)

            for token in tokens:
                if token.lower() not in seen and len(token) > 1:
                    seen.add(token.lower())
                    expanded.append(token)

        # 去重并保持原始查询在首位
        return list(dict.fromkeys(expanded))


# 测试代码
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 测试加载
    keywords = load_ucs_keywords()
    print(f"\n加载了 {len(keywords)} 个关键词映射")

    # 测试中文分词
    processor = UCSKeywordProcessor()
    print("\n=== 中文分词测试 ===")
    test_texts = ["鸟叫声", "古典音乐钢琴曲", "汽车发动机声音"]
    for text in test_texts:
        tokens = processor.tokenize(text)
        print(f"'{text}' -> {tokens}")

    # 测试查询扩展
    print("\n=== 查询扩展测试 ===")
    test_queries = ["石头", "风声", "爆炸", "门铃", "鸟叫声"]
    for query in test_queries:
        expanded = processor.expand_query_with_tokenization(query)
        print(f"\n'{query}' -> {expanded}")

    # 测试搜索
    print("\n=== 关键词搜索测试 ===")
    print("搜索 '石':")
    results = search_ucs_keywords("石", 5)
    print(results)
