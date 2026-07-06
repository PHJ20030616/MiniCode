# ReAct Agent Loop 设计文档

## 概述

将 MiniCode 从单轮对话升级为 ReAct（Reasoning + Acting）闭环：模型可以调用工具（读文件、搜索），工具执行结果自动回传给模型，模型据此继续推理回答，直到给出最终回复。

## 架构

```
ChatApp._handle_message()
  └─→ AgentLoop.run(user_input)
        ├─ 构建 messages = system + history + user
        └─ 循环（最多 max_rounds 轮）:
             1. provider.chat(messages, tools_schema, stream=True)
             2. 流式渲染文本 + 收集 tool_call_delta
             3. 有 tool_calls?
                ├─ yes → 串行执行工具 → append ToolMessage → 继续循环
                └─ no  → 返回最终文本 → 结束
```

## 组件

### agent/system_prompt.py
- `build_system_prompt(tool_registry) → str`
- 中文系统提示词，告知 Agent 身份、能力、工具列表
- 工具列表从 registry 动态生成

### agent/context.py
- `build_messages(messages, system_prompt, user_input) → list[Message]`
- 构建发往 Provider 的消息列表：[system, ...history, user]

### agent/loop.py
- `AgentLoop` 类
- 管理对话历史 `self.messages`
- `run(user_input)` — ReAct 主入口
- `_process_stream(stream)` — 处理 StreamChunk，分拣文本/tool_call/结束
- `_assemble_tool_calls(deltas)` — 将 PartialToolCall delta 组装为完整 ToolCall

### cli/renderer.py
- 新增逐步渲染 API，配合 ReAct 循环内的流式输出

### cli/app.py
- ChatApp 创建 AgentLoop 实例
- `_handle_message` 委派给 agent_loop.run()

## 数据流

```python
# 一次 tool_call 回合
messages = [SystemPrompt, user_msg, ...]
stream = provider.chat(messages, tools=[...], stream=True)

async for chunk in stream:
    if chunk.type == "text_delta" → buffer += text, 实时渲染
    if chunk.type == "tool_call_delta" → 收集 PartialToolCall
    if chunk.type == "done" → 停止

# 组装工具调用
tool_calls = assemble(partial_tool_calls)

# 执行工具
for tc in tool_calls:
    result = registry.execute_tool(tc.function.name, json.loads(tc.function.arguments))
    messages.append(ToolMessage(content=result.output, tool_call_id=tc.id))

# 下一轮：带着工具结果继续调模型
```

## 流式策略

- 始终使用 `stream=True`
- 文本 delta 实时渲染（Raw Text → 最终 Markdown）
- tool_call delta 只收集不渲染
- 每轮结束时渲染完整 Markdown

## 最大轮次

- 从 `config.agent.max_rounds` 读取（默认 20）
- 超过后输出提示 "已到达最大推理轮次，回复可能不完整"

## 测试策略

Mock provider 测试覆盖：
1. 无工具直接回答（纯文本对话）
2. 一次 read_file 后回答
3. 多工具串行调用
4. 工具错误返回给模型
5. 超过最大轮次截断

集成测试覆盖：
- Agent Loop + mock Provider + ToolRegistry + 临时 workspace
- "读取文件后总结" 完整链路
