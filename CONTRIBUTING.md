# 贡献指南

感谢你关注 TP-Voyager。

当前项目处于 **Real Voyage** 阶段，因此贡献原则不是“功能越多越好”，而是优先解决真实使用中的重复问题。

提交代码前请依次阅读：

1. `TP_VOYAGER_CHARTER.md`
2. `TP_VOYAGER_DIRECTORY_BASELINE.md`
3. `AGENTS.md`

## 建议流程

1. 先确认问题属于 TP-Voyager 职责；
2. 优先复用现有目录与 Durable Core；
3. 修改最小必要表面；
4. 默认只运行 Smoke + 受影响专项；
5. 在 PR 中说明：
   - 问题是什么；
   - 为什么当前能力无法解决；
   - 修改了哪些 Contract；
   - 跑了哪些测试；
   - 是否新增持久化、状态或公共工具。

## 默认不接受的方向

除非有真实使用证据并通过 Charter Gate，否则不要直接提交：

- 新的顶层架构层；
- 第二套 Task/Workflow/Result/Evidence 系统；
- 自动无限重试；
- 隐式 Backend/Model Fallback；
- Vector DB / 自动知识写回；
- Agent 社交网络；
- 大规模目录重构；
- 仅为了“更优雅”的包名/文件迁移。

## Bug Report

Bug 请尽量提供：

- TP-Voyager 版本；
- Python 版本；
- CodeBuddy/Qoder CLI 版本（如相关）；
- 最小复现步骤；
- 实际结果；
- 预期结果；
- 是否涉及 Runtime DB / Workspace / Verification。

请不要上传 Token、Credential、Cookie、真实业务敏感源码或完整 Runtime DB。
