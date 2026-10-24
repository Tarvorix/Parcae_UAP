import { test, expect } from '../../frontend/node_modules/@playwright/test/index';
test('play, pause, seek and replay encoded frames', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('heading', {name:'UAP Observer'})).toBeVisible();
  await expect(page.getByTestId('connection')).toHaveText('Connected');
  await expect(page.getByTestId('timestamp')).toHaveText('0.000 s');
  await page.getByRole('button', {name:'Play',exact:true}).click();
  await expect(page.getByTestId('timestamp')).not.toHaveText('0.000 s');
  await page.getByRole('button', {name:'Pause',exact:true}).click();
  await page.getByRole('combobox').selectOption('video-file');
  await page.getByRole('slider', {name:'Frame',exact:true}).fill('60');
  await expect(page.getByTestId('timestamp')).toHaveText('3.000 s');
  await expect(page.getByText('file_best_effort_pts', {exact:false})).toBeVisible();
  await expect.poll(() => page.locator('img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(320);
  await page.getByRole('button', {name:'Restart',exact:true}).click();
  await expect(page.getByTestId('timestamp')).toHaveText('0.000 s');
  expect(errors).toEqual([]);
});

test('analyze fixture, review partial event and export verified source evidence', async ({page}) => {
  test.setTimeout(60000);
  await page.goto('/');
  await page.getByRole('button',{name:'Analyze generated footage',exact:true}).click();
  await expect(page.getByRole('button',{name:'Analyze generated footage',exact:true})).toBeEnabled({timeout:30000});
  await page.locator('.event-row').first().click();
  await expect(page.getByText('Incomplete context',{exact:true})).toBeVisible();
  await expect(page.locator('video')).toBeVisible();
  await expect.poll(()=>page.locator('video').evaluate((video:HTMLVideoElement)=>video.readyState),{timeout:20000}).toBeGreaterThan(0);
  await page.getByPlaceholder('Record what you observed and why').fill('Known generated test target');
  await page.getByRole('button',{name:'Save review',exact:true}).click();
  await expect(page.getByText('unresolved_insufficient_data: Known generated test target',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Export and verify evidence',exact:true}).click();
  await expect(page.getByRole('link',{name:'Download evidence ZIP',exact:true})).toBeVisible({timeout:20000});
  await page.reload();
  await page.locator('.event-row').first().click();
  await expect(page.getByText('unresolved_insufficient_data: Known generated test target',{exact:true})).toBeVisible();
  await page.getByText('Evidence integrity and source references',{exact:true}).click();
  await expect(page.getByText('Calibration: not_calibrated.',{exact:false})).toBeVisible();
});

test('local update socket reconnects after a transport close',async({page})=>{
  let first=true;
  await page.routeWebSocket('**/api/v1/ws/updates',socket=>{
    socket.connectToServer();
    if(first){first=false;setTimeout(()=>socket.close(),700);}
  });
  await page.goto('/');
  await expect(page.getByTestId('connection')).toHaveText('Connected');
  await expect(page.getByTestId('connection')).toHaveText('Reconnecting',{timeout:5000});
  await expect(page.getByTestId('connection')).toHaveText('Connected',{timeout:10000});
});
