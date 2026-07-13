"""
首启建库 + 默认值 集成测试（回归保护 / CI 可跑）

模拟『初次部署』的完整首启流程：
    init_db(None) -> seed_app_config_defaults() -> fill_missing_app_config_defaults()

覆盖：
  1. 自动建库建表（所有业务表齐全）
  2. 首次部署自动创建 admin 用户并生成强随机密码（满足 GB/T 39786-2021 复杂度）
  3. app_config 写入全部 Config._DEFAULT_VALUES 作为默认值
  4. 自动扫描 4 字段默认值正确
  5. 默认值键的 section 前缀必须存在于 web.core.SECTION_SCHEMA（防『加了默认值却漏 schema』漂移）
  6. 幂等：重复首启不重生密码、不重复写默认值、不丢失用户已改配置

隔离性：本测试把 web.models.DB_PATH 重定位到独立临时目录，
        完全不触碰 conftest 共享库；teardown 还原，避免影响其它测试。
"""

import os
import re
import shutil
import sqlite3
import tempfile

import pytest
from app import Config
from web import models

# ── 业务表清单（首次部署 init_db 必须建出的表；sqlite_sequence 为内部表，不计入） ──
EXPECTED_TABLES = {
    'users',
    'audit_logs',
    'app_config',
    'sessions',
    'classification_dimensions',
    'classification_rules',
    'province_exclusion_map',
    'stream_source_categories',
    'channel_name_mapping',
    'category_dictionary',
    'github_download_cache',
}

AUTO_SCAN_KEYS = [
    'Testing.auto_scan_enabled',
    'Testing.auto_scan_mode',
    'Testing.auto_scan_interval_hours',
    'Testing.auto_scan_daily_time',
]


@pytest.fixture
def first_init_db():
    """把 web.models 的数据库重定位到独立临时目录，模拟『全新库首次部署』。

    返回临时 DB 文件路径；teardown 还原原始 DB_PATH/DATA_DIR，避免污染其它测试。
    """
    original_data_dir = models.DATA_DIR
    original_db_path = models.DB_PATH

    tmp_dir = tempfile.mkdtemp(prefix='first_init_test_')
    tmp_db = os.path.join(tmp_dir, 'web.db')
    models.DATA_DIR = tmp_dir
    models.DB_PATH = tmp_db

    try:
        yield tmp_db
    finally:
        # 还原，保证后续测试仍使用 conftest 共享库
        models.DATA_DIR = original_data_dir
        models.DB_PATH = original_db_path
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _run_first_init():
    """执行与 core.lifespan 完全一致的真实首启三步。

    注意：init_db 现已自包含写入 app_config 默认值（首次部署无需等待 Web 启动），
    故显式 seed_app_config_defaults() 在表非空时幂等跳过；seed_n 取首启后 DB 实际行数。
    """
    pw = models.init_db(None)  # 首次部署：不传密码 -> 自动生成强密码 + 自包含灌入默认值
    models.seed_app_config_defaults()  # 幂等：表非空时跳过
    fill_n = models.fill_missing_app_config_defaults()
    conn = sqlite3.connect(models.DB_PATH)
    try:
        seed_n = conn.execute('SELECT COUNT(*) FROM app_config').fetchone()[0]
    finally:
        conn.close()
    return pw, seed_n, fill_n


def _list_tables(db_path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _password_complexity_ok(pw: str) -> bool:
    """GB/T 39786-2021：>=8 位且至少含 3 类字符（小写/大写/数字/特殊）"""
    if len(pw) < 8:
        return False
    classes = 0
    if re.search(r'[a-z]', pw):
        classes += 1
    if re.search(r'[A-Z]', pw):
        classes += 1
    if re.search(r'[0-9]', pw):
        classes += 1
    if re.search(r'[^A-Za-z0-9]', pw):
        classes += 1
    return classes >= 3


@pytest.mark.integration
class TestFirstInitTablesAndUser:
    """① 建表 + ② 自动生成 admin 强密码"""

    def test_all_tables_created(self, first_init_db):
        _run_first_init()
        tables = _list_tables(first_init_db)
        missing = EXPECTED_TABLES - tables
        assert not missing, f'首启缺失数据表: {sorted(missing)}'

    def test_admin_user_created_with_admin_role(self, first_init_db):
        _run_first_init()
        conn = sqlite3.connect(first_init_db)
        try:
            admin = conn.execute("SELECT username, role FROM users WHERE username='admin'").fetchone()
        finally:
            conn.close()
        assert admin is not None, '首启未创建 admin 用户'
        assert admin[1] == 'admin', f'admin 角色应为 admin，实为 {admin[1]}'

    def test_viewer_user_not_created(self, first_init_db):
        _run_first_init()
        conn = sqlite3.connect(first_init_db)
        try:
            viewer = conn.execute("SELECT 1 FROM users WHERE username='viewer'").fetchone()
        finally:
            conn.close()
        assert viewer is None, '首启不应创建已废弃的 viewer 用户'

    def test_admin_password_auto_generated(self, first_init_db, capsys):
        _run_first_init()
        captured = capsys.readouterr().out
        match = re.search(r'^ADMIN_PASSWORD_INITIALIZED=(\S+)$', captured, re.MULTILINE)
        assert match, '首启未通过 stdout 输出 ADMIN_PASSWORD_INITIALIZED='
        pw = match.group(1)
        assert _password_complexity_ok(pw), f'自动生成的密码复杂度不足: {pw}'


@pytest.mark.integration
class TestFirstInitDefaults:
    """③ 默认值全量写入 + ④ 自动扫描字段 + ⑤ section↔schema 一致性"""

    def test_all_default_values_written(self, first_init_db):
        _run_first_init()
        total = len(Config._DEFAULT_VALUES)
        conn = sqlite3.connect(first_init_db)
        try:
            count = conn.execute('SELECT COUNT(*) FROM app_config').fetchone()[0]
            db_keys = {r[0] for r in conn.execute('SELECT key FROM app_config').fetchall()}
        finally:
            conn.close()
        assert count == total, f'app_config 默认值条数 {count} != Config._DEFAULT_VALUES {total}'
        missing = set(Config._DEFAULT_VALUES) - db_keys
        assert not missing, f'以下默认值键未写入 app_config: {sorted(missing)}'

    def test_auto_scan_defaults_present_and_correct(self, first_init_db):
        _run_first_init()
        conn = sqlite3.connect(first_init_db)
        try:
            rows = {
                r[0]: r[1]
                for r in conn.execute(
                    'SELECT key, value FROM app_config WHERE key IN ({})'.format(','.join('?' * len(AUTO_SCAN_KEYS))),
                    AUTO_SCAN_KEYS,
                ).fetchall()
            }
        finally:
            conn.close()
        assert set(rows) == set(AUTO_SCAN_KEYS), '自动扫描 4 个默认值键缺失'
        assert rows['Testing.auto_scan_enabled'] == 'False'
        assert rows['Testing.auto_scan_mode'] == 'interval'
        assert rows['Testing.auto_scan_interval_hours'] == '24'
        assert rows['Testing.auto_scan_daily_time'] == '03:00'

    def test_default_sections_exist_in_schema(self, first_init_db):
        """每个默认值键的 section 前缀必须存在于 web.core.SECTION_SCHEMA。

        防止『在 Config._DEFAULT_VALUES 加了默认值，却漏掉 SECTION_SCHEMA 定义』
        导致配置中心 UI 不渲染、/api/config 缺项。
        """
        from web.core import SECTION_SCHEMA

        schema_sections = set(SECTION_SCHEMA.keys())
        for key in Config._DEFAULT_VALUES:
            section = key.split('.', 1)[0]
            assert section in schema_sections, (
                f'默认值键 {key!r} 的 section {section!r} 不在 SECTION_SCHEMA 中，'
                '须同步到 web/core.py 的 SECTION_SCHEMA'
            )


@pytest.mark.integration
class TestFirstInitIdempotency:
    """⑥ 幂等：重复首启安全，不覆盖、不重生、不丢失"""

    def test_repeat_init_does_not_regenerate_password(self, first_init_db, capsys):
        _run_first_init()
        capsys.readouterr()  # 丢弃首次输出
        pw2 = models.init_db(None)  # 二次首启（用户已存在）
        assert pw2 is None, '二次首启不应再返回密码（不应重生 admin 密码）'
        captured = capsys.readouterr().out
        assert 'ADMIN_PASSWORD_INITIALIZED=' not in captured, '二次首启不应再打印初始密码'

    def test_repeat_seed_is_noop(self, first_init_db):
        pw, seed_n, fill_n = _run_first_init()
        assert pw is not None
        assert seed_n == len(Config._DEFAULT_VALUES)
        # 二次 seed 应跳过（表非空）
        seed_n2 = models.seed_app_config_defaults()
        assert seed_n2 == 0, '重复 seed 应幂等返回 0'
        # 二次 fill 不应产生新行（无缺失键）
        fill_n2 = models.fill_missing_app_config_defaults()
        assert fill_n2 == 0, '首次 seed 完整后，重复 fill 应返回 0'

    def test_config_row_count_stable_after_repeat(self, first_init_db):
        _run_first_init()
        conn = sqlite3.connect(first_init_db)
        try:
            before = conn.execute('SELECT COUNT(*) FROM app_config').fetchone()[0]
        finally:
            conn.close()
        # 再次跑全套首启流程
        models.init_db(None)
        models.seed_app_config_defaults()
        models.fill_missing_app_config_defaults()
        conn = sqlite3.connect(first_init_db)
        try:
            after = conn.execute('SELECT COUNT(*) FROM app_config').fetchone()[0]
        finally:
            conn.close()
        assert after == before, f'重复首启后 app_config 行数变化: {before} -> {after}'


@pytest.mark.integration
class TestOutputFileInLocalSources:
    """⑦ 首启默认将生成的输出文件加入本地文件源（Sources.local_dirs）"""

    def test_output_file_added_to_local_dirs(self, first_init_db):
        """首启后 Sources.local_dirs 必须包含 ./www/output/<Output.filename>。"""
        _run_first_init()
        cfg = Config()
        dirs = cfg.get_sources().get('local_dirs', [])
        out_dir = cfg.get('Output', 'output_dir', './www/output')
        fname = cfg.get('Output', 'filename', 'live.m3u')
        rel = os.path.normpath(os.path.join(out_dir, fname))
        if not (os.path.isabs(rel) or rel.startswith('.')):
            rel = './' + rel
        assert rel in dirs, f'首启未将输出文件默认加入本地源: 期望 {rel!r} 在 {dirs!r} 中'

    def test_output_file_addition_idempotent(self, first_init_db):
        """重复首启不应重复添加输出文件（local_dirs 中仅出现一次）。"""
        _run_first_init()
        cfg = Config()
        dirs1 = cfg.get_sources().get('local_dirs', [])
        # 二次首启
        models.init_db(None)
        models.seed_app_config_defaults()
        models.fill_missing_app_config_defaults()
        dirs2 = cfg.get_sources().get('local_dirs', [])
        rel = os.path.normpath(
            os.path.join(
                cfg.get('Output', 'output_dir', './www/output'),
                cfg.get('Output', 'filename', 'live.m3u'),
            )
        )
        if not (os.path.isabs(rel) or rel.startswith('.')):
            rel = './' + rel
        assert dirs2.count(rel) == 1, f'输出文件路径在 local_dirs 应只出现一次，实为 {dirs2.count(rel)} 次: {dirs2!r}'
        # 列表长度不应因二次首启增长（仅可能追加一次，但这里首次已含）
        assert len(dirs2) == len(dirs1), f'二次首启改变 local_dirs 长度: {dirs1} -> {dirs2}'


import tempfile as _tf

from app.source_manager import SourceManager


@pytest.mark.integration
class TestParseLocalFilesSupportsFilePath:
    """parse_local_files 应同时支持目录与单个源文件路径"""

    def _make_sm(self):
        from app import ChannelRules

        cfg = Config()
        import logging as _logging

        _logging.basicConfig(level=_logging.CRITICAL)
        sm = SourceManager(cfg, _logging.getLogger('test'), ChannelRules())
        return sm

    def test_parse_single_m3u_file(self):
        """传入单个 .m3u 文件路径应直接解析并返回频道，不抛 NotADirectoryError。"""
        d = _tf.mkdtemp(prefix='parse_file_test_')
        try:
            fpath = os.path.join(d, 'sample.m3u')
            with open(fpath, 'w', encoding='utf-8') as fh:
                fh.write(
                    '#EXTM3U\n'
                    '#EXTINF:-1 tvg-name="CCTV1" group-title="央视",CCTV1\n'
                    'http://example.com/cctv1.m3u8\n'
                    '#EXTINF:-1 tvg-name="CCTV2" group-title="央视",CCTV2\n'
                    'http://example.com/cctv2.m3u8\n'
                )
            sm = self._make_sm()
            sources = sm.parse_local_files(fpath)
            assert len(sources) == 2, f'应解析出 2 个频道，实为 {len(sources)}'
            names = {s['name'] for s in sources}
            assert names == {'CCTV1', 'CCTV2'}, f'频道名不匹配: {names}'
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_parse_directory_unchanged(self):
        """目录仍按原行为遍历解析。"""
        d = _tf.mkdtemp(prefix='parse_dir_test_')
        try:
            os.makedirs(os.path.join(d, 'sub'))
            with open(os.path.join(d, 'sub', 'a.m3u'), 'w', encoding='utf-8') as fh:
                fh.write('#EXTM3U\n#EXTINF:-1,C1\nhttp://example.com/c1.m3u8\n')
            sm = self._make_sm()
            sources = sm.parse_local_files(d)
            assert len(sources) == 1, f'目录解析应返回 1 个频道，实为 {len(sources)}'
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_parse_nonexistent_path_returns_empty(self):
        """不存在的路径应安全返回空列表而非抛错。"""
        sm = self._make_sm()
        sources = sm.parse_local_files('/nonexistent/path/that/should/not/exist')
        assert sources == [], f'不存在路径应返回空列表，实为 {sources!r}'
