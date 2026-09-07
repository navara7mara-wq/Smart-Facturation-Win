const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const baseUrl = process.env.PHOENIX_BASE_URL || 'http://127.0.0.1:8000';
const username = process.env.PHOENIX_VISUAL_USERNAME || 'admin';
const password = process.env.PHOENIX_VISUAL_PASSWORD || 'Visual123';
const outputDir = path.join(__dirname, '..', 'output', 'playwright');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

(async () => {
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const browserErrors = [];
  page.on('console', message => {
    if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', error => browserErrors.push(`page: ${error.message}`));

  await page.goto(`${baseUrl}/login`, { waitUntil: 'networkidle' });
  assert(await page.locator('body.auth-page').count() === 1, 'La page de connexion n’est pas centrée.');
  await page.screenshot({ path: path.join(outputDir, 'stage-1-login.png'), fullPage: true });
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);
  await Promise.all([
    page.waitForURL(url => !url.pathname.endsWith('/login')),
    page.getByRole('button', { name: 'Se connecter' }).click(),
  ]);

  await page.goto(`${baseUrl}/invoices`, { waitUntil: 'networkidle' });
  const labels = await page.locator('.invoice-head > label > span').allTextContents();
  assert(JSON.stringify(labels) === JSON.stringify(['Bon de commande', 'Site', 'N° Facture', 'Date facture', 'Type facture']), `Ordre des champs incorrect: ${labels.join(', ')}`);
  assert(await page.locator('.searchable-combobox').count() === 2, 'Les deux combobox recherchables sont absentes.');
  assert(await page.locator('#invoice-lines-body > tr').count() === 1, 'La facture doit commencer avec une seule ligne vide.');
  await page.screenshot({ path: path.join(outputDir, 'stage-1-invoices-1440.png'), fullPage: true });

  const targetPo = await page.evaluate(() => {
    const siteOption = Array.from(document.querySelector('select[name="site_id"]').options).find(option => option.dataset.po);
    if (!siteOption) return null;
    const poOption = Array.from(document.querySelector('select[name="purchase_order_id"]').options).find(option => option.value === siteOption.dataset.po);
    return poOption ? { id: poOption.value, text: poOption.textContent.trim() } : null;
  });
  if (targetPo) {
    const poInput = page.locator('.searchable-combobox input').nth(0);
    await poInput.fill(targetPo.text.slice(0, Math.min(5, targetPo.text.length)));
    const visiblePoOptions = page.locator('.searchable-combobox').nth(0).locator('.combobox-option');
    assert(await visiblePoOptions.count() > 0, 'Le filtre des bons de commande ne retourne aucun résultat.');
    await visiblePoOptions.first().click();
    const siteInput = page.locator('.searchable-combobox input').nth(1);
    await siteInput.click();
    assert(await page.locator('.searchable-combobox').nth(1).locator('.combobox-option').count() > 0, 'Les sites du BC sélectionné ne sont pas proposés.');
    await page.keyboard.press('Escape');
  }

  for (let index = 0; index < 10; index += 1) {
    const row = page.locator('#invoice-lines-body > tr').nth(index);
    await row.evaluate((element, articleNumber) => { element.dataset.articleNumber = String(articleNumber); }, index + 1);
    await row.locator('.quantity-input').fill('1');
  }
  assert(await page.locator('#invoice-lines-body > tr').count() === 11, 'Une seule ligne vide doit suivre les dix lignes remplies.');
  assert(await page.locator('#invoice-lines-scroll.is-scrollable').count() === 1, 'Le défilement vertical ne s’active pas à dix articles.');
  await page.screenshot({ path: path.join(outputDir, 'stage-1-invoices-scroll.png'), fullPage: true });

  await page.goto(`${baseUrl}/invoices?message=Enregistrement%20reussi`, { waitUntil: 'networkidle' });
  assert(await page.locator('.app-toast-success.is-visible').count() === 1, 'Le toast de succès ne s’affiche pas.');
  await page.waitForTimeout(5400);
  assert(await page.locator('.app-toast').count() === 0, 'Le toast de succès ne disparaît pas après cinq secondes.');

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`${baseUrl}/invoices`, { waitUntil: 'networkidle' });
  const layoutCheck = await page.evaluate(() => ({
    horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    rowCount: document.querySelectorAll('#invoice-lines-body > tr').length,
  }));
  assert(!layoutCheck.horizontalOverflow, 'La page Factures déborde horizontalement en 1280x800.');
  assert(layoutCheck.rowCount === 1, 'La ligne initiale unique n’est pas conservée en affichage compact.');
  await page.screenshot({ path: path.join(outputDir, 'stage-1-invoices-1280.png'), fullPage: true });

  assert(browserErrors.length === 0, browserErrors.join('\n'));
  await browser.close();
  process.stdout.write(JSON.stringify({ ok: true, screenshots: 4 }, null, 2));
})().catch(async error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
