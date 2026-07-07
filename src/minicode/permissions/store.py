"""持久化 always-allow 权限规则存储。

将用户标记为"始终允许"的工具调用规则保存到
.minicode/permissions.json 文件中。每条规则包含
工具名、路径模式和时间戳。

规则匹配：
  - 工具名必须完全一致
  - 路径模式使用 workspace 相对 POSIX 格式（如 src/**/*.py）
  - 使用 PurePosixPath.match() 进行路径模式匹配
  - workspace 外的路径永不匹配
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ValidationError


class PermissionRule(BaseModel):
    """一条 always-allow 权限规则。

    Attributes:
        tool_name: 工具名称（如 read_file、write_file）
        path_pattern: workspace 相对 POSIX 路径模式（如 src/**/*.py）
        created_at: 创建时间 ISO 8601 格式字符串
    """

    tool_name: str
    path_pattern: str
    created_at: str = ""


class PermissionStore:
    """持久化的 always-allow 规则存储。

    规则文件路径为 workspace_root/.minicode/permissions.json。
    路径模式使用 workspace 相对 POSIX 格式，不存储绝对路径。

    deny、敏感路径和 workspace 外路径不能被规则命中——
    但这些约束由 check_permission() 保证，store 本身不做二次判断。
    """

    def __init__(self, workspace_root: Path) -> None:
        """初始化 PermissionStore。

        Args:
            workspace_root: 工作区根目录（用于解析相对路径）
        """
        self._workspace_root = workspace_root.resolve()
        self._store_dir = self._workspace_root / ".minicode"
        self._store_path = self._store_dir / "permissions.json"
        self._rules: list[PermissionRule] = []
        self._load()

    # ------------------------------------------------------------------
    # 内部：序列化
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """从 JSON 文件加载规则。

        新版格式：{"rules": [...]}
        旧版格式：[...]（向后兼容）
        损坏的规则或字段类型错误会被静默忽略。
        """
        if not self._store_path.exists():
            self._rules = []
            return
        try:
            raw = json.loads(self._store_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._rules = []
            return

        # 兼容新旧格式
        if isinstance(raw, dict):
            raw_rules = raw.get("rules", [])
            items = raw_rules if isinstance(raw_rules, list) else []
        elif isinstance(raw, list):
            items = raw  # 旧版裸数组格式
        else:
            self._rules = []
            return

        rules: list[PermissionRule] = []
        for item in items:
            try:
                rules.append(PermissionRule(**item))
            except (ValidationError, TypeError, KeyError):
                # 损坏规则被忽略，不阻止整体加载
                continue
        self._rules = rules

    def _save(self) -> None:
        """将规则保存到 JSON 文件（格式：{"rules": [...]}）。"""
        self._store_dir.mkdir(parents=True, exist_ok=True)
        data = {"rules": [r.model_dump() for r in self._rules]}
        self._store_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def add_rule(self, tool_name: str, path_pattern: str) -> PermissionRule:
        """添加一条 always-allow 规则。

        如果同工具+同路径模式的规则已存在，先移除再添加（更新位置）。

        Args:
            tool_name: 工具名称
            path_pattern: workspace 相对 POSIX 路径模式

        Returns:
            新创建的 PermissionRule
        """
        # 去重
        self._rules = [
            r
            for r in self._rules
            if not (r.tool_name == tool_name and r.path_pattern == path_pattern)
        ]
        rule = PermissionRule(
            tool_name=tool_name,
            path_pattern=path_pattern,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._rules.append(rule)
        self._save()
        return rule

    def find_match(self, tool_name: str, target_paths: list[Path]) -> bool:
        """查找是否有匹配的规则。

        需要同时满足：
        - 工具名相同
        - target_paths 中所有路径都被规则覆盖
        - 路径必须在 workspace 范围内

        Args:
            tool_name: 工具名称
            target_paths: 权限判断中的目标路径列表（绝对路径）

        Returns:
            是否存在匹配的规则
        """
        if not target_paths:
            return False

        # 收集此工具的所有规则
        tool_rules = [r for r in self._rules if r.tool_name == tool_name]
        if not tool_rules:
            return False

        # 所有 target path 都必须被至少一条规则覆盖
        for tp in target_paths:
            try:
                rel = tp.relative_to(self._workspace_root)
            except ValueError:
                # 路径不在 workspace 内 → 不匹配
                return False
            rel_posix = rel.as_posix()

            matched = any(
                self._path_matches(rel_posix, rule.path_pattern)
                for rule in tool_rules
            )
            if not matched:
                return False

        return True

    @staticmethod
    def _path_matches(rel_posix: str, path_pattern: str) -> bool:
        """安全检查路径是否匹配模式，防止非预期跨目录匹配。

        对于不含通配符的精确模式使用严格相等比较。
        对于含通配符的模式，如果模式不含目录分隔符，
        则目标路径也不能含目录分隔符（防止 *.py 匹配 src/main.py）。

        Args:
            rel_posix: workspace 相对 POSIX 路径
            path_pattern: 路径模式

        Returns:
            是否匹配
        """
        # 精确模式：必须完全相等
        if '*' not in path_pattern and '?' not in path_pattern and '[' not in path_pattern:
            return rel_posix == path_pattern

        # 通配符模式
        if not PurePosixPath(rel_posix).match(path_pattern):
            return False

        # 防止跨目录：模式不含 '/' 时目标也不能含 '/'
        return not ('/' not in path_pattern and '/' in rel_posix)

    @property
    def rules(self) -> list[PermissionRule]:
        """获取所有规则的副本。"""
        return list(self._rules)

    def clear(self) -> None:
        """清空所有规则（主要用于测试）。"""
        self._rules = []
        self._save()
