import { getJson, patchJson, postJson } from "@/lib/http";
import type { AuthUser } from "@/stores/auth-store";

/**
 * 认证相关 REST 服务（后端 /auth 端点）。
 *
 * register / login 同构返回 { token, expires_at, user }（注册即登录）；
 * me / updateProfile 读写当前用户资料；changePassword 验原密码后改密
 * （成功不换发 token，当前会话保持有效）。401 业务语义（凭证错误 /
 * 原密码错误）走信封 error_code=5002，经 BizError 抛给调用方展示。
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

  /** 更新资料（昵称；留空则展示回退为用户名），返回更新后的用户 */
  async updateProfile(nickname: string): Promise<AuthUser> {
    return patchJson<AuthUser>("/auth/me", { nickname });
  },

  /** 修改密码（验原密码；原密码错误 5002，新密码不合规 5004） */
  async changePassword(oldPassword: string, newPassword: string): Promise<AuthUser> {
    return postJson<AuthUser>("/auth/change-password", {
      old_password: oldPassword,
      new_password: newPassword,
    });
  },
};
