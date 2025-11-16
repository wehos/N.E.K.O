## Xiao8 / Project N.E.K.O. React Web 前端

这是 Xiao8 / Project N.E.K.O. 的 **Lanlan Terminal Web 前端**，基于 **React Router v7 全栈框架**，
负责：

- **主界面 UI（Live2D + Chat 容器）**
- 与后端 `/api` 的交互与关机 Beacon（`/api/beacon/shutdown`）
- 与根项目 `static/` 目录中的 Live2D / JS 资源（`static/live2d.js`, `static/app.js` 等）的集成

同时保留一个示例组件构建流程（`ExampleButton`），可以将 React 组件打包为 ES Module 供传统 HTML/JS 页面使用。

---

## 目录结构（简化版）

```txt
react_web/
├── app/                      # React Router 应用源码
│   ├── components/           # 可复用的 React 组件（含 ExampleButton 示例）
│   ├── routes/
│   │   └── main.tsx          # Lanlan Terminal 主页面
│   ├── utils/                # API 封装等工具函数
│   ├── root.tsx              # 应用根布局（注入 api.js 等全局脚本）
│   └── routes.ts             # 路由配置
├── scripts/
│   └── copy-component.js     # 将组件构建文件复制到上级项目 static 目录
├── public/                   # 静态资源目录（React Router 用）
├── build/                    # React Router 标准构建输出
│   ├── client/               # 客户端静态资源
│   ├── server/               # SSR 服务端入口
│   └── components/           # 独立组件构建（临时）
├── vite.config.ts            # 应用构建配置
├── vite.component.config.ts  # 独立组件构建配置
└── package.json
```

---

## 与主项目 Xiao8 的集成关系

- **此目录位置**：`Xiao8/react_web`
- **静态资源来源**：依赖根项目的 `static/` 目录（`Xiao8/static`）
- **脚本依赖**：`static/api_interceptor.js`, `static/common_ui.js`, `static/app.js`, `static/libs/*.js`, `static/live2d.js` 等
- **API 地址**：通过环境变量 `VITE_API_BASE_URL` 统一配置，默认 `http://localhost:48911`
- **静态资源服务器地址**：通过 `VITE_STATIC_SERVER_URL` 配置，默认 `http://localhost:48911`

---

## API 拦截器 `static/api_interceptor.js`

- **主要作用**
  - 统一拦截 **`fetch` / `XMLHttpRequest` / `WebSocket`** 调用
  - 自动为以 **`/api/`、`/ws/`、`/static/`** 开头的请求补全前缀：
    - `/api/**`、`/ws/**` → 基于 `API_BASE_URL`（通常为 `VITE_API_BASE_URL`）
    - `/static/**` → 基于 `STATIC_SERVER_URL`（通常为 `VITE_STATIC_SERVER_URL`）
  - 支持既拦相对路径（`/api/...`）也拦截指向当前域名的完整 URL（如 `http://localhost:48911/api/...`）

- **内部工作方式（简要）**
  - 读取全局变量：`window.API_BASE_URL`、`window.STATIC_SERVER_URL`，若未设置则默认 `http://localhost:48911`
  - 包装：
    - `window.fetch(...)`
    - 原生 `XMLHttpRequest.prototype.open(...)`
    - `window.WebSocket(...)`
  - 根据 URL 前缀判断类别：
    - `api` / `ws`：使用 `buildApiUrl` 拼出完整 HTTP 地址，再转换为 WebSocket 地址（`ws://` / `wss://`）
    - `static`：使用 `buildStaticUrl` 拼出完整静态资源地址
  - 在开发调试时通过 `console.log("[API Interceptor] ...")` 打印重写前后 URL，便于排查网络问题

- **加载顺序要求**
  - **必须在其他依赖 `/api`、`/ws`、`/static` 的脚本之前加载**
    - 包括：`static/app.js`、`static/live2d.js`、页面自己的业务脚本等
  - 推荐的 HTML 片段示例（仅示意）：

```html
<script src="/static/api_interceptor.js"></script>
<script src="/static/common_ui.js"></script>
<script src="/static/app.js"></script>
<script src="/static/live2d.js"></script>
```

- **与 React Web 的关系**
  - 在 `app/root.tsx` / `app/routes/main.tsx` 中会根据 `.env` 设置：
    - `window.API_BASE_URL`
    - `window.STATIC_SERVER_URL`
  - 前端 / 旧版 `static/*.js` 中只要继续用 `/api/...`、`/ws/...`、`/static/...` 这类路径，就可以借助拦截器自动走到正确的后端和静态资源服务器，无需在每个调用点手动拼接 Base URL。

在 `app/root.tsx` 和 `app/routes/main.tsx` 中，会根据这些环境变量动态注入：

- `window.API_BASE_URL`
- `window.STATIC_SERVER_URL`
- `window.buildApiUrl` / `window.fetchWithBaseUrl`
- Live2D / 聊天相关的全局对象（如 `window.live2dManager`, `window.LanLan1` 等）

---

## 环境变量

可通过 `.env` 或命令行注入以下变量（Vite / React Router 标准）：

- **`VITE_API_BASE_URL`**  
  - 用途：指向 Xiao8 后端 API 根地址  
  - 默认值：`http://localhost:48911`
  - 影响位置：`app/root.tsx`、`app/routes/main.tsx` 里设置 `window.API_BASE_URL` 与 `fetchWithBaseUrl`

- **`VITE_STATIC_SERVER_URL`**  
  - 用途：指向提供 `static/` 目录的 HTTP 服务地址  
  - 默认值：`http://localhost:48911`
  - 用途示例：
    - 注入 CSS 变量 `--toast-background-url`
    - 拼接 `/static/xxx` 资源路径
    - 在运行时通过 `buildStaticUrl` 自动重写 `/static/` 路径

示例 `.env`：

```bash
VITE_API_BASE_URL=http://localhost:48911
VITE_STATIC_SERVER_URL=http://localhost:48911
```

---

## 安装依赖

```bash
cd react_web
npm install
```

---

## 开发模式

- **仅前端开发（需要后端已启动）**

```bash
cd react_web
npm run dev
```

默认监听 `http://localhost:5173`，前端会：

- 调用 `VITE_API_BASE_URL` 指向的后端接口（如 `/api/config/page_config`、`/api/characters`、`/api/live2d/models` 等）
- 从 `VITE_STATIC_SERVER_URL/static/` 拉取 `live2d.js`、`app.js`、Live2D 模型相关资源

---

## 构建与运行

### 1. 构建 React Router 应用（生产）

```bash
cd react_web
npm run build
```

输出目录：

```txt
build/
├── client/    # 前端静态资源
└── server/    # SSR 入口（Node）
```

> 注：使用 React Router v7 官方推荐的 `build/` 结构。

### 2. 启动生产服务器（仅前端）

```bash
cd react_web
npm run start
```

会通过 `@react-router/serve` 启动 Node 服务器，加载 `build/server/index.js`。
确保此时 Xiao8 主项目后端与 `static/` 静态资源服务已经就绪。

---

## 独立组件构建（ExampleButton 示例）

虽然主界面已经由 `main.tsx` + 传统 JS 管理，但这里仍保留一个示例流程，用于将 React 组件单独打包成 ES Module，方便在模板 HTML 中按需挂载。

### 构建命令

```bash
cd react_web
npm run build:component
```

流程：

1. 使用 `vite.component.config.ts` 将 `ExampleButton` 打包为 ES Module（`build/components/ExampleButton.js`）
2. 在构建过程中：
   - 将 React / ReactDOM 标记为外部依赖，改为从 CDN (`https://esm.sh`) 加载
   - 自动处理 `process.env.NODE_ENV`
   - 自动把 Tailwind CSS 样式内联到 JS，中途注入到 `<head>`
3. 最后通过 `scripts/copy-component.js` 将结果复制到 **上级项目的** `static/ExampleButton.js`

构建输出：

- `build/components/ExampleButton.js`（临时文件）
- `../static/ExampleButton.js`（供模板页面使用）

### 在传统 HTML 中使用组件（示例）

```html
<div id="example-button-container"></div>

<script type="module">
  import { ExampleButton } from "/static/ExampleButton.js";
  import React from "https://esm.sh/react@19";
  import { createRoot } from "https://esm.sh/react-dom@19/client";

  function mountComponent() {
    const container = document.getElementById("example-button-container");
    if (!container) return;
    const root = createRoot(container);
    root.render(
      React.createElement(ExampleButton, {
        buttonText: "打开 Modal",
        onSave: (text1, text2) => {
          console.log("保存的内容:", text1, text2);
        },
      })
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountComponent);
  } else {
    mountComponent();
  }
</script>
```

---

## 与 `static/` 下旧版 JS 的协作方式（重要）

`app/routes/main.tsx` 做了大量「桥接工作」，把现代 React 环境与旧版 `static/*.js` 串起来，核心点包括：

- **全局工具函数与变量**
  - `window.buildApiUrl` / `window.fetchWithBaseUrl`
  - `window.API_BASE_URL`、`window.STATIC_SERVER_URL`
  - `window.pageConfigReady`（异步加载 `/api/config/page_config`）
  - 全局菜单状态：`window.activeMenuCount`、`markMenuOpen`、`markMenuClosed`
- **静态资源路径重写**
  - 拦截 `HTMLImageElement.src` / `Element.setAttribute('src')`
  - 拦截 `style.cssText` / `backgroundImage` 等 CSS 属性
  - 自动把 `/static/...` 替换为基于 `VITE_STATIC_SERVER_URL` 的完整 URL
- **错误与日志处理（开发模式）**
  - 拦截 `console.error` 和 `window.onerror`，静默忽略 static 资源加载失败
- **Beacon 与跨页面通信**
  - 页面关闭时向 `/api/beacon/shutdown` 发送 `navigator.sendBeacon`
  - 通过 `localStorage` + `storage` 事件与设置页面通信，动态隐藏/显示主 UI 以及重新加载 Live2D 模型

修改这部分逻辑时，建议：

- 保持 `window.*` 的对外行为稳定（避免破坏 `static/*.js`）
- 如果新增全局变量或方法，同时在 `global.d.ts` 中补充类型声明

---

## 组件与样式约定

- **组件路径**：`app/components/`
- **样式**：默认使用 Tailwind CSS v4；
  - 若组件单独构建（如 `ExampleButton`），需要：
    - 在组件文件中显式导入 CSS：`import "./ComponentName.css";`
    - CSS 中包含 `@import "tailwindcss";`

---

## 技术栈

- **React Router v7**：全栈 React 框架（路由 + SSR）
- **React 19**：UI 库
- **TypeScript**：类型安全
- **Tailwind CSS v4**：样式系统
- **Vite 7**：构建工具（主应用 & 组件构建）

---

如需后续对 README 做更细的中文说明（比如面向非开发者的部署/使用文档），可以再单独拆一份到 `docs/` 或上层项目的文档中。
