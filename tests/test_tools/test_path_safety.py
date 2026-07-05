"""路径安全模块单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.tools.path_safety import (
    _is_sensitive_file,
    _is_within_workspace,
    resolve_and_validate_path,
)
from minicode.utils.exceptions import ToolError


class TestIsWithinWorkspace:
    """测试路径是否在 workspace 范围内。"""

    def test_within_workspace(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub" / "file.txt"
        sub.parent.mkdir(parents=True)
        sub.write_text("hello")
        assert _is_within_workspace(sub, tmp_path) is True

    def test_workspace_root_itself(self, tmp_path: Path) -> None:
        assert _is_within_workspace(tmp_path, tmp_path) is True

    def test_outside_workspace_absolute(self, tmp_path: Path) -> None:
        outside = Path("/tmp/outside.txt")
        # 这种情况下 outside 不在 tmp_path 下
        assert _is_within_workspace(outside, tmp_path) is False

    def test_sibling_outside(self, tmp_path: Path) -> None:
        sibling = tmp_path.parent / "sibling.txt"
        assert _is_within_workspace(sibling, tmp_path) is False

    def test_parent_escape(self, tmp_path: Path) -> None:
        escape = tmp_path / ".." / "escape.txt"
        assert _is_within_workspace(escape, tmp_path) is False


class TestIsSensitiveFile:
    """测试敏感文件检测。"""

    def test_dot_env(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        assert _is_sensitive_file(f) is True

    def test_ssh_key(self, tmp_path: Path) -> None:
        f = tmp_path / ".ssh" / "id_rsa"
        assert _is_sensitive_file(f) is True

    def test_pem_file(self, tmp_path: Path) -> None:
        f = tmp_path / "certificate.pem"
        assert _is_sensitive_file(f) is True

    def test_key_file(self, tmp_path: Path) -> None:
        f = tmp_path / "private.key"
        assert _is_sensitive_file(f) is True

    def test_credentials_json(self, tmp_path: Path) -> None:
        f = tmp_path / "credentials.json"
        assert _is_sensitive_file(f) is True

    def test_normal_py_file(self, tmp_path: Path) -> None:
        f = tmp_path / "main.py"
        assert _is_sensitive_file(f) is False

    def test_normal_text_file(self, tmp_path: Path) -> None:
        f = tmp_path / "README.md"
        assert _is_sensitive_file(f) is False

    def test_dot_git_is_not_sensitive(self, tmp_path: Path) -> None:
        # .git 不在敏感列表中
        f = tmp_path / ".git" / "config"
        assert _is_sensitive_file(f) is False

    def test_gitignore_is_not_sensitive(self, tmp_path: Path) -> None:
        f = tmp_path / ".gitignore"
        assert _is_sensitive_file(f) is False

    def test_env_in_path_component(self, tmp_path: Path) -> None:
        f = tmp_path / "project" / ".env" / "config"
        assert _is_sensitive_file(f) is True

    def test_service_account_json(self, tmp_path: Path) -> None:
        f = tmp_path / "service-account.json"
        assert _is_sensitive_file(f) is True

    def test_token_file(self, tmp_path: Path) -> None:
        f = tmp_path / "token"
        assert _is_sensitive_file(f) is True


class TestResolveAndValidatePath:
    """测试路径解析与验证的完整流程。"""

    def test_normal_relative_path(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "file.txt"
        target.parent.mkdir(parents=True)
        target.write_text("hello")

        result = resolve_and_validate_path("sub/file.txt", tmp_path)
        assert result == target.resolve()

    def test_absolute_path_within_workspace(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("hello")

        result = resolve_and_validate_path(str(target), tmp_path)
        assert result == target.resolve()

    def test_outside_workspace_absolute(self, tmp_path: Path) -> None:
        with pytest.raises(ToolError, match="路径越界"):
            resolve_and_validate_path("/tmp/outside.txt", tmp_path)

    def test_parent_path_escape(self, tmp_path: Path) -> None:
        with pytest.raises(ToolError, match="路径越界"):
            resolve_and_validate_path("../outside.txt", tmp_path)

    def test_sensitive_file_dot_env(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=123")

        with pytest.raises(ToolError, match="敏感文件"):
            resolve_and_validate_path(str(env_file), tmp_path)

    def test_sensitive_file_in_subdir(self, tmp_path: Path) -> None:
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(parents=True)
        key = ssh_dir / "id_rsa"
        key.write_text("private key")

        with pytest.raises(ToolError, match="敏感文件"):
            resolve_and_validate_path(".ssh/id_rsa", tmp_path)

    def test_skip_sensitive_check(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=123")

        # 关闭敏感文件检查，应该通过
        result = resolve_and_validate_path(
            str(env_file), tmp_path, check_sensitive=False
        )
        assert result == env_file.resolve()

    def test_nonexistent_file_allowed(self, tmp_path: Path) -> None:
        # 不存在的路径只要在 workspace 范围内且非敏感，就应该通过
        result = resolve_and_validate_path("new_file.txt", tmp_path)
        assert result == (tmp_path / "new_file.txt").resolve()

    def test_workspace_root_self(self, tmp_path: Path) -> None:
        result = resolve_and_validate_path(str(tmp_path), tmp_path)
        assert result == tmp_path.resolve()

    def test_pem_file_sensitive(self, tmp_path: Path) -> None:
        f = tmp_path / "key.pem"
        f.write_text("content")

        with pytest.raises(ToolError, match="敏感文件"):
            resolve_and_validate_path(str(f), tmp_path)
