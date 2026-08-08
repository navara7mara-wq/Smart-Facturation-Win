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
    const page = await browser.newPage({ viewport: { width: 1200, height: 1600 } });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.pdf({
      path: outputPath,
      format: "A4",
      printBackground: true,
      displayHeaderFooter: false,
      margin: { top: "12mm", right: "13mm", bottom: "12mm", left: "13mm" },
    });
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
