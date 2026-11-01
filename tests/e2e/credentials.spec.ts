import { test, expect } from '../../frontend/node_modules/@playwright/test/index';

test('credential visibility is explicit, accessible and preserves input on a rejected submission', async ({page})=>{
  let submissions=0;
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/v1/camera/start'){
      submissions++;
      await route.fulfill({status:409,json:{detail:'Synthetic rejection'}});return;
    }
    const data=path.startsWith('/api/v1/preview/')?{relative_seconds:0,source_frame_index:0,source_pts:0,time_base:'1/20',timestamp_basis:'generated'}:
      path==='/api/v1/camera/status'?{state:'ready',connection:'stopped'}:
      path==='/api/v1/stats'?{disk_free_bytes:1e9,application_bytes:0,active_job:false}:
      path==='/api/v1/settings'?{revision:0,detector:{},rectangles:[]}:[];
    await route.fulfill({json:data});
  });
  await page.routeWebSocket('**/api/v1/ws/updates',()=>{});
  await page.goto('/');
  const url=page.getByLabel('RTSP address',{exact:true});
  const password=page.getByLabel('Password (if separate)',{exact:true});
  const username=page.getByLabel('Username (if separate)',{exact:true});
  await expect(url).toHaveAttribute('type','password');
  await expect(password).toHaveAttribute('type','password');
  await expect(username).toHaveAttribute('type','text');
  await url.fill('rtsp://127.0.0.1/synthetic-visibility-test');
  await password.fill('synthetic-password-only');
  const showUrl=page.getByRole('button',{name:'Show RTSP address',exact:true});
  await showUrl.focus();await page.keyboard.press('Enter');
  await expect(url).toHaveAttribute('type','text');
  await expect(page.getByRole('button',{name:'Hide RTSP address',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(url).toHaveValue('rtsp://127.0.0.1/synthetic-visibility-test');
  await page.getByRole('button',{name:'Hide RTSP address',exact:true}).click();
  await expect(url).toHaveAttribute('type','password');
  await page.getByRole('button',{name:'Show password',exact:true}).click();
  await expect(password).toHaveAttribute('type','text');
  await page.getByRole('button',{name:'Hide password',exact:true}).click();
  await expect(password).toHaveAttribute('type','password');
  expect(submissions).toBe(0); // visibility buttons never start a capture
  await page.getByRole('button',{name:'Start ten-minute camera test',exact:true}).click();
  await expect(page.locator('section[aria-label="G100 commissioning"]').getByRole('alert')).toContainText('Connection could not start');
  await expect(url).toHaveValue('rtsp://127.0.0.1/synthetic-visibility-test');
  await expect(password).toHaveValue('synthetic-password-only');
  await expect(password).toHaveAttribute('type','password');
  expect(submissions).toBe(1);
  expect(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length}))).toEqual({local:0,session:0});
});
