#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
流媒体测试模块
负责测试流媒体源的有效性和质量
"""

import time
import json
import socket
import subprocess
import multiprocessing
import concurrent.futures
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from config_manager import Config

# 全局缓存，避免重复测试相同的URL
_url_cache = {}
_last_cache_cleanup = datetime.now()

class StreamTester:
    """流媒体测试类 - 增强版"""
    
    def __init__(self, config: Config, logger):
        self.config = config
        self.logger = logger
        self.testing_params = config.get_testing_params()
        self.filter_params = config.get_filter_params()
    
    def test_all_sources(self, sources: List[Dict]) -> List[Dict]:
        """测试所有源的有效性"""
        self.cleanup_cache()
        
        total = len(sources)
        self.logger.info(f"开始测试 {total} 个流媒体源")
        
        # 根据系统资源动态调整并发线程数
        max_workers = min(
            self.testing_params['concurrent_threads'],
            multiprocessing.cpu_count() * 2,
            self.testing_params['max_workers']
        )
        
        # 创建进度条
        from tqdm import tqdm
        pbar = tqdm(total=total, desc="测试流媒体源", unit="源")
        
        test_results = []
        
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
                    result = future.result(timeout=self.testing_params['timeout'] + 10)
                    test_results.append(result)
                    
                    # 检查是否合格
                    is_qualified = self.check_if_qualified(result)
                    result['is_qualified'] = is_qualified
                    
                    # 记录详细日志
                    self.log_test_result(source, result)
                    
                    # 更新进度条描述
                    status = result.get('status', 'unknown')
                    if status == 'success':
                        pbar.set_postfix_str(f"有效: {len([r for r in test_results if r.get('status') == 'success'])}/{len(test_results)}")
                    else:
                        pbar.set_postfix_str(f"失败: {len([r for r in test_results if r.get('status') != 'success'])}/{len(test_results)}")
                        
                except concurrent.futures.TimeoutError:
                    self.logger.error(f"测试超时: {source['name']} - {source['url']}")
                    test_results.append({**source, 'status': 'timeout', 'response_time': None, 'is_qualified': False})
                except Exception as e:
                    self.logger.error(f"测试流媒体源时发生错误: {e}")
                    test_results.append({**source, 'status': 'error', 'response_time': None, 'is_qualified': False})
                finally:
                    pbar.update(1)
        
        pbar.close()
        
        # 统计结果
        successful = sum(1 for r in test_results if r.get('status') == 'success')
        qualified = sum(1 for r in test_results if r.get('is_qualified'))
        failed = total - successful
        self.logger.info(f"测试完成: {successful} 个有效, {qualified} 个合格, {failed} 个失败")
        
        return test_results
    
    def test_single_stream(self, source: Dict) -> Dict:
        """测试单个流媒体源"""
        url = source['url']
        user_agent = source.get('user_agent')
        
        # 检查缓存
        cache_key = self.normalize_url(url)
        
        if cache_key in _url_cache:
            cached_result = _url_cache[cache_key]
            if datetime.now() - cached_result['timestamp'] < timedelta(minutes=self.testing_params['cache_ttl']):
                return {**source, 'status': cached_result['status'], 'response_time': cached_result['response_time'], **cached_result.get('metadata', {})}
        
        # 检查URL是否为IPv6地址且系统是否支持IPv6
        if '[' in url and ']' in url and not self.check_ipv6_support():
            return {**source, 'status': 'failed', 'response_time': None, 'is_qualified': False}
        
        # 测试流媒体
        start_time = time.time()
        status, metadata = self.test_stream_url(url, user_agent)
        response_time = round((time.time() - start_time) * 1000)
        
        # 如果需要速度测试，执行速度测试
        if status == 'success' and self.testing_params['enable_speed_test']:
            download_speed = self.test_download_speed(url, user_agent)
            metadata['download_speed'] = download_speed
        
        # 缓存结果
        _url_cache[cache_key] = {
            'status': status,
            'response_time': response_time,
            'metadata': metadata,
            'timestamp': datetime.now()
        }
        
        return {**source, 'status': status, 'response_time': response_time, **metadata}
    
    def test_stream_url(self, url: str, user_agent: Optional[str] = None) -> Tuple[str, Dict]:
        """使用ffprobe测试流媒体URL，返回状态和元数据"""
        try:
            # 使用ffprobe检测流媒体
            timeout_ms = self.testing_params['timeout'] * 1000000
            
            cmd = [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-show_format',
                '-timeout', str(timeout_ms),
                url
            ]
            
            # 添加User-Agent头（如果提供）
            if user_agent:
                cmd.extend(['-headers', f'User-Agent: {user_agent}'])
            
            # 执行命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.testing_params['timeout'] + 2
            )
            
            # 检查结果
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data.get('streams') and len(data['streams']) > 0:
                    # 提取元数据
                    metadata = self.extract_metadata(data)
                    return 'success', metadata
            
            return 'failed', {}
        except subprocess.TimeoutExpired:
            return 'timeout', {}
        except Exception as e:
            self.logger.debug(f"流媒体测试失败 {url}: {e}")
            return 'failed', {}
    
    def extract_metadata(self, data: Dict) -> Dict:
        """从ffprobe输出中提取元数据"""
        metadata = {
            'bitrate': 0,
            'resolution': '',
            'is_hd': False,
            'is_4k': False
        }
        
        # 从format中获取比特率
        if 'format' in data and 'bit_rate' in data['format']:
            try:
                metadata['bitrate'] = int(data['format']['bit_rate']) // 1000  # 转换为kbps
            except (ValueError, TypeError):
                pass
        
        # 从视频流中获取信息
        for stream in data['streams']:
            if stream['codec_type'] == 'video':
                # 分辨率
                width = stream.get('width', 0)
                height = stream.get('height', 0)
                if width and height:
                    metadata['resolution'] = f"{width}x{height}"
                    metadata['is_hd'] = height >= 720  # 720p及以上为HD
                    metadata['is_4k'] = height >= 2160  # 2160p为4K
                break  # 只取第一个视频流
        
        return metadata
    
    def test_download_speed(self, url: str, user_agent: Optional[str] = None) -> float:
        """测试下载速度（KB/s）"""
        try:
            import requests
            from io import BytesIO
            
            # 设置请求头
            headers = {}
            if user_agent:
                headers['User-Agent'] = user_agent
            
            # 下载一小部分数据来测试速度
            start_time = time.time()
            response = requests.get(url, stream=True, timeout=self.testing_params['timeout'], headers=headers)
            content = b''
            
            # 下载一定时间或一定量的数据
            duration = self.testing_params['speed_test_duration']
            for chunk in response.iter_content(chunk_size=1024):
                if time.time() - start_time >= duration:
                    break
                content += chunk
            
            # 计算速度 (KB/s)
            elapsed = time.time() - start_time
            if elapsed > 0:
                return len(content) / 1024 / elapsed
            return 0
        except Exception:
            return 0
    
    def check_if_qualified(self, result: Dict) -> bool:
        """检查源是否合格"""
        if result.get('status') != 'success':
            return False
        
        # 检查延迟
        response_time = result.get('response_time', 9999)
        if response_time > self.filter_params['max_latency']:
            return False
        
        # 检查分辨率（根据筛选模式）
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
        
        # 检查比特率
        bitrate = result.get('bitrate', 0)
        if bitrate < self.filter_params['min_bitrate']:
            return False
        
        # 检查HD/4K要求
        if self.filter_params['must_hd'] and not result.get('is_hd', False):
            return False
            
        if self.filter_params['must_4k'] and not result.get('is_4k', False):
            return False
        
        # 检查速度要求
        speed = result.get('download_speed', 0)
        if speed < self.filter_params['min_speed']:
            return False
        
        return True
    
    def is_resolution_meet_min(self, resolution: str, min_resolution: str) -> bool:
        """检查分辨率是否满足最低要求"""
        if not resolution or not min_resolution:
            return True
        
        # 将分辨率转换为数值
        def parse_resolution(res):
            if 'x' in res:
                # 格式: 1920x1080
                parts = res.split('x')
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except (ValueError, TypeError):
                        return 0, 0
            elif res.endswith('p'):
                # 格式: 1080p
                try:
                    height = int(res[:-1])
                    # 假设宽高比为16:9
                    width = int(height * 16 / 9)
                    return width, height
                except (ValueError, TypeError):
                    return 0, 0
            return 0, 0
        
        res_width, res_height = parse_resolution(resolution)
        min_width, min_height = parse_resolution(min_resolution)
        
        # 比较分辨率
        return res_width >= min_width and res_height >= min_height
    
    def is_resolution_meet_max(self, resolution: str, max_resolution: str) -> bool:
        """检查分辨率是否不超过最高限制"""
        if not resolution or not max_resolution:
            return True
        
        # 将分辨率转换为数值
        def parse_resolution(res):
            if 'x' in res:
                # 格式: 1920x1080
                parts = res.split('x')
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except (ValueError, TypeError):
                        return 9999, 9999  # 返回极大值，确保不会通过最大限制检查
            elif res.endswith('p'):
                # 格式: 1080p
                try:
                    height = int(res[:-1])
                    # 假设宽高比为16:9
                    width = int(height * 16 / 9)
                    return width, height
                except (ValueError, TypeError):
                    return 9999, 9999
            return 9999, 9999
        
        res_width, res_height = parse_resolution(resolution)
        max_width, max_height = parse_resolution(max_resolution)
        
        # 比较分辨率
        return res_width <= max_width and res_height <= max_height
    
    def log_test_result(self, source: Dict, result: Dict):
        """记录测试结果日志"""
        status = result.get('status', 'unknown')
        is_qualified = result.get('is_qualified', False)
        
        log_message = f"测试结果: 频道={source['name']}, URL={source['url']}, 状态={status}, 合格={is_qualified}"
        
        if status == 'success':
            log_message += f", 延迟={result.get('response_time')}ms, 速度={result.get('download_speed', 0):.2f}KB/s"
            log_message += f", 分辨率={result.get('resolution', '未知')}, 比特率={result.get('bitrate', 0)}kbps"
        
        if status == 'success':
            if is_qualified:
                self.logger.info(log_message)
            else:
                self.logger.warning(log_message)
        else:
            self.logger.error(log_message)
    
    def normalize_url(self, url: str) -> str:
        """规范化URL，用于缓存键"""
        try:
            from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
            
            parsed = urlparse(url)
            
            # 移除某些查询参数（如时间戳、随机数）
            query_params = parse_qs(parsed.query)
            filtered_params = {
                k: v for k, v in query_params.items() 
                if k not in ['t', 'time', 'timestamp', 'r', 'random']
            }
            
            # 重建URL
            normalized_query = urlencode(filtered_params, doseq=True)
            
            return urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                normalized_query,
                parsed.fragment
            ))
        except Exception:
            return url
    
    def check_ipv6_support(self) -> bool:
        """检查系统是否支持IPv6"""
        try:
            # 尝试创建一个IPv6 socket
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            sock.close()
            return True
        except Exception:
            self.logger.warning("系统不支持IPv6，将跳过IPv6地址的测试")
            return False
    
    def cleanup_cache(self):
        """清理过期的缓存"""
        global _last_cache_cleanup, _url_cache
        
        now = datetime.now()
        if (now - _last_cache_cleanup).total_seconds() > 300:  # 每5分钟清理一次
            expired_keys = [
                k for k, v in _url_cache.items()
                if now - v['timestamp'] > timedelta(minutes=self.testing_params['cache_ttl'])
            ]
            
            for key in expired_keys:
                del _url_cache[key]
            
            _last_cache_cleanup = now
            self.logger.debug(f"清理缓存: 移除了 {len(expired_keys)} 个过期项")