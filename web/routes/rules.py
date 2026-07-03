#!/usr/bin/env python3
"""规则 API — /api/rules/*, /api/channel-mapping*, /api/category-dictionary*"""

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from web import models
from web.core import (
    PROJECT_ROOT,
    app,
    get_current_user,
    require_admin,
)

router = APIRouter()


def _parse_keywords(keywords_raw):
    """将关键词字段解析为 Python 列表"""
    if isinstance(keywords_raw, list):
        return keywords_raw
    if isinstance(keywords_raw, str):
        try:
            return json.loads(keywords_raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


# ══════════════════════════════════════════════════
# 分类规则管理 API
# ══════════════════════════════════════════════════


@router.get('/api/rules')
async def api_list_rules(
    current_user: dict = Depends(get_current_user),
    rule_type: str = '',
    active_only: int = 0,
):
    """获取规则列表，可选的 rule_type 过滤 (dim_key，如 'content'|'region'|'media_type')"""
    rule_type_param = rule_type if rule_type else None
    if active_only:
        rules = models.get_active_classification_rules(rule_type_param)
    else:
        rules = models.get_all_classification_rules(rule_type_param)
    return {'rules': rules}


@router.post('/api/rules')
async def api_create_rule(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """新增分类规则"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）')

    rule_type = data.get('rule_type', '').strip()
    name = data.get('name', '').strip()
    if not rule_type:
        raise HTTPException(status_code=400, detail='rule_type 不能为空')
    if not name:
        raise HTTPException(status_code=400, detail='name 不能为空')

    keywords = data.get('keywords', [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split('\n') if k.strip()]

    priority = data.get('priority', 100)
    sort_order = data.get('sort_order', 0)

    rule_dict = {
        'rule_type': rule_type,
        'name': name,
        'keywords': keywords,
        'priority': priority,
        'sort_order': sort_order,
        'is_active': 1,
    }

    rule_id = models.add_classification_rule(rule_dict)

    # 记录审计日志
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='rule_create',
        target=f'[{rule_type}] {name}',
        ip_address=request.client.host if request.client else '',
    )

    return {'id': rule_id, 'message': '规则已创建'}


@router.put('/api/rules/{rule_id}')
async def api_update_rule(
    rule_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """更新分类规则"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）')

    update_dict = {}
    for key in ('rule_type', 'name', 'priority', 'sort_order', 'is_active'):
        if key in data and data[key] is not None:
            update_dict[key] = data[key]

    if 'keywords' in data and data['keywords'] is not None:
        kw = data['keywords']
        if isinstance(kw, str):
            kw = [k.strip() for k in kw.split('\n') if k.strip()]
        update_dict['keywords'] = kw

    if not update_dict:
        raise HTTPException(status_code=400, detail='没有需要更新的字段')

    success = models.update_classification_rule(rule_id, update_dict)
    if not success:
        raise HTTPException(status_code=404, detail='规则不存在')

    # 记录审计日志
    target = update_dict.get('name', str(rule_id))
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='rule_update',
        target=str(rule_id),
        detail=json.dumps({k: v for k, v in update_dict.items()}, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )

    return {'message': '规则已更新'}


@router.delete('/api/rules/{rule_id}')
async def api_delete_rule(
    rule_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """删除分类规则"""
    success = models.delete_classification_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail='规则不存在')

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='rule_delete',
        target=str(rule_id),
        ip_address=request.client.host if request.client else '',
    )

    return {'message': '规则已删除'}


@router.put('/api/rules/batch-order')
async def api_batch_update_order(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """批量更新排序"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）')

    orders = data.get('orders', [])
    if not orders:
        raise HTTPException(status_code=400, detail='orders 不能为空')

    for item in orders:
        rule_id = item.get('id')
        sort_order = item.get('sort_order', 0)
        if rule_id is not None:
            models.update_classification_rule(rule_id, {'sort_order': sort_order})

    return {'message': '排序已更新'}


# ══════════════════════════════════════════════════
# 分类维度管理 API
# ══════════════════════════════════════════════════


@router.get('/api/rules/dimensions')
async def api_list_dimensions(
    current_user: dict = Depends(get_current_user),
):
    """获取所有维度定义"""
    dims = models.get_all_dimensions()
    return {'dimensions': dims}


@router.post('/api/rules/dimensions')
async def api_create_dimension(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """新增维度"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式')

    dim_key = data.get('dim_key', '').strip()
    dim_name = data.get('dim_name', '').strip()
    sort_order = data.get('sort_order', 0)

    if not dim_key or not dim_name:
        raise HTTPException(status_code=400, detail='dim_key 和 dim_name 不能为空')

    # 检查是否已存在
    existing_dims = models.get_all_dimensions()
    if any(d['dim_key'] == dim_key for d in existing_dims):
        raise HTTPException(status_code=409, detail=f'维度 {dim_key} 已存在')

    dim_id = models.add_dimension(dim_key, dim_name, sort_order)
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='dimension_create',
        target=dim_key,
        detail=f'创建维度 {dim_name} ({dim_key})',
        ip_address=request.client.host if request.client else '',
    )
    return {'id': dim_id, 'message': f'维度 {dim_name} 已创建'}


@router.delete('/api/rules/dimensions/{dim_key}')
async def api_delete_dimension(
    dim_key: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """删除维度（同时删除该维度下所有规则）"""
    success = models.delete_dimension(dim_key)
    if not success:
        raise HTTPException(status_code=404, detail=f'维度 {dim_key} 不存在')
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='dimension_delete',
        target=dim_key,
        ip_address=request.client.host if request.client else '',
    )
    return {'message': f'维度 {dim_key} 已删除'}


# ══════════════════════════════════════════════════
# 排除映射 API
# ══════════════════════════════════════════════════


@router.get('/api/rules/exclusions')
async def api_list_exclusions(
    current_user: dict = Depends(get_current_user),
):
    """获取排除映射列表"""
    exclusions = models.get_all_exclusions()
    return {'exclusions': exclusions}


@router.post('/api/rules/exclusions')
async def api_create_exclusion(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """新增排除映射（检查唯一性）"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）')

    province_keyword = data.get('province_keyword', '').strip()
    excluded_keyword = data.get('excluded_keyword', '').strip()
    note = data.get('note', '').strip()

    if not province_keyword:
        raise HTTPException(status_code=400, detail='province_keyword 不能为空')
    if not excluded_keyword:
        raise HTTPException(status_code=400, detail='excluded_keyword 不能为空')

    # 检查唯一性
    existing = models.check_exclusion(province_keyword, excluded_keyword)
    if existing:
        raise HTTPException(status_code=409, detail='此排除映射已存在')

    excl_id = models.add_exclusion(province_keyword, excluded_keyword, note)
    if excl_id is None:
        raise HTTPException(status_code=409, detail='此排除映射已存在（重复）')

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='exclusion_create',
        target=f'{province_keyword}→{excluded_keyword}',
        ip_address=request.client.host if request.client else '',
    )

    return {'id': excl_id, 'message': '排除映射已添加'}


@router.delete('/api/rules/exclusions/{exclusion_id}')
async def api_delete_exclusion(
    exclusion_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """删除排除映射"""
    success = models.delete_exclusion(exclusion_id)
    if not success:
        raise HTTPException(status_code=404, detail='排除映射不存在')

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='exclusion_delete',
        target=str(exclusion_id),
        ip_address=request.client.host if request.client else '',
    )

    return {'message': '排除映射已删除'}


@router.post('/api/rules/test-classification')
async def api_test_classification(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """多维分类测试：返回所有维度的分类结果"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）')

    channel_name = data.get('channel_name', '').strip()
    if not channel_name:
        raise HTTPException(status_code=400, detail='channel_name 不能为空')

    try:
        from app import ChannelRules
    except ImportError:
        raise HTTPException(status_code=500, detail='无法导入 ChannelRules 模块')

    CHANNEL_RULES_PATH = os.path.join(PROJECT_ROOT, 'config', 'channel_rules.yml')
    # 使用模块级缓存避免每次创建新实例
    if not hasattr(app, '_test_rules_instance'):
        app._test_rules_instance = ChannelRules(rules_path=CHANNEL_RULES_PATH)
    rules_engine = app._test_rules_instance

    # 多维分类
    categories = rules_engine.determine_categories(channel_name)
    info = rules_engine.extract_channel_info(channel_name)

    # 从数据库中查找所有匹配的规则（按维度分组）
    all_rules = models.get_all_classification_rules()
    channel_upper = channel_name.upper()
    matches_by_dim = {}
    for rule in all_rules:
        if not rule.get('is_active'):
            continue
        dim = rule.get('rule_type', 'content')
        keywords = _parse_keywords(rule.get('keywords', '[]'))
        matched_kw = None
        for kw in keywords:
            if kw.upper() in channel_upper:
                matched_kw = kw
                break
        if matched_kw:
            if dim not in matches_by_dim:
                matches_by_dim[dim] = []
            matches_by_dim[dim].append(
                {
                    'keyword': matched_kw,
                    'rule_name': rule.get('name', ''),
                    'priority': rule.get('priority', 100),
                }
            )

    province = info.get('province', '')

    return {
        'categories': categories,
        'matches_by_dim': matches_by_dim,
        'province': province,
    }


@router.post('/api/rules/reimport')
async def api_reimport_rules(
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """从 YAML 重新导入规则（先清空再导入）"""
    CHANNEL_RULES_PATH = os.path.join(PROJECT_ROOT, 'config', 'channel_rules.yml')
    if not os.path.exists(CHANNEL_RULES_PATH):
        raise HTTPException(status_code=404, detail='规则文件不存在')

    try:
        import yaml
    except ImportError:
        raise HTTPException(status_code=500, detail='PyYAML 未安装')

    # 先清空现有规则
    conn = models.get_conn()
    conn.execute('DELETE FROM classification_rules')
    conn.commit()
    conn.close()

    count = 0
    with open(CHANNEL_RULES_PATH, encoding='utf-8') as f:
        data = yaml.safe_load(f)

    import datetime

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 导入 categories 作为 'content' 维度
    sort_idx = 0
    for cat in data.get('categories') or []:
        name = cat.get('name', '')
        if not name:
            continue
        keywords = json.dumps(cat.get('keywords', []), ensure_ascii=False)
        priority = cat.get('priority', 100)
        models.add_classification_rule(
            {
                'rule_type': 'content',
                'name': name,
                'keywords': keywords,
                'priority': priority,
                'sort_order': sort_idx,
                'is_active': 1,
            }
        )
        sort_idx += 1
        count += 1

    # 导入 channel_types 作为 'media_type' 维度
    sort_idx = 0
    for ctype_name, ctype_keywords in (data.get('channel_types') or {}).items():
        keywords = json.dumps(ctype_keywords, ensure_ascii=False)
        models.add_classification_rule(
            {
                'rule_type': 'media_type',
                'name': ctype_name,
                'keywords': keywords,
                'priority': 50,
                'sort_order': sort_idx,
                'is_active': 1,
            }
        )
        sort_idx += 1
        count += 1

    # 重新初始化排除映射
    conn = models.get_conn()
    conn.execute('DELETE FROM province_exclusion_map')
    conn.commit()
    conn.close()

    # 使用 models 中的 _seed_from_yaml 逻辑走一遍
    # 直接调用 init_db 的种子逻辑重建
    conn = models.get_conn()
    models._seed_from_yaml(conn)
    conn.close()

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='rules_reimport',
        target='channel_rules.yml',
        detail=f'从 YAML 重新导入 {count} 条规则',
        ip_address=request.client.host if request.client else '',
    )

    return {'message': f'已从 YAML 重新导入 {count} 条规则', 'count': count}


# ══════════════════════════════════════════════════
# 频道全名映射 API
# ══════════════════════════════════════════════════


@router.get('/api/channel-mapping/{channel_name}')
async def api_get_channel_mapping(
    channel_name: str,
    current_user: dict = Depends(get_current_user),
):
    """查询某个频道名的全名映射"""
    mapping = models.get_channel_name_mapping(channel_name)
    if not mapping:
        raise HTTPException(status_code=404, detail='未找到该频道的全名映射')
    return {'channel_name': channel_name, **mapping}


@router.put('/api/channel-mapping/{channel_name}')
async def api_save_channel_mapping(
    channel_name: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """保存或更新频道全名映射"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式')

    # 从请求体提取各维度（只取合法的 dim_key，大小写容错）
    valid_dims = {'content', 'region', 'language', 'quality', 'media_type', 'genre'}
    dim_alias = {k.lower(): k for k in valid_dims}
    categories = {}
    for key, val in data.items():
        key_clean = key.strip().lower()
        if key_clean in dim_alias:
            val_str = str(val).strip()
            if val_str:
                categories[dim_alias[key_clean]] = val_str

    if not categories:
        raise HTTPException(status_code=400, detail='至少需要提供一个维度的分类值')

    # 自动补全缺失维度
    try:
        from app import ChannelRules

        rules = ChannelRules()
        auto_cats = rules.determine_categories(channel_name)
    except (ImportError, ModuleNotFoundError, Exception):
        auto_cats = {}
    for dim in valid_dims:
        if dim not in categories and dim in auto_cats:
            categories[dim] = auto_cats[dim]

    success = models.save_channel_name_mapping(channel_name, categories)
    if not success:
        raise HTTPException(status_code=500, detail='保存失败')

    return {'message': f'{channel_name} 映射已保存', 'categories': categories, 'is_manual': 1}


@router.delete('/api/channel-mapping/{channel_name}')
async def api_delete_channel_mapping(
    channel_name: str,
    current_user: dict = Depends(get_current_user),
):
    """删除频道全名映射"""
    # 仅管理员可操作
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可删除映射')
    ok = models.delete_channel_name_mapping(channel_name)
    if not ok:
        raise HTTPException(status_code=404, detail='未找到该映射')
    return {'message': f'{channel_name} 映射已删除'}


@router.get('/api/channel-mappings')
async def api_list_channel_mappings(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """分页列出全部频道全名映射"""
    mappings, total = models.list_channel_name_mappings(page, page_size)
    return {'mappings': mappings, 'total': total, 'page': page, 'page_size': page_size}


@router.post('/api/channel-mappings/batch-import')
async def api_batch_import_mappings(
    current_user: dict = Depends(get_current_user),
):
    """从当前已测试成功的源批量导入频道全名映射"""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可批量导入')
    imported = models.batch_import_mappings_from_current_sources()
    return {'message': f'批量导入完成，共 {imported} 条'}


# ══════════════════════════════════════════════════
# 分类字典 API
# ══════════════════════════════════════════════════

VALID_DIMENSIONS = {'content', 'region', 'language', 'quality', 'media_type', 'genre'}
DIMENSION_LABELS = {
    'content': '内容分类',
    'region': '地区',
    'language': '语言',
    'quality': '画质',
    'media_type': '媒体类型',
    'genre': '类型',
}


@router.get('/api/category-dictionary')
async def api_get_category_dictionary(current_user: dict = Depends(get_current_user)):
    """获取全部分类字典（按维度分组的可选项列表）"""
    data = models.get_category_dictionary()
    # 补充维度标签
    dimensions = []
    for dim_key in ['content', 'region', 'language', 'quality', 'media_type', 'genre']:
        dimensions.append(
            {
                'key': dim_key,
                'label': DIMENSION_LABELS.get(dim_key, dim_key),
                'options': data.get(dim_key, []),
            }
        )
    return {'dimensions': dimensions, 'raw': data}


@router.post('/api/category-dictionary/{dimension}')
async def api_add_category_option(
    dimension: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """添加一条分类字典选项"""
    if dimension not in VALID_DIMENSIONS:
        raise HTTPException(
            status_code=400, detail=f'无效的维度: {dimension}，可选: {", ".join(sorted(VALID_DIMENSIONS))}'
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式')

    value = str(body.get('value', '')).strip()
    label = str(body.get('label', '')).strip() or value
    sort_order = int(body.get('sort_order', 99))

    if not value:
        raise HTTPException(status_code=400, detail='value 不能为空')

    ok = models.add_category_dictionary_option(dimension, value, label, sort_order)
    if not ok:
        raise HTTPException(status_code=409, detail=f'选项 "{value}" 已存在或添加失败')
    return {'message': f'{DIMENSION_LABELS.get(dimension, dimension)} 选项 "{value}" 已添加'}


@router.delete('/api/category-dictionary/{dimension}/{value:path}')
async def api_delete_category_option(
    dimension: str,
    value: str,
    current_user: dict = Depends(require_admin),
):
    """删除一条分类字典选项"""
    from urllib.parse import unquote

    value = unquote(value)
    if dimension not in VALID_DIMENSIONS:
        raise HTTPException(status_code=400, detail=f'无效的维度: {dimension}')

    ok = models.delete_category_dictionary_option(dimension, value)
    if not ok:
        raise HTTPException(status_code=404, detail=f'未找到选项 "{value}"')
    return {'message': f'{DIMENSION_LABELS.get(dimension, dimension)} 选项 "{value}" 已删除'}


@router.put('/api/category-dictionary/{dimension}')
async def api_set_category_dimension(
    dimension: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """批量设置某个维度的所有选项（替换式）"""
    if dimension not in VALID_DIMENSIONS:
        raise HTTPException(status_code=400, detail=f'无效的维度: {dimension}')

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式')

    options = body.get('options', [])
    if not isinstance(options, list):
        raise HTTPException(status_code=400, detail='options 必须是数组')

    ok = models.set_category_dictionary_dimension(dimension, options)
    if not ok:
        raise HTTPException(status_code=500, detail='保存失败')
    return {'message': f'{DIMENSION_LABELS.get(dimension, dimension)} 已更新，共 {len(options)} 项'}
