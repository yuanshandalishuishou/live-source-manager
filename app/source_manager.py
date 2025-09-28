#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源管理模块 - 修复网络问题版本
"""

import os
import re
import aiohttp
import aiofiles
import asyncio
import aiohttp_socks
import socket
from urllib.parse import urlparse
from typing import List, Dict, Optional
from config_manager import Config
from channel_rules import ChannelRules

class SourceManager:
    """源管理类 - 增强网络容错版"""
    
    def __init__(self, config: Config, logger, channel_rules: ChannelRules):
        self.config = config
        self.logger = logger
        self.channel_rules = channel_rules
        self.network_config = config.get_network_config()
        self.github_config = config.get_github_config()
        self.user_agents = config.get_user_agents()
        self.ua_enabled = config.is_ua_enabled()
        self.online_dir = "/config/online"
        
        os.makedirs(self.online_dir, exist_ok=True)
    
    async def create_session(self, use_proxy: bool = False) -> aiohttp.ClientSession:
        """创建HTTP会话 - 增强容错版"""
        connector = None
        
        # 设置更宽松的超时
        timeout = aiohttp.ClientTimeout(total=60, connect=30, sock_connect=30, sock_read=30)
        
        family = socket.AF_INET
        if self.network_config['ipv6_enabled']:
            family = socket.AF_UNSPEC
        
        if use_proxy and self.network_config['proxy_enabled']:
            proxy_type = self.network_config['proxy_type'].lower()
            proxy_host = self.network_config['proxy_host']
            proxy_port = self.network_config['proxy_port']
            proxy_username = self.network_config['proxy_username']
            proxy_password = self.network_config['proxy_password']
            
            try:
                if proxy_type in ['socks5', 'socks5h']:
                    if proxy_username and proxy_password:
                        proxy_url = f"{proxy_type}://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"
                    else:
                        proxy_url = f"{proxy_type}://{proxy_host}:{proxy_port}"
                    
                    connector = aiohttp_socks.ProxyConnector.from_url(
                        proxy_url, 
                        family=family,
                        verify_ssl=False,
                        limit=100
                    )
                else:
                    # 对于HTTP代理，使用aiohttp内置支持
                    if proxy_username and proxy_password:
                        proxy_auth = aiohttp.BasicAuth(proxy_username, proxy_password)
                    else:
                        proxy_auth = None
                        
                    connector = aiohttp.TCPConnector(
                        family=family,
                        verify_ssl=False,
                        limit=100
                    )
            except Exception as e:
                self.logger.warning(f"创建代理连接器失败: {e}, 将使用直连")
                connector = aiohttp.TCPConnector(family=family, verify_ssl=False, limit=100)
        else:
            connector = aiohttp.TCPConnector(family=family, verify_ssl=False, limit=100)
        
        return aiohttp.ClientSession(connector=connector, timeout=timeout)
    
    async def download_all_sources(self) -> List[str]:
        """下载所有源文件 - 增强容错版"""
        downloaded_files = []
        
        # 获取在线URL列表
        online_urls = self.config.get_sources()['online_urls']
        
        self.logger.info(f"开始下载 {len(online_urls)} 个源文件")
        
        # 分批下载，避免过多并发
        batch_size = 3
        for i in range(0, len(online_urls), batch_size):
            batch_urls = online_urls[i:i + batch_size]
            self.logger.info(f"下载批次 {i//batch_size + 1}/{(len(online_urls)-1)//batch_size + 1}")
            
            tasks = []
            for url in batch_urls:
                tasks.append(self.download_with_retry(url))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for j, result in enumerate(results):
                url = batch_urls[j]
                if isinstance(result, Exception):
                    self.logger.error(f"下载失败 {url}: {result}")
                elif result:
                    downloaded_files.append(result)
                    self.logger.info(f"下载成功: {url}")
            
            # 批次之间短暂暂停
            await asyncio.sleep(1)
        
        self.logger.info(f"成功下载 {len(downloaded_files)} 个源文件")
        return downloaded_files
    
    async def download_with_retry(self, url: str, max_retries: int = 2) -> Optional[str]:
        """带重试机制的下载 - 简化版"""
        strategies = [
            {'type': 'direct', 'use_proxy': False},
            {'type': 'proxy', 'use_proxy': True}
        ]
        
        for strategy in strategies:
            try:
                result = await self.download_file(url, strategy)
                if result:
                    return result
            except Exception as e:
                self.logger.warning(f"下载失败 [{strategy['type']}]: {url} - {e}")
        
        self.logger.error(f"所有下载策略均失败: {url}")
        return None
    
    async def download_file(self, url: str, strategy: Dict) -> Optional[str]:
        """下载单个文件 - 简化错误处理"""
        session = None
        try:
            self.logger.info(f"尝试下载 [{strategy['type']}]: {url}")
            session = await self.create_session(strategy['use_proxy'])
            
            async with session.get(url) as response:
                if response.status == 200:
                    content = await response.text()
                    
                    # 保存文件
                    filename = self.get_filename_from_url(url)
                    filepath = os.path.join(self.online_dir, filename)
                    
                    async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                        await f.write(content)
                    
                    return filepath
                else:
                    raise Exception(f"HTTP错误 {response.status}")
                    
        except Exception as e:
            self.logger.debug(f"下载详细错误 [{strategy['type']}]: {url} - {e}")
            raise
        finally:
            if session:
                await session.close()
    
    def get_filename_from_url(self, url: str) -> str:
        """从URL提取文件名"""
        clean_url = url.split('?')[0]
        filename = clean_url.split('/')[-1]
        
        if not filename or '.' not in filename:
            import hashlib
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            filename = f"source_{url_hash}.txt"
        
        return filename
    
    def parse_all_files(self) -> List[Dict]:
        """解析所有源文件"""
        all_sources = []
        
        # 解析本地文件
        local_dirs = self.config.get_sources()['local_dirs']
        for local_dir in local_dirs:
            if os.path.exists(local_dir):
                try:
                    sources = self.parse_local_files(local_dir)
                    all_sources.extend(sources)
                except Exception as e:
                    self.logger.error(f"解析本地文件失败 {local_dir}: {e}")
        
        # 解析在线文件
        try:
            online_sources = self.parse_local_files(self.online_dir)
            all_sources.extend(online_sources)
        except Exception as e:
            self.logger.error(f"解析在线文件失败: {e}")
        
        self.logger.info(f"成功解析 {len(all_sources)} 个源")
        return all_sources
    
    def parse_local_files(self, directory: str) -> List[Dict]:
        """解析本地目录中的所有源文件"""
        sources = []
        
        for root, _, files in os.walk(directory):
            for file in files:
                if file.endswith(('.m3u', '.m3u8', '.txt')):
                    file_path = os.path.join(root, file)
                    try:
                        file_sources = self.parse_file(file_path)
                        sources.extend(file_sources)
                    except Exception as e:
                        self.logger.error(f"解析文件失败 {file_path}: {e}")
        
        return sources
    
    def parse_file(self, file_path: str) -> List[Dict]:
        """解析单个源文件"""
        sources = []
        
        # 确定源类型
        source_type = "online" if file_path.startswith(self.online_dir) else "local"
        source_path = file_path.replace(self.online_dir + "/", "") if source_type == "online" else file_path
        
        # 检查UA配置
        user_agent = None
        if self.ua_enabled:
            user_agent = self.user_agents.get(source_path) or self.user_agents.get(file_path)
        
        # 读取文件内容
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin1']
        content = None
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                continue
        
        if content is None:
            with open(file_path, 'rb') as f:
                content_bytes = f.read()
            content = content_bytes.decode('utf-8', errors='ignore')
        
        # 解析内容
        lines = content.splitlines()
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith('#EXTM3U'):
                i += 1
                continue
            
            if line.startswith('#EXTINF:'):
                extinf = line
                i += 1
                if i < len(lines):
                    url = lines[i].strip()
                    if url and not url.startswith('#'):
                        name = self.extract_name(extinf)
                        try:
                            name = name.encode('latin1').decode('utf-8')
                        except:
                            pass
                        logo = self.extract_logo(extinf)
                        group = self.extract_group(extinf)
                        
                        # 处理URL中的UA信息
                        url_parts = url.split('|')
                        stream_url = url_parts[0]
                        url_user_agent = user_agent
                        
                        if len(url_parts) > 1 and 'User-Agent=' in url_parts[1]:
                            url_user_agent = url_parts[1].replace('User-Agent=', '')
                        
                        # 提取频道信息
                        channel_info = self.channel_rules.extract_channel_info(name)
                        
                        sources.append({
                            'name': name,
                            'url': stream_url,
                            'logo': logo,
                            'source_type': source_type,
                            'source_path': source_path,
                            'user_agent': url_user_agent,
                            'group': group,
                            'category': self.channel_rules.determine_category(name),
                            'country': channel_info.get('country', 'CN'),
                            'region': channel_info.get('region'),
                            'language': channel_info.get('language', 'zh')
                        })
            else:
                if line and not line.startswith('#') and self.is_valid_url(line):
                    name = f"Channel from {os.path.basename(file_path)}"
                    try:
                        name = name.encode('latin1').decode('utf-8')
                    except:
                        pass
                    channel_info = self.channel_rules.extract_channel_info(name)
                    
                    url_parts = line.split('|')
                    stream_url = url_parts[0]
                    url_user_agent = user_agent
                    
                    if len(url_parts) > 1 and 'User-Agent=' in url_parts[1]:
                        url_user_agent = url_parts[1].replace('User-Agent=', '')
                    
                    sources.append({
                        'name': name,
                        'url': stream_url,
                        'logo': None,
                        'source_type': source_type,
                        'source_path': source_path,
                        'user_agent': url_user_agent,
                        'group': source_path,
                        'category': self.channel_rules.determine_category(name),
                        'country': channel_info.get('country', 'CN'),
                        'region': channel_info.get('region'),
                        'language': channel_info.get('language', 'zh')
                    })
            
            i += 1
        
        return sources
    
    def extract_name(self, extinf_line: str) -> str:
        """从EXTINF行提取频道名称"""
        match = re.search(r',([^,]+)$', extinf_line)
        if match:
            return match.group(1).strip()
        return "Unknown Channel"
    
    def extract_logo(self, extinf_line: str) -> Optional[str]:
        """从EXTINF行提取频道图标"""
        match = re.search(r'tvg-logo="([^"]+)"', extinf_line)
        if match:
            return match.group(1).strip()
        return None
    
    def extract_group(self, extinf_line: str) -> Optional[str]:
        """从EXTINF行提取分组信息"""
        match = re.search(r'group-title="([^"]+)"', extinf_line)
        if match:
            return match.group(1).strip()
        return None
    
    def is_valid_url(self, url: str) -> bool:
        """检查URL是否有效"""
        try:
            clean_url = url.split('|')[0]
            result = urlparse(clean_url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False