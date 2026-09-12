import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { AxiosError, AxiosHeaders } from "axios";

import { BizError, getBinary, getJson, http, patchJson, postForm, postJson } from "@/lib/http";
import { useAuthStore } from "@/stores/auth-store";

// 刷新通道 mock：401 重试链只验证 http 侧的编排，刷新本身有独立测试。
// startProactiveRefresh 必须提供——http.ts 模块顶层会调用它。
vi.mock("@/lib/token-refresh", () => ({
  refreshSession: vi.fn(),
  startProactiveRefresh: vi.fn(),
}));

import { refreshSession } from "@/lib/token-refresh";
const refreshMock = vi.mocked(refreshSession);

const user = { id: "u-1", username: "hank", nickname: "Hank" };
const resetStore = () =>
  useAuthStore.setState({
    token: null,
    refreshToken: null,
    expiresAt: null,
    refreshExpiresAt: null,
    user: null,
  });

/** 成功信封 */
const envelope = <T,>(response: T) => ({ error_code: 0, error_message: "", response });

/**
 * mock adapter：按注册的剧本逐次应答。response 必须携带 adapter 收到的
 * 真实 config（拦截器要读 config.responseType / config.url / config.__retried）。
 * - ok(body)：2xx + 信封（或任意数据，供二进制用例传 ArrayBuffer）
 * - fail(status, data)：复刻内建 adapter 的 settle —— axios 1.x 的
 *   validateStatus 检查在内建 adapter 里，自定义 adapter 非 2xx 必须自行
 *   以带 response 的 AxiosError reject，才会进响应拦截器的 rejection 分支
 */
const adapterMock = vi.fn<(config: InternalAxiosRequestConfig) => Promise<AxiosResponse>>();
const scriptOk = (body: unknown, status = 200) =>
  adapterMock.mockImplementationOnce((config) =>
    Promise.resolve({
      data: body,
      status,
      statusText: "OK",
      headers: new AxiosHeaders(),
      config,
    }),
  );
const scriptFail = (status: number, data: unknown) =>
  adapterMock.mockImplementationOnce((config) => {
    const response: AxiosResponse = {
      data,
      status,
      statusText: "Error",
      headers: new AxiosHeaders(),
      config,
    };
    return Promise.reject(
      new AxiosError(
        `Request failed with status code ${status}`,
        AxiosError.ERR_BAD_REQUEST,
        config,
        undefined,
        response,
      ),
    );
  });

const lastConfig = (call = 0) =>
  adapterMock.mock.calls[call][0] as InternalAxiosRequestConfig;
const authHeaderOf = (config: InternalAxiosRequestConfig) =>
  new AxiosHeaders(config.headers).get("Authorization");

beforeEach(() => {
    resetStore();
    refreshMock.mockReset();
    adapterMock.mockReset();
    http.defaults.adapter = adapterMock as unknown as AxiosAdapter;
});

// ---------------------------------------------------------------------------
// 请求拦截器（Bearer 注入）
// ---------------------------------------------------------------------------

describe("请求拦截器（Bearer 注入）", () => {
  it("登录态：每次请求现读 store 注入 Authorization", async () => {
    useAuthStore.setState({ token: "t-1", refreshToken: "r", expiresAt: null, refreshExpiresAt: null, user });
    scriptOk(envelope({}));
    await getJson("/ping");
    expect(authHeaderOf(lastConfig())).toBe("Bearer t-1");
  });

  it("未登录：不注入 Authorization 头", async () => {
    scriptOk(envelope({}));
    await getJson("/ping");
    expect(authHeaderOf(lastConfig())).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 响应拦截器成功分支 + helper 拆信封
// ---------------------------------------------------------------------------

describe("信封拆包（error_code = 0）", () => {
  it.each([
    ["getJson", getJson],
    ["postJson", postJson],
    ["patchJson", patchJson],
  ] as const)("%s 返回剥壳后的 response 字段", async (_name, helper) => {
    scriptOk(envelope({ value: 42 }));
    await expect(helper("/thing")).resolves.toEqual({ value: 42 });
  });

  it("postJson 把 body 走 JSON 序列化", async () => {
    scriptOk(envelope({}));
    await postJson("/thing", { a: 1 });
    expect(lastConfig().data).toBe(JSON.stringify({ a: 1 }));
  });

  it("postForm：FormData 直传且删除 Content-Type（交浏览器生成 boundary）", async () => {
    scriptOk(envelope({ ok: true }));
    const form = new FormData();
    form.append("file", new Blob(["x"]), "a.png");
    await postForm("/upload", form);
    const config = lastConfig();
    expect(config.data).toBe(form);
    expect(new AxiosHeaders(config.headers).get("Content-Type")).toBeFalsy();
  });
});

describe("业务错误（BizError）", () => {
  it("2xx 信封 error_code !== 0 → 抛 BizError（保留 errorCode）", async () => {
    scriptOk({ error_code: 409, error_message: "重名" });
    const err = await getJson("/thing").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(BizError);
    expect((err as BizError).errorCode).toBe(409);
    expect((err as BizError).message).toBe("重名");
  });

  it("HTTP 4xx + 信封体 → 同样拆成 BizError（调用方无需区分走 2xx 还是 4xx）", async () => {
    scriptFail(400, { error_code: 4003, error_message: "状态冲突" });
    const err = await getJson("/thing").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(BizError);
    expect((err as BizError).errorCode).toBe(4003);
  });

  it("error_message 缺失回落通用文案", async () => {
    scriptOk({ error_code: 1, error_message: "" });
    const err = await getJson("/thing").catch((e: unknown) => e);
    expect((err as BizError).message).toBe("请求失败");
  });

  it("HTTP 4xx + 非 JSON 信封体 → 原 AxiosError 透传（不伪造成业务错误）", async () => {
    scriptFail(500, "Internal Server Error");
    const err = await getJson("/thing").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AxiosError);
    expect(err).not.toBeInstanceOf(BizError);
  });
});

// ---------------------------------------------------------------------------
// 二进制通道（无信封）
// ---------------------------------------------------------------------------

describe("getBinary（二进制响应跳过信封检查）", () => {
  it("成功：字节原样返回，不做 error_code 检查", async () => {
    const bytes = new TextEncoder().encode("PDF-ish");
    scriptOk(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    const result = await getBinary("/files/1");
    expect(new TextDecoder().decode(result)).toBe("PDF-ish");
  });
});

// ---------------------------------------------------------------------------
// 401 统一处理：单飞刷新 → 重放一次；失败登出；/auth/* 例外
// ---------------------------------------------------------------------------

describe("401 处理链", () => {
  it("非 /auth/* 401：刷新成功 → 重放原请求（带新票）且成功返回", async () => {
    scriptFail(401, { error_code: 4001, error_message: "token expired" });
    scriptOk(envelope({ fine: true }));
    refreshMock.mockImplementation(async () => {
      // 模拟刷新通道成功后的会话覆写
      useAuthStore.setState({ token: "t-new", refreshToken: "r-new", expiresAt: null, refreshExpiresAt: null, user });
      return true;
    });
    useAuthStore.setState({ token: "t-stale", refreshToken: "r", expiresAt: null, refreshExpiresAt: null, user });

    await expect(getJson("/orders")).resolves.toEqual({ fine: true });

    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(adapterMock).toHaveBeenCalledTimes(2);
    expect(authHeaderOf(lastConfig(1))).toBe("Bearer t-new"); // 重放读到了新票
  });

  it("重放仍 401：__retried 防二次循环，不再刷新直接 reject", async () => {
    scriptFail(401, {});
    scriptFail(401, {});
    refreshMock.mockResolvedValue(true);

    const err = await getJson("/orders").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AxiosError);
    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(adapterMock).toHaveBeenCalledTimes(2);
  });

  it("刷新失败：登出清会话并 reject 原始错误", async () => {
    scriptFail(401, {});
    refreshMock.mockResolvedValue(false);
    useAuthStore.setState({ token: "t", refreshToken: "r", expiresAt: null, refreshExpiresAt: null, user });

    const err = await getJson("/orders").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AxiosError);
    expect(useAuthStore.getState().token).toBeNull(); // logout 已执行
    expect(adapterMock).toHaveBeenCalledTimes(1); // 不重放
  });

  it("/auth/* 自身 401（凭据错误）：拆成 BizError 交表单展示，不刷新不登出", async () => {
    scriptFail(401, { error_code: 4001, error_message: "用户名或密码错误" });
    useAuthStore.setState({ token: "t-keep", refreshToken: "r-keep", expiresAt: null, refreshExpiresAt: null, user });

    const err = await getJson("/auth/login").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(BizError);
    expect((err as BizError).errorCode).toBe(4001);
    expect(refreshMock).not.toHaveBeenCalled();
    expect(useAuthStore.getState().token).toBe("t-keep");
  });
});
