#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3U文件生成器模块 - 增强修复版
增强内容：
- 修复排序时的None值类型错误
- 支持媒体类型分类
- 优化分组逻辑
- 增强EXTINF信息
- 改进错误处理
"""

import os
import re
from typing import List, Dict
from config_manager import Config

class EnhancedM3UGenerator:
    """增强版M3U文件生成器 - 支持分层筛选和智能分类"""
    
    def __init__(self, config: Config, logger):
        """
        初始化M3U生成器
        
        Args:
            config: 配置管理器实例
            logger: 日志记录器实例
        """
        self.config = config
        self.logger = logger
        self.output_params = config.get_output_params()
        self.filter_params = config.get_filter_params()
        self.ua_position = config.get_ua_position()
        self.ua_enabled = config.is_ua_enabled()
    
    def generate_enhanced_m3u(self, sources: List[Dict], level: str = "base") -> str:
        """生成增强版M3U文件内容
        
        Args:
            sources: 源数据列表
            level: 层级标识 (base/qualified)
            
        Returns:
            str: M3U文件内容
        """
        output_lines = ["#EXTM3U"]
        
        # 根据层级决定筛选策略
        if level == "base":
            # 基础层级：使用所有传入的源（已经过分辨率筛选）
            filtered_sources = sources
            self.logger.info(f"基础层级: 使用 {len(filtered_sources)} 个源")
        else:
            # 高级层级：根据条件筛选
            filtered_sources = self.enhanced_filter_sources(sources)
            self.logger.info(f"高级层级: 从 {len(sources)} 个源中筛选出 {len(filtered_sources)} 个合格源")
        
        # 按增强分组对源进行排序和分组
        grouped_sources = self.enhanced_group_and_sort_sources(filtered_sources, level)
        
        # 生成M3U内容
        for group, group_sources in grouped_sources.items():
            # 添加分组注释
            output_lines.append(f"#EXTGRP:{group}")
            
            for source in group_sources:
                extinf = self.build_enhanced_extinf(source, level)
                output_lines.append(extinf)
                
                # 构建URL
                url = source['url']
                if self.ua_enabled and source.get('user_agent'):
                    if self.ua_position == 'url':
                        url = f"{url}|User-Agent={source['user_agent']}"
                
                output_lines.append(url)
        
        return "\n".join(output_lines)
    
    def generate_enhanced_txt(self, sources: List[Dict], level: str = "base") -> str:
        """生成增强版TXT文件内容
        
        Args:
            sources: 源数据列表
            level: 层级标识 (base/qualified)
            
        Returns:
            str: TXT文件内容
        """
        output_lines = []
        
        # 根据层级决定筛选策略
        if level == "base":
            filtered_sources = sources
            self.logger.info(f"基础层级TXT: 使用 {len(filtered_sources)} 个源")
        else:
            filtered_sources = self.enhanced_filter_sources(sources)
            self.logger.info(f"高级层级TXT: 从 {len(sources)} 个源中筛选出 {len(filtered_sources)} 个合格源")
        
        # 按增强分组对源进行排序和分组
        grouped_sources = self.enhanced_group_and_sort_sources(filtered_sources, level)
        
        # 生成TXT内容
        for group, group_sources in grouped_sources.items():
            # 添加分组注释
            output_lines.append(f"# {group}")
            
            for source in group_sources:
                # 构建频道行
                channel_line = f"{source['name']},{source['url']}"
                
                # 添加UA信息
                if self.ua_enabled and source.get('user_agent'):
                    if self.ua_position == 'url':
                        channel_line = f"{source['name']},{source['url']}|User-Agent={source['user_agent']}"
                    else:
                        channel_line = f"{source['name']},{source['url']}#User-Agent={source['user_agent']}"
                
                output_lines.append(channel_line)
            
            # 添加空行分隔不同分组
            output_lines.append("")
        
        return "\n".join(output_lines)
    
    def enhanced_filter_sources(self, sources: List[Dict]) -> List[Dict]:
        """增强版源过滤 - 用于高级层级筛选
        
        Args:
            sources: 源数据列表
            
        Returns:
            List[Dict]: 过滤后的源数据列表
        """
        filtered = []
        for source in sources:
            # 基本状态检查
            if source.get('status') != 'success':
                continue
            
            # 音频内容简化检查
            media_type = source.get('media_type', 'video')
            if media_type in ['radio', 'audio']:
                # 音频只需要检查延迟
                response_time = source.get('response_time', 9999)
                if response_time <= self.filter_params['max_latency']:
                    filtered.append(source)
                continue
            
            # 视频内容详细检查
            # 延迟检查
            response_time = source.get('response_time', 9999)
            if response_time > self.filter_params['max_latency']:
                continue
            
            # 分辨率检查
            min_resolution = self.filter_params['min_resolution']
            max_resolution = self.filter_params['max_resolution']
            resolution_filter_mode = self.filter_params.get('resolution_filter_mode', 'range')
            
            if min_resolution or max_resolution:
                resolution = source.get('resolution', '')
                
                if resolution_filter_mode == 'range':
                    if min_resolution and not self.is_resolution_meet_min(resolution, min_resolution):
                        continue
                    if max_resolution and not self.is_resolution_meet_max(resolution, max_resolution):
                        continue
                elif resolution_filter_mode == 'min_only':
                    if min_resolution and not self.is_resolution_meet_min(resolution, min_resolution):
                        continue
                elif resolution_filter_mode == 'max_only':
                    if max_resolution and not self.is_resolution_meet_max(resolution, max_resolution):
                        continue
            
            # 比特率检查
            bitrate = source.get('bitrate', 0)
            if bitrate > 0 and bitrate < self.filter_params['min_bitrate']:
                continue
            
            # 特殊要求检查
            if self.filter_params['must_hd'] and not source.get('is_hd', False):
                continue
                
            if self.filter_params['must_4k'] and not source.get('is_4k', False):
                continue
            
            # 速度检查
            speed = source.get('download_speed', 0)
            if speed < self.filter_params['min_speed']:
                continue
            
            filtered.append(source)
        
        return filtered
    
    def enhanced_group_and_sort_sources(self, sources: List[Dict], level: str) -> Dict[str, List[Dict]]:
        """增强版分组和排序逻辑 - 修复None值问题
        
        Args:
            sources: 源数据列表
            level: 层级标识
            
        Returns:
            Dict[str, List[Dict]]: 分组后的源数据
        """
        group_by = self.output_params['group_by']
        grouped = {}
        
        # 第一步：按媒体类型预分组
        media_groups = {'video': [], 'audio': [], 'radio': []}
        for source in sources:
            media_type = source.get('media_type', 'video')
            if media_type in media_groups:
                media_groups[media_type].append(source)
            else:
                media_groups['video'].append(source)
        
        # 第二步：对每个媒体类型进行详细分组
        for media_type, media_sources in media_groups.items():
            if not media_sources:
                continue
            
            if media_type == 'video':
                # 视频内容按配置分组
                for source in media_sources:
                    group_key = self.get_group_key(source, group_by)
                    if group_key not in grouped:
                        grouped[group_key] = []
                    grouped[group_key].append(source)
            else:
                # 音频内容特殊分组
                audio_group_key = "收音机" if media_type == 'radio' else "在线音频"
                if audio_group_key not in grouped:
                    grouped[audio_group_key] = []
                grouped[audio_group_key].extend(media_sources)
        
        # 第三步：对每个分组内的源进行排序 - 修复None值问题
        for group_key, group_sources in grouped.items():
            # 根据媒体类型使用不同的排序策略
            if '收音机' in group_key or '在线音频' in group_key:
                # 音频按名称排序 - 修复None值问题
                group_sources.sort(key=lambda x: x.get('name', '') or '')
            else:
                # 视频按质量排序 - 修复None值问题
                group_sources.sort(key=lambda x: (
                    x.get('continent', '') or '',
                    x.get('country', '') or '',
                    x.get('province', '') or '',
                    -(x.get('download_speed', 0) or 0),  # 修复None值
                    x.get('response_time', 9999) or 9999,  # 修复None值
                    x.get('name', '') or ''
                ))
        
        return grouped
    
    def get_group_key(self, source: Dict, group_by: str) -> str:
        """获取分组键
        
        Args:
            source: 源数据字典
            group_by: 分组依据
            
        Returns:
            str: 分组键
        """
        if group_by == 'country':
            return source.get('country', 'Unknown') or 'Unknown'
        elif group_by == 'region':
            return source.get('region', 'Unknown') or 'Unknown'
        elif group_by == 'category':
            return source.get('category', 'Unknown') or 'Unknown'
        elif group_by == 'media_type':
            return source.get('media_type', 'video') or 'video'
        elif group_by == 'source':
            return source.get('source_type', 'Unknown') or 'Unknown'
        else:
            return 'All Channels'
    
    def build_enhanced_extinf(self, source: Dict, level: str) -> str:
        """构建增强版EXTINF行
        
        Args:
            source: 源数据字典
            level: 层级标识
            
        Returns:
            str: EXTINF行内容
        """
        parts = [f"#EXTINF:-1"]
        
        # 基本信息
        tvg_id = re.sub(r'[^a-zA-Z0-9]', '_', source['name']).lower()
        parts.append(f'tvg-id="{tvg_id}"')
        parts.append(f'tvg-name="{source["name"]}"')
        
        # 图标
        if source.get('logo'):
            parts.append(f'tvg-logo="{source["logo"]}"')
        
        # 分组标题
        group_title = source.get('group', 'Unknown')
        if source.get('category'):
            group_title = source.get('category', 'Unknown')
        parts.append(f'group-title="{group_title}"')
        
        # 媒体类型信息
        media_type = source.get('media_type', 'video')
        parts.append(f'media-type="{media_type}"')
        
        # 地区信息
        if source.get('country'):
            parts.append(f'tvg-country="{source["country"]}"')
        if source.get('region'):
            parts.append(f'tvg-region="{source["region"]}"')
        if source.get('province'):
            parts.append(f'tvg-province="{source["province"]}"')
        
        # UA信息
        if self.ua_enabled and self.ua_position == 'extinf' and source.get('user_agent'):
            parts.append(f'user-agent="{source["user_agent"]}"')
        
        # 质量信息（根据层级决定详细程度）
        if level == "qualified":
            if source.get('response_time'):
                parts.append(f'response-time="{source.get("response_time")}ms"')
            if source.get('download_speed'):
                parts.append(f'download-speed="{source.get("download_speed"):.1f}KB/s"')
        
        # 技术信息
        if source.get('resolution'):
            parts.append(f'resolution="{source.get("resolution")}"')
        if source.get('bitrate'):
            parts.append(f'bitrate="{source.get("bitrate")}kbps"')
        
        # 状态信息
        if source.get('status') != 'success':
            parts.append(f'status="{source.get("status")}"')
        
        # 频道名称
        parts.append(f',{source["name"]}')
        
        return " ".join(parts)
    
    def is_resolution_meet_min(self, resolution: str, min_resolution: str) -> bool:
        """检查分辨率是否满足最低要求
        
        Args:
            resolution: 实际分辨率
            min_resolution: 最低要求分辨率
            
        Returns:
            bool: 是否满足要求
        """
        if not resolution or not min_resolution:
            return True
        
        def parse_resolution(res):
            """解析分辨率字符串为(宽度, 高度)元组"""
            if 'x' in res:
                parts = res.split('x')
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except (ValueError, TypeError):
                        return 0, 0
            elif res.endswith('p'):
                try:
                    height = int(res[:-1])
                    width = int(height * 16 / 9)  # 假设宽高比为16:9
                    return width, height
                except (ValueError, TypeError):
                    return 0, 0
            return 0, 0
        
        res_width, res_height = parse_resolution(resolution)
        min_width, min_height = parse_resolution(min_resolution)
        
        return res_width >= min_width and res_height >= min_height
    
    def is_resolution_meet_max(self, resolution: str, max_resolution: str) -> bool:
        """检查分辨率是否不超过最高限制
        
        Args:
            resolution: 实际分辨率
            max_resolution: 最高限制分辨率
            
        Returns:
            bool: 是否满足要求
        """
        if not resolution or not max_resolution:
            return True
        
        def parse_resolution(res):
            """解析分辨率字符串为(宽度, 高度)元组"""
            if 'x' in res:
                parts = res.split('x')
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except (ValueError, TypeError):
                        return 9999, 9999
            elif res.endswith('p'):
                try:
                    height = int(res[:-1])
                    width = int(height * 16 / 9)  # 假设宽高比为16:9
                    return width, height
                except (ValueError, TypeError):
                    return 9999, 9999
            return 9999, 9999
        
        res_width, res_height = parse_resolution(resolution)
        max_width, max_height = parse_resolution(max_resolution)
        
        return res_width <= max_width and res_height <= max_height

# 保持向后兼容
class M3UGenerator(EnhancedM3UGenerator):
    """向后兼容的M3U生成器"""
    
    def generate_m3u(self, sources: List[Dict]) -> str:
        """生成M3U文件内容（基础层级）"""
        return self.generate_enhanced_m3u(sources, "base")
    
    def generate_txt(self, sources: List[Dict]) -> str:
        """生成TXT文件内容（基础层级）"""
        return self.generate_enhanced_txt(sources, "base")
    
    def filter_sources(self, sources: List[Dict]) -> List[Dict]:
        """过滤源数据（基础层级）"""
        return self.enhanced_filter_sources(sources)
    
    def group_and_sort_sources(self, sources: List[Dict]) -> Dict[str, List[Dict]]:
        """分组和排序源数据（基础层级）"""
        return self.enhanced_group_and_sort_sources(sources, "base")
    
    def build_extinf(self, source: Dict) -> str:
        """构建EXTINF行（基础层级）"""
        return self.build_enhanced_extinf(source, "base")