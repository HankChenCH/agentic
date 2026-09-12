// vitest 全局 setup：jsdom 用例的 DOM 断言匹配器（toBeInTheDocument 等）。
// 对 node 环境的纯逻辑用例无副作用（不触 DOM，仅注册 matcher 扩展）。
import "@testing-library/jest-dom/vitest";
