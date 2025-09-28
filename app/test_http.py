#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP服务测试脚本
"""

import requests
import time
import sys
from config_manager import Config, Logger

def test_http_service():
    """测试HTTP服务"""
    config = Config()
    logger_config = config.get_logging_config()
    temp_logger = Logger(logger_config)
    logger = temp_logger.logger
    
    http_config = config.get_http_server_config()
    host = http_config['host']
    port = http_config['port']
    
    base_url = f"http://{host}:{port}"
    
    logger.info(f"开始测试HTTP服务: {base_url}")
    
    # 测试服务器响应
    try:
        response = requests.get(base_url, timeout=10)
        logger.info(f"服务器响应: 状态码 {response.status_code}")
        
        if response.status_code == 200:
            logger.info("✓ 服务器基本访问正常")
        else:
            logger.error("✗ 服务器响应异常")
            return False
    except Exception as e:
        logger.error(f"✗ 服务器访问失败: {e}")
        return False
    
    # 测试文件访问
    test_files = ['live.m3u', 'live.txt', 'index.html']
    
    for file in test_files:
        try:
            url = f"{base_url}/{file}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"✓ 文件访问正常: {file} ({len(response.content)} 字节)")
            elif response.status_code == 404:
                logger.warning(f"⚠ 文件不存在: {file}")
            else:
                logger.warning(f"⚠ 文件访问异常 {response.status_code}: {file}")
                
        except Exception as e:
            logger.warning(f"⚠ 文件访问失败 {file}: {e}")
    
    logger.info("HTTP服务测试完成")
    return True

if __name__ == "__main__":
    if test_http_service():
        print("HTTP服务测试通过")
        sys.exit(0)
    else:
        print("HTTP服务测试失败")
        sys.exit(1)