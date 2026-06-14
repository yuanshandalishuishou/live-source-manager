#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据模型定义模块
使用 TypedDict 定义直播源相关数据结构
"""

from typing import TypedDict, Optional


class SourceData(TypedDict, total=False):
    """直播源数据结构定义"""
    name: str
    url: str
    url_original: str
    logo: str
    user_agent: str
    group: str
    status: str
    response_time: float
    download_speed: float
    resolution: str
    bitrate: int
    fps: float
    media_type: str
    category: str
    province: str
    country: str
    is_qualified: bool
