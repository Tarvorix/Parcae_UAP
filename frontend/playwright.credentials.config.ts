import { defineConfig } from '@playwright/test';
// A separate static preview with mocked APIs; never touches a running camera app.
export default defineConfig({
  testDir:'../tests/e2e',testMatch:'credentials.spec.ts',workers:1,
  use:{baseURL:'http://127.0.0.1:8123',headless:true,screenshot:'off',trace:'off',video:'off',
    launchOptions:process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}},
  webServer:{command:'npx vite preview --host 127.0.0.1 --port 8123 --strictPort',
    url:'http://127.0.0.1:8123',reuseExistingServer:false,timeout:30000},
});
