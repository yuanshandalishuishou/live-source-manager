#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
流媒体测试模块 - 增强版 v2.1

主要功能增强:
1. 音视频流智能检测: 自动识别纯音频、纯视频、混合流
2. 分层质量评估: 支持基础测试和详细质量分析
3. 增强元数据提取: 分辨率、编码、比特率、帧率等
4. 智能缓存机制: 避免重复测试相同资源
5. 网络适应性: IPv6支持、代理兼容、超时控制

技术特点:
- 使用ffprobe进行专业的流媒体分析
- 多线程并发测试，提升效率
- 详细的错误分类和日志记录
- 自适应网络环境检测
"""

import time
import json
import socket
import subprocess
import multiprocessing
import concurrent.futures
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from config_manager import Config

# 全局缓存配置
_url_cache = {}  # URL测试结果缓存
_last_cache_cleanup = datetime.now()  # 上次缓存清理时间
_CACHE_CLEANUP_INTERVAL = 300  # 缓存清理间隔(秒)

class StreamTester:
    """增强版流媒体测试类
    
    核心功能:
    - test_all_sources: 批量测试所有源
    - test_single_stream: 单个流媒体测试
    - test_stream_url: 使用ffprobe进行专业分析
    - extract_metadata: 提取详细的流媒体元数据
    - check_if_qualified: 质量合格性检查
    
    性能特性:
    - 智能缓存避免重复测试
    - 并发控制防止资源耗尽
    - 超时机制保证测试稳定性
    """
    
    def __init__(self, config: Config, logger):
        """初始化测试器
        
        Args:
            config: 配置管理器实例
            logger: 日志记录器实例
        """
        self.config = config
        self.logger = logger
        self.testing_params = config.get_testing_params()
        self.filter_params = config.get_filter_params()
        
        # 验证ffprobe可用性
        self._verify_ffprobe()
    
    def _verify_ffprobe(self):
        """验证ffprobe工具是否可用
        
        Raises:
            RuntimeError: 当ffprobe不可用时抛出
        """
        try:
            result = subprocess.run(
                ['ffprobe', '-version'], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            if result.returncode == 0:
                self.logger.info("✓ FFprobe工具验证成功")
            else:
                raise RuntimeError("FFprobe执行失败")
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            self.logger.error(f"✗ FFprobe工具验证失败: {e}")
            raise RuntimeError("FFprobe不可用，请确保已安装FFmpeg") from e
    
    def test_all_sources(self, sources: List[Dict]) -> List[Dict]:
        """批量测试所有流媒体源
        
        实现分层测试策略:
        1. 基础连通性测试
        2. 详细质量分析(如果基础测试通过)
        3. 质量合格性评估
        
        Args:
            sources: 源数据列表，每个源包含name、url等基本信息
            
        Returns:
            List[Dict]: 包含测试结果的源数据列表
        """
        # 清理过期缓存
        self.cleanup_cache()
        
        total_sources = len(sources)
        self.logger.info(f"开始测试 {total_sources} 个流媒体源")
        self.logger.info(f"并发线程数: {self.testing_params['concurrent_threads']}")
        self.logger.info(f"测试超时: {self.testing_params['timeout']}秒")
        
        # 动态调整并发线程数，避免资源耗尽
        max_workers = self._calculate_optimal_workers()
        
        # 创建进度显示
        try:
            from tqdm import tqdm
            pbar = tqdm(total=total_sources, desc="测试流媒体源", unit="源")
            use_tqdm = True
        except ImportError:
            self.logger.warning("tqdm模块未安装，使用简单进度显示")
            pbar = None
            use_tqdm = False
        
        test_results = []
        successful_count = 0
        failed_count = 0
        
        # 使用线程池执行并发测试
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有测试任务
            future_to_source = {
                executor.submit(self.test_single_stream, source): source 
                for source in sources
            }
            
            # 处理完成的任务
            for future in concurrent.futures.as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    # 获取测试结果，设置超时防止线程挂起
                    result = future.result(timeout=self.testing_params['timeout'] + 15)
                    test_results.append(result)
                    
                    # 质量合格性检查
                    is_qualified = self.check_if_qualified(result)
                    result['is_qualified'] = is_qualified
                    
                    # 统计和日志记录
                    if result.get('status') == 'success':
                        successful_count += 1
                        log_level = 'info' if is_qualified else 'warning'
                    else:
                        failed_count += 1
                        log_level = 'error'
                    
                    # 记录详细测试结果
                    self.log_test_result(source, result, log_level)
                    
                    # 更新进度显示
                    if use_tqdm:
                        pbar.update(1)
                        status_info = f"有效:{successful_count} 失败:{failed_count}"
                        pbar.set_postfix_str(status_info)
                    else:
                        if len(test_results) % 10 == 0:  # 每10个源输出一次进度
                            self.logger.info(f"测试进度: {len(test_results)}/{total_sources}")
                            
                except concurrent.futures.TimeoutError:
                    # 处理测试超时
                    self.logger.error(f"测试超时: {source['name']} - {source['url']}")
                    timeout_result = {
                        **source, 
                        'status': 'timeout', 
                        'response_time': None, 
                        'is_qualified': False
                    }
                    test_results.append(timeout_result)
                    failed_count += 1
                    
                    if use_tqdm:
                        pbar.update(1)
                except Exception as e:
                    # 处理其他异常
                    self.logger.error(f"测试异常 {source['name']}: {e}")
                    error_result = {
                        **source, 
                        'status': 'error', 
                        'response_time': None, 
                        'is_qualified': False
                    }
                    test_results.append(error_result)
                    failed_count += 1
                    
                    if use_tqdm:
                        pbar.update(1)
        
        # 关闭进度条
        if use_tqdm:
            pbar.close()
        
        # 输出测试统计
        qualified_count = sum(1 for r in test_results if r.get('is_qualified'))
        self.logger.info(f"测试完成统计:")
        self.logger.info(f"  - 总测试数: {total_sources}")
        self.logger.info(f"  - 成功数: {successful_count} ({successful_count/total_sources*100:.1f}%)")
        self.logger.info(f"  - 合格数: {qualified_count} ({qualified_count/total_sources*100:.1f}%)")
        self.logger.info(f"  - 失败数: {failed_count} ({failed_count/total_sources*100:.1f}%)")
        
        return test_results
    
    def _calculate_optimal_workers(self) -> int:
        """计算最优并发工作线程数
        
        基于系统资源和配置参数动态计算
        
        Returns:
            int: 推荐的并发线程数
        """
        # 获取配置的并发数
        config_workers = self.testing_params['concurrent_threads']
        
        # 获取系统CPU核心数
        cpu_cores = multiprocessing.cpu_count()
        
        # 计算基于系统资源的最大建议数
        system_max_workers = min(cpu_cores * 4, 50)  # 限制最大50线程
        
        # 取配置值和系统建议值的最小值
        optimal_workers = min(config_workers, system_max_workers)
        
        self.logger.debug(f"并发优化: 配置={config_workers}, CPU核心={cpu_cores}, 最终={optimal_workers}")
        return optimal_workers
    
    def test_single_stream(self, source: Dict) -> Dict:
        """测试单个流媒体源
        
        实现智能测试流程:
        1. 缓存检查避免重复测试
        2. 基础连通性测试
        3. 详细流媒体分析(如果连通)
        4. 速度测试(如果配置启用)
        
        Args:
            source: 源数据字典，包含url、name等信息
            
        Returns:
            Dict: 包含测试结果的源数据
        """
        url = source['url']
        user_agent = source.get('user_agent')
        
        # 生成缓存键(规范化URL)
        cache_key = self.normalize_url(url)
        
        # 检查缓存命中
        cache_result = self._get_cached_result(cache_key)
        if cache_result:
            self.logger.debug(f"缓存命中: {url}")
            return {**source, **cache_result}
        
        # 网络环境检查
        if not self._check_network_compatibility(url):
            return {
                **source, 
                'status': 'failed', 
                'response_time': None, 
                'is_qualified': False,
                'error_reason': 'network_incompatible'
            }
        
        # 执行流媒体测试
        start_time = time.time()
        test_status, metadata = self.test_stream_url(url, user_agent)
        response_time = round((time.time() - start_time) * 1000)  # 转换为毫秒
        
        # 如果测试成功，执行附加测试
        if test_status == 'success':
            # 速度测试(如果启用)
            if self.testing_params['enable_speed_test']:
                download_speed = self.test_download_speed(url, user_agent)
                metadata['download_speed'] = download_speed
            
            # 补充媒体类型信息
            metadata['media_type'] = self._determine_media_type(metadata)
        
        # 构建完整结果
        test_result = {
            'status': test_status,
            'response_time': response_time,
            **metadata
        }
        
        # 缓存测试结果
        self._cache_result(cache_key, test_result)
        
        return {**source, **test_result}
    
    def test_stream_url(self, url: str, user_agent: Optional[str] = None) -> Tuple[str, Dict]:
        """使用ffprobe测试流媒体URL
        
        专业级流媒体分析:
        - 格式探测
        - 流信息提取
        - 编码分析
        - 质量评估
        
        Args:
            url: 流媒体URL
            user_agent: 可选的User-Agent头
            
        Returns:
            Tuple[str, Dict]: (测试状态, 元数据字典)
        """
        try:
            # 配置ffprobe参数
            timeout_ms = self.testing_params['timeout'] * 1000000  # 转换为微秒
            
            # 基础命令参数
            cmd = [
                'ffprobe', 
                '-v', 'quiet',           # 安静模式，减少输出
                '-print_format', 'json', # JSON输出格式
                '-show_streams',         # 显示流信息
                '-show_format',          # 显示格式信息
                '-timeout', str(timeout_ms),  # 超时设置
                url
            ]
            
            # 添加User-Agent头(如果提供)
            if user_agent:
                cmd.extend(['-headers', f'User-Agent: {user_agent}'])
            
            # 执行ffprobe命令
            self.logger.debug(f"执行ffprobe命令: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.testing_params['timeout'] + 2  # 额外2秒缓冲
            )
            
            # 分析命令执行结果
            if result.returncode == 0:
                # 成功获取流信息
                data = json.loads(result.stdout)
                
                if data.get('streams') and len(data['streams']) > 0:
                    # 提取详细的元数据
                    metadata = self.extract_metadata(data)
                    return 'success', metadata
                else:
                    # 没有有效的流
                    return 'failed', {'error_reason': 'no_valid_streams'}
            else:
                # ffprobe执行失败
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                self.logger.debug(f"FFprobe执行失败: {error_msg}")
                return 'failed', {'error_reason': f'ffprobe_error: {error_msg}'}
                
        except subprocess.TimeoutExpired:
            # 处理超时
            self.logger.debug(f"FFprobe测试超时: {url}")
            return 'timeout', {'error_reason': 'timeout'}
        except json.JSONDecodeError as e:
            # JSON解析错误
            self.logger.debug(f"FFprobe输出JSON解析失败: {e}")
            return 'failed', {'error_reason': 'json_parse_error'}
        except Exception as e:
            # 其他异常
            self.logger.debug(f"FFprobe测试异常 {url}: {e}")
            return 'failed', {'error_reason': f'exception: {str(e)}'}
    
    def extract_metadata(self, data: Dict) -> Dict:
        """从ffprobe输出中提取详细的流媒体元数据
        
        提取的信息包括:
        - 基础信息: 比特率、时长、格式
        - 视频流: 分辨率、编码、帧率、宽高比
        - 音频流: 编码、采样率、声道数
        - 质量标识: HD/4K标志、流类型
        
        Args:
            data: ffprobe的JSON输出数据
            
        Returns:
            Dict: 包含所有提取的元数据
        """
        metadata = {
            # 基础信息
            'bitrate': 0,
            'duration': 0,
            'format_name': '',
            
            # 视频流信息
            'resolution': '',
            'is_hd': False,
            'is_4k': False,
            'video_codec': '',
            'video_profile': '',
            'video_level': 0,
            'frame_rate': 0,
            'pixel_format': '',
            'has_video_stream': False,
            
            # 音频流信息
            'audio_codec': '',
            'audio_sample_rate': 0,
            'audio_channels': 0,
            'audio_bitrate': 0,
            'has_audio_stream': False,
            
            # 流统计
            'stream_count': 0,
            'video_stream_count': 0,
            'audio_stream_count': 0,
            
            # 媒体类型(后续计算)
            'media_type': 'unknown'
        }
        
        # 提取格式信息
        if 'format' in data:
            format_info = data['format']
            
            # 比特率(转换为kbps)
            if 'bit_rate' in format_info:
                try:
                    metadata['bitrate'] = int(format_info['bit_rate']) // 1000
                except (ValueError, TypeError):
                    pass
            
            # 时长(秒)
            if 'duration' in format_info:
                try:
                    metadata['duration'] = float(format_info['duration'])
                except (ValueError, TypeError):
                    pass
            
            # 格式名称
            if 'format_name' in format_info:
                metadata['format_name'] = format_info['format_name']
        
        # 分析所有流
        video_streams = []
        audio_streams = []
        other_streams = []
        
        for stream in data.get('streams', []):
            metadata['stream_count'] += 1
            codec_type = stream.get('codec_type', 'unknown')
            
            if codec_type == 'video':
                metadata['video_stream_count'] += 1
                metadata['has_video_stream'] = True
                video_streams.append(stream)
                
                # 提取视频流详细信息
                video_info = self._extract_video_stream_info(stream)
                metadata.update(video_info)
                
            elif codec_type == 'audio':
                metadata['audio_stream_count'] += 1
                metadata['has_audio_stream'] = True
                audio_streams.append(stream)
                
                # 提取音频流详细信息
                audio_info = self._extract_audio_stream_info(stream)
                metadata.update(audio_info)
            else:
                other_streams.append(stream)
        
        # 确定主要视频流(如果有多个)
        if video_streams:
            # 选择第一个视频流作为主要流
            main_video = video_streams[0]
            # 如果之前没有提取分辨率，现在提取
            if not metadata['resolution']:
                width = main_video.get('width', 0)
                height = main_video.get('height', 0)
                if width and height:
                    metadata['resolution'] = f"{width}x{height}"
                    metadata['is_hd'] = height >= 720
                    metadata['is_4k'] = height >= 2160
        
        # 确定主要音频流(如果有多个)
        if audio_streams and not metadata['audio_codec']:
            main_audio = audio_streams[0]
            if 'codec_name' in main_audio:
                metadata['audio_codec'] = main_audio['codec_name']
        
        return metadata
    
    def _extract_video_stream_info(self, stream: Dict) -> Dict:
        """提取视频流详细信息
        
        Args:
            stream: 视频流数据
            
        Returns:
            Dict: 视频流信息
        """
        info = {}
        
        # 分辨率
        width = stream.get('width', 0)
        height = stream.get('height', 0)
        if width and height:
            info['resolution'] = f"{width}x{height}"
            info['is_hd'] = height >= 720
            info['is_4k'] = height >= 2160
        
        # 视频编码
        if 'codec_name' in stream:
            info['video_codec'] = stream['codec_name']
        
        # 编码配置
        if 'profile' in stream:
            info['video_profile'] = stream['profile']
        
        # 编码级别
        if 'level' in stream:
            try:
                info['video_level'] = int(stream['level'])
            except (ValueError, TypeError):
                pass
        
        # 帧率
        if 'avg_frame_rate' in stream:
            frame_rate_str = stream['avg_frame_rate']
            if frame_rate_str and '/' in frame_rate_str:
                try:
                    num, den = map(int, frame_rate_str.split('/'))
                    if den > 0:
                        info['frame_rate'] = round(num / den, 2)
                except (ValueError, ZeroDivisionError):
                    pass
        
        # 像素格式
        if 'pix_fmt' in stream:
            info['pixel_format'] = stream['pix_fmt']
        
        return info
    
    def _extract_audio_stream_info(self, stream: Dict) -> Dict:
        """提取音频流详细信息
        
        Args:
            stream: 音频流数据
            
        Returns:
            Dict: 音频流信息
        """
        info = {}
        
        # 音频编码
        if 'codec_name' in stream:
            info['audio_codec'] = stream['codec_name']
        
        # 采样率
        if 'sample_rate' in stream:
            try:
                info['audio_sample_rate'] = int(stream['sample_rate'])
            except (ValueError, TypeError):
                pass
        
        # 声道数
        if 'channels' in stream:
            try:
                info['audio_channels'] = int(stream['channels'])
            except (ValueError, TypeError):
                pass
        
        # 音频比特率
        if 'bit_rate' in stream:
            try:
                info['audio_bitrate'] = int(stream['bit_rate']) // 1000
            except (ValueError, TypeError):
                pass
        
        return info
    
    def _determine_media_type(self, metadata: Dict) -> str:
        """根据元数据确定媒体类型
        
        Args:
            metadata: 流媒体元数据
            
        Returns:
            str: 媒体类型 (video/audio/radio/unknown)
        """
        has_video = metadata.get('has_video_stream', False)
        has_audio = metadata.get('has_audio_stream', False)
        resolution = metadata.get('resolution', '')
        
        # 如果没有视频流，肯定是音频
        if not has_video:
            return 'audio'
        
        # 检查是否是极低分辨率的视频(可能是误判的音频)
        if resolution and 'x' in resolution:
            try:
                width, height = map(int, resolution.split('x'))
                if width < 100 or height < 100:
                    return 'audio'
            except:
                pass
        
        # 正常视频内容
        return 'video'
    
    def test_download_speed(self, url: str, user_agent: Optional[str] = None) -> float:
        """测试下载速度
        
        通过下载部分数据来计算平均下载速度
        
        Args:
            url: 测试URL
            user_agent: 可选的User-Agent头
            
        Returns:
            float: 下载速度(KB/s)
        """
        try:
            import requests
            from io import BytesIO
            
            # 设置请求头
            headers = {'User-Agent': user_agent} if user_agent else {}
            
            # 开始下载测试
            start_time = time.time()
            response = requests.get(
                url, 
                stream=True, 
                timeout=self.testing_params['timeout'], 
                headers=headers
            )
            
            total_downloaded = 0
            test_duration = self.testing_params['speed_test_duration']
            chunk_size = 64 * 1024  # 64KB chunks
            
            # 下载数据直到达到测试时长
            for chunk in response.iter_content(chunk_size=chunk_size):
                if time.time() - start_time >= test_duration:
                    break
                if chunk:
                    total_downloaded += len(chunk)
            
            # 计算平均速度(KB/s)
            elapsed = time.time() - start_time
            if elapsed > 0:
                speed = total_downloaded / 1024 / elapsed
                self.logger.debug(f"速度测试: {url} - {speed:.2f} KB/s")
                return speed
            
            return 0.0
            
        except Exception as e:
            self.logger.debug(f"速度测试失败 {url}: {e}")
            return 0.0
    
    def check_if_qualified(self, result: Dict) -> bool:
        """检查源是否满足质量要求
        
        实现分层质量检查:
        1. 基本连通性检查
        2. 性能指标检查(延迟、速度)
        3. 技术规格检查(分辨率、比特率)
        4. 特殊要求检查(HD/4K)
        
        Args:
            result: 测试结果数据
            
        Returns:
            bool: 是否合格
        """
        # 基本状态检查
        if result.get('status') != 'success':
            return False
        
        # 媒体类型特定检查
        media_type = result.get('media_type', 'video')
        
        if media_type in ['radio', 'audio']:
            # 音频内容简化检查 - 主要检查延迟
            response_time = result.get('response_time', 9999)
            return response_time <= self.filter_params['max_latency']
        
        # 视频内容详细检查
        
        # 延迟检查
        response_time = result.get('response_time', 9999)
        if response_time > self.filter_params['max_latency']:
            return False
        
        # 分辨率检查
        min_resolution = self.filter_params['min_resolution']
        max_resolution = self.filter_params['max_resolution']
        resolution_filter_mode = self.filter_params.get('resolution_filter_mode', 'range')
        
        if min_resolution or max_resolution:
            resolution = result.get('resolution', '')
            
            if resolution_filter_mode == 'range':
                # 区间模式：必须同时满足最小和最大分辨率
                if min_resolution and not self.is_resolution_meet_min(resolution, min_resolution):
                    return False
                if max_resolution and not self.is_resolution_meet_max(resolution, max_resolution):
                    return False
            elif resolution_filter_mode == 'min_only':
                # 仅最低：只检查最低分辨率
                if min_resolution and not self.is_resolution_meet_min(resolution, min_resolution):
                    return False
            elif resolution_filter_mode == 'max_only':
                # 仅最高：只检查最高分辨率
                if max_resolution and not self.is_resolution_meet_max(resolution, max_resolution):
                    return False
        
        # 比特率检查
        bitrate = result.get('bitrate', 0)
        if bitrate > 0 and bitrate < self.filter_params['min_bitrate']:
            return False
        
        # 特殊质量要求检查
        if self.filter_params['must_hd'] and not result.get('is_hd', False):
            return False
            
        if self.filter_params['must_4k'] and not result.get('is_4k', False):
            return False
        
        # 下载速度检查
        speed = result.get('download_speed', 0)
        if speed > 0 and speed < self.filter_params['min_speed']:
            return False
        
        return True
    
    def is_resolution_meet_min(self, resolution: str, min_resolution: str) -> bool:
        """检查分辨率是否满足最低要求
        
        Args:
            resolution: 实际分辨率 (如 "1920x1080" 或 "1080p")
            min_resolution: 要求的最低分辨率
            
        Returns:
            bool: 是否满足要求
        """
        if not resolution or not min_resolution:
            return True
        
        def parse_resolution(res):
            """将分辨率字符串解析为(宽度, 高度)元组"""
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
        
        res_width, res_height = parse_resolution(resolution)
        min_width, min_height = parse_resolution(min_resolution)
        
        # 比较分辨率尺寸
        return res_width >= min_width and res_height >= min_height
    
    def is_resolution_meet_max(self, resolution: str, max_resolution: str) -> bool:
        """检查分辨率是否不超过最高限制
        
        Args:
            resolution: 实际分辨率
            max_resolution: 要求的最高分辨率
            
        Returns:
            bool: 是否满足要求
        """
        if not resolution or not max_resolution:
            return True
        
        def parse_resolution(res):
            """将分辨率字符串解析为(宽度, 高度)元组"""
            if 'x' in res:
                parts = res.split('x')
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except (ValueError, TypeError):
                        return 9999, 9999  # 返回极大值确保检查失败
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
        
        # 比较分辨率尺寸
        return res_width <= max_width and res_height <= max_height
    
    def log_test_result(self, source: Dict, result: Dict, log_level: str = 'info'):
        """记录测试结果日志
        
        Args:
            source: 原始源数据
            result: 测试结果数据
            log_level: 日志级别
        """
        status = result.get('status', 'unknown')
        is_qualified = result.get('is_qualified', False)
        
        # 构建基础日志信息
        log_message = f"测试结果: 频道={source['name']}, URL={source['url']}, 状态={status}, 合格={is_qualified}"
        
        # 添加详细信息(如果测试成功)
        if status == 'success':
            log_message += f", 延迟={result.get('response_time')}ms"
            
            # 媒体类型信息
            media_type = result.get('media_type', 'unknown')
            log_message += f", 媒体类型={media_type}"
            
            # 视频相关信息
            if media_type == 'video':
                log_message += f", 分辨率={result.get('resolution', '未知')}"
                log_message += f", 比特率={result.get('bitrate', 0)}kbps"
            
            # 速度信息
            if result.get('download_speed'):
                log_message += f", 速度={result.get('download_speed', 0):.2f}KB/s"
        
        # 根据日志级别记录
        log_method = getattr(self.logger, log_level, self.logger.info)
        log_method(log_message)
    
    def normalize_url(self, url: str) -> str:
        """规范化URL用于缓存键
        
        移除可能变化的参数(如时间戳、随机数)，
        确保相同资源的URL能够命中缓存
        
        Args:
            url: 原始URL
            
        Returns:
            str: 规范化后的URL
        """
        try:
            from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
            
            parsed = urlparse(url)
            
            # 解析查询参数
            query_params = parse_qs(parsed.query)
            
            # 过滤掉可能变化的参数
            dynamic_params = ['t', 'time', 'timestamp', 'r', 'random', 'nonce', 'token']
            filtered_params = {
                k: v for k, v in query_params.items() 
                if k not in dynamic_params
            }
            
            # 重建URL
            normalized_query = urlencode(filtered_params, doseq=True)
            
            normalized_url = urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                normalized_query,
                parsed.fragment
            ))
            
            return normalized_url
            
        except Exception as e:
            self.logger.debug(f"URL规范化失败 {url}: {e}")
            return url  # 失败时返回原URL
    
    def _check_network_compatibility(self, url: str) -> bool:
        """检查网络兼容性
        
        主要检查IPv6支持情况
        
        Args:
            url: 要检查的URL
            
        Returns:
            bool: 是否兼容当前网络环境
        """
        # 检查是否是IPv6地址
        if '[' in url and ']' in url:
            # 包含IPv6地址标记
            if not self.check_ipv6_support():
                self.logger.debug(f"跳过IPv6地址(系统不支持): {url}")
                return False
        
        return True
    
    def check_ipv6_support(self) -> bool:
        """检查系统是否支持IPv6
        
        Returns:
            bool: 是否支持IPv6
        """
        try:
            # 尝试创建IPv6 socket来检测支持情况
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            sock.close()
            return True
        except Exception:
            self.logger.warning("系统不支持IPv6，将跳过IPv6地址的测试")
            return False
    
    def _get_cached_result(self, cache_key: str) -> Optional[Dict]:
        """从缓存获取测试结果
        
        Args:
            cache_key: 缓存键
            
        Returns:
            Optional[Dict]: 缓存结果，如果不存在或过期返回None
        """
        if cache_key in _url_cache:
            cached_data = _url_cache[cache_key]
            cache_age = datetime.now() - cached_data['timestamp']
            
            # 检查缓存是否过期
            cache_ttl = timedelta(minutes=self.testing_params['cache_ttl'])
            if cache_age < cache_ttl:
                return {
                    'status': cached_data['status'],
                    'response_time': cached_data['response_time'],
                    **cached_data.get('metadata', {})
                }
            else:
                # 移除过期缓存
                del _url_cache[cache_key]
        
        return None
    
    def _cache_result(self, cache_key: str, result: Dict):
        """缓存测试结果
        
        Args:
            cache_key: 缓存键
            result: 测试结果
        """
        _url_cache[cache_key] = {
            'status': result['status'],
            'response_time': result['response_time'],
            'metadata': {k: v for k, v in result.items() 
                        if k not in ['status', 'response_time']},
            'timestamp': datetime.now()
        }
    
    def cleanup_cache(self):
        """清理过期的缓存项"""
        global _last_cache_cleanup
        
        now = datetime.now()
        if (now - _last_cache_cleanup).total_seconds() > _CACHE_CLEANUP_INTERVAL:
            expired_keys = []
            cache_ttl = timedelta(minutes=self.testing_params['cache_ttl'])
            
            for key, data in _url_cache.items():
                if now - data['timestamp'] > cache_ttl:
                    expired_keys.append(key)
            
            # 移除过期项
            for key in expired_keys:
                del _url_cache[key]
            
            if expired_keys:
                self.logger.debug(f"缓存清理: 移除了 {len(expired_keys)} 个过期项")
            
            _last_cache_cleanup = now