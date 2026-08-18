"""压测场景（Locust）。

本阶段是骨架：需要真实 Platform 实例才有意义，因此不进 ``make check``。

跑法::

    uv run locust -f load/locustfile.py --host http://127.0.0.1:8000

压测的关注点不是「QPS 多高」，而是**哪个环节先成为瓶颈**：

- 核验/标注队列查询走 ``(status, created_at)`` 索引，Episode 到百万级时是否还够快
- 上传回调是写路径且要重算 checksum，大文件下 CPU 是否成为瓶颈
- WS 连接数上限：每个采集 PC 一条长连接，千台规模下单实例能否扛住
"""

from locust import HttpUser, between, task


class ToolUser(HttpUser):
    """模拟 Tool 前端的核验与标注操作（交互④）。"""

    wait_time = between(1, 5)
    token: str | None = None

    def on_start(self) -> None:
        """登录取 JWT。"""
        response = self.client.post(
            "/api/v1/auth/login",
            json={"username": "operator", "password": "demo-only-pass"},
            name="/auth/login",
        )
        if response.status_code == 200:
            self.token = response.json()["data"]["access_token"]

    @property
    def auth_headers(self) -> dict[str, str]:
        """认证头。"""
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(5)
    def browse_verification_queue(self) -> None:
        """核验队列查询 —— 最高频的读操作。"""
        self.client.get(
            "/api/v1/verification/queue?page=1&limit=20",
            headers=self.auth_headers,
            name="/verification/queue",
        )

    @task(3)
    def browse_annotation_queue(self) -> None:
        """标注队列查询。"""
        self.client.get(
            "/api/v1/annotation/queue?page=1&limit=20",
            headers=self.auth_headers,
            name="/annotation/queue",
        )

    @task(2)
    def list_episodes(self) -> None:
        """Episode 列表 —— 无状态过滤时最吃索引。"""
        self.client.get(
            "/api/v1/episodes?page=1&limit=20",
            headers=self.auth_headers,
            name="/episodes",
        )

    @task(1)
    def episode_stats(self) -> None:
        """状态聚合统计 —— 全表 group by，Episode 量大时最先变慢。"""
        self.client.get(
            "/api/v1/episodes/stats", headers=self.auth_headers, name="/episodes/stats"
        )


class AgentCallbackUser(HttpUser):
    """模拟 Agent 的上传回调（交互③）—— 写路径压测。"""

    wait_time = between(5, 15)

    @task
    def health(self) -> None:
        """探活。真实压测应构造 UploadCallback，但那需要预置 Episode。"""
        self.client.get("/api/v1/health", name="/health")
