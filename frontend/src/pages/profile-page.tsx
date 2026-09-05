import { useEffect, useState, type FC, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { ArrowLeftIcon, Loader2Icon, UserRoundIcon } from "lucide-react";

import { PasswordInput } from "@/components/shared/password-input";
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
import { formatDateTime } from "@/lib/format";
import { BizError } from "@/lib/http";
import { authService } from "@/services/auth-service";
import { useAuthStore } from "@/stores/auth-store";

/**
 * 用户资料页（/profile）：基本信息（昵称编辑）+ 修改密码。
 *
 * 挂载时经 authService.me() 取最新资料（store 里的可能是旧持久化数据）。
 * 昵称留空保存时后端展示回退为用户名；改密成功不换发 token，保持当前
 * 会话继续有效。校验错误按字段展示，服务端已知错误码映射到对应字段
 * （原密码错误 5002 → 原密码、格式 5004 → 新密码），其余全局提示。
 */
type PasswordField = "oldPassword" | "newPassword" | "confirmPassword";

export const ProfilePage: FC = () => {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);

  // 昵称输入值：null 表示尚未从后端水合，避免 me() 返回前输入框闪旧值
  const [nickname, setNickname] = useState<string | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordErrors, setPasswordErrors] = useState<
    Partial<Record<PasswordField, string>>
  >({});
  const [passwordFormError, setPasswordFormError] = useState<string | null>(
    null,
  );

  // 首次进入取最新资料；401 由 http 拦截器统一登出并跳登录页
  useEffect(() => {
    let cancelled = false;
    authService
      .me()
      .then((fresh) => {
        if (!cancelled) {
          setUser(fresh);
          setNickname(fresh.nickname);
        }
      })
      .catch(() => {
        // 取不到就以 store 现有数据兜底展示
        if (!cancelled) setNickname(user?.nickname ?? "");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅挂载时水合一次
  }, []);

  const saveNickname = async (event: FormEvent) => {
    event.preventDefault();
    if (nickname === null) return;
    setProfileError(null);
    if (nickname.length > 32) {
      setProfileError("昵称长度不能超过 32 字符");
      return;
    }

    setSavingProfile(true);
    try {
      const fresh = await authService.updateProfile(nickname);
      setUser(fresh);
      setNickname(fresh.nickname);
      toast.success("资料已更新");
    } catch (err) {
      setProfileError(
        err instanceof Error ? err.message : "保存失败，请稍后重试",
      );
    } finally {
      setSavingProfile(false);
    }
  };

  const clearPasswordField = (field: PasswordField) =>
    setPasswordErrors((prev) => ({ ...prev, [field]: undefined }));

  const changePassword = async (event: FormEvent) => {
    event.preventDefault();
    setPasswordFormError(null);

    const errors: Partial<Record<PasswordField, string>> = {};
    if (!oldPassword) errors.oldPassword = "请输入原密码";
    if (newPassword.length < 8 || newPassword.length > 64) {
      errors.newPassword = "密码长度须在 8-64 字符之间";
    } else if (newPassword === oldPassword) {
      errors.newPassword = "新密码不能与原密码相同";
    }
    if (confirmPassword !== newPassword) {
      errors.confirmPassword = "两次输入的密码不一致";
    }
    setPasswordErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setChangingPassword(true);
    try {
      await authService.changePassword(oldPassword, newPassword);
      toast.success("密码已修改");
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      if (err instanceof BizError) {
        if (err.errorCode === 5002) {
          setPasswordErrors({ oldPassword: err.message });
        } else if (err.errorCode === 5004) {
          setPasswordErrors({ newPassword: err.message });
        } else {
          setPasswordFormError(err.message);
        }
      } else {
        setPasswordFormError(
          err instanceof Error ? err.message : "请求失败，请稍后重试",
        );
      }
    } finally {
      setChangingPassword(false);
    }
  };

  return (
    <div className="min-h-dvh bg-background">
      <header className="flex h-12 items-center gap-1 border-b border-border/60 px-3">
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-foreground"
          onClick={() => void navigate("/")}
        >
          <ArrowLeftIcon />
          返回
        </Button>
        <span className="font-heading text-base font-semibold">个人资料</span>
      </header>

      <main className="mx-auto w-full max-w-xl space-y-6 p-4">
        {/* 基本信息：头像占位 + 只读账号信息 + 昵称编辑 */}
        <Card>
          <CardHeader>
            <CardTitle>基本信息</CardTitle>
            <CardDescription>账号信息与展示昵称</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-4">
              <span className="flex size-14 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                <UserRoundIcon className="size-7" />
              </span>
              <div className="min-w-0 space-y-0.5 text-sm">
                <p className="font-medium">{user?.username ?? "-"}</p>
                <p className="text-muted-foreground">
                  注册于 {formatDateTime(user?.created_at)}
                </p>
              </div>
            </div>

            <form className="space-y-2" onSubmit={(e) => void saveNickname(e)}>
              <Label htmlFor="nickname">昵称</Label>
              <div className="flex gap-2">
                <Input
                  id="nickname"
                  placeholder="留空则显示用户名"
                  maxLength={64}
                  value={nickname ?? ""}
                  onChange={(e) => setNickname(e.target.value)}
                  disabled={savingProfile || nickname === null}
                />
                <Button
                  type="submit"
                  variant="outline"
                  disabled={
                    savingProfile || nickname === null || nickname === user?.nickname
                  }
                >
                  {savingProfile && <Loader2Icon className="size-4 animate-spin" />}
                  保存
                </Button>
              </div>
              {profileError && (
                <p className="text-sm text-destructive" role="alert">
                  {profileError}
                </p>
              )}
            </form>
          </CardContent>
        </Card>

        {/* 修改密码：三个密码框 + 字段级错误 */}
        <Card>
          <CardHeader>
            <CardTitle>修改密码</CardTitle>
            <CardDescription>
              验证原密码后设置新密码（8-64 字符），修改后当前登录不受影响
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={(e) => void changePassword(e)}>
              <div className="space-y-2">
                <Label htmlFor="old-password">原密码</Label>
                <PasswordInput
                  id="old-password"
                  autoComplete="current-password"
                  value={oldPassword}
                  onChange={(e) => {
                    setOldPassword(e.target.value);
                    clearPasswordField("oldPassword");
                  }}
                  aria-invalid={passwordErrors.oldPassword ? true : undefined}
                  required
                />
                {passwordErrors.oldPassword && (
                  <p className="text-sm text-destructive" role="alert">
                    {passwordErrors.oldPassword}
                  </p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-password">新密码</Label>
                <PasswordInput
                  id="new-password"
                  autoComplete="new-password"
                  placeholder="至少 8 位"
                  value={newPassword}
                  onChange={(e) => {
                    setNewPassword(e.target.value);
                    clearPasswordField("newPassword");
                  }}
                  aria-invalid={passwordErrors.newPassword ? true : undefined}
                  required
                />
                {passwordErrors.newPassword && (
                  <p className="text-sm text-destructive" role="alert">
                    {passwordErrors.newPassword}
                  </p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirm-password">确认新密码</Label>
                <PasswordInput
                  id="confirm-password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(e) => {
                    setConfirmPassword(e.target.value);
                    clearPasswordField("confirmPassword");
                  }}
                  aria-invalid={
                    passwordErrors.confirmPassword ? true : undefined
                  }
                  required
                />
                {passwordErrors.confirmPassword && (
                  <p className="text-sm text-destructive" role="alert">
                    {passwordErrors.confirmPassword}
                  </p>
                )}
              </div>

              {passwordFormError && (
                <p className="text-sm text-destructive" role="alert">
                  {passwordFormError}
                </p>
              )}

              <Button type="submit" disabled={changingPassword}>
                {changingPassword && (
                  <Loader2Icon className="size-4 animate-spin" />
                )}
                修改密码
              </Button>
            </form>
          </CardContent>
        </Card>
      </main>
    </div>
  );
};
