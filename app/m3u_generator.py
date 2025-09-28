#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3U文件生成器模块
负责生成M3U和TXT格式的播放列表文件
"""

import os
import re
from typing import List, Dict
from config_manager import Config

class M3UGenerator:
    """M3U文件生成器 - 增强版"""
    
    def __init__(self, config: Config, logger):
        self.config = config
        self.logger = logger
        self.output_params = config.get_output_params()
        self.filter_params = config.get_filter_params()
        self.ua_position = config.get_ua_position()
        self.ua_enabled = config.is_ua_enabled()
    
    def generate_m3u(self, sources: List[Dict]) -> str:
        """生成M3U文件内容"""
        output_lines = ["#EXTM3U"]
        
        # 根据配置决定是否过滤源
        if self.output_params['enable_filter']:
            filtered_sources = self.filter_sources(sources)
            self.logger.info(f"过滤功能已启用，从 {len(sources)} 个源中筛选出 {len(filtered_sources)} 个合格源")
        else:
            filtered_sources = sources
            self.logger.info(f"过滤功能已禁用，使用所有 {len(filtered_sources)} 个源")
        
        # 按分组对源进行排序和分组
        grouped_sources = self.group_and_sort_sources(filtered_sources)
        
        # 生成M3U内容
        for group, group_sources in grouped_sources.items():
            # 添加分组注释
            output_lines.append(f"#EXTGRP:{group}")
            
            for source in group_sources:
                extinf = self.build_extinf(source)
                output_lines.append(extinf)
                
                # 构建URL，根据配置决定UA位置
                url = source['url']
                if self.ua_enabled and source.get('user_agent'):
                    if self.ua_position == 'url':
                        url = f"{url}|User-Agent={source['user_agent']}"
                
                output_lines.append(url)
        
        return "\n".join(output_lines)
    
    def generate_txt(self, sources: List[Dict]) -> str:
        """生成TXT文件内容"""
        output_lines = []
        
        # 根据配置决定是否过滤源
        if self.output_params['enable_filter']:
            filtered_sources = self.filter_sources(sources)
            self.logger.info(f"TXT过滤功能已启用，从 {len(sources)} 个源中筛选出 {len(filtered_sources)} 个合格源")
        else:
            filtered_sources = sources
            self.logger.info(f"TXT过滤功能已禁用，使用所有 {len(filtered_sources)} 个源")
        
        # 按分组对源进行排序和分组
        grouped_sources = self.group_and_sort_sources(filtered_sources)
        
        # 生成TXT内容
        for group, group_sources in grouped_sources.items():
            # 添加分组注释
            output_lines.append(f"# {group}")
            
            for source in group_sources:
                # 构建频道行
                channel_line = f"{source['name']},{source['url']}"
                
                # 添加UA信息（如果需要）
                if self.ua_enabled and source.get('user_agent'):
                    if self.ua_position == 'url':
                        channel_line = f"{source['name']},{source['url']}|User-Agent={source['user_agent']}"
                    else:
                        channel_line = f"{source['name']},{source['url']}#User-Agent={source['user_agent']}"
                
                output_lines.append(channel_line)
            
            # 添加空行分隔不同分组
            output_lines.append("")
        
        return "\n".join(output_lines)
    
    def filter_sources(self, sources: List[Dict]) -> List[Dict]:
        """根据条件过滤源"""
        filtered = []
        for source in sources:
            # 检查是否必须包含失败的源
            if not self.output_params['include_failed'] and source.get('status') != 'success':
                continue
            
            # 检查延迟
            response_time = source.get('response_time', 9999)
            if response_time > self.filter_params['max_latency']:
                continue
            
            # 检查分辨率（根据筛选模式）
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
            
            # 检查比特率
            bitrate = source.get('bitrate', 0)
            if bitrate > 0 and bitrate < self.filter_params['min_bitrate']:
                continue
            
            # 检查HD/4K要求
            if self.filter_params['must_hd'] and not source.get('is_hd', False):
                continue
                
            if self.filter_params['must_4k'] and not source.get('is_4k', False):
                continue
            
            # 检查速度要求
            speed = source.get('download_speed', 0)
            if speed > 0 and speed < self.filter_params['min_speed']:
                continue
            
            filtered.append(source)
        
        return filtered
    
    def is_resolution_meet_min(self, resolution: str, min_resolution: str) -> bool:
        """检查分辨率是否满足最低要求"""
        if not resolution or not min_resolution:
            return True
        
        def parse_resolution(res):
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
                    width = int(height * 16 / 9)
                    return width, height
                except (ValueError, TypeError):
                    return 0, 0
            return 0, 0
        
        res_width, res_height = parse_resolution(resolution)
        min_width, min_height = parse_resolution(min_resolution)
        
        return res_width >= min_width and res_height >= min_height
    
    def is_resolution_meet_max(self, resolution: str, max_resolution: str) -> bool:
        """检查分辨率是否不超过最高限制"""
        if not resolution or not max_resolution:
            return True
        
        def parse_resolution(res):
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
                    width = int(height * 16 / 9)
                    return width, height
                except (ValueError, TypeError):
                    return 9999, 9999
            return 9999, 9999
        
        res_width, res_height = parse_resolution(resolution)
        max_width, max_height = parse_resolution(max_resolution)
        
        return res_width <= max_width and res_height <= max_height
    
    def group_and_sort_sources(self, sources: List[Dict]) -> Dict[str, List[Dict]]:
        """对源进行分组和排序"""
        group_by = self.output_params['group_by']
        max_sources_per_channel = self.output_params['max_sources_per_channel']
        grouped = {}
        
        # 如果关闭筛选，则每个频道最大源数量增加到1000
        if not self.output_params['enable_filter']:
            max_sources_per_channel = 1000
        
        # 第一步：按频道名称分组
        channels = {}
        for source in sources:
            name = source['name']
            if name not in channels:
                channels[name] = []
            channels[name].append(source)
        
        # 第二步：对每个频道进行筛选和排序
        processed_sources = []
        for name, channel_sources in channels.items():
            # 如果启用了过滤，则过滤掉分辨率低于720p的源
            if self.output_params['enable_filter']:
                channel_sources = [s for s in channel_sources if self.is_resolution_meet_min(s.get('resolution', ''), '720p')]
            
            if not channel_sources:
                continue
                
            # 按照速度（降序）和延迟（升序）排序
            channel_sources.sort(key=lambda x: (
                -x.get('download_speed', 0),
                x.get('response_time', 9999)
            ))
            
            # 只保留前N个源
            processed_sources.extend(channel_sources[:max_sources_per_channel])
        
        # 第三步：按配置的group_by对处理后的源进行分组
        for source in processed_sources:
            if group_by == 'country':
                group_key = source.get('country', 'Unknown')
            elif group_by == 'region':
                group_key = source.get('region', 'Unknown')
            elif group_by == 'category':
                group_key = source.get('category', 'Unknown')
            elif group_by == 'source':
                group_key = source.get('source_type', 'Unknown')
            else:
                group_key = 'All Channels'
            
            if group_key not in grouped:
                grouped[group_key] = []
            
            grouped[group_key].append(source)
        
        # 第四步：对每个分组内的源进行排序
        for group_key, group_sources in grouped.items():
            group_sources.sort(key=lambda x: (
                x.get('continent', ''),
                x.get('country', ''),
                x.get('province', ''),
                x.get('city', ''),
                x.get('channel_type', ''),
                x.get('name', '')
            ))
        
        return grouped
    
    def build_extinf(self, source: Dict) -> str:
        """构建EXTINF行"""
        parts = [f"#EXTINF:-1"]
        
        # 添加tvg-id
        tvg_id = re.sub(r'[^a-zA-Z0-9]', '_', source['name']).lower()
        parts.append(f'tvg-id="{tvg_id}"')
        
        # 添加tvg-name
        parts.append(f'tvg-name="{source["name"]}"')
        
        # 添加tvg-logo（如果可用）
        if source.get('logo'):
            parts.append(f'tvg-logo="{source["logo"]}"')
        
        # 添加group-title
        group_title = source.get('group', 'Unknown')
        if source.get('category'):
            group_title = source.get('category', 'Unknown')
        parts.append(f'group-title="{group_title}"')
        
        # 添加user-agent（如果配置为放在extinf行且存在user_agent且UA功能启用）
        if self.ua_enabled and self.ua_position == 'extinf' and source.get('user_agent'):
            parts.append(f'user-agent="{source["user_agent"]}"')
        
        # 添加状态信息
        if source.get('status') != 'success':
            parts.append(f'status="{source.get("status")}"')
        
        # 添加延迟信息
        if source.get('response_time'):
            parts.append(f'response-time="{source.get("response_time")}ms"')
        
        # 添加分辨率信息
        if source.get('resolution'):
            parts.append(f'resolution="{source.get("resolution")}"')
        
        # 添加比特率信息
        if source.get('bitrate'):
            parts.append(f'bitrate="{source.get("bitrate")}kbps"')
        
        # 添加频道名称
        parts.append(f',{source["name"]}')
        
        return " ".join(parts)