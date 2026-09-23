#!/usr/bin/env python3
"""工作台 M1 真实文件端到端验证：三个桌面文件（招标 PDF / 规范书 docx / 投标 .doc 116MB）。

用法（backend 目录，走真实 LLM 与 .doc COM 转换，需 SYNC_TASKS=true 避免 Redis）：
  SYNC_TASKS=true uv run python scripts/e2e_workbench_real.py

步骤：登录 → 建工作台项目 → 三文件上传 → 触发解析（轮询，最长 20 分钟）→
读投标目录树 → 确认 → 校验章节入库（期望 160+ 章）。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402

FILES = [
    (r"C:\Users\qzh\Desktop\淮北国安电厂二期项目2×660MW超超临界机组翻车机自动摘钩、正钩、复钩机器人系统采购_招标文件.pdf", "main"),
    (r"C:\Users\qzh\Desktop\翻车机自动摘正复钩机器人系统技术规范书-20260805.docx", "spec"),
    (r"C:\Users\qzh\Desktop\正式投标文件(1).doc", "bid"),
]


def main() -> None:
    assert settings.sync_tasks, "请设置 SYNC_TASKS=true 再跑此脚本"
    client = TestClient(app)

    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin123"})
    if r.status_code != 200:
        r = client.post("/api/auth/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == 200, f"登录失败: {r.status_code} {r.text}"
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post("/api/projects", headers=h, json={
        "name": "淮北国安电厂二期翻车机机器人（真实文件验证）", "tender_no": "", "mode": "workbench",
    })
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    print(f"[1] 工作台项目 #{pid}")

    for path, role in FILES:
        with open(path, "rb") as f:
            r = client.post(f"/api/projects/{pid}/tender?role={role}", headers=h,
                            files={"file": (Path(path).name, f)})
        assert r.status_code == 200, f"上传 {role} 失败: {r.text}"
        print(f"[2] 上传 {role}: {Path(path).name} ({r.json()['file_type']})")

    t0 = time.time()
    r = client.post(f"/api/projects/{pid}/parse", headers=h)
    assert r.status_code == 200, r.text
    state = r.json()["state"]
    print(f"[3] 解析触发 state={state}")
    err = ""
    while time.time() - t0 < 1200:
        r = client.get(f"/api/projects/{pid}", headers=h)
        state, err = r.json()["state"], r.json()["parse_error"]
        if state in ("wb_outline_pending", "wb_parse_failed"):
            break
        time.sleep(5)
    print(f"[4] 解析结束 state={state} 用时 {time.time()-t0:.0f}s err={err[:300]}")
    assert state == "wb_outline_pending", f"解析失败: {err}"

    r = client.get(f"/api/projects/{pid}/analysis", headers=h)
    items = r.json()["scoring_items"]
    print(f"[5] 评分点 {len(items)} 个（技术 {sum(1 for i in items if i['category']=='技术')}）")

    r = client.get(f"/api/projects/{pid}/outline?kind=bid", headers=h)
    tree = r.json()["tree"]
    print(f"[6] 投标目录一级章 {len(tree['nodes'])} 个: {[n['title'][:20] for n in tree['nodes']]}")

    r = client.post(f"/api/projects/{pid}/outline/confirm?kind=bid", headers=h)
    assert r.status_code == 200, r.text
    print(f"[7] 确认: {r.json()}")

    r = client.get(f"/api/projects/{pid}/chapters", headers=h)
    chapters = r.json()
    total = sum(c["word_count"] for c in chapters)
    non_empty = sum(1 for c in chapters if c["word_count"] > 0)
    print(f"[8] 章节 {len(chapters)} 章入库，非空 {non_empty}，总字数 {total}")
    assert len(chapters) >= 100, "章节数远低于预期（真实文件应 160 左右）"
    print("E2E REAL OK")


if __name__ == "__main__":
    main()
