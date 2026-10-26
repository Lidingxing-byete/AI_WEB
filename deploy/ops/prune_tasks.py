#!/usr/bin/env python3
"""清理演示库：彻底删除 N 天前的任务及其子表数据。

为什么需要：公开演示允许任何人建任务，SQLite 会持续增长；
不清理的话几个月后列表和磁盘都会很难看。

用法：
    python3 prune_tasks.py            # 默认 7 天，打印将删数量
    python3 prune_tasks.py --days 14  # 自定义阈值
    python3 prune_tasks.py --dry-run  # 只统计不删除

设计：先删子表再删主表（外键是逻辑引用，无级联），单事务提交；
只清理「创建时间早于阈值」的任务，不动 7 天内的演示数据。
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

DB = Path("/opt/xinzhen/apps/data/xinzhen.db")

# 直接带 task_id 的子表
CHILD_TABLES = [
    "artifacts", "tool_invocations", "hypotheses", "unknowns", "verifications",
    "retrieval_hits", "reports", "hardware_tests", "task_followups", "llm_calls",
]
# 间接关联（先删这两张，再删上面带 task_id 的表，避免悬空引用）：
#   evidences     -> hypothesis_id
#   test_results  -> verification_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"[skip] 数据库不存在：{DB}")
        return 0

    cutoff = time.time() - args.days * 86400
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM tasks WHERE created_at < ?", (cutoff,))]
    print(f"阈值：{args.days} 天前；命中任务 {len(ids)} 个")

    if not ids or args.dry_run:
        # 统计一下总行数，便于对照
        for t in ("tasks", *CHILD_TABLES, "evidences", "test_results"):
            n = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            print(f"  {t}: {n}")
        conn.close()
        return 0

    marks = ",".join("?" * len(ids))
    try:
        conn.execute("BEGIN")
        conn.execute(
            f"DELETE FROM evidences WHERE hypothesis_id IN "
            f"(SELECT id FROM hypotheses WHERE task_id IN ({marks}))", ids)
        conn.execute(
            f"DELETE FROM test_results WHERE verification_id IN "
            f"(SELECT id FROM verifications WHERE task_id IN ({marks}))", ids)
        for t in CHILD_TABLES:
            conn.execute(f"DELETE FROM {t} WHERE task_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM tasks WHERE id IN ({marks})", ids)
        conn.commit()
    except Exception as exc:                              # noqa: BLE001
        conn.rollback()
        print(f"[fail] 清理失败，已回滚：{exc}")
        conn.close()
        return 1

    print(f"[ok] 已删除 {len(ids)} 个任务及其子表数据")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
