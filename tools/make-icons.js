/*
 * Генерирует PNG-иконки из SVG-исходников в папке icons/.
 * Нужен Playwright с Chromium:  npm i -D playwright && npx playwright install chromium
 * Запуск из корня репозитория:  node tools/make-icons.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ICONS = path.join(__dirname, '..', 'icons');
const targets = [
  { src: 'favicon.svg', out: 'favicon-32.png', size: 32, transparent: true },
  { src: 'app-icon.svg', out: 'apple-touch-icon.png', size: 180 },
  { src: 'app-icon.svg', out: 'icon-192.png', size: 192 },
  { src: 'app-icon.svg', out: 'icon-512.png', size: 512 },
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  for (const t of targets) {
    const svg = fs.readFileSync(path.join(ICONS, t.src), 'utf8');
    await page.setViewportSize({ width: t.size, height: t.size });
    await page.setContent(
      `<style>html,body{margin:0;background:transparent}svg{display:block;width:${t.size}px;height:${t.size}px}</style>${svg}`
    );
    await page.screenshot({ path: path.join(ICONS, t.out), omitBackground: !!t.transparent });
    console.log('icons/' + t.out);
  }
  await browser.close();
})();
