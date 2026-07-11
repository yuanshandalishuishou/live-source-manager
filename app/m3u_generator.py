"""M3U 文件生成器模块 — 从 app/__init__.py 拆分而来

提供 M3UGenerator 类，负责生成增强版 M3U/TXT 播放列表文件，
支持分层筛选、智能分类和多维分组。
"""

import re

from app.config import Config
from app.rules import get_source_categories_for_app


class M3UGenerator:
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
        # P2-⑥：白名单强制保留（白名单源即使未过质量过滤也保留到输出）
        self.whitelist_force_keep = self.output_params.get('whitelist_force_keep', False)
        self._whitelist_entries = self._parse_list(
            config.get('Testing', 'global_whitelist', '') if hasattr(config, 'get') else ''
        )

    def _resolve_ua_position(self, source: dict) -> str:
        """校验并归一化 ua_position：非法值（非 extinf/url）回退默认并告警，避免 UA 静默丢失。

        Returns:
            str: 合法的 ua_position（'extinf' 或 'url'）
        """
        ua_pos = source.get('ua_position') or self.ua_position
        if ua_pos not in ('extinf', 'url'):
            self.logger.warning(
                f'源 "{source.get("name", "?")}" 的 ua_position="{ua_pos}" 非法，'
                f'已回退为 "{self.ua_position}"（合法值: extinf/url）'
            )
            ua_pos = self.ua_position
        return ua_pos

    def generate_m3u(self, sources: list[dict], level: str = 'base') -> str:
        """生成M3U文件内容（别名，保持对外接口一致）

        Args:
            sources: 源数据列表
            level: 层级标识 (base/qualified)

        Returns:
            str: M3U文件内容
        """
        return self.generate_enhanced_m3u(sources, level)

    def generate_enhanced_m3u(self, sources: list[dict], level: str = 'base') -> str:
        """生成增强版M3U文件内容

        Args:
            sources: 源数据列表
            level: 层级标识 (base/qualified)

        Returns:
            str: M3U文件内容
        """
        output_lines = ['#EXTM3U']

        # 根据层级决定筛选策略
        if level == 'base':
            # 基础层级：使用所有传入的源（已经过分辨率筛选）
            filtered_sources = sources
            self.logger.info(f'基础层级: 使用 {len(filtered_sources)} 个源')
        else:
            # 高级层级：根据条件筛选
            filtered_sources = self.enhanced_filter_sources(sources)
            self.logger.info(f'高级层级: 从 {len(sources)} 个源中筛选出 {len(filtered_sources)} 个合格源')

        # ── 多分类展开：content 含逗号时复制频道到多个分组 ──
        expanded_sources = []
        for s in filtered_sources:
            content_val = s.get('content', '') or ''
            if ',' in content_val:
                for single_content in [c.strip() for c in content_val.split(',') if c.strip()]:
                    s_copy = dict(s)
                    s_copy['content'] = single_content
                    s_copy['category'] = single_content
                    expanded_sources.append(s_copy)
            else:
                expanded_sources.append(s)

        if len(expanded_sources) != len(filtered_sources):
            self.logger.info(f'多分类展开: {len(filtered_sources)} → {len(expanded_sources)} 个源')

        # 按增强分组对源进行排序和分组
        grouped_sources = self.enhanced_group_and_sort_sources(expanded_sources, level)

        # 生成M3U内容
        for group, group_sources in grouped_sources.items():
            # 添加分组注释
            output_lines.append(f'#EXTGRP:{group}')

            for source in group_sources:
                # ── 优先从 stream_source_categories 表读取维度分类 ──
                source_id = source.get('id') if isinstance(source, dict) else None
                if source_id:
                    try:
                        cat_from_db = get_source_categories_for_app(source_id)
                    except Exception:
                        cat_from_db = {}
                else:
                    cat_from_db = {}

                # 如果 DB 中有手动修正的维度，覆盖自动匹配结果
                if cat_from_db:
                    for dim_key, dim_value in cat_from_db.items():
                        if dim_value and dim_value != '未知':
                            source[dim_key] = dim_value
                            if dim_key == 'content':
                                source['category'] = dim_value

                extinf = self.build_enhanced_extinf(source, level)
                output_lines.append(extinf)

                # 构建URL
                url = source['url']
                ua_pos = self._resolve_ua_position(source)
                if self.ua_enabled and source.get('user_agent') and ua_pos == 'url':
                    url = f'{url}|User-Agent={source["user_agent"]}'

                output_lines.append(url)

        return '\n'.join(output_lines)

    def generate_txt(self, sources: list[dict], level: str = 'base') -> str:
        """生成TXT文件内容（别名，保持对外接口一致）

        Args:
            sources: 源数据列表
            level: 层级标识 (base/qualified)

        Returns:
            str: TXT文件内容
        """
        return self.generate_enhanced_txt(sources, level)

    def generate_enhanced_txt(self, sources: list[dict], level: str = 'base') -> str:
        """生成增强版TXT文件内容

        Args:
            sources: 源数据列表
            level: 层级标识 (base/qualified)

        Returns:
            str: TXT文件内容
        """
        output_lines = []

        # 根据层级决定筛选策略
        if level == 'base':
            filtered_sources = sources
            self.logger.info(f'基础层级TXT: 使用 {len(filtered_sources)} 个源')
        else:
            filtered_sources = self.enhanced_filter_sources(sources)
            self.logger.info(f'高级层级TXT: 从 {len(sources)} 个源中筛选出 {len(filtered_sources)} 个合格源')

        # 按增强分组对源进行排序和分组
        grouped_sources = self.enhanced_group_and_sort_sources(filtered_sources, level)

        # 生成TXT内容
        for group, group_sources in grouped_sources.items():
            # 添加分组注释
            output_lines.append(f'# {group}')

            for source in group_sources:
                # 构建频道行
                channel_line = f'{source["name"]},{source["url"]}'

                # 添加UA信息
                ua_pos = self._resolve_ua_position(source)
                if self.ua_enabled and source.get('user_agent'):
                    if ua_pos == 'url':
                        channel_line = f'{source["name"]},{source["url"]}|User-Agent={source["user_agent"]}'
                    else:
                        channel_line = f'{source["name"]},{source["url"]}#User-Agent={source["user_agent"]}'

                output_lines.append(channel_line)

            # 添加空行分隔不同分组
            output_lines.append('')

        return '\n'.join(output_lines)

    def enhanced_filter_sources(self, sources: list[dict]) -> list[dict]:
        """增强版源过滤 - 用于高级层级筛选

        Args:
            sources: 源数据列表

        Returns:
            List[Dict]: 过滤后的源数据列表
        """
        filtered = []
        for source in sources:
            # P2-⑥：白名单强制保留 —— 命中白名单的源跳过全部质量过滤直接保留
            if self.whitelist_force_keep and self._matches_whitelist(source):
                filtered.append(source)
                continue

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

            # M1: 速度检查 - 修复当 speed==0（未测速时）所有未测速源被丢弃的问题
            speed = source.get('download_speed', 0)
            if speed > 0 and speed < self.filter_params['min_speed']:
                continue

            filtered.append(source)

        return filtered

    def enhanced_group_and_sort_sources(self, sources: list[dict], level: str) -> dict[str, list[dict]]:
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
                audio_group_key = '收音机' if media_type == 'radio' else '在线音频'
                if audio_group_key not in grouped:
                    grouped[audio_group_key] = []
                grouped[audio_group_key].extend(media_sources)

        # 第三步：对每个分组内的源进行排序 - 修复None值问题
        sort_by = (self.output_params.get('output_sort_by') or 'speed').lower()
        for group_key, group_sources in grouped.items():
            # 根据媒体类型使用不同的排序策略
            if '收音机' in group_key or '在线音频' in group_key:
                # 音频按名称排序 - 修复None值问题
                group_sources.sort(key=lambda x: x.get('name', '') or '')
            elif sort_by == 'name':
                group_sources.sort(key=lambda x: x.get('name', '') or '')
            elif sort_by == 'resolution':
                # 按分辨率高度降序（无分辨率沉底）
                group_sources.sort(
                    key=lambda x: (
                        -(self._parse_height(x.get('resolution', '') or '')),
                        x.get('name', '') or '',
                    )
                )
            else:
                # 默认 speed：按测速降序 + 响应时间升序（快源在前）
                group_sources.sort(
                    key=lambda x: (
                        x.get('continent', '') or '',
                        x.get('country', '') or '',
                        x.get('province', '') or '',
                        -(x.get('download_speed', 0) or 0),  # 修复None值
                        x.get('response_time', 9999) or 9999,  # 修复None值
                        x.get('name', '') or '',
                    )
                )

        return grouped

    def get_group_key(self, source: dict, group_by: str) -> str:
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

    def _build_group_title(self, source: dict) -> str:
        """根据配置的 group_title_format 构建分组标题

        支持的格式化字段：
        {content} - 主内容分类
        {region} - 地域
        {language} - 语言
        {quality} - 清晰度
        {media_type} - 媒体类型
        {genre} - 节目类型
        {category} - 同 {content}（向后兼容）

        Returns:
            str: 格式化后的分组标题
        """
        # 默认格式：仅主分类（向后兼容）
        group_title_format = self.config.get('m3u_group_title_format', '')

        if not group_title_format:
            # 默认使用 content 维度作为 group-title
            return source.get('content') or source.get('category') or source.get('group', 'Unknown')

        try:
            group_title = group_title_format.format(
                content=source.get('content', source.get('category', 'Unknown')),
                category=source.get('category', 'Unknown'),
                region=source.get('region', ''),
                language=source.get('language', 'zh'),
                quality=source.get('quality', ''),
                media_type=source.get('media_type', 'Other'),
                genre=source.get('genre', ''),
            )
            # 去除多余的分隔符（如 // 或 /- ）
            if not isinstance(group_title, str):
                group_title = ''
            import re as _re

            group_title = _re.sub(r'[/\\-]{2,}', '/', group_title)
            group_title = _re.sub(r'^[/\\-]+|[/\\-]+$', '', group_title)
            if not group_title:
                group_title = source.get('content') or source.get('category', 'Unknown')
            return group_title
        except KeyError:
            # 如果格式包含不支持的字段，回退到默认
            return source.get('content') or source.get('category') or source.get('group', 'Unknown')

    def build_enhanced_extinf(self, source: dict, level: str) -> str:
        """构建增强版EXTINF行

        Args:
            source: 源数据字典
            level: 层级标识

        Returns:
            str: EXTINF行内容
        """
        parts = ['#EXTINF:-1']

        # M2: 修复 source['name'] 可能 KeyError
        channel_name = source.get('name', 'Unknown')
        # 基本信息
        tvg_id = re.sub(r'[^a-zA-Z0-9]', '_', channel_name).lower()
        parts.append(f'tvg-id="{tvg_id}"')
        parts.append(f'tvg-name="{channel_name}"')

        # 图标
        if source.get('logo'):
            parts.append(f'tvg-logo="{source["logo"]}"')

        # 分组标题（支持多维格式化）
        group_title = self._build_group_title(source)
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
        ua_pos = self._resolve_ua_position(source)
        if self.ua_enabled and ua_pos == 'extinf' and source.get('user_agent'):
            parts.append(f'user-agent="{source["user_agent"]}"')

        # 质量信息（根据层级决定详细程度）
        if level == 'qualified':
            if source.get('response_time'):
                parts.append(f'response-time="{source.get("response_time")}ms"')
            speed = source.get('download_speed')
            if speed is not None and speed > 0:
                parts.append(f'download-speed="{speed:.1f}KB/s"')

        # 技术信息
        if source.get('resolution'):
            parts.append(f'resolution="{source.get("resolution")}"')
        if source.get('bitrate'):
            parts.append(f'bitrate="{source.get("bitrate")}kbps"')

        # 状态信息
        if source.get('status') != 'success':
            parts.append(f'status="{source.get("status")}"')

        # 频道名称
        # 纪码修复 P1-4: 使用 .get('name', 'Unknown') 避免 KeyError
        parts.append(f',{source.get("name", "Unknown")}')

        return ' '.join(parts)

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

    # ────────────────────────────────────────────────
    # P2-⑤/⑥ 辅助：排序与白名单匹配
    # ────────────────────────────────────────────────
    @staticmethod
    def _parse_list(raw: str) -> list[str]:
        """解析逗号/换行/分号分隔的名单（去空、去空白），保持原大小写。"""
        if not raw:
            return []
        return [p.strip() for p in re.split(r'[\n,;]', raw) if p.strip()]

    @staticmethod
    def _parse_height(resolution: str) -> int:
        """从分辨率字符串解析高度（如 1920x1080 -> 1080, 720p -> 720）。"""
        if not resolution:
            return 0
        if 'x' in resolution:
            try:
                return int(resolution.split('x')[1])
            except (ValueError, IndexError):
                return 0
        if resolution.endswith('p'):
            try:
                return int(resolution[:-1])
            except ValueError:
                return 0
        return 0

    def _matches_whitelist(self, source: dict) -> bool:
        """判断源是否命中全局白名单（URL 子串或 host 精确匹配，大小写不敏感）。"""
        if not self._whitelist_entries:
            return False
        url = (source.get('url') or '').lower()
        try:
            from urllib.parse import urlparse

            host = urlparse(url).netloc.lower()
        except Exception:
            host = ''
        for e in self._whitelist_entries:
            el = (e or '').lower()
            if not el:
                continue
            if el == host or el in url:
                return True
        return False
