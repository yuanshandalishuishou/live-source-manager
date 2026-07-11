"""
频道规则管理模块 — 数据库驱动版

从 app/__init__.py 中提取的独立模块，包含：
- 分类规则数据库访问辅助函数（从 web.models 迁移，供 app 层使用）
- ChannelRules 类：频道规则分类引擎，负责从数据库加载频道分类规则，
  实现最长匹配优先和省份排除映射

核心特性：
1. 规则源从 YAML 改为数据库 (web.models)
2. 三层联合防御：负向排除 → 高优先级精确匹配 → 普通优先级+最长匹配+排除映射
3. 排除检查：防止省间混淆导致错误匹配
4. YAML 回退机制保留，新增 load_from_yaml() 方法
"""

import json as _json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import ClassVar

try:
    import yaml
except ImportError:
    yaml = None


# ── 分类规则数据库访问（从 web.models 迁移，供 app 层使用） ──


def _get_rule_models():
    """延迟获取 web.models 引用（避免 app 层模块级导入 web 包）"""
    try:
        from web import models as _m

        return _m
    except ImportError:
        raise ImportError('web.models 不可用，分类规则查询需要数据库') from None


def get_active_classification_rules_for_app():
    """获取活跃的分类规则列表（app 层版本）"""
    return _get_rule_models().get_active_classification_rules()


def check_exclusion_for_app(kw1: str, kw2: str) -> bool:
    """检查两个关键词之间的排除关系（双参数版本，供 classifier._is_excluded 调用）

    纪码修复 P0-2: 签名由单参数改为双参数，与 classifier._is_excluded 的调用一致。
    内部调用 _get_rule_models().check_exclusion(kw1, kw2) 完成实际排除检查。
    """
    return _get_rule_models().check_exclusion(kw1, kw2)


def get_all_exclusions_for_app() -> list:
    """获取所有排除规则（app 层版本）"""
    return _get_rule_models().get_all_exclusions()


def get_channel_name_mapping_for_app(channel_name: str) -> str:
    """获取频道全名映射（app 层版本）"""
    return _get_rule_models().get_channel_name_mapping(channel_name)


def save_source_categories_for_app(source_id: str, categories: dict):
    """保存源分类结果到数据库（app 层版本）"""
    return _get_rule_models().save_source_categories(source_id, categories)


def get_source_categories_for_app(source_id: str) -> dict:
    """获取源的分类信息（app 层版本）"""
    return _get_rule_models().get_source_categories(source_id)


# ══════════════════════════════════════════════════
# 频道规则分类引擎 (ChannelRules)
# ══════════════════════════════════════════════════

# 默认负向排除关键词（类级常量）
_DEFAULT_NEGATIVE_KEYWORDS = [
    '测试',
    'test',
    'demo',
    '示例',
    'sample',
    '未知',
    'unknown',
    '测试频道',
    '测试台',
    'test channel',
    'demo channel',
]


class ChannelRules:
    """频道规则管理类 — 数据库驱动版（多维分类）

    主要功能：
    - 从数据库加载分类规则
    - 按多个维度（content/region/language/quality/media_type/genre）分别匹配
    - 三层联合防御匹配 + 最长匹配优先 + 排除映射
    - 提取频道的地理信息和类型
    - 提供 YAML 回退和缓存刷新能力
    """

    # 维度列表
    DIMENSIONS: ClassVar[list[str]] = ['content', 'region', 'language', 'quality', 'media_type', 'genre']

    # 省份名称列表，用于省份正则匹配
    PROVINCE_NAMES: ClassVar[list[str]] = [
        '北京',
        '上海',
        '天津',
        '重庆',
        '河北',
        '山西',
        '内蒙古',
        '辽宁',
        '吉林',
        '黑龙江',
        '江苏',
        '浙江',
        '安徽',
        '福建',
        '江西',
        '山东',
        '河南',
        '湖北',
        '湖南',
        '广东',
        '广西',
        '海南',
        '四川',
        '贵州',
        '云南',
        '西藏',
        '陕西',
        '甘肃',
        '青海',
        '宁夏',
        '新疆',
        '香港',
        '澳门',
        '台湾',
    ]

    # 清晰度关键词
    QUALITY_KEYWORDS: ClassVar[dict[str, str]] = {
        '8K': '8K',
        '4K': '4K',
        '超清': '超清',
        '高清': '高清',
        'HD': '高清',
        '标清': '标清',
        '普清': '普清',
    }

    def __init__(self, rules_path: str = '/config/channel_rules.yml'):
        """初始化频道规则管理器

        Args:
            rules_path: YAML规则文件路径（仅作为DB加载失败时的回退）
        """
        self.rules_path = rules_path
        self.logger = logging.getLogger('ChannelRules')
        self.rules = {}  # 原始 YAML 结构（用于回退）
        self.rule_list: list[dict] = []  # 扁平化的规则列表（content 维度）
        self.rules_by_dim: dict[str, list[dict]] = {}  # {dim: [{name, keywords, priority, sort_order}]}
        self.category_rules_raw: list[dict] = []  # DB 原始分类规则
        self.channel_type_rules_raw: list[dict] = []  # DB 原始频道类型规则
        self.province_exclusion_map: dict[str, list[str]] = {}  # 排除映射
        self.negative_keywords: list[str] = list(_DEFAULT_NEGATIVE_KEYWORDS)  # 负向排除关键词

        # 缓存（使用 OrderedDict 确保 LRU popitem(last=False) 正确工作）
        self._category_cache: dict[str, str] = OrderedDict()  # 单分类缓存
        self._multi_category_cache: dict[str, dict[str, str]] = OrderedDict()  # 多维分类缓存
        # 缓存读写锁：防止多线程并发读写 OrderedDict 导致的竞态/损坏
        # （规则引擎在 web 请求线程与后台解析线程间共享，单一锁覆盖两个缓存）
        self._cache_lock: threading.RLock = threading.RLock()
        self._last_load: float = 0.0

        # 加载规则
        self._load_from_db()

    # ── 数据库加载 ─────────────────────────────────

    def _prune_cache(self):
        """限制缓存大小（LRU-style: 保留最近写入的 MAX_CACHE 条）

        使用 OrderedDict 保证 O(1) 头部弹出：
        self._multi_category_cache 和 self._category_cache 都必须是 OrderedDict 实例。
        """
        MAX_CACHE = 200
        with self._cache_lock:
            # 保证是 OrderedDict
            if not isinstance(self._multi_category_cache, OrderedDict):
                self._multi_category_cache = OrderedDict(self._multi_category_cache)
            if not isinstance(self._category_cache, OrderedDict):
                self._category_cache = OrderedDict(self._category_cache)

            if len(self._multi_category_cache) > MAX_CACHE:
                while len(self._multi_category_cache) > MAX_CACHE:
                    self._multi_category_cache.popitem(last=False)
            if len(self._category_cache) > MAX_CACHE:
                while len(self._category_cache) > MAX_CACHE:
                    self._category_cache.popitem(last=False)

    def _load_from_db(self):
        """从数据库加载规则（主加载路径）"""
        try:
            # 加载所有维度的规则
            all_rules = get_active_classification_rules_for_app()

            # 按 rule_type（即 dim_key）分组
            # P3-新-5: 兼容旧格式 rule_type='category' → 'content'，'channel_type' → 'media_type'
            self.rules_by_dim = {}
            _RULE_TYPE_MAP = {
                'category': 'content',
                'channel_type': 'media_type',
            }
            for rule in all_rules:
                raw_dim = rule.get('rule_type', 'content')
                dim = _RULE_TYPE_MAP.get(raw_dim, raw_dim)  # 映射旧格式到新格式
                rule_name = rule.get('name', '').strip()
                keywords_raw = rule.get('keywords', [])
                priority = int(rule.get('priority', 100))
                sort_order = int(rule.get('sort_order', 0))

                # keywords 在 DB 中是 JSON 字符串，解析为列表
                keywords = keywords_raw
                if isinstance(keywords_raw, str):
                    try:
                        keywords = _json.loads(keywords_raw)
                    except (_json.JSONDecodeError, TypeError):
                        keywords = [keywords_raw] if keywords_raw else []

                if not keywords or not isinstance(keywords, list):
                    continue

                if dim not in self.rules_by_dim:
                    self.rules_by_dim[dim] = []
                self.rules_by_dim[dim].append(
                    {
                        'name': rule_name,
                        'keywords': keywords,
                        'priority': priority,
                        'sort_order': sort_order,
                    }
                )

            # 每个维度内按 priority → sort_order 排序
            for dim in self.rules_by_dim:
                self.rules_by_dim[dim].sort(key=lambda r: (r['priority'], r['sort_order']))

            # 保留 content 维度的扁平化规则列表（向后兼容）
            self.rule_list = self.rules_by_dim.get('content', []).copy()

            # 加载排除映射
            self.province_exclusion_map = get_all_exclusions_for_app()

            # 提取负向排除关键词（仅从 content 维度提取）
            neg_set = set(self.negative_keywords)  # 继承类级常量默认值
            for rule in self.rule_list:
                if rule['name'] in ('其他频道', '其他'):
                    kw_list = rule['keywords']
                    if isinstance(kw_list, list):
                        for kw in kw_list:
                            # 排除过于宽泛的词：单字词、'频道/台/channel' 这样的通用词
                            if len(kw) >= 3 and kw.lower() not in ('频道', 'channel', '台'):
                                neg_set.add(kw)
            self.negative_keywords = list(neg_set)

            self._last_load = time.time()
            dim_stats = ', '.join(f'{d}: {len(r)}' for d, r in sorted(self.rules_by_dim.items()))
            self.logger.info(f'✓ 数据库规则加载成功，维度分布: {dim_stats}')
            self.logger.info(f'  负向排除词: {len(self.negative_keywords)} 个')

            # 同步构建 self.rules 字典（兼容旧版 get_category_rules 等方法）
            self._sync_rules_dict()

        except Exception as e:
            self.logger.error(f'✗ 数据库规则加载失败: {e}')
            import traceback

            self.logger.error(f'  详情: {traceback.format_exc()}')
            self.logger.info('ℹ 回退到 YAML 加载...')
            self._fallback_to_yaml()

    def _sync_rules_dict(self):
        """将 DB 规则同步到 self.rules 字典，保持旧接口兼容"""
        categories = []
        for r in self.rule_list:
            categories.append(
                {
                    'name': r['name'],
                    'priority': r['priority'],
                    'keywords': r['keywords'],
                }
            )
        self.rules = {
            'categories': categories,
            'channel_types': {},  # DB 不提供 channel_types 时为空
            'geography': {},  # DB 不提供 geography 时为空
        }

    def _fallback_to_yaml(self):
        """YAML 回退加载"""
        self.logger.info('ℹ 回退到 YAML 加载...')
        self.load_from_yaml(self.rules_path)
        self._last_load = time.time()

    def load_from_yaml(self, path: str | None = None) -> dict:
        """手动从 YAML 文件加载规则（调试/回退用）

        Args:
            path: YAML 文件路径，默认使用 self.rules_path

        Returns:
            Dict: 加载的规则字典
        """
        path = path or self.rules_path
        self.logger.info(f'ℹ 从 YAML 加载规则: {path}')

        if yaml is None:
            self.logger.error('✗ PyYAML 未安装，无法从 YAML 加载')
            self.rules = {}
            return self.rules

        if not os.path.exists(path):
            self.logger.error(f'✗ YAML 文件不存在: {path}')
            self.rules = self.get_empty_rules()
            self._rebuild_from_rules()
            return self.rules

        try:
            encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig']
            content = None

            for encoding in encodings:
                try:
                    with open(path, encoding=encoding) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue

            if content is None:
                with open(path, 'rb') as f:
                    content_bytes = f.read()
                content = content_bytes.decode('utf-8', errors='ignore')

            rules = yaml.safe_load(content)
            if not rules:
                rules = self.get_empty_rules()

            self.rules = rules
            self._rebuild_from_rules()
            self.logger.info(f'✓ YAML 规则加载成功，共 {len(self.rule_list)} 条分类规则')
            return rules

        except Exception as e:
            self.logger.error(f'✗ YAML 加载失败: {e}')
            self.rules = self.get_empty_rules()
            self._rebuild_from_rules()
            return self.rules

    def _rebuild_from_rules(self):
        """从 self.rules (YAML dict) 重建内部数据结构（支持多维）

        P3-新-2: 使用 _dim 字段直接访问，避免 pop() 修改原始 dict，
        保持 source dict（dim_rules 内各元素）不变。
        """
        self.rules_by_dim = {}

        # categories → content 维度
        categories = self.rules.get('categories', [])
        dim_rules = []
        for rule in categories:
            rule_name = rule.get('name', '').strip()
            keywords = rule.get('keywords', [])
            priority = int(rule.get('priority', 100))
            dim_rules.append(
                {
                    'name': rule_name,
                    'keywords': keywords,
                    'priority': priority,
                    'sort_order': 0,
                    '_dim': 'content',  # 始终保持 _dim 字段，不用 pop
                }
            )

        # channel_types → media_type 维度
        channel_types = self.rules.get('channel_types', {})
        for ct_name, ct_keywords in channel_types.items():
            if isinstance(ct_keywords, list):
                dim_rules.append(
                    {
                        'name': ct_name,
                        'keywords': ct_keywords,
                        'priority': 50,
                        'sort_order': 0,
                        '_dim': 'media_type',  # 保持一致，所有条目都有 _dim
                    }
                )

        # 按维度分组——直接读 _dim 字段，不 pop
        self.rules_by_dim = {}
        for r in dim_rules:
            dim = r.get('_dim', 'content')  # 直接 get，不修改原始 dict
            if dim not in self.rules_by_dim:
                self.rules_by_dim[dim] = []
            # 创建一个干净的副本，移除外部的 _dim 字段
            clean_rule = {k: v for k, v in r.items() if k != '_dim'}
            self.rules_by_dim[dim].append(clean_rule)

        # 各维度内排序
        for dim in self.rules_by_dim:
            self.rules_by_dim[dim].sort(key=lambda x: (x['priority'], x['sort_order']))

        # 保留 content 维度作为默认 rule_list（向后兼容）
        self.rule_list = self.rules_by_dim.get('content', [])

        # 重建排除关键词（合并类级常量 + 从规则提取）
        neg_set = set(_DEFAULT_NEGATIVE_KEYWORDS)
        for rule in self.rule_list:
            if rule['name'] in ('其他频道', '其他'):
                kw_list = rule['keywords']
                if isinstance(kw_list, list):
                    for kw in kw_list:
                        if len(kw) >= 3 and kw.lower() not in ('频道', 'channel', '台'):
                            neg_set.add(kw)
                else:
                    if len(str(kw_list)) >= 3:
                        neg_set.add(str(kw_list))
        self.negative_keywords = list(neg_set)

    def reload(self):
        """手动刷新规则数据"""
        with self._cache_lock:
            self.clear_category_cache()
            self._multi_category_cache.clear()
        self._load_from_db()
        self.logger.info('✓ 规则已刷新')

    # ── 规则访问（兼容旧接口）─────────────────────────

    def get_empty_rules(self) -> dict:
        """返回空的规则结构"""
        return {
            'categories': [{'name': '其他频道', 'priority': 100, 'keywords': ['台', '频道', 'channel', 'Channel']}],
            'channel_types': {},
            'geography': {
                'continents': [
                    {
                        'name': '亚洲',
                        'code': 'AS',
                        'countries': [
                            {
                                'name': '中国大陆',
                                'code': 'CN',
                                'keywords': ['中国', 'China', '中华', '华夏'],
                                'provinces': [],
                                'regions': [],
                            }
                        ],
                    }
                ]
            },
        }

    def get_category_rules(self) -> list[dict]:
        """获取分类规则列表，按优先级排序（兼容旧接口）"""
        return sorted(self.rules.get('categories', []), key=lambda x: x.get('priority', 100))

    def get_channel_type_rules(self) -> dict[str, list[str]]:
        """获取频道类型规则（兼容旧接口）"""
        return self.rules.get('channel_types', {})

    def get_geography_rules(self) -> dict:
        """获取地理规则（兼容旧接口）"""
        return self.rules.get('geography', {})

    # ── 核心匹配逻辑（多维）─────────────────────────

    def _match_dimension(self, channel_upper: str, rules: list[dict]) -> list[dict]:
        """对单个维度的规则列表进行匹配，返回按最佳优先排序的结果

        Returns:
            [{'name', 'keyword', 'priority', 'keyword_len', 'sort_order'}, ...]
        """
        matches = []
        for rule in rules:
            for kw in rule['keywords']:
                if isinstance(kw, str) and kw and kw.upper() in channel_upper:
                    matches.append(
                        {
                            'name': rule['name'],
                            'keyword': kw,
                            'priority': rule['priority'],
                            'keyword_len': len(kw),
                            'sort_order': rule['sort_order'],
                        }
                    )

        # 按 priority → 最长关键词(-keyword_len) → sort_order 排序
        matches.sort(key=lambda m: (m['priority'], -m['keyword_len'], m['sort_order']))
        return matches

    # ── 懒加载的频道全名映射查询 ──
    _channel_name_mapping_func = None

    def _get_channel_name_mapping(self, channel_name: str):
        """懒加载的 channel_name_mapping 查询，避免每调用一次就 import 一次"""
        if self._channel_name_mapping_func is None:
            self._channel_name_mapping_func = get_channel_name_mapping_for_app
        return self._channel_name_mapping_func(channel_name)

    def determine_categories(self, channel_name: str) -> dict[str, str]:
        """按维度分别匹配，返回各维度的最佳分类

        Returns:
            Dict: {'content': '央视频道', 'region': '境内', 'language': '汉语',
                   'quality': '高清', 'media_type': '电视节目', 'genre': '综合'}
        """
        if not channel_name:
            return {dim: '未知' for dim in self.DIMENSIONS}

        # 缓存查找
        with self._cache_lock:
            if channel_name in self._multi_category_cache:
                return self._multi_category_cache[channel_name]

        # ── 优先查全名映射表（人工修正/导入的权威数据） ──
        try:
            mapping = self._get_channel_name_mapping(channel_name)
            if mapping:
                result = {
                    'content': mapping.get('content', '其他频道'),
                    'region': mapping.get('region', '未知'),
                    'language': mapping.get('language', '未知'),
                    'quality': mapping.get('quality', '高清'),
                    'media_type': mapping.get('media_type', '电视节目'),
                    'genre': mapping.get('genre', '综合'),
                }
                with self._cache_lock:
                    self._multi_category_cache[channel_name] = result
                return result
        except Exception:
            pass  # 映射表查不到或异常，回退规则引擎

        channel_upper = channel_name.upper()
        result = {}

        for dim in self.DIMENSIONS:
            rules = self.rules_by_dim.get(dim, [])
            if not rules:
                result[dim] = '未知'
                continue

            matches = self._match_dimension(channel_upper, rules)

            if not matches:
                result[dim] = '未知'
                continue

            # ── content 维度应用三层联合防御 ──
            if dim == 'content':
                try:
                    final = self._apply_defense_layers(channel_name, channel_upper, matches)
                    result[dim] = final
                except Exception:
                    result[dim] = matches[0]['name']
            else:
                result[dim] = matches[0]['name']

        # 写入缓存（限制大小，LRU-style）
        with self._cache_lock:
            self._multi_category_cache[channel_name] = result
        self._prune_cache()

        return result

    def determine_category(self, channel_name: str) -> str:
        """向后兼容：返回主内容分类（content 维度）

        同 determine_categories()['content']

        Args:
            channel_name: 频道名称

        Returns:
            str: 分类名称，无匹配返回 '其他频道'
        """
        result = self.determine_categories(channel_name)
        return result.get('content', '其他频道')

    def _apply_defense_layers(self, channel_name: str, channel_upper: str, matches: list[dict]) -> str:
        """对 content 维度应用三层联合防御：负向排除 → 高优先级→ 省际排除+最长匹配

        Args:
            channel_name: 原始频道名
            channel_upper: 大写频道名
            matches: _match_dimension 的返回值，已按(priority, -keyword_len, sort_order)排序

        Returns:
            str: 最终分类名称
        """
        if not matches:
            return '其他频道'

        # ── 第一层：高优先级匹配（priority <= 5，如 CCTV/港澳台）不允许被排除 ──
        high_prio = [m for m in matches if m['priority'] <= 5]
        if high_prio:
            return high_prio[0]['name']

        # ── 第二层：负向排除 ──
        # 频道名中包含负向排词且唯一匹配该词时，归为其他频道
        matched_neg_names = set()
        for m in matches:
            matched_neg_names.add(m['name'])

        # ── 第三层：普通优先级匹配 + 最长匹配 + 排除映射 ──
        # 从最佳起步，检查是否应该被次佳替代
        # 遍历所有匹配项，找到不被排除的最佳结果

        # 先按(priority, -keyword_len, sort_order)排序——matches 已排好
        # 从最佳候选开始，检查是否有次佳候选应该胜出

        # 收集所有不同分类的候选
        candidates_by_name: dict[str, dict] = {}
        for m in matches:
            cat = m['name']
            if cat not in candidates_by_name or m['priority'] < candidates_by_name[cat]['priority']:
                candidates_by_name[cat] = m

        # ── 长短词覆盖检查：
        # 如果一条候选的关键词（如"湖南"）被另一条候选的关键词（如"湖南卫视"）
        # 包含，且更长的关键词属于不同分类，则应有优先权。
        # 先收集所有关键词对，检查包含关系
        names = list(candidates_by_name.keys())
        longer_kw_override = {}
        for i in range(len(names)):
            for j in range(len(names)):
                if i == j:
                    continue
                kw_i = candidates_by_name[names[i]]['keyword']
                kw_j = candidates_by_name[names[j]]['keyword']
                # 如果 kw_i 是 kw_j 的子串但两者不同（如"湖南" in "湖南卫视"）
                # 且两者属于不同分类，则长的优先
                if (
                    len(kw_i) < len(kw_j)
                    and kw_i in kw_j
                    and names[i] != names[j]
                    and (names[j] not in longer_kw_override or len(kw_j) > len(longer_kw_override.get(names[j], '')))
                ):
                    # kw_j 更长，标记 kw_i 的分类应被 kw_j 的分类覆盖
                    longer_kw_override[names[i]] = names[j]

        candidates = sorted(
            candidates_by_name.values(), key=lambda x: (x['priority'], -x['keyword_len'], x['sort_order'])
        )

        if len(candidates) == 1:
            return candidates[0]['name']

        best = candidates[0]

        # 检查 best 的分类是否应该被更长的关键词覆盖
        if best['name'] in longer_kw_override:
            override_name = longer_kw_override[best['name']]
            override_candidate = candidates_by_name.get(override_name)
            if override_candidate:
                self.logger.debug(
                    f"长短词覆盖: '{channel_name}' {best['name']}({best['keyword']}) → {override_name}({override_candidate['keyword']})"
                )
                return override_name

        # 检查最佳是否因为排除映射而应当让位给次佳
        # 例："河北北京西电视台"同时匹配"北京"(北京频道)和"河北"(河北频道)
        # 排除映射 "北京→河北" 表示"北京"不应排挤"河北"，所以选河北频道
        for other in candidates[1:]:
            if self._is_excluded(best['keyword'], other['keyword'], best['name'], other['name']):
                # best 的关键词不应该排挤 other，所以 other 胜出
                return other['name']

        return candidates[0]['name']

    def _is_excluded(self, candidate_kw: str, other_kw: str, candidate_rule_name: str, other_rule_name: str) -> bool:
        """检查 other_kw 是否会因为错误包含而被 candidate_kw 排挤

        仅在以下情况下才触发排除：
        1. candidate_kw 和 other_kw 是不同的词（不是同一个词的上下级）
        2. candidate_rule_name 和 other_rule_name 属于不同的分类
        3. province_exclusion_map 中有映射记录

        Args:
            candidate_kw: 候选关键词
            other_kw: 其他关键词
            candidate_rule_name: 候选规则名（分类名）
            other_rule_name: 其他规则名

        Returns:
            bool: 是否排除
        """
        # 如果属于同一分类，不排除
        if candidate_rule_name == other_rule_name:
            return False

        # 如果候选关键词包含另一关键词（属于上下级关系），不排除
        # 例如 "北京" 包含 "北京新闻" 中的 "北京"，但 "北京" 是上级
        cand_upper = candidate_kw.upper()
        other_upper = other_kw.upper()
        if cand_upper == other_upper:
            return False
        if cand_upper in other_upper or other_upper in cand_upper:
            return False

        # 查排除映射表
        try:
            return check_exclusion_for_app(candidate_kw, other_kw)
        except Exception as e:
            self.logger.warning(f'排除检查异常: {e}')
            return False

    def _cache_result(self, channel_name: str, category: str):
        """缓存分类结果并修剪"""
        with self._cache_lock:
            self._category_cache[channel_name] = category
        # 使用统一的 _prune_cache 方法修剪（P3-新-1: 替代手动list切片）
        self._prune_cache()

    def clear_category_cache(self):
        """清空分类缓存"""
        with self._cache_lock:
            self._category_cache.clear()
        self.logger.debug('分类缓存已清空')

    # ── 频道信息提取 ─────────────────────────────────

    def extract_channel_info(self, channel_name: str, source_id: int | None = None) -> dict:
        """使用规则提取频道信息 — 多维分类版

        提取的信息包括各维度分类 + 原有字段

        Args:
            channel_name: 原始频道名称
            source_id: 可选的源ID，提供后将各维度分类结果写入数据库

        Returns:
            Dict: 包含完整多维分类信息的字典
        """
        # 初始化默认信息
        info = {
            'name': channel_name.strip(),
            'source_id': source_id,
            'category': '其他频道',  # 主分类（content 维度）
            'content': '其他频道',
            'region': '未知',
            'language': 'zh',
            'quality': None,
            'media_type': 'Other',
            'genre': '未知',
            'province': None,
            'country': 'CN',
            'channel_type': None,
            'city': None,
            'continent': 'Asia',
        }

        if not channel_name:
            return info

        clean_name = channel_name.strip()

        # ── 各维度分类 ──
        categories = self.determine_categories(channel_name)
        info['content'] = categories.get('content', '其他频道')
        info['category'] = info['content']  # 主分类向后兼容

        # region 维度（优先用规则匹配，回退到原有地理检测）
        region_from_rules = categories.get('region', '未知')
        if region_from_rules != '未知':
            info['region'] = region_from_rules

        # language 维度（优先用规则匹配）
        lang_from_rules = categories.get('language', '未知')
        if lang_from_rules != '未知':
            info['language'] = lang_from_rules

        # quality 维度（优先用规则匹配，再补充频道名关键词检测）
        quality_from_rules = categories.get('quality', '未知')
        if quality_from_rules != '未知':
            info['quality'] = quality_from_rules

        # media_type 维度
        media_from_rules = categories.get('media_type', '未知')
        if media_from_rules != '未知':
            info['media_type'] = media_from_rules

        # genre 维度
        genre_from_rules = categories.get('genre', '未知')
        if genre_from_rules != '未知':
            info['genre'] = genre_from_rules

        # ── 推断媒体类型（频道名关键字回退检测） ──
        if info['media_type'] == 'Other' or info['media_type'] == '未知':
            cat = info['category']
            ch_upper = clean_name.upper()
            if '收音机' in cat or cat in ('在线音频',):
                info['media_type'] = 'Radio'
            elif cat in ('央视频道', '卫视频道') or cat.endswith('频道') or cat in ('港澳台',):
                info['media_type'] = 'TV'
            elif any(kw in ch_upper for kw in ['FM', 'AM', '广播', 'RADIO']):
                info['media_type'] = 'Radio'
            elif any(kw in ch_upper for kw in ['TV', 'TELEVISION', '电视']):
                info['media_type'] = 'TV'

        # ── 提取清晰度（频道名关键字回退检测） ──
        if not info['quality']:
            for pattern, quality in sorted(self.QUALITY_KEYWORDS.items(), key=lambda x: -len(x[0])):
                if pattern.lower() in clean_name.lower():
                    info['quality'] = quality
                    break

        # ── 语言识别回退 ──
        if info['language'] == '未知':
            lang_patterns = {
                'en': [
                    '英文',
                    '英语',
                    'ENGLISH',
                    'BBC',
                    'CNN',
                    'FOX',
                    'HBO',
                    'DISCOVERY',
                    'NATIONAL GEOGRAPHIC',
                    'AL JAZEERA',
                ],
                'ja': ['日语', '日文', 'JAPANESE', 'NHK'],
                'ko': ['韩语', '韩文', 'KOREAN', 'KBS', 'MBC', 'SBS'],
                'ru': ['俄语', '俄文', 'RUSSIAN', 'RT'],
                'fr': ['法语', '法文', 'FRENCH', 'FRANCE'],
                'de': ['德语', '德文', 'GERMAN'],
            }
            ch_upper = clean_name.upper()
            for lang, keywords in lang_patterns.items():
                if any(kw in ch_upper for kw in keywords):
                    info['language'] = lang
                    break
            else:
                info['language'] = 'zh'

        # ── 提取省份（优先匹配与 content 分类一致的省份） ──
        cat = info['category']
        all_found_provinces = []
        for province in self.PROVINCE_NAMES:
            if province in clean_name:
                all_found_provinces.append(province)

        if all_found_provinces:
            matched = None
            for p in all_found_provinces:
                if p in cat or cat.startswith(p):
                    matched = p
                    break
            if not matched:
                all_found_provinces.sort(key=len, reverse=True)
                matched = all_found_provinces[0]
            info['province'] = matched

        # ── 使用YAML地理规则（如果可用）提取国家/地区 ──
        self._extract_geography(channel_name, info)

        # ── 如果提供了 source_id，将各维度分类结果写入数据库 ──
        if source_id is not None:
            try:
                categories_to_save = {dim: info.get(dim, '未知') for dim in self.DIMENSIONS}
                save_source_categories_for_app(source_id, categories_to_save)
            except Exception:
                # 落库失败不影响分类结果
                pass

        self.logger.debug(f'频道信息提取完成: {info}')
        return info

    def _extract_geography(self, channel_name: str, info: dict):
        """使用地理规则提取国家/地区/省份信息（兼容旧接口）

        从 YAML 规则中提取，如果 YAML 不可用则跳过。
        """
        if not self.rules:
            return

        geography_rules = self.get_geography_rules()
        if not geography_rules:
            return

        clean_name = re.sub(r'[^\w\u4e00-\u9fff]', '', channel_name.upper())
        country_matched = False

        for continent in geography_rules.get('continents', []):
            for country in continent.get('countries', []):
                for keyword in country.get('keywords', []):
                    if keyword.upper() in clean_name:
                        info['country'] = country.get('code', 'CN')
                        info['continent'] = continent.get('name', 'Asia')
                        country_matched = True
                        break

                if not country_matched and country.get('code') == 'CN':
                    for province in country.get('provinces', []):
                        for keyword in province.get('keywords', []):
                            if keyword.upper() in clean_name:
                                info['province'] = province.get('name')
                                info['country'] = 'CN'
                                info['continent'] = 'Asia'
                                country_matched = True
                                break
                        if country_matched:
                            break

                if country_matched:
                    for region in country.get('regions', []):
                        for keyword in region.get('keywords', []):
                            if keyword.upper() in clean_name:
                                info['country'] = region.get('code', 'CN')
                                info['region'] = region.get('name')
                                break
                    break

            if country_matched:
                break

    # ── 测试工具 ────────────────────────────────────

    def test_classification(self, test_cases: list[tuple] | None = None):
        """测试分类准确性 — 用于调试和验证

        Args:
            test_cases: 测试用例列表，格式为 [(频道名称, 期望分类), ...]
        """
        if test_cases is None:
            test_cases = [
                ('CCTV-1 综合', '央视频道'),
                ('CCTV-13 新闻', '央视频道'),
                ('湖南卫视', '卫视频道'),
                ('北京卫视', '卫视频道'),
                ('北京新闻', '北京频道'),
                ('FM103.9 交通广播', '收音机'),
                ('经典电影频道', '影视频道'),
                ('NBA 直播', '体育频道'),
                ('少儿动画', '少儿频道'),
                ('香港TVB', '港澳台'),
                ('未知频道', '其他频道'),
            ]

        self.logger.info('🧪 开始频道分类测试...')
        results = []

        for channel_name, expected in test_cases:
            actual = self.determine_category(channel_name)
            status = '✓' if actual == expected else '✗'
            results.append((channel_name, expected, actual, status))

            if status == '✗':
                self.logger.warning(f"{status} '{channel_name}' -> 实际: {actual}, 期望: {expected}")
            else:
                self.logger.info(f"{status} '{channel_name}' -> {actual}")

        total = len(results)
        correct = sum(1 for r in results if r[3] == '✓')
        accuracy = correct / total * 100

        self.logger.info(f'📊 测试结果: {correct}/{total} 正确 ({accuracy:.1f}%)')
        return results
