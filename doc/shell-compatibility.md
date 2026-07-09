# Shell 工具跨平台命令兼容矩阵

> **文档版本**: v0.2 · 对应 Task 4.3 Shell 工具
>
> **核心结论**: Windows (PowerShell) 与 Unix (sh/bash) 的 shell 语法**不兼容**。
> Shell 工具自动根据 `sys.platform` 选择正确的 shell，但模型生成的命令
> **必须基于当前平台**，否则命令会执行失败。

---

## 一、平台派发规则

Shell 工具内部通过 `_build_shell_invocation(command, platform)` 纯函数派发：

| 平台 | `sys.platform` | 调用目标 | 命令参数 |
|------|----------------|----------|----------|
| Windows | 以 `"win"` 开头 | `powershell.exe -NoProfile -NonInteractive -Command` | 前缀注入 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;` + 原命令 |
| Linux | `"linux"` | `$SHELL` 或 `/bin/sh -c` | 原命令 |
| macOS | `"darwin"` | `$SHELL` 或 `/bin/sh -c` | 原命令 |

---

## 二、命令兼容矩阵

| 场景 | Windows (PowerShell) | Unix (sh/bash) | 说明 |
|------|----------------------|----------------|------|
| **成功命令** | `echo hello` | `echo hello` | 两端均可使用 `echo` |
| **失败命令（退出码）** | `exit 42` | `exit 42` | 两端均可使用 `exit N` |
| **标准输出** | `Write-Output "text"` | `echo "text"` | 语法不同 |
| **标准错误** | `Write-Error "msg"` 或 `[Console]::Error.WriteLine("msg")` | `echo "msg" >&2` | 语法完全不同 |
| **环境变量读取** | `$env:VAR_NAME` | `$VAR_NAME` | 语法完全不同 |
| **当前工作目录** | `Get-Location` 或 `pwd` | `pwd` | PowerShell 的 `pwd` 是 `Get-Location` 的别名 |
| **文件列表** | `Get-ChildItem` / `dir` / `ls` | `ls` | `ls` 在 PowerShell 中是 `Get-ChildItem` 的别名，输出格式不同 |
| **UTF-8 中文输出** | `Write-Output '中文'`（编码前缀自动注入） | `echo '中文'` | 两端均支持；Windows 依赖 `[Console]::OutputEncoding` 前缀 |
| **超时命令** | `Start-Sleep -Seconds 30` | `sleep 30` | 语法完全不同 |
| **多条命令** | `cmd1; cmd2; cmd3` | `cmd1 && cmd2 && cmd3` | `;` 两端通用；`&&` 在 PowerShell 中也支持 |
| **条件执行** | `if ($LASTEXITCODE -eq 0) { ... }` | `if [ $? -eq 0 ]; then ... fi` | 语法完全不同 |
| **管道** | `cmd1 \| cmd2` | `cmd1 \| cmd2` | 两端语法相同，但对象模型不同 |
| **变量赋值** | `$var = "value"` | `var="value"` | 语法完全不同 |

---

## 三、Windows PowerShell 编码说明

Shell 工具在 Windows 上自动注入 PowerShell 编码前缀：

```
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;
```

此前缀确保 `Write-Output`、`echo` 等 cmdlet 的输出以 UTF-8 编码写入管道，
保证中文、日文等 Unicode 字符能被正确捕获和显示。

**注意**：
- `[Console]::OutputEncoding` 只影响 PowerShell 自身的输出编码。
- 外部命令（如 `python.exe`、`git` 等）的输出编码由其自身的 stdout 编码决定，
  需单独设置（如 Python 的 `-X utf8` 参数或 `PYTHONUTF8=1` 环境变量）。

---

## 四、Unix 手动验证命令

若 CI 或有 Unix 执行环境，可用以下命令验证 Unix 分支行为。此处列出命令和预期输出：

| 测试场景 | 验证命令 | 预期输出特征 |
|----------|----------|-------------|
| 纯函数：Unix 分支 | `python -c "from minicode.tools.shell import _build_shell_invocation; print(_build_shell_invocation('echo hi', 'linux'))"` | `['/bin/sh', '-c', 'echo hi']` |
| 成功命令 | 通过 shell 工具执行 `echo hello` | `退出码：0` + `hello` |
| 失败命令 | 通过 shell 工具执行 `exit 42` | `退出码：42` + success=False |
| 中文输出 | 通过 shell 工具执行 `echo '你好'` | `你好` 出现在 stdout 中 |
| 环境变量 | 通过 shell 工具执行 `echo $HOME` | 路径出现在 stdout 中 |
| 超时终止 | 通过 shell 工具执行 `sleep 30` 超时 1s | `执行超时（已终止进程）` |

Unix 环境下运行完整测试套件：

```bash
uv run pytest tests/test_tools/test_shell.py -v
```

`TestShellWindowsNative` 类带有 `@pytest.mark.skipif` 装饰器，
在非 Windows 平台会被 pytest 自动跳过，其余跨平台测试（纯函数、参数校验、执行行为等）仍会正常执行。

---

## 五、测试覆盖说明

### 纯函数测试（跨平台，不依赖 shell 环境）

| 测试类 | 测试数 | 覆盖内容 |
|--------|--------|----------|
| `TestBuildShellInvocation` | 7 | 四平台分支、SHELL 环境变量、命令不变性 |
| `TestNormalizeTimeout` | 12 | 默认/None/bool/float/string/边界夹逼/有效值 |
| `TestTruncateOutput` | 5 | 短文本/精确边界/超长截断/空字符串 |

### 参数校验测试（跨平台，不依赖 shell 环境）

| 测试类 | 测试数 | 覆盖内容 |
|--------|--------|----------|
| `TestShellParameterValidation` | 9 | command 缺失/None/空/空白/非字符串；timeout 无效；workspace_root 未设置 |

### 行为测试（跨平台，通过当前 shell 执行）

| 测试类 | 测试数 | 覆盖内容 |
|--------|--------|----------|
| `TestShellExecution` | 4 | echo、退出码 0/42、命令不存在 |
| `TestShellTimeout` | 2 | 超时终止进程、正常 timeout 内完成 |
| `TestShellTruncation` | 2 | stdout 截断长度、短输出不截断 |
| `TestShellChinese` | 2 | Python stdout/stderr 中文输出 |
| `TestShellCwdEnv` | 2 | cwd 固定为 workspace_root、env 继承 os.environ |
| `TestShellOutputFormat` | 2 | 成功/失败均含退出码、stdout、stderr |

### Windows 专用测试

| 测试类 | 测试数 | 覆盖内容 |
|--------|--------|----------|
| `TestShellWindowsNative` | 9 | `Get-Location`、`$env:PATH`、中文 `Write-Output`、`Get-ChildItem`、`exit`、`Write-Error`、`Start-Sleep` 超时、分号分隔多命令、`Get-Date` |

### 集成测试（跨平台）

| 测试类 | 测试数 | 覆盖内容 |
|--------|--------|----------|
| `TestShellIntegration` | 4 | ToolRegistry 注入、schema 兼容性、注册器导出、default_registry 包含 shell |

---

## 六、命令生成建议

当 AI 模型使用 Shell 工具时，应遵循以下规则：

1. **检测平台**：通过 `sys.platform` 判断当前运行环境。
2. **选择语法**：
   - Windows → PowerShell 语法（`$env:VAR`、`Write-Output`、`Get-ChildItem`）
   - Unix → sh/bash 语法（`$VAR`、`echo`、`ls`）
3. **避免跨平台命令**：不要在 Windows 上生成 `ls -la`，也不要在 Unix 上生成 `Get-ChildItem`。
4. **中文处理**：Windows 上 PowerShell 原生 cmdlet 的中文输出由编码前缀保证；
   调用外部命令（Python、Node.js 等）时需额外设置 UTF-8 模式。
5. **超时设置**：合理设置 timeout 值，长时间运行的任务（编译、测试）应设置为 300-600 秒。
