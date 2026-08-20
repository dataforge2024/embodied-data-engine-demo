/** Tool 应用外壳。三个工作台对应三个人工环节。
 *
 * 支持从 Platform 深链进入：`?episode=<id>&stage=<verify|annotate|review>`。
 * 只在挂载时读一次 —— 深链只决定「打开时落在哪」，之后手动切工作台不受影响。
 */

import { useEffect, useState } from "react";
import type { User } from "@contract";
import { AnnotatePage } from "./pages/AnnotatePage";
import { LoginPage } from "./pages/LoginPage";
import { ReviewPage } from "./pages/ReviewPage";
import { VerifyPage } from "./pages/VerifyPage";
import {
  contractVersion,
  logout,
  restoreSession,
  setUnauthorizedHandler,
} from "./api/client";

type Workspace = "verify" | "annotate" | "review";

const WORKSPACES: readonly Workspace[] = ["verify", "annotate", "review"];

function readDeepLink(): { workspace: Workspace | null; episodeId: string } {
  const params = new URLSearchParams(window.location.search);
  const stage = params.get("stage");
  const workspace = WORKSPACES.find((w) => w === stage) ?? null;
  return { workspace, episodeId: params.get("episode") ?? "" };
}

export function App() {
  const [deepLink] = useState(readDeepLink);
  const [workspace, setWorkspace] = useState<Workspace>(
    deepLink.workspace ?? "verify",
  );
  const [episodeId, setEpisodeId] = useState(deepLink.episodeId);
  // 初始值从 localStorage 恢复：刷新页面不该被登出
  const [user, setUser] = useState<User | null>(restoreSession);

  // 凭据过期时清掉用户状态回登录页。JWT 有 TTL（默认 1 小时），Tool 是浏览器
  // 应用，回登录页即可 —— 不像 Agent 那样需要无人值守自动重登。
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  if (!user) {
    return <LoginPage onLoggedIn={setUser} />;
  }

  return (
    <div className="app">
      <nav>
        <button type="button" onClick={() => setWorkspace("verify")}>
          质检
        </button>
        <button type="button" onClick={() => setWorkspace("annotate")}>
          标注
        </button>
        <button type="button" onClick={() => setWorkspace("review")}>
          审核
        </button>
        <span className="current-user">{user.display_name}</span>
        <button type="button" onClick={logout}>
          登出
        </button>
        <span className="version">契约 {contractVersion()}</span>
      </nav>

      {workspace === "verify" && <VerifyPage verifiedBy={user.user_id} />}
      {workspace === "review" && <ReviewPage reviewedBy={user.user_id} />}
      {workspace === "annotate" && (
        <>
          <input
            value={episodeId}
            onChange={(event) => setEpisodeId(event.target.value)}
            placeholder="Episode ID"
          />
          {/* 标注不传提交人：`annotated_by` 由 Platform 从 JWT 取（review.py 路由里），
              客户端传一个 user_id 反而给了伪造空间。质检与审核不同 —— 契约的
              VerifyResult / ReviewResult 把操作人放在请求体里，得由前端填。 */}
          {episodeId && <AnnotatePage episodeId={episodeId} />}
        </>
      )}
    </div>
  );
}
