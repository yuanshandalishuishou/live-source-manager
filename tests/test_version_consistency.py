#!/usr/bin/env python3
"""版本号一致性测试（D-02 / D-03）

单一来源原则：app.__version__ 是唯一版本号来源，以下位置必须与之保持一致：
  1. pyproject.toml [project].version
  2. web.core FastAPI 应用实例的 app.version

历史上三处各自硬编码（1.0.0 / 2.0.0 / 3.0）互不一致，本测试守护不再回退。
"""

import os
import re
import tomllib

from app import __version__

_PYPROJECT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'pyproject.toml')


def _read_pyproject_version() -> str:
    """读取 pyproject.toml [project].version 字段。"""
    with open(_PYPROJECT_PATH, 'rb') as f:
        data = tomllib.load(f)
    return str(data['project']['version'])


def test_app_version_is_semver_like():
    """app.__version__ 应为 X.Y.Z 三段式语义化版本号。"""
    assert re.fullmatch(r'\d+\.\d+\.\d+', __version__), f'__version__ 不是三段式版本号: {__version__!r}'


def test_pyproject_version_matches_app_version():
    """pyproject.toml version 必须等于 app.__version__。"""
    assert _read_pyproject_version() == __version__


def test_fastapi_app_version_matches_app_version():
    """web.core 中 FastAPI 实例 version 必须等于 app.__version__。"""
    from web.core import app

    assert app.version == __version__
