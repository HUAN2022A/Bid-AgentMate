#!/usr/bin/env python3
"""标书工作台一次性迁移：为既有 sqlite/postgres 表补列 + create_all 建新表。

无 Alembic（见 main.py bootstrap 注释），列变更走本脚本；新表（check_runs/findings/
score_estimates）由 create_all 兜底。幂等：缺列才 ALTER。

用法（backend 目录）：uv run python scripts/migrate_workbench.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from app.core.database import Base, SessionLocal, engine
from app.models import (  # noqa: F401 注册全部模型（含 workbench 三张新表）
    chapter,
    copilot,
    file_object,
    material,
    outline,
    project,
    scoring_item,
    tech_requirement,
    tender_file,
    user,
    workbench,
)


# (表, 列, DDL 片段)
COLUMNS = [
    ("projects", "mode", "VARCHAR(16) DEFAULT 'draft' NOT NULL"),
    ("projects", "bid_outline_version", "INTEGER DEFAULT 0 NOT NULL"),
    ("outline_drafts", "doc_kind", "VARCHAR(8) DEFAULT 'tender' NOT NULL"),
    ("outline_snapshots", "doc_kind", "VARCHAR(8) DEFAULT 'tender' NOT NULL"),
    ("findings", "fix", "JSON"),
]


def main() -> None:
    Base.metadata.create_all(bind=engine)  # 新表 + 新库一步到位
    insp = inspect(engine)
    existing = {t for t, _, _ in COLUMNS if insp.has_table(t)}
    with SessionLocal() as db:
        for table, column, ddl in COLUMNS:
            if table not in existing:
                print(f"跳过 {table}（表不存在，create_all 已按新结构建表）")
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column in cols:
                print(f"OK {table}.{column} 已存在")
                continue
            db.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            print(f"ALTER {table} ADD COLUMN {column}")
        db.commit()
    print("迁移完成")


if __name__ == "__main__":
    main()
