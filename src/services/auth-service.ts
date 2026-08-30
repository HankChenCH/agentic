import { getJson, postJson } from "@/lib/http";
import type { AuthUser } from "@/stores/auth-store";

/**
 * 认证相关 REST 服务（后端 /auth 端点）。
 *
 * register / login 同构返回 { token, expires_at, user }（注册即登录）；
 * me 供需要刷新用户信息的场景使用。401 业务语义（用户名或密码错误）
 * 走信封 error_code=5002，经 BizError 抛给调用方展示。
 */
export interface AuthSession {
  token: string;
  expires_at: string;
  user: AuthUser;
}

export const authService = {
  /** 注册（注册即登录：成功直接返回 token + 用户信息） */
  async register(username: string, password: string): Promise<AuthSession> {
    return postJson<AuthSession>("/auth/register", { username, password });
  },

  /** 登录：错用户名/错密码统一 5002，不区分是否存在该用户 */
  async login(username: string, password: string): Promise<AuthSession> {
    return postJson<AuthSession>("/auth/login", { username, password });
  },

  /** 当前登录用户（验 token 有效性并取最新资料） */
  async me(): Promise<AuthUser> {
    return getJson<AuthUser>("/auth/me");
  },
};
