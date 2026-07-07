# Task 4.1 — 写文件工具（WriteFile）设计文档

> **日期**: 2026-07-07
> **版本**: v0.2
> **状态**: 已确认
> **关联**: [[Task 3.1 权限模型]], [[Task 4.2 精确编辑工具]]

---

## 1. 目标

实现 `write_file` 工具，支持创建文件、覆盖文件、追加内容，并在覆盖已有文件时触发权限确认。自动创建不存在的父目录。

---

## 2. 范围

### 包含

- 创建新文件（overwrite 模式，默认）
- 覆盖已有文件（overwrite 模式）
- 追加内容到文件（append 模式）
- 自动创建父目录
- 参数级权限判断（复用并增强现有 `_check_write_file` 检查器）
- 完整的单元测试覆盖

### 不包含

- 二进制文件写入（v1.x）
- 文件权限/属性设置（v1.x）
- 事务性写入（先写临时文件再替换）（v1.x）
- 目录创建工具（这是文件写入的附带能力）

---

## 3. 工具参数设计

### 参数表

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `file_path` | string | ✅ | - | 目标文件路径（相对工作区根目录的路径，或绝对路径） |
| `content` | string | ✅ | - | 要写入的文本内容 |
| `mode` | string | ❌ | `"overwrite"` | 写入模式：`"overwrite"` 覆盖写入，`"append"` 追加写入 |

### JSON Schema

```json
{
  "type": "object",
  "properties": {
    "file_path": {
      "type": "string",
      "description": "要写入的文件路径（相对工作区根目录的路径，或绝对路径）"
    },
    "content": {
      "type": "string",
      "description": "要写入文件的文本内容"
    },
    "mode": {
      "type": "string",
      "enum": ["overwrite", "append"],
      "description": "写入模式：overwrite=覆盖写入（默认），append=追加到文件末尾",
      "default": "overwrite"
    }
  },
  "required": ["file_path", "content"],
  "additionalProperties": false
}
```

---

## 4. 权限模型

### 4.1 权限矩阵

| 场景 | mode | 文件存在 | 权限级别 | 理由 |
|------|------|----------|----------|------|
| 创建新文件 | overwrite | ❌ | `CAUTION` | 创建新文件，低风险 |
| 创建新文件 | append | ❌ | `CAUTION` | 创建新文件（append 对不存在文件等价于 overwrite），低风险 |
| 覆盖已有文件 | overwrite | ✅ | `DANGEROUS` | 覆盖会丢失原内容 |
| 追加已有文件 | append | ✅ | `CAUTION` | 追加不破坏原内容 |
| workspace 外 | 任意 | 任意 | `DENY` | 安全边界不可逾越 |
| 敏感文件 | 任意 | 任意 | `DENY` | 永不触碰敏感文件 |

### 4.2 修改点

需要修改 `src/minicode/permissions/checker.py` 中的 `_check_write_file` 函数：
- 从 `arguments` 中提取 `mode` 参数（默认 `"overwrite"`）
- append 模式 + 文件已存在 → `CAUTION`（而非 `DANGEROUS`）
- overwrite 模式 + 文件已存在 → `DANGEROUS`（保持现有行为）

---

## 5. 工具实现

### 5.1 文件

- **新建**: `src/minicode/tools/file_write.py`
- **修改**: `src/minicode/permissions/checker.py`（`_check_write_file`）
- **修改**: `src/minicode/tools/__init__.py`（注册 WriteFile）

### 5.2 核心流程

```
1. 参数校验
   ├─ file_path：非空字符串
   ├─ content：字符串（允许空字符串）
   └─ mode：只能为 "overwrite" 或 "append"

2. 路径安全检查（复用 path_safety.resolve_and_validate_path）
   ├─ workspace 越界 → ToolResult(success=False)
   └─ 敏感文件 → ToolResult(success=False)

3. 目标检查
   ├─ 路径指向已存在的目录 → ToolResult(success=False)
   └─ 目标为常规文件 → 根据 mode 决定覆盖/追加

4. 自动创建父目录
   └─ target.parent.mkdir(parents=True, exist_ok=True)

5. 写入内容
   ├─ mode="overwrite" → target.write_text(content, encoding="utf-8")
   └─ mode="append" → 以 "a" 模式打开并写入

6. 返回结果摘要
   ├─ 操作类型（创建 / 覆盖 / 追加）
   ├─ 文件路径
   ├─ 字节数
   └─ 行数
```

### 5.3 返回结果格式

```
操作类型：创建新文件 / 覆盖已有文件 / 追加内容到文件
路径：/absolute/path/to/file.txt
大小：1,234 字节，42 行
```

### 5.4 编码

统一使用 UTF-8，与 `read_file` 保持一致。

---

## 6. 测试计划

### 6.1 工具单元测试 (`tests/test_tools/test_file_write.py`)

| 分类 | 测试用例 | 覆盖场景 |
|------|----------|----------|
| **成功-创建** | `test_create_new_file` | overwrite 模式创建新文件 |
| **成功-创建** | `test_create_new_file_absolute_path` | 绝对路径创建 |
| **成功-创建** | `test_create_new_file_in_subdirectory` | 子目录中创建（自动创建父目录） |
| **成功-创建** | `test_create_deeply_nested_file` | 多层父目录自动创建 |
| **成功-覆盖** | `test_overwrite_existing_file` | overwrite 模式覆盖已有文件 |
| **成功-追加** | `test_append_to_existing_file` | 追加到已有文件末尾 |
| **成功-追加** | `test_append_to_nonexistent_file` | 追加到不存在的文件（自动创建） |
| **成功-内容** | `test_write_empty_content` | 写入空内容 |
| **成功-内容** | `test_write_chinese_content` | 写入中文内容 |
| **成功-内容** | `test_write_multiline_content` | 写入多行内容 |
| **成功-内容** | `test_write_large_content` | 写入较大内容（如 100KB） |
| **成功-内容** | `test_write_special_characters` | 写入特殊字符（换行、制表符等） |
| **成功-mode** | `test_mode_defaults_to_overwrite` | mode 不传时默认 overwrite |
| **错误-参数** | `test_missing_file_path` | file_path 缺失 |
| **错误-参数** | `test_empty_file_path` | file_path 为空字符串 |
| **错误-参数** | `test_whitespace_file_path` | file_path 为纯空白 |
| **错误-参数** | `test_file_path_not_string` | file_path 为非字符串 |
| **错误-参数** | `test_missing_content` | content 缺失 |
| **错误-参数** | `test_content_not_string` | content 为非字符串 |
| **错误-参数** | `test_invalid_mode` | mode 为无效值 |
| **错误-安全** | `test_write_outside_workspace` | workspace 外拒绝 |
| **错误-安全** | `test_write_parent_path_escape` | ../ 逃逸拒绝 |
| **错误-安全** | `test_write_sensitive_file_env` | .env 敏感文件拒绝 |
| **错误-安全** | `test_write_sensitive_file_ssh_key` | SSH 密钥拒绝 |
| **错误-路径** | `test_write_to_directory` | 目标路径是目录 |
| **错误-权限** | `test_write_permission_denied` | 文件系统权限不足（mock） |
| **错误-状态** | `test_no_workspace_root` | workspace_root 未设置 |
| **集成** | `test_schema_compatible` | 工具 schema 符合 OpenAI 格式 |
| **集成** | `test_via_registry` | 通过 ToolRegistry 执行 |
| **集成** | `test_register_builtin_tools` | 注册到默认工具集 |

### 6.2 权限测试补充 (`tests/test_permissions/test_checker.py`)

在现有 `TestWriteFile` 类中补充：

| 测试用例 | 覆盖场景 |
|----------|----------|
| `test_append_new_file_caution` | append 模式创建新文件 → CAUTION |
| `test_append_existing_file_caution` | append 模式追加已有文件 → CAUTION |
| `test_overwrite_new_file_caution` | overwrite 模式创建新文件 → CAUTION（已存在） |
| `test_overwrite_existing_dangerous` | overwrite 模式覆盖已有文件 → DANGEROUS（已存在） |

---

## 7. Agent Loop 中的系统 Prompt

### 7.1 工具描述 Prompt（给模型看）

```
write_file：写入文本内容到文件。支持覆盖写入和追加写入两种模式。
- 默认模式为覆盖写入（overwrite），将完全替换文件内容
- 追加模式（append）会将内容添加到文件末尾
- 如果文件所在的父目录不存在，会自动创建
- 覆盖已有文件时，系统会要求用户确认权限
```

### 7.2 权限确认 Prompt（给用户看）

```
⚠️  write_file 需要覆盖已有文件

工具：write_file
操作：覆盖已有文件
路径：/path/to/existing/file.py
原因：该文件已存在，写入将覆盖原内容

[y] 允许本次  [n] 拒绝  [a] 始终允许此模式
```

---

## 8. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/minicode/tools/file_write.py` | **新建** | `WriteFile` 工具类（~120 行） |
| `tests/test_tools/test_file_write.py` | **新建** | 工具单元测试（~450 行） |
| `src/minicode/permissions/checker.py` | **修改** | `_check_write_file` 适配 mode 参数（~15 行变更） |
| `tests/test_permissions/test_checker.py` | **修改** | 补充 append 模式测试用例（~50 行新增） |
| `src/minicode/tools/__init__.py` | **修改** | 注册 `WriteFile` 到内置工具集（~3 行新增） |

---

## 9. 设计决策记录

| 决策 | 理由 |
|------|------|
| 使用 `path_safety.resolve_and_validate_path` | 复用已有安全检查，避免重复实现 |
| 默认 mode 为 "overwrite" | 与用户直觉一致，"写入"通常意味着覆盖 |
| append 不存在的文件自动创建 | 与 Unix `>>` 重定向行为一致，减少模型负担 |
| 返回操作摘要而非文件内容 | 模型已有 content，避免浪费 token |
| 编码固定 UTF-8 | 与 read_file 保持一致，简化跨平台兼容 |
| 不验证 content 长度上限 | 模型输出长度由 max_tokens 控制，工具层面不限制 |
