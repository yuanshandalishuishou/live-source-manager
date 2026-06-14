import pytest
import os
import tempfile
import sys
sys.path.insert(0, 'app')
from file_utils import atomic_write, safe_read_file

class TestAtomicWrite:
    def test_basic_write_and_read(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            target = f.name
            os.unlink(target)
        try:
            atomic_write(target, "Hello World")
            with open(target, 'r', encoding='utf-8') as f:
                assert f.read() == "Hello World"
        finally:
            if os.path.exists(target):
                os.unlink(target)
    
    def test_write_then_verify(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            target = f.name
            os.unlink(target)
        try:
            content = "测试内容"
            atomic_write(target, content, verify=True)
            assert os.path.exists(target)
        finally:
            if os.path.exists(target):
                os.unlink(target)

class TestSafeReadFile:
    def test_read_existing_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("测试内容")
            target = f.name
        try:
            content = safe_read_file(target)
            assert content == "测试内容"
        finally:
            os.unlink(target)
    
    def test_read_nonexistent_file(self):
        from exceptions import FileException
        with pytest.raises(FileException):
            safe_read_file("/nonexistent/path/file.txt")
