import { defineConfig } from '@playwright/test';
export default defineConfig({testDir:'../tests/e2e',workers:1,use:{baseURL:'http://127.0.0.1:8000',headless:true,launchOptions:process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {}},webServer:{command:'cd .. && make demo',url:'http://127.0.0.1:8000/api/v1/health',reuseExistingServer:false,timeout:60000}});
