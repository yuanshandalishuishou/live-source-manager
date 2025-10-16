#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播源管理工具 - 增强分层筛选修复版
主程序模块，协调各个模块的工作

修复内容：
1. 修复排序时的None值类型错误
2. 增强错误处理和日志记录
3. 改进分类优先级判断
4. 添加备份播放列表生成机制

主要功能增强：
1. 分层筛选机制：
   - 第一层：测试所有源的有效性
   - 第二层：按分辨率分组，每个分辨率保留质量最好的5个源
   - 第三层：根据条件筛选生成高级文件
2. 智能分类：
   - 音频/视频自动识别和分类
   - 地区电视台合理归集
   - 内容分类优化
"""

import os
import sys
import time
import asyncio
import traceback
import socket
from typing import List, Dict, Tuple

# 🔧 关键修复：确保容器内Python模块导入路径正确
sys.path.insert(0, '/app')

from config_manager import Config, Logger
from channel_rules import ChannelRules
from source_manager import SourceManager
from stream_tester import StreamTester
from m3u_generator import M3UGenerator

class EnhancedLiveSourceManager:
    """增强版直播源管理器 - 支持分层筛选和智能分类（修复版）
    
    核心功能：
    - 初始化所有组件（配置、日志、规则、源管理、测试器）
    - 分层筛选处理（有效性测试 → 分辨率筛选 → 条件筛选）
    - 智能频道分类（内容类型、地区、媒体类型）
    - 生成多级播放列表文件
    """
    
    def __init__(self):
        """初始化管理器实例"""
        self.config = None
        self.logger = None
        self.channel_rules = None
        self.source_manager = None
        self.stream_tester = None
        self.start_time = None
        
    def initialize(self) -> bool:
        """初始化所有组件 - 增强错误处理版
        
        初始化顺序：
        1. 配置管理器
        2. 日志系统
        3. 频道规则
        4. 源管理器
        5. 流测试器
        
        Returns:
            bool: 初始化是否成功
        """
        try:
            self.start_time = time.time()
            
            # 第一步：初始化配置管理器
            self.config = Config()
            self.logger_info("开始初始化增强版直播源管理工具...")
            
            # 第二步：初始化日志系统
            logger_config = self.config.get_logging_config()
            temp_logger = Logger(logger_config)
            self.logger = temp_logger.logger
            self.logger_info("配置管理器和日志系统初始化完成")
            
            # 第三步：验证Nginx输出目录权限
            if not self._verify_nginx_directory():
                self.logger_error("Nginx输出目录验证失败")
                return False
                
            # 第四步：初始化频道规则管理器
            self.logger_info("初始化频道规则管理器...")
            self.channel_rules = ChannelRules()
            
            # 第五步：测试频道分类规则
            if not self._test_channel_rules():
                self.logger_warning("频道规则测试失败，但继续运行")
            
            # 第六步：初始化其他组件
            self.logger_info("初始化源管理器...")
            self.source_manager = SourceManager(self.config, self.logger, self.channel_rules)
            
            self.logger_info("初始化流测试器...")
            self.stream_tester = StreamTester(self.config, self.logger)
            
            initialization_time = time.time() - self.start_time
            self.logger_info(f"✓ 所有组件初始化完成，耗时 {initialization_time:.2f} 秒")
            return True
            
        except Exception as e:
            error_msg = f"✗ 初始化失败: {e}"
            print(error_msg)
            if hasattr(self, 'logger') and self.logger:
                self.logger_error(error_msg)
                self.logger_error(traceback.format_exc())
            return False
    
    def _test_channel_rules(self) -> bool:
        """测试频道分类规则准确性
        
        Returns:
            bool: 测试是否通过
        """
        try:
            self.logger_info("🧪 开始频道分类规则测试...")
            test_results = self.channel_rules.test_classification()
            
            # 计算准确率
            total = len(test_results)
            correct = sum(1 for r in test_results if r[3] == "✓")
            accuracy = correct / total * 100
            
            if accuracy >= 80:  # 80%准确率认为通过
                self.logger_info(f"✓ 频道规则测试通过: {correct}/{total} 正确 ({accuracy:.1f}%)")
                return True
            else:
                self.logger_warning(f"⚠ 频道规则测试准确率较低: {correct}/{total} 正确 ({accuracy:.1f}%)")
                return False
                
        except Exception as e:
            self.logger_error(f"✗ 频道规则测试失败: {e}")
            return False
    
    def _verify_nginx_directory(self) -> bool:
        """验证Nginx输出目录权限
        
        Returns:
            bool: 目录权限是否正常
        """
        try:
            output_dir = self.config.get_output_params()['output_dir']
            self.logger_info(f"验证Nginx输出目录: {output_dir}")
            
            # 确保目录存在
            os.makedirs(output_dir, exist_ok=True)
            
            # 检查写权限
            if not os.access(output_dir, os.W_OK):
                self.logger_warning(f"输出目录不可写，尝试修复权限: {output_dir}")
                try:
                    os.chmod(output_dir, 0o755)
                    self.logger_info("✓ 目录权限修复成功")
                except Exception as e:
                    self.logger_error(f"✗ 目录权限修复失败: {e}")
                    return False
            
            # 验证Nginx用户访问权限（通过测试文件）
            test_file = os.path.join(output_dir, ".permission_test")
            try:
                with open(test_file, 'w') as f:
                    f.write("test")
                os.remove(test_file)
                self.logger_info("✓ Nginx目录权限验证通过")
                return True
            except Exception as e:
                self.logger_error(f"✗ Nginx目录权限验证失败: {e}")
                return False
                
        except Exception as e:
            self.logger_error(f"验证Nginx目录时发生错误: {e}")
            return False
    
    def classify_media_type(self, source: Dict) -> str:
        """智能分类媒体类型 - 增强版
        
        根据流媒体特征自动识别：
        - 视频内容 (video)
        - 收音机内容 (radio) 
        - 在线音频内容 (audio)
        
        Args:
            source: 源数据字典
            
        Returns:
            str: 媒体类型标识
        """
        # 检查是否有视频流
        has_video = source.get('has_video_stream', True)
        resolution = source.get('resolution', '')
        bitrate = source.get('bitrate', 0)
        
        # 如果没有视频流，肯定是音频
        if not has_video:
            return self._refine_audio_type(source)
        
        # 检查是否是极低分辨率的视频(可能是误判的音频)
        if resolution and 'x' in resolution:
            try:
                width, height = map(int, resolution.split('x'))
                if width < 100 or height < 100:  # 极低分辨率，可能是音频
                    return self._refine_audio_type(source)
            except (ValueError, TypeError):
                # 分辨率解析失败，按默认处理
                pass
        
        # 正常视频内容
        return 'video'
    
    def _refine_audio_type(self, source: Dict) -> str:
        """细化音频类型分类
        
        Args:
            source: 源数据字典
            
        Returns:
            str: 细化后的音频类型 (radio/audio)
        """
        channel_name = source['name'].lower()
        
        # 收音机关键词 - 传统广播电台
        radio_keywords = [
            'radio', '广播', '电台', 'fm', 'am', 
            '交通广播', '音乐广播', '新闻广播', '经济广播',
            '文艺广播', '都市广播', '农村广播'
        ]
        
        # 在线音频关键词 - 网络音频内容
        audio_keywords = [
            'music', '音乐', '歌曲', 'mtv', '演唱会', 
            '音乐会', '有声', '听书', '相声', '小品',
            '朗诵', '配音', '音效', 'asmr', '播客'
        ]
        
        # 优先匹配收音机关键词
        if any(keyword in channel_name for keyword in radio_keywords):
            return 'radio'
        # 其次匹配在线音频关键词
        elif any(keyword in channel_name for keyword in audio_keywords):
            return 'audio'
        else:
            # 默认归为在线音频
            return 'audio'
    
    def enhance_channel_classification(self, source: Dict) -> Dict:
        """增强频道分类 - 修复版
        
        修复逻辑：只有当规则分类比现有分类更具体时才覆盖
        避免正确的分类被低优先级规则覆盖
        
        Args:
            source: 原始源数据
            
        Returns:
            Dict: 增强分类后的源数据
        """
        channel_name = source['name']
        
        # 调用规则引擎进行分类
        enhanced_info = self.channel_rules.extract_channel_info(channel_name)
        rule_category = self.channel_rules.determine_category(channel_name)
        
        # 合并基础信息（国家、地区、语言等）
        source.update(enhanced_info)
        
        # 智能分类合并策略
        current_category = source.get('category', '其他频道')
        
        # 判断是否应该用规则分类覆盖现有分类
        should_override = self._should_override_category(
            rule_category, 
            current_category, 
            channel_name
        )
        
        if should_override:
            source['category'] = rule_category
            self.logger_debug(f"分类覆盖: '{channel_name}' [{current_category} → {rule_category}]")
        else:
            self.logger_debug(f"保留原分类: '{channel_name}' [{current_category}]")
        
        # 媒体类型分类
        source['media_type'] = self.classify_media_type(source)
        
        return source
    
    def _should_override_category(self, new_cat: str, old_cat: str, channel_name: str) -> bool:
        """判断是否应该用新分类覆盖旧分类
        
        判断逻辑：
        1. 如果原分类是"其他频道"，总是覆盖
        2. 如果新分类比原分类更具体，则覆盖
        3. 如果原分类明显错误，则覆盖
        
        Args:
            new_cat: 规则引擎产生的新分类
            old_cat: 现有的分类
            channel_name: 频道名称（用于特殊判断）
            
        Returns:
            bool: 是否应该覆盖
        """
        # 如果原分类是兜底分类，总是覆盖
        if old_cat == '其他频道':
            return True
        
        # 如果新分类是兜底分类，不覆盖
        if new_cat == '其他频道':
            return False
        
        # 分类优先级定义（数值越小优先级越高）
        category_priority = {
            "央视频道": 1,
            "收音机": 2,
            "在线音频": 3,
            "港澳台": 5,
            "卫视频道": 10,
            "影视频道": 15,
            "剧集频道": 15,
            "体育频道": 15,
            "少儿频道": 15,
            "新闻频道": 15,
            "纪实频道": 15,
            "音乐频道": 15,
            "综艺频道": 15,
            "教育频道": 15,
            "生活频道": 15,
            "财经频道": 15,
            "交通频道": 15,
            # 地区频道优先级较低
            "北京频道": 20,
            "上海频道": 20,
            "天津频道": 20,
            "重庆频道": 20,
            "河北频道": 20,
            # ... 其他地区频道
            "国际频道": 25,
            "其他频道": 100
        }
        
        # 获取优先级
        new_priority = category_priority.get(new_cat, 50)
        old_priority = category_priority.get(old_cat, 50)
        
        # 新分类优先级更高（数值更小）则覆盖
        if new_priority < old_priority:
            return True
        
        # 特殊规则：如果频道名称包含卫视但原分类不是卫视频道，则覆盖
        if '卫视' in channel_name and old_cat != '卫视频道' and new_cat == '卫视频道':
            return True
        
        # 特殊规则：如果频道名称包含CCTV但原分类不是央视频道，则覆盖
        if 'CCTV' in channel_name.upper() and old_cat != '央视频道' and new_cat == '央视频道':
            return True
        
        return False
    
    def hierarchical_filtering(self, sources: List[Dict]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """分层筛选机制 - 核心处理流程
        
        三层筛选：
        1. 有效性测试：过滤掉无法连接的源
        2. 分辨率筛选：每个频道每个分辨率保留质量最好的源
        3. 条件筛选：根据配置参数进行质量过滤
        
        Args:
            sources: 原始源数据列表
            
        Returns:
            Tuple[List[Dict], List[Dict], List[Dict]]: 
                (有效源, 基础筛选源, 高级筛选源)
        """
        self.logger_info("开始分层筛选处理...")
        
        # 第一层：测试有效性
        self.logger_info("=== 第一层: 有效性测试 ===")
        valid_sources = [s for s in sources if s.get('status') == 'success']
        failed_sources = len(sources) - len(valid_sources)
        self.logger_info(f"有效性测试完成: {len(valid_sources)} 个有效源, {failed_sources} 个失败源")
        
        if not valid_sources:
            self.logger_error("✗ 没有有效的源可供处理")
            return [], [], []
        
        # 增强分类处理
        self.logger_info("=== 智能分类处理 ===")
        classified_sources = []
        classification_stats = {}
        
        for source in valid_sources:
            try:
                enhanced_source = self.enhance_channel_classification(source)
                classified_sources.append(enhanced_source)
                
                # 统计分类结果
                category = enhanced_source.get('category', '未知')
                classification_stats[category] = classification_stats.get(category, 0) + 1
                
            except Exception as e:
                self.logger_warning(f"分类处理失败 {source['name']}: {e}")
                classified_sources.append(source)  # 保留原始源
        
        # 输出分类统计
        self.logger_info("分类统计:")
        for category, count in sorted(classification_stats.items(), key=lambda x: x[1], reverse=True):
            self.logger_info(f"  {category}: {count} 个")
        
        # 第二层：按分辨率分组筛选
        self.logger_info("=== 第二层: 分辨率分组筛选 ===")
        base_sources = self.resolution_based_filtering(classified_sources)
        self.logger_info(f"分辨率分组筛选完成: {len(base_sources)} 个基础源")
        
        # 第三层：条件筛选
        self.logger_info("=== 第三层: 条件筛选 ===")
        qualified_sources = self.condition_based_filtering(base_sources)
        self.logger_info(f"条件筛选完成: {len(qualified_sources)} 个合格源")
        
        return classified_sources, base_sources, qualified_sources
    
    def resolution_based_filtering(self, sources: List[Dict]) -> List[Dict]:
        """基于分辨率的筛选 - 每个频道保留最佳源
        
        筛选策略：
        - 音频内容：不按分辨率筛选，按名称分组
        - 视频内容：按分辨率分组，每个分组保留质量最好的5个源
        
        Args:
            sources: 分类后的源数据列表
            
        Returns:
            List[Dict]: 分辨率筛选后的源列表
        """
        # 按频道名称和分辨率分组
        channel_groups = {}
        
        for source in sources:
            media_type = source.get('media_type', 'video')
            
            if media_type in ['radio', 'audio']:
                # 音频内容：按频道名称分组（不区分分辨率）
                channel_key = f"audio_{source['name']}"
                if channel_key not in channel_groups:
                    channel_groups[channel_key] = []
                channel_groups[channel_key].append(source)
                continue
            
            # 视频内容：按频道名称和分辨率分组
            resolution = source.get('resolution', 'unknown')
            channel_key = f"{source['name']}_{resolution}"
            
            if channel_key not in channel_groups:
                channel_groups[channel_key] = []
            channel_groups[channel_key].append(source)
        
        # 对每个分组进行质量排序并保留前5个
        filtered_sources = []
        
        for channel_key, group_sources in channel_groups.items():
            # 按质量排序（响应时间 + 下载速度 + 比特率）- 修复None值问题
            sorted_sources = sorted(group_sources, 
                key=lambda x: (
                    -(x.get('download_speed', 0) or 0),  # 速度降序（越高越好）- 修复None值
                    x.get('response_time', 9999) or 9999,  # 延迟升序（越低越好）- 修复None值
                    -(x.get('bitrate', 0) or 0),  # 比特率降序（越高越好）- 修复None值
                    x.get('name', '') or ''  # 名称升序（稳定排序）- 修复None值
                ))
            
            # 保留前5个质量最好的源
            keep_count = min(5, len(sorted_sources))
            filtered_sources.extend(sorted_sources[:keep_count])
            
            if len(sorted_sources) > keep_count:
                self.logger_debug(f"分组 '{channel_key}': 保留 {keep_count}/{len(sorted_sources)} 个源")
        
        return filtered_sources
    
    def condition_based_filtering(self, sources: List[Dict]) -> List[Dict]:
        """基于条件的筛选 - 应用配置参数
        
        Args:
            sources: 分辨率筛选后的源列表
            
        Returns:
            List[Dict]: 条件筛选后的合格源列表
        """
        filter_params = self.config.get_filter_params()
        filtered_sources = []
        
        for source in sources:
            if self.is_source_qualified(source, filter_params):
                filtered_sources.append(source)
        
        return filtered_sources
    
    def is_source_qualified(self, source: Dict, filter_params: Dict) -> bool:
        """检查源是否满足筛选条件 - 增强版
        
        Args:
            source: 源数据字典
            filter_params: 过滤参数配置
            
        Returns:
            bool: 是否合格
        """
        # 基本状态检查
        if source.get('status') != 'success':
            return False
        
        # 延迟检查
        response_time = source.get('response_time', 9999)
        if response_time > filter_params['max_latency']:
            self.logger_debug(f"延迟不合格: {source['name']} ({response_time}ms)")
            return False
        
        # 音频内容简化检查
        media_type = source.get('media_type', 'video')
        if media_type in ['radio', 'audio']:
            # 音频只需要检查基本连通性和延迟
            return response_time <= filter_params['max_latency']
        
        # 视频内容详细检查
        
        # 分辨率检查
        resolution = source.get('resolution', '')
        min_res = filter_params['min_resolution']
        max_res = filter_params['max_resolution']
        resolution_mode = filter_params.get('resolution_filter_mode', 'range')
        
        if not self.check_resolution(resolution, min_res, max_res, resolution_mode):
            self.logger_debug(f"分辨率不合格: {source['name']} ({resolution})")
            return False
        
        # 比特率检查
        bitrate = source.get('bitrate', 0)
        if bitrate > 0 and bitrate < filter_params['min_bitrate']:
            self.logger_debug(f"比特率不合格: {source['name']} ({bitrate}kbps)")
            return False
        
        # 特殊要求检查
        if filter_params['must_hd'] and not source.get('is_hd', False):
            self.logger_debug(f"非高清源: {source['name']}")
            return False
            
        if filter_params['must_4k'] and not source.get('is_4k', False):
            self.logger_debug(f"非4K源: {source['name']}")
            return False
        
        # 速度检查
        speed = source.get('download_speed', 0)
        if speed > 0 and speed < filter_params['min_speed']:
            self.logger_debug(f"速度不合格: {source['name']} ({speed:.1f}KB/s)")
            return False
        
        return True
    
    def check_resolution(self, resolution: str, min_res: str, max_res: str, mode: str) -> bool:
        """检查分辨率是否符合要求
        
        Args:
            resolution: 实际分辨率 (如 "1920x1080" 或 "1080p")
            min_res: 要求的最低分辨率
            max_res: 要求的最高分辨率
            mode: 筛选模式 (range/min_only/max_only)
            
        Returns:
            bool: 是否满足要求
        """
        if not resolution or resolution == 'unknown':
            return True  # 未知分辨率默认通过
        
        def parse_resolution(res):
            """将分辨率字符串解析为(宽度, 高度)元组"""
            if not res:
                return 0, 0
                
            if 'x' in res:
                # 格式: "1920x1080"
                parts = res.split('x')
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except (ValueError, TypeError):
                        return 0, 0
            elif res.endswith('p'):
                # 格式: "1080p"
                try:
                    height = int(res[:-1])
                    # 假设宽高比为16:9计算宽度
                    width = int(height * 16 / 9)
                    return width, height
                except (ValueError, TypeError):
                    return 0, 0
            return 0, 0
        
        res_w, res_h = parse_resolution(resolution)
        min_w, min_h = parse_resolution(min_res)
        max_w, max_h = parse_resolution(max_res)
        
        if mode == 'range':
            # 必须同时满足最小和最大分辨率
            min_ok = (min_w == 0 and min_h == 0) or (res_w >= min_w and res_h >= min_h)
            max_ok = (max_w == 0 and max_h == 0) or (res_w <= max_w and res_h <= max_h)
            return min_ok and max_ok
        elif mode == 'min_only':
            # 只检查最低分辨率
            return (min_w == 0 and min_h == 0) or (res_w >= min_w and res_h >= min_h)
        elif mode == 'max_only':
            # 只检查最高分辨率
            return (max_w == 0 and max_h == 0) or (res_w <= max_w and res_h <= max_h)
        
        return True
    
    async def enhanced_process_sources(self) -> bool:
        """增强的处理流程 - 支持分层筛选
        
        Returns:
            bool: 处理是否成功
        """
        if not all([self.source_manager, self.stream_tester]):
            self.logger_error("必要的组件未正确初始化")
            return False
        
        try:
            self.logger_info("开始增强版直播源处理流程...")
            process_start_time = time.time()
            
            # 步骤1: 下载所有源文件
            self.logger_info("=== 步骤1: 下载源文件 ===")
            downloaded_files = await self.source_manager.download_all_sources()
            
            if not downloaded_files:
                self.logger_warning("没有成功下载任何源文件，尝试使用缓存文件继续处理")
            
            # 步骤2: 解析所有源文件
            self.logger_info("=== 步骤2: 解析源文件 ===")
            sources = self.source_manager.parse_all_files()
            
            if not sources:
                self.logger_error("没有解析到任何有效的直播源")
                return False
            
            self.logger_info(f"成功解析 {len(sources)} 个直播源")
            
            # 步骤3: 测试所有流媒体源
            self.logger_info("=== 步骤3: 测试流媒体源 ===")
            test_results = self.stream_tester.test_all_sources(sources)
            
            # 步骤4: 分层筛选
            self.logger_info("=== 步骤4: 分层筛选 ===")
            valid_sources, base_sources, qualified_sources = self.hierarchical_filtering(test_results)
            
            # 步骤5: 生成不同层次的播放列表
            self.logger_info("=== 步骤5: 生成播放列表文件 ===")
            generator = M3UGenerator(self.config, self.logger)
            
            # 生成基础播放列表（第二层筛选结果）
            if base_sources:
                success = self._generate_enhanced_playlist(generator, base_sources, "", "基础")
                if not success:
                    self.logger_error("生成基础播放列表文件失败")
            else:
                self.logger_warning("没有基础源，跳过基础播放列表文件生成")
            
            # 生成高级播放列表（第三层筛选结果）
            if qualified_sources:
                success = self._generate_enhanced_playlist(generator, qualified_sources, "qualified_", "高级")
                if not success:
                    self.logger_error("生成高级播放列表文件失败")
            else:
                self.logger_warning("没有合格源，跳过高级播放列表文件生成")
            
            # 步骤6: 输出统计信息
            self.logger_info("=== 步骤6: 生成统计信息 ===")
            self.enhanced_output_statistics(valid_sources, base_sources, qualified_sources)
            
            process_time = time.time() - process_start_time
            self.logger_info(f"✓ 增强版处理流程完成，总耗时 {process_time:.2f} 秒")
            
            return True
            
        except Exception as e:
            self.logger_error(f"处理直播源过程中发生错误: {e}")
            self.logger_error(traceback.format_exc())
            return False
    
    def _generate_enhanced_playlist(self, generator: M3UGenerator, sources: List[Dict], prefix: str, level: str) -> bool:
        """生成增强版播放列表文件 - 增强错误处理版
        
        Args:
            generator: M3U生成器实例
            sources: 源数据列表
            prefix: 文件名前缀
            level: 层级描述
            
        Returns:
            bool: 生成是否成功
        """
        try:
            # 生成M3U文件内容 - 添加异常捕获
            try:
                m3u_content = generator.generate_m3u(sources)
            except Exception as e:
                self.logger_error(f"生成M3U内容失败: {e}")
                # 生成一个简单的备份M3U文件
                m3u_content = self._create_backup_m3u_content(sources, level)
            
            # 生成TXT文件内容 - 添加异常捕获
            try:
                txt_content = generator.generate_txt(sources)
            except Exception as e:
                self.logger_error(f"生成TXT内容失败: {e}")
                # 生成一个简单的备份TXT文件
                txt_content = self._create_backup_txt_content(sources, level)
            
            # 获取基础文件名
            base_filename = self.config.get_output_params()['filename'].replace('.m3u', '')
            
            # 直接写入到输出目录
            output_dir = self.config.get_output_params()['output_dir']
            os.makedirs(output_dir, exist_ok=True)
            
            # 原子写入M3U文件（避免写入过程中文件不完整）
            m3u_filename = f"{prefix}{base_filename}.m3u"
            m3u_final_path = os.path.join(output_dir, m3u_filename)
            m3u_temp_path = f"{m3u_final_path}.tmp"
            
            with open(m3u_temp_path, 'w', encoding='utf-8') as f:
                f.write(m3u_content)
            os.replace(m3u_temp_path, m3u_final_path)
            
            # 原子写入TXT文件
            txt_filename = f"{prefix}{base_filename}.txt"
            txt_final_path = os.path.join(output_dir, txt_filename)
            txt_temp_path = f"{txt_final_path}.tmp"
            
            with open(txt_temp_path, 'w', encoding='utf-8') as f:
                f.write(txt_content)
            os.replace(txt_temp_path, txt_final_path)
            
            # 记录文件信息
            m3u_size = os.path.getsize(m3u_final_path)
            txt_size = os.path.getsize(txt_final_path)
            
            self.logger_info(f"✓ 成功生成 {level} 播放列表文件:")
            self.logger_info(f"  {m3u_filename} ({m3u_size} 字节, {len(sources)} 个频道)")
            self.logger_info(f"  {txt_filename} ({txt_size} 字节)")
            
            # 设置文件权限（确保Nginx可读）
            os.chmod(m3u_final_path, 0o644)
            os.chmod(txt_final_path, 0o644)
            
            return True
                
        except Exception as e:
            self.logger_error(f"生成{level}播放列表文件时发生错误: {e}")
            return False
    
    def _create_backup_m3u_content(self, sources: List[Dict], level: str) -> str:
        """创建备份M3U文件内容
        
        Args:
            sources: 源数据列表
            level: 层级描述
            
        Returns:
            str: 备份M3U内容
        """
        lines = ["#EXTM3U"]
        for source in sources:
            lines.append(f"#EXTINF:-1,{source.get('name', 'Unknown')}")
            lines.append(source['url'])
        return "\n".join(lines)
    
    def _create_backup_txt_content(self, sources: List[Dict], level: str) -> str:
        """创建备份TXT文件内容
        
        Args:
            sources: 源数据列表
            level: 层级描述
            
        Returns:
            str: 备份TXT内容
        """
        lines = [f"# {level}播放列表 - 备份版本"]
        for source in sources:
            lines.append(f"{source.get('name', 'Unknown')},{source['url']}")
        return "\n".join(lines)
    
    def enhanced_output_statistics(self, valid_sources: List[Dict], base_sources: List[Dict], qualified_sources: List[Dict]):
        """增强版统计信息输出
        
        Args:
            valid_sources: 有效源列表
            base_sources: 基础筛选源列表  
            qualified_sources: 高级筛选源列表
        """
        self.logger_info("=" * 60)
        self.logger_info("增强版直播源处理统计报告")
        self.logger_info("=" * 60)
        
        # 基本统计
        total_sources = len(valid_sources) + (len(valid_sources) - len(base_sources))  # 估算总数
        self.logger_info(f"有效源总数: {len(valid_sources)}")
        self.logger_info(f"基础筛选源: {len(base_sources)} ({len(base_sources)/len(valid_sources)*100:.1f}%)")
        self.logger_info(f"高级筛选源: {len(qualified_sources)} ({len(qualified_sources)/len(valid_sources)*100:.1f}%)")
        
        # 媒体类型统计
        self.logger_info("-" * 40)
        self.logger_info("媒体类型统计:")
        media_types = {}
        for source in valid_sources:
            media_type = source.get('media_type', 'unknown')
            media_types[media_type] = media_types.get(media_type, 0) + 1
        
        for media_type, count in sorted(media_types.items(), key=lambda x: x[1], reverse=True):
            percentage = count / len(valid_sources) * 100
            self.logger_info(f"  {media_type}: {count} 个 ({percentage:.1f}%)")
        
        # 分辨率统计（仅视频）
        self.logger_info("-" * 40)
        self.logger_info("视频分辨率统计:")
        resolutions = {}
        video_sources = [s for s in valid_sources if s.get('media_type') == 'video']
        
        for source in video_sources:
            res = source.get('resolution', 'unknown')
            resolutions[res] = resolutions.get(res, 0) + 1
        
        # 按数量排序，显示前10个
        sorted_resolutions = sorted(resolutions.items(), key=lambda x: x[1], reverse=True)
        for res, count in sorted_resolutions[:10]:
            if video_sources:
                percentage = count / len(video_sources) * 100
                self.logger_info(f"  {res}: {count} 个 ({percentage:.1f}%)")
            else:
                self.logger_info(f"  {res}: {count} 个")
        
        # 分类统计
        self.logger_info("-" * 40)
        self.logger_info("频道分类统计:")
        categories = {}
        for source in valid_sources:
            category = source.get('category', 'unknown')
            categories[category] = categories.get(category, 0) + 1
        
        # 按数量排序
        sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
        for category, count in sorted_categories:
            percentage = count / len(valid_sources) * 100
            self.logger_info(f"  {category}: {count} 个 ({percentage:.1f}%)")
        
        self.logger_info("=" * 60)

    def run_enhanced(self) -> bool:
        """运行增强版主程序
        
        Returns:
            bool: 程序运行是否成功
        """
        # 准备输出目录
        self.logger_info("第一步：准备输出目录...")
        if not self.ensure_output_directory():
            self.logger_error("输出目录准备失败")
            return False
        
        # 运行增强处理流程
        self.logger_info("第二步：开始增强版处理流程...")
        try:
            # 创建新的事件循环（确保在容器环境中正常工作）
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            process_success = loop.run_until_complete(self.enhanced_process_sources())
            
            if process_success:
                total_time = time.time() - self.start_time
                self.logger_info(f"✓ 增强版处理完成，总耗时 {total_time:.2f} 秒")
                return True
            else:
                self.logger_error("✗ 增强版处理失败")
                return False
                
        except Exception as e:
            self.logger_error(f"✗ 增强版主程序运行失败: {e}")
            self.logger_error(traceback.format_exc())
            return False
        finally:
            # 清理事件循环
            if 'loop' in locals():
                loop.close()

    def ensure_output_directory(self) -> bool:
        """确保输出目录存在
        
        Returns:
            bool: 目录准备是否成功
        """
        try:
            output_dir = self.config.get_output_params()['output_dir']
            self.logger_info(f"检查输出目录: {output_dir}")
            
            os.makedirs(output_dir, exist_ok=True)
            
            if not os.access(output_dir, os.W_OK):
                self.logger_error(f"输出目录不可写: {output_dir}")
                return False
            
            self._create_default_files(output_dir)
            self.logger_info(f"✓ 输出目录准备完成: {output_dir}")
            return True
            
        except Exception as e:
            self.logger_error(f"准备输出目录失败: {e}")
            return False

    def _create_default_files(self, output_dir: str):
        """创建默认文件（防止空目录）
        
        Args:
            output_dir: 输出目录路径
        """
        try:
            base_filename = self.config.get_output_params()['filename'].replace('.m3u', '')
            default_m3u_path = os.path.join(output_dir, f"{base_filename}.m3u")
            
            if not os.path.exists(default_m3u_path):
                default_content = """#EXTM3U
#EXTINF:-1 tvg-id="default" tvg-name="默认频道" group-title="系统消息",默认频道
# 直播源管理工具正在处理中，请稍后刷新...
https://example.com/default"""
                
                with open(default_m3u_path, 'w', encoding='utf-8') as f:
                    f.write(default_content)
                self.logger_info(f"创建默认M3U文件: {default_m3u_path}")
            
            default_txt_path = os.path.join(output_dir, f"{base_filename}.txt")
            if not os.path.exists(default_txt_path):
                default_txt_content = """# 直播源管理工具
# 正在处理直播源，请稍后刷新...
默认频道,https://example.com/default"""
                
                with open(default_txt_path, 'w', encoding='utf-8') as f:
                    f.write(default_txt_content)
                self.logger_info(f"创建默认TXT文件: {default_txt_path}")
                
            # 设置文件权限
            os.chmod(default_m3u_path, 0o644)
            os.chmod(default_txt_path, 0o644)
                
        except Exception as e:
            self.logger_warning(f"创建默认文件失败: {e}")

    # 日志辅助方法
    def logger_info(self, message: str):
        """信息级别日志"""
        if self.logger:
            self.logger.info(message)
        else:
            print(f"INFO: {message}")

    def logger_error(self, message: str):
        """错误级别日志"""
        if self.logger:
            self.logger.error(message)
        else:
            print(f"ERROR: {message}")

    def logger_warning(self, message: str):
        """警告级别日志"""
        if self.logger:
            self.logger.warning(message)
        else:
            print(f"WARNING: {message}")

    def logger_debug(self, message: str):
        """调试级别日志"""
        if self.logger:
            self.logger.debug(message)
        # 调试信息不输出到控制台

def main():
    """主函数入口点 - 使用增强版管理器"""
    print("直播源管理工具（增强分层筛选修复版）启动中...")
    
    # 创建增强版管理器实例
    manager = EnhancedLiveSourceManager()
    
    # 初始化所有组件
    if not manager.initialize():
        print("初始化失败，程序退出")
        return 1
    
    # 运行增强版主程序
    success = manager.run_enhanced()
    
    if success:
        print("增强版程序执行成功")
        return 0
    else:
        print("增强版程序执行失败")
        return 1

if __name__ == "__main__":
    # 设置默认编码（确保在容器环境中正确处理中文）
    import locale
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except:
        try:
            locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
        except:
            pass  # 使用系统默认编码
    
    # 运行主程序
    sys.exit(main())