#!/usr/bin/env python3
"""工作台 M1 端到端验证（样例模式，无 LLM / 无 Redis）：一键样例项目全流程。

用法（backend 目录，需 SAMPLE_MODE=true SYNC_TASKS=true）：
  SAMPLE_MODE=true SYNC_TASKS=true uv run python scripts/e2e_workbench_sample.py

步骤：登录 → 创建样例项目 → 轮询解析态 → 读投标目录树 → 确认 → 校验章节入库。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402

assert settings.sample_mode, "请设置 SAMPLE_MODE=true 再跑此脚本"
assert settings.sync_tasks, "请设置 SYNC_TASKS=true 再跑此脚本"


def main() -> None:
    client = TestClient(app)

    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin123"})
    if r.status_code != 200:
        r = client.post("/api/auth/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == 200, f"登录失败: {r.status_code} {r.text}"
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/projects/sample-project", headers=h)
    assert r.status_code == 200, f"创建样例项目失败: {r.text}"
    pid = r.json()["id"]
    print(f"[1] 样例项目 #{pid} 已创建，state={r.json()['state']}")

    state = ""
    for _ in range(30):
        r = client.get(f"/api/projects/{pid}", headers=h)
        state = r.json()["state"]
        if state in ("wb_outline_pending", "wb_parse_failed"):
            break
        time.sleep(0.5)
    assert state == "wb_outline_pending", f"解析未达预期: state={state} err={r.json()['parse_error']}"
    print(f"[2] 解析完成 state={state}")

    r = client.get(f"/api/projects/{pid}/analysis", headers=h)
    items = r.json()["scoring_items"]
    print(f"[3] 评分点 {len(items)} 个: {[(i['item_key'], i['item'], i['score']) for i in items]}")
    assert len(items) >= 4

    r = client.get(f"/api/projects/{pid}/outline?kind=bid", headers=h)
    assert r.status_code == 200, r.text
    tree = r.json()["tree"]
    top = [(n["id"], n["title"], len(n["children"])) for n in tree["nodes"]]
    print(f"[4] 投标目录树一级章: {top}")

    r = client.post(f"/api/projects/{pid}/outline/confirm?kind=bid", headers=h)
    assert r.status_code == 200, r.text
    print(f"[5] 目录确认: {r.json()}")

    r = client.get(f"/api/projects/{pid}/chapters", headers=h)
    chapters = r.json()
    assert chapters, "章节为空"
    total_words = sum(c["word_count"] for c in chapters)
    print(f"[6] 章节入库 {len(chapters)} 章，总字数 {total_words}:")
    for c in chapters:
        print(f"    {c['chapter_key']:8} {c['title'][:30]:32} {c['state']:9} {c['word_count']} 字")
    typo_ch = [c for c in chapters if "按装" in (c.get("title") or "")]
    print(f"[7] 章节状态集合: {sorted({c['state'] for c in chapters})}")
    print("E2E SAMPLE OK")


if __name__ == "__main__":
    main()
