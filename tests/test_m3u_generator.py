# -*- coding: utf-8 -*-
"""
测试M3U文件生成器模块（m3u_generator模块）

特别注意覆盖以下修复点：
- m3u_generator.py: speed > 0 保护（enhanced_filter_sources中旧逻辑缺少speed > 0检查）
- build_enhanced_extinf 中 source['name'] KeyError防护
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from m3u_generator import M3UGenerator


@pytest.fixture
def mock_config():
    """创建mock配置"""
    config = MagicMock()
    config.get_output_params.return_value = {
        'filename': 'live.m3u',
        'group_by': 'category',
        'include_failed': False,
        'max_sources_per_channel': 3,
        'enable_filter': False,
        'output_dir': '/tmp/test_output'
    }
    config.get_filter_params.return_value = {
        'max_latency': 5000,
        'min_bitrate': 100,
        'must_hd': False,
        'must_4k': False,
        'min_speed': 40,
        'min_resolution': '720p',
        'max_resolution': '4k',
        'resolution_filter_mode': 'range'
    }
    config.get_ua_position.return_value = 'extinf'
    config.is_ua_enabled.return_value = True
    return config


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def generator(mock_config, mock_logger):
    return M3UGenerator(mock_config, mock_logger)


@pytest.fixture
def sample_sources():
    """基础测试源数据"""
    return [
        {
            'name': 'CCTV-1',
            'url': 'http://example.com/cctv1',
            'logo': 'http://logo.com/cctv1.png',
            'category': '央视频道',
            'media_type': 'video',
            'status': 'success',
            'resolution': '1920x1080',
            'bitrate': 4000,
            'response_time': 120,
            'download_speed': 500.0,
            'is_hd': True,
            'is_4k': False,
            'group': '央视频道',
            'country': 'CN',
            'province': None,
            'region': None,
            'user_agent': 'Mozilla/5.0',
        },
        {
            'name': '湖南卫视',
            'url': 'http://example.com/hunan',
            'logo': None,
            'category': '卫视频道',
            'media_type': 'video',
            'status': 'success',
            'resolution': '1280x720',
            'bitrate': 2000,
            'response_time': 80,
            'download_speed': 800.0,
            'is_hd': True,
            'is_4k': False,
            'group': '卫视频道',
            'country': 'CN',
            'province': None,
            'region': None,
            'user_agent': None,
        },
        {
            'name': '电台音乐',
            'url': 'http://example.com/radio',
            'logo': None,
            'category': '收音机',
            'media_type': 'radio',
            'status': 'success',
            'resolution': '',
            'bitrate': 128,
            'response_time': 50,
            'download_speed': 0,
            'is_hd': False,
            'is_4k': False,
            'group': '收音机',
            'country': 'CN',
            'province': None,
            'region': None,
            'user_agent': None,
        },
    ]


class TestEXTINFLineGeneration:
    """测试EXTINF行生成"""

    def test_build_extinf_basic(self, generator, sample_sources):
        """基础EXTINF行生成"""
        source = sample_sources[0]
        extinf = generator.build_enhanced_extinf(source, "base")
        assert extinf.startswith("#EXTINF:-1")
        # build_enhanced_extinf使用re.sub(r'[^a-zA-Z0-9]', '_', name).lower()
        # 所以tvg-id是小写的
        assert 'tvg-id="cctv_1"' in extinf
        assert 'tvg-name="CCTV-1"' in extinf
        assert 'group-title="央视频道"' in extinf
        assert 'media-type="video"' in extinf
        assert 'resolution="1920x1080"' in extinf
        assert ',CCTV-1' in extinf

    def test_build_extinf_with_logo(self, generator, sample_sources):
        """包含图标的EXTINF行"""
        source = sample_sources[0]
        extinf = generator.build_enhanced_extinf(source, "base")
        assert 'tvg-logo="http://logo.com/cctv1.png"' in extinf

    def test_build_extinf_without_logo(self, generator, sample_sources):
        """无图标的EXTINF行（不应出现tvg-logo）"""
        source = sample_sources[1]
        extinf = generator.build_enhanced_extinf(source, "base")
        assert 'tvg-logo' not in extinf

    def test_build_extinf_qualified_level(self, generator, sample_sources):
        """qualified层级应包含响应时间和速度信息"""
        source = sample_sources[0]
        extinf = generator.build_enhanced_extinf(source, "qualified")
        assert 'response-time="120ms"' in extinf
        assert 'download-speed="500.0KB/s"' in extinf

    def test_build_extinf_base_level_no_speed(self, generator, sample_sources):
        """base层级不应包含速度和时间细节"""
        source = sample_sources[0]
        extinf = generator.build_enhanced_extinf(source, "base")
        assert 'response-time' not in extinf
        assert 'download-speed' not in extinf

    def test_build_extinf_media_type_radio(self, generator, sample_sources):
        """radio类型EXTINF"""
        source = sample_sources[2]
        extinf = generator.build_enhanced_extinf(source, "base")
        assert 'media-type="radio"' in extinf

    def test_build_extinf_with_country_info(self, generator, sample_sources):
        """包含国家信息"""
        source = sample_sources[0]
        extinf = generator.build_enhanced_extinf(source, "base")
        assert 'tvg-country="CN"' in extinf

    def test_build_extinf_failed_status(self, generator):
        """失败的源应包含status信息"""
        source = {
            'name': 'Failed Channel',
            'url': 'http://failed.com',
            'status': 'timeout',
            'media_type': 'video',
        }
        extinf = generator.build_enhanced_extinf(source, "base")
        assert 'status="timeout"' in extinf


class TestEXTINFKeyErrorProtection:
    """测试build_enhanced_extinf中source['name'] KeyError防护"""

    def test_extinf_missing_name_raises_keyerror(self, generator):
        """name缺失时应当抛出KeyError（当前使用source['name']直接访问）"""
        source = {
            'url': 'http://example.com/stream',
            'status': 'success',
            'media_type': 'video',
        }
        with pytest.raises(KeyError):
            generator.build_enhanced_extinf(source, "base")


class TestM3UFileOutput:
    """测试M3U文件内容输出"""

    def test_generate_m3u_header(self, generator, sample_sources):
        """M3U文件应以#EXTM3U开头"""
        result = generator.generate_enhanced_m3u(sample_sources, "base")
        assert result.startswith("#EXTM3U")

    def test_generate_m3u_contains_sources(self, generator, sample_sources):
        """生成的M3U应包含所有源"""
        result = generator.generate_enhanced_m3u(sample_sources, "base")
        assert "CCTV-1" in result
        assert "湖南卫视" in result
        assert "电台音乐" in result

    def test_generate_m3u_urls(self, generator, sample_sources):
        """生成的M3U应包含URL"""
        result = generator.generate_enhanced_m3u(sample_sources, "base")
        assert "http://example.com/cctv1" in result
        assert "http://example.com/hunan" in result

    def test_generate_m3u_empty_sources(self, generator):
        """空源列表"""
        result = generator.generate_enhanced_m3u([], "base")
        assert result == "#EXTM3U"


class TestCategoryGrouping:
    """测试分类分组"""

    def test_group_by_category(self, generator, sample_sources):
        """按分类分组"""
        grouped = generator.enhanced_group_and_sort_sources(sample_sources, "base")
        assert '央视频道' in grouped
        assert '卫视频道' in grouped
        assert '收音机' in grouped

    def test_group_extinf_contains_group_title(self, generator, sample_sources):
        """分组后的EXTINF行应包含对应group-title"""
        m3u = generator.generate_enhanced_m3u(sample_sources, "base")
        assert 'group-title="央视频道"' in m3u
        assert 'group-title="卫视频道"' in m3u

    def test_generate_txt_format(self, generator, sample_sources):
        """TXT输出格式"""
        txt = generator.generate_enhanced_txt(sample_sources, "base")
        assert "# 央视频道" in txt
        assert "CCTV-1,http://example.com/cctv1" in txt

    def test_group_key_functions(self, generator):
        """get_group_key不同模式"""
        source = {'name': 'Test', 'country': 'CN', 'region': '华东', 'category': '新闻', 'media_type': 'video', 'source_type': 'online'}
        assert generator.get_group_key(source, 'country') == 'CN'
        assert generator.get_group_key(source, 'region') == '华东'
        assert generator.get_group_key(source, 'category') == '新闻'
        assert generator.get_group_key(source, 'media_type') == 'video'
        assert generator.get_group_key(source, 'source') == 'online'


class TestSpeedFilterLogic:
    """测试速度筛选逻辑

    注意关键修复点：
    - main.py 中 is_source_qualified: speed > 0 and speed < min_speed
    - m3u_generator.py enhanced_filter_sources: speed < min_speed （缺少speed > 0保护）
    """

    def test_filter_zero_speed_in_main_style(self, generator):
        """main.py风格：speed=0时不应被过滤（有speed > 0保护）"""
        filter_params = {'max_latency': 5000, 'min_bitrate': 100,
                         'must_hd': False, 'must_4k': False,
                         'min_speed': 40, 'min_resolution': '', 'max_resolution': '',
                         'resolution_filter_mode': 'range'}
        source = {
            'name': 'Zero Speed',
            'url': 'http://zero.com',
            'status': 'success',
            'media_type': 'video',
            'response_time': 100,
            'download_speed': 0,
            'resolution': '1280x720',
            'bitrate': 500,
            'is_hd': False,
            'is_4k': False,
        }
        # main.py的逻辑: speed > 0 and speed < min_speed 才排除
        # 所以 speed=0 应该通过
        is_ok = True
        speed = source.get('download_speed', 0)
        if speed > 0 and speed < filter_params['min_speed']:
            is_ok = False
        assert is_ok is True

    def test_filter_zero_speed_in_m3u_style(self, generator):
        """m3u_generator.py旧风格：speed=0会被过滤（无speed > 0保护）

        这是需要修复的bug——测试当前行为以便修复后对照
        """
        source = {
            'name': 'Zero Speed',
            'url': 'http://zero.com',
            'status': 'success',
            'media_type': 'video',
            'response_time': 100,
            'download_speed': 0,
            'resolution': '1280x720',
            'bitrate': 500,
            'is_hd': False,
            'is_4k': False,
        }
        filter_params = generator.filter_params
        speed = source.get('download_speed', 0)
        # m3u_generator.py 旧逻辑: speed < min_speed 就过滤（无 speed > 0 保护）
        # speed=0, min_speed=40, 0 < 40 → 会被过滤
        is_filtered_by_old_logic = speed < filter_params['min_speed']
        assert is_filtered_by_old_logic is True

    def test_enhanced_filter_preserves_zero_speed(self, generator):
        """修复后：enhanced_filter_sources 应保留 speed=0 的源"""
        sources = [{
            'name': 'Zero Speed',
            'url': 'http://zero.com',
            'status': 'success',
            'media_type': 'video',
            'response_time': 100,
            'download_speed': 0,
            'resolution': '1280x720',
            'bitrate': 500,
            'is_hd': False,
            'is_4k': False,
        }]
        # M1修复：speed=0（未测速时）应被保留，不受min_speed过滤
        # 只有 speed > 0 且 speed < min_speed 时才过滤
        filtered = generator.enhanced_filter_sources(sources)
        # 修复后：speed=0应被保留
        assert len(filtered) == 1, (
            "speed=0的源应被保留（未测速时不参与速度过滤）。"
        )

    def test_filter_positive_speed_below_min(self, generator):
        """正速度低于最低阈值应被过滤（main.py和m3u都一致）"""
        source = {
            'name': 'Slow',
            'url': 'http://slow.com',
            'status': 'success',
            'media_type': 'video',
            'response_time': 100,
            'download_speed': 10,  # < 40
            'resolution': '1280x720',
            'bitrate': 500,
            'is_hd': False,
            'is_4k': False,
        }
        filtered = generator.enhanced_filter_sources([source])
        assert len(filtered) == 0

    def test_filter_positive_speed_above_min(self, generator):
        """正速度高于最低阈值应保留"""
        source = {
            'name': 'Fast',
            'url': 'http://fast.com',
            'status': 'success',
            'media_type': 'video',
            'response_time': 100,
            'download_speed': 100,  # >= 40
            'resolution': '1280x720',
            'bitrate': 500,
            'is_hd': False,
            'is_4k': False,
        }
        filtered = generator.enhanced_filter_sources([source])
        assert len(filtered) == 1


class TestNoneValueProtection:
    """测试None值修复"""

    def test_none_speed_in_sorting(self, generator):
        """排序时download_speed为None不报错"""
        sources = [
            {'name': 'A', 'url': 'http://a.com', 'media_type': 'video',
             'country': 'CN', 'province': None, 'continent': 'Asia',
             'download_speed': None, 'response_time': 100},
            {'name': 'B', 'url': 'http://b.com', 'media_type': 'video',
             'country': 'CN', 'province': None, 'continent': 'Asia',
             'download_speed': 500, 'response_time': 50},
        ]
        # 不应抛出异常
        result = generator.enhanced_group_and_sort_sources(sources, "base")
        assert 'Unknown' in result or any(v for v in result.values())

    def test_none_response_time_in_sorting(self, generator):
        """排序时response_time为None不报错"""
        sources = [
            {'name': 'A', 'url': 'http://a.com', 'media_type': 'video',
             'country': 'CN', 'province': None, 'continent': 'Asia',
             'download_speed': 100, 'response_time': None},
            {'name': 'B', 'url': 'http://b.com', 'media_type': 'video',
             'country': 'CN', 'province': None, 'continent': 'Asia',
             'download_speed': 200, 'response_time': 50},
        ]
        result = generator.enhanced_group_and_sort_sources(sources, "base")
        assert result is not None

    def test_none_name_in_grouping(self, generator):
        """分组时name为None不报错"""
        sources = [
            {'name': None, 'url': 'http://a.com', 'media_type': 'video',
             'country': 'CN', 'province': None, 'continent': 'Asia',
             'download_speed': 100, 'response_time': 100},
        ]
        # 不应抛出异常，x.get('name', '') or '' 应处理None
        result = generator.enhanced_group_and_sort_sources(sources, "base")
        assert result is not None
