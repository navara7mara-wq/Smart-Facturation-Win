const { chromium } = require("playwright");
const fs = require("fs");

const browserCandidates = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];

async function main() {
  const [url, outputPath] = process.argv.slice(2);
  if (!url || !outputPath) {
    throw new Error("Usage: node render_pdf.js <url> <outputPath>");
  }
  const executablePath = browserCandidates.find((path) => fs.existsSync(path));
  const browser = await chromium.launch({
    headless: true,
    executablePath,
  });
  try {
    const context = await browser.newContext({ viewport: { width: 1200, height: 1600 } });
    const sessionToken = process.env.PHOENIX_RENDER_SESSION;
    if (sessionToken) {
      await context.addCookies([{
        name: process.env.PHOENIX_RENDER_SESSION_COOKIE || "phoenix_session",
        value: sessionToken,
        url: new URL(url).origin,
        httpOnly: true,
        sameSite: "Lax",
      }]);
    }
    const page = await context.newPage();
    await page.goto(url, { waitUntil: "networkidle" });
    if (
      new URL(page.url()).pathname === "/login" &&
      process.env.PHOENIX_RENDER_ALLOW_LOGIN !== "1"
    ) {
      throw new Error("PDF preview authentication failed");
    }
    await page.pdf({
      path: outputPath,
      format: "A4",
      printBackground: true,
      displayHeaderFooter: false,
      margin: { top: "12mm", right: "13mm", bottom: "12mm", left: "13mm" },
    });
    await context.close();
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
