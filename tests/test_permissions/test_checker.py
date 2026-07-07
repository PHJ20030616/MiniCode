"""权限检查器单元测试。

覆盖 safe / caution / dangerous / deny 四类结果，
以及 trust_mode 下敏感文件仍 deny 的约束。
"""

from __future__ import annotations

from pathlib import Path

from minicode.permissions.checker import check_permission
from minicode.permissions.models import PermissionLevel


class TestReadFile:
    """read_file 工具权限测试。"""

    def test_read_normal_file_safe(self, tmp_path: Path) -> None:
        """读取普通 workspace 文件 → safe。"""
        f = tmp_path / "main.py"
        f.write_text("print('hello')")

        result = check_permission(
            "read_file",
            {"file_path": "main.py"},
            tmp_path,
        )
        assert result.level == PermissionLevel.SAFE
        assert result.allowed_without_prompt is True
        assert result.tool_name == "read_file"
        assert "main.py" in result.summary

    def test_read_normal_file_absolute_path(self, tmp_path: Path) -> None:
        """使用绝对路径读取普通文件 → safe。"""
        f = tmp_path / "src" / "app.py"
        f.parent.mkdir(parents=True)
        f.write_text("code")

        result = check_permission(
            "read_file",
            {"file_path": str(f)},
            tmp_path,
        )
        assert result.level == PermissionLevel.SAFE

    def test_read_outside_workspace_deny(self, tmp_path: Path) -> None:
        """读取 workspace 外路径 → deny。"""
        result = check_permission(
            "read_file",
            {"file_path": "/tmp/outside.txt"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY
        assert result.denied is True
        assert len(result.reasons) > 0
        assert len(result.target_paths) > 0

    def test_read_parent_escape_deny(self, tmp_path: Path) -> None:
        """使用 ../ 逃逸 workspace → deny。"""
        result = check_permission(
            "read_file",
            {"file_path": "../outside.txt"},
            tmp_path,
        )
        # ../outside.txt 会被解析为 tmp_path/../outside.txt → tmp_path.parent/outside.txt
        # 这个路径不在 tmp_path 内 → deny
        assert result.level == PermissionLevel.DENY

    def test_read_sensitive_file_deny(self, tmp_path: Path) -> None:
        """读取敏感文件 .env → deny。"""
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=123")

        result = check_permission(
            "read_file",
            {"file_path": str(env_file)},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY
        assert result.denied is True

    def test_read_sensitive_file_trust_mode_still_deny(self, tmp_path: Path) -> None:
        """trust_mode=True 仍拒绝读取敏感文件。"""
        key = tmp_path / ".ssh" / "id_rsa"
        key.parent.mkdir(parents=True)
        key.write_text("private")

        result = check_permission(
            "read_file",
            {"file_path": ".ssh/id_rsa"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.DENY

    def test_read_missing_file_path_deny(self, tmp_path: Path) -> None:
        """file_path 参数缺失 → deny。"""
        result = check_permission("read_file", {}, tmp_path)
        assert result.level == PermissionLevel.DENY

    def test_read_none_file_path_deny(self, tmp_path: Path) -> None:
        """file_path 为 None → deny。"""
        result = check_permission(
            "read_file",
            {"file_path": None},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_read_empty_file_path_deny(self, tmp_path: Path) -> None:
        """file_path 为空字符串 → deny。"""
        result = check_permission(
            "read_file",
            {"file_path": "   "},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_read_non_existent_file_inside_workspace(self, tmp_path: Path) -> None:
        """读取不存在的文件（路径合法）→ safe。"""
        result = check_permission(
            "read_file",
            {"file_path": "nonexistent.py"},
            tmp_path,
        )
        # 文件虽不存在，但路径在 workspace 内且非敏感 → safe
        assert result.level == PermissionLevel.SAFE

    def test_read_workspace_root_itself(self, tmp_path: Path) -> None:
        """读取 workspace root 本身 → safe。"""
        result = check_permission(
            "read_file",
            {"file_path": str(tmp_path)},
            tmp_path,
        )
        assert result.level == PermissionLevel.SAFE

    def test_read_summary_contains_tool_and_path(self, tmp_path: Path) -> None:
        """summary 包含工具名和目标路径。"""
        result = check_permission(
            "read_file",
            {"file_path": "test.txt"},
            tmp_path,
        )
        assert "read_file" in result.summary
        assert "test.txt" in result.summary or "test.txt" in str(result.target_paths)

    def test_read_deny_paths_present(self, tmp_path: Path) -> None:
        """deny 结果中 target_paths 应有值。"""
        result = check_permission(
            "read_file",
            {"file_path": "/tmp/outside.txt"},
            tmp_path,
        )
        assert len(result.target_paths) > 0
        assert "outside.txt" in str(result.target_paths[0])


class TestGrep:
    """grep 工具权限测试。"""

    def test_grep_with_glob_safe(self, tmp_path: Path) -> None:
        """grep 有限定 glob → safe。"""
        result = check_permission(
            "grep",
            {"pattern": "TODO", "glob": "*.py"},
            tmp_path,
        )
        assert result.level == PermissionLevel.SAFE

    def test_grep_with_specific_glob_safe(self, tmp_path: Path) -> None:
        """grep 有限定 glob（如 src/**/*.py）→ safe。"""
        result = check_permission(
            "grep",
            {"pattern": "class", "glob": "src/**/*.py"},
            tmp_path,
        )
        assert result.level == PermissionLevel.SAFE

    def test_grep_no_glob_caution(self, tmp_path: Path) -> None:
        """grep 无 glob → caution。"""
        result = check_permission(
            "grep",
            {"pattern": "TODO"},
            tmp_path,
        )
        assert result.level == PermissionLevel.CAUTION
        assert result.requires_confirmation is True

    def test_grep_broad_glob_caution(self, tmp_path: Path) -> None:
        """grep 使用 **/* 宽泛 glob → caution。"""
        result = check_permission(
            "grep",
            {"pattern": "TODO", "glob": "**/*"},
            tmp_path,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_grep_none_glob_caution(self, tmp_path: Path) -> None:
        """grep 的 glob 为 None → caution。"""
        result = check_permission(
            "grep",
            {"pattern": "TODO", "glob": None},
            tmp_path,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_grep_empty_glob_caution(self, tmp_path: Path) -> None:
        """grep 的 glob 为空字符串 → caution。"""
        result = check_permission(
            "grep",
            {"pattern": "TODO", "glob": "   "},
            tmp_path,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_grep_missing_pattern_deny(self, tmp_path: Path) -> None:
        """grep 缺少 pattern → deny。"""
        result = check_permission("grep", {}, tmp_path)
        assert result.level == PermissionLevel.DENY

    def test_grep_trust_mode_not_change_level(self, tmp_path: Path) -> None:
        """trust_mode 不改变 caution 等级。"""
        result = check_permission(
            "grep",
            {"pattern": "TODO"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_grep_summary_contains_pattern(self, tmp_path: Path) -> None:
        """grep 的 summary 包含搜索模式关键字。"""
        result = check_permission(
            "grep",
            {"pattern": "def main"},
            tmp_path,
        )
        assert "def main" in result.summary or "def main" in str(result.reasons)

    def test_grep_sensitive_glob_env_deny(self, tmp_path: Path) -> None:
        """grep 的 glob 为 .env → deny（敏感文件）。"""
        result = check_permission(
            "grep",
            {"pattern": "SECRET", "glob": ".env"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY
        assert result.denied is True

    def test_grep_sensitive_glob_pem_deny(self, tmp_path: Path) -> None:
        """grep 的 glob 为 *.pem → deny（敏感扩展名）。"""
        result = check_permission(
            "grep",
            {"pattern": "BEGIN", "glob": "*.pem"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_grep_sensitive_glob_key_deny(self, tmp_path: Path) -> None:
        """grep 的 glob 为 *.key → deny（敏感扩展名）。"""
        result = check_permission(
            "grep",
            {"pattern": "PRIVATE", "glob": "*.key"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_grep_sensitive_glob_ssh_deny(self, tmp_path: Path) -> None:
        """grep 的 glob 为 .ssh/** → deny（敏感路径）。"""
        result = check_permission(
            "grep",
            {"pattern": "ssh", "glob": ".ssh/**"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_grep_sensitive_glob_credentials_deny(self, tmp_path: Path) -> None:
        """grep 的 glob 为 **/credentials.json → deny（敏感文件名）。"""
        result = check_permission(
            "grep",
            {"pattern": "key", "glob": "**/credentials.json"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY


class TestGlob:
    """glob 工具权限测试。"""

    def test_glob_safe(self, tmp_path: Path) -> None:
        """glob 任何合法 pattern → safe。"""
        result = check_permission(
            "glob",
            {"pattern": "**/*.py"},
            tmp_path,
        )
        assert result.level == PermissionLevel.SAFE

    def test_glob_simple_pattern(self, tmp_path: Path) -> None:
        """glob 简单模式 → safe。"""
        result = check_permission(
            "glob",
            {"pattern": "*.txt"},
            tmp_path,
        )
        assert result.level == PermissionLevel.SAFE

    def test_glob_missing_pattern_deny(self, tmp_path: Path) -> None:
        """glob 缺少 pattern → deny。"""
        result = check_permission("glob", {}, tmp_path)
        assert result.level == PermissionLevel.DENY

    def test_glob_summary(self, tmp_path: Path) -> None:
        """glob 的 summary 包含模式。"""
        result = check_permission(
            "glob",
            {"pattern": "src/**/*.ts"},
            tmp_path,
        )
        assert "src/**/*.ts" in result.summary or "src/**/*.ts" in str(result.reasons)

    def test_glob_sensitive_pattern_env_deny(self, tmp_path: Path) -> None:
        """glob 模式 .env → deny（敏感文件）。"""
        result = check_permission(
            "glob",
            {"pattern": ".env"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_glob_sensitive_pattern_ssh_deny(self, tmp_path: Path) -> None:
        """glob 模式 .ssh/** → deny（敏感路径）。"""
        result = check_permission(
            "glob",
            {"pattern": ".ssh/**"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_glob_sensitive_pattern_pem_deny(self, tmp_path: Path) -> None:
        """glob 模式 *.pem → deny（敏感扩展名）。"""
        result = check_permission(
            "glob",
            {"pattern": "*.pem"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_glob_sensitive_pattern_credentials_deny(self, tmp_path: Path) -> None:
        """glob 模式 **/credentials.json → deny（敏感文件名）。"""
        result = check_permission(
            "glob",
            {"pattern": "**/credentials.json"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_glob_sensitive_pattern_key_deny(self, tmp_path: Path) -> None:
        """glob 模式 *.key → deny（敏感扩展名）。"""
        result = check_permission(
            "glob",
            {"pattern": "*.key"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY


class TestWriteFile:
    """write_file 工具权限测试（预留工具）。"""

    def test_write_new_file_caution(self, tmp_path: Path) -> None:
        """创建新文件 → caution。"""
        result = check_permission(
            "write_file",
            {"file_path": "new_file.txt", "content": "hello"},
            tmp_path,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_write_overwrite_existing_dangerous(self, tmp_path: Path) -> None:
        """覆盖已有文件 → dangerous。"""
        f = tmp_path / "existing.txt"
        f.write_text("old content")

        result = check_permission(
            "write_file",
            {"file_path": "existing.txt", "content": "new content"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_write_outside_deny(self, tmp_path: Path) -> None:
        """写入 workspace 外路径 → deny。"""
        result = check_permission(
            "write_file",
            {"file_path": "/tmp/outside.txt", "content": "data"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_write_sensitive_deny(self, tmp_path: Path) -> None:
        """写入敏感文件 → deny。"""
        result = check_permission(
            "write_file",
            {"file_path": ".env", "content": "SECRET=123"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_write_trust_mode_not_change_caution(self, tmp_path: Path) -> None:
        """trust_mode 不改变 caution 等级。"""
        result = check_permission(
            "write_file",
            {"file_path": "new_file.txt", "content": "hello"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_write_trust_mode_not_change_dangerous(self, tmp_path: Path) -> None:
        """trust_mode 不改变 dangerous 等级。"""
        f = tmp_path / "existing.txt"
        f.write_text("old")

        result = check_permission(
            "write_file",
            {"file_path": "existing.txt", "content": "new"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_write_trust_mode_sensitive_still_deny(self, tmp_path: Path) -> None:
        """trust_mode 下写入敏感文件仍 deny。"""
        result = check_permission(
            "write_file",
            {"file_path": ".env", "content": "SECRET=123"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.DENY

    def test_write_missing_file_path_deny(self, tmp_path: Path) -> None:
        """write_file 缺少 file_path → deny。"""
        result = check_permission("write_file", {}, tmp_path)
        assert result.level == PermissionLevel.DENY

    # ------------------------------------------------------------------
    # append 模式测试
    # ------------------------------------------------------------------

    def test_append_new_file_caution(self, tmp_path: Path) -> None:
        """append 模式创建不存在的文件 → CAUTION。"""
        result = check_permission(
            "write_file",
            {"file_path": "new_file.txt", "content": "hello", "mode": "append"},
            tmp_path,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_append_existing_file_caution(self, tmp_path: Path) -> None:
        """append 模式追加到已有文件 → CAUTION。"""
        f = tmp_path / "existing.txt"
        f.write_text("old content")

        result = check_permission(
            "write_file",
            {"file_path": "existing.txt", "content": "more", "mode": "append"},
            tmp_path,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_append_sensitive_file_deny(self, tmp_path: Path) -> None:
        """append 模式写入敏感文件 → DENY。"""
        result = check_permission(
            "write_file",
            {"file_path": ".env", "content": "data", "mode": "append"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_append_outside_workspace_deny(self, tmp_path: Path) -> None:
        """append 模式写入 workspace 外 → DENY。"""
        result = check_permission(
            "write_file",
            {"file_path": "/tmp/outside.txt", "content": "data", "mode": "append"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_append_trust_mode_not_change_caution(self, tmp_path: Path) -> None:
        """trust_mode 不改变 append 的 CAUTION 等级。"""
        f = tmp_path / "existing.txt"
        f.write_text("old")

        result = check_permission(
            "write_file",
            {"file_path": "existing.txt", "content": "more", "mode": "append"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_overwrite_new_file_caution(self, tmp_path: Path) -> None:
        """overwrite 模式创建新文件 → CAUTION（显式验证 mode 参数）。"""
        result = check_permission(
            "write_file",
            {"file_path": "new_file.txt", "content": "hello", "mode": "overwrite"},
            tmp_path,
        )
        assert result.level == PermissionLevel.CAUTION

    def test_invalid_mode_deny(self, tmp_path: Path) -> None:
        """mode 为无效值（如 "delete"）→ DENY。"""
        result = check_permission(
            "write_file",
            {"file_path": "file.txt", "content": "data", "mode": "delete"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY


class TestEditFile:
    """edit_file 工具权限测试（预留工具）。"""

    def test_edit_dangerous(self, tmp_path: Path) -> None:
        """修改普通文件 → dangerous。"""
        f = tmp_path / "app.py"
        f.write_text("original")

        result = check_permission(
            "edit_file",
            {"file_path": "app.py", "old_string": "original", "new_string": "modified"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_edit_outside_deny(self, tmp_path: Path) -> None:
        """编辑 workspace 外文件 → deny。"""
        result = check_permission(
            "edit_file",
            {"file_path": "/etc/passwd", "old_string": "x", "new_string": "o"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_edit_sensitive_deny(self, tmp_path: Path) -> None:
        """编辑敏感文件 → deny。"""
        result = check_permission(
            "edit_file",
            {"file_path": ".env", "old_string": "x", "new_string": "y"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_edit_trust_mode_not_change_dangerous(self, tmp_path: Path) -> None:
        """trust_mode 不改变 dangerous 等级。"""
        result = check_permission(
            "edit_file",
            {"file_path": "app.py", "old_string": "a", "new_string": "b"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.DANGEROUS


class TestDeleteFile:
    """delete_file / remove_file 工具权限测试（预留工具）。"""

    def test_delete_dangerous(self, tmp_path: Path) -> None:
        """删除文件 → dangerous。"""
        f = tmp_path / "old.py"
        f.write_text("code")

        result = check_permission(
            "delete_file",
            {"file_path": "old.py"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_delete_outside_deny(self, tmp_path: Path) -> None:
        """删除 workspace 外文件 → deny。"""
        result = check_permission(
            "delete_file",
            {"file_path": "/tmp/outside.txt"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_delete_sensitive_deny(self, tmp_path: Path) -> None:
        """删除敏感文件 → deny。"""
        result = check_permission(
            "delete_file",
            {"file_path": ".ssh/id_rsa"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_delete_workspace_root_deny(self, tmp_path: Path) -> None:
        """删除 workspace root → deny。"""
        result = check_permission(
            "delete_file",
            {"file_path": str(tmp_path)},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_remove_file_alias(self, tmp_path: Path) -> None:
        """remove_file 别名行为同 delete_file。"""
        result = check_permission(
            "remove_file",
            {"file_path": "some_file.txt"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS


class TestShell:
    """shell 工具权限测试（预留工具）。"""

    def test_shell_default_dangerous(self, tmp_path: Path) -> None:
        """普通 shell 命令 → dangerous。"""
        result = check_permission(
            "shell",
            {"command": "ls -la"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_shell_rm_rf_root_deny(self, tmp_path: Path) -> None:
        """rm -rf / → deny。"""
        result = check_permission(
            "shell",
            {"command": "rm -rf /"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_rm_rf_root_with_flag_variants(self, tmp_path: Path) -> None:
        """rm -rf / 其他写法 → deny。"""
        result = check_permission(
            "shell",
            {"command": "rm -rf /var/log"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS  # 不是根目录
        # 但如果是 rm -rf /  或 rm -rf  / 应该 deny
        result2 = check_permission(
            "shell",
            {"command": "rm -rf  /"},
            tmp_path,
        )
        assert result2.level == PermissionLevel.DENY

    def test_shell_rm_rf_build_dangerous(self, tmp_path: Path) -> None:
        """rm -rf build → dangerous。"""
        result = check_permission(
            "shell",
            {"command": "rm -rf build"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_shell_git_reset_hard_dangerous(self, tmp_path: Path) -> None:
        """git reset --hard → dangerous。"""
        result = check_permission(
            "shell",
            {"command": "git reset --hard HEAD~1"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_shell_format_deny(self, tmp_path: Path) -> None:
        """format 命令 → deny。"""
        result = check_permission(
            "shell",
            {"command": "format D:"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_shutdown_dangerous(self, tmp_path: Path) -> None:
        """shutdown 命令 → dangerous。"""
        result = check_permission(
            "shell",
            {"command": "shutdown /s /t 0"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_shell_remove_item_recurse_root_deny(self, tmp_path: Path) -> None:
        """Remove-Item -Recurse C:\\ → deny。"""
        result = check_permission(
            "shell",
            {"command": "Remove-Item -Recurse C:\\"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_missing_command_deny(self, tmp_path: Path) -> None:
        """缺少 command 参数 → deny。"""
        result = check_permission("shell", {}, tmp_path)
        assert result.level == PermissionLevel.DENY

    def test_shell_empty_command_deny(self, tmp_path: Path) -> None:
        """command 为空 → deny。"""
        result = check_permission(
            "shell",
            {"command": "   "},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_trust_mode_not_change_dangerous(self, tmp_path: Path) -> None:
        """trust_mode 不改变 dangerous 等级。"""
        result = check_permission(
            "shell",
            {"command": "ls -la"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_shell_trust_mode_deny_still_deny(self, tmp_path: Path) -> None:
        """trust_mode 下拒绝级命令仍 deny。"""
        result = check_permission(
            "shell",
            {"command": "rm -rf /"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_rm_rf_wildcard_root_deny(self, tmp_path: Path) -> None:
        """rm -rf /* → deny（通配符根目录）。"""
        result = check_permission(
            "shell",
            {"command": "rm -rf /*"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_sudo_rm_rf_root_deny(self, tmp_path: Path) -> None:
        """sudo rm -rf / → deny。"""
        result = check_permission(
            "shell",
            {"command": "sudo rm -rf /"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_sudo_rm_rf_wildcard_deny(self, tmp_path: Path) -> None:
        """sudo rm -rf /* → deny。"""
        result = check_permission(
            "shell",
            {"command": "sudo rm -rf /*"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_remove_item_recurse_users_deny(self, tmp_path: Path) -> None:
        """Remove-Item -Recurse C:\\Users → deny。"""
        result = check_permission(
            "shell",
            {"command": "Remove-Item -Recurse C:\\Users"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_rm_rf_build_still_dangerous(self, tmp_path: Path) -> None:
        """rm -rf build 仍是 dangerous（项目内破坏）。"""
        result = check_permission(
            "shell",
            {"command": "rm -rf build"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS


class TestUnknownTool:
    """未知工具权限测试。"""

    def test_unknown_tool_dangerous(self, tmp_path: Path) -> None:
        """未注册的工具 → dangerous。"""
        result = check_permission(
            "unknown_tool",
            {"some_param": "value"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DANGEROUS

    def test_unknown_tool_trust_mode_not_change_dangerous(self, tmp_path: Path) -> None:
        """trust_mode 不改变 dangerous 等级。"""
        result = check_permission(
            "unknown_tool",
            {"param": "value"},
            tmp_path,
            trust_mode=True,
        )
        assert result.level == PermissionLevel.DANGEROUS


class TestPermissionDecisionProperties:
    """PermissionDecision 便捷属性测试。"""

    def test_allowed_without_prompt_safe(self, tmp_path: Path) -> None:
        result = check_permission("glob", {"pattern": "*.py"}, tmp_path)
        assert result.allowed_without_prompt is True
        assert result.requires_confirmation is False
        assert result.denied is False

    def test_requires_confirmation_caution(self, tmp_path: Path) -> None:
        result = check_permission("grep", {"pattern": "TODO"}, tmp_path)
        assert result.allowed_without_prompt is False
        assert result.requires_confirmation is True
        assert result.denied is False

    def test_requires_confirmation_dangerous(self, tmp_path: Path) -> None:
        f = tmp_path / "existing.txt"
        f.write_text("data")
        result = check_permission(
            "write_file",
            {"file_path": "existing.txt", "content": "new"},
            tmp_path,
        )
        assert result.allowed_without_prompt is False
        assert result.requires_confirmation is True
        assert result.denied is False

    def test_denied_property(self, tmp_path: Path) -> None:
        result = check_permission(
            "read_file",
            {"file_path": "/etc/passwd"},
            tmp_path,
        )
        assert result.allowed_without_prompt is False
        assert result.requires_confirmation is False
        assert result.denied is True

    def test_summary_contains_context(self, tmp_path: Path) -> None:
        """summary 字段应包含工具名、路径摘要等信息。"""
        result = check_permission(
            "read_file",
            {"file_path": "main.py"},
            tmp_path,
        )
        assert "read_file" in result.summary

    def test_reasons_not_empty_for_deny(self, tmp_path: Path) -> None:
        """deny 结果应有 reasons。"""
        result = check_permission(
            "read_file",
            {"file_path": ".env"},
            tmp_path,
        )
        assert len(result.reasons) > 0

    def test_target_paths_included(self, tmp_path: Path) -> None:
        """路径类操作应有 target_paths。"""
        result = check_permission(
            "read_file",
            {"file_path": "main.py"},
            tmp_path,
        )
        assert len(result.target_paths) > 0


class TestEdgeCases:
    """边界情况测试。"""

    def test_workspace_root_is_file(self, tmp_path: Path) -> None:
        """workspace_root 是文件路径（异常但不应崩溃）。"""
        f = tmp_path / "file.txt"
        f.write_text("data")
        # 用文件路径作为 workspace_root
        result = check_permission(
            "read_file",
            {"file_path": "test.txt"},
            f,  # workspace_root is a file!
        )
        # 不崩溃即可
        assert isinstance(result.level, PermissionLevel)

    def test_special_characters_in_path(self, tmp_path: Path) -> None:
        """路径含特殊字符。"""
        weird_dir = tmp_path / "dir with spaces"
        weird_dir.mkdir(parents=True)
        result = check_permission(
            "read_file",
            {"file_path": "dir with spaces/file.txt"},
            tmp_path,
        )
        assert result.level == PermissionLevel.SAFE

    def test_empty_arguments(self, tmp_path: Path) -> None:
        """空参数字典不崩溃。"""
        result = check_permission("read_file", {}, tmp_path)
        assert result.level == PermissionLevel.DENY

    def test_case_insensitive_sensitive_file(self, tmp_path: Path) -> None:
        """敏感文件检测大小写不敏感。"""
        result = check_permission(
            "read_file",
            {"file_path": ".ENV"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY

    def test_shell_dd_dev_deny(self, tmp_path: Path) -> None:
        """dd 覆写设备 → deny。"""
        result = check_permission(
            "shell",
            {"command": "dd if=/dev/zero of=/dev/sda bs=4M"},
            tmp_path,
        )
        assert result.level == PermissionLevel.DENY
