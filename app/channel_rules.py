#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
频道规则管理模块
负责从YAML文件加载和管理频道分类规则
"""

import os
import logging
import yaml
import re  # 移到文件开头
from typing import Dict, List, Optional

class ChannelRules:
    """频道规则管理类 - 仅从YAML文件加载规则"""
    
    def __init__(self, rules_path: str = "/config/channel_rules.yml"):
        self.rules_path = rules_path
        self.rules = self.load_rules()
    
    def load_rules(self) -> Dict:
        """从YAML文件加载频道规则"""
        if not os.path.exists(self.rules_path):
            logging.error(f"频道规则文件不存在: {self.rules_path}")
            return self.get_empty_rules()
        
        try:
            with open(self.rules_path, 'r', encoding='utf-8') as f:
                rules = yaml.safe_load(f)
                if not rules:
                    logging.warning("频道规则文件为空，使用空规则")
                    return self.get_empty_rules()
                return rules
        except Exception as e:
            logging.error(f"加载频道规则文件失败: {e}")
            return self.get_empty_rules()
    
    def get_empty_rules(self) -> Dict:
        """返回空的规则结构"""
        return {
            'categories': [],
            'channel_types': {},
            'geography': {'continents': []}
        }
    
    def get_category_rules(self) -> List[Dict]:
        """获取分类规则"""
        return self.rules.get('categories', [])
    
    def get_channel_type_rules(self) -> Dict[str, List[str]]:
        """获取频道类型规则"""
        return self.rules.get('channel_types', {})
    
    def get_geography_rules(self) -> Dict:
        """获取地理规则"""
        return self.rules.get('geography', {})
    
    def extract_channel_info(self, channel_name: str) -> Dict:
        """使用规则提取频道信息"""
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
            return info
        
        # 清理频道名称
        clean_name = re.sub(r'[^\w\u4e00-\u9fff]', '', channel_name.upper())
        
        # 使用YAML规则识别国家/地区
        geography_rules = self.get_geography_rules()
        
        for continent in geography_rules.get('continents', []):
            for country in continent.get('countries', []):
                # 检查国家关键词
                country_matched = False
                for keyword in country.get('keywords', []):
                    if keyword.upper() in clean_name:
                        info['country'] = country.get('code', 'CN')
                        info['continent'] = continent.get('name', 'Asia')
                        country_matched = True
                        break
                
                # 如果没有明确的国家关键词，但频道名称包含省份信息，则默认为中国
                if not country_matched and country.get('code') == 'CN':
                    for province in country.get('provinces', []):
                        for keyword in province.get('keywords', []):
                            if keyword.upper() in clean_name:
                                info['country'] = 'CN'
                                info['continent'] = 'Asia'
                                info['province'] = province.get('name')
                                country_matched = True
                                break
                        if country_matched:
                            break
                
                if country_matched:
                    # 检查省份/地区
                    for province in country.get('provinces', []):
                        for keyword in province.get('keywords', []):
                            if keyword.upper() in clean_name:
                                info['province'] = province.get('name')
                                break
                    
                    # 检查特别行政区
                    for region in country.get('regions', []):
                        for keyword in region.get('keywords', []):
                            if keyword.upper() in clean_name:
                                info['country'] = region.get('code', 'CN')
                                info['region'] = region.get('name')
                                break
                    
                    break
            if info['country'] != 'CN':
                break
        
        # 使用YAML规则识别频道类型
        channel_type_rules = self.get_channel_type_rules()
        for channel_type, keywords in channel_type_rules.items():
            for keyword in keywords:
                if keyword in clean_name:
                    info['channel_type'] = channel_type
                    break
            if info['channel_type']:
                break
        
        # 识别语言
        if any(keyword in clean_name for keyword in ['英文', '英语', 'EN', 'ENG']):
            info['language'] = 'en'
        elif any(keyword in clean_name for keyword in ['日语', '日文', 'JP']):
            info['language'] = 'ja'
        elif any(keyword in clean_name for keyword in ['韩语', '韩文', 'KR']):
            info['language'] = 'ko'
        elif any(keyword in clean_name for keyword in ['俄语', '俄文', 'RU']):
            info['language'] = 'ru'
        
        return info
    
    def determine_category(self, channel_name: str) -> str:
        """根据频道名称判断分类"""
        if not self.rules:
            return '其他频道'
            
        channel_name_upper = channel_name.upper()
        category_rules = self.get_category_rules()
        
        # 按优先级排序规则
        sorted_rules = sorted(category_rules, key=lambda x: x.get('priority', 100))
        
        for rule in sorted_rules:
            for keyword in rule.get('keywords', []):
                if keyword.upper() in channel_name_upper:
                    return rule.get('name', '其他频道')
        
        return '其他频道'