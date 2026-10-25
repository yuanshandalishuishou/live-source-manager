#!/usr/bin/env python3
"""UI 静态资源压缩管线（rjsmin / rcssmin，纯 Python，无 Node 依赖）。

用法：
    python tools/build_ui.py          # 生成/更新 .min.js、.min.css 产物
    python tools/build_ui.py --check  # 校验产物与源文件一致（新鲜度），不写入，供 CI/测试守门

约定：
- 源文件：web/static/js/**/*.js 与 web/static/css/**/*.css（排除 *.min.* 自身）；
- 产物与源文件同目录、同名加 .min 后缀，随仓库提交，Docker 构建零额外步骤；
- 模板只引用 .min 产物；修改源文件后必须重新运行本脚本，否则 --check 失败。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / 'web' / 'static' / 'js'
CSS_DIR = ROOT / 'web' / 'static' / 'css'


def iter_sources() -> list[Path]:
    """全部待压缩源文件（.min 产物与 vendor 不纳入）。"""
    srcs = [p for p in JS_DIR.rglob('*.js') if not p.name.endswith('.min.js')]
    srcs += [p for p in CSS_DIR.rglob('*.css') if not p.name.endswith('.min.css')]
    return sorted(srcs)


def minified_path(src: Path) -> Path:
    return src.with_name(src.stem + '.min' + src.suffix)


def minify_text(src: Path, text: str) -> str:
    if src.suffix == '.js':
        import rjsmin

        return rjsmin.jsmin(text)
    import rcssmin

    return rcssmin.cssmin(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只校验产物新鲜度，不写入')
    args = parser.parse_args()

    sources = iter_sources()
    if not sources:
        print('未找到任何源文件', file=sys.stderr)
        return 2

    stale: list[str] = []
    total_src = total_min = 0
    for src in sources:
        # 统一按 \n 读取/写出，保证跨平台产物字节一致（新鲜度可比较）
        text = src.read_text(encoding='utf-8')
        expected = minify_text(src, text)
        mini = minified_path(src)
        total_src += len(text.encode('utf-8'))
        total_min += len(expected.encode('utf-8'))

        if args.check:
            if not mini.exists():
                stale.append(f'{mini.relative_to(ROOT)}（缺失）')
            elif mini.read_text(encoding='utf-8') != expected:
                stale.append(f'{mini.relative_to(ROOT)}（与源文件不一致）')
        else:
            mini.write_text(expected, encoding='utf-8', newline='\n')

    if args.check:
        if stale:
            print('压缩产物过期，请运行 python tools/build_ui.py 重新生成：', file=sys.stderr)
            for item in stale:
                print(f'  - {item}', file=sys.stderr)
            return 1
        print(f'[PASS] {len(sources)} 个压缩产物均为最新')
        return 0

    saved = (1 - total_min / total_src) * 100 if total_src else 0
    print(f'已生成 {len(sources)} 个压缩产物：{total_src / 1024:.1f}KB -> {total_min / 1024:.1f}KB（减小 {saved:.1f}%）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
