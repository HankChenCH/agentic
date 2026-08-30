import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";
import { BotIcon, Loader2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { authService } from "@/services/auth-service";
import { useAuthStore } from "@/stores/auth-store";

/**
 * 登录/注册页（双 Tab 切换）。
 *
 * 注册成功后端直接签发 token（注册即登录），与登录同构处理：写入
 * auth-store 后按 next 参数回跳来源页（缺省进聊天页）。业务错误
 * （重名 5001 / 凭证错误 5002 / 格式 5004）经 BizError 在表单上方提示。
 */
type Mode = "login" | "register";

export const LoginPage = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const setSession = useAuthStore((s) => s.setSession);

  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);

    if (mode === "register") {
      if (password !== confirmPassword) {
        setFormError("两次输入的密码不一致");
        return;
      }
      if (password.length < 8 || password.length > 64) {
        setFormError("密码长度须在 8-64 字符之间");
        return;
      }
    }
    if (username.length < 3 || username.length > 32) {
      setFormError("用户名长度须在 3-32 字符之间");
      return;
    }

    setSubmitting(true);
    try {
      const session =
        mode === "login"
          ? await authService.login(username, password)
          : await authService.register(username, password);
      setSession(session.token, session.user);
      toast.success(mode === "login" ? "登录成功" : "注册成功");
      // next 由 401 跳转 / RequireAuth 带上，回跳来源页
      const next = searchParams.get("next");
      navigate(next && next.startsWith("/") ? next : "/", { replace: true });
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "请求失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="paper-glow-bg flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <div className="mb-1 flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <BotIcon className="size-6" />
          </div>
          <CardTitle>Agentic 智能助手</CardTitle>
          <CardDescription>
            {mode === "login" ? "登录以继续对话" : "创建账号，注册后直接进入对话"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* Tab 切换（ui 暂无 tabs 原语，双按钮实现） */}
          <div className="mb-4 grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => {
                  setMode(m);
                  setFormError(null);
                }}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  mode === m
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {m === "login" ? "登录" : "注册"}
              </button>
            ))}
          </div>

          <form className="space-y-4" onSubmit={(e) => void submit(e)}>
            <div className="space-y-2">
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                autoComplete="username"
                placeholder="3-32 个字符"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder={mode === "register" ? "至少 8 位" : "输入密码"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {mode === "register" && (
              <div className="space-y-2">
                <Label htmlFor="confirm-password">确认密码</Label>
                <Input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                />
              </div>
            )}

            {formError && (
              <p className="text-sm text-destructive" role="alert">
                {formError}
              </p>
            )}

            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting && <Loader2Icon className="size-4 animate-spin" />}
              {mode === "login" ? "登录" : "注册并进入"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
};
