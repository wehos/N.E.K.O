import { createRequestClient } from "./createClient";
import { NativeTokenStorage } from "./src/request-client/tokenStorage";
import type { TokenRefreshFn } from "./src/request-client/types";

/**
 * Creates a request client configured for React Native environments.
 *
 * @param options.baseURL - 基础 API 地址（应用侧传入）
 * @param options.refreshApi - 刷新 Token 的回调
 * @returns 基于 NativeTokenStorage 的请求客户端实例
 */
export function createNativeRequestClient(options: { baseURL: string; refreshApi: TokenRefreshFn }) {
  return createRequestClient({
    baseURL: options.baseURL,
    storage: new NativeTokenStorage(),
    refreshApi: options.refreshApi
  });
}

// 导出类型和工具
export { createRequestClient } from "./createClient";
export { NativeTokenStorage } from "./src/request-client/tokenStorage";
export type { RequestClientConfig, TokenStorage, TokenRefreshFn, TokenRefreshResult } from "./src/request-client/types";

