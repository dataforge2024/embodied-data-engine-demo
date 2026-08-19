/**
 * 登录用户与角色判定。
 *
 * 用户对象在登录时落 localStorage，这里只读不写。角色判定是**界面收敛**，
 * 不是安全边界 —— 真正的拦截在 Platform 的 require_roles 上。
 */

import { useMemo } from "react";
import type { Role, User } from "@contract";

const USER_KEY = "rdh_user";

function readUser(): User | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null; // 存储被写坏就当未登录，让上层退回登录页
  }
}

export interface CurrentUser {
  user: User | null;
  roles: readonly Role[];
  /** admin 视作全通 —— 与后端 require_roles 的通配一致 */
  can: (...allowed: Role[]) => boolean;
}

/**
 * `revision` 变化时重读 localStorage。登录成功后 App 只是换了 state，
 * 组件实例没重建 —— 不带这个参数就会一直用登录前读到的 null。
 */
export function useCurrentUser(revision: unknown = null): CurrentUser {
  return useMemo(() => {
    const user = readUser();
    const roles = user?.roles ?? [];
    return {
      user,
      roles,
      can: (...allowed: Role[]) =>
        roles.includes("admin") || allowed.some((r) => roles.includes(r)),
    };
  }, [revision]);
}
