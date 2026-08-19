/** Platform 前端外壳。工作区按登录角色收敛。 */

import { useEffect, useMemo, useState } from "react";
import { ConfigProvider, theme } from "antd";
import type { Role } from "@contract";
import { TaskDetailPage } from "./pages/admin/TaskDetailPage";
import { TasksPage } from "./pages/admin/TasksPage";
import { EpisodesPage } from "./pages/recorder/EpisodesPage";
import { SysOpsPage } from "./pages/sysops/SysOpsPage";
import { LoginPage } from "./pages/LoginPage";
import { contractVersion, setAccessToken } from "./api/client";
import { useCurrentUser } from "./hooks/useCurrentUser";
import "./App.css";

type Workspace = "admin" | "recorder" | "sysops";

/**
 * 工作区 → 可进入的角色。与后端 require_roles 对齐：
 * 任务的增删改派是 admin 独占；采集记录与运维监控 admin/recorder 都能看。
 * admin 通配由 useCurrentUser().can 处理。
 */
const WORKSPACES: ReadonlyArray<{
  key: Workspace;
  label: string;
  roles: readonly Role[];
}> = [
  { key: "admin", label: "任务管理", roles: ["admin"] },
  { key: "recorder", label: "采集记录", roles: ["admin", "recorder"] },
  { key: "sysops", label: "运维监控", roles: ["admin", "recorder"] },
];

export function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [session, setSession] = useState(0);
  const { user, can } = useCurrentUser(session);

  const visible = useMemo(
    () => WORKSPACES.filter((w) => can(...w.roles)),
    [can],
  );

  // 落在无权页面上会一直 403，所以默认停在第一个可见工作区
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const active = workspace ?? visible[0]?.key ?? null;
  /** 任务管理的二级页面：非 null 时显示任务详情（子任务列表） */
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("rdh_access_token");
    if (token) {
      setAccessToken(token);
      setAuthenticated(true);
    }
  }, []);

  const handleLoginSuccess = () => {
    setSession((n) => n + 1); // 触发重读 rdh_user
    setWorkspace(null);
    setAuthenticated(true);
  };

  const handleLogout = () => {
    localStorage.removeItem("rdh_access_token");
    localStorage.removeItem("rdh_user");
    setAccessToken(null);
    setWorkspace(null);
    setSession((n) => n + 1);
    setAuthenticated(false);
  };

  if (!authenticated) {
    return (
      <ConfigProvider
        theme={{
          algorithm: theme.darkAlgorithm,
          token: {
            colorPrimary: "#38BDF8",
            colorBgBase: "#0A0D12",
            colorBgContainer: "#0F131C",
            colorBorder: "#161D2B",
            borderRadius: 8,
          },
        }}
      >
        <LoginPage onSuccess={handleLoginSuccess} />
      </ConfigProvider>
    );
  }

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: "#38BDF8",
          colorBgBase: "#0A0D12",
          colorBgContainer: "#0F131C",
          colorBorder: "#161D2B",
          borderRadius: 8,
        },
      }}
    >
      <div className="app">
        <nav className="app-nav">
          <div className="nav-items">
            {visible.map((item) => (
              <button
                key={item.key}
                type="button"
                className={active === item.key ? "active" : ""}
                onClick={() => {
                  setWorkspace(item.key);
                  setOpenTaskId(null); // 切工作区时退出二级页，否则回来还停在详情
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="nav-right">
            {user && (
              <span className="current-user">
                {user.display_name}
                <span className="user-roles">{user.roles.join(" / ")}</span>
              </span>
            )}
            <span className="contract-version">契约 {contractVersion()}</span>
            <button type="button" className="logout-btn" onClick={handleLogout}>
              退出
            </button>
          </div>
        </nav>
        <main className="app-main">
          {active === "admin" &&
            (openTaskId === null ? (
              <TasksPage onOpenTask={setOpenTaskId} />
            ) : (
              <TaskDetailPage
                taskId={openTaskId}
                onBack={() => setOpenTaskId(null)}
              />
            ))}
          {active === "recorder" && <EpisodesPage />}
          {active === "sysops" && <SysOpsPage />}
          {active === null && (
            <div className="error-state" role="alert">
              当前账号没有可访问的工作区，请联系管理员分配角色。
            </div>
          )}
        </main>
      </div>
    </ConfigProvider>
  );
}
