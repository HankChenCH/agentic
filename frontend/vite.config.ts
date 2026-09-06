import fs from 'node:fs'
import path from 'node:path'

import { esmExternalRequirePlugin } from 'rolldown/plugins'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/

/**
 * 基础依赖 CDN 外置（仅生产构建生效，dev 不受影响）。
 *
 * react / react-dom / react-router 体积大（合计约 900KB 源码量，min+gzip
 * 约 65KB）且版本稳定，改由 esm.sh 以 importmap + external 方式提供：主包
 * 不再打包它们，浏览器按 importmap 裸说明符直取 CDN ESM 模块。
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
 * - react-dom / react-router 的 esm.sh 构建内部会把 peer 依赖 react 解析到
 *   ^19 范围内的最新版（≠ 本地 pin 版本），产生两份 React 实例 →
 *   "Cannot read properties of null (reading 'useContext')"。必须加
 *   `?external=react` 让 esm.sh 产物保持 bare import "react"，统一回落到
 *   本 importmap 的唯一 react 实例；
 * - jsx-runtime / jsx-dev-runtime / client 等子路径单独映射（importmap 不
 *   做查询参数式前缀匹配）。
 */
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
  const esm = (name: string, version: string, subpath = '') =>
    `https://esm.sh/${name}@${version}${subpath}`;
  return {
    'react': esm('react', reactV),
    'react/jsx-runtime': esm('react', reactV, '/jsx-runtime'),
    'react/jsx-dev-runtime': esm('react', reactV, '/jsx-dev-runtime'),
    'react-dom': esm('react-dom', domV, '?external=react'),
    'react-dom/client': esm('react-dom', domV, '/client?external=react'),
    'react-router': esm('react-router', routerV, '?external=react'),
    'react-router/dom': esm('react-router', routerV, '/dom?external=react'),
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

export default defineConfig({
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
})
