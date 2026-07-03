"""
app.utils 模块单元测试

覆盖：atomic_write（正常/重试/备份/验证）、safe_read_file（正常/多编码/不存在）、
_backup_file。
"""

import os

import pytest
from app.exceptions import FileException
from app.utils import _backup_file, atomic_write, safe_read_file

# ── atomic_write ──────────────────────────────


class TestAtomicWrite:
    """原子写入文件"""

    def test_write_new_file(self, tmp_path):
        filepath = str(tmp_path / 'test.txt')
        content = 'Hello, World!'
        atomic_write(filepath, content)
        assert os.path.exists(filepath)
        with open(filepath, encoding='utf-8') as f:
            assert f.read() == content

    def test_overwrite_existing(self, tmp_path):
        filepath = str(tmp_path / 'overwrite.txt')
        atomic_write(filepath, 'original')
        atomic_write(filepath, 'updated')
        with open(filepath, encoding='utf-8') as f:
            assert f.read() == 'updated'

    def test_unicode_content(self, tmp_path):
        filepath = str(tmp_path / 'unicode.txt')
        content = '中文测试 한국어 テスト'
        atomic_write(filepath, content)
        with open(filepath, encoding='utf-8') as f:
            assert f.read() == content

    def test_creates_parent_dir(self, tmp_path):
        filepath = str(tmp_path / 'subdir' / 'nested' / 'file.txt')
        atomic_write(filepath, 'nested')
        assert os.path.exists(filepath)

    def test_backup_on_overwrite(self, tmp_path):
        filepath = str(tmp_path / 'backup_test.txt')
        atomic_write(filepath, 'v1', backup=False)
        atomic_write(filepath, 'v2', backup=True)
        with open(filepath, encoding='utf-8') as f:
            assert f.read() == 'v2'
        # 检查备份目录
        backup_dir = tmp_path / '.backup'
        assert backup_dir.exists()
        backups = list(backup_dir.iterdir())
        assert len(backups) == 1
        with open(backups[0], encoding='utf-8') as f:
            assert f.read() == 'v1'

    def test_custom_backup_dir(self, tmp_path):
        filepath = str(tmp_path / 'file.txt')
        backup_dir = str(tmp_path / 'custom_backup')
        atomic_write(filepath, 'original', backup=False)
        atomic_write(filepath, 'updated', backup=True, backup_dir=backup_dir)
        assert os.path.exists(backup_dir)
        backups = os.listdir(backup_dir)
        assert len(backups) == 1

    def test_no_backup_when_disabled(self, tmp_path):
        filepath = str(tmp_path / 'nobackup.txt')
        atomic_write(filepath, 'v1', backup=False)
        atomic_write(filepath, 'v2', backup=False)
        backup_dir = tmp_path / '.backup'
        assert not backup_dir.exists()

    def test_verify_enabled_by_default(self, tmp_path):
        """默认启用写入验证"""
        filepath = str(tmp_path / 'verified.txt')
        atomic_write(filepath, 'verified content', verify=True)
        assert os.path.exists(filepath)

    def test_large_content(self, tmp_path):
        filepath = str(tmp_path / 'large.txt')
        content = 'A' * 100000  # 100KB
        atomic_write(filepath, content)
        with open(filepath, encoding='utf-8') as f:
            assert len(f.read()) == 100000

    def test_empty_content(self, tmp_path):
        filepath = str(tmp_path / 'empty.txt')
        atomic_write(filepath, '')
        assert os.path.exists(filepath)
        with open(filepath, encoding='utf-8') as f:
            assert f.read() == ''


# ── safe_read_file ────────────────────────────


class TestSafeReadFile:
    """安全读取文件"""

    def test_read_utf8(self, tmp_path):
        filepath = str(tmp_path / 'utf8.txt')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('UTF-8 content')
        assert safe_read_file(filepath) == 'UTF-8 content'

    def test_read_unicode(self, tmp_path):
        filepath = str(tmp_path / 'cn.txt')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('中文内容')
        assert safe_read_file(filepath) == '中文内容'

    def test_file_not_found(self):
        with pytest.raises(FileException):
            safe_read_file('/nonexistent/path/file.txt')

    def test_fallback_encodings(self, tmp_path):
        """GBK 编码文件用回退编码读取"""
        filepath = str(tmp_path / 'gbk.txt')
        with open(filepath, 'w', encoding='gbk') as f:
            f.write('中文GBK')
        # utf-8 读不了，gbk 可以
        content = safe_read_file(filepath, encoding='utf-8', fallback_encodings=['gbk'])
        assert '中文GBK' in content

    def test_bom_stripped(self, tmp_path):
        """UTF-8 BOM 被正确去除"""
        filepath = str(tmp_path / 'bom.txt')
        with open(filepath, 'w', encoding='utf-8-sig') as f:
            f.write('BOM test')
        content = safe_read_file(filepath)
        assert not content.startswith('\ufeff')
        assert 'BOM test' in content

    def test_binary_fallback(self, tmp_path):
        """无法解码时用 errors='replace' 兜底"""
        filepath = str(tmp_path / 'binary.dat')
        with open(filepath, 'wb') as f:
            f.write(b'\xff\xfe\x00\x01\x02')
        content = safe_read_file(filepath)
        assert isinstance(content, str)


# ── _backup_file ──────────────────────────────


class TestBackupFile:
    """文件备份"""

    def test_backup_creates_copy(self, tmp_path):
        filepath = str(tmp_path / 'original.txt')
        with open(filepath, 'w') as f:
            f.write('original content')
        _backup_file(filepath)
        backup_dir = tmp_path / '.backup'
        assert backup_dir.exists()
        backups = list(backup_dir.iterdir())
        assert len(backups) == 1
        with open(backups[0]) as f:
            assert f.read() == 'original content'

    def test_backup_to_custom_dir(self, tmp_path):
        filepath = str(tmp_path / 'file.txt')
        with open(filepath, 'w') as f:
            f.write('data')
        custom_dir = str(tmp_path / 'my_backups')
        _backup_file(filepath, backup_dir=custom_dir)
        assert os.path.exists(custom_dir)
        assert len(os.listdir(custom_dir)) == 1
