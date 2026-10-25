#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LSM Web UI 质量门禁（UI 优化增量设计 D5：纯 Python 标准库，不引入 node 生态）。

检查规则（T01 初版 → T05 全量）：

  ERROR（硬失败，任何模式退出码均为 1）：
    E1  未定义 CSS 类引用 —— 模板 class 属性中使用、但无任何 CSS 定义（允许清单除外）
    E2  未定义 CSS 变量引用 —— var(--x) 无兜底值且未定义
    E3  CDN 引用 —— <script src>/<link href> 指向 http(s):// （htmx 必须本地化）

  WARNING（报告模式仅提示；--strict 模式下按失败计，T05 起默认启用）：
    W1  原生弹窗 confirm/prompt/alert —— 应统一走 LSM.confirm/prompt/showToast
    W2  console.log —— 生产遗留调试输出
    W3  内联 <script>/<style> 块超过 20 行 —— T05 目标：各页仅留 <20 行数据挂钩

  扫描范围（T01 初版）：web/templates/ + web/static/css/。
  T05 全量化扩展点：iter_js_files() 已预留（web/static/js/），
  届时将原生弹窗/console.log/CDN/类引用检查扩展至 JS 字符串。

用法：
    python tools/check_ui.py             # 报告模式（WARNING 不影响退出码）
    python tools/check_ui.py --strict    # 严格模式（WARNING 亦失败）
"""
from __future__ import annotations

import glob
import os
import re
import sys

# ── 路径配置（相对于仓库根目录） ──────────────────────────
TEMPLATE_GLOB = 'web/templates/*.html'
CSS_GLOB = 'web/static/css/**/*.css'
JS_GLOB = 'web/static/js/**/*.js'  # T05 起纳入扫描

# ── 已知结构化钩子类允许清单 ─────────────────────────────
# 这些类作为 JS 查询作用域 / 语义页面标识使用，刻意不配样式（非缺陷）；
# 标注 [T04]/[T05] 的条目将随对应批次迁移/删除后从本清单移除。
KNOWN_HOOK_CLASSES = {
    'audit-page',        # audit.html 页面包装（T05 删除死模板后移除）
    'config-page',       # config.html JS 作用域钩子
    'dashboard',         # dashboard.html 页面包装
    'form',              # 表单语义钩子（样式由 .form-* 家族承载）
    'login-form',        # login.html JS 查询钩子（querySelector('.login-form')）
    'logs-page',         # logs.html JS 作用域钩子（.logs-page .tabs .tab-btn）
    'modal-body',        # sources.html（T04 Modal 迁移后移除）
    'modal-footer',      # sources.html:113 仅内联兜底（T04 LSM.showModal 自绘页脚后移除）
    'rules-page',        # rules.html 页面包装
    'source-form-page',  # source_form.html（T05 删除孤岛模板后移除）
    'sources-page',      # sources.html 页面包装
    'test-page',         # livetest.html 页面包装
    'users-page',        # users.html 页面包装
}

# 内联块行数上限（W3）
INLINE_BLOCK_MAX_LINES = 20

# ── 正则 ─────────────────────────────────────────────────
STYLE_BLOCK_RE = re.compile(r'<style[^>]*>(.*?)</style>', re.S | re.I)
SCRIPT_BLOCK_RE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S | re.I)
JINJA_RE = re.compile(r'\{[{%].*?[%}]\}', re.S)
CLASS_ATTR_RE = re.compile(r'class\s*=\s*"([^"]*)"')
CLASS_TOKEN_RE = re.compile(r'^[a-zA-Z_][\w-]*$')
CSS_CLASS_RE = re.compile(r'\.([a-zA-Z_][\w-]*)')
CSS_VAR_DEF_RE = re.compile(r'(--[\w-]+)\s*:')
VAR_REF_RE = re.compile(r'var\(\s*(--[\w-]+)\s*\)')          # 无兜底引用
VAR_REF_FALLBACK_RE = re.compile(r'var\(\s*(--[\w-]+)\s*,')  # 带兜底引用（仅提示）
CDN_SCRIPT_RE = re.compile(r'<script[^>]+src\s*=\s*"(https?://[^"]*)"', re.I)
CDN_LINK_RE = re.compile(r'<link[^>]+href\s*=\s*"(https?://[^"]*)"', re.I)
NATIVE_DIALOG_RE = re.compile(r'(?<![\w.])(confirm|prompt|alert)\s*\(')
CONSOLE_LOG_RE = re.compile(r'console\.log\s*\(')


def iter_template_files(root: str) -> list[str]:
    """返回全部模板文件路径（相对 root）。"""
    return sorted(os.path.relpath(p, root) for p in glob.glob(os.path.join(root, TEMPLATE_GLOB)))


def iter_css_files(root: str) -> list[str]:
    """返回全部 CSS 文件路径（含 static/css 子目录）。"""
    return sorted(
        os.path.relpath(p, root)
        for p in glob.glob(os.path.join(root, CSS_GLOB), recursive=True)
    )


def iter_js_files(root: str) -> list[str]:
    """返回全部 JS 文件路径（T05 起纳入扫描，当前仅预留）。"""
    return sorted(
        os.path.relpath(p, root)
        for p in glob.glob(os.path.join(root, JS_GLOB), recursive=True)
    )


def collect_css_definitions(css_texts: dict[str, str]) -> tuple[set[str], set[str]]:
    """从 CSS 文本集合中提取已定义的类名与变量名。

    Args:
        css_texts: {来源标识: CSS 文本}，来源含静态 CSS 文件与模板内联 <style> 块。

    Returns:
        (已定义类名集合, 已定义变量名集合)
    """
    classes: set[str] = set()
    variables: set[str] = set()
    for text in css_texts.values():
        classes.update(CSS_CLASS_RE.findall(text))
        variables.update(CSS_VAR_DEF_RE.findall(text))
    return classes, variables


def strip_html(text: str) -> str:
    """剥离脚本/样式块与 Jinja 表达式，仅保留静态 HTML 结构。"""
    text = SCRIPT_BLOCK_RE.sub('', text)
    text = STYLE_BLOCK_RE.sub('', text)
    return JINJA_RE.sub(' ', text)


def scan_templates(root: str) -> dict[str, list[str]]:
    """扫描模板，返回分类后的违规清单。

    Returns:
        {类别: [人类可读的违规描述]}
    """
    findings: dict[str, list[str]] = {
        'undefined_class': [],
        'undefined_var': [],
        'cdn': [],
        'native_dialog': [],
        'console_log': [],
        'inline_block': [],
        'var_fallback': [],
    }

    css_texts: dict[str, str] = {}
    for path in iter_css_files(root):
        css_texts[path] = read_text(os.path.join(root, path))
    template_files = iter_template_files(root)
    for path in template_files:
        text = read_text(os.path.join(root, path))
        for match in STYLE_BLOCK_RE.finditer(text):
            css_texts[f'{path}::style'] = match.group(1)

    defined_classes, defined_vars = collect_css_definitions(css_texts)

    for path in template_files:
        text = read_text(os.path.join(root, path))
        html = strip_html(text)

        # E1 未定义类（HTML class 属性；JS 动态拼接类不在静态扫描范围）
        for match in CLASS_ATTR_RE.finditer(html):
            for token in match.group(1).split():
                if not CLASS_TOKEN_RE.match(token):
                    continue  # 过滤 Jinja 残片等非标识符 token
                if token not in defined_classes and token not in KNOWN_HOOK_CLASSES:
                    findings['undefined_class'].append(f'{path}: class="{token}" 无 CSS 定义')

        # E2 未定义变量（无兜底引用才计违规；带兜底仅提示）
        for match in VAR_REF_RE.finditer(text):
            name = match.group(1)
            if name not in defined_vars:
                findings['undefined_var'].append(f'{path}: var({name}) 未定义且无兜底值')
        for match in VAR_REF_FALLBACK_RE.finditer(text):
            name = match.group(1)
            if name not in defined_vars:
                findings['var_fallback'].append(f'{path}: var({name}, ...) 未定义（有兜底，建议补定义）')

        # E3 CDN 引用（script/link；正文外链 <a href> 属正常文档链接，不检查）
        for pattern, label in ((CDN_SCRIPT_RE, 'script'), (CDN_LINK_RE, 'link')):
            for match in pattern.finditer(text):
                findings['cdn'].append(f'{path}: <{label} src="{match.group(1)}"> 应改为本地引用')

        # W1 原生弹窗
        for lineno, line in enumerate(text.splitlines(), start=1):
            if NATIVE_DIALOG_RE.search(line):
                findings['native_dialog'].append(f'{path}:{lineno}: {line.strip()[:80]}')

        # W2 console.log
        for lineno, line in enumerate(text.splitlines(), start=1):
            if CONSOLE_LOG_RE.search(line):
                findings['console_log'].append(f'{path}:{lineno}: {line.strip()[:80]}')

        # W3 内联块行数
        for block_re, label in ((SCRIPT_BLOCK_RE, 'script'), (STYLE_BLOCK_RE, 'style')):
            for match in block_re.finditer(text):
                lines = match.group(1).count('\n') + 1
                if lines > INLINE_BLOCK_MAX_LINES:
                    findings['inline_block'].append(f'{path}: 内联 <{label}> 块 {lines} 行（上限 {INLINE_BLOCK_MAX_LINES}）')

    # CSS 文件自身的未定义变量引用（跨文件变量池）
    for name, text in css_texts.items():
        if name.endswith('::style'):
            continue  # 模板内联样式已随模板扫描
        for match in VAR_REF_RE.finditer(text):
            var_name = match.group(1)
            if var_name not in defined_vars:
                findings['undefined_var'].append(f'{name}: var({var_name}) 未定义且无兜底值')

    return findings


def read_text(path: str) -> str:
    """读取文本文件（UTF-8，容错 BOM）。"""
    with open(path, encoding='utf-8-sig') as handle:
        return handle.read()


ERROR_CATEGORIES = ('undefined_class', 'undefined_var', 'cdn')
WARNING_CATEGORIES = ('native_dialog', 'console_log', 'inline_block')
INFO_CATEGORIES = ('var_fallback',)

CATEGORY_TITLES = {
    'undefined_class': '未定义 CSS 类引用（E1）',
    'undefined_var': '未定义 CSS 变量引用（E2）',
    'cdn': 'CDN 引用（E3）',
    'native_dialog': '原生弹窗 confirm/prompt/alert（W1，T03/T04 批清理）',
    'console_log': 'console.log（W2，T04 批清理）',
    'inline_block': '内联 script/style 块超行（W3，T05 清零）',
    'var_fallback': '未定义变量带兜底引用（提示，建议补定义）',
}


def main(argv: list[str]) -> int:
    strict = '--strict' in argv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    findings = scan_templates(root)

    print('═══ LSM Web UI 质量门禁 ═══')
    print(f'模式: {"严格（WARNING 计入失败）" if strict else "报告（WARNING 仅提示）"}')
    print(f'扫描: {len(iter_template_files(root))} 个模板 / {len(iter_css_files(root))} 个 CSS 文件')
    print()

    error_count = 0
    for category in ERROR_CATEGORIES:
        items = findings[category]
        status = 'FAIL' if items else 'PASS'
        print(f'[{status}] {CATEGORY_TITLES[category]}: {len(items)} 处')
        for item in items:
            print(f'    - {item}')
        error_count += len(items)

    warning_count = 0
    for category in WARNING_CATEGORIES:
        items = findings[category]
        print(f'[{"FAIL" if strict and items else "WARN"}] {CATEGORY_TITLES[category]}: {len(items)} 处')
        for item in items:
            print(f'    - {item}')
        warning_count += len(items)

    for category in INFO_CATEGORIES:
        items = findings[category]
        print(f'[INFO] {CATEGORY_TITLES[category]}: {len(items)} 处')
        for item in items:
            print(f'    - {item}')

    print()
    print(f'小结: ERROR {error_count} 处 / WARNING {warning_count} 处')
    if error_count or (strict and warning_count):
        print('结果: 未通过')
        return 1
    print('结果: 通过')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
