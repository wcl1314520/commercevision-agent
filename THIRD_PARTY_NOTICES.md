# Third-Party Notices

CommerceVision Agent 原创代码采用 Apache License 2.0。第三方依赖仍受各自许可证约束，本文件不改变其许可条款。

## 参考项目

### Open PicsetAI

- Repository: [ddlmanus/open-picsetai](https://github.com/ddlmanus/open-picsetai)
- Reviewed revision: `440ebcff70cc65c42fea0defb8139ce8317ce967`
- License: MIT
- Current use: Phase 0 未复制其源码；仅参考公开的产品流程与交互经验。

后续若复制或修改 MIT 文件，必须在本节列出来源路径、上游路径、revision，并保留上游版权与 MIT License。

### Fashion-AI

- Repository: [liangdabiao/Fashion-AI](https://github.com/liangdabiao/Fashion-AI)
- Reviewed revision: `02cdf3122dde240e09283e36eff9abf3de378f24`
- License status at review time: 未发现明确许可证
- Current use: 不复制源码、Prompt 或素材；仅独立实现公开思想。

## 运行时组件

本地 Compose 引用了 MySQL、Valkey、RabbitMQ、MinIO、etcd、Milvus 和 OpenTelemetry Collector 等独立运行时组件。它们不因本项目的 Apache-2.0 License 而重新授权。

Valkey 作为 Redis 协议兼容的本地缓存实现，采用 BSD-3-Clause License。生产部署仍可按架构文档接入企业批准的 Redis 兼容托管服务。

Python、Node.js 和容器依赖的精确版本由 `uv.lock`、`pnpm-lock.yaml`、Dockerfile 与生成的 SPDX SBOM 记录。发布前必须审查 SBOM 和所有许可证例外。
