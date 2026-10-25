"""芯诊 AI —— FastAPI 服务入口。

Phase 3 第二批。

三条硬约束在本文件中落地：
1. **SSE 推真实执行过程**：诊断引擎在独立线程中运行，通过 `on_event`
   回调把每一步实时投递到队列，主线程边取边推。
   前端时间线是**后端真实 trace**，不是前端伪造的动画。
2. **实测结果只能由用户填写**：`observed` / `match` 缺失即 422，
   且不存在任何服务端填充入口。
3. **证据标签贯穿全程**：写库时标签必须合法，否则 SQLite CHECK 约束拒绝。
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import db
from app.config import CHIP_F103C8T6, LLM_ENABLED, LLM_MODEL, LLM_TIMEOUT_S, OLLAMA_HOST
from app.engine.orchestrator import DiagnosticEngine, DiagnosticRequest
from app.engine.serialcap import match_phenomena, parse_serial_capture
from app.corpus.loader import load_cards, rules_by_id

app = FastAPI(title="芯诊 AI", version="0.1.0",
              description="证据驱动的 STM32 故障诊断工作台")

# 仅允许本机前端开发服务器
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:4173", "http://127.0.0.1:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine = DiagnosticEngine()


# ======================================================================
# Schemas
# ======================================================================
class TaskCreate(BaseModel):
    title: str = "未命名诊断任务"
    chip: str = CHIP_F103C8T6["name"]
    symptom_text: str = ""
    pclk2_hz: int | None = None
    pclk1_hz: int | None = None
    actual_hse_hz: int | None = None
    # Phase 4：用户实测波特率（反向比对用）。提交即视为用户提供的实测观测。
    observed_baud: float | None = None
    observed_baud_source: str = ""


class TaskPatch(BaseModel):
    title: str | None = None
    symptom_text: str | None = None
    status: str | None = None
    pclk2_hz: int | None = None
    pclk1_hz: int | None = None
    actual_hse_hz: int | None = None
    observed_baud: float | None = None
    observed_baud_source: str | None = None


class HardwareTestIn(BaseModel):
    """硬件实测记录（Phase 5）。

    红线落地：
    - `raw_text` 必填且非空 —— 记录必须由用户提交，系统无自动生成入口。
    - `recorded_by` 必填 —— 每条实测都要能追溯到记录人。
    - `evidence_tag` **只允许 HW_TEST / SIM_TEST**：
      把"静态分析/推断"存进实测表正是本项目要防的事，接口层先挡一道，
      数据库 CHECK 再挡一道。
    """
    kind: str = Field(
        "serial_capture",
        pattern="^(serial_capture|manual|scope|logic_analyzer)$",
    )
    raw_text: str = Field(..., min_length=1)
    recorded_by: str = Field(..., min_length=1)
    case_id: str = ""
    preset: str = ""
    observed_baud: float | None = None
    evidence_tag: str = Field("HW_TEST", pattern="^(HW_TEST|SIM_TEST)$")
    note: str = ""


class ArtifactIn(BaseModel):
    kind: str = "source_code"      # source_code / build_log / serial_capture
    name: str = "main.c"
    content: str = ""


class TestResultIn(BaseModel):
    """实测结果回填。

    `observed` 与 `match` **均无默认值**：缺失即 422。
    这是红线 #2 的接口层保障——服务端不替用户编造观测值。
    """
    observed: str = Field(..., min_length=1)
    match: str = Field(..., pattern="^(match|mismatch|partial|unknown)$")
    recorded_by: str = Field(..., min_length=1)
    raw_ref: str | None = None


# ======================================================================
# 基础
# ======================================================================
@app.get("/api/health")
def health() -> dict:
    cards = load_cards()
    return {
        "ok": True,
        "version": "0.1.0",
        "model_layer": {
            "provider": "none",
            "level": "rule",
            "degraded": False,
            "note": "Phase 3 决策：先纯规则层，不接模型",
        },
        "corpus": {"rules": len(rules_by_id()), "cards": len(cards)},
    }


@app.get("/api/models/status")
def models_status() -> dict:
    return {
        "levels": [
            {"level": "L1", "provider": "cloud", "available": False,
             "reason": "Phase 3 未接入"},
            {"level": "L2", "provider": "ollama", "available": False,
             "reason": "Phase 3 未接入"},
            {"level": "L3", "provider": "rule", "available": True,
             "reason": "确定性规则层，始终可用"},
        ],
        "current": "L3",
    }


# ======================================================================
# 任务
# ======================================================================
@app.post("/api/tasks")
def create_task(body: TaskCreate) -> dict:
    conn = db.connect()
    tid = db.new_id("t")
    ts = db.now()
    conn.execute(
        "INSERT INTO tasks (id,title,chip,symptom_text,status,overall_tag,"
        "pclk2_hz,pclk1_hz,actual_hse_hz,observed_baud,observed_baud_source,"
        "created_at,updated_at,deleted)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
        (tid, body.title, body.chip, body.symptom_text, "created",
         "STATIC", body.pclk2_hz, body.pclk1_hz, body.actual_hse_hz,
         body.observed_baud, body.observed_baud_source, ts, ts),
    )
    conn.commit()
    return db.row_to_dict(conn.execute(
        "SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())


@app.get("/api/tasks")
def list_tasks() -> dict:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE deleted=0 ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    return {"items": db.rows_to_list(rows)}


@app.get("/api/tasks/{tid}")
def get_task(tid: str) -> dict:
    return _task_or_404(tid)


@app.patch("/api/tasks/{tid}")
def patch_task(tid: str, body: TaskPatch) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    fields, values = [], []
    for k, v in body.model_dump(exclude_none=True).items():
        fields.append(f"{k}=?")
        values.append(v)
    if not fields:
        return _task_or_404(tid)
    fields.append("updated_at=?")
    values.append(db.now())
    values.append(tid)
    conn.execute(f"UPDATE tasks SET {','.join(fields)} WHERE id=?", values)
    conn.commit()
    return _task_or_404(tid)


@app.delete("/api/tasks/{tid}")
def delete_task(tid: str) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    conn.execute("UPDATE tasks SET deleted=1, updated_at=? WHERE id=?",
                 (db.now(), tid))
    conn.commit()
    return {"id": tid, "deleted": True}


# ======================================================================
# 材料
# ======================================================================
@app.post("/api/tasks/{tid}/artifacts")
def add_artifact(tid: str, body: ArtifactIn) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    aid = db.new_id("a")
    conn.execute(
        "INSERT INTO artifacts (id,task_id,kind,name,content,size_bytes,created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (aid, tid, body.kind, body.name, body.content,
         len(body.content.encode("utf-8")), db.now()),
    )
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (db.now(), tid))
    conn.commit()
    return db.row_to_dict(conn.execute(
        "SELECT id,task_id,kind,name,size_bytes,created_at FROM artifacts"
        " WHERE id=?", (aid,)).fetchone())


@app.get("/api/tasks/{tid}/artifacts")
def list_artifacts(tid: str) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    rows = conn.execute(
        "SELECT id,task_id,kind,name,size_bytes,created_at FROM artifacts"
        " WHERE task_id=? ORDER BY created_at", (tid,)).fetchall()
    return {"items": db.rows_to_list(rows)}


@app.get("/api/tasks/{tid}/artifacts/{aid}")
def get_artifact(tid: str, aid: str) -> dict:
    conn = db.connect()
    r = conn.execute(
        "SELECT * FROM artifacts WHERE id=? AND task_id=?", (aid, tid)).fetchone()
    if r is None:
        raise HTTPException(404, "artifact not found")
    return db.row_to_dict(r)


# ======================================================================
# 诊断（SSE）
# ======================================================================
# SSE 单步等待上限。必须大于本地模型冷启动时间（7B 权重载入显存可达数十秒），
# 否则"模型还在加载"会被误报成"诊断超时"。
_SSE_WAIT_S = max(60.0, LLM_TIMEOUT_S + 60)


def _load_request(tid: str, use_llm: bool = False) -> DiagnosticRequest:
    """从库里取出任务与材料，组装诊断请求。"""
    task = _task_or_404(tid)
    conn = db.connect()
    rows = conn.execute(
        "SELECT kind, content FROM artifacts WHERE task_id=? ORDER BY created_at",
        (tid,)).fetchall()
    source_parts, build_parts = [], []
    for r in rows:
        if r["kind"] == "source_code":
            source_parts.append(r["content"])
        elif r["kind"] == "build_log":
            build_parts.append(r["content"])
    # Phase 5：取最新一条串口抓帧类实测记录参与诊断。
    # 只取 kind='serial_capture' 且经用户提交的记录，绝不在此合成任何内容。
    hw = conn.execute(
        "SELECT kind, raw_text, evidence_tag, recorded_by, preset"
        " FROM hardware_tests WHERE task_id=? AND kind='serial_capture'"
        " ORDER BY created_at DESC LIMIT 1", (tid,)).fetchone()
    serial_cap, serial_src = "", ""
    if hw is not None:
        serial_cap = hw["raw_text"]
        serial_src = (f"{hw['recorded_by']} 采集（样例 {hw['preset'] or '未标注'}，"
                      f"标签 {hw['evidence_tag']}）")
    return DiagnosticRequest(
        source_code="\n".join(source_parts),
        symptom_text=task["symptom_text"] or "",
        chip=task["chip"],
        build_log="\n".join(build_parts),
        actual_hse_hz=task["actual_hse_hz"],
        pclk2_hz=task["pclk2_hz"],
        pclk1_hz=task["pclk1_hz"],
        observed_baud=task["observed_baud"],
        observed_baud_source=task["observed_baud_source"] or "",
        serial_capture=serial_cap,
        serial_capture_source=serial_src,
        use_llm=use_llm,
    )


@app.get("/api/tasks/{tid}/diagnose")
def diagnose_stream(tid: str, use_llm: bool = False) -> StreamingResponse:
    """SSE：触发诊断并流式推送**真实执行过程**。

    `use_llm=1` 时启用模型解读（T-08）。默认关闭：
    默认路径保持纯规则层，不依赖模型可用性。
    """
    _task_or_404(tid)
    req = _load_request(tid, use_llm=use_llm)
    if use_llm:
        _engine.llm = _llm_service()

    q: queue.Queue[tuple[str, dict] | None] = queue.Queue()

    def worker() -> None:
        try:
            def on_event(etype: str, data: dict) -> None:
                q.put((etype, data))
            report = _engine.run(req, on_event=on_event)
            _persist_report(tid, report)
            q.put(None)          # 结束哨兵
        except Exception as exc:                      # noqa: BLE001
            q.put(("error", {"code": "ENGINE_ERROR",
                             "message": str(exc), "recoverable": True}))
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen() -> Iterator[bytes]:
        yield _sse("step_start", {"seq": 0, "name": "诊断开始",
                                  "chip": req.chip})
        start = time.time()
        while True:
            try:
                # 超时必须覆盖模型冷启动：7B 权重首次载入显存可达数十秒，
                # 沿用 60s 会把"模型还在加载"误报成"诊断超时"。
                item = q.get(timeout=_SSE_WAIT_S)
            except queue.Empty:
                yield _sse("error", {"code": "TIMEOUT",
                                     "message": f"诊断超时（>{_SSE_WAIT_S}s）",
                                     "recoverable": True})
                break
            if item is None:
                break
            etype, data = item
            yield _sse(etype, data)
        yield _sse("closed", {"duration_ms": int((time.time() - start) * 1000)})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/tasks/{tid}/reassess")
def reassess(tid: str, use_llm: bool = False) -> dict:
    """补充材料后**重新诊断**（同步端点）。

    用途：用户在回填实测波特率 / 实际晶振等信息后再次触发诊断，
    走的是与 SSE 完全相同的引擎路径，因此结论与流式诊断一致——
    不存在"两条诊断逻辑"。返回本次新产生的候选与仍需补充的信息。
    """
    _task_or_404(tid)
    req = _load_request(tid, use_llm=use_llm)
    if use_llm:
        _engine.llm = _llm_service()
    report = _engine.run(req)
    _persist_report(tid, report)
    return {
        "status": report.status,
        "overall_tag": report.overall_tag,
        "symptom": report.symptom,
        "hypotheses": [c.to_dict() for c in report.candidates],
        "unknowns": report.unknowns,
        "followups": report.followups,
        "limitations": report.limitations,
        "tool_runs": [t.to_dict() for t in report.trace],
        "llm_narrative": report.llm_narrative,
    }


# ======================================================================
# 模型层状态（只读探测，不加载模型、不下载任何模型）
# ======================================================================
_llm: Any = None


def _llm_service() -> Any:
    """惰性创建模型服务：构造引擎/启动服务时都不应触发网络探测。"""
    global _llm
    if _llm is None:
        from app.llm.service import LlmService
        _llm = LlmService()
    return _llm


@app.get("/api/llm/status")
def llm_status(probe: bool = True) -> dict:
    """模型层可用状态。供前端显示"模型在线/离线"，避免假装在跑。"""
    svc = _llm_service()
    ok, why = svc.probe(force=probe)
    return {
        "enabled": svc.enabled,
        "provider": "ollama-local",
        "host": OLLAMA_HOST,
        "model": LLM_MODEL,
        "available": ok,
        "reason": why,
        "models": svc.client.list_models() if ok else [],
        # 云端未配置时明确为空——不允许把"未启用"说成"已接入"
        "cloud_provider": "",
        "note": ("模型仅用于解释已查出的结论与补充验证建议，"
                 "输出恒为 [推断]，不参与根因判定；不可用时自动降级"),
    }


@app.get("/api/tasks/{tid}/llm-calls")
def get_llm_calls(tid: str) -> dict:
    """本任务最近一次诊断的模型调用记录（元数据，不含提示词原文）。"""
    _task_or_404(tid)
    conn = db.connect()
    rows = conn.execute(
        "SELECT provider,model,ok,degraded,reason,duration_ms,error,"
        "prompt_chars,response_chars,eval_count,temperature,summary,"
        "extra_steps,cautions,created_at FROM llm_calls WHERE task_id=?"
        " ORDER BY created_at DESC LIMIT 5", (tid,)).fetchall()
    items = []
    for r in rows:
        d = db.row_to_dict(r)
        for k in ("extra_steps", "cautions"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except json.JSONDecodeError:
                d[k] = []
        # 补成与 SSE 事件同构的结构，前端可当 LlmNarrative 直接用
        d["available"] = bool(d.get("ok"))
        d["tag"] = "[推断]"
        d["call"] = {
            "model": d.get("model", ""),
            "ok": bool(d.get("ok")),
            "duration_ms": d.get("duration_ms", 0),
            "error": d.get("error", ""),
            "eval_count": d.get("eval_count"),
            "temperature": d.get("temperature"),
        }
        items.append(d)
    return {"items": items}


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _snapshot_test_results(conn: Any, tid: str) -> dict[tuple[str, int, str], dict]:
    """重诊断前把用户已回填的实测结果取快照。

    为什么必须有这一步：`_persist_report` 会重建 `verifications`（新 id），
    而 `test_results` 通过 `verification_id` 外键挂着。若不快照迁移，
    用户做完实验回填的实测记录会在下一次诊断时被清空或悬空——
    等于"系统把你做过的实验忘了"，直接违反
    「用户记录实测结果，Agent 根据结果更新诊断」这一核心流程。

    匹配键用 (规则号, 步骤序号, 步骤文本)：这三项在同一任务内稳定，
    重诊断后即使候选 id 变化也能挂回原处。
    """
    rows = conn.execute(
        "SELECT h.rule_id, v.seq, v.action, t.observed, t.match,"
        " t.recorded_by, t.raw_ref, t.evidence_tag, t.created_at"
        " FROM test_results t"
        " JOIN verifications v ON v.id = t.verification_id"
        " JOIN hypotheses h ON h.id = v.hypothesis_id"
        " WHERE v.task_id=? ORDER BY t.created_at", (tid,)).fetchall()
    snap: dict[tuple[str, int, str], dict] = {}
    for r in rows:
        # 同一键多次回填时保留最近一次
        snap[(r["rule_id"], r["seq"], r["action"])] = dict(r)
    return snap


def _replay_test_results(
    conn: Any, tid: str, snap: dict[tuple[str, int, str], dict], ts: int
) -> list[dict]:
    """把快照的实测结果挂回重建后的验证步骤，并据此更新候选状态与排序。

    返回本次被重新应用的记录，供调用方写入日志/事件。
    """
    if not snap:
        return []

    applied: list[dict] = []
    pairs = conn.execute(
        "SELECT v.id AS vid, v.seq, v.action, h.id AS hid, h.rule_id, h.score"
        " FROM verifications v JOIN hypotheses h ON h.id = v.hypothesis_id"
        " WHERE v.task_id=?", (tid,)).fetchall()

    touched: dict[str, str] = {}
    for p in pairs:
        old = snap.get((p["rule_id"], p["seq"], p["action"]))
        if old is None:
            continue
        conn.execute(
            "INSERT INTO test_results (id,verification_id,observed,match,"
            "recorded_by,evidence_tag,raw_ref,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (db.new_id("tr"), p["vid"], old["observed"], old["match"],
             old["recorded_by"], old["evidence_tag"], old["raw_ref"],
             old["created_at"]),
        )
        # 状态回补：与 submit_test_result 保持同一套规则
        match = old["match"]
        if match == "mismatch":
            touched[p["hid"]] = "refuted"
        elif match == "match":
            touched[p["hid"]] = "confirmed"
        applied.append({"rule_id": p["rule_id"], "seq": p["seq"],
                        "match": match, "observed": old["observed"][:80]})

    for hid, status in touched.items():
        row = conn.execute(
            "SELECT score FROM hypotheses WHERE id=?", (hid,)).fetchone()
        if not row:
            continue
        base = row["score"]
        score_to = round(base * 0.25, 3) if status == "refuted" else round(
            min(base * 1.5, 3.0), 3)
        conn.execute("UPDATE hypotheses SET status=?, score=? WHERE id=?",
                     (status, score_to, hid))

    _rerank_hypotheses(conn, tid)
    return applied


def _rerank_hypotheses(conn: Any, tid: str) -> None:
    """重排候选顺序：已排除的沉底，已确认的靠前，其余按支持度降序。

    这是"根据用户反馈更新诊断"的可见结果：用户排除一条，它就该往下掉，
    而不是仍排在第一位让人误以为系统没听见。
    """
    rows = conn.execute(
        "SELECT id,status,score FROM hypotheses WHERE task_id=?", (tid,)).fetchall()
    order = {"confirmed": 0, "active": 1, "refuted": 2}
    ranked = sorted(rows, key=lambda r: (order.get(r["status"], 1), -r["score"]))
    for i, r in enumerate(ranked):
        conn.execute("UPDATE hypotheses SET rank=? WHERE id=?", (i + 1, r["id"]))


def _persist_report(tid: str, report: Any) -> None:
    """把诊断结果落库。证据标签非法会在此被 SQLite CHECK 拒绝。"""
    conn = db.connect()
    ts = db.now()

    # 先快照用户已回填的实测结果，重建后原样挂回（见 _snapshot_test_results）
    carried = _snapshot_test_results(conn, tid)

    conn.execute("DELETE FROM hypotheses WHERE task_id=?", (tid,))
    conn.execute("DELETE FROM unknowns WHERE task_id=?", (tid,))
    conn.execute("DELETE FROM retrieval_hits WHERE task_id=?", (tid,))
    conn.execute("DELETE FROM verifications WHERE task_id=?", (tid,))
    conn.execute("DELETE FROM tool_invocations WHERE task_id=?", (tid,))
    conn.execute("DELETE FROM task_followups WHERE task_id=?", (tid,))
    conn.execute("DELETE FROM llm_calls WHERE task_id=?", (tid,))

    # 工具调用留痕：入参摘要 / 出参摘要 / 耗时 / 失败原因全部落库。
    # Phase 4 验收项"记录工具输入输出、执行状态和失败原因"在此落地。
    for i, step in enumerate(report.trace):
        conn.execute(
            "INSERT INTO tool_invocations (id,task_id,seq,tool_name,status,"
            "reason,input_sum,output_sum,duration_ms,error,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (db.new_id("ti"), tid, step.seq, step.tool, step.status,
             step.reason, step.input_sum or "", step.summary or "",
             step.duration_ms or 0, step.error, ts),
        )

    for i, item in enumerate(getattr(report, "followups", [])):
        conn.execute(
            "INSERT INTO task_followups (id,task_id,seq,item,created_at)"
            " VALUES (?,?,?,?,?)",
            (db.new_id("fu"), tid, i + 1, item, ts),
        )

    # 模型调用元数据（PROJECT_PLAN 六·3）。
    # 只存元数据与结果摘要——**提示词原文不入库**（含用户源码）。
    nar = getattr(report, "llm_narrative", None)
    if nar:
        call = nar.get("call") or {}
        conn.execute(
            "INSERT INTO llm_calls (id,task_id,provider,model,ok,degraded,"
            "reason,duration_ms,error,prompt_chars,response_chars,eval_count,"
            "temperature,summary,extra_steps,cautions,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.new_id("lc"), tid, nar.get("provider", ""), nar.get("model", ""),
             1 if nar.get("available") else 0,
             1 if nar.get("degraded") else 0,
             nar.get("reason", "")[:400], nar.get("duration_ms", 0),
             (call.get("error") or "")[:300], call.get("prompt_chars", 0),
             call.get("response_chars", 0), call.get("eval_count"),
             call.get("temperature"), nar.get("summary", ""),
             json.dumps(nar.get("extra_steps", []), ensure_ascii=False),
             json.dumps(nar.get("cautions", []), ensure_ascii=False), ts),
        )

    for c in report.candidates:
        hid = db.new_id("h")
        conn.execute(
            "INSERT INTO hypotheses (id,task_id,rule_id,title,detail,score,"
            "evidence_tag,status,rank,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (hid, tid, c.rule_id, c.cause, "", c.score,
             _tag(c.supporting[0].tag if c.supporting else "STATIC"),
             "active", c.rank, ts),
        )
        for f in c.supporting:
            conn.execute(
                "INSERT INTO evidences (id,hypothesis_id,kind,item,detail,"
                "evidence_tag,created_at) VALUES (?,?,?,?,?,?,?)",
                (db.new_id("e"), hid, "violated", f.text, f.source,
                 _tag(f.tag), ts),
            )
        for vi, vs in enumerate(c.verification_steps):
            conn.execute(
                "INSERT INTO verifications (id,task_id,hypothesis_id,seq,action,"
                "expectation,evidence_tag,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (db.new_id("v"), tid, hid, vi + 1, vs, "", "INFER", ts),
            )

    for u in report.unknowns:
        conn.execute(
            "INSERT INTO unknowns (id,task_id,item,need,created_at)"
            " VALUES (?,?,?,?,?)",
            (db.new_id("u"), tid, u.get("text", ""),
             u.get("hint", ""), ts),
        )

    for h in report.references:
        conn.execute(
            "INSERT INTO retrieval_hits (id,task_id,card_id,title,score,"
            "source_ref,matched_terms,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (db.new_id("rh"), tid, h.get("card_id", ""), h.get("title", ""),
             h.get("score", 0.0), h.get("source_ref", ""),
             ",".join(h.get("matched_terms", [])), ts),
        )

    # 用户此前回填的实测结果挂回新生成的验证步骤，并据此更新状态与排序
    _replay_test_results(conn, tid, carried, ts)

    conn.execute(
        "UPDATE tasks SET status=?, overall_tag=?, updated_at=? WHERE id=?",
        ("awaiting_verification" if report.status == "ok" else "insufficient",
         _tag(report.overall_tag), ts, tid),
    )
    conn.commit()


def _tag(t: str) -> str:
    """引擎显示标签 -> 数据库机器短码。

    非法或无法映射的值直接拒绝，**不静默降级**为 STATIC——
    静默降级会让未标注的证据伪装成静态分析结论，正是本系统要防的事。
    注意：`EvidenceTag` 是 `str` 子类枚举，其 `hash` 基于成员名而非值，
    因此必须先取 `.value` 再查表，否则映射不命中。
    """
    raw = getattr(t, "value", t)
    if isinstance(raw, str) and raw in db.VALID_TAGS:
        return raw
    code = db.DISPLAY_TO_CODE.get(raw)
    if code is None:
        raise ValueError(f"非法证据标签: {t!r}")
    return code


# ======================================================================
# 硬件实测记录（Phase 5）
# ======================================================================
@app.post("/api/tasks/{tid}/hw-evidence")
def add_hw_evidence(tid: str, body: HardwareTestIn) -> dict:
    """提交一条硬件实测记录。

    只接受用户实际采集的数据。`serial_capture` 类型的记录会由
    `parse_serial_capture` 做**确定性判定**（EMPTY/GARBLED/NORMAL），
    但判定结果只写进 `verdict` 字段，绝不改写 `raw_text`。

    注意：本端点**不替用户推断根因**。实测现象与候选根因的比对在诊断引擎中
    以 `[推断]` 形式给出，两者在数据层是分开存放的。
    """
    _task_or_404(tid)
    conn = db.connect()
    hid = db.new_id("hw")

    verdict, detail = "", ""
    if body.kind == "serial_capture":
        facts = parse_serial_capture(body.raw_text)
        verdict, detail = facts.verdict, facts.note

    conn.execute(
        "INSERT INTO hardware_tests (id,task_id,case_id,kind,preset,raw_text,"
        "observed_baud,verdict,verdict_detail,evidence_tag,recorded_by,note,"
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (hid, tid, body.case_id, body.kind, body.preset, body.raw_text,
         body.observed_baud, verdict, detail, body.evidence_tag,
         body.recorded_by, body.note, db.now()),
    )
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (db.now(), tid))
    conn.commit()
    return {
        "id": hid,
        "evidence_tag": body.evidence_tag,
        "verdict": verdict,
        "verdict_detail": detail,
        "bytes": len(body.raw_text.encode("utf-8", errors="ignore")),
        "hint": ("已存档。调用 POST /reassess 可让诊断引擎把这段实测现象"
                 "计入证据链（比对结论标 [推断]）"),
    }


@app.get("/api/tasks/{tid}/hw-evidence")
def list_hw_evidence(tid: str) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    rows = conn.execute(
        "SELECT id,case_id,kind,preset,observed_baud,verdict,verdict_detail,"
        "evidence_tag,recorded_by,note,created_at,"
        "LENGTH(raw_text) AS raw_bytes"
        " FROM hardware_tests WHERE task_id=? ORDER BY created_at",
        (tid,)).fetchall()
    return {"items": db.rows_to_list(rows)}


@app.get("/api/tasks/{tid}/hw-evidence/{hid}")
def get_hw_evidence(tid: str, hid: str) -> dict:
    """取单条实测记录**含原文**，供复核与导出。"""
    _task_or_404(tid)
    conn = db.connect()
    r = conn.execute(
        "SELECT * FROM hardware_tests WHERE id=? AND task_id=?",
        (hid, tid)).fetchone()
    if r is None:
        raise HTTPException(404, "hardware test not found")
    return db.row_to_dict(r)


@app.delete("/api/tasks/{tid}/hw-evidence/{hid}")
def delete_hw_evidence(tid: str, hid: str) -> dict:
    """删除误提交的实测记录（例如粘错了串口窗口的内容）。"""
    _task_or_404(tid)
    conn = db.connect()
    cur = conn.execute(
        "DELETE FROM hardware_tests WHERE id=? AND task_id=?", (hid, tid))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "hardware test not found")
    return {"deleted": hid}


# ======================================================================
# 结果查询
# ======================================================================
@app.get("/api/tasks/{tid}/trace")
def get_trace(tid: str) -> dict:
    """工具调用时间线（UI 展示用精简字段）。"""
    _task_or_404(tid)
    conn = db.connect()
    rows = conn.execute(
        "SELECT seq,tool_name,status,reason,output_sum AS summary"
        " FROM tool_invocations WHERE task_id=? ORDER BY seq", (tid,)).fetchall()
    return {"items": db.rows_to_list(rows)}


@app.get("/api/tasks/{tid}/tool-runs")
def get_tool_runs(tid: str) -> dict:
    """工具调用**全量留痕**（审计/评测用）。

    相比 `/trace` 多出 input_sum / duration_ms / error 三项，
    用于回答"这个结论是哪个工具、拿什么输入、跑了多久、有没有出错得出的"。
    """
    _task_or_404(tid)
    conn = db.connect()
    rows = conn.execute(
        "SELECT seq,tool_name,status,reason,input_sum,output_sum,duration_ms,error"
        " FROM tool_invocations WHERE task_id=? ORDER BY seq", (tid,)).fetchall()
    items = db.rows_to_list(rows)
    return {
        "items": items,
        "stats": {
            "total": len(items),
            "called": sum(1 for i in items if i["status"] == "called"),
            "skipped": sum(1 for i in items if i["status"] == "skipped"),
            "error": sum(1 for i in items if i["status"] == "error"),
            "total_ms": sum(i["duration_ms"] or 0 for i in items),
        },
    }


@app.get("/api/tasks/{tid}/followups")
def get_followups(tid: str) -> dict:
    """本轮诊断"还需要补充什么"——多步诊断的下一步入口。"""
    _task_or_404(tid)
    conn = db.connect()
    return {"items": db.rows_to_list(conn.execute(
        "SELECT seq,item FROM task_followups WHERE task_id=? ORDER BY seq",
        (tid,)).fetchall())}


@app.get("/api/tools")
def list_tools() -> dict:
    """工具清单与职责说明（可解释性：系统能做什么，全部摊开）。"""
    return {"items": _engine.registry.describe()}


@app.get("/api/tasks/{tid}/hypotheses")
def get_hypotheses(tid: str) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    hs = conn.execute(
        "SELECT * FROM hypotheses WHERE task_id=? ORDER BY rank", (tid,)).fetchall()
    out = []
    for h in hs:
        d = dict(h)
        d["evidences"] = db.rows_to_list(conn.execute(
            "SELECT kind,item,detail,evidence_tag FROM evidences"
            " WHERE hypothesis_id=?", (h["id"],)).fetchall())
        # 验证步骤带上已回填的实测结果（回填后重新诊断也不会丢，见 _replay_test_results）
        d["verifications"] = db.rows_to_list(conn.execute(
            "SELECT v.id,v.seq,v.action,v.expectation,v.evidence_tag,"
            " t.observed, t.match, t.recorded_by, t.created_at AS recorded_at"
            " FROM verifications v"
            " LEFT JOIN test_results t ON t.verification_id = v.id"
            " WHERE v.hypothesis_id=? ORDER BY v.seq", (h["id"],)).fetchall())
        out.append(d)
    return {"items": out}


@app.get("/api/tasks/{tid}/unknowns")
def get_unknowns(tid: str) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    return {"items": db.rows_to_list(conn.execute(
        "SELECT item,need FROM unknowns WHERE task_id=?", (tid,)).fetchall())}


@app.get("/api/tasks/{tid}/references")
def get_references(tid: str) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    return {"items": db.rows_to_list(conn.execute(
        "SELECT card_id,title,score,source_ref,matched_terms FROM retrieval_hits"
        " WHERE task_id=? ORDER BY score DESC", (tid,)).fetchall())}


# ======================================================================
# 验证与实测回填
# ======================================================================
@app.get("/api/tasks/{tid}/verifications")
def list_verifications(tid: str) -> dict:
    _task_or_404(tid)
    conn = db.connect()
    rows = conn.execute(
        "SELECT v.id,v.seq,v.action,v.expectation,v.evidence_tag,v.hypothesis_id,"
        " h.title AS hypothesis_title,"
        " (SELECT COUNT(*) FROM test_results t WHERE t.verification_id=v.id)"
        "   AS result_count"
        " FROM verifications v LEFT JOIN hypotheses h ON h.id=v.hypothesis_id"
        " WHERE v.task_id=? ORDER BY v.seq", (tid,)).fetchall()
    return {"items": db.rows_to_list(rows)}


@app.post("/api/tasks/{tid}/verifications/{vid}/result")
def submit_test_result(tid: str, vid: str, body: TestResultIn) -> dict:
    """回填实测结果。

    仅接受用户提交的观测值。缺失 `observed` / `match` 时 pydantic 直接 422。
    """
    _task_or_404(tid)
    conn = db.connect()
    v = conn.execute(
        "SELECT * FROM verifications WHERE id=? AND task_id=?", (vid, tid)).fetchone()
    if v is None:
        raise HTTPException(404, "verification not found")

    tr_id = db.new_id("tr")
    conn.execute(
        "INSERT INTO test_results (id,verification_id,observed,match,recorded_by,"
        "evidence_tag,raw_ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (tr_id, vid, body.observed, body.match, body.recorded_by,
         "HW_TEST", body.raw_ref, db.now()),
    )

    # 依据实测结果更新候选状态：mismatch -> refuted；match -> confirmed
    updates: list[dict] = []
    hid = v["hypothesis_id"]
    if hid:
        h = conn.execute("SELECT * FROM hypotheses WHERE id=?", (hid,)).fetchone()
        if h:
            new_status = {"match": "confirmed",
                          "mismatch": "refuted"}.get(body.match, "active")
            score_to = h["score"]
            if new_status == "refuted":
                score_to = round(h["score"] * 0.25, 3)
            elif new_status == "confirmed":
                score_to = round(min(h["score"] * 1.5, 3.0), 3)
            conn.execute(
                "UPDATE hypotheses SET status=?, score=? WHERE id=?",
                (new_status, score_to, hid))
            updates.append({"id": hid, "from": h["score"], "to": score_to,
                            "status": new_status,
                            "reason": f"实测结果：{body.match}"})

    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (db.now(), tid))
    conn.commit()
    return {
        "test_result_id": tr_id,
        "evidence_tag": "HW_TEST",
        "hypothesis_updates": updates,
        "task_status": _task_or_404(tid)["status"],
    }


@app.get("/api/tasks/{tid}/report")
def get_report(tid: str, regenerate: bool = False) -> dict:
    """生成 Markdown 诊断报告。全文带证据标签。"""
    task = _task_or_404(tid)
    conn = db.connect()
    if not regenerate:
        r = conn.execute(
            "SELECT * FROM reports WHERE task_id=? ORDER BY version DESC LIMIT 1",
            (tid,)).fetchone()
        if r:
            return db.row_to_dict(r)

    hs = get_hypotheses(tid)["items"]
    unk = get_unknowns(tid)["items"]
    refs = get_references(tid)["items"]
    trace = get_trace(tid)["items"]
    fu = get_followups(tid)["items"]

    lines = [
        f"# 诊断报告：{task['title']}",
        "",
        f"- 任务 ID：`{task['id']}`",
        f"- 芯片：{task['chip']}",
        f"- 现象：{task['symptom_text'] or '（未填写）'}",
        f"- 整体证据等级：`{db.CODE_TO_DISPLAY.get(task['overall_tag'], task['overall_tag'])}`",
        "",
        "> 根因判定由**确定性规则层 + 诊断工具**完成，结论可追溯到规则号与工具调用。",
        "> 若启用模型解读，模型**只**做解释与补充验证建议，输出恒为 `[推断]`，",
        "> 不参与根因判定与评分；模型不可用时自动降级，不影响本报告的结论。",
        "",
        "## 一、候选根因",
        "",
    ]
    if hs:
        for h in hs:
            lines.append(f"### {h['rank']}. {h['title']}  `{h['evidence_tag']}`")
            lines.append("")
            lines.append(f"- 规则：`{h['rule_id']}`　支持度：{h['score']}　状态：{h['status']}")
            lines.append("")
            lines.append("**支持证据**")
            lines.append("")
            for e in h["evidences"]:
                tag_disp = db.CODE_TO_DISPLAY.get(e['evidence_tag'], e['evidence_tag'])
                lines.append(f"- `{tag_disp}` {e['item']}（来源：{e['detail']}）")
            lines.append("")
            lines.append("**验证步骤与实测回填**")
            lines.append("")
            for v in h["verifications"]:
                lines.append(f"{v['seq']}. {v['action']}")
                if v.get("observed"):
                    verdict = {"match": "与预期一致", "mismatch": "与预期不符",
                               "partial": "部分符合", "unknown": "无法判断"}.get(
                        v.get("match") or "", v.get("match") or "")
                    lines.append(
                        f"   - 实测回填 `[实测-硬件]`：{v['observed']}（判定：{verdict}，"
                        f"记录人：{v.get('recorded_by') or '—'}）")
            lines.append("")
    else:
        lines += ["本次未检出配置违规。", ""]

    lines += ["## 二、无法判定的项（unknown）", ""]
    if unk:
        for u in unk:
            lines.append(f"- {u['item']}")
            if u["need"]:
                lines.append(f"  - 需要：{u['need']}")
    else:
        lines.append("- 无")
    lines.append("")

    lines += ["## 三、参考知识卡片", ""]
    if refs:
        for r in refs:
            lines.append(f"- **{r['title']}**（{r['card_id']}，来源：{r['source_ref']}，得分 {r['score']}）")
    else:
        lines.append("- 无命中")
    lines.append("")

    lines += ["## 四、诊断过程（trace）", ""]
    lines.append("| # | 工具 | 状态 | 说明 |")
    lines.append("|---|---|---|---|")
    for t in trace:
        lines.append(f"| {t['seq']} | `{t['tool_name']}` | {t['status']} | {t['summary'] or t['reason']} |")
    lines.append("")

    lines += ["## 五、下一步需要补充的信息（多步诊断入口）", ""]
    if fu:
        for f in fu:
            lines.append(f"{f['seq']}. {f['item']}")
        lines.append("")
        lines.append("> 补齐上述信息后再次诊断，系统会据此更新候选根因与排序。")
    else:
        lines.append("- 无；现有材料下已无可补充的关键项")
    lines.append("")

    # ---------------- Phase 5：硬件实测记录 ----------------
    hw_rows = conn.execute(
        "SELECT * FROM hardware_tests WHERE task_id=? ORDER BY created_at",
        (tid,)).fetchall()
    lines += ["## 六、硬件实测记录", ""]
    if not hw_rows:
        lines += [
            "**本任务暂无硬件实测记录。**",
            "",
            "因此上文所有结论的证据等级最高只到 `[静态分析]`——",
            "系统只看过代码，没有看过板子。",
            "",
        ]
    else:
        lines.append("| # | 类型 | 案例 | 样例 | 判定 | 记录人 | 标签 |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, h in enumerate(hw_rows, 1):
            tag_disp = db.CODE_TO_DISPLAY.get(h["evidence_tag"], h["evidence_tag"])
            lines.append(
                f"| {i} | {h['kind']} | {h['case_id'] or '—'} | "
                f"{h['preset'] or '—'} | {h['verdict'] or '未判定'} | "
                f"{h['recorded_by']} | `{tag_disp}` |")
        lines.append("")
        for i, h in enumerate(hw_rows, 1):
            tag_disp = db.CODE_TO_DISPLAY.get(h["evidence_tag"], h["evidence_tag"])
            lines.append(f"### 记录 {i}　`{tag_disp}`")
            lines.append("")
            if h["verdict_detail"]:
                lines.append(f"- 工具判定：**{h['verdict']}** —— {h['verdict_detail']}")
            if h["observed_baud"]:
                lines.append(f"- 记录人实测波特率：{h['observed_baud']:.0f} bps")
            if h["note"]:
                lines.append(f"- 备注：{h['note']}")
            lines.append("")
            lines.append("原始记录（未改写）：")
            lines.append("")
            lines.append("```")
            lines.append(h["raw_text"][:2000])
            lines.append("```")
            lines.append("")

        # 现象 ↔ 预期一致性比对（重算，保证报告自包含且可复现）
        cap_row = next((h for h in reversed(hw_rows)
                        if h["kind"] == "serial_capture"), None)
        if cap_row is not None:
            facts = parse_serial_capture(cap_row["raw_text"])
            matches = match_phenomena(facts)
            lines += ["### 实测现象与案例预期的一致性比对　`[推断]`", ""]
            if not matches:
                lines += ["- 抓帧样本不足以判定，未做比对", ""]
            else:
                lines.append("| 案例 | 预期现象 | 实测现象 | 是否一致 | 判别要点 |")
                lines.append("|---|---|---|---|---|")
                for m in matches:
                    lines.append(
                        f"| {m.case_id} | {m.expected_tx} | {m.actual_tx} | "
                        f"{'一致' if m.consistent else '不一致'} | "
                        f"{m.discriminator[:60]} |")
                lines.append("")
                lines += [
                    "> 预期现象来自 `docs/corpus/phenomena_map.json`，"
                    "**全部为未实测的 `[推断]`**。",
                    "> 一致只表示**未被证伪**，不等于根因已被硬件证实；",
                    "> 不一致则可作为排除依据。",
                    "",
                ]

    # ---------------- 模型解读（T-08，可选）----------------
    lc = conn.execute(
        "SELECT * FROM llm_calls WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
        (tid,)).fetchone()
    lines += ["## 七、模型解读（可选增强）", ""]
    if lc is None:
        lines += [
            "本轮**未启用**模型解读，或诊断时尚未接入模型。",
            "",
            "上文全部结论来自确定性规则层与诊断工具的静态检查。",
            "",
        ]
    elif not lc["ok"]:
        lines += [
            f"模型未产出可用输出，已**降级**为规则层结论。",
            "",
            f"- 模型：`{lc['model']}`（{lc['provider']}）",
            f"- 原因：{lc['reason'] or lc['error'] or '未记录'}",
            "",
            "> 降级不等于诊断失败：根因判定本就不依赖模型。",
            "",
        ]
    else:
        lines += [
            f"- 模型：`{lc['model']}`（{lc['provider']}）",
            f"- 耗时：{lc['duration_ms']} ms　温度参数：{lc['temperature']}",
            f"- 生成 token 数：{lc['eval_count'] if lc['eval_count'] is not None else '—'}",
            "",
            f"**解读　`[推断]`（模型输出，非实测、非静态检查结论）**",
            "",
            lc["summary"] or "（空）",
            "",
        ]
        try:
            steps = json.loads(lc["extra_steps"] or "[]")
        except json.JSONDecodeError:
            steps = []
        if steps:
            lines.append("**模型补充的验证建议（待用户执行，尚未验证）**")
            lines.append("")
            for i, s in enumerate(steps, 1):
                lines.append(f"{i}. {s}")
            lines.append("")
        try:
            caut = json.loads(lc["cautions"] or "[]")
        except json.JSONDecodeError:
            caut = []
        if caut:
            lines.append("**注意事项**")
            lines.append("")
            for s in caut:
                lines.append(f"- {s}")
            lines.append("")
        lines += [
            "> 以上为模型基于**已查出结论**生成的解释，标签恒为 `[推断]`。",
            "> 模型不参与根因判定与评分，也不能标注任何证据等级。",
            "",
        ]

    md = "\n".join(lines)
    ver = 1
    if regenerate:
        row = conn.execute(
            "SELECT MAX(version) AS m FROM reports WHERE task_id=?", (tid,)).fetchone()
        ver = (row["m"] or 0) + 1
    rid = db.new_id("r")
    conn.execute(
        "INSERT INTO reports (id,task_id,version,md_content,generated_at)"
        " VALUES (?,?,?,?,?)", (rid, tid, ver, md, db.now()))
    conn.commit()
    return db.row_to_dict(conn.execute(
        "SELECT * FROM reports WHERE id=?", (rid,)).fetchone())


# ======================================================================
# 知识库
# ======================================================================
@app.get("/api/knowledge/cards")
def list_cards() -> dict:
    cards = load_cards()
    return {"items": [{"card_id": c.card_id, "title": c.title,
                       "topic": c.topic, "source_ref": c.source_ref}
                      for c in cards]}


@app.post("/api/knowledge/search")
def search_cards(q: str = "", top_k: int = 5) -> dict:
    from app.corpus.bm25 import BM25
    hits = BM25().search(q, top_k=top_k)
    return {"items": [h.to_dict() for h in hits]}


# ======================================================================
# 内置示例（P1-D7：内置，全部标 [演示数据]）
# ======================================================================
_SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"

# 样例元数据：自撰，技术依据标 [待验证]（未经硬件实测）
# 样例元数据：全部自撰（R19：不引用江协科技教程源码，命名体系亦不沿用）。
# 技术依据未经硬件实测的一律标 [待验证]，由 Phase 5 回填。
SAMPLES_META = {
    "c01_no_usart1_clock": {
        "title": "C1 · USART1 外设时钟未使能",
        "symptom": "串口完全没有输出，但板载LED还在闪烁",
        "case": "C1", "variant": "fault",
        "expect_rules": ["R-S1-01"],
        "note": "故障注入：RCC_APB2PeriphClockCmd 的参数中删去 USART1",
    },
    "c01_fixed": {
        "title": "C1 对照 · 正确配置版本",
        "symptom": "想确认这套配置有没有问题",
        "case": "C1", "variant": "fixed",
        "expect_rules": [],
        "note": "与故障版成对，用于验证系统**不会误报**",
    },
    "c02_hse_mismatch": {
        "title": "C2 · 波特率所依据的时钟与实际不符",
        "symptom": "串口输出全是乱码，但确实有数据在发",
        "case": "C2", "variant": "fault",
        "expect_rules": ["R-S2-01"],
        "note": "故障注入：HSE_VALUE 定义为 12MHz，板载实际 8MHz。"
                "**需用户填入实际晶振频率**才能判定，否则引擎返回 unknown",
    },
    "c02_fixed": {
        "title": "C2 对照 · HSE_VALUE 与实际晶振一致",
        "symptom": "想确认波特率相关配置",
        "case": "C2", "variant": "fixed",
        "expect_rules": [],
        "note": "配合 actual_hse_hz=8000000 使用",
    },
    "c03_tx_not_afpp": {
        "title": "C3 · TX 引脚未配置为复用推挽",
        "symptom": "串口无输出，示波器测 PA9 也没有波形",
        "case": "C3", "variant": "fault",
        "expect_rules": ["R-S1-03"],
        "note": "故障注入：PA9 配成 GPIO_Mode_Out_PP 而非 AF_PP",
    },
    "c03_fixed": {
        "title": "C3 对照 · TX 为复用推挽",
        "symptom": "想确认引脚模式配置",
        "case": "C3", "variant": "fixed",
        "expect_rules": [],
        "note": "PA9 = GPIO_Mode_AF_PP",
    },
    "c04_remap_no_afio": {
        "title": "C4 · 使用重映射但未开启 AFIO 时钟",
        "symptom": "串口完全无输出，线接在 PB6/PB7 上",
        "case": "C4", "variant": "fault",
        "expect_rules": ["R-S1-04"],
        "note": "故障注入：GPIO_PinRemapConfig 已调用，但 RCC 使能参数漏掉 AFIO。"
                "本案例在 Phase 2 标 [待验证]，Phase 5 上板后回填",
    },
    "c04_fixed": {
        "title": "C4 对照 · 重映射前已开 AFIO 时钟",
        "symptom": "想确认重映射配置",
        "case": "C4", "variant": "fixed",
        "expect_rules": [],
        "note": "RCC 参数含 RCC_APB2Periph_AFIO",
    },
    "c05_rx_irq_no_nvic": {
        "title": "C5 · 接收中断未挂接 NVIC（含 ISR 未读 DR）",
        "symptom": "能发不能收，接收中断进不去",
        "case": "C5", "variant": "fault",
        "expect_rules": ["R-S6-01", "R-S6-03"],
        "note": "双重故障：缺 NVIC_Init；ISR 内未读 DR。"
                "**多步诊断演示样本**：check_uart_config 命中 R-S6-01，"
                "analyze_isr_body 进一步命中 R-S6-03",
    },
    "c05_fixed": {
        "title": "C5 对照 · NVIC 与 ISR 均正确",
        "symptom": "想确认接收中断配置",
        "case": "C5", "variant": "fixed",
        "expect_rules": [],
        "note": "NVIC_Init 挂 USART1_IRQn，ISR 内读 DR",
    },
    "c06_printf_no_redirect": {
        "title": "C6 · printf 未重定向",
        "symptom": "初始化看着都对，串口就是没有输出",
        "case": "C6", "variant": "fault",
        "expect_rules": ["R-S5-01"],
        "note": "故障注入：调用 printf 但无 fputc/_write 实现。"
                "本案例在 Phase 2 标 [待验证]，Phase 5 上板后回填",
    },
    "c06_fixed": {
        "title": "C6 对照 · 已实现 fputc 重定向",
        "symptom": "想确认 printf 输出链路",
        "case": "C6", "variant": "fixed",
        "expect_rules": [],
        "note": "含 fputc 重定向；MicroLIB 属工程配置，仍会返回 unknown",
    },
}


@app.get("/api/samples")
def list_samples() -> dict:
    """列出内置示例任务。

    全部标记 `demo: true`，前端必须显示 `[演示数据]`，
    严禁与真实诊断结果混排（P1-D7）。
    """
    items = []
    for key, meta in SAMPLES_META.items():
        p = _SAMPLES_DIR / f"{key}.c"
        items.append({
            "id": key,
            "demo": True,
            "size_bytes": p.stat().st_size if p.exists() else 0,
            **meta,
        })
    # 故障版在前、对照版在后，便于前端成对展示
    items.sort(key=lambda x: (x.get("case", ""), x.get("variant", ""), x["id"]))
    return {"items": items}


@app.get("/api/samples/{sid}")
def get_sample(sid: str) -> dict:
    if sid not in SAMPLES_META:
        raise HTTPException(404, "sample not found")
    p = _SAMPLES_DIR / f"{sid}.c"
    if not p.exists():
        raise HTTPException(404, "sample file missing")
    return {
        "id": sid,
        "demo": True,
        "content": p.read_text(encoding="utf-8"),
        **SAMPLES_META[sid],
    }


# ======================================================================
def _task_or_404(tid: str) -> dict:
    conn = db.connect()
    r = conn.execute("SELECT * FROM tasks WHERE id=? AND deleted=0",
                     (tid,)).fetchone()
    if r is None:
        raise HTTPException(404, "task not found")
    return dict(r)
