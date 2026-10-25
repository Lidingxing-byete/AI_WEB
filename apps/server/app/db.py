"""SQLite 持久化层。

设计要点
--------
1. **证据标签在数据库层强制**：`evidence_tag` 列 `NOT NULL` 且带 `CHECK` 约束。
   即使绕过 Python 的类型系统直接写库，非法标签也插不进去。
   这是红线 #2（禁止伪造实测结果）的第二道闸门。

2. **实测结果只能由用户填写**：`test_results` 表的 `observed` 与 `match`
   均为 `NOT NULL` 且无默认值，不存在任何服务端填充入口。

3. 零部署：单文件 SQLite，评审 `git clone` 后无需装数据库。
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

# 与 app.engine.evidence 保持一致的合法标签集合（数据库层独立声明，
# 不 import 引擎模块，避免持久化层依赖诊断逻辑）
VALID_TAGS = (
    "HW_TEST",        # [实测-硬件]
    "SIM_TEST",       # [实测-仿真]
    "STATIC",         # [静态分析]
    "INFER",          # [推断]
    "ASSUMPTION",     # [假设]
    "UNVERIFIED",     # [待验证]
)

# 引擎内部使用**显示标签**（带方括号的中文，直接呈现给用户），
# 数据库列使用**机器短码**（便于 CHECK 约束与索引）。两者一一对应。
DISPLAY_TO_CODE = {
    "[实测-硬件]": "HW_TEST",
    "[实测-仿真]": "SIM_TEST",
    "[静态分析]": "STATIC",
    "[推断]": "INFER",
    "[假设]": "ASSUMPTION",
    "[待验证]": "UNVERIFIED",
}

CODE_TO_DISPLAY = {v: k for k, v in DISPLAY_TO_CODE.items()}

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "xinzhen.db"
_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    chip          TEXT NOT NULL DEFAULT 'STM32F103C8T6',
    symptom_text  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'created',
    overall_tag   TEXT NOT NULL DEFAULT 'STATIC'
                  CHECK (overall_tag IN ('HW_TEST','SIM_TEST','STATIC','INFER','ASSUMPTION','UNVERIFIED')),
    pclk2_hz      INTEGER,
    pclk1_hz      INTEGER,
    actual_hse_hz INTEGER,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    deleted       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS artifacts (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,          -- source_code / build_log / serial_capture
    name       TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_invocations (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    tool_name    TEXT NOT NULL,
    status       TEXT NOT NULL,        -- ok / skipped / error
    reason       TEXT NOT NULL DEFAULT '',
    input_sum    TEXT NOT NULL DEFAULT '',
    output_sum   TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    rule_id      TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '',
    score        REAL NOT NULL DEFAULT 0,
    evidence_tag TEXT NOT NULL
                 CHECK (evidence_tag IN ('HW_TEST','SIM_TEST','STATIC','INFER','ASSUMPTION','UNVERIFIED')),
    status       TEXT NOT NULL DEFAULT 'active',   -- active / refuted / confirmed
    rank         INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS evidences (
    id           TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,        -- violated / ok / unknown
    item         TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '',
    evidence_tag TEXT NOT NULL
                 CHECK (evidence_tag IN ('HW_TEST','SIM_TEST','STATIC','INFER','ASSUMPTION','UNVERIFIED')),
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS unknowns (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    item         TEXT NOT NULL,
    need         TEXT NOT NULL DEFAULT '',   -- 需要什么才能判定
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS verifications (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    hypothesis_id TEXT,
    seq          INTEGER NOT NULL,
    action       TEXT NOT NULL,
    expectation  TEXT NOT NULL DEFAULT '',
    evidence_tag TEXT NOT NULL DEFAULT 'INFER'
                 CHECK (evidence_tag IN ('HW_TEST','SIM_TEST','STATIC','INFER','ASSUMPTION','UNVERIFIED')),
    created_at   REAL NOT NULL
);

-- 实测结果：只能由用户填写，observed / match 均无默认值
CREATE TABLE IF NOT EXISTS test_results (
    id            TEXT PRIMARY KEY,
    verification_id TEXT NOT NULL REFERENCES verifications(id) ON DELETE CASCADE,
    observed      TEXT NOT NULL,
    match         TEXT NOT NULL CHECK (match IN ('match','mismatch','partial','unknown')),
    recorded_by   TEXT NOT NULL,
    evidence_tag  TEXT NOT NULL DEFAULT 'HW_TEST'
                  CHECK (evidence_tag IN ('HW_TEST','SIM_TEST','STATIC','INFER','ASSUMPTION','UNVERIFIED')),
    raw_ref       TEXT,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_hits (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    card_id     TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    score       REAL NOT NULL DEFAULT 0,
    source_ref  TEXT NOT NULL DEFAULT '',
    matched_terms TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL DEFAULT 1,
    md_content  TEXT NOT NULL,
    generated_at REAL NOT NULL
);

-- 硬件实测记录：只能由用户提交，evidence_tag 只允许 HW_TEST / SIM_TEST
-- 把"静态分析"混进实测表正是本项目要防的事，因此在库层直接拒绝。
CREATE TABLE IF NOT EXISTS hardware_tests (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    case_id       TEXT NOT NULL DEFAULT '',     -- C1..C6，可空表示未关联案例
    kind          TEXT NOT NULL,                -- serial_capture / manual / scope / logic_analyzer
    preset        TEXT NOT NULL DEFAULT '',     -- 本次上板使用的样例 id（如 c02_hse_mismatch）
    raw_text      TEXT NOT NULL,                -- 原始记录，不做任何改写
    observed_baud REAL,
    verdict       TEXT NOT NULL DEFAULT '',     -- 工具判定：EMPTY / GARBLED / NORMAL / UNKNOWN
    verdict_detail TEXT NOT NULL DEFAULT '',
    evidence_tag  TEXT NOT NULL DEFAULT 'HW_TEST'
                  CHECK (evidence_tag IN ('HW_TEST','SIM_TEST')),
    recorded_by   TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hw_task ON hardware_tests(task_id);

CREATE TABLE IF NOT EXISTS task_followups (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    item        TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_hyp_task ON hypotheses(task_id);
CREATE INDEX IF NOT EXISTS idx_verif_task ON verifications(task_id);
CREATE INDEX IF NOT EXISTS idx_ti_task ON tool_invocations(task_id);
CREATE INDEX IF NOT EXISTS idx_fu_task ON task_followups(task_id);

-- 模型调用元数据（PROJECT_PLAN 六·3：记录模型名称、关键参数、调用错误）
-- 只存元数据与结果摘要，**不存提示词原文**（含用户源码，避免源码进库）
CREATE TABLE IF NOT EXISTS llm_calls (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    provider       TEXT NOT NULL DEFAULT '',   -- ollama-local / cloud:<name>
    model          TEXT NOT NULL DEFAULT '',
    ok             INTEGER NOT NULL DEFAULT 0,
    degraded       INTEGER NOT NULL DEFAULT 1, -- 1=降级展示规则层结论
    reason         TEXT NOT NULL DEFAULT '',   -- 降级或被守卫拒绝的原因
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT '',
    prompt_chars   INTEGER NOT NULL DEFAULT 0,
    response_chars INTEGER NOT NULL DEFAULT 0,
    eval_count     INTEGER,
    temperature    REAL,
    summary        TEXT NOT NULL DEFAULT '',
    extra_steps    TEXT NOT NULL DEFAULT '',   -- JSON 数组文本
    cautions       TEXT NOT NULL DEFAULT '',   -- JSON 数组文本
    created_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_task ON llm_calls(task_id);
"""

# ---------------------------------------------------------------- 轻量迁移
# 数据库已存在时 `CREATE TABLE IF NOT EXISTS` 不会补列，
# 因此用显式的列检查做增量迁移（评审 clone 的是新库，本机是旧库，两条路都要通）
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("tasks", "observed_baud", "ALTER TABLE tasks ADD COLUMN observed_baud REAL"),
    ("tasks", "observed_baud_source",
     "ALTER TABLE tasks ADD COLUMN observed_baud_source TEXT NOT NULL DEFAULT ''"),
    ("tool_invocations", "error",
     "ALTER TABLE tool_invocations ADD COLUMN error TEXT"),
    ("tool_invocations", "input_sum",
     "ALTER TABLE tool_invocations ADD COLUMN input_sum TEXT NOT NULL DEFAULT ''"),
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """补齐后加的列。幂等：列已存在则跳过。"""
    for table, column, ddl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(ddl)


def db_path() -> Path:
    return _DB_PATH


def connect() -> sqlite3.Connection:
    """每线程独立连接（SQLite 连接非线程安全）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # 并发写等待上限：WAL 模式下多个诊断 worker 同时持久化时，
        # 让请求侧等待而非立刻抛 "database is locked"。
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(SCHEMA)
        _ensure_columns(conn)      # 旧库补列，新库无操作
        conn.commit()
        _local.conn = conn
    return conn


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


def row_to_dict(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
