"""
app.security 单元测试

覆盖解析阶段**真实生效**的窄门禁 is_static_safe：
  - 合法协议 (http/https/rtmp/rtsp/rtp) 放行
  - 空 / 缺 scheme / 不支持协议 (file/ftp/javascript/data) 拒绝
  - 缺主机 / 非法主机格式拒绝
  - SSRF：localhost / .local / .internal / 127.0.0.1 / 10.x / 192.168.x / 172.16.x / 链路本地 拒绝
  - 合法公网域名 / 公网 IP / IPv6 放行
  - URL 带端口 / query / fragment 剥离 / | 分隔 正确处理

（历史上本文件曾测试 validate_url / is_safe_url / sanitize_url 等「全量审查」逻辑，
经安全审计 S1/S2 确认其为死代码并已移除，故改为聚焦 is_static_safe 这一真门禁。）
"""

from app.security import SourceData, is_static_safe


class TestIsStaticSafe:
    """解析阶段窄门禁 is_static_safe"""

    # ── 合法协议放行 ──
    def test_valid_http(self):
        ok, reason, cat = is_static_safe('http://example.com/stream.m3u8')
        assert ok is True and cat == 'ok'

    def test_valid_https(self):
        assert is_static_safe('https://example.com/live/stream')[0] is True

    def test_valid_rtmp_rtsp_rtp(self):
        assert is_static_safe('rtmp://example.com/live')[0] is True
        assert is_static_safe('rtsp://example.com/live')[0] is True
        assert is_static_safe('rtp://example.com/live')[0] is True

    # ── 非法 / 缺失 ──
    def test_empty(self):
        ok, reason, cat = is_static_safe('')
        assert ok is False and cat == 'host' and '空' in reason

    def test_whitespace_only(self):
        assert is_static_safe('   ')[0] is False

    def test_missing_scheme(self):
        ok, reason, cat = is_static_safe('example.com/stream')
        assert ok is False and cat == 'scheme'

    def test_blocked_file_scheme(self):
        ok, reason, cat = is_static_safe('file:///etc/passwd')
        assert ok is False and cat == 'scheme'

    def test_blocked_ftp_scheme(self):
        assert is_static_safe('ftp://example.com/file')[0] is False

    def test_blocked_javascript_scheme(self):
        assert is_static_safe('javascript:alert(1)')[0] is False

    def test_blocked_data_scheme(self):
        assert is_static_safe('data:text/html,<script>')[0] is False

    def test_missing_host(self):
        ok, reason, cat = is_static_safe('http:///path')
        assert ok is False and cat == 'host'

    def test_invalid_host_chars(self):
        assert is_static_safe('http://exa mple/stream')[0] is False

    # ── SSRF 防护 ──
    def test_ssrf_localhost(self):
        ok, reason, cat = is_static_safe('http://localhost/stream')
        assert ok is False and cat == 'ssrf'

    def test_ssrf_dot_local(self):
        assert is_static_safe('http://host.local/stream')[0] is False

    def test_ssrf_dot_internal(self):
        assert is_static_safe('http://host.internal/stream')[0] is False

    def test_ssrf_loopback_ip(self):
        assert is_static_safe('http://127.0.0.1/stream')[0] is False

    def test_ssrf_private_10(self):
        assert is_static_safe('http://10.0.0.1/stream')[0] is False

    def test_ssrf_private_192168(self):
        assert is_static_safe('http://192.168.1.1/stream')[0] is False

    def test_ssrf_private_17216(self):
        assert is_static_safe('http://172.16.0.1/stream')[0] is False

    def test_ssrf_link_local(self):
        assert is_static_safe('http://169.254.169.254/stream')[0] is False

    # ── 合法公网地址放行（须用真正公网地址；文档保留段 203.0.113.x / 2001:db8::
    #     会被 Python ipaddress 归类为 is_private，属 SSRF 正确拦截，不算「公网」）──
    def test_valid_public_ip(self):
        assert is_static_safe('http://8.8.8.8/stream')[0] is True

    def test_valid_public_domain(self):
        assert is_static_safe('http://example.com/stream')[0] is True

    def test_ipv6_host(self):
        assert is_static_safe('http://[2606:4700:4700::1111]/stream')[0] is True

    # ── 形态处理 ──
    def test_url_with_port(self):
        assert is_static_safe('http://example.com:8080/stream.m3u8')[0] is True

    def test_url_with_query(self):
        assert is_static_safe('https://example.com/stream?key=value&token=abc')[0] is True

    def test_fragment_stripped(self):
        assert is_static_safe('http://example.com/stream#frag')[0] is True

    def test_pipe_stripped(self):
        assert is_static_safe('http://example.com/stream|extra')[0] is True


class TestSourceData:
    """SourceData TypedDict 基本验证"""

    def test_can_create_with_fields(self):
        data: SourceData = {
            'name': 'CCTV-1',
            'url': 'http://example.com/stream',
            'group': '央视频道',
            'logo': 'http://example.com/logo.png',
        }
        assert data['name'] == 'CCTV-1'
        assert data['url'] == 'http://example.com/stream'

    def test_can_create_with_partial_fields(self):
        data: SourceData = {
            'name': 'Test',
            'url': 'http://example.com/stream',
        }
        assert data['name'] == 'Test'
