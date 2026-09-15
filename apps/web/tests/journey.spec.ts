import { test, expect, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
const evidenceDir =
  process.env.NF_SCREENSHOT_DIR || '/private/tmp/neighborhood-fixer-e2e';
async function report(page: Page, share = true) {
  await page.goto('/report');
  await page
    .getByRole('button', { name: 'Use illustrative demo photo' })
    .click();
  await expect(page.getByText('1 photo attached')).toBeVisible();
  if (share) {
    await page.getByLabel(/Share a sanitized observation/).check();
    await page.getByLabel(/Also publish the photos I reviewed above/).check();
  }
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page
    .getByLabel('Street, intersection or landmark')
    .fill('Maple Street & Alder Avenue, Demo Borough');
  await page
    .getByLabel('Is this a public street or walkway?')
    .selectOption('yes');
  await page.getByLabel(/I checked the pin/).check();
  await page
    .getByRole('button', { name: 'Review observations', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: 'Here’s what we know' }),
  ).toBeVisible();
  await expect(
    page.getByText(
      'An image was supplied; the local fixture does not perform visual analysis.',
    ),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Check nearby cases' }).click();
}

async function demonstrateInternalOutreach(page: Page) {
  await page.getByRole('button', { name: 'Find official contact' }).click();
  await expect(
    page.getByRole('heading', { name: 'Confirm the government area' }),
  ).toBeVisible();
  const search = page.getByRole('button', {
    name: 'Confirm Seattle and search',
  });
  await expect(search).toBeDisabled();
  await page
    .getByLabel('I confirm this case is in Seattle, Washington.')
    .check();
  await search.click();

  const contacts = page.getByRole('radio', { name: /City of Seattle/ });
  await expect(contacts.first()).toBeVisible();
  await expect(contacts.first()).not.toBeChecked();
  await expect(page.locator('a[href^="mailto:"]')).toHaveCount(0);
  await expect(page.locator('a[href^="tel:"]')).toHaveCount(0);
  await contacts.first().check();
  await page.getByRole('button', { name: 'Use selected contact' }).click();

  await page.getByRole('button', { name: 'Prepare demo email' }).click();
  await expect(
    page.getByRole('heading', { name: 'Review exact demo email' }),
  ).toBeVisible();
  await page
    .getByLabel(/I approve this exact internal email simulation/)
    .check();
  await page
    .getByRole('button', { name: 'Approve exact email simulation' })
    .click();
  await page
    .getByRole('button', { name: 'Run approved email simulation' })
    .click();
  await expect(page.getByText('Email simulation recorded')).toBeVisible();
  await expect(page.getByText(/No email was sent/)).toBeVisible();

  await page.getByRole('button', { name: 'Prepare demo call' }).click();
  await expect(
    page.getByRole('heading', { name: 'Review demo call envelope' }),
  ).toBeVisible();
  await page.getByLabel(/I approve this internal call simulation/).check();
  await page
    .getByRole('button', { name: 'Approve demo call envelope' })
    .click();
  await page.getByRole('button', { name: 'Start approved demo call' }).click();
  await expect(page.getByText('SIMULATED · NOT DIALED')).toBeVisible({
    timeout: 30000,
  });
  await expect(page.getByRole('log')).toHaveCount(0);
  const audioRequest = page.waitForRequest((request) =>
    /\/outreach\/voice\/runs\/[^/]+\/turns\/[^/]+\/audio$/.test(
      new URL(request.url()).pathname,
    ),
  );
  await page.getByRole('button', { name: 'Play approved demo call' }).click();
  const privateAudio = await audioRequest;
  expect(privateAudio.headers()['x-nf-playback-token']).toBeTruthy();
  expect(new URL(privateAudio.url()).search).toBe('');
  await expect(page.getByRole('log')).toContainText(/pothole|sidewalk|curb/i);
  await page.getByRole('button', { name: 'End demo call' }).click();
  await expect(page.getByText('Call simulation complete')).toBeVisible();
  await expect(
    page.getByText(/Audio and captions were temporary and were not saved/),
  ).toBeVisible();
}
test('two residents share one report, distinguish closure and verify the repair', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1536, height: 1024 });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Neighborhood', exact: true }),
  ).toBeVisible();
  await expect(page.locator('.map-canvas')).toHaveAttribute(
    'data-map-ready',
    'true',
  );
  await mkdir(evidenceDir, { recursive: true });
  await page.screenshot({
    path: join(evidenceDir, 'neighborhood-desktop.png'),
    fullPage: true,
  });
  await report(page);
  await page.getByRole('button', { name: 'Prepare my report' }).click();
  await expect(
    page.getByRole('heading', { name: 'Your report is ready' }),
  ).toBeVisible();
  const caseUrl = page.url().split('?')[0];
  await page.reload();
  await expect(
    page.getByRole('button', { name: 'Approve exact submission' }),
  ).toBeDisabled();
  await demonstrateInternalOutreach(page);
  await page.getByLabel('Local demo resident').selectOption('sam');
  await expect(page.getByLabel('Local demo resident')).toHaveValue('sam');
  await report(page);
  await expect(
    page.getByRole('heading', { name: 'Could this be the same issue?' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Add my observation' }).click();
  await expect(page).toHaveURL(/\/cases\//);
  await expect(page.getByText('2 observations', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Approve exact submission' }),
  ).toHaveCount(0);
  await page.goto('/');
  await expect(page.locator('.incident-row')).toHaveCount(2);
  await expect(page.locator('.case-list img')).toHaveCount(2);
  await expect(page.locator('.map-canvas')).toHaveAttribute(
    'data-map-ready',
    'true',
  );
  await page.screenshot({
    path: join(evidenceDir, 'neighborhood-desktop.png'),
    fullPage: true,
  });
  await page.goto(caseUrl);
  await page.getByLabel('Local demo resident').selectOption('alex');
  await expect(page.getByLabel('Local demo resident')).toHaveValue('alex');
  await page.goto(caseUrl);
  await page.getByLabel(/I approve sending this exact report/).check();
  await page.getByRole('button', { name: 'Approve exact submission' }).click();
  await expect(
    page.getByRole('heading', { name: 'Agency receipt' }),
  ).toBeVisible({ timeout: 45000 });
  await expect(page.getByText('Unverified', { exact: true })).toBeVisible();
  await page.getByText('Local scenario controls', { exact: true }).click();
  await page.getByLabel('Fictional ticket status').selectOption('CLOSED');
  await page
    .getByLabel('Agency note')
    .fill('Duplicate ticket closed by fictional agency');
  await page
    .getByRole('button', { name: 'Update fictional ticket', exact: true })
    .click();
  await expect(page.getByText(/Fictional agency ticket updated/)).toBeVisible();
  await page
    .getByRole('button', { name: 'Advance local clock 1 hour' })
    .click();
  await expect(
    page.getByText('Verification requested', { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByText('Resident-confirmed fixed', { exact: true }),
  ).toHaveCount(0);
  await page.getByLabel('Looks fixed', { exact: true }).check();
  await page
    .getByRole('button', { name: 'Use illustrative after-photo' })
    .click();
  await expect(page.getByText('Photo attached', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Save verification' }).click();
  await expect(
    page.getByRole('heading', { name: 'A repair, confirmed together.' }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'A repair, confirmed together.' }),
  ).toBeVisible();
  await page.getByLabel('Local demo resident').selectOption('sam');
  await expect(
    page.getByRole('heading', { name: 'A repair, confirmed together.' }),
  ).toBeVisible();
  await expect(page.getByText('2 observations', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: join(evidenceDir, 'case-mobile.png'),
    fullPage: false,
  });
  await page.setViewportSize({ width: 1536, height: 1024 });
  await page.screenshot({
    path: join(evidenceDir, 'case-desktop.png'),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
test('mobile map/list, filters and report draft survive refresh', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Neighborhood', exact: true }),
  ).toBeVisible();
  await expect(page.locator('.map-canvas')).toHaveAttribute(
    'data-map-ready',
    'true',
  );
  await expect(page.locator('.map-canvas')).not.toHaveAttribute(
    'data-feature-count',
    '0',
  );
  await page
    .getByRole('combobox', { name: 'Category', exact: true })
    .selectOption('walkway_obstruction');
  await expect(
    page.getByRole('heading', {
      name: 'No matching case found in Neighborhood Fixer',
    }),
  ).toBeVisible();
  await page.goto('/report');
  await page
    .getByLabel('Describe the issue')
    .fill('An obstruction is blocking the public walkway near the corner.');
  await page.reload();
  await expect(page.getByLabel('Describe the issue')).toHaveValue(
    'An obstruction is blocking the public walkway near the corner.',
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
