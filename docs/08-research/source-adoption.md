# 来源项目吸收策略

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用基线 | `open-picsetai@440ebcff`、`Fashion-AI@02cdf312` |

## Open PicsetAI

### 可以吸收

- Next.js 工作区交互和候选图展示方式。
- 商品图、服装、精修和参考图工作流经验。
- Prompt 与 JSON 结构化输出思路。
- OpenAI-compatible、Gemini、ApiMart 等适配经验。

### 不能原样继承

- 进程内 Job Store。
- Next.js 请求进程中的长任务。
- 本地 uploads 和 SQLite。
- 占位认证、积分和项目接口。
- 任意 URL 图片代理。
- 缺少统一超时、重试、熔断和评测的 Provider 调用。

### 许可

仓库提供 MIT License。复用代码时必须保留原始版权和许可证声明，并在 `THIRD_PARTY_NOTICES.md` 中列明来源文件。

## Fashion-AI

### 可以吸收的思想

- Dense 图片向量与文本信号混合检索。
- 业务属性过滤。
- RRF 或加权融合。
- 历史优秀素材辅助风格规划。
- 商品图和参考图共同参与生成。

### 必须重写

- CLI 编排。
- CSV 主数据。
- TF-IDF 请求时重新训练。
- 本地 output。
- Milvus Collection 全量删除重建。
- OpenRouter 调用和硬编码配置。

### 许可

远端仓库未提供明确 LICENSE。CommerceVision Agent 不复制其代码、Prompt 或素材，仅依据公开思想独立实现。

## 最终策略

```text
PicsetAI UI/交互经验
         \
          -> 独立实现 CommerceVision Agent
         /
Fashion-AI 检索与参考图思想
```

新项目必须拥有：

- 独立的数据模型。
- 独立的 Agent Graph。
- 独立的 Prompt 和评测集。
- 独立的 Provider Adapter。
- 独立的测试和部署体系。

## 素材边界

只使用：

- 自有拍摄素材。
- 明确授权素材。
- 公开许可覆盖展示、Embedding 和派生生成的素材。
- 合成或专门为测试创建的商品素材。

每个资产记录来源、权利人、用途、有效期和允许发送的供应商。

