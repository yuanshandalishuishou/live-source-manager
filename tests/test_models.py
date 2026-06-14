# -*- coding: utf-8 -*-
"""
测试数据模型模块（models模块）
"""

import pytest
import sys

sys.path.insert(0, 'app')

from models import SourceData


class TestSourceData:
    """测试SourceData TypedDict"""

    def test_source_data_is_typed_dict(self):
        """基本字段赋值和读取"""
        sd: SourceData = {"name": "CCTV1", "url": "http://example.com"}
        assert sd["name"] == "CCTV1"
        assert sd["url"] == "http://example.com"

    def test_source_data_with_all_fields(self):
        """所有字段填写"""
        sd: SourceData = {
            "name": "CCTV1",
            "url": "http://example.com/stream",
            "url_original": "http://original.com/stream",
            "logo": "http://example.com/logo.png",
            "user_agent": "Mozilla/5.0",
            "group": "央视",
            "status": "online",
            "response_time": 0.5,
            "download_speed": 5.2,
            "resolution": "1920x1080",
            "bitrate": 4000,
            "fps": 30.0,
            "media_type": "video",
            "category": "央视综合",
            "province": "北京",
            "country": "中国",
            "is_qualified": True,
        }
        assert sd["name"] == "CCTV1"
        assert sd["resolution"] == "1920x1080"
        assert sd["bitrate"] == 4000

    def test_source_data_optional_fields(self):
        """total=False 的字段都是可选的"""
        sd: SourceData = {"name": "Test"}
        assert sd["name"] == "Test"
        # 不存在的字段触发KeyError
        with pytest.raises(KeyError):
            _ = sd["nonexistent"]
