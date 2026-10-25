"""UI 静态资源产物守护（第二轮 UI 性能优化）。

- .min 产物必须与源文件一致（tools/build_ui.py --check）；
- 产物存在且确实小于源文件；
- 模板统一引用 defer + .min 资源，避免回退到未压缩/阻塞式加载。
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_minified_assets_fresh():
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'build_ui.py'), '--check'],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_minified_assets_exist_and_smaller():
    sources = [p for p in (ROOT / 'web' / 'static' / 'js').rglob('*.js') if not p.name.endswith('.min.js')]
    sources += [p for p in (ROOT / 'web' / 'static' / 'css').rglob('*.css') if not p.name.endswith('.min.css')]
    assert len(sources) >= 16, '第一方 JS/CSS 源文件数量异常'
    for src in sources:
        mini = src.with_name(src.stem + '.min' + src.suffix)
        assert mini.exists(), f'缺少压缩产物: {mini.relative_to(ROOT)}'
        assert mini.stat().st_size < src.stat().st_size, f'压缩产物未小于源文件: {mini.relative_to(ROOT)}'


def test_templates_use_deferred_minified_assets():
    for tpl in sorted((ROOT / 'web' / 'templates').glob('*.html')):
        text = tpl.read_text(encoding='utf-8')
        for match in re.finditer(r'<script src="([^"]+)"([^>]*)></script>', text):
            url, attrs = match.groups()
            assert 'defer' in attrs, f'{tpl.name}: {url} 缺少 defer（阻塞首屏渲染）'
            if '/vendor/' not in url:
                assert '.min.js' in url, f'{tpl.name}: {url} 未引用压缩产物'
        for match in re.finditer(r'<link rel="stylesheet" href="([^"]+)"', text):
            assert '.min.css' in match.group(1), f'{tpl.name}: {match.group(1)} 未引用压缩 CSS'
