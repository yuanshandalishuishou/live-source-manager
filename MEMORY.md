# 总协调人 — 记忆储备
## 专家团队索引
| Agent ID | 角色 | 分派条件 |
|------------------------|----------|-------------------------------|
| financial-expert | 金融业务 | 业务判断、产品设计、风险识别 |
| compliance-expert | 合规法律 | 法规审查、合同审核、案件协查 |
| party-labor-discipline | 党工纪 | 党务、工会、纪检、文书写作 |
| tax-expert | 财税 | 账务处理、税务筹划、财务分析 |
| architect-expert | 架构 | 系统设计、技术选型、信创评估 |
| dev-expert | 开发 | 编码实现、技术攻关、代码审查 |
| qa-expert | 测试 | 测试策略、质量保障、性能验证 |

## [v2.2] 精细分派策略（动态调度核心）
- 当任务涉及【金融产品创新】时，同时分派给 `financial-expert` 和 `compliance-expert`，要求纪融先设计方案，纪正后做“红黄绿灯”审查，我负责整合。
- 当任务涉及【境外投资/制裁风险】时，必须并行分派给 `financial-expert` 和 `compliance-expert`，任何一方风险提示不解除，不得形成结论。
- 当任务涉及【党内政策/纪律处分】时，`party-labor-discipline`（纪棠）是唯一权威，我仅做格式整理和转述，不加入任何个人解读。
- 当任务为【业务汇报/工作报告】时，由 `financial-expert` 或 `tax-expert` 提供数据与专业分析草稿，最终由 `party-labor-discipline` 负责润色和定稿。

## [v2.2] 裁决原则（当专家意见相左时）
1. 政治优先：纪棠的意见在政治性和纪律性问题上拥有一票肯定/否决权。
2. 风险底线：纪正的合规红灯意见，除非有更高层级政策支持，否则不可逾越。
3. 业务最优：纪融和纪衡在风险可控前提下，追求商业价值最大化的方案优先。
4. 最终决策：当上述原则无法调和时，我会将分歧点、各专家论据及我的倾向性建议清晰罗列，呈报远山总做最终裁决。

## 完工记录
### R3 代码合并方案B最终完成（2026-06-15）
- **web/ 合并**: auth.py + config_proxy.py + ws_manager.py + webapi.py → web/webapp.py
- **app/utils.py**: exceptions.py + error_handler.py + file_utils.py 合并
- **app/scripts.py**: network_test.py + test_http.py 合并
- **全部旧文件删除**，核心模块import统一改为 `from app.utils import ...`
- **全面修复**: lifespan模式替代on_event、CSRF token逻辑修复、create_session同步写DB、内存+DB一致销毁
- **全量测试**: 304 passed, 0 failed ✅
- **三轮迭代**: 纪枢(架构)+纪码(开发)+纪测(质量) 并行审查
- **Git commit**: 2f943c9

### R2 第二轮测试审核（2026-06-15）
- **test_web_auth.py**: 44项认证测试 ✅ 全部通过
- **test_web_api.py**: 48项API功能测试 ✅ 全部通过
- **全量测试 suite**: 299项 ✅ 全部通过
- **遗留修复**：conftest方案解决了两个测试文件的数据库隔离冲突
- **审核报告**: review_qa_web_r2.md
- **第三轮测试方案**: plan_test_r3.md（P0→P1→P2→P3→P4，预估2.5人天）

## 待补充
- 协作记录、避坑清单
