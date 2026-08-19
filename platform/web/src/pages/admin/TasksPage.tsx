/** Admin 工作区：采集任务列表、新建任务、分派给 Agent、查看任务下的采集记录。
 *
 * 创建与分派是两步：POST /tasks 建草稿，POST /tasks/{id}/assign 才推给 Agent。
 * 拆开的好处是草稿可以先攒着；分派失败任务仍留在 draft，重试即可。
 *
 * 任务是父，Episode 是子。行可展开看该任务下的采集记录 —— 这是父子关系的主视角，
 * 采集记录页则是跨任务的历史视图。
 *
 * Agent 在线状态在这里只用来判断「能不能选它」，节点监控本身在运维监控页。
 */

import { useCallback, useEffect, useState } from "react";
import { Button, Form, Input, InputNumber, Modal, Select, message } from "antd";
import type { AgentNode, CollectTask, TaskStatus } from "@contract";
import {
  assignTask,
  createTask,
  fetchAgents,
  fetchTasks,
} from "../../api/client";
import { useConsoleStream } from "../../hooks/useConsoleStream";
import "../shared.css";

const STATUS_LABELS: Record<TaskStatus, string> = {
  draft: "草稿",
  published: "已发布",
  assigned: "已分派",
  in_progress: "采集中",
  completed: "已完成",
  cancelled: "已取消",
};

const STATUS_COLORS: Record<TaskStatus, string> = {
  draft: "#64748b",
  published: "#4fd1c5",
  assigned: "#38bdf8",
  in_progress: "#22d3ee",
  completed: "#10b981",
  cancelled: "#64748b",
};

interface TaskFormValues {
  name: string;
  description?: string;
  robot_model: string;
  scene: string;
  required_topics: string;
  min_duration_ms: number;
  max_duration_ms: number;
  target_episode_count: number;
  /** 建完立刻分派给谁；留空则只存草稿 */
  agent_id?: string;
}

/**
 * 表单默认值。topics 必须是 Agent 录制器实际产出的名字，否则文件一落地就被
 * 预检拒收 —— 对齐 agent/src/agent/recorder/mcap_writer.py 的 SIMULATED_TOPICS。
 */
const FORM_DEFAULTS: Partial<TaskFormValues> = {
  robot_model: "rm-75-6f",
  scene: "kitchen",
  required_topics: "/camera/front/image_raw, /joint_states, /gripper/state",
  min_duration_ms: 3000,
  max_duration_ms: 30000,
  target_episode_count: 5,
};

/** 分派历史的最后一条即当前负责方。 */
function currentAgent(task: CollectTask): string | null {
  const last = task.assignments?.[task.assignments.length - 1];
  return last?.agent_id ?? null;
}
interface TasksPageProps {
  /** 点任务名或「查看子任务」时跳转 */
  onOpenTask: (taskId: string) => void;
}

export function TasksPage({ onOpenTask }: TasksPageProps) {
  const [tasks, setTasks] = useState<CollectTask[]>([]);
  const [agents, setAgents] = useState<AgentNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  /** 为已有任务补分派时，记住是哪一条 */
  const [assignTarget, setAssignTarget] = useState<CollectTask | null>(null);
  const [assignAgent, setAssignAgent] = useState<string | null>(null);
  const [form] = Form.useForm<TaskFormValues>();
  const { agentOnline } = useConsoleStream();

  /** REST 拉到的 online 为底，WS 增量覆盖。 */
  const isOnline = useCallback(
    (row: AgentNode): boolean => agentOnline[row.agent_id] ?? row.online,
    [agentOnline],
  );

  const load = useCallback(async () => {
    try {
      const [{ items }, nodes] = await Promise.all([
        fetchTasks(),
        fetchAgents(),
      ]);
      setTasks(items);
      setAgents(nodes);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => clearInterval(timer);
  }, [load]);

  // 离线节点选不了：assign 会成功但 WS 推不到，任务卡在已分派没人干
  const agentOptions = agents.filter(isOnline).map((a) => ({
    value: a.agent_id,
    label: `${a.agent_id} · ${a.hostname}`,
  }));

  const openCreate = () => {
    form.setFieldsValue(FORM_DEFAULTS as TaskFormValues);
    setCreating(true);
  };

  const closeCreate = () => {
    setCreating(false);
    form.resetFields();
  };

  const handleCreate = async () => {
    let values: TaskFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // 校验失败，antd 已在字段上标红
    }

    setSubmitting(true);
    try {
      const task = await createTask({
        name: values.name,
        description: values.description ?? null,
        requirement: {
          robot_model: values.robot_model,
          scene: values.scene,
          required_topics: values.required_topics
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
          min_duration_ms: values.min_duration_ms,
          max_duration_ms: values.max_duration_ms,
          target_episode_count: values.target_episode_count,
        },
      });

      if (values.agent_id) {
        // 建好但分派失败：任务留在 draft，列表里可以再点「分派」重试
        await assignTask(task.task_id, values.agent_id);
        message.success(`任务已创建并下发到 ${values.agent_id}`);
      } else {
        message.success("任务已创建（草稿，未分派）");
      }
      closeCreate();
      void load();
    } catch (e) {
      message.error(e instanceof Error ? e.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleAssign = async () => {
    if (assignTarget === null || assignAgent === null) return;
    setSubmitting(true);
    try {
      await assignTask(assignTarget.task_id, assignAgent);
      message.success(`已下发到 ${assignAgent}`);
      setAssignTarget(null);
      setAssignAgent(null);
      void load();
    } catch (e) {
      message.error(e instanceof Error ? e.message : "分派失败");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <div className="loading-state">加载中...</div>;

  const activeCount = tasks.filter((t) => t.status === "in_progress").length;
  const completedCount = tasks.filter((t) => t.status === "completed").length;
  // 已采集与已发布分开统计：前者上传完回调时 +1，后者要等标注审核通过。
  // 只看已发布的话，刚上传的数据在界面上毫无反应。
  const collectedTotal = tasks.reduce(
    (sum, t) => sum + (t.collected_count ?? 0),
    0,
  );
  const publishedTotal = tasks.reduce(
    (sum, t) => sum + (t.published_count ?? 0),
    0,
  );

  return (
    <main className="workspace-main">
      <header className="workspace-header">
        <h1>采集任务</h1>
        <Button type="primary" onClick={openCreate}>
          新建任务
        </Button>
      </header>

      {error && (
        <div className="error-state" role="alert">
          {error}
        </div>
      )}

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">进行中</div>
          <div className="stat-value">{activeCount}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">已完成</div>
          <div className="stat-value">{completedCount}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">已采集</div>
          <div className="stat-value">{collectedTotal} ep</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">已发布</div>
          <div className="stat-value">{publishedTotal} ep</div>
        </div>
      </div>

      <div className="table-container">
        <table className="data-table">
          <thead>
            <tr>
              <th>任务名称</th>
              <th>状态</th>
              <th>机型</th>
              <th>场景</th>
              <th>采集进度</th>
              <th>已发布</th>
              <th>负责 Agent</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => {
              const target = task.requirement.target_episode_count;
              const collected = task.collected_count ?? 0;
              const published = task.published_count ?? 0;
              // 进度按已采集算 —— 上传完就该看到动静，而不是等标注审核通过
              const percent = target
                ? Math.min(100, Math.round((collected / target) * 100))
                : 0;
              const agent = currentAgent(task);
              return (
                <tr key={task.task_id} className="clickable-row">
                  <td className="task-name">
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => onOpenTask(task.task_id)}
                    >
                      {task.name}
                    </button>
                  </td>
                  <td>
                    <span
                      className="status-chip"
                      style={{
                        backgroundColor:
                          STATUS_COLORS[task.status] || "#64748b",
                      }}
                    >
                      {STATUS_LABELS[task.status] || task.status}
                    </span>
                  </td>
                  <td className="mono-cell">{task.requirement.robot_model}</td>
                  <td>{task.requirement.scene}</td>
                  <td>
                    <div
                      className="progress-bar-container"
                      title={`已采集 ${collected} / 目标 ${target}`}
                    >
                      <div
                        className="progress-bar"
                        style={{ width: `${percent}%` }}
                      />
                      <span className="progress-text">
                        {collected} / {target}
                      </span>
                    </div>
                  </td>
                  <td
                    className="mono-cell"
                    title="要走完解析→核验→标注→审核才计入"
                  >
                    {published}
                  </td>
                  <td className="mono-cell">
                    {agent ? (
                      <span className="agent-id">{agent}</span>
                    ) : (
                      <span className="empty-value">未分派</span>
                    )}
                  </td>
                  <td className="actions-cell">
                    <Button
                      size="small"
                      onClick={() => onOpenTask(task.task_id)}
                    >
                      查看子任务
                    </Button>
                    <Button
                      size="small"
                      disabled={agentOptions.length === 0}
                      title={
                        agentOptions.length === 0
                          ? "没有在线 Agent 可分派"
                          : undefined
                      }
                      onClick={() => {
                        setAssignTarget(task);
                        setAssignAgent(null);
                      }}
                    >
                      {agent ? "改派" : "分派"}
                    </Button>
                  </td>
                </tr>
              );
            })}
            {tasks.length === 0 && (
              <tr className="empty-row">
                <td colSpan={8}>暂无任务</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Modal
        title="新建采集任务"
        open={creating}
        onOk={() => void handleCreate()}
        onCancel={closeCreate}
        confirmLoading={submitting}
        okText="创建"
        cancelText="取消"
        width={560}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="任务名称"
            rules={[{ required: true, message: "请输入任务名称" }]}
          >
            <Input placeholder="厨房抓取-放置" />
          </Form.Item>

          <Form.Item name="description" label="任务说明">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>

          <Form.Item
            name="agent_id"
            label="下发给"
            extra={
              agentOptions.length === 0
                ? "当前没有在线 Agent，创建后任务留作草稿，可在列表里补分派"
                : "留空则只创建草稿，之后可在列表里分派"
            }
          >
            <Select
              allowClear
              placeholder="选择目标 Agent"
              options={agentOptions}
              notFoundContent="无在线 Agent"
            />
          </Form.Item>

          <Form.Item
            name="robot_model"
            label="机器人型号"
            rules={[{ required: true }]}
          >
            <Input placeholder="rm-75-6f" />
          </Form.Item>

          <Form.Item name="scene" label="场景" rules={[{ required: true }]}>
            <Input placeholder="kitchen" />
          </Form.Item>

          <Form.Item
            name="required_topics"
            label="必需 Topics"
            extra="逗号分隔。缺任一 topic 的文件会被 Agent 预检拒收，须与采集软件实际录制的 topic 名一致"
            rules={[{ required: true, message: "至少填一个 topic" }]}
          >
            <Input placeholder="/camera/front, /arm/joint_states" />
          </Form.Item>

          <Form.Item
            name="min_duration_ms"
            label="最短时长（毫秒）"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} step={1000} style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item
            name="max_duration_ms"
            label="最长时长（毫秒）"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} step={1000} style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item
            name="target_episode_count"
            label="目标条数"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`分派任务 · ${assignTarget?.name ?? ""}`}
        open={assignTarget !== null}
        onOk={() => void handleAssign()}
        onCancel={() => {
          setAssignTarget(null);
          setAssignAgent(null);
        }}
        confirmLoading={submitting}
        okText="下发"
        cancelText="取消"
        okButtonProps={{ disabled: assignAgent === null }}
      >
        <Select
          style={{ width: "100%" }}
          placeholder="选择目标 Agent"
          options={agentOptions}
          value={assignAgent}
          onChange={setAssignAgent}
          notFoundContent="无在线 Agent"
        />
      </Modal>
    </main>
  );
}
