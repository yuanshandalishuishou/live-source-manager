#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
频道规则管理模块 - 修复版
负责从YAML文件加载和管理频道分类规则

修复内容：
1. 修正re模块导入位置
2. 增强错误处理和日志记录
3. 优化分类逻辑
"""

import os
import re  # 修复：将re模块移到文件开头
import logging
import yaml
from typing import Dict, List, Optional

class ChannelRules:
    """频道规则管理类 - 修复版
    
    主要功能：
    - 从YAML文件加载分类规则
    - 提取频道的地理信息和类型
    - 根据规则确定频道分类
    - 提供容错机制和错误处理
    """
    
    def __init__(self, rules_path: str = "/config/channel_rules.yml"):
        """初始化频道规则管理器
        
        Args:
            rules_path: YAML规则文件路径，默认为容器内标准路径
        """
        self.rules_path = rules_path
        self.logger = logging.getLogger('ChannelRules')
        self.rules = self.load_rules()
    
    def load_rules(self) -> Dict:
        """从YAML文件加载频道规则 - 增强容错版
        
        支持多种编码格式，提供详细的错误信息
        
        Returns:
            Dict: 加载的规则字典，如果失败返回空规则
        """
        if not os.path.exists(self.rules_path):
            self.logger.error(f"✗ 频道规则文件不存在: {self.rules_path}")
            self.logger.info("ℹ 使用默认空规则配置")
            return self.get_empty_rules()
        
        try:
            # 尝试多种编码读取
            encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig']
            content = None
            
            for encoding in encodings:
                try:
                    with open(self.rules_path, 'r', encoding=encoding) as f:
                        content = f.read()
                    self.logger.info(f"✓ 规则文件加载成功，编码: {encoding}")
                    break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                # 所有编码都失败，使用二进制读取
                with open(self.rules_path, 'rb') as f:
                    content_bytes = f.read()
                content = content_bytes.decode('utf-8', errors='ignore')
                self.logger.warning("⚠ 使用二进制方式加载规则文件，可能存在编码问题")
            
            # 解析YAML内容
            rules = yaml.safe_load(content)
            
            if not rules:
                self.logger.warning("⚠ 频道规则文件为空，使用默认空规则")
                return self.get_empty_rules()
            
            # 验证规则结构
            if self.validate_rules_structure(rules):
                self.logger.info(f"✓ 规则结构验证通过，包含 {len(rules.get('categories', []))} 个分类")
                return rules
            else:
                self.logger.error("✗ 规则结构验证失败，使用默认空规则")
                return self.get_empty_rules()
                
        except yaml.YAMLError as e:
            self.logger.error(f"✗ YAML解析失败: {e}")
            return self.get_empty_rules()
        except Exception as e:
            self.logger.error(f"✗ 加载频道规则文件失败: {e}")
            return self.get_empty_rules()
    
    def validate_rules_structure(self, rules: Dict) -> bool:
        """验证规则文件结构完整性
        
        Args:
            rules: 解析后的规则字典
            
        Returns:
            bool: 结构是否有效
        """
        required_sections = ['categories', 'channel_types', 'geography']
        
        for section in required_sections:
            if section not in rules:
                self.logger.error(f"✗ 规则文件缺少必要部分: {section}")
                return False
        
        # 验证分类规则
        categories = rules.get('categories', [])
        if not categories:
            self.logger.warning("⚠ 分类规则列表为空")
        
        # 验证每个分类都有必要的字段
        for i, category in enumerate(categories):
            if 'name' not in category:
                self.logger.error(f"✗ 第 {i} 个分类缺少 'name' 字段")
                return False
            if 'priority' not in category:
                self.logger.error(f"✗ 分类 '{category.get('name')}' 缺少 'priority' 字段")
                return False
        
        return True
    
    def get_empty_rules(self) -> Dict:
        """返回空的规则结构 - 确保程序能继续运行
        
        Returns:
            Dict: 包含基本结构的空规则字典
        """
        return {
            'categories': [
                {
                    'name': '其他频道',
                    'priority': 100,
                    'keywords': ['台', '频道', 'channel']
                }
            ],
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
                                'regions': []
                            }
                        ]
                    }
                ]
            }
        }
    
    def get_category_rules(self) -> List[Dict]:
        """获取分类规则列表
        
        Returns:
            List[Dict]: 分类规则列表，按优先级排序
        """
        categories = self.rules.get('categories', [])
        # 按优先级排序，数值越小优先级越高
        return sorted(categories, key=lambda x: x.get('priority', 100))
    
    def get_channel_type_rules(self) -> Dict[str, List[str]]:
        """获取频道类型规则
        
        Returns:
            Dict[str, List[str]]: 频道类型到关键词列表的映射
        """
        return self.rules.get('channel_types', {})
    
    def get_geography_rules(self) -> Dict:
        """获取地理规则
        
        Returns:
            Dict: 包含大洲、国家、地区信息的嵌套字典
        """
        return self.rules.get('geography', {})
    
    def extract_channel_info(self, channel_name: str) -> Dict:
        """使用规则提取频道信息 - 增强版
        
        提取的信息包括：
        - 国家、地区、省份、城市
        - 语言
        - 频道类型
        - 大洲信息
        
        Args:
            channel_name: 原始频道名称
            
        Returns:
            Dict: 包含提取信息的字典
        """
        # 初始化默认信息（中国大陆频道）
        info = {
            'country': 'CN',
            'region': None,
            'language': 'zh',
            'channel_type': None,
            'province': None,
            'city': None,
            'continent': 'Asia'
        }
        
        if not self.rules:
            self.logger.warning("⚠ 无规则可用，返回默认频道信息")
            return info
        
        # 清理频道名称：移除特殊字符，转为大写便于匹配
        clean_name = re.sub(r'[^\w\u4e00-\u9fff]', '', channel_name.upper())
        self.logger.debug(f"清理频道名称: '{channel_name}' -> '{clean_name}'")
        
        # 使用YAML规则识别国家/地区
        geography_rules = self.get_geography_rules()
        country_matched = False
        
        for continent in geography_rules.get('continents', []):
            for country in continent.get('countries', []):
                # 检查国家关键词
                for keyword in country.get('keywords', []):
                    if keyword.upper() in clean_name:
                        info['country'] = country.get('code', 'CN')
                        info['continent'] = continent.get('name', 'Asia')
                        country_matched = True
                        self.logger.debug(f"匹配国家: {country.get('name')} - 关键词: {keyword}")
                        break
                
                # 如果没有明确的国家关键词，但频道名称包含中国省份信息，则默认为中国
                if not country_matched and country.get('code') == 'CN':
                    for province in country.get('provinces', []):
                        for keyword in province.get('keywords', []):
                            if keyword.upper() in clean_name:
                                info['country'] = 'CN'
                                info['continent'] = 'Asia'
                                info['province'] = province.get('name')
                                country_matched = True
                                self.logger.debug(f"匹配中国省份: {province.get('name')} - 关键词: {keyword}")
                                break
                        if country_matched:
                            break
                
                if country_matched:
                    # 检查特别行政区（港澳台）
                    for region in country.get('regions', []):
                        for keyword in region.get('keywords', []):
                            if keyword.upper() in clean_name:
                                info['country'] = region.get('code', 'CN')
                                info['region'] = region.get('name')
                                self.logger.debug(f"匹配特别行政区: {region.get('name')} - 关键词: {keyword}")
                                break
                    
                    break
            
            if country_matched:
                break
        
        # 使用YAML规则识别频道类型
        channel_type_rules = self.get_channel_type_rules()
        for channel_type, keywords in channel_type_rules.items():
            for keyword in keywords:
                if keyword.upper() in clean_name:
                    info['channel_type'] = channel_type
                    self.logger.debug(f"匹配频道类型: {channel_type} - 关键词: {keyword}")
                    break
            if info['channel_type']:
                break
        
        # 识别语言（基于关键词）
        language_keywords = {
            'en': ['英文', '英语', 'EN', 'ENG', 'ENGLISH'],
            'ja': ['日语', '日文', 'JP', 'JAPANESE'],
            'ko': ['韩语', '韩文', 'KR', 'KOREAN'],
            'ru': ['俄语', '俄文', 'RU', 'RUSSIAN'],
            'fr': ['法语', '法文', 'FR', 'FRENCH'],
            'de': ['德语', '德文', 'DE', 'GERMAN']
        }
        
        for lang, keywords in language_keywords.items():
            if any(keyword in clean_name for keyword in keywords):
                info['language'] = lang
                self.logger.debug(f"匹配语言: {lang}")
                break
        
        self.logger.debug(f"频道信息提取完成: {info}")
        return info
    
    def determine_category(self, channel_name: str) -> str:
        """根据频道名称确定分类 - 使用优先级系统
        
        分类规则按优先级排序，数值越小优先级越高
        第一个匹配的关键词决定最终分类
        
        Args:
            channel_name: 频道名称
            
        Returns:
            str: 分类名称，如果没有匹配返回'其他频道'
        """
        if not self.rules:
            self.logger.warning("⚠ 无规则可用，返回默认分类")
            return '其他频道'
        
        channel_name_upper = channel_name.upper()
        category_rules = self.get_category_rules()
        
        self.logger.debug(f"开始分类: '{channel_name}'")
        
        for rule in category_rules:
            rule_name = rule.get('name', '未知分类')
            keywords = rule.get('keywords', [])
            priority = rule.get('priority', 100)
            
            for keyword in keywords:
                if keyword.upper() in channel_name_upper:
                    self.logger.debug(f"分类匹配: '{channel_name}' -> '{rule_name}' (优先级: {priority}, 关键词: {keyword})")
                    return rule_name
        
        # 没有匹配任何分类规则
        self.logger.debug(f"分类未匹配: '{channel_name}' -> '其他频道'")
        return '其他频道'
    
    def test_classification(self, test_cases: List[tuple] = None):
        """测试分类准确性 - 用于调试和验证
        
        Args:
            test_cases: 测试用例列表，格式为[(频道名称, 期望分类), ...]
        """
        if test_cases is None:
            # 默认测试用例
            test_cases = [
                ("CCTV-1 综合", "央视频道"),
                ("CCTV-13 新闻", "央视频道"),
                ("湖南卫视", "卫视频道"),
                ("北京卫视", "卫视频道"),
                ("北京新闻", "北京频道"),
                ("FM103.9 交通广播", "收音机"),
                ("经典电影频道", "影视频道"),
                ("NBA 直播", "体育频道"),
                ("少儿动画", "少儿频道"),
                ("香港TVB", "港澳台"),
                ("未知频道", "其他频道")
            ]
        
        self.logger.info("🧪 开始频道分类测试...")
        results = []
        
        for channel_name, expected in test_cases:
            actual = self.determine_category(channel_name)
            status = "✓" if actual == expected else "✗"
            results.append((channel_name, expected, actual, status))
            
            if status == "✗":
                self.logger.warning(f"{status} '{channel_name}' -> 实际: {actual}, 期望: {expected}")
            else:
                self.logger.info(f"{status} '{channel_name}' -> {actual}")
        
        # 统计结果
        total = len(results)
        correct = sum(1 for r in results if r[3] == "✓")
        accuracy = correct / total * 100
        
        self.logger.info(f"📊 测试结果: {correct}/{total} 正确 ({accuracy:.1f}%)")
        
        return results