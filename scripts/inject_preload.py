#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把最新 data.json 注入 index.html 的 window.__PRELOADED__ 内联变量中。

为什么需要这个脚本：
  当前线上 index.html 依赖内联的 window.__PRELOADED__ 直接渲染，
  并没有在浏览器端 fetch data.json。所以只更新 data.json 不会刷新页面。
  本脚本在 Actions 中跑，把 data.json 的内容写回 index.html 的 __PRELOADED__，
  然后 GitHub Pages 部署新 index.html，页面数据就刷新了。
"""
import json, re, sys

DATA_PATH = 'data.json'
HTML_PATH = 'index.html'


def main():
    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    # 把 data.json 序列化为单行 JSON，避免破坏 HTML 结构
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))

    # 替换 window.__PRELOADED__ = {...};
    new_html, count = re.subn(
        r'window\.__PRELOADED__\s*=\s*\{.*?\};',
        'window.__PRELOADED__=' + payload + ';',
        html,
        count=1,
        flags=re.DOTALL
    )

    if count == 0:
        print('ERROR: 在 index.html 中找不到 window.__PRELOADED__ = {...};')
        sys.exit(1)

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print('OK: 已把 %s 注入 %s （sessions=%d, syncedAt=%s）' % (
        DATA_PATH, HTML_PATH, len(data.get('sessions', [])),
        data.get('meta', {}).get('syncedAt', '-')
    ))


if __name__ == '__main__':
    main()
