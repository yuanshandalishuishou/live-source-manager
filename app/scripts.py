#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运维诊断/测试脚本模块
合并自: network_test.py, test_http.py
功能不减少，仅合并文件
"""
import socket
import requests
import asyncio
import aiohttp
import os
import sys
import time

sys.path.insert(0, '/app')


# ═══════════════════════════════════════════════════
# 网络连接测试 (原 network_test.py)
# ═══════════════════════════════════════════════════

def test_basic_connectivity():
    """测试基本网络连接"""
    print("=== 基本网络连接测试 ===")
    test_hosts = ['google.com', 'github.com', 'raw.githubusercontent.com']
    for host in test_hosts:
        try:
            ip = socket.gethostbyname(host)
            print(f"\u2713 {host} -> {ip}")
        except Exception as e:
            print(f"\u2717 {host} DNS\u89e3\u6790\u5931\u8d25: {e}")
    test_urls = [
        'http://www.baidu.com',
        'https://www.google.com',
        'https://raw.githubusercontent.com'
    ]
    for url in test_urls:
        try:
            response = requests.get(url, timeout=10)
            print(f"\u2713 {url} - \u72b6\u6001\u7801: {response.status_code}")
        except Exception as e:
            print(f"\u2717 {url} - \u8fde\u63a5\u5931\u8d25: {e}")


async def test_async_connectivity():
    """测试异步网络连接"""
    print("\n=== \u5f02\u6b65\u7f51\u7edc\u8fde\u63a5\u6d4b\u8bd5 ===")
    async with aiohttp.ClientSession() as session:
        urls = [
            'https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u',
            'https://live.zbds.org/tv/iptv4.m3u'
        ]
        for url in urls:
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        print(f"\u2713 {url} - \u5f02\u6b65\u8fde\u63a5\u6210\u529f")
                    else:
                        print(f"\u26a0 {url} - \u72b6\u6001\u7801: {response.status}")
            except Exception as e:
                print(f"\u2717 {url} - \u5f02\u6b65\u8fde\u63a5\u5931\u8d25: {e}")


def test_proxy_connection():
    """测试代理连接"""
    print("\n=== \u4ee3\u7406\u8fde\u63a5\u6d4b\u8bd5 ===")
    from config_manager import Config
    config = Config()
    network_config = config.get_network_config()
    if network_config['proxy_enabled']:
        print(f"\u4ee3\u7406\u914d\u7f6e: {network_config['proxy_type']}://{network_config['proxy_host']}:{network_config['proxy_port']}")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((network_config['proxy_host'], network_config['proxy_port']))
            sock.close()
            if result == 0:
                print("\u2713 \u4ee3\u7406\u670d\u52a1\u5668\u8fde\u63a5\u6210\u529f")
            else:
                print(f"\u2717 \u4ee3\u7406\u670d\u52a1\u5668\u8fde\u63a5\u5931\u8d25\uff0c\u9519\u8bef\u7801: {result}")
        except Exception as e:
            print(f"\u2717 \u4ee3\u7406\u670d\u52a1\u5668\u8fde\u63a5\u5f02\u5e38: {e}")
    else:
        print("\u4ee3\u7406\u672a\u542f\u7528")


def test_container_network():
    """测试容器内部网络连通性"""
    print("\n=== \u5bb9\u5668\u5185\u90e8\u7f51\u7edc\u6d4b\u8bd5 ===")
    try:
        response = requests.get('http://localhost:12345/health', timeout=5)
        print(f"\u2713 \u672c\u5730Nginx\u670d\u52a1: \u72b6\u6001\u7801 {response.status_code}")
    except Exception as e:
        print(f"\u2717 \u672c\u5730Nginx\u670d\u52a1\u4e0d\u53ef\u8fbe: {e}")
    if os.path.exists('/www/output'):
        print("\u2713 \u8f93\u51fa\u76ee\u5f55\u5b58\u5728")
        if os.access('/www/output', os.W_OK):
            print("\u2713 \u8f93\u51fa\u76ee\u5f55\u53ef\u5199")
        else:
            print("\u2717 \u8f93\u51fa\u76ee\u5f55\u4e0d\u53ef\u5199")
    else:
        print("\u2717 \u8f93\u51fa\u76ee\u5f55\u4e0d\u5b58\u5728")
    config_files = ['/config/config.ini', '/config/channel_rules.yml', '/etc/nginx/nginx.conf']
    for config_file in config_files:
        if os.path.exists(config_file):
            print(f"\u2713 \u914d\u7f6e\u6587\u4ef6\u5b58\u5728: {config_file}")
        else:
            print(f"\u2717 \u914d\u7f6e\u6587\u4ef6\u4e0d\u5b58\u5728: {config_file}")


def test_nginx_service():
    """测试Nginx服务功能"""
    print("\n=== Nginx\u670d\u52a1\u6d4b\u8bd5 ===")
    test_endpoints = [
        ('http://localhost:12345/health', '\u5065\u5eb7\u68c0\u67e5'),
        ('http://localhost:12345/live.m3u', 'M3U\u6587\u4ef6'),
        ('http://localhost:12345/live.txt', 'TXT\u6587\u4ef6')
    ]
    for url, description in test_endpoints:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"\u2713 {description}: \u72b6\u6001\u7801 200 ({len(response.content)} \u5b57\u8282)")
            elif response.status_code == 404:
                print(f"\u26a0 {description}: \u6587\u4ef6\u4e0d\u5b58\u5728 (404)")
            else:
                print(f"\u26a0 {description}: \u72b6\u6001\u7801 {response.status_code}")
        except Exception as e:
            print(f"\u2717 {description}: \u8fde\u63a5\u5931\u8d25 - {e}")


# ═══════════════════════════════════════════════════
# HTTP服务测试 (原 test_http.py)
# ═══════════════════════════════════════════════════

def test_http_service():
    """测试HTTP服务"""
    from config_manager import Config, Logger
    config = Config()
    logger_config = config.get_logging_config()
    temp_logger = Logger(logger_config)
    logger = temp_logger.logger
    http_config = config.get_http_server_config()
    host = http_config['host']
    port = http_config['port']
    base_url = f"http://{host}:{port}"
    logger.info(f"\u5f00\u59cb\u6d4b\u8bd5HTTP\u670d\u52a1: {base_url}")
    try:
        response = requests.get(base_url, timeout=10)
        logger.info(f"\u670d\u52a1\u5668\u54cd\u5e94: \u72b6\u6001\u7801 {response.status_code}")
        if response.status_code == 200:
            logger.info("\u2713 \u670d\u52a1\u5668\u57fa\u672c\u8bbf\u95ee\u6b63\u5e38")
        else:
            logger.error("\u2717 \u670d\u52a1\u5668\u54cd\u5e94\u5f02\u5e38")
            return False
    except Exception as e:
        logger.error(f"\u2717 \u670d\u52a1\u5668\u8bbf\u95ee\u5931\u8d25: {e}")
        return False
    test_files = ['live.m3u', 'live.txt', 'index.html']
    for file in test_files:
        try:
            url = f"{base_url}/{file}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                logger.info(f"\u2713 \u6587\u4ef6\u8bbf\u95ee\u6b63\u5e38: {file} ({len(response.content)} \u5b57\u8282)")
            elif response.status_code == 404:
                logger.warning(f"\u26a0 \u6587\u4ef6\u4e0d\u5b58\u5728: {file}")
            else:
                logger.warning(f"\u26a0 \u6587\u4ef6\u8bbf\u95ee\u5f02\u5e38 {response.status_code}: {file}")
        except Exception as e:
            logger.warning(f"\u26a0 \u6587\u4ef6\u8bbf\u95ee\u5931\u8d25 {file}: {e}")
    logger.info("HTTP\u670d\u52a1\u6d4b\u8bd5\u5b8c\u6210")
    return True


if __name__ == "__main__":
    print("\u5f00\u59cb\u7f51\u7edc\u8bca\u65ad...")
    test_basic_connectivity()
    test_proxy_connection()
    test_container_network()
    test_nginx_service()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_async_connectivity())
    print("\n\u7f51\u7edc\u8bca\u65ad\u5b8c\u6210")


# ── 向后兼容：模块级别名 ──────────────────────────
# 原 network_test.py 和 test_http.py 被合并后，
# 仍可通过 app.scripts.network_test 导入原模块函数
network_test = test_basic_connectivity
test_http = test_http_service
