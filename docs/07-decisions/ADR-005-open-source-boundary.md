# ADR-005：开源许可与来源代码边界

| 属性 | 值 |
|---|---|
| 状态 | accepted |
| 日期 | 2026-07-21 |

## 背景

项目将公开 GitHub 和在线 Demo，并参考 Open PicsetAI 与 Fashion-AI。

## 决策

- CommerceVision Agent 原创部分计划采用 Apache-2.0。
- 复用 PicsetAI MIT 代码时保留原许可证和版权。
- 使用 `THIRD_PARTY_NOTICES.md` 记录来源。
- Fashion-AI 没有明确许可证，不复制代码、Prompt 和素材。
- 测试和 Demo 只使用有权公开的数据。

## 后果

- 每个迁移文件需要来源审查。
- 不能把本地下载的所有文件直接放入新仓库。
- 公开数据集需要独立 License/Manifest。

## 验证

- License 扫描通过。
- 第三方声明完整。
- Git 历史中不存在真实凭证或无权素材。

