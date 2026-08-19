#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gz-live-taizhang 数据同步器 —— 腾讯云 SCF 替代版（纯 GitHub Actions 运行，零腾讯云依赖）

做什么：
  1) 用 FEISHU_APP_ID + FEISHU_APP_SECRET 换取 tenant_access_token（当场换，无需存 refresh_token、无需 COS）
  2) 拉飞书多维表格「迈科代理直播数据」(tblB2XqdF1BFzVNF) + 「主播月度目标」(tblUMTDEHugCAq0C)
  3) 转成 sessions，写出 data.json（由调用方 git commit & push 回仓库）

为何这是"突破"：
  原 SCF 同步器已停摆（线上 data.json 停在 2026-08-17 22:00，8/18、8/19 零同步）。
  本脚本把数据侧完全迁到飞书官方 API，定时触发交给 GitHub Actions，
  整条链路不再依赖腾讯云 SCF / COS / 任何外部定时触发器。

鉴权模式：
  默认 tenant_access_token（服务端到服务端，app_id+app_secret 即可）。
  若你的飞书应用只授权了 user 级令牌，设 FEISHU_AUTH_MODE=user 并提供 FEISHU_REFRESH_TOKEN 秘密。
"""
import json, os, math, time, datetime, urllib.request, urllib.error, sys

API = 'https://open.feishu.cn/open-apis'
BASE_TOKEN = 'SQekb0IT4apUinst61Hcc6YCnkf'      # 飞书多维表格 app token（与线上 data.json meta.baseToken 一致）
TABLE_MAIKE = 'tblB2XqdF1BFzVNF'                 # 迈科代理直播数据
TABLE_TARGET = 'tblUMTDEHugCAq0C'                # 主播月度目标
OUT_PATH = os.environ.get('OUT_PATH', 'data.json')

APP_ID = os.environ.get('FEISHU_APP_ID', '')
APP_SECRET = os.environ.get('FEISHU_APP_SECRET', '')
AUTH_MODE = os.environ.get('FEISHU_AUTH_MODE', 'tenant').lower()
REFRESH_TOKEN = os.environ.get('FEISHU_REFRESH_TOKEN', '')
MIN_SESSIONS = int(os.environ.get('MIN_SESSIONS', '500'))   # 完整性护栏：低于此值判定异常，拒绝写出

DRY_RUN = '--dry-run' in sys.argv


def log(*a):
    print('[gz-sync]', *a, flush=True)


# ---------------- HTTP ----------------
def http(method, url, token=None, data=None, timeout=30):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(data).encode('utf-8') if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8'))
        except Exception:
            return e.code, {'error': str(e)}


# ---------------- 鉴权 ----------------
def get_tenant_token():
    status, d = http('POST', API + '/auth/v3/tenant_access_token/internal',
                     data={'app_id': APP_ID, 'app_secret': APP_SECRET})
    if status != 200 or d.get('code') != 0:
        raise RuntimeError('tenant token fail: ' + json.dumps(d, ensure_ascii=False)[:300])
    return d['tenant_access_token']


def get_user_token():
    """user 模式：用 refresh_token 换一对新令牌（需要秘密里预置 FEISHU_REFRESH_TOKEN）。"""
    if not REFRESH_TOKEN:
        raise RuntimeError('FEISHU_AUTH_MODE=user 需要 FEISHU_REFRESH_TOKEN 秘密')
    status, d = http('POST', API + '/authen/v1/refresh_access_token',
                     token=None,
                     data={'grant_type': 'refresh_token', 'refresh_token': REFRESH_TOKEN})
    if status != 200 or d.get('code') != 0:
        raise RuntimeError('refresh fail: ' + json.dumps(d, ensure_ascii=False)[:300])
    return d['data']['access_token']


def acquire_token():
    if AUTH_MODE == 'user':
        return get_user_token()
    return get_tenant_token()


# ---------------- 拉数据 ----------------
def fetch_records(token, table_id):
    rows, fields = [], None
    page_token = None
    while True:
        url = '%s/bitable/v1/apps/%s/tables/%s/records?page_size=200' % (API, BASE_TOKEN, table_id)
        if page_token:
            url += '&page_token=' + page_token
        status, d = http('GET', url, token=token)
        if status != 200 or d.get('code') != 0:
            raise RuntimeError('bitable fail: ' + json.dumps(d, ensure_ascii=False)[:300])
        items = d['data']['items']
        if fields is None:
            fields = list(items[0]['fields'].keys()) if items else []
        rows.extend(items)
        if not d['data'].get('has_more'):
            break
        page_token = d['data'].get('page_token')
        if not page_token:
            break
    return rows, fields


# ---------------- 字段解析（移植自旧 SCF，逻辑完全一致） ----------------
def cell(f, name):
    v = f.get(name)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def num(v):
    if v in (None, ''):
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def hhmm(t):
    if not t or ':' not in str(t):
        return None
    try:
        h, m = str(t).strip().split(':')[:2]
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return None


def bill(m):
    if not m or m <= 0:
        return None
    return math.ceil(m / 30.0) * 0.5


def parse_date(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        sec = v / 1000.0 if v > 1e11 else float(v)
        try:
            return datetime.datetime.fromtimestamp(sec)
        except (ValueError, OSError, OverflowError):
            return None
    s = str(v).strip().replace('/', '-')[:10]
    try:
        return datetime.datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        return None


def parse_ym(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        dt = parse_date(v)
        return dt.strftime('%Y-%m') if dt else None
    s = str(v).strip().replace('/', '-')[:7]
    return s if len(s) == 7 else None


def bucket_of(s):
    if s is None:
        return '未知'
    h = (s // 60) % 24
    if 6 <= h < 10:
        return '早间 06-10'
    if 10 <= h < 14:
        return '午间 10-14'
    if 14 <= h < 18:
        return '下午 14-18'
    if 18 <= h < 22:
        return '晚间 18-22'
    return '深夜 22-06'


def build_sessions(rows):
    out = []
    for row in rows:
        f = row.get('fields', {})
        anchor = cell(f, '主播')
        dt = parse_date(cell(f, '直播日期'))
        if dt is None or not anchor:
            continue
        d = dt.strftime('%Y-%m-%d')
        sm, em = hhmm(cell(f, '开播时间')), hhmm(cell(f, '下播时间'))
        dm = None
        if sm is not None and em is not None:
            dm = em - sm
            if dm < 0:
                dm += 24 * 60
            if not (0 < dm <= 12 * 60):
                dm = None
        if dm is None:
            h = num(cell(f, '直播时长'))
            dm = h * 60 if (h and 0 < h <= 12) else None
        leads = num(cell(f, '留资数')) or 0
        cost = num(cell(f, '总消耗_元')) or 0
        out.append({
            'id': row.get('record_id'),
            'sn': cell(f, '场次编号'),
            'date': d,
            'ts': int(datetime.datetime(dt.year, dt.month, dt.day).timestamp() * 1000),
            'ym': d[:7], 'wd': dt.weekday(),
            'anchor': anchor,
            'atype': cell(f, '主播类型') or '未分类',
            'team': cell(f, '团队归属') or '未分类',
            'agent': cell(f, '代理') or '未分类',
            'slot': cell(f, '场次属性') or '未知',
            'st': cell(f, '开播时间'),
            'et': cell(f, '下播时间'),
            'bucket': bucket_of(sm),
            'durMin': round(dm, 1) if dm else None,
            'billH': bill(dm),
            'cost': round(cost, 2),
            'leads': int(leads),
            'views': int(num(cell(f, '场观人数')) or 0) or None,
            'clicks': int(num(cell(f, '小风车点击数')) or 0) or None,
            'comments': int(num(cell(f, '评论数')) or 0) or None,
            'advice': cell(f, '投手建议'),
            'backing': cell(f, '背贴'),
        })
    out.sort(key=lambda s: (s['date'], s['st'] or ''))
    return out


def build_targets(rows):
    out = {}
    for row in rows:
        f = row.get('fields', {})
        ym_raw, anchor, tgt = cell(f, '月份'), cell(f, '主播'), num(cell(f, '月度目标'))
        ym = parse_ym(ym_raw)
        if not ym or not anchor or tgt is None:
            continue
        agent = cell(f, '所属代理') or ''
        if agent and '迈科' not in agent:
            continue
        out.setdefault(ym, {})[anchor] = tgt
    return out


# ---------------- 主流程 ----------------
def main():
    if not APP_ID or not APP_SECRET:
        raise RuntimeError('缺少 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量')
    log('auth mode =', AUTH_MODE)
    at = acquire_token()
    log('token ready')

    raw, fields = fetch_records(at, TABLE_MAIKE)
    sessions = build_sessions(raw)          # 用于完整性校验
    log('maike sessions:', len(sessions), '| fields:', fields)

    targets_raw = []
    try:
        targets_raw, _ = fetch_records(at, TABLE_TARGET)
        build_targets(targets_raw)          # 仅校验目标表可解析
    except Exception as e:
        log('target table warn (skip):', e)

    if len(sessions) < MIN_SESSIONS:
        raise RuntimeError('abort: 仅拉到 %d 条（< %d），疑似飞书返回异常，拒绝覆盖' % (len(sessions), MIN_SESSIONS))

    dates = [s['date'] for s in sessions]
    anchors = sorted({s['anchor'] for s in sessions})
    # 页面 JS 期望原始数据格式：{meta, maike, target}，由前端 transformRaw/build_sessions 自行转换
    payload = {
        'meta': {
            'source': '飞书多维表格 · 迈科代理直播数据',
            'baseToken': BASE_TOKEN, 'tableId': TABLE_MAIKE,
            'syncedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total': len(sessions),
            'dateMin': min(dates) if dates else None,
            'dateMax': max(dates) if dates else None,
            'anchors': anchors, 'billingRule': 'ceil(分钟/30)×0.5',
            'notes': {
                'noCity': '数据源无「获客城市」字段，城市维度暂不可用',
                'noExposure': '「曝光人数」字段全表为空，曝光进入率不展示',
                'sparseEarly': '小风车/评论/场观数据 2024 年缺失，2025 年起逐步完整',
            },
        },
        'maike': raw, 'target': targets_raw,
    }

    if DRY_RUN:
        log('[DRY-RUN] 不写出文件。total=%d dateMax=%s anchors=%d' % (len(sessions), max(dates), len(anchors)))
        return payload

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    log('OK 写出 %s, raw=%d, sessions=%d' % (OUT_PATH, len(raw), len(sessions)))
    return payload


if __name__ == '__main__':
    main()
