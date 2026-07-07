"""权限模型定义。

提供 PermissionLevel 枚举和 PermissionDecision 数据类，
用于描述参数级权限判断的结果。
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel


class PermissionLevel(StrEnum):
    """权限级别。

    - safe: 自动允许，无需用户确认
    - caution: 需要用户确认（低风险操作）
    - dangerous: 需要用户确认（高风险操作）
    - deny: 永远拒绝，不可绕过
    """

    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"
    DENY = "deny"


class PermissionDecision(BaseModel):
    """权限判断结果。

    Attributes:
        level: 权限级别
        tool_name: 工具名称
        operation: 操作描述（如"读取文件""执行命令"）
        summary: 权限提示摘要，包含工具名、目标路径和操作描述
        target_paths: 操作涉及的目标路径列表
        reasons: 判断依据列表
    """

    level: PermissionLevel
    tool_name: str
    operation: str
    summary: str
    target_paths: list[Path] = []
    reasons: list[str] = []

    @property
    def allowed_without_prompt(self) -> bool:
        """无需用户确认，自动允许。"""
        return self.level == PermissionLevel.SAFE

    @property
    def requires_confirmation(self) -> bool:
        """需要用户确认才能执行。"""
        return self.level in (PermissionLevel.CAUTION, PermissionLevel.DANGEROUS)

    @property
    def denied(self) -> bool:
        """被拒绝，不可绕过。"""
        return self.level == PermissionLevel.DENY
