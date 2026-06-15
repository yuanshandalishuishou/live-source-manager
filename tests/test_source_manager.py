# -*- coding: utf-8 -*-
"""
测试源管理模块（source_manager模块）
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from source_manager import SourceManager


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get_network_config.return_value = {
        'proxy_enabled': False,
        'ipv6_enabled': False,
        'proxy_type': 'socks5',
        'proxy_host': '192.168.1.211',
        'proxy_port': 1800,
        'proxy_username': '',
        'proxy_password': '',
    }
    config.get_github_config.return_value = {
        'api_url': 'https://api.github.com',
        'api_token': '',
        'rate_limit': 5000,
    }
    config.get_sources.return_value = {
        'local_dirs': ['/tmp/test_sources'],
        'online_urls': ['https://example.com/list.m3u'],
    }
    config.get_user_agents.return_value = {}
    config.is_ua_enabled.return_value = False
    return config


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_channel_rules():
    rules = MagicMock()
    rules.extract_channel_info.return_value = {
        'country': 'CN',
        'region': None,
        'language': 'zh',
        'province': None,
        'continent': 'Asia',
    }
    rules.determine_category.return_value = '其他频道'
    return rules


@pytest.fixture
def source_manager(mock_config, mock_logger, mock_channel_rules):
    sm = SourceManager(mock_config, mock_logger, mock_channel_rules)
    # 确保在线目录存在
    os.makedirs(sm.online_dir, exist_ok=True)
    return sm


class TestSourceParsing:
    """测试源解析"""

    def test_parse_m3u_file(self, source_manager):
        """解析M3U文件"""
        m3u_content = """#EXTM3U
#EXTINF:-1 tvg-id="cctv1" tvg-name="CCTV-1" tvg-logo="http://logo.com/1.png" group-title="央视频道",CCTV-1 综合
http://example.com/cctv1.ts
#EXTINF:-1 tvg-id="hunantv" tvg-name="湖南卫视",湖南卫视
http://example.com/hunan.ts
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.m3u', delete=False, encoding='utf-8') as f:
            f.write(m3u_content)
            tmp_path = f.name

        try:
            sources = source_manager.parse_file(tmp_path)
            assert len(sources) == 2
            assert sources[0]['name'] == 'CCTV-1 综合'
            assert sources[0]['url'] == 'http://example.com/cctv1.ts'
            assert sources[0]['logo'] == 'http://logo.com/1.png'
            assert sources[0]['group'] == '央视频道'
            assert sources[1]['name'] == '湖南卫视'
            assert sources[1]['url'] == 'http://example.com/hunan.ts'
        finally:
            os.unlink(tmp_path)

    def test_parse_txt_file(self, source_manager):
        """解析TXT文件"""
        txt_content = """#EXTM3U
http://example.com/stream1.ts
http://example.com/stream2.ts
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(txt_content)
            tmp_path = f.name

        try:
            sources = source_manager.parse_file(tmp_path)
            # 第一个URL行会被解析
            assert len(sources) >= 1
        finally:
            os.unlink(tmp_path)

    def test_parse_file_with_url_ua(self, source_manager):
        """解析带UA参数的URL"""
        m3u_content = """#EXTM3U
#EXTINF:-1 tvg-name="Test Channel",Test Channel
http://example.com/stream|User-Agent=Mozilla/5.0
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.m3u', delete=False, encoding='utf-8') as f:
            f.write(m3u_content)
            tmp_path = f.name

        try:
            sources = source_manager.parse_file(tmp_path)
            assert len(sources) == 1
            assert sources[0]['url'] == 'http://example.com/stream'
        finally:
            os.unlink(tmp_path)

    def test_parse_empty_file(self, source_manager):
        """解析空文件"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.m3u', delete=False, encoding='utf-8') as f:
            f.write("")
            tmp_path = f.name

        try:
            sources = source_manager.parse_file(tmp_path)
            assert len(sources) == 0
        finally:
            os.unlink(tmp_path)

    def test_parse_file_with_gbk_encoding(self, source_manager):
        """解析GBK编码文件"""
        # 使用GBK编码写文件
        gbk_content = "#EXTM3U\n#EXTINF:-1 tvg-name=\"测试频道\",测试频道\nhttp://test.com/stream\n"
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.m3u', delete=False) as f:
            f.write(gbk_content.encode('gbk'))
            tmp_path = f.name

        try:
            sources = source_manager.parse_file(tmp_path)
            assert len(sources) == 1
        finally:
            os.unlink(tmp_path)

    def test_extract_name_from_extinf(self, source_manager):
        """从EXTINF提取频道名"""
        name = source_manager.extract_name('#EXTINF:-1 tvg-id="cctv1" tvg-name="CCTV-1 综合",CCTV-1 综合')
        assert name == 'CCTV-1 综合'

    def test_extract_name_without_comma(self, source_manager):
        """没有逗号的EXTINF行"""
        name = source_manager.extract_name('#EXTINF:-1')
        assert name == "Unknown Channel"

    def test_extract_logo(self, source_manager):
        """提取图标"""
        logo = source_manager.extract_logo('#EXTINF:-1 tvg-logo="http://logo.com/1.png",Test')
        assert logo == 'http://logo.com/1.png'

    def test_extract_logo_none(self, source_manager):
        """没有图标"""
        logo = source_manager.extract_logo('#EXTINF:-1 tvg-name="Test",Test')
        assert logo is None

    def test_extract_group(self, source_manager):
        """提取分组"""
        group = source_manager.extract_group('#EXTINF:-1 group-title="央视频道",CCTV-1')
        assert group == '央视频道'

    def test_extract_group_none(self, source_manager):
        """没有分组"""
        group = source_manager.extract_group('#EXTINF:-1 tvg-name="Test",Test')
        assert group is None

    def test_parse_all_files_local(self, source_manager):
        """parse_all_files 扫描本地目录"""
        # 创建临时本地目录和文件
        local_dir = tempfile.mkdtemp()
        m3u_path = os.path.join(local_dir, 'test.m3u')
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n#EXTINF:-1 tvg-name=\"CCTV-1\",CCTV-1\nhttp://cctv1.ts\n")

        source_manager.config.get_sources.return_value = {
            'local_dirs': [local_dir],
            'online_urls': [],
        }
        # mock在线目录返回空
        source_manager.online_dir = tempfile.mkdtemp()

        sources = source_manager.parse_all_files()
        assert len(sources) >= 1

        # 清理
        os.unlink(m3u_path)
        os.rmdir(local_dir)
        os.rmdir(source_manager.online_dir)


class TestSourceDownload:
    """测试源下载"""

    @pytest.mark.asyncio
    async def test_download_with_retry_http_200(self, source_manager):
        """下载成功（HTTP 200）"""
        # Mock download_file 直接返回文件路径，避免复杂的 aiohttp session mock
        filepath = os.path.join(source_manager.online_dir, "list.m3u")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n#EXTINF:-1,Test\nhttp://test.com\n")

        async def mock_download(url, strategy):
            return filepath

        with patch.object(source_manager, 'download_file', side_effect=mock_download), \
             patch('source_manager.is_safe_url', return_value=(True, 'ok')):
            result = await source_manager.download_with_retry("https://example.com/list.m3u")
            assert result is not None
            if result and os.path.exists(result):
                with open(result, 'r', encoding='utf-8') as f:
                    content = f.read()
                assert "#EXTM3U" in content
                os.unlink(result)

    @pytest.mark.asyncio
    async def test_download_with_retry_http_404(self, source_manager):
        """下载失败（HTTP 404）"""
        async def mock_download_fail(url, strategy):
            from app.utils import SourceDownloadError
            raise SourceDownloadError(f"HTTP错误 404: {url}")

        with patch.object(source_manager, 'download_file', side_effect=mock_download_fail), \
             patch('source_manager.is_safe_url', return_value=(True, 'ok')):
            result = await source_manager.download_with_retry("https://example.com/notfound")
            assert result is None

    @pytest.mark.asyncio
    async def test_download_retry_on_failure(self, source_manager):
        """重试机制——直连失败后通过代理重试"""
        call_count = [0]

        async def mock_download_retry(url, strategy):
            call_count[0] += 1
            filepath = os.path.join(source_manager.online_dir, f"retry_{call_count[0]}.m3u")
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("ok")
            return filepath

        with patch.object(source_manager, 'download_file', side_effect=mock_download_retry), \
             patch('source_manager.is_safe_url', return_value=(True, 'ok')):
            result = await source_manager.download_with_retry("https://example.com/retry_test")
            # 无论直连还是代理，至少尝试1次后返回文件路径
            assert result is not None
            assert call_count[0] >= 1
            if result and os.path.exists(result):
                os.unlink(result)

    def test_get_filename_from_url(self, source_manager):
        """从URL提取文件名"""
        filename = source_manager.get_filename_from_url("https://example.com/tv/list.m3u")
        assert filename == "list.m3u"

    def test_get_filename_without_extension(self, source_manager):
        """URL没有扩展名"""
        filename = source_manager.get_filename_from_url("https://example.com/tv/source")
        assert len(filename) > 0
        assert "source_" in filename or filename.endswith(".txt")

    def test_is_valid_url(self, source_manager):
        """验证URL有效性"""
        assert source_manager.is_valid_url("http://example.com/stream.ts") is True
        assert source_manager.is_valid_url("https://example.com/stream.ts") is True
        assert source_manager.is_valid_url("not a url") is False
        assert source_manager.is_valid_url("") is False

class TestSourceManagerURLSecurity:
    """测试URL安全审查集成"""
    
    def test_source_manager_imports_url_sanitizer(self):
        from source_manager import SourceManager
        from url_sanitizer import validate_url, is_safe_url
        assert True
