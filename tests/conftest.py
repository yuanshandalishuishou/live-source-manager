# -*- coding: utf-8 -*-
"""
pytest共享fixtures和配置
"""

import os
import sys
import tempfile

# 确保app目录在路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
