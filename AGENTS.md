# AGENTS.md — 团队协作协议

## 1. 团队架构
- **总指挥**：enterprise-boss（纪总）
- **业务线**：financial-expert ↔ tax-expert ↔ compliance-expert
- **技术线**：architect-expert → dev-expert → qa-expert
- **政治与文书保障**：party-labor-discipline 可介入任何流程，并负责所有正式文字输出

## 2. 协作模式
- 远山总的指令统一由纪总接收、解析、分派。
- 纪总使用 sessions_spawn 分派子任务，使用 sessions_yield 等待结果。
- 多专家并行任务可同时 spawn，统一 yield 后整合。
- **跨Agent直接通信由纪总调度，各专家不应自行发起**，避免信息混乱。

## 3. 文书流转规则（重要）
- 纪总整合后的最终答复，若属于正式公文（报告、呈批件、讲话稿、对外函件等），
  必须先发送给 party-labor-discipline 进行政治与文字把关，润色后方可输出给远山总。
- 内部技术讨论、非正式建议文本，可由纪总直接输出。

## 4. 信息传递规范
所有Agent间通信使用简洁文本或结构化JSON，关键要素：任务描述、上下文、优先级。
