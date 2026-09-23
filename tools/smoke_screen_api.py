# -*- coding: utf-8 -*-
"""screen-data 聚合接口冒烟：登录 → GET /screen-data → 校验顶层契约字段。

用法: python smoke_screen_api.py <base_url> <project_id>
退出码 0=契约字段齐全；1=HTTP 错误或缺字段。输出 JSON 摘要。
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

REQUIRED = [
    "project", "flow", "current_step_index", "summary", "score_dist",
    "coverage", "chapters", "copilot", "materials", "risks",
]


def main():
    base, pid = sys.argv[1], sys.argv[2]
    data = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
    token = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(f"{base}/api/auth/login", data=data), timeout=10
        ).read()
    )["access_token"]

    req = urllib.request.Request(
        f"{base}/api/projects/{pid}/screen-data",
        headers={"Authorization": f"Bearer {token}"},
    )
    raw = b""
    try:
        raw = urllib.request.urlopen(req, timeout=20).read()
        body = json.loads(raw)
    except urllib.error.HTTPError as e:
        print(json.dumps(
            {"ok": False, "http": e.code, "body": e.read()[:300].decode("utf-8", "replace")},
            ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        # 未知 API 路径会被 SPA 兜底成 index.html（200 + HTML），也归为未实现
        print(json.dumps(
            {"ok": False, "error": str(e), "head": raw[:120].decode("utf-8", "replace")},
            ensure_ascii=False))
        sys.exit(1)

    missing = [k for k in REQUIRED if k not in body]
    cop = body.get("copilot", {})
    brief = {
        "ok": not missing,
        "missing": missing,
        "state": body.get("project", {}).get("state"),
        "summary": body.get("summary"),
        "copilot": {k: cop.get(k) for k in ("total", "applied", "apply_rate", "recent")},
        "materials": body.get("materials"),
        "coverage_items": len(body.get("coverage", {}).get("items", [])),
        "risks": len(body.get("risks", [])),
    }
    print(json.dumps(brief, ensure_ascii=False))
    sys.exit(0 if not missing else 1)


if __name__ == "__main__":
    main()
