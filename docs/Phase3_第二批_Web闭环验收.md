# Phase 3 第二批交付 · Web 诊断闭环验收

> 证据标签：本文所有测试结果均为**实际运行输出** `[实测-仿真]`（本机 HTTP 调用与构建，非硬件实测）。
> 未做任何性能或准确率的推断性宣称。
> 时间：2026-10-25 15:30　作者：WorkBuddy Agent　批准人：Zero

---

## 一、本次交付

第一批做的是**诊断内核**（5 个工具 + BM25 + 状态机，CLI 可跑）。
第二批把它接成**完整的 Web 闭环**：后端服务、持久化、事件流、三栏工作台。

| 层 | 新增内容 | 位置 |
|---|---|---|
| 服务 | FastAPI 应用，19 个端点 | `apps/server/app/main.py` |
| 持久化 | SQLite 9 张表 + 3 张索引，证据标签 CHECK 约束 | `apps/server/app/db.py` |
| 事件流 | SSE，推**后端真实执行过程** | `main.py: diagnose_stream` |
| 前端 | React 18 + TS + Vite + Tailwind + Zustand 三栏工作台 | `apps/web/src/` |
| 测试 | 端到端冒烟脚本，19 项断言 | `apps/server/smoke_e2e.py` |

---

## 二、三条硬约束的落点

### 1. SSE 推的是真实 trace，不是前端动画

第一批的 `DiagnosticEngine.run()` 是一次性返回报告。第二批给它加了 `on_event`
流式回调（`orchestrator.py`），引擎**每完成一步**就投递事件：

```
run(req, on_event=...)  →  内部 push(TraceStep)  →  emit("step_done", {...})
```

API 层用「线程 + 队列」承接：引擎在独立线程跑，主线程边取边推。
因此前端时间线的每一条都对应引擎真实执行的一步。

已验证的完整事件流（C1 故障版）：

```
step_start        诊断开始 / STM32F103C8T6
step_done   #1    parse_build_log    skipped  未提供编译日志
step_done   #2    analyze_c_code     called   USART_Init 1 / GPIO_Init 2 / NVIC_Init 0
step_done   #3    check_uart_config  called   未通过 1 / 通过 6 / 无法判定 6
step_done   #4    calc_baud_error    called   115200 → BRR=0x0271，误差 0.000%
hypothesis_update R-S1-01  支持度 1.5
retrieval_done    命中 5 条
step_done   #5    search_reference   called   命中 5 条
done              status=ok  hypothesis_count=1  unknown_count=6
```

### 2. 实测结果只能由用户填写

| 层 | 保障 |
|---|---|
| 接口层 | `TestResultIn.observed` 与 `match` **无默认值**，pydantic 缺失即 422 |
| 数据库层 | `test_results.observed` / `match` 均 `NOT NULL` 且无默认值 |
| 前端层 | 回填表单提示「实测结果只能由你填写，系统不会代填」 |

**已验证**：缺失 `observed` 时返回 **422**（冒烟测试第 11 项）。

### 3. 证据标签贯穿三层

| 层 | 机制 | 验证结果 |
|---|---|---|
| Python 类型系统 | `Finding` / `Candidate` 构造时校验，非法即 `ValueError` | 第一批已验证 |
| 服务层 | `_tag()` 做显示标签→机器短码映射，**无法映射即抛错，不静默降级** | 本次修掉了映射缺失导致的真实 bug |
| 数据库 | 6 张表的 `evidence_tag` 列 `NOT NULL` + `CHECK` 约束 | 见下表 4/4 |

数据库层约束反向测试（实际执行）：

| 尝试写入的标签 | 期望 | 实际 |
|---|---|---|
| `HW_TEST` | 通过 | ✅ 通过 |
| `[静态分析]`（显示标签，未经映射） | 拒绝 | ✅ 拒绝 |
| `MEASURED_HW`（臆造） | 拒绝 | ✅ 拒绝 |
| `''`（空） | 拒绝 | ✅ 拒绝 |

> **为什么不静默降级**：把无法识别的标签悄悄记成 `STATIC`，
> 等于让「来源不明」的证据伪装成静态分析结论——正是本系统要防的事。

---

## 三、端到端冒烟测试（19/19 通过）

运行方式（需先启动后端）：

```bash
cd apps/server
python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
python smoke_e2e.py
```

实际输出：

```
====================================================================
芯诊 AI · Phase 3 第二批端到端冒烟测试
====================================================================
✅ 健康检查通过  — 规则 23 条 / 卡片 40 条
✅ [c01_no_usart1_clock] SSE 无引擎错误
✅ [c01_no_usart1_clock] SSE 含 hypothesis_update 事件
✅ [c01_no_usart1_clock] SSE 含 retrieval_done 事件
✅ [c01_no_usart1_clock] 检出 R-S1-01  — 实际 ['R-S1-01']
✅ [c01_no_usart1_clock] done 事件计数一致  — hypothesis_count=1
✅ 所有证据标签合法  — 实际 {'STATIC'}
✅ 产出 unknown 项（知道自己不知道）  — 6 项
✅ 知识卡片命中且均带来源引用  — 5 条
✅ 生成验证步骤  — 3 步
✅ 缺失 observed 时拒绝（422）  — 实际 422
✅ 回填成功且标签为 HW_TEST  — HTTP 200
✅ [c01_fixed] SSE 无引擎错误
✅ [c01_fixed] SSE 含 hypothesis_update 事件
✅ [c01_fixed] SSE 含 retrieval_done 事件
✅ [c01_fixed] 正确版不误报  — 实际检出 []
✅ 空材料判 insufficient（不硬给根因）  — status=insufficient
✅ Markdown 报告结构完整  — 1737 字符
✅ 报告含证据等级标注
====================================================================
结果：19/19 通过
====================================================================
```

**其中三项最能说明问题**：

- **正确版不误报**（第 16 项）：只会喊"有问题"的系统没有价值，正确配置下沉默才是本事
- **空材料判 insufficient**（第 17 项）：不给材料就只说"材料不足"，不硬凑根因
- **缺失 observed 即 422**（第 11 项）：红线在接口层真的拦得住

---

## 四、本次修掉的三个真实 bug

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | SSE 末尾报 `非法证据标签: '[静态分析]'` | 引擎用**显示标签**，数据库 CHECK 用**机器短码**，缺映射 | `db.py` 加 `DISPLAY_TO_CODE`，`_tag()` 做映射 |
| 2 | 落库报 `'Finding' object has no attribute 'tool'` | `Finding` 的字段实际叫 `source` | 改为 `f.source` |
| 3 | 空材料时 SSE 无 `done` 事件，前端流不收敛 | 材料不足分支**提前 return**，跳过了末尾的 `emit("done")` | 提前返回前补发 `done` |

另外：`EvidenceTag` 是 `str` 子类枚举，其 `hash` 基于成员名而非值，
直接查表会不命中——`_tag()` 中先取 `.value`。这个坑已写进代码注释。

---

## 五、前端

三栏工作台（`320px / 自适应 / 380px`）：

| 栏 | 内容 |
|---|---|
| 左 | 任务创建、内置示例（`[演示数据]` 标记）、源码提交、材料列表、历史任务 |
| 中 | 诊断过程时间线（SSE 实时）、无法判定的项、命中的知识卡片（含命中词） |
| 右 | 候选根因（含支持度与状态）、支持证据（带标签）、验证步骤、**实测回填表单** |

证据标签按**可信度**配色，不看文字也能判断结论有多硬：

`[实测-硬件]`绿 > `[实测-仿真]`青 > `[静态分析]`蓝 > `[推断]`琥珀 > `[假设]`橙 > `[待验证]`灰

**构建验证**（实际输出）：

```
vite v5.4.21 building for production...
✓ 50 modules transformed.
dist/assets/index-DV-znMCN.css   12.41 kB │ gzip:  3.02 kB
dist/assets/index-XpEnquA1.js   163.45 kB │ gzip: 52.79 kB
✓ built in 7.61s
```

TypeScript 严格模式通过（`tsc -b` 无错）。

**启动方式**：

```bash
# 后端
cd apps/server
python -m uvicorn app.main:app --host 127.0.0.1 --port 8787

# 前端（另开终端）
cd apps/web
npm run dev        # http://127.0.0.1:5173
```

前端已配置 `/api` 代理到 8787，无需手动处理跨域。

---

## 六、依赖与纪律

**本次新增安装的依赖**（均在 AGENTS.md 批次②白名单内）：

| 环境 | 包 |
|---|---|
| pip（venv 3.13.14） | fastapi 0.162.0、uvicorn 0.54.0、pydantic 2.14.1 |
| npm（Node 22.22.2，项目本地） | react、react-dom、zustand、typescript、vite、@vitejs/plugin-react、tailwindcss、postcss、autoprefixer（136 包） |

未安装任何模型 SDK、向量库或组件库。

**纪律**：未调用任何大模型；未连接或烧录硬件；服务仅监听 `127.0.0.1`；
未修改系统 PATH 或全局环境；样例代码自撰，命名 `Uart1_*` / `Led_*`（避开教程 `Serial_*` 体系，R19）。

---

## 七、已知缺口（不掩饰）

| 缺口 | 影响 | 计划 |
|---|---|---|
| **波特率未做反向比对** | C2 案例（HSE_VALUE 与实际晶振不符）仍无法量化，只能 unknown | Phase 4 补 |
| ISR 函数体未分析 | R-S6-03/04 只能判 unknown | Phase 4 |
| 未接入大模型 | 无自然语言叙述整合，结论是结构化条目 | Phase 4（ModelRouter 三级降级链） |
| 只有 C1 一个案例的样例代码 | 其余 5 个案例的故障版/正确版待写 | Phase 4–5 |
| 无硬件实测证据 | 全部结论仍为 `[静态分析]` | Phase 5 |
| SQLite 无迁移脚本 | 表结构变更需手工处理 | Phase 4 视需要 |

---

## 八、验收结论

**19/19 冒烟测试通过，前端构建通过，数据库约束反向测试 4/4 通过。**

最小诊断闭环已成立：

> 提交源码 → SSE 流式执行 5 个工具 → 产出候选根因（带证据与标签）
> → 给出验证步骤 → 用户回填实测 → 候选状态更新 → 生成 Markdown 报告

**Phase 3 第二批交付完成，等待验收。**

---

## 九、下一步选项

1. **验收 Phase 3，进入 Phase 4**（工具与 Agent 工作流，14h，11/07–11/10）
   —— 补波特率反向比对、ISR 体分析、ModelRouter 三级降级、其余 5 个案例样例
2. **先补 Phase 2.5 目标 B**（自撰基准工程上板）—— 用已写好的 `firmware/baseline_usart1/main.c`
3. **先写 6 个案例的故障版/正确版样例代码** —— 为 Phase 5 实测做准备

按阶段审批制，未获批准不自动推进。
