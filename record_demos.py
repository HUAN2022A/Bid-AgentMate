# -*- coding: utf-8 -*-
"""录制三个新功能的演示视频（Playwright + 系统 Chrome）。

产物（仓库根目录）：
  demo-cockpit.webm           项目驾驶舱：状态流程条 → 汇总 → 评分分布 → 章节进度
  demo-coverage-heatmap.webm  覆盖矩阵：三态热力图 + 格级 Tooltip + 只看技术类过滤
  demo-copilot-streaming.webm 流式 Copilot：选中段落 → 对齐评分点 → 打字机流式 → Diff → 应用 → 保存

用法：python record_demos.py [cockpit|heatmap|copilot|all]
"""
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8000")
ROOT = Path(__file__).parent.resolve()
RAW = ROOT / ".demo-videos-raw"


def get_token() -> str:
    data = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
    req = urllib.request.Request(f"{BASE}/api/auth/login", data=data)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())["access_token"]


def new_ctx(browser, token: str):
    RAW.mkdir(exist_ok=True)
    ctx = browser.new_context(
        viewport={"width": 1600, "height": 900},
        record_video_dir=str(RAW),
        record_video_size={"width": 1600, "height": 900},
    )
    ctx.add_init_script(f"localStorage.setItem('bam_token', '{token}')")
    return ctx


def settle(page, ms=1200):
    page.wait_for_timeout(ms)


def save_video(page, final_name: str):
    page.wait_for_timeout(400)
    src = Path(page.video.path())
    dst = ROOT / final_name
    ctx = page.context
    page.close()
    ctx.close()
    # video 落盘发生在 page/context 关闭之后
    for _ in range(20):
        if src.exists() and src.stat().st_size > 0:
            break
        time.sleep(0.3)
    shutil.move(str(src), dst)
    print(f"[saved] {dst}  ({dst.stat().st_size // 1024} KB)")


def record_cockpit(browser, token):
    ctx = new_ctx(browser, token)
    page = ctx.new_page()
    page.goto(f"{BASE}/projects/1/cockpit", wait_until="domcontentloaded")
    page.wait_for_selector("text=评分分布", timeout=15000)
    settle(page, 1600)

    # 滚动展示：流程条/汇总 → 评分分布 → 章节进度
    for _ in range(4):
        page.mouse.wheel(0, 260)
        settle(page, 650)
    settle(page, 800)
    for _ in range(4):
        page.mouse.wheel(0, 260)
        settle(page, 650)
    settle(page, 900)
    # 章节进度区域悬停片刻，再回到顶部
    page.locator("text=章节进度").scroll_into_view_if_needed()
    settle(page, 1800)
    page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    settle(page, 1600)
    save_video(page, "demo-cockpit.webm")


def record_heatmap(browser, token):
    ctx = new_ctx(browser, token)
    page = ctx.new_page()
    page.goto(f"{BASE}/projects/1/delivery", wait_until="domcontentloaded")
    page.wait_for_selector("text=覆盖矩阵：评分点 × 章节", timeout=15000)
    settle(page, 1200)

    card = page.locator(".ant-card", has=page.locator("text=覆盖矩阵：评分点 × 章节"))
    card.scroll_into_view_if_needed()
    settle(page, 1400)

    # 依次悬停各状态的格子（当前数据里未必每种状态都有，存在才悬停）
    for style_key in ("rgb(82, 196, 26)", "rgb(250, 173, 20)", "rgb(245, 245, 245)"):
        cell = card.locator(f'div[style*="{style_key}"]')
        if cell.count() == 0:
            print(f"[skip] 无 {style_key} 格子")
            continue
        cell.first.scroll_into_view_if_needed()
        cell.first.hover()
        settle(page, 1700)

    # 移开鼠标让 Tooltip 关闭，再悬停行首（评分点摘要）
    page.mouse.move(900, 40)
    settle(page, 600)
    row_head = card.locator('div[style*="left: 0"]').nth(2)
    row_head.hover()
    settle(page, 1500)
    page.mouse.move(900, 40)
    settle(page, 600)

    # 只看技术类 过滤切换
    page.locator("label", has_text="只看技术类").click()
    settle(page, 1600)
    page.locator("label", has_text="只看技术类").click()
    settle(page, 1400)
    save_video(page, "demo-coverage-heatmap.webm")


def record_copilot(browser, token):
    ctx = new_ctx(browser, token)
    page = ctx.new_page()
    page.goto(f"{BASE}/projects/1/chapters/1", wait_until="domcontentloaded")
    page.wait_for_selector(".ProseMirror", timeout=15000)
    settle(page, 1500)

    # 选一个中等长度段落
    idx = page.evaluate(
        """() => {
            const ps = [...document.querySelectorAll('.ProseMirror p')];
            for (let i = 0; i < ps.length; i++) {
                const t = ps[i].innerText.trim();
                if (t.length >= 80 && t.length <= 320 && !t.includes('|')) return i;
            }
            return -1;
        }"""
    )
    if idx < 0:
        raise RuntimeError("没有可演示的段落")
    box = page.locator(".ProseMirror p").nth(idx).bounding_box()
    page.mouse.move(box["x"] + 10, box["y"] + 10)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 14, box["y"] + box["height"] - 8, steps=18)
    settle(page, 200)
    page.mouse.up()
    settle(page, 600)

    btn = page.locator('button:has-text("对齐评分点")')
    btn.wait_for(state="visible", timeout=8000)
    settle(page, 700)
    btn.click()

    # 首个 delta 到达时弹窗打开（流式打字机）
    page.wait_for_selector(".ant-modal", timeout=90000)
    settle(page, 4000)  # 展示流式输出 + 停止生成

    apply = page.locator('.ant-modal button:has-text("应用")')
    apply.wait_for(state="visible", timeout=150000)
    settle(page, 2200)  # 展示 Diff 视图
    apply.click()
    settle(page, 2200)  # 正文替换 + 未保存/含 AI 段落修改 标签

    page.get_by_role("button", name=re.compile(r"保\s*存")).click()
    page.wait_for_selector("text=已保存", timeout=8000)
    settle(page, 1600)
    save_video(page, "demo-copilot-streaming.webm")


def record_screen(browser, token):
    """作战大屏：1920×1080 满屏，展示入场动画 + 粒子背景 + 发光图表 + hover 效果。"""
    RAW.mkdir(exist_ok=True)
    ctx = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        record_video_dir=str(RAW),
        record_video_size={"width": 1920, "height": 1080},
    )
    ctx.add_init_script(f"localStorage.setItem('bam_token', '{token}')")
    page = ctx.new_page()
    page.goto(f"{BASE}/screen/1", wait_until="domcontentloaded")
    settle(page, 8000)  # 入场动画 + 图表生长 + 粒子背景运转

    # 鼠标缓缓扫过几个区域，带出 hover 提亮效果
    for x, y in ((960, 540), (320, 420), (1600, 420), (960, 900)):
        page.mouse.move(x, y, steps=12)
        settle(page, 1400)
    settle(page, 2500)  # 结尾静置（时钟跳动/滚动列表仍在动）
    save_video(page, "demo-battle-screen.webm")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    token = get_token()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            if which in ("cockpit", "all"):
                record_cockpit(browser, token)
            if which in ("heatmap", "all"):
                record_heatmap(browser, token)
            if which in ("copilot", "all"):
                record_copilot(browser, token)
            if which in ("screen", "all"):
                record_screen(browser, token)
        finally:
            browser.close()
    print("done")


if __name__ == "__main__":
    main()
