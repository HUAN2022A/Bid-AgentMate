# -*- coding: utf-8 -*-
"""大屏截图工具：登录 → 打开 /screen/:id → 等图表/动画就绪 → 1920×1080 截图。

用法: python shot_screen.py <base_url> <out_png> [route=/screen/1] [wait_ms=9000]
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    base, out = sys.argv[1], sys.argv[2]
    route = sys.argv[3] if len(sys.argv) > 3 else "/screen/1"
    wait_ms = int(sys.argv[4]) if len(sys.argv) > 4 else 9000

    data = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
    token = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(f"{base}/api/auth/login", data=data), timeout=10
        ).read()
    )["access_token"]

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        ctx.add_init_script(f"localStorage.setItem('bam_token', '{token}')")
        page = ctx.new_page()
        page.goto(base + route, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        page.screenshot(path=out)
        browser.close()
    print(f"shot -> {out}")


if __name__ == "__main__":
    main()
