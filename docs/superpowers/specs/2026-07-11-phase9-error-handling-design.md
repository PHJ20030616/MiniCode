# Phase 9: 错误处理与稳定性 — 设计文档

> **版本：** 0.1 | **最后更新：** 2026-07-11
> **所属项目：** MiniCode（v1.0 系列）
> **对应任务：** `doc/minicode-task-plan.md` Phase 9

---

## 1. 目标与范围

### 1.1 目标

将 MiniCode 从"基本可用"提升到"在异常条件下仍能稳定运行"的工程水平。核心目标：

1. **恢复能力：** 网络抖动、限流、临时服务端错误自动恢复（重试+退避）
2. **可诊断性：** 所有错误记录充分上下文，便于事后定位
3. **用户体验：** 错误信息具体可操作，不展示内部细节
4. **容错性：** 工具执行异常不污染对话历史，全局异常不丢会话数据

### 1.2 不包含的范围

- ❌ 并行工具执行（v1.x）
- ❌ 精确 token 计数（v1.x）
- ❌ Provider 熔断/降级（v1.x）
- ❌ Anthropic Provider 错误处理（Phase 10 实现）

---

## 2. 架构设计

### 2.1 错误处理层级

```
用户可见层 (Renderer)
  └─ 友好的中文错误消息 + 具体操作建议
       ↑
Agent Loop 层 (loop.py)
  ├─ Provider 错误 → 回滚消息 + 渲染错误
  ├─ 工具错误 → ToolMessage(内容) + Renderer.show_error
  └─ 全局异常 → 记录 debug 日志 + 保存当前会话 + 优雅退出
       ↑
Provider 层 (openai_compatible.py)
  ├─ chat() / list_models()
  ├─ RetryWrapper: 指数退避重试（transient 错误）
  ├─ 不可恢复错误 → 抛出 ProviderError
  └─ 重试耗尽 → yield error chunk / raise
       ↑
网络层 (openai SDK / httpx)
  └─ APITimeoutError, APIConnectionError,
     AuthenticationError (401), RateLimitError (429),
     InternalServerError (5xx), APIStatusError
```

### 2.2 各层职责

| 层级 | 职责 | 错误类型 | 处理方式 |
|------|------|---------|---------|
| Provider 网络层 | SDK/HTTP 异常分类 | 超时/断网/401/429/5xx | 映射为内部 ProviderError |
| Provider 重试层 | transient 错误自动恢复 | 超时/断网/429/5xx | 最多 3 次指数退避 + jitter |
| Agent Loop 层 | 会话级错误恢复 | ProviderError/工具异常 | 消息回滚 + 渲染错误 |
| 用户界面层 | 友好展示 | 已包装的错误 | Renderer.show_error() |

---

## 3. 详细设计

### 3.1 重试工具函数 (`src/minicode/utils/retry.py`)

```python
# 新增文件
class RetryConfig(BaseModel):
    max_retries: int = 3
    base_delay: float = 1.0       # 初始延迟(秒)
    max_delay: float = 10.0       # 最大延迟(秒)
    jitter: float = 0.5           # 随机抖动(秒)

def is_retryable(error: Exception) -> bool:
    """判断是否应重试该错误。
    
    可重试：
    - APITimeoutError / APIConnectionError
    - RateLimitError (429)
    - InternalServerError (5xx)
    
    不重试：
    - AuthenticationError (401)
    - APIStatusError (400, 403, 404 等 client 错误)
    """
```

**指数退避计算：**
```
delay = min(base_delay * 2^attempt, max_delay) + random.uniform(-jitter, jitter)
```

**集成方式：** 在 `OpenAICompatibleProvider.chat()` 和 `list_models()` 中，对 `client.chat.completions.create()` 和 `client.models.list()` 调用包裹 retry 逻辑。

### 3.2 Provider 错误增强 (`src/minicode/providers/openai_compatible.py`)

**修改内容：**

1. **chat() 方法**：在 `try/except` 块之前插入重试逻辑
2. **_stream_chat()**：对 API 调用包裹 retry，重试耗尽后 yield error chunk
3. **_non_stream_chat()**：对 API 调用包裹 retry，重试耗尽后 raise ProviderError（带重试次数信息）
4. **list_models()**：对 API 调用包裹 retry

**错误消息增强：**

| 错误类型 | 当前消息 | 增强后消息 |
|---------|---------|-----------|
| 401 | API key 认证失败（401），请检查 api_key 配置。 | API key 认证失败（401）。请检查：1) api_key 配置是否正确 2) base_url 是否指向正确的 API 地址 |
| 429 | 请求过于频繁（429），请稍后重试。 | 请求频率过高（429）{Retry-After}。已在 {n} 次重试后放弃，请稍后重试或检查用量配额 |
| 5xx | 服务端错误（XXX），请稍后重试。 | 服务端暂时不可用（{code}）。已在 {n} 次重试后放弃，请稍后重试。若持续出现请联系服务提供商 |
| 超时 | 请求超时，请检查网络连接或增加超时时间。 | 请求超时（{timeout}s）。已在 {n} 次重试后放弃，请检查网络连接或增加 --timeout 参数 |
| 断网 | 网络连接失败，请检查网络连接或 API 地址是否正确。 | 无法连接到 {base_url}。请检查：1) 网络连接 2) API 地址是否正确 3) 是否需要代理 |

### 3.3 Agent Loop 错误处理增强 (`src/minicode/agent/loop.py`)

**修改内容：**

1. **run()** 方法增加 ProviderError 的针对性处理，区分"可恢复"和"不可恢复"
2. **_process_stream()** 中收到 error chunk 后的渲染完善
3. **_execute_tools()** 中工具错误的用户渲染完善

### 3.4 全局异常处理增强 (`src/minicode/main.py` + `src/minicode/cli/app.py`)

**新增内容：**

1. **Signal 处理**：在 `ChatApp.run()` 中注册 SIGTERM/SIGINT 处理器
2. **保存当前会话**：收到退出信号或全局异常时，自动保存当前会话到磁盘
3. **增强日志**：在所有 catch 块中包含当前 provider、model、workspace、round 等信息

**ChatApp 修改：**

```python
# 新增方法
async def _shutdown_gracefully(self, signum=None):
    """优雅关闭：保存当前会话 + 清理资源 + 退出。"""
    try:
        if self._agent_loop is not None and self._current_session is not None:
            self._current_session.messages = list(self._agent_loop.messages)
            self._get_session_manager().save(self._current_session)
    except Exception:
        pass  # fail-soft
    self.renderer.show_info("再见！")
```

---

## 4. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/minicode/utils/retry.py` | **新增** | 指数退避重试工具 |
| `src/minicode/utils/exceptions.py` | 修改 | 新增 RetryExhaustedError |
| `src/minicode/providers/openai_compatible.py` | 修改 | 集成 retry + 增强错误消息 |
| `src/minicode/agent/loop.py` | 修改 | ProviderError 针对性处理 + 工具错误渲染增强 |
| `src/minicode/cli/app.py` | 修改 | 信号处理 + 优雅关闭 |
| `src/minicode/main.py` | 修改 | 全局异常日志增强 |
| `tests/test_utils/test_retry.py` | **新增** | retry 工具单元测试 |
| `tests/test_providers/test_openai_compatible.py` | 修改 | 重试行为测试 + 增强错误消息测试 |
| `tests/test_agent/test_loop.py` | 修改 | ProviderError 恢复测试 |
| `tests/test_cli/test_app.py` | **新增** | 优雅关闭测试 |

---

## 5. 测试策略

### 5.1 单元测试

| 测试目标 | 测试内容 |
|---------|---------|
| retry 工具 | 重试次数、退避时间计算、jitter、可重试/不可重试判断 |
| Provider 重试 | mock 模拟超时→成功、连续失败→重试耗尽、429→重试后成功 |
| Provider 错误消息 | 各 HTTP 状态码映射到正确的建议文本 |
| Agent Loop 恢复 | ProviderError 发生后消息回滚正常、用户可见错误渲染 |

### 5.2 集成测试

| 测试场景 | 验证点 |
|---------|--------|
| 模拟网络断开 → Provider 重试耗尽 | 用户看到清晰错误，对话可继续 |
| 模拟 API key 无效 → 不重试直接报错 | 显示"检查 api_key 配置"建议 |
| 模拟 429 → 重试 → 成功 | 最终正常响应 |
| 工具执行失败 → 错误返回模型 | 模型第二轮基于错误回复 |
| Ctrl+C 中断 → 保存当前会话 | 会话文件包含中断时的消息 |

### 5.3 手动验收清单

- [ ] 断网后发起对话，观察重试行为和错误消息
- [ ] 配置错误 API key 后发起对话，观察是否立即报错（不重试）
- [ ] 创建文件后 Ctrl+C 中断，检查会话文件已保存
- [ ] 让模型调用不存在的工具，观察错误返回和处理
- [ ] shell 命令超时，观察超时错误处理

---

## 6. 错误消息格式规范

所有用户可见的错误消息遵循以下模板：

```
错误：<具体描述>
建议：<1-2 条可操作建议>
```

Agent Loop 中的错误渲染使用 `Renderer.show_error()` 方法，确保统一的红色前缀 `错误：`。

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 重试导致 API 调用延迟增加 | 用户体验下降 | 最多 3 次重试，每次重试前渲染状态提示 |
| 重试 jitter 导致测试不稳定 | CI 偶发失败 | 测试中通过依赖注入控制 RetryConfig |
| 保存会话时写入失败 | 丢会话数据 | fail-soft 策略，不阻断主流程 |
| 信号处理与 prompt_toolkit 冲突 | 退出异常 | 使用 patch_stdout 上下文，信号处理中仅安全操作 |

---

## 8. 迁移路径

Phase 9 不涉及数据迁移或配置文件变更。所有修改向后兼容，现有功能不受影响。
