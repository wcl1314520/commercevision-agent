# Security Policy

## Supported Versions

安全修复只针对 `main` 和最新发布版本。Phase 0 尚未发布稳定版本。

## Reporting

不要通过公开 Issue 披露漏洞、真实凭证或可识别用户数据。

公开仓库启用 GitHub Private Vulnerability Reporting 后，请通过仓库的 Security 页面提交报告。报告至少包含：

- 受影响版本或 commit。
- 可复现步骤和最小验证材料。
- 影响范围。
- 已知缓解方式。
- 是否已经在其他渠道公开。

维护者确认前，不要测试第三方生产系统、在线 Demo 的其他用户数据或模型供应商账户。

## Secret Exposure

发现 Secret 后立即：

1. 停止继续传播。
2. 在供应商侧撤销或轮换。
3. 检查日志、镜像、Git 历史和 CI Artifact。
4. 使用独立凭证恢复服务。
5. 补充检测规则和事故复盘。

删除 Git 文件不能替代凭证轮换。
