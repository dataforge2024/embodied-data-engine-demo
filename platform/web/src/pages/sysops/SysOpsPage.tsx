/** SysOps 工作区：Agent 节点状态查看。
 *
 * 纯查看页 —— 任务的创建与分派在「任务管理」里做，这里不碰任务生命周期。
 * 唯一的动作是「触发回传」：Agent 平时靠目录监听自动上传，这是监听漏掉或
 * 上传失败后的人工补救，属于运维范畴。
 */

import { useEffect, useState } from "react";
import { Button, Table, Tag, message } from "antd";
import type { AgentNode } from "@contract";
import { fetchAgents } from "../../api/client";
import { triggerUpload } from "../../api/sysops";
import { useConsoleStream } from "../../hooks/useConsoleStream";
import "./SysOpsPage.css";

export function SysOpsPage() {
  const [agents, setAgents] = useState<AgentNode[]>([]);
  const [loading, setLoading] = useState(false);
  const { agentOnline, connected } = useConsoleStream();

  /** REST 拉到的 online 为底，WS 推来的增量覆盖 —— 上下线立刻可见。 */
  const isOnline = (row: AgentNode): boolean =>
    agentOnline[row.agent_id] ?? row.online;

  const loadAgents = async () => {
    setLoading(true);
    try {
      setAgents(await fetchAgents());
    } catch (error) {
      message.error(error instanceof Error ? error.message : "加载 Agent 失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAgents();
    // WS 负责上下线的即时通知；轮询兜底心跳字段（pending_upload_count 等）与新注册节点
    const timer = setInterval(() => void loadAgents(), 5000);
    return () => clearInterval(timer);
  }, []);

  const handleTriggerUpload = async (agentId: string) => {
    try {
      await triggerUpload(agentId, null, "SysOps 手动触发");
      message.success(`已通知 ${agentId} 重扫并上传`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "触发失败");
    }
  };

  const onlineCount = agents.filter(isOnline).length;

  const columns = [
    {
      title: "Agent ID",
      dataIndex: "agent_id",
      key: "agent_id",
      render: (id: string) => <code className="agent-id">{id}</code>,
    },
    { title: "主机名", dataIndex: "hostname", key: "hostname" },
    { title: "版本", dataIndex: "version", key: "version" },
    {
      title: "状态",
      key: "online",
      render: (_: unknown, row: AgentNode) => {
        const online = isOnline(row);
        return (
          <Tag color={online ? "cyan" : "default"} className="status-tag">
            {online ? "在线" : "离线"}
          </Tag>
        );
      },
    },
    {
      title: "待上传",
      key: "pending",
      render: (_: unknown, row: AgentNode) =>
        row.last_heartbeat?.pending_upload_count ?? "—",
    },
    {
      title: "已分派",
      key: "assigned",
      render: (_: unknown, row: AgentNode) =>
        row.assigned_task_ids?.length ?? 0,
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, row: AgentNode) => (
        <Button
          size="small"
          disabled={!isOnline(row)}
          onClick={() => handleTriggerUpload(row.agent_id)}
        >
          触发回传
        </Button>
      ),
    },
  ];

  return (
    <div className="sysops-page">
      <header className="page-header">
        <h1>
          Agent 运维监控
          <Tag
            color={connected ? "green" : "default"}
            className="live-tag"
            title={connected ? "上下线即时推送" : "WS 断开，5s 轮询兜底"}
          >
            {connected ? "实时" : "轮询"}
          </Tag>
        </h1>
        <div className="stats">
          <div className="stat-card">
            <span className="stat-value">{agents.length}</span>
            <span className="stat-label">注册节点</span>
          </div>
          <div className="stat-card online">
            <span className="stat-value">{onlineCount}</span>
            <span className="stat-label">在线节点</span>
          </div>
        </div>
      </header>

      <Table
        dataSource={agents}
        columns={columns}
        loading={loading}
        rowKey="agent_id"
        pagination={false}
        locale={{ emptyText: "暂无 Agent 注册" }}
        className="agents-table"
      />
    </div>
  );
}
