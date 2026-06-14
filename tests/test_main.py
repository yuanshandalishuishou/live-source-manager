# -*- coding: utf-8 -*-
"""
集成测试 - 测试主模块（main模块）

测试EnhancedLiveSourceManager初始化和模块交互逻辑
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import EnhancedLiveSourceManager


class TestEnhancedLiveSourceManagerInit:
    """测试EnhancedLiveSourceManager初始化流程"""

    def test_initial_object_state(self):
        """不调用initialize时各组件为None"""
        manager = EnhancedLiveSourceManager()
        assert manager.config is None
        assert manager.logger is None
        assert manager.channel_rules is None
        assert manager.source_manager is None
        assert manager.stream_tester is None

    @patch('main.Config')
    @patch('main.Logger')
    @patch('main.ChannelRules')
    @patch('main.SourceManager')
    @patch('main.StreamTester')
    def test_initialize_success(self, MockStreamTester, MockSourceManager,
                                 MockChannelRules, MockLogger, MockConfig):
        """初始化成功流程"""
        MockConfig.return_value.get_logging_config.return_value = {
            'level': 'INFO', 'file': '/tmp/test.log',
            'max_size': 10, 'backup_count': 5, 'enable_console': True
        }
        MockConfig.return_value.get_output_params.return_value = {
            'output_dir': '/tmp/test_output', 'filename': 'live.m3u'
        }
        MockConfig.return_value.get_filter_params.return_value = {
            'max_latency': 5000, 'min_bitrate': 100, 'must_hd': False,
            'must_4k': False, 'min_speed': 40, 'min_resolution': '720p',
            'max_resolution': '4k', 'resolution_filter_mode': 'range',
        }

        MockLogger.return_value.logger = MagicMock()

        MockChannelRules.return_value.test_classification.return_value = [
            ("CCTV-1", "央视频道", "央视频道", "✓"),
        ]

        manager = EnhancedLiveSourceManager()
        with patch.object(manager, '_verify_nginx_directory', return_value=True):
            result = manager.initialize()

        assert result is True
        assert manager.config is not None
        assert manager.logger is not None
        assert manager.channel_rules is not None
        assert manager.source_manager is not None
        assert manager.stream_tester is not None

    @patch('main.Config')
    @patch('main.Logger')
    def test_initialize_config_failure(self, MockLogger, MockConfig):
        """配置初始化失败"""
        MockConfig.side_effect = Exception("Config init failed")

        manager = EnhancedLiveSourceManager()
        result = manager.initialize()
        assert result is False

    @patch('main.Config')
    @patch('main.Logger')
    @patch('main.ChannelRules')
    def test_channel_rules_test_failure_still_continues(self, MockChannelRules,
                                                         MockLogger, MockConfig):
        """频道规则测试失败时仍继续运行"""
        MockConfig.return_value.get_logging_config.return_value = {
            'level': 'INFO', 'file': '', 'enable_console': True
        }
        MockConfig.return_value.get_output_params.return_value = {
            'output_dir': '/tmp/test_out', 'filename': 'live.m3u'
        }
        MockLogger.return_value.logger = MagicMock()
        MockChannelRules.return_value.test_classification.side_effect = Exception("Test failed")

        manager = EnhancedLiveSourceManager()
        with patch.object(manager, '_verify_nginx_directory', return_value=True):
            result = manager.initialize()

        assert result is True  # 测试失败不阻止继续


class TestRefineAudioTypeKeyError:
    """测试_refine_audio_type中source['name'] KeyError防护"""

    def test_refine_audio_type_normal(self):
        """正常调用"""
        manager = EnhancedLiveSourceManager()
        result = manager._refine_audio_type({'name': '中央人民广播电台'})
        assert result == 'radio'

    def test_refine_audio_type_audio_keyword(self):
        """音频关键词匹配"""
        manager = EnhancedLiveSourceManager()
        result = manager._refine_audio_type({'name': '有声小说频道'})
        assert result == 'audio'

    def test_refine_audio_type_default(self):
        """默认返回audio"""
        manager = EnhancedLiveSourceManager()
        result = manager._refine_audio_type({'name': 'UnknownChannel'})
        assert result == 'audio'

    def test_refine_audio_type_missing_name_defaults_to_audio(self):
        """name缺失时默认返回audio（M3修复已使用source.get('name', '')）"""
        manager = EnhancedLiveSourceManager()
        # 已修复：使用source.get('name', '')，不会抛出KeyError
        result = manager._refine_audio_type({})
        assert result == 'audio'


class TestEnhanceChannelClassification:
    """测试enhance_channel_classification"""

    def test_enhance_classification_basic(self):
        """基本分类增强"""
        manager = EnhancedLiveSourceManager()
        manager.channel_rules = MagicMock()
        manager.channel_rules.extract_channel_info.return_value = {
            'country': 'CN', 'region': None, 'language': 'zh',
            'province': None, 'continent': 'Asia',
        }
        manager.channel_rules.determine_category.return_value = '央视频道'
        manager.logger = MagicMock()

        source = {'name': 'CCTV-1', 'url': 'http://t.com/1.ts', 'media_type': 'video'}
        result = manager.enhance_channel_classification(source)
        assert result['category'] == '央视频道'
        assert result['media_type'] == 'video'
        assert result['country'] == 'CN'


class TestShouldOverrideCategory:
    """测试_should_override_category"""

    def test_override_default_category(self):
        """其他频道总是可覆盖"""
        manager = EnhancedLiveSourceManager()
        assert manager._should_override_category('央视频道', '其他频道', 'CCTV-1') is True

    def test_not_override_with_fallback(self):
        """新分类为其他频道不覆盖"""
        manager = EnhancedLiveSourceManager()
        assert manager._should_override_category('其他频道', '央视频道', 'Test') is False

    def test_override_with_higher_priority(self):
        """高优先级覆盖低优先级"""
        manager = EnhancedLiveSourceManager()
        assert manager._should_override_category('央视频道', '北京频道', 'CCTV-1') is True

    def test_not_override_with_lower_priority(self):
        """低优先级不覆盖高优先级"""
        manager = EnhancedLiveSourceManager()
        assert manager._should_override_category('北京频道', '央视频道', 'BTV-1') is False

    def test_override_weishi_special_rule(self):
        """卫视特殊规则：名称含卫视但分类不是卫视时覆盖"""
        manager = EnhancedLiveSourceManager()
        assert manager._should_override_category('卫视频道', '北京频道', '湖南卫视') is True

    def test_override_cctv_special_rule(self):
        """CCTV特殊规则：名称含CCTV但分类不是央视时覆盖"""
        manager = EnhancedLiveSourceManager()
        assert manager._should_override_category('央视频道', '其他频道', 'CCTV-1') is True


class TestConditionBasedFiltering:
    """测试条件筛选逻辑"""

    @patch('main.Config')
    def test_is_source_qualified_basic(self, MockConfig):
        """条件筛选"""
        MockConfig.return_value.get_filter_params.return_value = {
            'max_latency': 5000, 'min_bitrate': 100,
            'must_hd': False, 'must_4k': False,
            'min_speed': 40, 'min_resolution': '720p',
            'max_resolution': '4k', 'resolution_filter_mode': 'range',
        }

        manager = EnhancedLiveSourceManager()
        manager.config = MockConfig.return_value
        manager.logger = MagicMock()

        source = {
            'status': 'success', 'name': 'Test', 'media_type': 'video',
            'response_time': 100, 'resolution': '1920x1080',
            'bitrate': 2000, 'is_hd': False, 'is_4k': False,
            'download_speed': 500.0,
        }
        filter_params = MockConfig.return_value.get_filter_params()
        assert manager.is_source_qualified(source, filter_params) is True

    @patch('main.Config')
    def test_is_source_qualified_low_latency_audio(self, MockConfig):
        """音频内容的延迟检查"""
        MockConfig.return_value.get_filter_params.return_value = {
            'max_latency': 5000, 'min_bitrate': 100,
            'must_hd': False, 'must_4k': False,
            'min_speed': 40, 'min_resolution': '720p',
            'max_resolution': '4k', 'resolution_filter_mode': 'range',
        }

        manager = EnhancedLiveSourceManager()
        manager.config = MockConfig.return_value
        manager.logger = MagicMock()

        source = {
            'status': 'success', 'name': 'Radio FM', 'media_type': 'radio',
            'response_time': 3000, 'download_speed': 0,
        }
        filter_params = MockConfig.return_value.get_filter_params()
        assert manager.is_source_qualified(source, filter_params) is True

    def test_check_resolution_range(self):
        """分辨率范围检查"""
        manager = EnhancedLiveSourceManager()
        assert manager.check_resolution("1920x1080", "1280x720", "3840x2160", "range") is True
        assert manager.check_resolution("640x480", "1280x720", "3840x2160", "range") is False

    def test_check_resolution_min_only(self):
        """仅最低分辨率检查"""
        manager = EnhancedLiveSourceManager()
        assert manager.check_resolution("1920x1080", "1280x720", "", "min_only") is True
        assert manager.check_resolution("640x480", "1280x720", "", "min_only") is False

    def test_check_resolution_max_only(self):
        """仅最高分辨率检查"""
        manager = EnhancedLiveSourceManager()
        assert manager.check_resolution("1280x720", "", "1920x1080", "max_only") is True
        assert manager.check_resolution("3840x2160", "", "1920x1080", "max_only") is False

    def test_check_resolution_1080p_format(self):
        """1080p格式解析"""
        manager = EnhancedLiveSourceManager()
        assert manager.check_resolution("1920x1080", "720p", "", "min_only") is True

    def test_check_resolution_unknown_passed(self):
        """未知分辨率默认通过"""
        manager = EnhancedLiveSourceManager()
        assert manager.check_resolution("", "1280x720", "", "min_only") is True


class TestHierarchicalFiltering:
    """测试分层筛选"""

    @patch('main.Config')
    def test_hierarchical_filtering_empty(self, MockConfig):
        """空源列表"""
        manager = EnhancedLiveSourceManager()
        manager.config = MockConfig.return_value
        manager.logger = MagicMock()
        manager.channel_rules = MagicMock()
        manager.stream_tester = MagicMock()

        valid, base, qualified = manager.hierarchical_filtering([])
        assert valid == []
        assert base == []
        assert qualified == []

    @patch('main.Config')
    def test_hierarchical_filtering_with_sources(self, MockConfig):
        """有源的分层筛选"""
        MockConfig.return_value.get_filter_params.return_value = {
            'max_latency': 5000, 'min_bitrate': 100,
            'must_hd': False, 'must_4k': False,
            'min_speed': 40, 'min_resolution': '720p',
            'max_resolution': '4k', 'resolution_filter_mode': 'range',
        }

        manager = EnhancedLiveSourceManager()
        manager.config = MockConfig.return_value
        manager.logger = MagicMock()
        manager.channel_rules = MagicMock()
        manager.channel_rules.extract_channel_info.return_value = {
            'country': 'CN', 'region': None, 'province': None, 'continent': 'Asia',
        }
        manager.channel_rules.determine_category.return_value = '央视频道'

        sources = [
            {'name': 'CCTV-1', 'url': 'http://t.com/1', 'status': 'success',
             'media_type': 'video', 'resolution': '1920x1080', 'bitrate': 2000,
             'response_time': 100, 'download_speed': 500.0, 'is_hd': True, 'is_4k': False},
            {'name': 'CCTV-2', 'url': 'http://t.com/2', 'status': 'success',
             'media_type': 'video', 'resolution': '1280x720', 'bitrate': 1500,
             'response_time': 200, 'download_speed': 300.0, 'is_hd': True, 'is_4k': False},
        ]

        with patch.object(manager, 'resolution_based_filtering', side_effect=lambda x: x):
            with patch.object(manager, 'condition_based_filtering', side_effect=lambda x: x):
                valid, base, qualified = manager.hierarchical_filtering(sources)

        assert len(valid) == 2
        assert len(base) == 2
        assert len(qualified) == 2
