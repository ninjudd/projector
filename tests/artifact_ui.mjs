import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright', {
  paths: [process.env.PROJECTOR_PLAYWRIGHT_DIR ?? process.cwd()]
}));
const output = mkdtempSync(join(tmpdir(), 'projector-artifact-ui-'));
process.stdout.write(`Screenshots: ${output}\n`);
const bases = process.argv.slice(2);
if (!bases.length) bases.push('http://127.0.0.1:8765/demo/');
const browser = await chromium.launch({ headless: true, channel: process.env.PROJECTOR_BROWSER_CHANNEL });
const results = [];
try {
  for (const base of bases) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.setDefaultNavigationTimeout(10000);
    const failures = [];
    const origin = new URL(base).origin;
    page.on('pageerror', error => failures.push(error.message));
    page.on('response', response => {
      if (response.url().startsWith(origin) && response.status() >= 400) {
        failures.push(`${response.status()} ${response.url()}`);
      }
    });
    const suffix = new URL(base).pathname === '/' ? 'root' : 'prefix';
    try {
      const response = await context.request.get(`${base}site.json`);
      assert(response.ok());
      const site = await response.json();
      assert.equal(site.artifacts.length, 1);
      const artifact = site.artifacts[0];
      assert.equal(artifact.versions.length, 2);
      const older = artifact.versions.find(version => version.version !== artifact.latest);
      const latestRoute = `${base}artifacts/${artifact.id}/`;
      const fixedRoute = `${latestRoute}${older.version}/`;
      const frame = () => page.frameLocator('iframe.htmlpage');
      const result = async value => {
        await frame().getByText(`Result: ${value}`, { exact: true }).waitFor();
        await frame().getByText('Binary bytes: 0, 255, 0, 13, 10', { exact: true }).waitFor();
        const image = frame().getByRole('img', { name: 'Cache scale' });
        await image.waitFor();
        await image.evaluate(element => element.decode());
        assert(await image.evaluate(element => element.complete && element.naturalWidth > 0), 'The relative SVG did not load');
      };
      await page.goto(`${base}artifacts/`);
      await page.getByRole('heading', { name: 'Artifacts', exact: true }).waitFor();
      const filter = page.getByRole('searchbox', { name: 'Filter artifacts' });
      await filter.fill('no-such-artifact');
      await page.getByText('No artifacts match.', { exact: true }).waitFor();
      assert.equal(await page.getByRole('link', { name: 'Cache explorer', exact: true }).count(), 0);
      await filter.fill('cache');
      await page.getByRole('link', { name: 'Cache explorer', exact: true }).waitFor();
      assert.equal(await page.getByRole('link', { name: 'Cache explorer', exact: true }).count(), 1);
      await page.goto(`${latestRoute}?value=6#details`);
      await result(29);
      assert.equal(await frame().getByRole('slider', { name: 'Cached entries' }).inputValue(), '6');
      assert.match(await page.locator('iframe.htmlpage').getAttribute('src'), new RegExp(`${artifact.latest}/index.html\\?value=6#details$`));
      await page.getByRole('combobox', { name: 'Artifact version' }).selectOption(older.version);
      await page.waitForURL(`${fixedRoute}?value=6#details`);
      await result(23);
      await page.goBack();
      await page.waitForURL(`${latestRoute}?value=6#details`);
      await result(29);
      await page.goto(`${fixedRoute}?value=6#details`);
      await result(23);
      await frame().getByRole('slider', { name: 'Cached entries' }).press('ArrowRight');
      await result(24);
      await page.waitForURL(`${fixedRoute}?value=6#entries-7`);
      await page.getByRole('link', { name: 'Latest', exact: true }).click();
      await page.waitForURL(`${latestRoute}?value=6#entries-7`);
      await result(29);
      const before = await page.locator('iframe.htmlpage').boundingBox();
      await page.getByRole('button', { name: 'Full screen', exact: true }).click();
      await page.locator('header.sitebar').waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: 'Exit full screen', exact: true }).waitFor();
      const expanded = await page.locator('iframe.htmlpage').boundingBox();
      assert(expanded.y < before.y && expanded.height > before.height, 'Full screen did not expand the artifact');
      await page.getByRole('button', { name: 'Exit full screen', exact: true }).click();
      await page.locator('header.sitebar').waitFor({ state: 'visible' });
      await page.screenshot({ path: join(output, `desktop-${suffix}.png`), fullPage: true });
      await page.setViewportSize({ width: 375, height: 812 });
      await result(29);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'The artifact wrapper overflows the phone viewport');
      assert(await frame().locator('body').evaluate(body => body.ownerDocument.documentElement.scrollWidth <= body.ownerDocument.defaultView.innerWidth), 'The artifact content overflows the phone viewport');
      await page.getByRole('combobox', { name: 'Artifact version' }).selectOption(older.version);
      await page.waitForURL(`${fixedRoute}?value=6#entries-7`);
      await result(23);
      await frame().getByRole('slider', { name: 'Cached entries' }).press('ArrowRight');
      await result(24);
      await page.getByRole('button', { name: 'Full screen', exact: true }).click();
      await page.locator('header.sitebar').waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: 'Exit full screen', exact: true }).click();
      await page.locator('header.sitebar').waitFor({ state: 'visible' });
      await page.screenshot({ path: join(output, `phone-${suffix}.png`), fullPage: true });
      await page.goto(`${base}artifacts/`);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.getByRole('searchbox', { name: 'Search the site' }).fill('Cached entries');
      await page.getByRole('searchbox', { name: 'Search the site' }).press('Enter');
      await page.getByRole('link', { name: 'Cache explorer', exact: true }).waitFor();
      assert.equal(await page.getByRole('link', { name: 'Cache explorer', exact: true }).count(), 1);
      assert.deepEqual(failures, []);
      results.push({ base, passed: true, versions: 2, desktop: '1440x900', phone: '375x812' });
    } catch (error) {
      process.stderr.write(`Failed at ${page.url()}\n`);
      await page.screenshot({ path: join(output, `failure-${suffix}.png`), fullPage: true }).catch(() => {});
      throw error;
    } finally {
      await context.close();
    }
  }
  process.stdout.write(JSON.stringify(results, null, 2) + '\n');
} finally {
  await browser.close();
}
