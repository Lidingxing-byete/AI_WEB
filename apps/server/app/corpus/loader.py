"""L1 规则库 / L2 知识卡片加载。

语料来源：**本项目自撰**（docs/corpus/），不含手册原文、不含本地知识库原文。
加载器只做解析，不做任何改写。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import CARDS_DIR, RULES_PATH

CARD_SEP = "<!-- card -->"


@dataclass
class Rule:
    rule_id: str
    symptom: str
    candidate_cause: str
    check_items: list[str] = field(default_factory=list)
    supporting_evidence: list[str] = field(default_factory=list)
    refuting_evidence: list[str] = field(default_factory=list)
    source_ref: str = ""
    applicable_chip: str = ""
    library: str = ""
    not_applicable_note: str = ""
    verification_steps: list[str] = field(default_factory=list)
    linked_case: str = ""


@dataclass
class Card:
    card_id: str
    title: str
    tags: list[str]
    body: str
    source_ref: str = ""
    applicable_chip: str = ""
    not_applicable_note: str = ""


_FIELD_RE = re.compile(r"^\*\*(.+?)\*\*[：:]\s*(.*)$")


def _parse_card(block: str) -> Card | None:
    """解析单个卡片块。"""
    lines = [l.rstrip() for l in block.strip().splitlines()]
    if not lines:
        return None

    title_line = ""
    for l in lines:
        if l.startswith("## "):
            title_line = l[3:].strip()
            break
    if not title_line:
        return None

    # "card-01 · USART 在 STM32F103 上的总线归属"
    m = re.match(r"^(card-\d+)\s*·\s*(.*)$", title_line)
    if m:
        card_id, title = m.group(1).strip(), m.group(2).strip()
    else:
        card_id, title = "", title_line

    fields: dict[str, str] = {}
    key = None
    buf: list[str] = []
    for l in lines:
        fm = _FIELD_RE.match(l)
        if fm:
            if key:
                fields[key] = "\n".join(buf).strip()
            key = fm.group(1).strip()
            buf = [fm.group(2).strip()]
        elif key:
            buf.append(l.strip())
    if key:
        fields[key] = "\n".join(buf).strip()

    tags = re.findall(r"`([^`]+)`", fields.get("标签", ""))

    return Card(
        card_id=card_id,
        title=title,
        tags=tags,
        body=fields.get("正文", ""),
        source_ref=fields.get("source_ref", ""),
        applicable_chip=fields.get("applicable_chip", ""),
        not_applicable_note=fields.get("不适用警告", ""),
    )


@lru_cache(maxsize=1)
def load_rules() -> tuple[Rule, ...]:
    """加载 L1 规则库（l1_rules_uart.json）。"""
    if not RULES_PATH.exists():
        raise FileNotFoundError(f"规则库不存在：{RULES_PATH}")
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    rules = []
    for raw in data.get("rules", []):
        rules.append(Rule(
            rule_id=raw.get("rule_id", ""),
            symptom=raw.get("symptom", ""),
            candidate_cause=raw.get("candidate_cause", ""),
            check_items=raw.get("check_items", []),
            supporting_evidence=raw.get("supporting_evidence", []),
            refuting_evidence=raw.get("refuting_evidence", []),
            source_ref=raw.get("source_ref", ""),
            applicable_chip=raw.get("applicable_chip", ""),
            library=raw.get("library", ""),
            not_applicable_note=raw.get("not_applicable_note", ""),
            verification_steps=raw.get("verification_steps", []),
            linked_case=raw.get("linked_case", ""),
        ))
    return tuple(rules)


@lru_cache(maxsize=1)
def load_cards() -> tuple[Card, ...]:
    """加载 L2 知识卡片（cards/*.md，按 `<!-- card -->` 切分）。"""
    if not CARDS_DIR.exists():
        raise FileNotFoundError(f"卡片目录不存在：{CARDS_DIR}")
    cards: list[Card] = []
    for path in sorted(CARDS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        # 只取第一个分隔标记之后的内容，跳过文件头说明
        parts = text.split(CARD_SEP)
        for block in parts[1:]:
            card = _parse_card(block)
            if card and card.body:
                cards.append(card)
    return tuple(cards)


def rules_by_id() -> dict[str, Rule]:
    return {r.rule_id: r for r in load_rules()}
