# RAG 自建语料（L1 规则库 + L2 知识卡片）

> 建立时间：2026-10-25 | 阶段：Phase 2 补充产出（R13 🔴 高风险项的应对）
> 合规：全部内容**本项目自撰**，仅做章节级引用，**不含官方手册原文段落，不含本地嵌入式知识库原文**（D1 决策约束）。

---

## 一、语料总览

| 层 | 内容 | 数量 | 文件 |
|---|---|---|---|
| **L1** | 结构化 UART 诊断规则库 | **23 条** | `l1_rules_uart.json` |
| **L2** | 知识卡片 | **40 条** | `cards/*.md`（6 个主题文件） |

覆盖率：L1 覆盖全部症状分类 S1–S6，另含 2 条通用规则（编译错误优先、材料不足/超范围）。

---

## 二、文件结构

```
docs/corpus/
├─ README.md                  本文件
├─ l1_rules_uart.json         L1 规则库（23 条）
└─ cards/
   ├─ A_usart_baud.md         card-01 ~ 08  USART 基础与波特率（8）
   ├─ B_gpio_afio.md          card-09 ~ 16  GPIO 与引脚复用（8）
   ├─ C_rcc_clock.md          card-17 ~ 22  RCC 时钟（6）
   ├─ D_nvic_isr.md           card-23 ~ 28  中断与 NVIC（6）
   ├─ E_fault_patterns.md     card-29 ~ 36  常见故障模式（8）
   └─ F_boundary.md           card-37 ~ 40  边界与不适用（4）
```

卡片之间以 HTML 注释分隔标记划分，Phase 3 可按卡片粒度切分后入库。

---

## 三、字段说明

### L1 规则条目

| 字段 | 说明 |
|---|---|
| `rule_id` | 规则标识（如 `R-S1-01`：症状 S1 的第 1 条） |
| `symptom` | 适用症状分类（S1–S6 / 任意） |
| `candidate_cause` | 候选根因 |
| `check_items` | 应执行的确定性检查项（写明对应工具名） |
| `supporting_evidence` | 支持该根因的证据形态 |
| `refuting_evidence` | 可推翻该根因的证据形态 |
| `source_ref` | 章节级引用（如 `RM0008 Ch.19 USART`） |
| `applicable_chip` | 适用芯片 |
| `library` | 适用库（SPL / 不限） |
| `not_applicable_note` | 不适用警告（防跨型号、跨库套用） |
| `verification_steps` | 验证步骤 |
| `linked_case` | 关联的 Phase 2 案例 ID（无则 null） |
| `static_analysis_only` | 是否静态分析无法判定（需用户实测） |
| `priority` | 仅 R-X-01 / R-X-02 为 `highest` |

### L2 知识卡片

| 字段 | 说明 |
|---|---|
| 标题 | `card-NN · 主题` |
| `标签` | 检索用关键词 |
| `正文` | 自撰内容 |
| `source_ref` | 章节级引用 |
| `applicable_chip` | 适用芯片 |
| `不适用警告` | 边界声明（**每条必备**） |

---

## 四、Phase 3 迁移路径

语料当前放在 `docs/corpus/`（内容产出，非代码）。Phase 3 编码启动后：

| 当前位置 | 目标位置 | 说明 |
|---|---|---|
| `docs/corpus/l1_rules_uart.json` | `backend/rag/corpus/rules/` | JSON 可直接加载 |
| `docs/corpus/cards/*.md` | `backend/rag/corpus/cards/` | 按卡片切分为单卡文件或整体切片 |

迁移成本：一次文件移动 + 一个 Markdown 卡片解析器（切分规则已由分隔标记固定）。

---

## 五、待校准项（Phase 5 实测后回填）

| 项 | 当前值 | 状态 |
|---|---|---|
| `calc_baud_error` 容差阈值 | 2% | `[待验证]`，Phase 5 实测校准 |
| card-21「HSI 高波特率更易出问题」 | 原理性推断 | `[推断]`，未经实测量化 |
| card-06「12MHz 晶振按 8MHz 配置 → 偏移约五成」 | 按特定配置推算 | `[推断]` |
| card-30 现象与根因对应关系 | 经验性归纳 | `[推断]` |

**以上各项在 Phase 5 前不得作为已验证结论对外宣称。**

---

## 六、合规自检

| 检查项 | 结果 |
|---|---|
| 全部条目自撰，无手册原文段落 | ✅ |
| 无本地嵌入式知识库原文（D1） | ✅ |
| 每条规则/卡片均有 `source_ref` | ✅（23/23、40/40） |
| 涉及实测的表述均标 `[待验证]` / `[推断]` | ✅ |
| 每条卡片均有「不适用警告」 | ✅（40/40） |
| 未出现「已实测」「准确率达」类表述 | ✅ |
