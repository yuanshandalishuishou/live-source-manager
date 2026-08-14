#!/usr/bin/env python3
"""生成 CONFIG.md — 配置文档（对标 iptv-api 的配置完备度）。

从 web.core.SECTION_SCHEMA（含 label/描述）与 app.config.Config._DEFAULT_VALUES
产出人类可读的配置参考，并附「环境变量覆盖」说明。
运行：python tools/gen_config_doc.py
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from web.core import SECTION_SCHEMA  # noqa: E402
from app.config import Config  # noqa: E402

TYPE_CN = {
    'str': '字符串',
    'int': '整数',
    'bool': '布尔',
    'textarea': '多行文本',
    'float': '浮点',
}


def env_name(section: str, key: str) -> str:
    return 'LSM_' + '_'.join(p.upper() for p in f'{section}.{key}'.split('.'))


def main():
    lines = []
    lines.append('# 配置参考 (CONFIG.md)')
    lines.append('')
    lines.append('本文件由 `tools/gen_config_doc.py` 自动生成，列出全部配置项、默认值与说明。')
    lines.append('配置单一事实来源为 SQLite `app_config` 表（Web 配置中心实时读写）。')
    lines.append('')
    lines.append('## 环境变量覆盖（对标 iptv-api）')
    lines.append('')
    lines.append('任何配置键均可被环境变量 `LSM_<SECTION>_<KEY>` 覆盖，优先级：**环境变量 > SQLite(用户/默认) > 代码默认**。')
    lines.append('其中 `<SECTION>` 与 `<KEY>` 为配置段名与键名大写，点号 `.` 替换为下划线 `_`。')
    lines.append('')
    lines.append('示例：')
    lines.append('')
    lines.append('```bash')
    lines.append('# 使用 aiohttp 异步测速（而非默认 ffprobe）')
    lines.append('export LSM_TESTING_TEST_METHOD=aiohttp')
    lines.append('')
    lines.append('# 关闭 IPv4/IPv6 分文件发布')
    lines.append('export LSM_OUTPUT_SEPARATE_IPV4_IPV6=False')
    lines.append('')
    lines.append('# 关闭候选池择优闭环')
    lines.append('export LSM_OUTPUT_CANDIDATE_POOL_ENABLED=False')
    lines.append('```')
    lines.append('')
    lines.append('> 注意：环境变量覆盖在进程启动后持续生效，修改需重启服务。')
    lines.append('')

    for section, fields in SECTION_SCHEMA.items():
        lines.append(f'## [{section}]')
        lines.append('')
        lines.append('| 键 | 类型 | 默认值 | 说明 | 环境变量 |')
        lines.append('| --- | --- | --- | --- | --- |')
        for key, field in fields.items():
            ftype = field[0] if len(field) > 0 else ''
            default = field[1] if len(field) > 1 else ''
            label = field[2] if len(field) > 2 else ''
            desc = field[3] if len(field) > 3 else ''
            type_cn = TYPE_CN.get(ftype, ftype)
            # 默认值转义管道符，避免破坏表格
            default_cell = str(default).replace('|', '\\|').replace('\n', ' ')
            desc_cell = str(desc).replace('|', '\\|').replace('\n', ' ')
            lines.append(f'| {key} | {type_cn} | {default_cell} | {label}；{desc_cell} | `{env_name(section, key)}` |')
        lines.append('')

    # 兜底：_DEFAULT_VALUES 中存在但 SCHEMA 未列出的键（理论上不应有），列在末尾
    schema_keys = {(s, k) for s, fs in SECTION_SCHEMA.items() for k in fs}
    extra = [(k, v) for k, v in Config._DEFAULT_VALUES.items() if tuple(k.split('.', 1)) not in schema_keys]
    if extra:
        lines.append('## 其他内部默认键（未暴露在配置中心 UI）')
        lines.append('')
        lines.append('| 键 | 默认值 | 环境变量 |')
        lines.append('| --- | --- | --- |')
        for k, v in extra:
            lines.append(f'| {k} | {str(v).replace(chr(10), " ")} | `{env_name(*k.split(".", 1))}` |')
        lines.append('')

    out_path = os.path.join(PROJECT_ROOT, 'CONFIG.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'已生成 {out_path}（{len(lines)} 行）')


if __name__ == '__main__':
    main()
