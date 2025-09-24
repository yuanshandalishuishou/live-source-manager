#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播源管理工具 - SQLite 增强版
功能：从多个来源获取直播源，测试有效性，生成M3U播放列表

主要特点：
1. 增强的下载逻辑，支持多级下载策略
2. 完整的来源追踪和记录
3. 增强的频道信息提取和分类（使用外部YAML规则文件）
4. 优化的SQLite数据库结构
5. 详细的日志记录
6. 支持UA信息处理，可配置UA位置（EXTINF属性或URL参数）
7. 支持过滤功能开关
8. 增强的统计功能和筛选参数建议
9. 增加API接口支持
10. 优化数据库连接管理
11. 增强错误处理和重试机制
12. 新增分辨率筛选模式（区间、最小、最大）
13. 新增多格式输出（M3U和TXT格式，每种格式包含有效源和筛选后源）
"""

import os
import re
import sys
import time
import json
import logging
import logging.handlers
import configparser
import tempfile
import concurrent.futures
import socket
import asyncio
import ssl
import traceback
import threading
import requests
import sqlite3
import subprocess
import multiprocessing
import yaml
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from typing import List, Dict, Set, Tuple, Optional, Any
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass

import aiohttp
import aiohttp_socks
import aiofiles
import tqdm
from dateutil.parser import parse as parse_date

# 全局缓存，避免重复测试相同的URL
_url_cache = {}
_last_cache_cleanup = datetime.now()

# 数据库连接池
_db_connections = {}
_db_lock = threading.Lock()

class LogLevel(Enum):
    """日志级别枚举"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class SourceType(Enum):
    """源类型枚举"""
    LOCAL = "local"
    ONLINE = "online"
    GITHUB = "github"

@dataclass
class ChannelInfo:
    """频道信息数据类"""
    name: str
    clean_name: str
    channel_type: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    language: Optional[str] = None
    continent: Optional[str] = None

@dataclass
class SourceMetadata:
    """源元数据数据类"""
    bitrate: int = 0
    resolution: str = ""
    is_hd: bool = False
    is_4k: bool = False
    response_time: int = 0
    download_speed: float = 0.0

class Config:
    """配置管理类 - 增强版"""
    
    def __init__(self, config_path: str = "/config/config.ini"):
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load_config()
    
    def load_config(self):
        """加载配置文件"""
        if not os.path.exists(self.config_path):
            self.create_default_config()
            return
        
        # 尝试多种编码读取配置文件
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin1']
        for encoding in encodings:
            try:
                with open(self.config_path, 'r', encoding=encoding) as f:
                    self.config.read_file(f)
                return
            except UnicodeDecodeError:
                continue
        
        # 如果所有编码都失败，使用默认方式读取
        self.config.read(self.config_path)
    
    def create_default_config(self):
        """创建默认配置文件"""
        self.config['Sources'] = {
            'local_dirs': '/config/sources',
            'online_urls': '',
            'github_sources': ''
        }
        self.config['Network'] = {
            'proxy_enabled': 'False',
            'proxy_type': 'http',
            'proxy_host': '',
            'proxy_port': '8080',
            'proxy_username': '',
            'proxy_password': '',
            'github_mirror_enabled': 'True',
            'github_mirror_url': 'https://hub.fastgit.org',
            'ipv6_enabled': 'False',
            'retry_times': '3',
            'retry_delay': '1'
        }
        self.config['GitHub'] = {
            'api_url': 'https://api.github.com',
            'api_token': '',
            'rate_limit': '5000'
        }
        self.config['Testing'] = {
            'timeout': '10',
            'concurrent_threads': '20',
            'cache_ttl': '120',
            'enable_speed_test': 'True',
            'speed_test_duration': '5',
            'max_workers': '50'
        }
        self.config['Output'] = {
            'filename': 'live.m3u',
            'group_by': 'category',
            'include_failed': 'False',
            'max_sources_per_channel': '4',
            'enable_filter': 'True',
            'output_dir': '/www/output'
        }
        self.config['Logging'] = {
            'level': 'INFO',
            'file': '/log/app.log',
            'max_size': '10',
            'backup_count': '5',
            'clear_on_startup': 'True',
            'enable_console': 'True'
        }
        self.config['Database'] = {
            'type': 'sqlite',
            'path': '/data/livesourcemanager.db',
            'cleanup_days': '30',
            'connection_pool_size': '5'
        }
        self.config['Filter'] = {
            'min_resolution': '720p',
            'max_resolution': '1080p',
            'max_latency': '5000',
            'min_bitrate': '200',
            'must_hd': 'False',
            'must_4k': 'False',
            'min_speed': '0',
            'resolution_filter_mode': 'range'  # range: 范围筛选, min_only: 仅最低要求, max_only: 仅最高限制
        }
        # 已移除AllInOne配置部分
        self.config['UserAgents'] = {
            'ua_position': 'url',  # UA位置配置: extinf (作为EXTINF属性) 或 url (作为URL参数)
            'ua_enabled': 'True',  # 是否启用UA功能
            # 示例配置，可以为特定源文件或URL指定UA
            # 'source_path_or_url': 'User-Agent-String'
        }
        self.config['API'] = {
            'enabled': 'False',
            'host': '0.0.0.0',
            'port': '8080',
            'auth_required': 'False',
            'username': 'admin',
            'password': 'password'
        }
        
        # 确保配置目录存在
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w') as f:
            self.config.write(f)
    
    def get_sources(self) -> Dict[str, List[str]]:
        """获取所有源配置"""
        return {
            'local_dirs': self.config.get('Sources', 'local_dirs', fallback='').split(),
            'online_urls': [url.strip() for url in self.config.get('Sources', 'online_urls', fallback='').split('\n') if url.strip()],
            'github_sources': [repo.strip() for repo in self.config.get('Sources', 'github_sources', fallback='').split('\n') if repo.strip()]
        }
    
    def get_network_config(self) -> Dict:
        """获取网络配置"""
        return {
            'proxy_enabled': self.config.getboolean('Network', 'proxy_enabled', fallback=False),
            'proxy_type': self.config.get('Network', 'proxy_type', fallback='http'),
            'proxy_host': self.config.get('Network', 'proxy_host', fallback=''),
            'proxy_port': self.config.getint('Network', 'proxy_port', fallback=8080),
            'proxy_username': self.config.get('Network', 'proxy_username', fallback=''),
            'proxy_password': self.config.get('Network', 'proxy_password', fallback=''),
            'github_mirror_enabled': self.config.getboolean('Network', 'github_mirror_enabled', fallback=True),
            'github_mirror_url': self.config.get('Network', 'github_mirror_url', fallback='https://hub.fastgit.org'),
            'ipv6_enabled': self.config.getboolean('Network', 'ipv6_enabled', fallback=False),
            'retry_times': self.config.getint('Network', 'retry_times', fallback=3),
            'retry_delay': self.config.getint('Network', 'retry_delay', fallback=1)
        }
    
    def get_github_config(self) -> Dict:
        """获取GitHub配置"""
        return {
            'api_url': self.config.get('GitHub', 'api_url', fallback='https://api.github.com'),
            'api_token': self.config.get('GitHub', 'api_token', fallback=''),
            'rate_limit': self.config.getint('GitHub', 'rate_limit', fallback=5000)
        }
    
    def get_testing_params(self) -> Dict:
        """获取测试参数"""
        return {
            'timeout': self.config.getint('Testing', 'timeout', fallback=10),
            'concurrent_threads': self.config.getint('Testing', 'concurrent_threads', fallback=20),
            'cache_ttl': self.config.getint('Testing', 'cache_ttl', fallback=120),
            'enable_speed_test': self.config.getboolean('Testing', 'enable_speed_test', fallback=True),
            'speed_test_duration': self.config.getint('Testing', 'speed_test_duration', fallback=5),
            'max_workers': self.config.getint('Testing', 'max_workers', fallback=50)
        }
    
    def get_output_params(self) -> Dict:
        """获取输出参数"""
        return {
            'filename': self.config.get('Output', 'filename', fallback='live.m3u'),
            'group_by': self.config.get('Output', 'group_by', fallback='source'),
            'include_failed': self.config.getboolean('Output', 'include_failed', fallback=False),
            'max_sources_per_channel': self.config.getint('Output', 'max_sources_per_channel', fallback=4),
            'enable_filter': self.config.getboolean('Output', 'enable_filter', fallback=True),
            'output_dir': self.config.get('Output', 'output_dir', fallback='/www/output')
        }
    
    def get_logging_config(self) -> Dict:
        """获取日志配置"""
        return {
            'level': self.config.get('Logging', 'level', fallback='INFO'),
            'file': self.config.get('Logging', 'file', fallback='/log/app.log'),
            'max_size': self.config.getint('Logging', 'max_size', fallback=10) * 1024 * 1024,
            'backup_count': self.config.getint('Logging', 'backup_count', fallback=5),
            'clear_on_startup': self.config.getboolean('Logging', 'clear_on_startup', fallback=True),
            'enable_console': self.config.getboolean('Logging', 'enable_console', fallback=True)
        }
    
    def get_database_config(self) -> Dict:
        """获取数据库配置"""
        return {
            'type': self.config.get('Database', 'type', fallback='sqlite'),
            'path': self.config.get('Database', 'path', fallback='/data/livesourcemanager.db'),
            'cleanup_days': self.config.getint('Database', 'cleanup_days', fallback=30),
            'connection_pool_size': self.config.getint('Database', 'connection_pool_size', fallback=5)
        }
    
    def get_filter_params(self) -> Dict:
        """获取过滤参数"""
        return {
            'min_resolution': self.config.get('Filter', 'min_resolution', fallback='720p'),
            'max_resolution': self.config.get('Filter', 'max_resolution', fallback='1080p'),
            'max_latency': self.config.getint('Filter', 'max_latency', fallback=5000),
            'min_bitrate': self.config.getint('Filter', 'min_bitrate', fallback=200),
            'must_hd': self.config.getboolean('Filter', 'must_hd', fallback=False),
            'must_4k': self.config.getboolean('Filter', 'must_4k', fallback=False),
            'min_speed': self.config.getint('Filter', 'min_speed', fallback=0),
            'resolution_filter_mode': self.config.get('Filter', 'resolution_filter_mode', fallback='range')
        }
    
    # 已移除get_allinone_config方法
    
    def get_user_agents(self) -> Dict[str, str]:
        """获取UA配置"""
        ua_config = {}
        if 'UserAgents' in self.config:
            for key in self.config['UserAgents']:
                if key not in ['ua_position', 'ua_enabled']:  # 排除UA位置和启用配置
                    ua_config[key] = self.config['UserAgents'][key]
        return ua_config
    
    def get_ua_position(self) -> str:
        """获取UA位置配置 (extinf或url)"""
        return self.config.get('UserAgents', 'ua_position', fallback='url')
    
    def is_ua_enabled(self) -> bool:
        """检查UA功能是否启用"""
        return self.config.getboolean('UserAgents', 'ua_enabled', fallback=True)
    
    def get_api_config(self) -> Dict:
        """获取API配置"""
        return {
            'enabled': self.config.getboolean('API', 'enabled', fallback=False),
            'host': self.config.get('API', 'host', fallback='0.0.0.0'),
            'port': self.config.getint('API', 'port', fallback=8080),
            'auth_required': self.config.getboolean('API', 'auth_required', fallback=False),
            'username': self.config.get('API', 'username', fallback='admin'),
            'password': self.config.get('API', 'password', fallback='password')
        }

class ChannelRules:
    """频道规则管理类 - 从YAML文件加载规则"""
    
    def __init__(self, rules_path: str = "/config/channel_rules.yml"):
        self.rules_path = rules_path
        self.rules = self.load_rules()
    
    def load_rules(self) -> Dict:
        """从YAML文件加载频道规则"""
        default_rules = {
            'categories': [
                {'name': '央视频道', 'priority': 1, 'keywords': ['CCTV', '央视', '中央']},
                {'name': '卫视频道', 'priority': 5, 'keywords': ['卫视']},
                {'name': '影视频道', 'priority': 10, 'keywords': ['电影', '影院', '剧场', '影视']},
                {'name': '体育频道', 'priority': 10, 'keywords': ['体育', '赛事', '奥运', '足球', '篮球']},
                {'name': '少儿频道', 'priority': 10, 'keywords': ['少儿', '卡通', '动画', '动漫']},
                {'name': '新闻频道', 'priority': 10, 'keywords': ['新闻', '资讯']},
                {'name': '纪实频道', 'priority': 10, 'keywords': ['纪实', '纪录', '探索', '发现']},
                {'name': '音乐频道', 'priority': 10, 'keywords': ['音乐']},
                {'name': '地方频道', 'priority': 15, 'keywords': ['本地', '公共', '都市', '生活', '经济', '综合']},
                {'name': '港澳台', 'priority': 20, 'keywords': ['TVB', '翡翠', '凤凰', '中天', '东森', 'TVBS']},
                {'name': '国际频道', 'priority': 25, 'keywords': ['HBO', 'CNN', 'BBC', 'FOX', 'ABC', 'NBC', 'CBS']}
            ],
            'channel_types': {
                '卫视': ['卫视'],
                '电影': ['电影', '影院', '剧场'],
                '体育': ['体育', '赛事'],
                '新闻': ['新闻', '资讯'],
                '综艺': ['综艺', '娱乐'],
                '纪实': ['纪实', '纪录']
            },
            'geography': {
                'continents': [
                    {
                        'name': 'Asia',
                        'countries': [
                            {
                                'name': '中国大陆',
                                'code': 'CN',
                                'keywords': [],
                                'provinces': [
                                    {'name': '北京', 'keywords': ['北京', 'BTV', '京']},
                                    {'name': '上海', 'keywords': ['上海', '东方', 'SMG', '沪']},
                                    {'name': '广东', 'keywords': ['广东', '大湾区', '珠江', '南方', 'TVS', '粤']},
                                    {'name': '湖南', 'keywords': ['湖南', '金鹰', '芒果', '湘']},
                                    {'name': '浙江', 'keywords': ['浙江', 'ZTV', '浙']},
                                    {'name': '江苏', 'keywords': ['江苏', 'JSBC', '苏']},
                                    {'name': '四川', 'keywords': ['四川', 'SCTV', '川', '蜀']},
                                    {'name': '重庆', 'keywords': ['重庆', 'CQTV', '渝']}
                                ],
                                'regions': [
                                    {'name': '香港', 'code': 'HK', 'keywords': ['香港', 'TVB', '翡翠', '凤凰', 'ViuTV', 'HK']},
                                    {'name': '澳门', 'code': 'MO', 'keywords': ['澳门', '澳亚', '澳广视', 'MO']},
                                    {'name': '台湾', 'code': 'TW', 'keywords': ['台湾', '臺灣', '中天', '东森', 'TVBS', '台视', '华视', '民视', 'TW']}
                                ]
                            }
                        ]
                    }
                ]
            }
        }
        
        if not os.path.exists(self.rules_path):
            # 创建默认规则文件
            os.makedirs(os.path.dirname(self.rules_path), exist_ok=True)
            with open(self.rules_path, 'w', encoding='utf-8') as f:
                yaml.dump(default_rules, f, allow_unicode=True, default_flow_style=False)
            return default_rules
        
        try:
            with open(self.rules_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or default_rules
        except Exception as e:
            logging.error(f"加载频道规则文件失败: {e}, 使用默认规则")
            return default_rules
    
    def get_category_rules(self) -> List[Dict]:
        """获取分类规则"""
        return self.rules.get('categories', [])
    
    def get_channel_type_rules(self) -> Dict[str, List[str]]:
        """获取频道类型规则"""
        return self.rules.get('channel_types', {})
    
    def get_geography_rules(self) -> Dict:
        """获取地理规则"""
        return self.rules.get('geography', {})

class Logger:
    """日志管理类"""
    
    def __init__(self, config: Dict):
        self.setup_logging(config)
    
    def setup_logging(self, config: Dict):
        """配置日志系统"""
        log_level = getattr(logging, config['level'].upper(), logging.INFO)
        
        # 创建日志目录（如果不存在）
        log_dir = os.path.dirname(config['file'])
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # 清空日志文件（如果配置了）
        if config.get('clear_on_startup', False) and os.path.exists(config['file']):
            try:
                os.remove(config['file'])
            except Exception as e:
                print(f"无法清空日志文件: {e}")
        
        # 创建根日志记录器
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        
        # 清除现有处理器
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # 创建格式化器
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # 文件处理器
        file_handler = logging.handlers.RotatingFileHandler(
            config['file'],
            maxBytes=config['max_size'],
            backupCount=config['backup_count']
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        
        # 控制台处理器（如果启用）
        if config.get('enable_console', True):
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("日志系统初始化完成")

class ChannelDB:
    """SQLite数据库管理类 - 增强版"""
    
    def __init__(self, db_config: Dict):
        self.db_config = db_config
        self.connection_pool = []
        self.init_db()
    
    def get_connection(self):
        """从连接池获取数据库连接"""
        with _db_lock:
            if not self.connection_pool:
                # 创建新的连接
                try:
                    # 确保数据库目录存在
                    db_dir = os.path.dirname(self.db_config['path'])
                    if not os.path.exists(db_dir):
                        os.makedirs(db_dir)
                    
                    conn = sqlite3.connect(
                        self.db_config['path'],
                        check_same_thread=False,
                        timeout=30
                    )
                    conn.row_factory = sqlite3.Row
                    return conn
                except sqlite3.Error as e:
                    logging.error(f"数据库连接失败: {e}")
                    raise
            
            # 从连接池获取连接
            return self.connection_pool.pop()
    
    def return_connection(self, conn):
        """将连接返回到连接池"""
        with _db_lock:
            if len(self.connection_pool) < self.db_config['connection_pool_size']:
                self.connection_pool.append(conn)
            else:
                conn.close()
    
    def init_db(self):
        """初始化数据库表结构 (SQLite版本) - 增强版"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 创建所有表 (使用SQLite语法)
            cursor.executescript("""
                -- 原始源表
                CREATE TABLE IF NOT EXISTS raw_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    logo_url TEXT,
                    category TEXT,
                    source_type TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    language TEXT DEFAULT 'zh',
                    country TEXT DEFAULT 'CN',
                    region TEXT,
                    resolution TEXT,
                    bitrate INTEGER,
                    is_hd BOOLEAN DEFAULT FALSE,
                    is_4k BOOLEAN DEFAULT FALSE,
                    user_agent TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(url, source_path)
                );
                
                -- 有效源表
                CREATE TABLE IF NOT EXISTS valid_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_source_id INTEGER NOT NULL,
                    response_time INTEGER,
                    download_speed REAL,
                    bitrate INTEGER,
                    status TEXT NOT NULL,
                    is_qualified BOOLEAN DEFAULT FALSE,
                    last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(raw_source_id),
                    FOREIGN KEY (raw_source_id) REFERENCES raw_sources (id) ON DELETE CASCADE
                );
                
                -- 频道信息表
                CREATE TABLE IF NOT EXISTS channel_info (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    clean_name TEXT NOT NULL,
                    channel_type TEXT,
                    province TEXT,
                    city TEXT,
                    language TEXT,
                    continent TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- 创建索引以提高查询性能
                CREATE INDEX IF NOT EXISTS idx_raw_sources_url ON raw_sources(url);
                CREATE INDEX IF NOT EXISTS idx_raw_sources_source_path ON raw_sources(source_path);
                CREATE INDEX IF NOT EXISTS idx_valid_sources_status ON valid_sources(status);
                CREATE INDEX IF NOT EXISTS idx_valid_sources_is_qualified ON valid_sources(is_qualified);
                CREATE INDEX IF NOT EXISTS idx_valid_sources_last_check ON valid_sources(last_check);
                CREATE INDEX IF NOT EXISTS idx_channel_info_name ON channel_info(name);
            """)
            
            conn.commit()
            logging.info("SQLite数据库初始化成功")
        except Exception as e:
            logging.error(f"数据库初始化失败: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                self.return_connection(conn)
    
    def clear_raw_sources(self):
        """清空原始源表"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM raw_sources")
            conn.commit()
            logging.info("已清空原始源表")
        except Exception as e:
            logging.error(f"清空原始源表失败: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                self.return_connection(conn)
    
    def add_raw_source(self, source_data: Dict) -> int:
        """添加原始源到数据库"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO raw_sources 
                (name, url, logo_url, category, source_type, source_path, language, country, region, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_data['name'],
                source_data['url'],
                source_data.get('logo'),
                source_data.get('category'),
                source_data['source_type'],
                source_data['source_path'],
                source_data.get('language', 'zh'),
                source_data.get('country', 'CN'),
                source_data.get('region'),
                source_data.get('user_agent')
            ))
            
            # 获取最后插入的ID
            if cursor.lastrowid:
                source_id = cursor.lastrowid
            else:
                # 如果插入被忽略（已存在），则查询现有ID
                cursor.execute("""
                    SELECT id FROM raw_sources 
                    WHERE url = ? AND source_path = ?
                """, (source_data['url'], source_data['source_path']))
                result = cursor.fetchone()
                source_id = result['id'] if result else -1
            
            conn.commit()
            return source_id
        except Exception as e:
            logging.error(f"添加原始源失败: {e}")
            if conn:
                conn.rollback()
            return -1
        finally:
            if conn:
                self.return_connection(conn)
    
    def add_valid_source(self, raw_source_id: int, status_data: Dict):
        """添加有效源到数据库"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO valid_sources 
                (raw_source_id, response_time, download_speed, bitrate, status, is_qualified)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                raw_source_id,
                status_data.get('response_time'),
                status_data.get('download_speed'),
                status_data.get('bitrate'),
                status_data['status'],
                status_data.get('is_qualified', False)
            ))
            
            conn.commit()
        except Exception as e:
            logging.error(f"添加有效源失败: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                self.return_connection(conn)
    
    def update_source_metadata(self, source_id: int, metadata: Dict):
        """更新源元数据信息"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE raw_sources SET 
                resolution = ?, bitrate = ?, is_hd = ?, is_4k = ?
                WHERE id = ?
            """, (
                metadata.get('resolution'),
                metadata.get('bitrate'),
                metadata.get('is_hd', False),
                metadata.get('is_4k', False),
                source_id
            ))
            
            conn.commit()
        except Exception as e:
            logging.error(f"更新源元数据失败: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                self.return_connection(conn)
    
    def add_channel_info(self, channel_data: Dict):
        """添加频道信息到数据库"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO channel_info 
                (name, clean_name, channel_type, province, city, language, continent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                channel_data['name'],
                channel_data['clean_name'],
                channel_data.get('channel_type'),
                channel_data.get('province'),
                channel_data.get('city'),
                channel_data.get('language'),
                channel_data.get('continent')
            ))
            
            conn.commit()
        except Exception as e:
            logging.error(f"添加频道信息失败: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                self.return_connection(conn)
    
    def get_valid_sources(self) -> List[Dict]:
        """获取所有有效的源"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT rs.*, vs.status, vs.response_time, vs.download_speed, 
                       vs.last_check, vs.is_qualified, ci.channel_type, ci.province, ci.city, ci.language, ci.continent
                FROM raw_sources rs
                JOIN valid_sources vs ON rs.id = vs.raw_source_id
                LEFT JOIN channel_info ci ON rs.name = ci.name
                WHERE vs.status = 'success'
                ORDER BY ci.continent, ci.province, ci.city, rs.name, vs.response_time
            """)
            
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"获取有效源失败: {e}")
            return []
        finally:
            if conn:
                self.return_connection(conn)
    
    def get_qualified_sources(self) -> List[Dict]:
        """获取所有合格的源"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT rs.*, vs.status, vs.response_time, vs.download_speed, 
                       vs.last_check, vs.is_qualified, ci.channel_type, ci.province, ci.city, ci.language, ci.continent
                FROM raw_sources rs
                JOIN valid_sources vs ON rs.id = vs.raw_source_id
                LEFT JOIN channel_info ci ON rs.name = ci.name
                WHERE vs.status = 'success' AND vs.is_qualified = 1
                ORDER BY ci.continent, ci.province, ci.city, rs.name, vs.response_time
            """)
            
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"获取合格源失败: {e}")
            return []
        finally:
            if conn:
                self.return_connection(conn)
    
    def get_all_valid_sources(self) -> List[Dict]:
        """获取所有有效的源（不区分是否合格）"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT rs.*, vs.status, vs.response_time, vs.download_speed, 
                       vs.last_check, vs.is_qualified, ci.channel_type, ci.province, ci.city, ci.language, ci.continent
                FROM raw_sources rs
                JOIN valid_sources vs ON rs.id = vs.raw_source_id
                LEFT JOIN channel_info ci ON rs.name = ci.name
                WHERE vs.status = 'success'
                ORDER BY ci.continent, ci.province, ci.city, rs.name, vs.response_time
            """)
            
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"获取有效源失败: {e}")
            return []
        finally:
            if conn:
                self.return_connection(conn)
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 总体统计
            cursor.execute("""
                SELECT COUNT(*) as total, 
                       (SELECT COUNT(*) FROM valid_sources WHERE status = 'success') as working,
                       (SELECT COUNT(*) FROM valid_sources WHERE status = 'success' AND is_qualified = 1) as qualified
                FROM raw_sources
            """)
            total_row = cursor.fetchone()
            total_stats = {'total': total_row[0], 'working': total_row[1], 'qualified': total_row[2]}
            
            # 按来源类型统计
            cursor.execute("""
                SELECT source_type, COUNT(*) as total,
                       SUM(CASE WHEN vs.status = 'success' THEN 1 ELSE 0 END) as working,
                       SUM(CASE WHEN vs.status = 'success' AND vs.is_qualified = 1 THEN 1 ELSE 0 END) as qualified
                FROM raw_sources rs
                LEFT JOIN valid_sources vs ON rs.id = vs.raw_source_id
                GROUP BY source_type
            """)
            source_stats = {
                row[0]: {'total': row[1], 'working': row[2], 'qualified': row[3]} 
                for row in cursor.fetchall()
            }
            
            # 按分类统计
            cursor.execute("""
                SELECT category, COUNT(*) as total,
                       SUM(CASE WHEN vs.status = 'success' THEN 1 ELSE 0 END) as working,
                       SUM(CASE WHEN vs.status = 'success' AND vs.is_qualified = 1 THEN 1 ELSE 0 END) as qualified
                FROM raw_sources rs
                LEFT JOIN valid_sources vs ON rs.id = vs.raw_source_id
                GROUP BY category
            """)
            category_stats = {
                row[0]: {'total': row[1], 'working': row[2], 'qualified': row[3]} 
                for row in cursor.fetchall()
            }
            
            return {
                'total': total_stats,
                'sources': source_stats,
                'categories': category_stats
            }
        except Exception as e:
            logging.error(f"获取统计信息失败: {e}")
            return {}
        finally:
            if conn:
                self.return_connection(conn)
    
    def get_source_statistics(self) -> Dict:
        """获取源质量统计信息"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 获取有效源的统计信息
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    AVG(vs.response_time) as avg_latency,
                    AVG(vs.download_speed) as avg_speed,
                    AVG(rs.bitrate) as avg_bitrate,
                    (SELECT response_time FROM valid_sources WHERE status = 'success' ORDER BY response_time LIMIT 1 OFFSET (SELECT COUNT(*) FROM valid_sources WHERE status = 'success') / 2) as median_latency,
                    (SELECT download_speed FROM valid_sources WHERE status = 'success' ORDER BY download_speed LIMIT 1 OFFSET (SELECT COUNT(*) FROM valid_sources WHERE status = 'success') / 2) as median_speed,
                    (SELECT rs.bitrate FROM raw_sources rs JOIN valid_sources vs ON rs.id = vs.raw_source_id WHERE vs.status = 'success' AND rs.bitrate IS NOT NULL ORDER BY rs.bitrate LIMIT 1 OFFSET (SELECT COUNT(*) FROM valid_sources WHERE status = 'success') / 2) as median_bitrate,
                    (SELECT rs.resolution FROM raw_sources rs JOIN valid_sources vs ON rs.id = vs.raw_source_id WHERE vs.status = 'success' AND rs.resolution IS NOT NULL GROUP BY rs.resolution ORDER BY COUNT(*) DESC LIMIT 1) as common_resolution
                FROM valid_sources vs
                JOIN raw_sources rs ON vs.raw_source_id = rs.id
                WHERE vs.status = 'success'
            """)
            
            stats_row = cursor.fetchone()
            
            # 获取分辨率分布
            cursor.execute("""
                SELECT resolution, COUNT(*) 
                FROM raw_sources rs
                JOIN valid_sources vs ON rs.id = vs.raw_source_id
                WHERE vs.status = 'success' AND resolution IS NOT NULL
                GROUP BY resolution
                ORDER BY COUNT(*) DESC
            """)
            
            resolution_dist = {row[0]: row[1] for row in cursor.fetchall()}
            
            # 获取延迟分布
            cursor.execute("""
                SELECT 
                    CASE 
                        WHEN response_time < 500 THEN '0-500ms'
                        WHEN response_time < 1000 THEN '500-1000ms'
                        WHEN response_time < 2000 THEN '1000-2000ms'
                        WHEN response_time < 3000 THEN '2000-3000ms'
                        ELSE '3000ms+'
                    END as latency_range,
                    COUNT(*)
                FROM valid_sources
                WHERE status = 'success' AND response_time IS NOT NULL
                GROUP BY latency_range
                ORDER BY latency_range
            """)
            
            latency_dist = {row[0]: row[1] for row in cursor.fetchall()}
            
            return {
                'total': stats_row[0],
                'avg_latency': float(stats_row[1]) if stats_row[1] else 0,
                'avg_speed': float(stats_row[2]) if stats_row[2] else 0,
                'avg_bitrate': float(stats_row[3]) if stats_row[3] else 0,
                'median_latency': float(stats_row[4]) if stats_row[4] else 0,
                'median_speed': float(stats_row[5]) if stats_row[5] else 0,
                'median_bitrate': float(stats_row[6]) if stats_row[6] else 0,
                'common_resolution': stats_row[7],
                'resolution_distribution': resolution_dist,
                'latency_distribution': latency_dist
            }
        except Exception as e:
            logging.error(f"获取源统计信息失败: {e}")
            return {}
        finally:
            if conn:
                self.return_connection(conn)
    
    def cleanup_old_data(self, days: int = 30):
        """清理旧数据"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 删除无效的原始源（没有对应的有效源）
            cursor.execute("""
                DELETE FROM raw_sources 
                WHERE id NOT IN (SELECT raw_source_id FROM valid_sources)
                AND created_at < datetime('now', ?)
            """, (f'-{days} days',))
            
            deleted_count = cursor.rowcount
            conn.commit()
            logging.info(f"已清理 {deleted_count} 条旧数据")
        except Exception as e:
            logging.error(f"清理旧数据失败: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                self.return_connection(conn)
    
    def close_all_connections(self):
        """关闭所有数据库连接"""
        with _db_lock:
            for conn in self.connection_pool:
                conn.close()
            self.connection_pool.clear()

# 已移除AllInOneRunner类

class SourceManager:
    """源管理类 - 增强版"""
    
    def __init__(self, config: Config, logger: logging.Logger, channel_rules: ChannelRules):
        self.config = config
        self.logger = logger
        self.channel_rules = channel_rules
        self.network_config = config.get_network_config()
        self.github_config = config.get_github_config()
        self.user_agents = config.get_user_agents()
        self.ua_enabled = config.is_ua_enabled()
        self.online_dir = "/config/online"
        
        # 确保在线目录存在
        os.makedirs(self.online_dir, exist_ok=True)
    
    async def create_session(self, use_proxy: bool = False) -> aiohttp.ClientSession:
        """创建HTTP会话，支持代理"""
        connector = None
        
        # 设置地址族
        family = socket.AF_INET
        if self.network_config['ipv6_enabled']:
            family = socket.AF_UNSPEC
        
        if use_proxy and self.network_config['proxy_enabled']:
            proxy_type = self.network_config['proxy_type'].lower()
            proxy_host = self.network_config['proxy_host']
            proxy_port = self.network_config['proxy_port']
            proxy_username = self.network_config['proxy_username']
            proxy_password = self.network_config['proxy_password']
            
            if proxy_type in ['socks5', 'socks5h']:
                if proxy_username and proxy_password:
                    proxy_url = f"{proxy_type}://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"
                else:
                    proxy_url = f"{proxy_type}://{proxy_host}:{proxy_port}"
                
                connector = aiohttp_socks.ProxyConnector.from_url(
                    proxy_url, 
                    family=family,
                    ssl=False
                )
            else:
                if proxy_username and proxy_password:
                    proxy_auth = aiohttp.BasicAuth(proxy_username, proxy_password)
                else:
                    proxy_auth = None
                    
                connector = aiohttp.TCPConnector(
                    proxy=f"{proxy_type}://{proxy_host}:{proxy_port}",
                    proxy_auth=proxy_auth,
                    family=family,
                    ssl=False
                )
        else:
            connector = aiohttp.TCPConnector(family=family, ssl=False)
        
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        return aiohttp.ClientSession(connector=connector, timeout=timeout)
    
    async def download_all_sources(self) -> List[str]:
        """下载所有源文件"""
        downloaded_files = []
        
        # 获取GitHub源文件列表
        github_files = await self.get_github_files()
        
        # 获取在线URL列表
        online_urls = self.config.get_sources()['online_urls']
        
        # 合并所有URL
        all_urls = github_files + online_urls
        
        self.logger.info(f"开始下载 {len(all_urls)} 个源文件")
        
        # 并发下载所有文件
        tasks = []
        for url in all_urls:
            tasks.append(self.download_with_retry(url))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"下载失败 {all_urls[i]}: {result}")
            elif result:
                downloaded_files.append(result)
        
        self.logger.info(f"成功下载 {len(downloaded_files)} 个源文件")
        return downloaded_files
    
    async def get_github_files(self) -> List[str]:
        """获取GitHub仓库中的所有源文件"""
        github_sources = self.config.get_sources()['github_sources']
        all_files = []
        
        for repo_path in github_sources:
            try:
                files = await self.get_github_repo_files(repo_path)
                all_files.extend(files)
            except Exception as e:
                self.logger.error(f"获取GitHub文件列表失败 {repo_path}: {e}")
        
        return all_files
    
    async def get_github_repo_files(self, repo_path: str) -> List[str]:
        """获取GitHub仓库中的文件列表"""
        parts = repo_path.split('/')
        if len(parts) < 2:
            return []
        
        owner, repo = parts[0], parts[1]
        path = '/'.join(parts[2:]) if len(parts) > 2 else ""
        
        api_url = f"{self.github_config['api_url']}/repos/{owner}/{repo}/contents/{path}"
        
        headers = {}
        if self.github_config['api_token']:
            headers['Authorization'] = f"token {self.github_config['api_token']}"
        
        async with await self.create_session() as session:
            try:
                async with session.get(api_url, headers=headers) as response:
                    if response.status == 200:
                        items = await response.json()
                        files = []
                        
                        for item in items:
                            if item['type'] == 'file' and item['name'].endswith(('.m3u', '.m3u8', '.txt')):
                                files.append(item['download_url'])
                            elif item['type'] == 'dir':
                                sub_files = await self.get_github_repo_files(f"{repo_path}/{item['name']}")
                                files.extend(sub_files)
                        
                        return files
                    else:
                        self.logger.error(f"GitHub API错误 {response.status}: {api_url}")
                        return []
            except Exception as e:
                self.logger.error(f"GitHub API请求失败: {e}")
                return []
    
    async def download_with_retry(self, url: str, max_retries: int = None) -> Optional[str]:
        """带重试机制的下载"""
        if max_retries is None:
            max_retries = self.network_config['retry_times']
        
        strategies = [
            {'type': 'direct', 'use_proxy': False, 'use_mirror': False},
            {'type': 'proxy', 'use_proxy': True, 'use_mirror': False},
            {'type': 'mirror', 'use_proxy': False, 'use_mirror': True}
        ]
        
        for attempt in range(max_retries):
            for strategy in strategies:
                try:
                    result = await self.download_file(url, strategy)
                    if result:
                        self.logger.info(f"下载成功 [{strategy['type']}]: {url}")
                        return result
                    else:
                        self.logger.warning(f"下载返回空 [{strategy['type']}] (尝试 {attempt + 1}/{max_retries}): {url}")
                except Exception as e:
                    self.logger.warning(f"下载失败 [{strategy['type']}] (尝试 {attempt + 1}/{max_retries}): {url} - {e}")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(self.network_config['retry_delay'] * (2 ** attempt))  # 指数退避
        
        self.logger.error(f"下载最终失败: {url}")
        return None
    
    async def download_file(self, url: str, strategy: Dict) -> Optional[str]:
        """下载单个文件"""
        download_url = url
        
        # 应用镜像策略
        if strategy['use_mirror'] and self.network_config['github_mirror_enabled']:
            mirror_base = self.network_config['github_mirror_url'].rstrip('/')
            if 'raw.githubusercontent.com' in url:
                path = url.split('raw.githubusercontent.com')[1]
                download_url = f"{mirror_base}/https://raw.githubusercontent.com{path}"
            elif 'github.com' in url and '/blob/' in url:
                path = url.split('github.com')[1]
                download_url = f"{mirror_base}/https://github.com{path}".replace('/blob/', '/raw/')
            elif 'github.com' in url:
                path = url.split('github.com')[1]
                download_url = f"{mirror_base}/https://github.com{path}"
        
        # 创建会话
        session = await self.create_session(strategy['use_proxy'])
        
        try:
            self.logger.info(f"尝试下载 [{strategy['type']}]: {download_url}")
            async with session.get(download_url) as response:
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
            self.logger.error(f"下载失败 [{strategy['type']}]: {download_url} - {e}")
            raise
        finally:
            await session.close()
    
    def get_filename_from_url(self, url: str) -> str:
        """从URL提取文件名"""
        # 移除查询参数
        clean_url = url.split('?')[0]
        
        # 获取最后一部分作为文件名
        filename = clean_url.split('/')[-1]
        
        # 确保文件名有效
        if not filename or '.' not in filename:
            filename = f"source_{hash(url)}.txt"
        
        return filename
    
    def parse_all_files(self, db: ChannelDB) -> List[Dict]:
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
        
        # 添加到数据库
        source_ids = []
        for source in all_sources:
            source_id = db.add_raw_source(source)
            if source_id > 0:
                source_ids.append(source_id)
        
        self.logger.info(f"成功解析 {len(source_ids)} 个源")
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
        if file_path.startswith(self.online_dir):
            source_type = "online"
            source_path = file_path.replace(self.online_dir + "/", "")
        else:
            source_type = "local"
            source_path = file_path
        
        # 检查是否有配置的UA
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
            # 如果所有编码都失败，尝试使用二进制读取并忽略错误
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
                        # 尝试修复编码问题
                        try:
                            name = name.encode('latin1').decode('utf-8')
                        except:
                            pass
                        logo = self.extract_logo(extinf)
                        group = self.extract_group(extinf)
                        
                        # 检查URL是否包含UA信息
                        url_parts = url.split('|')
                        stream_url = url_parts[0]
                        url_user_agent = user_agent
                        
                        # 如果URL中有UA信息，则使用URL中的UA
                        if len(url_parts) > 1 and 'User-Agent=' in url_parts[1]:
                            url_user_agent = url_parts[1].replace('User-Agent=', '')
                        
                        # 提取频道信息
                        channel_info = self.extract_channel_info(name)
                        
                        sources.append({
                            'name': name,
                            'url': stream_url,
                            'logo': logo,
                            'source_type': source_type,
                            'source_path': source_path,
                            'user_agent': url_user_agent,
                            'group': group,
                            'category': self.determine_category(name),
                            'country': channel_info.get('country', 'CN'),
                            'region': channel_info.get('region'),
                            'language': channel_info.get('language', 'zh')
                        })
            else:
                if line and not line.startswith('#') and self.is_valid_url(line):
                    name = f"Channel from {os.path.basename(file_path)}"
                    # 尝试修复编码问题
                    try:
                        name = name.encode('latin1').decode('utf-8')
                    except:
                        pass
                    channel_info = self.extract_channel_info(name)
                    
                    # 检查URL是否包含UA信息
                    url_parts = line.split('|')
                    stream_url = url_parts[0]
                    url_user_agent = user_agent
                    
                    # 如果URL中有UA信息，则使用URL中的UA
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
                        'category': self.determine_category(name),
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
            # 移除可能的UA部分
            clean_url = url.split('|')[0]
            result = urlparse(clean_url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False
    
    def extract_channel_info(self, channel_name: str) -> Dict:
        """使用外部规则提取频道信息"""
        info = {
            'country': 'CN',
            'region': None,
            'language': 'zh',
            'channel_type': None,
            'province': None,
            'city': None,
            'continent': 'Asia'
        }
        
        # 清理频道名称
        clean_name = re.sub(r'[^\w\u4e00-\u9fff]', '', channel_name.upper())
        
        # 使用YAML规则识别国家/地区
        geography_rules = self.channel_rules.get_geography_rules()
        
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
        channel_type_rules = self.channel_rules.get_channel_type_rules()
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
        """使用外部规则根据频道名称判断分类"""
        channel_name_upper = channel_name.upper()
        category_rules = self.channel_rules.get_category_rules()
        
        # 按优先级排序规则
        sorted_rules = sorted(category_rules, key=lambda x: x.get('priority', 100))
        
        for rule in sorted_rules:
            for keyword in rule.get('keywords', []):
                if keyword.upper() in channel_name_upper:
                    return rule.get('name', '其他频道')
        
        return '其他频道'

class StreamTester:
    """流媒体测试类 - 增强版"""
    
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.testing_params = config.get_testing_params()
        self.filter_params = config.get_filter_params()
    
    def test_all_sources(self, db: ChannelDB) -> List[Dict]:
        """测试所有源的有效性"""
        self.cleanup_cache()
        
        # 获取所有需要测试的源
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, url, user_agent FROM raw_sources")
        sources = [dict(row) for row in cursor.fetchall()]
        db.return_connection(conn)
        
        total = len(sources)
        self.logger.info(f"开始测试 {total} 个流媒体源")
        
        # 根据系统资源动态调整并发线程数
        max_workers = min(
            self.testing_params['concurrent_threads'],
            multiprocessing.cpu_count() * 2,
            self.testing_params['max_workers']  # 最大不超过配置的线程数
        )
        
        # 创建进度条
        pbar = tqdm.tqdm(total=total, desc="测试流媒体源", unit="源")
        
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
                    
                    # 更新数据库
                    db.add_valid_source(source['id'], result)
                    
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
            import subprocess
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

class M3UGenerator:
    """M3U文件生成器 - 增强版"""
    
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.output_params = config.get_output_params()
        self.filter_params = config.get_filter_params()
        self.ua_position = config.get_ua_position()  # 获取UA位置配置
        self.ua_enabled = config.is_ua_enabled()  # 获取UA启用状态
    
    def generate_m3u(self, sources: List[Dict]) -> str:
        """生成M3U文件内容"""
        output_lines = ["#EXTM3U"]
        
        # 获取过滤参数
        filter_params = self.filter_params
        
        # 根据配置决定是否过滤源
        if self.output_params['enable_filter']:
            # 过滤源
            filtered_sources = self.filter_sources(sources, filter_params)
            self.logger.info(f"过滤功能已启用，从 {len(sources)} 个源中筛选出 {len(filtered_sources)} 个合格源")
        else:
            # 不启用过滤，使用所有有效源
            filtered_sources = [s for s in sources if s.get('status') == 'success']
            self.logger.info(f"过滤功能已禁用，使用所有 {len(filtered_sources)} 个有效源")
        
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
                        # UA作为URL参数
                        url = f"{url}|User-Agent={source['user_agent']}"
                    # 如果ua_position为'extinf'，UA已经在build_extinf中作为属性添加
                
                output_lines.append(url)
        
        return "\n".join(output_lines)
    
    def generate_txt(self, sources: List[Dict]) -> str:
        """生成TXT文件内容"""
        output_lines = []
        
        # 获取过滤参数
        filter_params = self.filter_params
        
        # 根据配置决定是否过滤源
        if self.output_params['enable_filter']:
            # 过滤源
            filtered_sources = self.filter_sources(sources, filter_params)
            self.logger.info(f"TXT过滤功能已启用，从 {len(sources)} 个源中筛选出 {len(filtered_sources)} 个合格源")
        else:
            # 不启用过滤，使用所有有效源
            filtered_sources = [s for s in sources if s.get('status') == 'success']
            self.logger.info(f"TXT过滤功能已禁用，使用所有 {len(filtered_sources)} 个有效源")
        
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
                        # UA作为URL参数
                        channel_line = f"{source['name']},{source['url']}|User-Agent={source['user_agent']}"
                    else:
                        # UA作为单独的参数
                        channel_line = f"{source['name']},{source['url']}#User-Agent={source['user_agent']}"
                
                output_lines.append(channel_line)
            
            # 添加空行分隔不同分组
            output_lines.append("")
        
        return "\n".join(output_lines)
    
    def filter_sources(self, sources: List[Dict], filter_params: Dict) -> List[Dict]:
        """根据条件过滤源"""
        filtered = []
        for source in sources:
            # 检查是否必须包含失败的源
            if not self.output_params['include_failed'] and source.get('status') != 'success':
                continue
            
            # 检查延迟
            response_time = source.get('response_time', 9999)
            if response_time > filter_params['max_latency']:
                continue
            
            # 检查分辨率（根据筛选模式）
            min_resolution = filter_params['min_resolution']
            max_resolution = filter_params['max_resolution']
            resolution_filter_mode = filter_params.get('resolution_filter_mode', 'range')
            
            if min_resolution or max_resolution:
                resolution = source.get('resolution', '')
                
                if resolution_filter_mode == 'range':
                    # 区间模式：必须同时满足最小和最大分辨率
                    if min_resolution and not self.is_resolution_meet_min(resolution, min_resolution):
                        continue
                    if max_resolution and not self.is_resolution_meet_max(resolution, max_resolution):
                        continue
                elif resolution_filter_mode == 'min_only':
                    # 仅最低：只检查最低分辨率
                    if min_resolution and not self.is_resolution_meet_min(resolution, min_resolution):
                        continue
                elif resolution_filter_mode == 'max_only':
                    # 仅最高：只检查最高分辨率
                    if max_resolution and not self.is_resolution_meet_max(resolution, max_resolution):
                        continue
            
            # 检查比特率
            bitrate = source.get('bitrate', 0)
            if bitrate > 0 and bitrate < filter_params['min_bitrate']:
                continue
            
            # 检查HD/4K要求
            if filter_params['must_hd'] and not source.get('is_hd', False):
                continue
                
            if filter_params['must_4k'] and not source.get('is_4k', False):
                continue
            
            # 检查速度要求
            speed = source.get('download_speed', 0)
            if speed > 0 and speed < filter_params['min_speed']:
                continue
            
            filtered.append(source)
        
        return filtered
    
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
    
    def group_and_sort_sources(self, sources: List[Dict]) -> Dict[str, List[Dict]]:
        """
        根据配置对源进行分组和排序
        对于同一个频道存在多个源的，选择速度最快的前N个源
        """
        group_by = self.output_params['group_by']
        max_sources_per_channel = self.output_params['max_sources_per_channel']
        grouped = {}
        
        # 如果关闭筛选，则每个频道最大源数量增加到1000，并且不进行分辨率过滤
        if not self.output_params['enable_filter']:
            max_sources_per_channel = 4
        
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
            
            if not channel_sources:  # 如果没有符合条件的源，跳过该频道
                continue
                
            # 按照速度（降序）和延迟（升序）排序
            channel_sources.sort(key=lambda x: (
                -x.get('download_speed', 0),  # 速度越高越好
                x.get('response_time', 9999)  # 延迟越低越好
            ))
            
            # 只保留前N个源
            processed_sources.extend(channel_sources[:max_sources_per_channel])
        
        # 第三步：按配置的group_by对处理后的源进行分组
        for source in processed_sources:
            # 确定分组键
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
            # 按地区、频道类型和名称排序
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
        elif source.get('logo_url'):
            parts.append(f'tvg-logo="{source["logo_url"]}"')
        
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
    
    def save_m3u_to_file(self, content: str, filename: str = None):
        """保存M3U内容到文件"""
        output_dir = self.output_params['output_dir']
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        if filename is None:
            filename = self.output_params['filename']
        filepath = os.path.join(output_dir, filename)
        
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 写入文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            self.logger.info(f"M3U文件已生成: {filepath}，包含 {content.count('#EXTINF:')} 个频道")
        except Exception as e:
            self.logger.error(f"保存M3U文件失败: {e}")
            raise
    
    def save_txt_to_file(self, content: str, filename: str):
        """保存TXT内容到文件"""
        output_dir = self.output_params['output_dir']
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        filepath = os.path.join(output_dir, filename)
        
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 写入文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 计算频道数量（减去分组行和空行）
            channel_count = len([line for line in content.split('\n') if line and not line.startswith('#')])
            self.logger.info(f"TXT文件已生成: {filepath}，包含 {channel_count} 个频道")
        except Exception as e:
            self.logger.error(f"保存TXT文件失败: {e}")
            raise

class LiveSourceManagerAPI:
    """直播源管理器API类"""
    
    def __init__(self, config: Config, db: ChannelDB, logger: logging.Logger):
        self.config = config
        self.db = db
        self.logger = logger
        self.api_config = config.get_api_config()
    
    def start(self):
        """启动API服务器"""
        if not self.api_config['enabled']:
            self.logger.info("API功能未启用，跳过启动")
            return
        
        try:
            from flask import Flask, jsonify, request
            app = Flask(__name__)
            
            # 添加认证中间件（如果需要）
            if self.api_config['auth_required']:
                from flask_httpauth import HTTPBasicAuth
                auth = HTTPBasicAuth()
                
                @auth.verify_password
                def verify_password(username, password):
                    return username == self.api_config['username'] and password == self.api_config['password']
            else:
                # 创建一个空的认证装饰器
                def no_auth(f):
                    return f
                auth = no_auth
            
            @app.route('/api/sources', methods=['GET'])
            @auth
            def get_sources():
                """获取所有有效的源"""
                try:
                    sources = self.db.get_valid_sources()
                    return jsonify({
                        'status': 'success',
                        'count': len(sources),
                        'sources': sources
                    })
                except Exception as e:
                    return jsonify({
                        'status': 'error',
                        'message': str(e)
                    }), 500
            
            @app.route('/api/sources/qualified', methods=['GET'])
            @auth
            def get_qualified_sources():
                """获取所有合格的源"""
                try:
                    sources = self.db.get_qualified_sources()
                    return jsonify({
                        'status': 'success',
                        'count': len(sources),
                        'sources': sources
                    })
                except Exception as e:
                    return jsonify({
                        'status': 'error',
                        'message': str(e)
                    }), 500
            
            @app.route('/api/stats', methods=['GET'])
            @auth
            def get_stats():
                """获取统计信息"""
                try:
                    stats = self.db.get_stats()
                    return jsonify({
                        'status': 'success',
                        'stats': stats
                    })
                except Exception as e:
                    return jsonify({
                        'status': 'error',
                        'message': str(e)
                    }), 500
            
            @app.route('/api/refresh', methods=['POST'])
            @auth
            def refresh_sources():
                """手动刷新源"""
                try:
                    # 这里可以添加刷新源的逻辑
                    return jsonify({
                        'status': 'success',
                        'message': '刷新任务已启动'
                    })
                except Exception as e:
                    return jsonify({
                        'status': 'error',
                        'message': str(e)
                    }), 500
            
            # 启动Flask应用
            host = self.api_config['host']
            port = self.api_config['port']
            self.logger.info(f"启动API服务器: {host}:{port}")
            app.run(host=host, port=port, threaded=True)
            
        except ImportError:
            self.logger.error("启动API需要安装Flask: pip install flask flask-httpauth")
        except Exception as e:
            self.logger.error(f"启动API服务器失败: {e}")

def check_network_connectivity() -> bool:
    """检查网络连接性"""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        return True
    except OSError:
        return False

def main():
    """主函数 - 增强版"""
    try:
        # 检查网络连接
        if not check_network_connectivity():
            print("网络连接不可用，请检查容器网络配置")
            return
        
        # 初始化配置
        config = Config()
        
        # 初始化日志
        logger_config = config.get_logging_config()
        logger = Logger(logger_config).logger
        
        # 初始化频道规则
        channel_rules = ChannelRules()
        
        # 已移除运行肥羊allinone程序的部分
        
        # 初始化数据库
        db_config = config.get_database_config()
        db = ChannelDB(db_config)
        
        logger.info("开始处理直播源")
        
        # 清空原始源表
        db.clear_raw_sources()
        
        # 下载所有源文件
        manager = SourceManager(config, logger, channel_rules)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        downloaded_files = loop.run_until_complete(manager.download_all_sources())
        
        # 解析所有源文件
        sources = manager.parse_all_files(db)
        
        if not sources:
            logger.warning("未获取到任何直播源，程序将退出")
            return
        
        # 测试所有源
        tester = StreamTester(config, logger)
        try:
            test_results = tester.test_all_sources(db)
        except Exception as e:
            logger.error(f"测试过程中发生严重错误: {e}")
            logger.error(traceback.format_exc())
            test_results = []
        
        # 输出源质量统计信息
        stats = db.get_source_statistics()
        if stats:
            logger.info("===== 源质量统计信息 =====")
            logger.info(f"有效源总数: {stats['total']}")
            logger.info(f"平均延迟: {stats['avg_latency']:.2f}ms, 中位数延迟: {stats['median_latency']:.2f}ms")
            logger.info(f"平均速度: {stats['avg_speed']:.2f}KB/s, 中位数速度: {stats['median_speed']:.2f}KB/s")
            logger.info(f"平均比特率: {stats['avg_bitrate']:.2f}kbps, 中位数比特率: {stats['median_bitrate']:.2f}kbps")
            logger.info(f"最常见分辨率: {stats['common_resolution']}")
            
            logger.info("分辨率分布:")
            for res, count in stats['resolution_distribution'].items():
                logger.info(f"  {res}: {count}个")
            
            logger.info("延迟分布:")
            for latency_range, count in stats['latency_distribution'].items():
                logger.info(f"  {latency_range}: {count}个")
            
            # 提供筛选参数建议
            logger.info("===== 筛选参数建议 =====")
            if stats['median_latency'] > 0:
                suggested_max_latency = min(5000, max(2000, stats['median_latency'] * 2))
                logger.info(f"建议最大延迟: {suggested_max_latency}ms")
            
            if stats['median_speed'] > 0:
                suggested_min_speed = max(100, stats['median_speed'] * 0.5)
                logger.info(f"建议最小速度: {suggested_min_speed:.2f}KB/s")
            
            if stats['median_bitrate'] > 0:
                suggested_min_bitrate = max(200, stats['median_bitrate'] * 0.5)
                logger.info(f"建议最小比特率: {suggested_min_bitrate:.2f}kbps")
            
            # 根据分辨率分布建议最小分辨率
            if stats['resolution_distribution']:
                resolutions = list(stats['resolution_distribution'].keys())
                if any('1080' in res or '1920' in res for res in resolutions):
                    logger.info("建议最小分辨率: 1080p (存在大量高清源)")
                elif any('720' in res for res in resolutions):
                    logger.info("建议最小分辨率: 720p")
                else:
                    logger.info("建议最小分辨率: 480p (高清源较少)")
        
        # 从数据库获取有效源生成M3U和TXT
        valid_sources = db.get_all_valid_sources()
        logger.info(f"获取到 {len(valid_sources)} 个有效源")
        
        if valid_sources:
            generator = M3UGenerator(config, logger)
            
            # 生成所有有效源的M3U文件
            m3u_content = generator.generate_m3u(valid_sources)
            generator.save_m3u_to_file(m3u_content)
            
            # 生成所有有效源的TXT文件
            base_filename = config.get_output_params()['filename'].replace('.m3u', '')
            txt_content = generator.generate_txt(valid_sources)
            generator.save_txt_to_file(txt_content, f"{base_filename}.txt")
        else:
            logger.warning("没有有效源，无法生成M3U和TXT文件")
        
        # 从数据库获取合格源生成M3U和TXT
        qualified_sources = db.get_qualified_sources()
        logger.info(f"获取到 {len(qualified_sources)} 个合格源")
        
        if qualified_sources:
            generator = M3UGenerator(config, logger)
            
            # 生成合格源的M3U文件
            base_filename = config.get_output_params()['filename'].replace('.m3u', '')
            qualified_m3u_content = generator.generate_m3u(qualified_sources)
            generator.save_m3u_to_file(qualified_m3u_content, f"qualified_{base_filename}.m3u")
            
            # 生成合格源的TXT文件
            qualified_txt_content = generator.generate_txt(qualified_sources)
            generator.save_txt_to_file(qualified_txt_content, f"qualified_{base_filename}.txt")
        else:
            logger.warning("没有合格源，无法生成合格源M3U和TXT文件")
        
        # 输出统计信息
        stats = db.get_stats()
        logger.info(f"频道统计: 总计{stats['total']['total']}个, 有效{stats['total']['working']}个, 合格{stats['total']['qualified']}个")
        
        # 按来源类型输出统计
        for source_type, count in stats['sources'].items():
            logger.info(f"{source_type}: {count['working']}/{count['total']} 有效, {count['qualified']} 合格")
        
        # 按分类输出统计
        for category, count in stats['categories'].items():
            if category:  # 排除空分类
                logger.info(f"{category}: {count['working']}/{count['total']} 有效, {count['qualified']} 合格")
        
        # 清理旧数据
        db.cleanup_old_data(db_config['cleanup_days'])
        
        # 启动API服务器
        api = LiveSourceManagerAPI(config, db, logger)
        api.start()
        
        logger.info("任务完成")
        
    except Exception as e:
        logging.error(f"程序执行失败: {e}")
        import traceback
        logging.error(traceback.format_exc())
    finally:
        if 'db' in locals():
            db.close_all_connections()

if __name__ == "__main__":
    main()
