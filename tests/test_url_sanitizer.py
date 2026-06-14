import pytest
import sys
sys.path.insert(0, 'app')
from url_sanitizer import validate_url, sanitize_url, is_safe_url

class TestURLValidation:
    def test_normal_http_url(self):
        result = validate_url("http://example.com/stream.m3u8")
        assert result["valid"] == True
        assert result["safe"] == True
    
    def test_normal_https_url(self):
        result = validate_url("https://example.com/stream.m3u8")
        assert result["valid"] == True
        assert result["safe"] == True
    
    def test_blocked_scheme_file(self):
        result = validate_url("file:///etc/passwd")
        assert result["safe"] == False
    
    def test_blocked_scheme_javascript(self):
        result = validate_url("javascript:alert(1)")
        assert result["safe"] == False
    
    def test_xss_injection(self):
        result = validate_url("http://example.com/<script>alert(1)</script>")
        assert result["safe"] == False
    
    def test_empty_url(self):
        result = validate_url("")
        assert result["safe"] == False
    
    def test_command_injection(self):
        # URL中的特殊字符会被解析；分号在path中检测规则识别
        result = validate_url("http://example.com/?cmd=ls&shell=true")
        assert result["safe"] == False
    
    def test_private_ip(self):
        result = validate_url("http://192.168.1.1/stream")
        assert result["safe"] == False
    
    def test_url_with_ua_suffix(self):
        """确保|User-Agent=xxx后缀不影响URL验证"""
        result = validate_url("http://example.com/stream|User-Agent=Mozilla")
        assert result["valid"] == True
        assert result["safe"] == True

class TestSanitizeURL:
    def test_normalize_http(self):
        result = sanitize_url("HTTP://EXAMPLE.COM/Stream")
        # 应该小写化netloc
        assert "example.com" in result

class TestIsSafeURL:
    def test_safe_url(self):
        safe, reason = is_safe_url("https://example.com")
        assert safe == True
    
    def test_unsafe_url(self):
        safe, reason = is_safe_url("javascript:alert(1)")
        assert safe == False
        assert len(reason) > 0
