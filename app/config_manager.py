#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块
负责配置文件的加载、验证和管理
增强内容:
- 增强日志系统错误处理
- 改进配置文件编码检测
- 添加缺失的配置获取方法
"""

import os
import sys
import logging
import logging.handlers
import configparser
from typing import Dict, List, Any, Optional

class Config:
    """配置管理类 - 增强版"""
    
    def __init__(self, config_path: str = "/config/config.ini"):
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load_config()
    
    def load_config(self):
        """加载配置文件 - 增强编码检测"""
        if not os.path.exists(self.config_path):
            self.create_default_config()
            print(f"创建默认配置文件: {self.config_path}")
            return
        
        # 尝试多种编码读取配置文件
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin1', 'utf-8-sig']
        for encoding in encodings:
            try:
                with open(self.config_path, 'r', encoding=encoding) as f:
                    content = f.read()
                    # 移除BOM标记（如果存在）
                    if content.startswith('\ufeff'):
                        content = content[1:]
                    self.config.read_string(content)
                print(f"配置文件加载成功，编码: {encoding}")
                return
            except (UnicodeDecodeError, configparser.Error) as e:
                print(f"编码 {encoding} 失败: {e}")
                continue
        
        # 如果所有编码都失败，使用默认方式读取
        try:
            self.config.read(self.config_path)
            print("使用默认方式加载配置文件")
        except Exception as e:
            print(f"配置文件加载失败: {e}")
            self.create_default_config()
    
    def create_default_config(self):
        """创建默认配置文件"""
        self.config['Sources'] = {
            'local_dirs': '/config/sources',
            'online_urls': 'https://live.zbds.org/tv/iptv4.m3u\nhttps://raw.githubusercontent.com/YueChan/Live/main/APTV.m3u'
        }
        
        self.config['Network'] = {
            'proxy_enabled': 'False',
            'proxy_type': 'socks5',
            'proxy_host': '192.168.1.211',
            'proxy_port': '1800',
            'proxy_username': '',
            'proxy_password': '',
            'ipv6_enabled': 'False'
        }
        
        self.config['HTTPServer'] = {
            'enabled': 'True',
            'host': '0.0.0.0',
            'port': '12345',
            'document_root': '/www/output'
        }
        
        self.config['GitHub'] = {
            'api_url': 'https://api.github.com',
            'api_token': '',
            'rate_limit': '5000'
        }
        
        self.config['Testing'] = {
            'timeout': '10',
            'concurrent_threads': '30',
            'cache_ttl': '120',
            'enable_speed_test': 'True',
            'speed_test_duration': '6'
        }
        
        self.config['Output'] = {
            'filename': 'live.m3u',
            'group_by': 'category',
            'include_failed': 'False',
            'max_sources_per_channel': '3',
            'enable_filter': 'False'
        }
        
        self.config['Logging'] = {
            'level': 'INFO',
            'file': '/log/app.log',
            'max_size': '10',
            'backup_count': '5'
        }
        
        self.config['Filter'] = {
            'max_latency': '5000',
            'min_bitrate': '100',
            'must_hd': 'False',
            'must_4k': 'False',
            'min_speed': '40',
            'min_resolution': '720p',
            'max_resolution': '4k',
            'resolution_filter_mode': 'range'
        }
        
        self.config['UserAgents'] = {
            'ua_position': 'extinf',
            'ua_enabled': 'True'
        }
        
        # 保存默认配置
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)
    
    # 添加缺失的配置获取方法
    def get_logging_config(self) -> Dict:
        """获取日志配置"""
        if not self.config.has_section('Logging'):
            return {
                'level': 'INFO',
                'file': '/log/app.log',
                'max_size': 10,
                'backup_count': 5,
                'enable_console': True
            }
        
        return {
            'level': self.config.get('Logging', 'level', fallback='INFO'),
            'file': self.config.get('Logging', 'file', fallback='/log/app.log'),
            'max_size': self.config.getint('Logging', 'max_size', fallback=10),
            'backup_count': self.config.getint('Logging', 'backup_count', fallback=5),
            'enable_console': True
        }
    
    def get_network_config(self) -> Dict:
        """获取网络配置"""
        if not self.config.has_section('Network'):
            return {
                'proxy_enabled': False,
                'proxy_type': 'socks5',
                'proxy_host': '192.168.1.211',
                'proxy_port': 1800,
                'proxy_username': '',
                'proxy_password': '',
                'ipv6_enabled': False
            }
        
        return {
            'proxy_enabled': self.config.getboolean('Network', 'proxy_enabled', fallback=False),
            'proxy_type': self.config.get('Network', 'proxy_type', fallback='socks5'),
            'proxy_host': self.config.get('Network', 'proxy_host', fallback='192.168.1.211'),
            'proxy_port': self.config.getint('Network', 'proxy_port', fallback=1800),
            'proxy_username': self.config.get('Network', 'proxy_username', fallback=''),
            'proxy_password': self.config.get('Network', 'proxy_password', fallback=''),
            'ipv6_enabled': self.config.getboolean('Network', 'ipv6_enabled', fallback=False)
        }
    
    def get_github_config(self) -> Dict:
        """获取GitHub配置"""
        if not self.config.has_section('GitHub'):
            return {
                'api_url': 'https://api.github.com',
                'api_token': '',
                'rate_limit': 5000
            }
        
        return {
            'api_url': self.config.get('GitHub', 'api_url', fallback='https://api.github.com'),
            'api_token': self.config.get('GitHub', 'api_token', fallback=''),
            'rate_limit': self.config.getint('GitHub', 'rate_limit', fallback=5000)
        }
    
    def get_testing_params(self) -> Dict:
        """获取测试参数"""
        if not self.config.has_section('Testing'):
            return {
                'timeout': 10,
                'concurrent_threads': 30,
                'cache_ttl': 120,
                'enable_speed_test': True,
                'speed_test_duration': 6,
                'max_workers': 50
            }
        
        return {
            'timeout': self.config.getint('Testing', 'timeout', fallback=10),
            'concurrent_threads': self.config.getint('Testing', 'concurrent_threads', fallback=30),
            'cache_ttl': self.config.getint('Testing', 'cache_ttl', fallback=120),
            'enable_speed_test': self.config.getboolean('Testing', 'enable_speed_test', fallback=True),
            'speed_test_duration': self.config.getint('Testing', 'speed_test_duration', fallback=6),
            'max_workers': 50  # 默认值
        }
    
    def get_filter_params(self) -> Dict:
        """获取过滤参数"""
        if not self.config.has_section('Filter'):
            return {
                'max_latency': 5000,
                'min_bitrate': 100,
                'must_hd': False,
                'must_4k': False,
                'min_speed': 40,
                'min_resolution': '720p',
                'max_resolution': '4k',
                'resolution_filter_mode': 'range'
            }
        
        return {
            'max_latency': self.config.getint('Filter', 'max_latency', fallback=5000),
            'min_bitrate': self.config.getint('Filter', 'min_bitrate', fallback=100),
            'must_hd': self.config.getboolean('Filter', 'must_hd', fallback=False),
            'must_4k': self.config.getboolean('Filter', 'must_4k', fallback=False),
            'min_speed': self.config.getint('Filter', 'min_speed', fallback=40),
            'min_resolution': self.config.get('Filter', 'min_resolution', fallback='720p'),
            'max_resolution': self.config.get('Filter', 'max_resolution', fallback='4k'),
            'resolution_filter_mode': self.config.get('Filter', 'resolution_filter_mode', fallback='range')
        }
    
    def get_output_params(self) -> Dict:
        """获取输出参数"""
        if not self.config.has_section('Output'):
            return {
                'filename': 'live.m3u',
                'group_by': 'category',
                'include_failed': False,
                'max_sources_per_channel': 3,
                'enable_filter': False,
                'output_dir': '/www/output'  # Nginx版固定输出目录
            }
        
        return {
            'filename': self.config.get('Output', 'filename', fallback='live.m3u'),
            'group_by': self.config.get('Output', 'group_by', fallback='category'),
            'include_failed': self.config.getboolean('Output', 'include_failed', fallback=False),
            'max_sources_per_channel': self.config.getint('Output', 'max_sources_per_channel', fallback=3),
            'enable_filter': self.config.getboolean('Output', 'enable_filter', fallback=False),
            'output_dir': '/www/output'  # Nginx版固定输出目录
        }
    
    def get_ua_position(self) -> str:
        """获取UA位置配置"""
        if not self.config.has_section('UserAgents'):
            return 'extinf'
        
        return self.config.get('UserAgents', 'ua_position', fallback='extinf')
    
    def is_ua_enabled(self) -> bool:
        """是否启用UA功能"""
        if not self.config.has_section('UserAgents'):
            return True
        
        return self.config.getboolean('UserAgents', 'ua_enabled', fallback=True)
    
    def get_user_agents(self) -> Dict:
        """获取User-Agent配置"""
        if not self.config.has_section('UserAgents'):
            return {}
        
        # 从配置文件中读取User-Agent配置
        ua_config = {}
        for key, value in self.config.items('UserAgents'):
            if key not in ['ua_position', 'ua_enabled']:
                ua_config[key] = value
        
        return ua_config
    
    def get_sources(self) -> Dict:
        """获取源配置"""
        if not self.config.has_section('Sources'):
            return {
                'local_dirs': ['/config/sources'],
                'online_urls': []
            }
        
        local_dirs = self.config.get('Sources', 'local_dirs', fallback='/config/sources')
        # 将字符串转换为列表
        if isinstance(local_dirs, str):
            local_dirs = [d.strip() for d in local_dirs.split(',')]
        
        online_urls = self.config.get('Sources', 'online_urls', fallback='')
        # 将字符串转换为列表，每行一个URL
        if online_urls:
            online_urls = [url.strip() for url in online_urls.split('\n') if url.strip()]
        else:
            online_urls = []
        
        return {
            'local_dirs': local_dirs,
            'online_urls': online_urls
        }
    
    def get_http_server_config(self) -> Dict:
        """获取HTTP服务器配置（Nginx版中可能不需要）"""
        # 在Nginx版中，HTTP服务器由Nginx提供
        return {
            'enabled': False,
            'host': '0.0.0.0',
            'port': 12345,
            'document_root': '/www/output'
        }

class Logger:
    """日志管理类 - 增强错误处理"""
    
    def __init__(self, config: Dict):
        self.logger = self.setup_logging(config)
    
    def setup_logging(self, config: Dict):
        """配置日志系统 - 增强错误处理"""
        # 首先创建一个基本的logger
        import logging
        logger = logging.getLogger('LiveSourceManager')
        
        # 设置日志级别
        log_level = getattr(logging, config.get('level', 'INFO').upper(), logging.INFO)
        logger.setLevel(log_level)
        
        # 清除现有处理器
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # 创建格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 文件处理器（如果配置了文件路径）
        log_file = config.get('file', '/log/app.log')
        file_handler = None
        
        if log_file:
            try:
                # 确保日志目录存在
                log_dir = os.path.dirname(log_file)
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)
                    print(f"创建日志目录: {log_dir}")
                
                # 清空日志文件（如果配置了）
                if config.get('clear_on_startup', False) and os.path.exists(log_file):
                    try:
                        os.remove(log_file)
                        print(f"清空日志文件: {log_file}")
                    except Exception as e:
                        print(f"无法清空日志文件: {e}")
                
                # 创建RotatingFileHandler
                max_size = config.get('max_size', 10) * 1024 * 1024  # 转换为字节
                backup_count = config.get('backup_count', 5)
                
                file_handler = logging.handlers.RotatingFileHandler(
                    log_file,
                    maxBytes=max_size,
                    backupCount=backup_count,
                    encoding='utf-8'
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
                print(f"文件日志处理器创建成功: {log_file}")
            except Exception as e:
                print(f"创建文件日志处理器失败: {e}")
                # 继续执行，使用控制台日志
        
        # 控制台处理器（如果启用）
        if config.get('enable_console', True):
            try:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setFormatter(formatter)
                logger.addHandler(console_handler)
                print("控制台日志处理器创建成功")
            except Exception as e:
                print(f"创建控制台日志处理器失败: {e}")
        
        # 如果没有任何处理器，添加一个基本的控制台处理器
        if not logger.handlers:
            print("警告: 无日志处理器，创建基本控制台处理器")
            basic_handler = logging.StreamHandler(sys.stdout)
            basic_handler.setFormatter(formatter)
            logger.addHandler(basic_handler)
        
        # 测试日志系统
        try:
            logger.info("日志系统初始化完成")
        except Exception as e:
            print(f"日志系统测试失败: {e}")
        
        return logger