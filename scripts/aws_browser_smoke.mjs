/**
 * Opt-in, read-only AWS browser verification using an existing provisioned account.
 * No reports, traces, screenshots, storage state, network logs, or raw errors are saved.
 *
 * NF_RUN_AWS_BROWSER_SMOKE=1 npm run test:aws:web -- --config .local/amplify/frontend.manifest.json
 * Supply NF_AWS_SMOKE_USERNAME and NF_AWS_SMOKE_PASSWORD through private environment.
 * The Clerk account must already have a password and be admitted for validation.
 * MFA, CAPTCHA, and email verification challenges require the account owner to finish setup.
 * Optional --session-output .local/aws-sessions/alex.json exports an authenticated
 * short-lived API session for an authorized backend journey. It contains a bearer
 * token, is written atomically with mode 0600, and must be kept private and deleted
 * after use. No session is exported without this explicit argument.
 */
import { randomUUID } from 'node:crypto';
import {
  lstat,
  mkdir,
  open,
  readFile,
  realpath,
  rename,
  unlink,
} from 'node:fs/promises';
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repository = fileURLToPath(new URL('..', import.meta.url));
function contains(parent, path) {
  const child = relative(parent, path);
  return (
    child === '' ||
    (child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child))
  );
}

export function sessionOutputPath(args, root = repository) {
  const index = args.indexOf('--session-output');
  if (index === -1) return undefined;
  const value = args[index + 1];
  if (
    !value ||
    value.startsWith('--') ||
    args.lastIndexOf('--session-output') !== index
  )
    throw new Error('Supply exactly one explicit session output path.');
  const destination = resolve(value);
  if (
    contains(root, destination) &&
    !contains(join(root, '.local'), destination)
  )
    throw new Error(
      'Repository session exports must remain under ignored .local.',
    );
  return destination;
}

export async function writeSessionExport(
  destination,
  session,
  root = repository,
) {
  // Validate before creating any file; do not serialize arbitrary API response data.
  if (
    !destination ||
    !/^[\w-]+\.[\w-]+\.[\w-]+$/.test(session.access_token || '') ||
    !Number.isFinite(session.expires_at) ||
    session.expires_at <= Date.now() ||
    typeof session.workspace_id !== 'string' ||
    !session.workspace_id ||
    typeof session.user_id !== 'string' ||
    !session.user_id
  )
    throw new Error(
      'A verified, unexpired API session is required for export.',
    );
  const record = {
    schema_version: 1,
    api_origin: httpsOrigin(session.api_origin, 'session API origin'),
    workspace_id: session.workspace_id,
    user_id: session.user_id,
    access_token: session.access_token,
    expires_at: new Date(session.expires_at).toISOString(),
  };
  const target = sessionOutputPath(['--session-output', destination], root);
  await mkdir(dirname(target), { recursive: true, mode: 0o700 });
  const parent = await realpath(dirname(target));
  // Refuse symlink redirection, including a symlinked .local directory.
  if (parent !== dirname(target))
    throw new Error('Session output directory must not use symlinks.');
  const existing = await lstat(target).catch((error) => {
    if (error.code === 'ENOENT') return undefined;
    throw error;
  });
  if (existing && !existing.isFile())
    throw new Error('Session output must be a regular file.');
  const temporary = join(parent, `.nf-session-${randomUUID()}.tmp`);
  const file = await open(temporary, 'wx', 0o600);
  try {
    await file.writeFile(JSON.stringify(record, null, 2) + '\n', 'utf8');
    await file.sync();
    await file.close();
    await rename(temporary, join(parent, basename(target)));
  } finally {
    await file.close().catch(() => {});
    await unlink(temporary).catch((error) => {
      if (error.code !== 'ENOENT') throw error;
    });
  }
}

export function httpsOrigin(value, name) {
  try {
    const url = new URL(value);
    if (
      url.protocol !== 'https:' ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== '/' ||
      ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
    )
      throw new Error();
    return url.origin;
  } catch {
    throw new Error(`Invalid ${name}; supply a deployed HTTPS origin.`);
  }
}

export function smokeConfig(env, manifest = {}) {
  const frontend = manifest.frontend || {};
  const required = (name, fallback) => {
    const value = env[name] || fallback;
    if (typeof value !== 'string' || !value)
      throw new Error(`Missing ${name}.`);
    return value;
  };
  const region = required(
    'NF_AWS_REGION',
    frontend.aws_region || env.AWS_REGION,
  );
  if (!/^[a-z]{2}(?:-[a-z]+)+-\d+$/.test(region))
    throw new Error('Invalid NF_AWS_REGION.');
  return {
    web: httpsOrigin(
      required('NF_AWS_WEB_URL', frontend.web_url),
      'NF_AWS_WEB_URL',
    ),
    api: httpsOrigin(
      required('NF_AWS_API_URL', frontend.api_url),
      'NF_AWS_API_URL',
    ),
    clerk: httpsOrigin(
      required('NF_AWS_CLERK_ISSUER', frontend.clerk_issuer),
      'NF_AWS_CLERK_ISSUER',
    ),
    audience: frontend.auth_audience || 'neighborhood-fixer-api',
    mapHost: `maps.geo.${region}.amazonaws.com`,
    username: required('NF_AWS_SMOKE_USERNAME'),
    password: required('NF_AWS_SMOKE_PASSWORD'),
  };
}

export async function probeAuthenticatedApi({ api, accessToken, expiresAt }) {
  const tokenPresent = typeof accessToken === 'string' && Boolean(accessToken);
  const headers = tokenPresent
    ? { Authorization: `Bearer ${accessToken}` }
    : {};
  const probe = async (path, options = {}, includeSession = false) => {
    try {
      const response = await fetch(`${api}${path}`, {
        headers,
        credentials: 'omit',
        ...options,
      });
      let value = {};
      let jsonValid = true;
      if (includeSession && response.ok) {
        try {
          value = await response.json();
        } catch {
          jsonValid = false;
        }
      }
      return { status: response.status, value, jsonValid };
    } catch {
      // Fetch exceptions can include URLs; return only a fixed marker.
      return { status: 'network_error', value: {}, jsonValid: false };
    }
  };
  const session = await probe('/api/session', {}, true);
  const value = session.value || {};
  const incidents = await probe('/api/incidents');
  const fixtures = await probe('/api/demo/fixtures');
  const demoLogin = await probe('/api/demo/session', {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ resident: 'alex' }),
  });
  return {
    session: session.status,
    sessionJsonValid: session.jsonValid,
    tokenPresent,
    mode: value.mode,
    member: Boolean(value.user?.id && value.workspace_id),
    workspace_id: value.workspace_id,
    user_id: value.user?.id,
    expires_at: expiresAt,
    incidents: incidents.status,
    fixtures: fixtures.status,
    demoLogin: demoLogin.status,
  };
}

// Run the browser library directly; failure DOM, traces and token logs are never saved.
export function tokenMetadata(token) {
  try {
    const value = JSON.parse(
      Buffer.from(token.split('.')[1], 'base64url').toString('utf8'),
    );
    return {
      expiresAt: value.exp * 1000,
      issuer: value.iss,
      audience: value.aud,
      origin: value.azp,
      scope: value.scope,
      session: value.sid,
    };
  } catch {
    throw new Error('A verified Clerk session token is required.');
  }
}

export function signedOutEntry(page) {
  return page.getByRole('link', { name: 'Sign in', exact: true });
}

export async function runSmoke(env = process.env) {
  if (env.NF_RUN_AWS_BROWSER_SMOKE !== '1') {
    console.log(
      'SKIP AWS browser smoke: set NF_RUN_AWS_BROWSER_SMOKE=1 explicitly.',
    );
    return 0;
  }
  let phase = 'configuration',
    browser,
    context,
    timer;
  try {
    const configIndex = process.argv.indexOf('--config');
    const configPath =
      configIndex >= 0
        ? process.argv[configIndex + 1]
        : env.NF_AWS_FRONTEND_MANIFEST;
    const manifest = configPath
      ? JSON.parse(await readFile(configPath, 'utf8'))
      : {};
    const c = smokeConfig(env, manifest);
    const sessionOutput = sessionOutputPath(process.argv);
    const keeperIndex = process.argv.indexOf('--keep-session-seconds');
    const keeperSeconds =
      keeperIndex < 0 ? 0 : Number(process.argv[keeperIndex + 1]);
    if (
      !Number.isInteger(keeperSeconds) ||
      keeperSeconds < 0 ||
      keeperSeconds > 600 ||
      (keeperSeconds && !sessionOutput)
    )
      throw new Error('Invalid bounded session keeper configuration.');
    delete process.env.DEBUG;
    delete process.env.PWDEBUG;
    delete process.env.PLAYWRIGHT_DEBUG;
    const { chromium } = await import('@playwright/test');
    const browserEnv = Object.fromEntries(
      Object.entries(process.env).filter(
        ([name]) =>
          !/^(NF_|AWS_|VITE_|CLERK_|DEBUG$|PWDEBUG$|PLAYWRIGHT_DEBUG$)/.test(
            name,
          ),
      ),
    );
    browser = await chromium.launch({ headless: true, env: browserEnv });
    context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      serviceWorkers: 'block',
    });
    const page = await context.newPage();
    page.setDefaultTimeout(25000);
    page.setDefaultNavigationTimeout(35000);
    timer = setTimeout(
      () => {
        console.error(`FAIL ${phase}: bounded AWS browser check timed out.`);
        void browser?.close().finally(() => process.exit(1));
      },
      (200 + keeperSeconds) * 1000,
    );
    let accessToken,
      appErrors = 0,
      descriptorLoaded = false,
      tileLoaded = false;
    const captures = [];
    page.on('pageerror', () => {
      if (new URL(page.url()).origin === c.web) appErrors++;
    });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (
        url.origin === c.api &&
        url.pathname === '/api/session' &&
        request.method() === 'GET'
      ) {
        captures.push(
          request
            .allHeaders()
            .then((headers) => {
              const bearer = /^Bearer ([\w-]+\.[\w-]+\.[\w-]+)$/.exec(
                headers.authorization || '',
              );
              if (bearer) accessToken = bearer[1];
            })
            .catch(() => {}),
        );
      }
    });
    page.on('response', (response) => {
      const url = new URL(response.url());
      if (url.hostname === c.mapHost && response.ok()) {
        if (url.pathname.endsWith('/style-descriptor')) descriptorLoaded = true;
        if (url.pathname.includes('/tiles/')) tileLoaded = true;
      }
    });
    const verify = (condition) => {
      if (!condition) throw new Error('Verification failed.');
    };
    const pass = (step) => console.log(`PASS ${step}`);
    phase = 'AWS browser CORS preflight';
    const preflight = await context.request.fetch(`${c.api}/api/health`, {
      method: 'OPTIONS',
      headers: {
        Origin: c.web,
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers':
          'authorization,content-type,idempotency-key',
      },
    });
    const cors = preflight.headers();
    const allowedHeaders = (cors['access-control-allow-headers'] || '')
      .toLowerCase()
      .split(',')
      .map((s) => s.trim());
    verify(
      preflight.ok() &&
        cors['access-control-allow-origin'] === c.web &&
        ['authorization', 'content-type', 'idempotency-key'].every((h) =>
          allowedHeaders.includes(h),
        ),
    );
    pass(phase);
    phase = 'AWS health and signed-out entry';
    const health = await context.request.get(`${c.api}/api/health`);
    verify(health.ok() && (await health.json()).mode === 'aws');
    verify(
      [401, 403].includes(
        (await context.request.get(`${c.api}/api/session`)).status(),
      ),
    );
    await page.goto(c.web + '/', { waitUntil: 'domcontentloaded' });
    await signedOutEntry(page).click();
    phase = 'Clerk sign-in (verification challenges require owner setup)';
    verify([c.web, c.clerk].includes(new URL(page.url()).origin));
    await page
      .locator(
        'input[name="identifier"]:visible,input[name="emailAddress"]:visible,input[type="email"]:visible',
      )
      .first()
      .fill(c.username);
    const password = page.locator('input[type="password"]:visible').first();
    if (!(await password.isVisible()))
      await page.getByRole('button', { name: /^Continue$/i }).click();
    await password.waitFor({ state: 'visible' });
    verify([c.web, c.clerk].includes(new URL(page.url()).origin));
    await password.fill(c.password);
    await page
      .getByRole('button', { name: /^Continue$|^Sign in$/i })
      .last()
      .click();
    await page
      .getByRole('heading', { name: 'Neighborhood', exact: true })
      .waitFor();
    verify(new URL(page.url()).origin === c.web && !new URL(page.url()).search);
    await Promise.all(captures);
    const checks = async () => {
      verify(accessToken);
      const metadata = tokenMetadata(accessToken);
      verify(
        metadata.issuer === c.clerk &&
          (metadata.audience === c.audience ||
            metadata.audience?.includes?.(c.audience)) &&
          metadata.origin === c.web &&
          metadata.scope?.split(' ').includes('nf:resident') &&
          metadata.session?.startsWith('sess_') &&
          metadata.expiresAt > Date.now(),
      );
      const result = await page.evaluate(probeAuthenticatedApi, {
        api: c.api,
        accessToken,
        expiresAt: metadata.expiresAt,
      });
      verify(
        result.session === 200 &&
          result.mode === 'aws' &&
          result.member &&
          result.incidents === 200 &&
          [403, 404].includes(result.fixtures) &&
          [403, 404].includes(result.demoLogin),
      );
      return result;
    };
    let api = await checks();
    pass(
      'Clerk sign-in and authenticated API with exact issuer/audience/origin/scope',
    );
    verify((await page.getByLabel('Local demo resident').count()) === 0);
    verify(
      (await page
        .getByText('Local scenario controls', { exact: true })
        .count()) === 0,
    );
    pass('development controls absent and rejected');
    phase = 'Amazon Location style, tiles, rendered map, and attribution';
    await page.waitForFunction(
      () =>
        document
          .querySelector('.map-canvas')
          ?.getAttribute('data-map-ready') === 'true',
      null,
      { timeout: 45000 },
    );
    verify(
      descriptorLoaded &&
        tileLoaded &&
        (await page.locator('.maplibregl-ctrl-attrib').innerText()).includes(
          'Amazon Location',
        ) &&
        appErrors === 0,
    );
    pass(phase);
    phase = 'page refresh and Clerk session renewal';
    const oldToken = accessToken;
    // A real token refresh is tested across Clerk's short session-token lifetime.
    await page.waitForTimeout(35000);
    await page.waitForTimeout(35000);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page
      .getByRole('heading', { name: 'Neighborhood', exact: true })
      .waitFor();
    await Promise.all(captures);
    api = await checks();
    verify(accessToken !== oldToken && appErrors === 0);
    pass(phase);
    const exportSession = async () => {
      if (sessionOutput)
        await writeSessionExport(sessionOutput, {
          api_origin: c.api,
          workspace_id: api.workspace_id,
          user_id: api.user_id,
          access_token: accessToken,
          expires_at: tokenMetadata(accessToken).expiresAt,
        });
    };
    await exportSession();
    if (sessionOutput) pass('private authenticated session export');
    const end = Date.now() + keeperSeconds * 1000;
    while (Date.now() < end) {
      await page.waitForTimeout(Math.min(20000, end - Date.now()));
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page
        .getByRole('heading', { name: 'Neighborhood', exact: true })
        .waitFor();
      await Promise.all(captures);
      api = await checks();
      await exportSession();
    }
    phase = 'Clerk logout and signed-out refresh';
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await signedOutEntry(page).waitFor();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await signedOutEntry(page).waitFor();
    verify(
      [401, 403].includes(
        (await context.request.get(`${c.api}/api/session`)).status(),
      ),
    );
    pass(phase);
    console.log(
      'PASS AWS Clerk browser smoke: no reports submitted or browser artifacts saved.',
    );
    return 0;
  } catch {
    console.error(`FAIL ${phase}. No sensitive diagnostics were recorded.`);
    return 1;
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    clearTimeout(timer);
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  process.exitCode = await runSmoke();
