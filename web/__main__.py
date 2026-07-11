#!/usr/bin/env python3
"""
Web 管理服务入口 — 支持 python -m web

使用方式:
  python -m web                    # 默认端口 23456
  python -m web --port 8080        # 自定义端口
  python -m web --host 127.0.0.1   # 自定义监听地址
  python -m web --install-deps     # 自动安装依赖后退出
"""

import os
import subprocess
import sys


def _install_deps() -> None:
    """通过 pip 自动安装 requirements.txt 中的依赖。"""
    req_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'requirements.txt')
    if not os.path.exists(req_path):
        print(f'✗ 未找到 requirements.txt: {req_path}')
        sys.exit(1)
    print(f'正在安装依赖（来自 {req_path}）...')
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-r', req_path],
        capture_output=False,
    )
    if result.returncode == 0:
        print('✓ 依赖安装完成')
    else:
        print('✗ 依赖安装失败')
    sys.exit(result.returncode)


if __name__ == '__main__':
    if '--install-deps' in sys.argv:
        _install_deps()
    else:
        from web.webapp import main

        main()
