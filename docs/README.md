# TP-Voyager 文档

README 只负责快速理解和上手；实现细节按主题放在这里。

## 使用者

- [运行与配置](OPERATIONS.md) — 启动、Runtime Home、环境变量、诊断。
- [模型路由目录](MODEL_ROUTING.md) — 白名单、能力资料、倍率、routable 三态。
- [Captain Skill](../skills/tp-voyager-captain/README.md) — 上层 AI 如何查询、选择、派遣、验收。

## 架构 / 维护

- [架构说明](ARCHITECTURE.md) — Captain / Voyager / Crew 边界与核心数据流。
- [测试策略](TESTING.md) — Smoke、Current、Regression、Live Gate。
- [CodeBuddy Backend](BACKEND_CODEBUDDY.md) — CodeBuddy 受控路线与模型目录来源。
- [Qoder Backend](BACKEND_QODER.md) — Qoder ACP/SDK 路线与动态模型目录。
- [项目 Charter](architecture/CHARTER.md) — 产品最高边界。
- [目录基线](architecture/DIRECTORY_BASELINE.md) — 代码职责与目录约束。
- [AI 开发规则](../AGENTS.md) — 修改仓库前必须遵守的约束。

## 历史

- [CHANGELOG](../CHANGELOG.md) — 对外版本变化。
- `records/` — 历史验收证据，仅用于追溯，不是当前使用说明。

## 文档原则

```text
README            → 第一次来的人
MODEL_ROUTING      → Captain / operator 怎么选模型
OPERATIONS         → 怎么跑
ARCHITECTURE       → 为什么这样设计
BACKEND_*          → 供应商接入细节
TESTING            → 怎么验证
CHANGELOG/records  → 过去发生过什么
```

不要把发布历史、Live Gate 明细或内部开发日志重新堆回根 README。

- [`MODEL_EVALUATION_STANDARD.md`](MODEL_EVALUATION_STANDARD.md) — standardized model evidence, Scorecard, Tier authority, migration, and manual update rules.
