"""记忆系统数据模型测试。

覆盖以下场景：
- MemoryMetadata 必填字段验证
- MemoryMetadata 全字段构造
- MemoryMetadata 验证（confidence 范围、枚举值）
- Memory 构造
- frontmatter 解析（正常/无 frontmatter/空内容/损坏）
- 格式化后能被重新解析
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from minicode.memory.models import (
    Memory,
    MemoryMetadata,
    MemoryScope,
    MemorySource,
    MemoryType,
)


class TestMemoryMetadata:
    """MemoryMetadata 模型测试。"""

    def test_required_fields_only(self) -> None:
        """验证仅提供必填字段可以构造。"""
        now = datetime.now()
        meta = MemoryMetadata(name="test-memory", created_at=now, updated_at=now)
        assert meta.name == "test-memory"
        assert meta.description == ""
        assert meta.source == MemorySource.USER
        assert meta.scope == MemoryScope.GLOBAL
        assert meta.confidence == 0.5
        assert meta.type == MemoryType.USER

    def test_all_fields(self) -> None:
        """验证全字段构造。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="full-memory",
            description="A full test memory",
            created_at=now,
            updated_at=now,
            source=MemorySource.CONVERSATION,
            scope=MemoryScope.WORKSPACE,
            confidence=0.9,
            type=MemoryType.FEEDBACK,
        )
        assert meta.name == "full-memory"
        assert meta.description == "A full test memory"
        assert meta.source == MemorySource.CONVERSATION
        assert meta.scope == MemoryScope.WORKSPACE
        assert meta.confidence == 0.9
        assert meta.type == MemoryType.FEEDBACK

    def test_confidence_range_low(self) -> None:
        """验证 confidence 最低为 0.0。"""
        now = datetime.now()
        meta = MemoryMetadata(name="test", created_at=now, updated_at=now, confidence=0.0)
        assert meta.confidence == 0.0

    def test_confidence_range_high(self) -> None:
        """验证 confidence 最高为 1.0。"""
        now = datetime.now()
        meta = MemoryMetadata(name="test", created_at=now, updated_at=now, confidence=1.0)
        assert meta.confidence == 1.0

    def test_confidence_below_zero(self) -> None:
        """验证 confidence 低于 0 时抛出异常。"""
        now = datetime.now()
        with pytest.raises(ValidationError):
            MemoryMetadata(name="test", created_at=now, updated_at=now, confidence=-0.1)

    def test_confidence_above_one(self) -> None:
        """验证 confidence 高于 1 时抛出异常。"""
        now = datetime.now()
        with pytest.raises(ValidationError):
            MemoryMetadata(name="test", created_at=now, updated_at=now, confidence=1.1)

    def test_source_enum_accepts_string(self) -> None:
        """验证 source 接受字符串值。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="test",
            created_at=now,
            updated_at=now,
            source="manual",
        )
        assert meta.source == MemorySource.MANUAL

    def test_source_invalid_value(self) -> None:
        """验证 source 传入无效值时抛出异常。"""
        now = datetime.now()
        with pytest.raises(ValidationError):
            MemoryMetadata(
                name="test",
                created_at=now,
                updated_at=now,
                source="invalid_source",
            )

    def test_scope_enum_accepts_string(self) -> None:
        """验证 scope 接受字符串值。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="test",
            created_at=now,
            updated_at=now,
            scope="workspace",
        )
        assert meta.scope == MemoryScope.WORKSPACE

    def test_type_enum_accepts_string(self) -> None:
        """验证 type 接受字符串值。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="test",
            created_at=now,
            updated_at=now,
            type="project",
        )
        assert meta.type == MemoryType.PROJECT

    def test_missing_name_raises_error(self) -> None:
        """验证 name 为必填字段。"""
        now = datetime.now()
        with pytest.raises(ValidationError):
            MemoryMetadata(created_at=now, updated_at=now)


class TestMemory:
    """Memory 模型测试。"""

    def test_construct(self) -> None:
        """验证 Memory 基本构造。"""
        now = datetime.now()
        meta = MemoryMetadata(name="test", created_at=now, updated_at=now)
        mem = Memory(metadata=meta, content="Hello, world!")
        assert mem.metadata.name == "test"
        assert mem.content == "Hello, world!"

    def test_construct_default_content(self) -> None:
        """验证 content 有默认值。"""
        now = datetime.now()
        meta = MemoryMetadata(name="test", created_at=now, updated_at=now)
        mem = Memory(metadata=meta)
        assert mem.content == ""

    def test_parse_frontmatter_normal(self) -> None:
        """解析含完整 frontmatter 的正常文件。"""
        content = """---
name: test-memory
description: A test memory
created_at: 2026-07-10T10:00:00
updated_at: 2026-07-10T12:00:00
source: user
scope: global
confidence: 0.8
type: user
---

这是记忆正文内容。
"""
        fm, body = Memory.parse_frontmatter(content)
        assert fm["name"] == "test-memory"
        assert fm["confidence"] == 0.8
        assert "这是记忆正文内容。" in body

    def test_parse_frontmatter_minimal(self) -> None:
        """解析仅含必要字段的 frontmatter。"""
        content = """---
name: minimal
created_at: 2026-07-10T10:00:00
updated_at: 2026-07-10T10:00:00
---

Minimal body"""
        fm, body = Memory.parse_frontmatter(content)
        assert fm["name"] == "minimal"
        assert body == "Minimal body"

    def test_parse_no_frontmatter(self) -> None:
        """解析无 frontmatter 的文件。"""
        content = "纯文本内容，没有 frontmatter。"
        fm, body = Memory.parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_parse_empty_content(self) -> None:
        """解析空内容。"""
        fm, body = Memory.parse_frontmatter("")
        assert fm == {}
        assert body == ""

    def test_parse_broken_yaml(self) -> None:
        """解析 YAML 损坏的文件。"""
        content = """---
name: broken
created_at: not-a-date::
: : invalid yaml
---

body
"""
        fm, body = Memory.parse_frontmatter(content)
        assert fm == {}
        assert "body" in body

    def test_parse_frontmatter_only(self) -> None:
        """解析只有 frontmatter 没有正文的文件。"""
        content = """---
name: only-frontmatter
created_at: 2026-07-10T10:00:00
updated_at: 2026-07-10T10:00:00
---
"""
        fm, body = Memory.parse_frontmatter(content)
        assert fm["name"] == "only-frontmatter"
        assert body == ""

    def test_format_then_parse_roundtrip(self) -> None:
        """验证格式化后能被重新解析回相同的 Memory 对象。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="roundtrip",
            description="Round-trip test",
            created_at=now,
            updated_at=now,
            source=MemorySource.CONVERSATION,
            scope=MemoryScope.WORKSPACE,
            confidence=0.75,
            type=MemoryType.FEEDBACK,
        )
        body = "这是经过 round-trip 测试的正文。"
        formatted = Memory.format_file(meta, body)

        # 重新解析
        parsed = Memory.from_file_content(formatted)
        assert parsed.metadata.name == "roundtrip"
        assert parsed.metadata.description == "Round-trip test"
        assert parsed.metadata.confidence == 0.75
        assert parsed.metadata.source == MemorySource.CONVERSATION
        assert parsed.metadata.scope == MemoryScope.WORKSPACE
        assert parsed.metadata.type == MemoryType.FEEDBACK
        assert parsed.content == body

    def test_format_file_has_yaml_delimiters(self) -> None:
        """验证格式化结果包含 --- frontmatter 分隔符。"""
        now = datetime.now()
        meta = MemoryMetadata(name="separators", created_at=now, updated_at=now)
        formatted = Memory.format_file(meta, "body")
        assert formatted.startswith("---\n")
        assert "\n---\n" in formatted

    def test_from_file_content_no_frontmatter(self) -> None:
        """验证无 frontmatter 的文件也能构造 Memory。"""
        mem = Memory.from_file_content("纯文本内容")
        assert mem.metadata.name == "unknown"
        assert mem.content == "纯文本内容"

    def test_from_file_content_empty(self) -> None:
        """验证空内容也能构造 Memory。"""
        mem = Memory.from_file_content("")
        assert mem.metadata.name == "unknown"
