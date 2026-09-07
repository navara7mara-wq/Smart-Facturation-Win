const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  });
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("request", (request) => {
    if (request.method() === "POST") {
      console.log(JSON.stringify({ request: request.url(), headers: request.headers(), data: request.postData() }));
    }
  });
  page.on("response", (response) => {
    if (response.request().method() === "POST") {
      console.log(JSON.stringify({ response: response.url(), status: response.status(), headers: response.headers() }));
    }
  });
  await page.goto("http://127.0.0.1:8766/login");
  console.log(JSON.stringify({ cookies: await context.cookies(), token: await page.locator('[name="csrf_token"]').inputValue() }));
  await page.locator('[name="username"]').fill("admin");
  await page.locator('[name="password"]').fill("Visual123");
  await page.locator('button[type="submit"]').click();
  await page.waitForTimeout(1000);
  console.log(JSON.stringify({ finalUrl: page.url(), cookies: await context.cookies() }));
  await browser.close();
})();
