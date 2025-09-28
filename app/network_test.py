#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网络连接测试脚本
增强内容:
- 增加容器内部网络测试
- 增强错误处理和日志记录
- 添加Nginx服务可达性测试
"""

import socket
import requests
import asyncio
import aiohttp
import os
import sys

# 确保可以导入自定义模块
sys.path.insert(0, '/app')
from config_manager import Config, Logger

def test_basic_connectivity():
    """测试基本网络连接"""
    print("=== 基本网络连接测试 ===")
    
    # 测试DNS解析
    test_hosts = ['google.com', 'github.com', 'raw.githubusercontent.com']
    for host in test_hosts:
        try:
            ip = socket.gethostbyname(host)
            print(f"✓ {host} -> {ip}")
        except Exception as e:
            print(f"✗ {host} DNS解析失败: {e}")
    
    # 测试HTTP连接
    test_urls = [
        'http://www.baidu.com',
        'https://www.google.com',
        'https://raw.githubusercontent.com'
    ]
    
    for url in test_urls:
        try:
            response = requests.get(url, timeout=10)
            print(f"✓ {url} - 状态码: {response.status_code}")
        except Exception as e:
            print(f"✗ {url} - 连接失败: {e}")

async def test_async_connectivity():
    """测试异步网络连接"""
    print("\n=== 异步网络连接测试 ===")
    
    async with aiohttp.ClientSession() as session:
        urls = [
            'https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u',
            'https://live.zbds.org/tv/iptv4.m3u'
        ]
        
        for url in urls:
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        print(f"✓ {url} - 异步连接成功")
                    else:
                        print(f"⚠ {url} - 状态码: {response.status}")
            except Exception as e:
                print(f"✗ {url} - 异步连接失败: {e}")

def test_proxy_connection():
    """测试代理连接"""
    print("\n=== 代理连接测试 ===")
    
    config = Config()
    network_config = config.get_network_config()
    
    if network_config['proxy_enabled']:
        print(f"代理配置: {network_config['proxy_type']}://{network_config['proxy_host']}:{network_config['proxy_port']}")
        
        # 测试代理服务器连接
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((network_config['proxy_host'], network_config['proxy_port']))
            sock.close()
            
            if result == 0:
                print("✓ 代理服务器连接成功")
            else:
                print(f"✗ 代理服务器连接失败，错误码: {result}")
        except Exception as e:
            print(f"✗ 代理服务器连接异常: {e}")
    else:
        print("代理未启用")

def test_container_network():
    """测试容器内部网络连通性"""
    print("\n=== 容器内部网络测试 ===")
    
    # 测试本地Nginx服务
    try:
        response = requests.get('http://localhost:12345/health', timeout=5)
        print(f"✓ 本地Nginx服务: 状态码 {response.status_code}")
    except Exception as e:
        print(f"✗ 本地Nginx服务不可达: {e}")
    
    # 测试文件系统
    if os.path.exists('/www/output'):
        print("✓ 输出目录存在")
        # 检查目录权限
        if os.access('/www/output', os.W_OK):
            print("✓ 输出目录可写")
        else:
            print("✗ 输出目录不可写")
    else:
        print("✗ 输出目录不存在")
    
    # 测试配置文件
    config_files = ['/config/config.ini', '/config/channel_rules.yml', '/etc/nginx/nginx.conf']
    for config_file in config_files:
        if os.path.exists(config_file):
            print(f"✓ 配置文件存在: {config_file}")
        else:
            print(f"✗ 配置文件不存在: {config_file}")

def test_nginx_service():
    """测试Nginx服务功能"""
    print("\n=== Nginx服务测试 ===")
    
    test_endpoints = [
        ('http://localhost:12345/health', '健康检查'),
        ('http://localhost:12345/live.m3u', 'M3U文件'),
        ('http://localhost:12345/live.txt', 'TXT文件')
    ]
    
    for url, description in test_endpoints:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"✓ {description}: 状态码 200 ({len(response.content)} 字节)")
            elif response.status_code == 404:
                print(f"⚠ {description}: 文件不存在 (404)")
            else:
                print(f"⚠ {description}: 状态码 {response.status_code}")
        except Exception as e:
            print(f"✗ {description}: 连接失败 - {e}")

if __name__ == "__main__":
    print("开始网络诊断...")
    
    test_basic_connectivity()
    test_proxy_connection()
    test_container_network()
    test_nginx_service()
    
    # 运行异步测试
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_async_connectivity())
    
    print("\n网络诊断完成")