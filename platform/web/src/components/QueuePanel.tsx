/** 队列巡检面板：手工测试时看积压与死信。
 *
 * 两个后端共用这一份 UI —— `RDH_QUEUE_BACKEND` 决定后端，面板只是把巡检结果画出来。
 * 队列名与绑定的 routing_key 都来自契约，前端不硬编码。
 *
 * 几个状态要能一眼分清，因为它们的处置完全不同：
 * - 积压高 → worker 没起或跟不上
 * - 死信非零 → 有消息处理失败，要去看日志
 * - 队列未声明 → Scheduler 从没启动过（不是错误）
 * - broker 不可达 → 没起 broker
 */

import { useCallback, useEffect, useState } from "react";
import { Button, Table, Tag, Tooltip, message } from "antd";
import { type QueueDepth, type QueueSnapshot, fetchQueues } from "../api/sysops";

/** 积压到多少算「该看一眼了」。POC 阈值，不是生产 SLO。 */
const BACKLOG_WARN = 10;

export function QueuePanel() {
  const [snapshot, setSnapshot] = useState<QueueSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const load = useCallback(async (manual = false) => {
    setLoading(true);
    try {
      setSnapshot(await fetchQueues());
      setFailed(null);
      if (manual) message.success("队列状态已刷新");
    } catch (error) {
      // 接口本身挂了（401/500）——与「broker 不可达」是两件事，后者在 payload 里
      setFailed(error instanceof Error ? error.message : "巡检失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);

  const columns = [
    {
      title: "队列",
      dataIndex: "queue",
      key: "queue",
      render: (name: string) => <code className="queue-name">{name}</code>,
    },
    {
      title: "待消费",
      key: "pending",
      render: (_: unknown, row: QueueDepth) => (
        <span
          className={
            row.pending >= BACKLOG_WARN ? "queue-depth warn" : "queue-depth"
          }
        >
          {row.pending}
        </span>
      ),
    },
    {
      title: "订阅的 routing_key",
      key: "routing_keys",
      render: (_: unknown, row: QueueDepth) =>
        row.routing_keys.length === 0 ? (
          <Tooltip title="algo 队列由 ingest 内部调度，不直接订阅事件">
            <span className="queue-muted">（内部调度）</span>
          </Tooltip>
        ) : (
          <span className="queue-keys">
            {row.routing_keys.map((key) => (
              <Tag key={key} className="routing-key">
                {key}
              </Tag>
            ))}
          </span>
        ),
    },
    {
      title: "已声明",
      key: "reachable",
      render: (_: unknown, row: QueueDepth) =>
        row.reachable ? (
          <Tag color="cyan">是</Tag>
        ) : (
          <Tooltip title="Scheduler 还没启动过 —— 队列由消费方声明">
            <Tag color="default">未声明</Tag>
          </Tooltip>
        ),
    },
  ];

  const backlog = snapshot?.queues.reduce((sum, q) => sum + q.pending, 0) ?? 0;

  return (
    <section className="queue-panel">
      <header className="queue-header">
        <h2>
          队列巡检
          {snapshot && (
            <Tag
              color={snapshot.backend === "rabbit" ? "blue" : "default"}
              className="live-tag"
              title={
                snapshot.backend === "rabbit"
                  ? `broker ${snapshot.broker}`
                  : "文件队列后端（零外部依赖）"
              }
            >
              {snapshot.backend === "rabbit" ? "RabbitMQ" : "文件队列"}
            </Tag>
          )}
        </h2>
        <Button size="small" loading={loading} onClick={() => void load(true)}>
          刷新
        </Button>
      </header>

      {failed && (
        <div className="error-state" role="alert">
          {failed}
        </div>
      )}

      {snapshot?.error && (
        <div className="warn-state" role="alert">
          {snapshot.error}
          <span className="warn-hint">先执行 make broker-up</span>
        </div>
      )}

      <div className="queue-stats">
        <div className="stat-card">
          <span className="stat-value">{backlog}</span>
          <span className="stat-label">总积压</span>
        </div>
        <div
          className={
            (snapshot?.dlq_count ?? 0) > 0
              ? "stat-card danger"
              : "stat-card"
          }
        >
          <span className="stat-value">{snapshot?.dlq_count ?? 0}</span>
          <span className="stat-label">死信</span>
        </div>
      </div>

      <Table
        dataSource={snapshot?.queues ?? []}
        columns={columns}
        loading={loading && snapshot === null}
        rowKey="queue"
        pagination={false}
        size="small"
        locale={{ emptyText: "无队列信息" }}
        className="queues-table"
      />

      {snapshot && (
        <footer className="queue-footer">
          <span>
            exchange <code>{snapshot.exchange}</code>
          </span>
          <span>
            死信 exchange <code>{snapshot.dlx}</code>
          </span>
          {snapshot.broker && (
            <span>
              broker <code>{snapshot.broker}</code>
            </span>
          )}
        </footer>
      )}
    </section>
  );
}
