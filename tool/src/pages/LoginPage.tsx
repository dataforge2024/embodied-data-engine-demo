/**
 * 登录页。
 *
 * 复用 Platform 的 `POST /auth/login`，不自建认证机制 —— Tool 与 Platform 是两个
 * 独立前端，但共用同一套用户体系与 JWT（design.md 第 4 节选甲）。
 *
 * 登录页自己的 401 是「密码错」，不是「会话过期」，所以 client 不会把它当登出处理。
 */

import { useState } from "react";
import type { User } from "@contract";
import { login } from "../api/client";

interface Props {
  readonly onLoggedIn: (user: User) => void;
}

export function LoginPage({ onLoggedIn }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError("请输入用户名与密码");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const token = await login(username.trim(), password);
      onLoggedIn(token.user);
    } catch (e) {
      // 不回显是「用户不存在」还是「密码错」—— 后端也不区分，避免账号枚举
      setError((e as Error).message);
    } finally {
      setPending(false);
      setPassword("");
    }
  };

  return (
    <main className="login-page">
      <h1>RobotDataHub 标注工作台</h1>
      <form onSubmit={submit}>
        <label htmlFor="username">用户名</label>
        <input
          id="username"
          value={username}
          autoComplete="username"
          onChange={(event) => setUsername(event.target.value)}
        />

        <label htmlFor="password">密码</label>
        <input
          id="password"
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(event) => setPassword(event.target.value)}
        />

        <button type="submit" disabled={pending}>
          {pending ? "登录中…" : "登录"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
