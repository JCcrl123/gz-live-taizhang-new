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

    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

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

    print('OK: 已把 %s 注入 %s （sessions=%d, syncedAt=%s）' % (
        DATA_PATH, HTML_PATH, len(data.get('sessions', [])),
        data.get('meta', {}).get('syncedAt', '-')
    ))


if __name__ == '__main__':
    main()
