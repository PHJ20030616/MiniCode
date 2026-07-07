"""PermissionStore 单元测试。

覆盖 store 的加载、保存、匹配、去重、损坏文件处理等功能。
"""

from __future__ import annotations

import json
from pathlib import Path

from minicode.permissions.store import PermissionStore


class TestPermissionStore:
    """PermissionStore 基础功能测试。"""

    def test_empty_store_when_no_file(self, tmp_path: Path) -> None:
        """permissions.json 不存在时返回空列表。"""
        store = PermissionStore(tmp_path)
        assert store.rules == []

    def test_add_rule_and_persist(self, tmp_path: Path) -> None:
        """添加规则后应持久化到 JSON 文件。"""
        store = PermissionStore(tmp_path)
        store.add_rule("write_file", "src/main.py")

        assert len(store.rules) == 1
        assert store.rules[0].tool_name == "write_file"
        assert store.rules[0].path_pattern == "src/main.py"

        store_file = tmp_path / ".minicode" / "permissions.json"
        assert store_file.exists()
        data = json.loads(store_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "rules" in data
        assert len(data["rules"]) == 1
        assert data["rules"][0]["tool_name"] == "write_file"
        assert data["rules"][0]["path_pattern"] == "src/main.py"

    def test_reload_persisted_rules(self, tmp_path: Path) -> None:
        """新 PermissionStore 实例应加载已有规则。"""
        store1 = PermissionStore(tmp_path)
        store1.add_rule("read_file", "*.py")

        store2 = PermissionStore(tmp_path)
        assert len(store2.rules) == 1
        assert store2.rules[0].tool_name == "read_file"
        assert store2.rules[0].path_pattern == "*.py"

    def test_find_match_exact_path(self, tmp_path: Path) -> None:
        """精确路径匹配。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "src/main.py")

        target = tmp_path / "src" / "main.py"
        assert store.find_match("read_file", [target]) is True

    def test_find_match_wildcard(self, tmp_path: Path) -> None:
        """通配符模式匹配。"""
        store = PermissionStore(tmp_path)
        store.add_rule("write_file", "*.py")

        target = tmp_path / "test.py"
        assert store.find_match("write_file", [target]) is True

    def test_find_match_recursive_wildcard(self, tmp_path: Path) -> None:
        """递归通配符 ** 匹配子目录。"""
        store = PermissionStore(tmp_path)
        store.add_rule("edit_file", "src/**/*.py")

        target = tmp_path / "src" / "utils" / "helper.py"
        assert store.find_match("edit_file", [target]) is True

    def test_find_match_wrong_tool(self, tmp_path: Path) -> None:
        """工具名不匹配时返回 False。"""
        store = PermissionStore(tmp_path)
        store.add_rule("write_file", "*.py")

        target = tmp_path / "test.py"
        assert store.find_match("read_file", [target]) is False

    def test_find_match_non_matching_path(self, tmp_path: Path) -> None:
        """路径不匹配时返回 False。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "src/*.py")

        target = tmp_path / "tests" / "test_main.py"
        assert store.find_match("read_file", [target]) is False

    def test_find_match_outside_workspace(self, tmp_path: Path) -> None:
        """workspace 外的路径不应匹配。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "*")

        outside = Path("/tmp/outside.txt")
        assert store.find_match("read_file", [outside]) is False

    def test_find_match_no_target_paths(self, tmp_path: Path) -> None:
        """空 target_paths 列表返回 False。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "*.py")
        assert store.find_match("read_file", []) is False

    def test_add_rule_dedup(self, tmp_path: Path) -> None:
        """同工具+同路径模式替换旧规则。"""
        store = PermissionStore(tmp_path)
        store.add_rule("write_file", "src/main.py")
        store.add_rule("write_file", "src/main.py")  # 重复添加
        assert len(store.rules) == 1

    def test_clear(self, tmp_path: Path) -> None:
        """清空所有规则。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "*.py")
        store.clear()
        assert store.rules == []

        # 重新加载应为空
        store2 = PermissionStore(tmp_path)
        assert store2.rules == []

    def test_broken_json_file(self, tmp_path: Path) -> None:
        """损坏的 JSON 文件应被静默忽略，返回空列表。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            "not valid json", encoding="utf-8"
        )

        store = PermissionStore(tmp_path)
        assert store.rules == []

    def test_created_at_set_on_add(self, tmp_path: Path) -> None:
        """添加规则时自动设置 created_at（ISO 格式）。"""
        store = PermissionStore(tmp_path)
        rule = store.add_rule("read_file", "test.txt")
        assert rule.created_at != ""
        assert "T" in rule.created_at

    def test_rule_immutable_via_property(self, tmp_path: Path) -> None:
        """store.rules 返回的列表不应影响内部存储。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "a.txt")
        rules_copy = store.rules
        rules_copy.clear()
        assert len(store.rules) == 1

    def test_find_match_subdirectory_not_matching_shallow_pattern(
        self, tmp_path: Path
    ) -> None:
        """子目录路径不应被浅层模式匹配（精确安全）。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "main.py")

        # src/main.py 不应匹配模式 "main.py"（跨目录）
        target = tmp_path / "src" / "main.py"
        assert store.find_match("read_file", [target]) is False

        # 根目录的 main.py 应匹配
        target_root = tmp_path / "main.py"
        assert store.find_match("read_file", [target_root]) is True

    def test_exact_pattern_does_not_cross_directory(self, tmp_path: Path) -> None:
        """规则 'main.py' 不应匹配子目录下的 'src/main.py'。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "main.py")

        target = tmp_path / "src" / "main.py"
        assert store.find_match("read_file", [target]) is False

    def test_exact_pattern_respects_full_path(self, tmp_path: Path) -> None:
        """规则 'src/a.py' 只匹配 src/a.py，不匹配 src/b.py 或其他。"""
        store = PermissionStore(tmp_path)
        store.add_rule("read_file", "src/a.py")

        a_target = tmp_path / "src" / "a.py"
        b_target = tmp_path / "src" / "b.py"
        other_target = tmp_path / "other" / "a.py"

        assert store.find_match("read_file", [a_target]) is True
        assert store.find_match("read_file", [b_target]) is False
        assert store.find_match("read_file", [other_target]) is False

    def test_wildcard_pattern_does_not_cross_directory(self, tmp_path: Path) -> None:
        """通配符 '*.py' 不应匹配子目录下的文件。"""
        store = PermissionStore(tmp_path)
        store.add_rule("write_file", "*.py")

        root_file = tmp_path / "test.py"
        sub_file = tmp_path / "src" / "main.py"

        assert store.find_match("write_file", [root_file]) is True
        assert store.find_match("write_file", [sub_file]) is False

    def test_find_match_partial_targets_returns_false(self, tmp_path: Path) -> None:
        """多个 target_paths 中只匹配一个时返回 False。"""
        store = PermissionStore(tmp_path)
        store.add_rule("edit_file", "doc/*.md")

        targets = [
            tmp_path / "doc" / "readme.md",
            tmp_path / "src" / "main.py",
        ]
        assert store.find_match("edit_file", targets) is False

    def test_find_match_all_targets_match_returns_true(self, tmp_path: Path) -> None:
        """多个 target_paths 全部被规则覆盖时返回 True。"""
        store = PermissionStore(tmp_path)
        store.add_rule("edit_file", "doc/*.md")

        targets = [
            tmp_path / "doc" / "readme.md",
            tmp_path / "doc" / "changelog.md",
        ]
        assert store.find_match("edit_file", targets) is True

    def test_find_match_cross_rule_coverage(self, tmp_path: Path) -> None:
        """多个规则共同覆盖所有 target_paths。"""
        store = PermissionStore(tmp_path)
        store.add_rule("edit_file", "doc/*.md")
        store.add_rule("edit_file", "src/*.py")

        targets = [
            tmp_path / "doc" / "readme.md",
            tmp_path / "src" / "main.py",
        ]
        assert store.find_match("edit_file", targets) is True

    # ─── Schema migration tests ──────────────────────────────────────

    def test_save_uses_new_schema_format(self, tmp_path: Path) -> None:
        """保存的文件应使用 {"rules": [...]} 新格式。"""
        store = PermissionStore(tmp_path)
        store.add_rule("write_file", "src/main.py")

        store_file = tmp_path / ".minicode" / "permissions.json"
        data = json.loads(store_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "rules" in data
        assert isinstance(data["rules"], list)
        assert len(data["rules"]) == 1
        assert data["rules"][0]["tool_name"] == "write_file"

    def test_load_new_schema_format(self, tmp_path: Path) -> None:
        """能加载 {"rules": [...]} 新格式。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            json.dumps({"rules": [{"tool_name": "read_file", "path_pattern": "*.py"}]}),
            encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert len(store.rules) == 1
        assert store.rules[0].tool_name == "read_file"
        assert store.rules[0].path_pattern == "*.py"

    def test_load_old_bare_array_format(self, tmp_path: Path) -> None:
        """能兼容加载旧的裸数组格式。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            json.dumps([{"tool_name": "read_file", "path_pattern": "*.py"}]),
            encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert len(store.rules) == 1
        assert store.rules[0].tool_name == "read_file"
        assert store.rules[0].path_pattern == "*.py"

    def test_load_skips_bad_rule_type_field(self, tmp_path: Path) -> None:
        """字段类型错误的规则被跳过但不影响其他规则加载。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        bad_data = [
            {"tool_name": "read_file", "path_pattern": "good.py"},
            {"tool_name": 123, "path_pattern": "bad.py"},  # tool_name 应为字符串
            {"tool_name": "read_file", "path_pattern": "also_good.py"},
        ]
        (store_dir / "permissions.json").write_text(
            json.dumps(bad_data), encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert len(store.rules) == 2
        assert store.rules[0].path_pattern == "good.py"
        assert store.rules[1].path_pattern == "also_good.py"

    def test_load_skips_bad_rule_in_new_format(self, tmp_path: Path) -> None:
        """新格式中的损坏规则也被跳过。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        bad_data = {
            "rules": [
                {"tool_name": "read_file", "path_pattern": "good.py"},
                {"tool_name": {}, "path_pattern": "bad.py"},  # tool_name 类型错误
            ],
        }
        (store_dir / "permissions.json").write_text(
            json.dumps(bad_data), encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert len(store.rules) == 1
        assert store.rules[0].path_pattern == "good.py"

    def test_load_non_dict_non_list_clears(self, tmp_path: Path) -> None:
        """根数据既不是 dict 也不是 list 时返回空。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            '"just a string"', encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert store.rules == []

    def test_load_missing_rules_key_uses_empty_list(self, tmp_path: Path) -> None:
        """dict 格式但缺少 rules 键时使用空列表。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            json.dumps({"version": 2}), encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert store.rules == []

    def test_load_rules_is_none_uses_empty_list(self, tmp_path: Path) -> None:
        """{"rules": null} 视为空规则列表。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            json.dumps({"rules": None}), encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert store.rules == []

    def test_load_rules_is_number_uses_empty_list(self, tmp_path: Path) -> None:
        """{"rules": 123} 视为空规则列表。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            json.dumps({"rules": 123}), encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert store.rules == []

    def test_load_rules_is_string_uses_empty_list(self, tmp_path: Path) -> None:
        """{"rules": "bad"} 视为空规则列表。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            json.dumps({"rules": "bad"}), encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert store.rules == []

    def test_load_rules_is_dict_uses_empty_list(self, tmp_path: Path) -> None:
        """{"rules": {"tool_name": "x"}} 视为空规则列表。"""
        store_dir = tmp_path / ".minicode"
        store_dir.mkdir(parents=True)
        (store_dir / "permissions.json").write_text(
            json.dumps({"rules": {"tool_name": "x"}}), encoding="utf-8",
        )

        store = PermissionStore(tmp_path)
        assert store.rules == []
