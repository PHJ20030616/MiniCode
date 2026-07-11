"""记忆管理器 — 核心 CRUD 与索引维护。

管理 .minicode/memory/ 目录下的记忆文件：
- 创建/读取/删除记忆
- MEMORY.md 索引维护（添加/移除/加载）
- 记忆名称验证（仅允许字母、数字、下划线、连字符）
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import ValidationError

from minicode.memory.models import Memory, MemoryMetadata, MemoryScope
from minicode.utils.log import get_logger

logger = get_logger(__name__)

# 索引行格式：- [name](file.md) — description
INDEX_LINE_PATTERN = re.compile(r"^-\s*\[(.+?)\]\((.+?\.md)\)\s*—\s*(.*)")

# 允许的记忆名称字符
VALID_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class MemoryManager:
    """记忆管理器。

    负责记忆文件的 CRUD 操作和索引维护。
    所有记忆文件存储在 ``workspace_root/.minicode/memory/`` 目录下。
    MEMORY.md 作为索引文件记录所有记忆的摘要信息。
    """

    INDEX_FILENAME = "MEMORY.md"

    def __init__(self, workspace_root: Path) -> None:
        """初始化 MemoryManager。

        Args:
            workspace_root: 工作区根路径。
        """
        self._memory_dir = workspace_root / ".minicode" / "memory"
        self._index_path = self._memory_dir / self.INDEX_FILENAME

    @staticmethod
    def _validate_memory_name(name: str) -> None:
        """验证记忆名称是否合法。

        仅允许字母、数字、下划线和连字符，防止路径穿越。

        Args:
            name: 记忆名称。

        Raises:
            ValueError: 名称包含非法字符。
        """
        if not VALID_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"记忆名称 '{name}' 包含非法字符。"
                "仅允许字母、数字、下划线（_）和连字符（-）。"
            )

    # ── 索引文件操作 ───────────────────────────────────────────────

    def _load_index(self) -> list[dict[str, str]]:
        """从 MEMORY.md 加载索引列表。

        Returns:
            索引条目列表，每个条目包含 name、filename、description 键。
            MEMORY.md 不存在或为空时返回空列表。
        """
        if not self._index_path.exists():
            return []

        try:
            text = self._index_path.read_text("utf-8")
        except OSError:
            logger.debug("无法读取索引文件", path=str(self._index_path))
            return []

        entries: list[dict[str, str]] = []
        for line in text.splitlines():
            match = INDEX_LINE_PATTERN.match(line.strip())
            if match:
                entries.append({
                    "name": match.group(1),
                    "filename": match.group(2),
                    "description": match.group(3),
                })
        return entries

    def _save_index(self, entries: list[dict[str, str]]) -> None:
        """将索引条目列表写入 MEMORY.md。

        Args:
            entries: 索引条目列表。
        """
        lines = ["# 记忆索引\n"]
        for entry in entries:
            desc = entry.get("description", "")
            lines.append(f"- [{entry['name']}]({entry['filename']}) — {desc}\n")
        try:
            self._memory_dir.mkdir(parents=True, exist_ok=True)
            self._index_path.write_text("".join(lines), encoding="utf-8")
        except OSError:
            logger.debug("无法写入索引文件", path=str(self._index_path))

    def _update_index(self, metadata: MemoryMetadata) -> None:
        """添加或更新索引中的记忆条目。

        Args:
            metadata: 记忆元数据。
        """
        entries = self._load_index()
        filename = f"{metadata.name}.md"

        # 查找并更新已有条目，或追加新条目
        for entry in entries:
            if entry["name"] == metadata.name:
                entry["filename"] = filename
                entry["description"] = metadata.description
                break
        else:
            entries.append({
                "name": metadata.name,
                "filename": filename,
                "description": metadata.description,
            })

        self._save_index(entries)

    def _remove_from_index(self, name: str) -> None:
        """从索引中移除指定名称的记忆条目。

        Args:
            name: 记忆名称。
        """
        entries = self._load_index()
        original_count = len(entries)
        entries = [e for e in entries if e["name"] != name]
        if len(entries) < original_count:
            self._save_index(entries)

    # ── 公开 CRUD API ─────────────────────────────────────────────

    def add(self, metadata: MemoryMetadata, content: str) -> Path:
        """添加一条新的记忆。

        创建记忆文件并更新索引。

        Args:
            metadata: 记忆元数据。
            content: 记忆正文。

        Returns:
            创建的文件的路径。

        Raises:
            ValueError: 记忆名称包含非法字符。
        """
        self._validate_memory_name(metadata.name)
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._memory_dir / f"{metadata.name}.md"
        file_path.write_text(Memory.format_file(metadata, content), encoding="utf-8")
        self._update_index(metadata)
        logger.debug("记忆已添加", name=metadata.name, path=str(file_path))
        return file_path

    def get(self, name: str) -> Memory | None:
        """获取指定名称的记忆。

        Args:
            name: 记忆名称。

        Returns:
            解析成功的 Memory 实例。文件不存在时返回 None。

        Raises:
            ValueError: 记忆名称包含非法字符。
        """
        self._validate_memory_name(name)
        path = self._memory_dir / f"{name}.md"
        if not path.exists():
            return None
        try:
            content = path.read_text("utf-8")
            return Memory.from_file_content(content)
        except OSError:
            logger.debug("读取记忆文件失败", name=name, path=str(path))
            return None

    def delete(self, name: str) -> bool:
        """删除指定名称的记忆。

        Args:
            name: 记忆名称。

        Returns:
            是否成功删除（True 表示已删除，False 表示文件不存在）。

        Raises:
            ValueError: 记忆名称包含非法字符。
        """
        self._validate_memory_name(name)
        path = self._memory_dir / f"{name}.md"
        if not path.exists():
            return False
        try:
            path.unlink()
            self._remove_from_index(name)
            logger.debug("记忆已删除", name=name)
            return True
        except OSError:
            logger.debug("删除记忆文件失败", name=name, path=str(path))
            return False

    def list_memories(self) -> list[dict[str, str]]:
        """列出所有记忆的摘要信息。

        Returns:
            索引条目列表，每个条目包含 name、filename、description 键。
        """
        return self._load_index()

    # ── 记忆内容聚合 ────────────────────────────────────────────

    def _parse_memory_files(self) -> list[Memory]:
        """遍历记忆目录，解析所有记忆文件。

        跳过 MEMORY.md 和无法解析的损坏文件。
        仅包含具有有效 YAML frontmatter 且 frontmatter 能通过
        ``MemoryMetadata`` 验证的记忆。
        检测同名不同 scope 的冲突并记录 debug 日志。

        Returns:
            解析成功的 Memory 列表。
        """
        if not self._memory_dir.exists():
            return []

        memories: list[Memory] = []
        seen_names: dict[str, MemoryScope] = {}

        try:
            md_files = sorted(self._memory_dir.glob("*.md"))
        except OSError:
            logger.debug("无法读取记忆目录", path=str(self._memory_dir))
            return []

        for fpath in md_files:
            if fpath.name == self.INDEX_FILENAME:
                continue

            try:
                content = fpath.read_text("utf-8")
            except OSError:
                logger.debug("读取记忆文件失败", path=str(fpath))
                continue

            fm, body = Memory.parse_frontmatter(content)
            if not fm:
                logger.debug(
                    "跳过无 frontmatter 的文件",
                    path=str(fpath),
                )
                continue

            try:
                metadata = MemoryMetadata(**fm)
            except ValidationError:
                logger.debug(
                    "跳过 frontmatter 元数据验证失败的文件",
                    path=str(fpath),
                )
                continue

            memory = Memory(metadata=metadata, content=body)
            memories.append(memory)

            # 检测同名不同 scope 的冲突
            name = memory.metadata.name
            if name in seen_names:
                prev_scope = seen_names[name]
                if prev_scope != memory.metadata.scope:
                    logger.debug(
                        "同名记忆存在不同 scope 的冲突",
                        name=name,
                        scope1=prev_scope.value,
                        scope2=memory.metadata.scope.value,
                    )
            else:
                seen_names[name] = memory.metadata.scope

        return memories

    def get_all_content(
        self,
        max_chars: int = 8000,
        workspace: str | None = None,
    ) -> str:
        """获取全部记忆内容，按优先级排序后拼接为文本。

        排序优先级（从高到低）：
        1. scope 匹配当前工作区的记忆优先
        2. 更新时间更近的记忆优先
        3. 置信度更高的记忆优先

        Args:
            max_chars: 返回文本的最大字符数，超出时在最后一个换行处截断。
            workspace: 当前工作区路径。用于过滤 workspace 作用域的记忆。

        Returns:
            格式化后的记忆文本。无记忆时返回空字符串。
        """
        memories = self._parse_memory_files()
        if not memories:
            return ""

        # 排序：scope 匹配优先 > 更新时间 > 置信度
        def _sort_key(m: Memory) -> tuple:
            if workspace is not None:
                scope_priority = 0 if m.metadata.scope == MemoryScope.WORKSPACE else 1
            else:
                scope_priority = 0
            try:
                ts = -m.metadata.updated_at.timestamp()
            except (OSError, OverflowError):
                ts = 0.0
            return (
                scope_priority,
                ts,
                -m.metadata.confidence,
            )

        memories.sort(key=_sort_key)

        # 格式化为文本
        parts: list[str] = []
        for m in memories:
            header = (
                f"--- 记忆：{m.metadata.name}"
                f"（{m.metadata.scope.value}，{m.metadata.confidence}）---\n"
            )
            parts.append(f"{header}{m.content}")

        result = "\n\n".join(parts)

        # 超出 max_chars 时截断
        if max_chars > 0 and len(result) > max_chars:
            truncated = result[:max_chars]
            last_newline = truncated.rfind("\n")
            result = truncated[:last_newline] if last_newline > 0 else truncated
            result += "\n\n...（截断）"

        return result
