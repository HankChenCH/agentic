import fs from 'node:fs'
import path from 'node:path'

import { esmExternalRequirePlugin } from 'rolldown/plugins'
import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/

/**
 * 基础依赖 CDN 外置（仅生产构建生效，dev 不受影响）。
 *
 * react / react-dom / react-router 体积大（合计约 900KB 源码量，min+gzip
 * 约 65KB）且版本稳定，改由 ESM CDN 以 importmap 方式提供：主包不再打包
 * 它们，浏览器按 importmap 裸说明符直取 CDN ESM 模块。
 *
 * **换源（CDN_BASE）**：构建期配置，改完重新构建即生效——
 *   .env.local / .env.production 写 CDN_BASE=<base>，或环境变量注入
 *   （Docker build 经 ARG CDN_BASE 烘焙进 .env.production，见 Dockerfile；
 *   compose 侧变量 FRONTEND_CDN_BASE）。
 *   CDN_BASE=https://esm.sh            # 缺省，esm.sh 官方
 *   CDN_BASE=https://esm.corp.example  # 自建 esm.sh（开源实现，协议兼容）
 *
 * 兼容性要求：CDN_BASE 必须是 **esm.sh 协议兼容**的服务——① 对 npm 包做
 * CJS→ESM 转换：react 19 的 npm 包是纯 CJS（`module.exports =
 * require(...)`），npmmirror 等「原文件镜像」的 files API 返回的就是原始
 * CJS，浏览器按 module 加载必炸（已实测），因此它们只能当**包下载源**、
 * 不能当浏览器侧 CDN；② 支持 `?external=react`（见下）；③ 支持子路径
 * 转换（/jsx-runtime、/client）。自建 esm.sh（github.com/esm-dev/esm.sh）
 * 三者天然满足，且可把 upstream registry 配置成 npmmirror（阿里云镜像）——
 * 「阿里云包源 + 自建 CDN」即此组合，换源只动 CDN_BASE。
 *
 * 为什么不用现成插件：vite-plugin-cdn-import / vite-plugin-cdn2 的代码改写
 * 都依赖 UMD globals（import {x} from 'react' → 全局 React.x），而 React 19
 * 起官方不再发布 UMD 构建，react-router v8 也是 ESM-only——实测 CDN2 构建
 * 结果主包纹丝不动，故自实现（transformIndexHtml 注入 importmap）。
 *
 * rolldown 特有坑：产物里的 CJS 模块（如 zustand 的依赖
 * use-sync-external-store，纯 CJS 包）会保留 require("react")，浏览器 ESM
 * 环境没有 require 函数，运行期直接抛错。解法是 rolldown 内置的
 * esmExternalRequirePlugin——把 external 的 require 调用转成 ESM import
 * （importmap 接得住）。注意：externals 必须且只能由该插件的 external
 * 声明，顶层 external 会优先跳过转换（官方文档明确要求，二者绝不同时列出）。
 *
 * 约束：
 * - 版本号从 node_modules 读取，与 package.json 单一事实来源，不会漂移；
 * - external 与 importmap 的 imports 必须一一对应——漏掉 mapping 会导致
 *   运行期裸说明符解析失败（白屏）；
 * - react-dom / react-router（含 jsx-runtime 子路径）的 CDN 构建会把 peer
 *   依赖 react 解析到 ^19 范围内的最新版（≠ 本地 pin 版本），产生两份
 *   React 实例 → "Cannot read properties of null (reading 'useContext')"。
 *   除 react 根入口外所有 URL 必须带 `?external=react`，让 CDN 产物保持
 *   bare import "react"，统一回落到本 importmap 的唯一 react 实例；
 * - jsx-runtime / jsx-dev-runtime / client 等子路径单独映射（importmap 不
 *   做查询参数式前缀匹配）。
 */

/** CDN base 缺省值；可被 .env.production/.env.local 或环境变量 CDN_BASE 覆盖 */
const CDN_BASE_DEFAULT = 'https://esm.sh';

export default defineConfig(({ mode }) => {
  // 前缀空串 = 读取 .env* 全部键（CDN_BASE 无 VITE_ 前缀，非客户端变量：
  // 只在构建期决定 importmap 指向，不进浏览器代码）
  const env = loadEnv(mode, __dirname, '');
  const cdnBase = (env.CDN_BASE || CDN_BASE_DEFAULT).replace(/\/+$/, '');

  function depVersion(name: string): string {
    const pkg = JSON.parse(
      fs.readFileSync(path.join(__dirname, 'node_modules', name, 'package.json'), 'utf8'),
    ) as { version: string };
    return pkg.version;
  }

  function buildCdnImports(): Record<string, string> {
    const reactV = depVersion('react');
    const domV = depVersion('react-dom');
    const routerV = depVersion('react-router');
    // react 根入口即唯一实例源；其余一切（react 子路径与其他包）的内部 react
    // 依赖一律外置回 importmap（?external=react），否则 CDN 会各自解析 peer
    // 最新版造成双实例
    const esm = (name: string, version: string, subpath = '') => {
      const external = name === 'react' && subpath === '' ? '' : '?external=react';
      return `${cdnBase}/${name}@${version}${subpath}${external}`;
    };
    return {
      'react': esm('react', reactV),
      'react/jsx-runtime': esm('react', reactV, '/jsx-runtime'),
      'react/jsx-dev-runtime': esm('react', reactV, '/jsx-dev-runtime'),
      'react-dom': esm('react-dom', domV),
      'react-dom/client': esm('react-dom', domV, '/client'),
      'react-router': esm('react-router', routerV),
      'react-router/dom': esm('react-router', routerV, '/dom'),
    };
  }

  function cdnExternals(): Plugin {
    const cdnImports = buildCdnImports();
    return {
      name: 'cdn-externals',
      apply: 'build',
      config() {
        return {
          build: {
            rolldownOptions: {
              // externals 由该插件全权接管：require() → ESM import + 标记 external
              plugins: [esmExternalRequirePlugin({ external: Object.keys(cdnImports) })],
            },
          },
        };
      },
      transformIndexHtml() {
        return [
          {
            tag: 'script',
            attrs: { type: 'importmap' },
            children: JSON.stringify({ imports: cdnImports }),
            injectTo: 'head-prepend',
          },
        ];
      },
    };
  }

  return {
    plugins: [react(), tailwindcss(), cdnExternals()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    build: {
      rolldownOptions: {
        output: {
          // 主包内的大块静态 vendor 拆成独立可缓存 chunk：业务代码改动后
          // 无需重新下载这些基本不变的依赖（仅影响 chunk 划分，不改语义）。
          // 只圈定「随主包静态加载」的包；recharts/pdf/xyflow 等动态导入的
          // 依赖留在各自懒加载 chunk，不 here 匹配以免被提前拉主链路。
          advancedChunks: {
            groups: [
              {
                name: 'vendor-assistant',
                test: /node_modules\/(@assistant-ui|@ag-ui|@a2ui|assistant-stream|rxjs)\//,
              },
              {
                name: 'vendor-markdown',
                test: /node_modules\/(micromark|mdast|remark|unified|rehype|hast|vfile|zod|property-information|web-namespaces|character-entities|decode-named-character-reference|comma-separated-tokens|devlop|bail|trough|is-plain-obj|html-url-attributes|space-separated-tokens|trim-lines|ccount|longest-streak|escape-string-regexp|markdown-table)/,
              },
              {
                name: 'vendor-ui',
                test: /node_modules\/(@base-ui|radix-ui|lucide-react|sonner|zustand|clsx|tailwind-merge|class-variance-authority)/,
              },
            ],
          },
        },
      },
    },
  };
});
