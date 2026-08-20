#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把最新 data.json 注入 index.html 的 window.__PRELOADED__ 内联变量中（安全版）。

修复了上一版的致命 bug：
  旧版用正则 window\.__PRELOADED__\s*=\s*\{.*?\}; 匹配，
  当 JSON 字符串内部含有 "};" 字符时会提前截断，导致 index.html 被删成残片、页面空白。
  新版使用 json.JSONDecoder.raw_decode 定位真正的 JSON 结束位置，不受字符串内容影响。
"""
import json, re, sys

DATA_PATH = 'data.json'
HTML_PATH = 'index.html'


def main():
    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    # 关键安全修复：防止 JSON 字符串内的 </script> 被 HTML 解析器误认为是 script 结束标签，
    # 导致后续 JS 代码被截断，出现 "g is not defined" 等离奇报错。
    payload = payload.replace('</script>', '<\\/script>')

    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    # 修复已知损坏：某些编辑器/复制操作会把正则 /\\/g 错误拆成 /\\/ / g，
    # 导致 "g is not defined" 报错。注入前先自动修掉这种空格。
    broken_count = html.count(r'/\\/ / g')
    if broken_count:
        html = html.replace(r'/\\/ / g', r'/\\/g')
        print('WARN: 修复 %d 处被拆坏的正则（/\\\\/ / g -> /\\\\/g）' % broken_count)

    # 定位 window.__PRELOADED__ = 的起始位置
    m = re.search(r'window\.__PRELOADED__\s*=\s*', html)
    if not m:
        print('ERROR: 在 index.html 中找不到 window.__PRELOADED__ =', file=sys.stderr)
        sys.exit(1)

    start = m.end()  # '=' 后面第一个字符的位置

    # 用 JSON 解析器从 start 开始解析，得到 JSON 真正的结束位置
    decoder = json.JSONDecoder()
    try:
        _, json_end = decoder.raw_decode(html, start)
    except json.JSONDecodeError as e:
        print('ERROR: 无法解析现有 index.html 中的 __PRELOADED__ JSON:', e, file=sys.stderr)
        sys.exit(1)

    # 跳过 JSON 后的空白，并吞掉一个可选的分号
    semicolon_pos = json_end
    while semicolon_pos < len(html) and html[semicolon_pos].isspace():
        semicolon_pos += 1
    if semicolon_pos < len(html) and html[semicolon_pos] == ';':
        semicolon_pos += 1

    new_html = html[:start] + payload + html[semicolon_pos:]

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)

    # 验证：支持两种数据格式
    #   - 新注入的原始格式 {meta, maike, target}
    #   - 旧转换格式 {meta, sessions, targets}
    sessions = data.get('sessions', [])
    raw_records = data.get('maike', [])
    total = data.get('meta', {}).get('total', 0)
    synced_at = data.get('meta', {}).get('syncedAt', '-')

    if sessions:
        print('OK: 已把 %s 注入 %s （sessions=%d, syncedAt=%s）' % (
            DATA_PATH, HTML_PATH, len(sessions), synced_at
        ))
        if len(sessions) < 500:
            print('ERROR: sessions 数量异常（%d < 500），拒绝提交' % len(sessions), file=sys.stderr)
            sys.exit(1)
        if total != len(sessions):
            print('ERROR: meta.total 与 sessions 长度不一致', file=sys.stderr)
            sys.exit(1)
    elif raw_records:
        print('OK: 已把 %s 注入 %s （raw=%d, syncedAt=%s）' % (
            DATA_PATH, HTML_PATH, len(raw_records), synced_at
        ))
        if len(raw_records) < 500:
            print('ERROR: maike 原始记录数异常（%d < 500），拒绝提交' % len(raw_records), file=sys.stderr)
            sys.exit(1)
    else:
        print('ERROR: 数据中没有 sessions 也没有 maike，疑似注入失败', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
