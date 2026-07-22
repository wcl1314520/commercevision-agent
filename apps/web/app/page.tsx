const services = [
  { name: "Control API", role: "工作流控制面", status: "Ready" },
  { name: "Agent Worker", role: "Durable Agent 执行", status: "Baseline" },
  { name: "Generation Worker", role: "生图与评测执行", status: "Planned" },
  { name: "MCP Server", role: "受控工具协议", status: "Baseline" },
];

export default function Home() {
  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">CommerceVision Agent</p>
          <h1>工程控制面</h1>
        </div>
        <div className="version">
          <span className="status-dot" aria-hidden="true" />
          <span>Phase 0 · 0.1.0</span>
        </div>
      </header>

      <section className="summary" aria-labelledby="summary-title">
        <div>
          <p className="eyebrow">运行状态</p>
          <h2 id="summary-title">基础设施基线已就绪</h2>
          <p className="muted">
            当前工作台只展示工程状态。商品、工作流和人工审核能力将在对应阶段按契约接入。
          </p>
        </div>
        <div className="summary-value">
          <strong>READY</strong>
          <span>Control plane</span>
        </div>
      </section>

      <section className="section" aria-labelledby="services-title">
        <div className="section-heading">
          <h2 id="services-title">服务组件</h2>
          <span className="muted">4 个运行边界</span>
        </div>
        <div className="service-table" role="table" aria-label="服务组件状态">
          <div className="service-row service-row-header" role="row">
            <span>组件</span>
            <span>职责</span>
            <span>状态</span>
          </div>
          {services.map((service) => (
            <div className="service-row" role="row" key={service.name}>
              <strong>{service.name}</strong>
              <span className="muted">{service.role}</span>
              <span className={`badge badge-${service.status.toLowerCase()}`}>
                {service.status}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="section split" aria-labelledby="stack-title">
        <div>
          <div className="section-heading">
            <h2 id="stack-title">工程基线</h2>
          </div>
          <dl className="facts">
            <div>
              <dt>API</dt>
              <dd>FastAPI · OpenAPI 3.1</dd>
            </div>
            <div>
              <dt>Agent</dt>
              <dd>LangGraph · Python</dd>
            </div>
            <div>
              <dt>事实主库</dt>
              <dd>MySQL 8.4</dd>
            </div>
            <div>
              <dt>异步投递</dt>
              <dd>RabbitMQ · Celery</dd>
            </div>
          </dl>
        </div>
        <div className="next-step">
          <p className="eyebrow">下一阶段</p>
          <h2>Phase 1</h2>
          <p className="muted">Workflow 状态机、Outbox、Lease 和 MySQL Checkpoint。</p>
        </div>
      </section>
    </main>
  );
}
