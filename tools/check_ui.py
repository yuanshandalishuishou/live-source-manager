#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LSM Web UI 质量门禁（UI 优化增量设计 D5：纯 Python 标准库，不引入 node 生态）。

检查规则（T01 初版 → T05 全量化：扫描范围扩展至 web/static/js/）：

  ERROR（硬失败，任何模式退出码均为 1）：
    E1  未定义 CSS 类引用 —— 模板 class 属性 / JS 字符串 HTML 中使用、
        但无任何 CSS 定义（允许清单除外）
    E2  未定义 CSS 变量引用 —— var(--x) 无兜底值且未定义
    E3  CDN 引用 —— <script src>/<link href> 或 JS 动态资源 src 指向 http(s)://
        （htmx 必须本地化）

  WARNING（报告模式仅提示；--strict 模式下按失败计，T05 起默认启用）：
    W1  原生弹窗 confirm/prompt/alert —— 应统一走 LSM.confirm/prompt/showToast
    W2  console.log —— 生产遗留调试输出
    W3  内联 <script>/<style> 块超过 20 行 —— 各页仅留 <20 行数据挂钩

  扫描范围：web/templates/ + web/static/css/ + web/static/js/（T05 起）。
  JS 侧注意事项：
    - 类引用只校验「纯字面量」class 属性；字符串拼接（含 '+' / '${}' 残片）
      的属性整体跳过，避免把 JS 变量名误报为未定义类；
    - W1/W2 按行扫描，跳过整行 // 注释与 /* */ 块注释，并剔除
      function confirm/prompt/alert( 声明（lsm-components.js 的组件实现）；
    - E3 检查动态资源注入模式 src='http(s)://...'（正文超链接不检查）。

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
JS_GLOB = 'web/static/js/**/*.js'  # T05 起纳入扫描（不含 vendor/）

# ── 已知结构化钩子类允许清单 ─────────────────────────────
# 这些类作为 JS 查询作用域 / 语义页面标识使用，刻意不配样式（非缺陷）。
KNOWN_HOOK_CLASSES = {
    'config-page',           # config.html JS 作用域钩子
    'config-section',        # config.js 渲染的分区卡片语义标记（无独立样式）
    'dashboard',             # dashboard.html 页面包装
    'field-validation',      # config.js 字段校验容器（id=fv-*，className 切 form-error）
    'form',                  # 表单语义钩子（样式由 .form-* 家族承载）
    'login-form',            # login.html JS 查询钩子（querySelector('.login-form')）
    'logs-page',             # logs.html JS 作用域钩子（.logs-page .tabs .tab-btn）
    'modal-body',            # sources.html 静态 Modal 结构钩子（Q5：静态弹窗保留）
    'modal-footer',          # sources.html 静态 Modal 页脚钩子（Q5：静态弹窗保留）
    'rules-page',            # rules.html 页面包装
    'section-save-status',   # config.js 分段保存状态容器（id=ss-*）
    'sources-page',          # sources.html 页面包装
    'stat-total',            # dashboard.js 直播源总数卡片语义标记（无独立样式）
    'test-page',             # livetest.html 页面包装
    'users-page',            # users.html 页面包装
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
# JS 专属：函数声明剔除（lsm-components.js 的 confirm/prompt 实现）
JS_DIALOG_DECL_RE = re.compile(r'\bfunction\s+(?:confirm|prompt|alert)\s*\(')
# JS 专属：动态资源 CDN 注入（src='http(s)://...'）
JS_SRC_CDN_RE = re.compile(r'src\s*=\s*[\'"]https?://')


def iter_template_files(root: str) -> list[str]:
    """返回全部模板文件路径（相对 root）。"""
    return sorted(os.path.relpath(p, root) for p in glob.glob(os.path.join(root, TEMPLATE_GLOB)))


def iter_css_files(root: str) -> list[str]:
    """返回全部 CSS 文件路径（含 static/css 子目录，排除 build_ui.py 的 .min 产物）。"""
    return sorted(
        os.path.relpath(p, root)
        for p in glob.glob(os.path.join(root, CSS_GLOB), recursive=True)
        if not p.endswith('.min.css')
    )


def iter_js_files(root: str) -> list[str]:
    """返回全部第一方 JS 文件路径（T05 起纳入扫描，不含 vendor/ 与 .min 产物）。"""
    return sorted(
        os.path.relpath(p, root)
        for p in glob.glob(os.path.join(root, JS_GLOB), recursive=True)
        if not p.endswith('.min.js')
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


def collect_definitions(root: str) -> tuple[set[str], set[str]]:
    """汇总静态 CSS 与模板内联 <style>，得到全站类/变量定义池。"""
    css_texts: dict[str, str] = {}
    for path in iter_css_files(root):
        css_texts[path] = read_text(os.path.join(root, path))
    for path in iter_template_files(root):
        text = read_text(os.path.join(root, path))
        for match in STYLE_BLOCK_RE.finditer(text):
            css_texts[f'{path}::style'] = match.group(1)
    return collect_css_definitions(css_texts)


def strip_html(text: str) -> str:
    """剥离脚本/样式块与 Jinja 表达式，仅保留静态 HTML 结构。"""
    text = SCRIPT_BLOCK_RE.sub('', text)
    text = STYLE_BLOCK_RE.sub('', text)
    return JINJA_RE.sub(' ', text)


def iter_js_code_lines(text: str) -> list[str]:
    """按行产出 JS 代码：跳过整行 // 注释与 /* */ 块注释。

    刻意不剥离行内注释与字符串内容：URL 等字符串字面量含 '//'，
    通用剥离需完整词法分析，超出质量门禁目标；既有约定 = 行内注释
    不出现 W1/W2 关键词（由代码评审保证）。
    """
    lines: list[str] = []
    in_block = False
    for raw in text.splitlines():
        if in_block:
            end = raw.find('*/')
            if end < 0:
                continue
            raw = raw[end + 2:]
            in_block = False
        if raw.lstrip().startswith('//'):
            continue
        start = raw.find('/*')
        if start >= 0:
            end = raw.find('*/', start + 2)
            if end < 0:
                raw = raw[:start]
                in_block = True
            else:
                raw = raw[:start] + raw[end + 2:]
        lines.append(raw)
    return lines


def iter_static_class_values(code_text: str):
    """产出 JS 代码中「可静态判定」的 (原始值, 令牌列表) 对。

    JS 以字符串拼接构造 HTML，CLASS_ATTR_RE 会跨单引号捕获到
    class="a ' + x + '" 这类拼接残片。判定规则：属性值按空白切分后
    若存在任一非法令牌（'+' / "'" / '${}' 等），说明含动态成分，
    整体跳过——只校验纯字面量类列表。
    """
    for match in CLASS_ATTR_RE.finditer(code_text):
        tokens = match.group(1).split()
        if tokens and all(CLASS_TOKEN_RE.match(t) for t in tokens):
            yield match.group(1), tokens


def new_findings() -> dict[str, list[str]]:
    """创建空的分类违规清单。"""
    return {
        'undefined_class': [],
        'undefined_var': [],
        'cdn': [],
        'native_dialog': [],
        'console_log': [],
        'inline_block': [],
        'var_fallback': [],
    }


def scan_templates(root: str, defined_classes: set[str], defined_vars: set[str]) -> dict[str, list[str]]:
    """扫描模板与 CSS 文件，返回分类后的违规清单。"""
    findings = new_findings()
    template_files = iter_template_files(root)

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
    for path in iter_css_files(root):
        text = read_text(os.path.join(root, path))
        for match in VAR_REF_RE.finditer(text):
            var_name = match.group(1)
            if var_name not in defined_vars:
                findings['undefined_var'].append(f'{path}: var({var_name}) 未定义且无兜底值')

    return findings


def scan_js_files(root: str, defined_classes: set[str], defined_vars: set[str]) -> dict[str, list[str]]:
    """扫描第一方 JS 文件（T05 全量化）：E1/E2/E3/W1/W2。"""
    findings = new_findings()

    for path in iter_js_files(root):
        text = read_text(os.path.join(root, path))
        code = '\n'.join(iter_js_code_lines(text))

        # E1 未定义类（仅纯字面量 class 属性；拼接属性整体跳过）
        for value, tokens in iter_static_class_values(code):
            for token in tokens:
                if token not in defined_classes and token not in KNOWN_HOOK_CLASSES:
                    findings['undefined_class'].append(f'{path}: JS class="{value}" 中 "{token}" 无 CSS 定义')

        # E2 未定义变量（JS 字符串内联样式等）
        for match in VAR_REF_RE.finditer(code):
            name = match.group(1)
            if name not in defined_vars:
                findings['undefined_var'].append(f'{path}: var({name}) 未定义且无兜底值')
        for match in VAR_REF_FALLBACK_RE.finditer(code):
            name = match.group(1)
            if name not in defined_vars:
                findings['var_fallback'].append(f'{path}: var({name}, ...) 未定义（有兜底，建议补定义）')

        # E3 动态资源 CDN 注入（正文超链接/placeholder 不检查）
        for match in JS_SRC_CDN_RE.finditer(code):
            findings['cdn'].append(f'{path}: 动态资源 {match.group(0)} 应改为本地引用')

        # W1 原生弹窗（剔除函数声明后按行匹配）
        cleaned = JS_DIALOG_DECL_RE.sub('', code)
        for lineno, line in enumerate(cleaned.splitlines(), start=1):
            if NATIVE_DIALOG_RE.search(line):
                findings['native_dialog'].append(f'{path}:{lineno}: {line.strip()[:80]}')

        # W2 console.log
        for lineno, line in enumerate(cleaned.splitlines(), start=1):
            if CONSOLE_LOG_RE.search(line):
                findings['console_log'].append(f'{path}:{lineno}: {line.strip()[:80]}')

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
    'native_dialog': '原生弹窗 confirm/prompt/alert（W1）',
    'console_log': 'console.log（W2）',
    'inline_block': '内联 script/style 块超行（W3）',
    'var_fallback': '未定义变量带兜底引用（提示，建议补定义）',
}


def main(argv: list[str]) -> int:
    strict = '--strict' in argv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    defined_classes, defined_vars = collect_definitions(root)
    findings = scan_templates(root, defined_classes, defined_vars)
    for category, items in scan_js_files(root, defined_classes, defined_vars).items():
        findings[category].extend(items)

    print('═══ LSM Web UI 质量门禁 ═══')
    print(f'模式: {"严格（WARNING 计入失败）" if strict else "报告（WARNING 仅提示）"}')
    print(
        f'扫描: {len(iter_template_files(root))} 个模板 / '
        f'{len(iter_css_files(root))} 个 CSS 文件 / '
        f'{len(iter_js_files(root))} 个 JS 文件'
    )
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
        status = 'FAIL' if strict and items else 'WARN'
        print(f'[{status}] {CATEGORY_TITLES[category]}: {len(items)} 处')
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
