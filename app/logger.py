#!/usr/bin/env python3
"""
日志管理模块
============

包含:
  - 统一日志格式常量
  - setup_logger — 创建统一格式的日志记录器
  - Logger — 日志管理类（文件轮转 + 控制台输出）
"""

import contextlib
import logging
import logging.handlers
import os
import sys

# 统一日志格式
UNIFIED_LOG_FORMAT = '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'
UNIFIED_LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """创建统一格式的日志记录器"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(UNIFIED_LOG_FORMAT, datefmt=UNIFIED_LOG_DATE_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


class Logger:
    """日志管理类 — 增强错误处理（与原版完全一致）"""

    def __init__(self, config: dict):
        self.logger = self.setup_logging(config)

    def setup_logging(self, config: dict):
        """配置日志系统 - 增强错误处理"""
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
            datefmt='%Y-%m-%d %H:%M:%S',
        )

        # 文件处理器（如果配置了文件路径）
        log_file = config.get('file', '/log/app.log')
        file_handler = None

        if log_file:
            try:
                log_dir = os.path.dirname(log_file)
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)

                if config.get('clear_on_startup', False) and os.path.exists(log_file):
                    try:
                        os.remove(log_file)
                    except Exception as e:
                        print(f'无法清空日志文件: {e}')

                max_size = config.get('max_size', 10) * 1024 * 1024
                backup_count = config.get('backup_count', 5)

                file_handler = logging.handlers.RotatingFileHandler(
                    log_file,
                    maxBytes=max_size,
                    backupCount=backup_count,
                    encoding='utf-8',
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            except Exception as e:
                print(f'创建文件日志处理器失败: {e}')

        # 控制台处理器（如果启用）
        if config.get('enable_console', True):
            try:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setFormatter(formatter)
                logger.addHandler(console_handler)
            except Exception as e:
                print(f'创建控制台日志处理器失败: {e}')

        # 如果没有任何处理器，添加一个基本的控制台处理器
        if not logger.handlers:
            print('警告: 无日志处理器，创建基本控制台处理器')
            basic_handler = logging.StreamHandler(sys.stdout)
            basic_handler.setFormatter(formatter)
            logger.addHandler(basic_handler)

        with contextlib.suppress(Exception):
            logger.info('日志系统初始化完成')

        return logger
