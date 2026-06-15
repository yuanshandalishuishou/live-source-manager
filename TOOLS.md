# TOOLS.md — 工具配置（总协调人专用）

## Agent间协作工具
- `sessions_spawn(agent_id, task)` — 向指定专家下发子任务
- `sessions_yield()` — 等待所有已派发子任务返回结果

## 其他工具
- `write_file` — 更新各Agent的MEMORY.md等知识文件（若平台支持）
- 标准文件读写、搜索等基础工具
