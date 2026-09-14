/**
 * Opt-in, read-only AWS browser verification using an existing provisioned account.
 * No reports, traces, screenshots, storage state, network logs, or raw errors are saved.
 *
 * NF_RUN_AWS_BROWSER_SMOKE=1 npm run test:aws:web -- --config .local/amplify/frontend.manifest.json
 * Supply NF_AWS_SMOKE_USERNAME and NF_AWS_SMOKE_PASSWORD through private environment.
 * The account must already have a permanent password and workspace membership.
 * MFA, CAPTCHA, and password-change challenges require the account owner to finish setup.
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
  const clientId = required(
    'NF_AWS_COGNITO_CLIENT_ID',
    frontend.cognito_client_id,
  );
  if (!/^[a-zA-Z0-9]+$/.test(clientId))
    throw new Error('Invalid NF_AWS_COGNITO_CLIENT_ID.');
  return {
    web: httpsOrigin(
      required('NF_AWS_WEB_URL', frontend.web_url),
      'NF_AWS_WEB_URL',
    ),
    api: httpsOrigin(
      required('NF_AWS_API_URL', frontend.api_url),
      'NF_AWS_API_URL',
    ),
    cognito: httpsOrigin(
      required('NF_AWS_COGNITO_DOMAIN', frontend.cognito_domain),
      'NF_AWS_COGNITO_DOMAIN',
    ),
    clientId,
    mapHost: `maps.geo.${region}.amazonaws.com`,
    username: required('NF_AWS_SMOKE_USERNAME'),
    password: required('NF_AWS_SMOKE_PASSWORD'),
  };
}

export async function probeAuthenticatedApi(api) {
  let token;
  try {
    token = JSON.parse(sessionStorage.getItem('nf-cognito-access') || 'null');
  } catch {
    token = null;
  }
  const tokenPresent =
    typeof token?.accessToken === 'string' && Boolean(token.accessToken);
  const headers = tokenPresent
    ? { Authorization: `Bearer ${token.accessToken}` }
    : {};
  const probe = async (path, options = {}, includeSession = false) => {
    try {
      const response = await fetch(`${api}${path}`, {
        headers,
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
    expires_at: token?.expiresAt,
    incidents: incidents.status,
    fixtures: fixtures.status,
    demoLogin: demoLogin.status,
  };
}

// Playwright's test runner captures failure DOM even when screenshots are off.
// Use its browser library directly so authentication pages never become artifacts.
export async function runSmoke(env = process.env) {
  if (env.NF_RUN_AWS_BROWSER_SMOKE !== '1') {
    console.log(
      'SKIP AWS browser smoke: set NF_RUN_AWS_BROWSER_SMOKE=1 explicitly.',
    );
    return 0;
  }
  let phase = 'configuration';
  let browser;
  let context;
  let timeout;
  const pass = (label) => console.log(`PASS ${label}`);
  const verify = (condition) => {
    if (!condition) throw new Error('Check failed.');
  };
  try {
    const configIndex = process.argv.indexOf('--config');
    const configPath =
      env.NF_AWS_FRONTEND_MANIFEST ||
      (configIndex >= 0 ? process.argv[configIndex + 1] : undefined);
    const manifest = configPath
      ? JSON.parse(await readFile(configPath, 'utf8'))
      : {};
    const c = smokeConfig(env, manifest);
    const sessionOutput = sessionOutputPath(process.argv);
    // Debug output can include locator values and OAuth URLs. Disable it before import.
    delete process.env.DEBUG;
    delete process.env.PWDEBUG;
    delete process.env.PLAYWRIGHT_DEBUG;
    const { chromium } = await import('@playwright/test');
    const browserEnv = Object.fromEntries(
      Object.entries(process.env).filter(
        ([name]) =>
          !/^(NF_|AWS_|VITE_|DEBUG$|PWDEBUG$|PLAYWRIGHT_DEBUG$)/.test(name),
      ),
    );
    timeout = setTimeout(() => {
      console.error(`FAIL ${phase}: bounded AWS browser check timed out.`);
      void browser?.close().finally(() => process.exit(1));
      setTimeout(() => process.exit(1), 2000).unref();
    }, 180000);
    browser = await chromium.launch({ headless: true, env: browserEnv });
    context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      serviceWorkers: 'block',
    });
    const page = await context.newPage();
    page.setDefaultTimeout(25000);
    page.setDefaultNavigationTimeout(35000);
    let appErrors = 0;
    let authorizedPkce = false;
    let tokenExchange = false;
    let descriptorLoaded = false;
    let tileLoaded = false;
    let observedAccessToken;
    const tokenCaptures = [];
    page.on('pageerror', () => {
      if (new URL(page.url()).origin === c.web) appErrors++;
    });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (
        sessionOutput &&
        url.origin === c.api &&
        url.pathname === '/api/session' &&
        request.method() === 'GET'
      ) {
        tokenCaptures.push(
          request
            .allHeaders()
            .then((headers) => {
              const bearer = /^Bearer ([\w-]+\.[\w-]+\.[\w-]+)$/.exec(
                headers.authorization || '',
              );
              if (bearer) observedAccessToken = bearer[1];
            })
            .catch(() => {}),
        );
      }
      if (url.origin === c.cognito && url.pathname === '/oauth2/authorize') {
        const p = url.searchParams;
        authorizedPkce =
          p.get('response_type') === 'code' &&
          p.get('code_challenge_method') === 'S256' &&
          /^[\w-]{43}$/.test(p.get('code_challenge') || '') &&
          (p.get('state') || '').length >= 32 &&
          p.get('client_id') === c.clientId &&
          p.get('redirect_uri') === `${c.web}/`;
      }
    });
    page.on('response', (response) => {
      const url = new URL(response.url());
      if (url.origin === c.cognito && url.pathname === '/oauth2/token')
        tokenExchange = response.ok();
      if (url.hostname === c.mapHost && response.ok()) {
        if (url.pathname.endsWith('/style-descriptor')) descriptorLoaded = true;
        if (url.pathname.includes('/tiles/')) tileLoaded = true;
      }
    });

    phase = 'AWS browser CORS preflight';
    const preflight = await context.request.fetch(`${c.api}/api/health`, {
      method: 'OPTIONS',
      headers: {
        Origin: c.web,
        'Access-Control-Request-Method': 'GET',
        'Access-Control-Request-Headers': 'authorization,content-type',
      },
    });
    const cors = preflight.headers();
    const allowedHeaders = (cors['access-control-allow-headers'] || '')
      .toLowerCase()
      .split(',')
      .map((header) => header.trim());
    verify(
      preflight.ok() &&
        cors['access-control-allow-origin'] === c.web &&
        cors['access-control-allow-credentials'] === 'true' &&
        allowedHeaders.includes('authorization') &&
        allowedHeaders.includes('content-type'),
    );
    pass(phase);

    phase = 'AWS health and signed-out entry';
    const health = await context.request.get(`${c.api}/api/health`);
    verify(health.ok() && (await health.json()).mode === 'aws');
    const anonymous = await context.request.get(`${c.api}/api/session`);
    verify([401, 403].includes(anonymous.status()));
    await page.goto(`${c.web}/`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Sign in with Cognito' }).waitFor();
    pass(phase);

    phase = 'Cognito authorization redirect';
    await page.getByRole('button', { name: 'Sign in with Cognito' }).click();
    await page.waitForURL((url) => url.origin === c.cognito);
    phase = 'Cognito S256 PKCE request validation';
    verify(authorizedPkce);
    // Restrict credential entry to the configured provider's HTTPS origin.
    verify(new URL(page.url()).origin === c.cognito);
    phase = 'Cognito username field';
    await page
      .locator('input[name="username"]:visible')
      .first()
      .fill(c.username);
    phase = 'Cognito password field';
    await page
      .locator('input[type="password"]:visible')
      .first()
      .fill(c.password);
    phase = 'Cognito sign-in submit control';
    // Observed Classic Hosted UI exposes aria-label="submit", so its accessible
    // name is not the visible "Sign in" value. Use the form's stable input name.
    await page
      .locator('input[name="signInSubmitButton"]:visible')
      .first()
      .click();
    phase = 'return from Cognito (account challenges require owner setup)';
    await page.waitForURL((url) => url.origin === c.web);
    await page
      .getByRole('heading', { name: 'Neighborhood', exact: true })
      .waitFor();
    verify(tokenExchange && !new URL(page.url()).search);
    verify(
      await page.evaluate(() => {
        const token = JSON.parse(
          sessionStorage.getItem('nf-cognito-access') || 'null',
        );
        return Boolean(
          token?.accessToken &&
          token.expiresAt > Date.now() &&
          !sessionStorage.getItem('nf-cognito-pkce'),
        );
      }),
    );
    pass('Cognito sign-in with S256 PKCE');

    phase = 'authenticated API response checks';
    const apiChecks = await page.evaluate(probeAuthenticatedApi, c.api);
    // Allowlisted numeric/boolean diagnostics only. apiChecks also carries private
    // identity/export fields, so never log or serialize that object wholesale.
    console.log(
      'CHECK API ' +
        JSON.stringify({
          session_status: apiChecks.session,
          session_json_valid: apiChecks.sessionJsonValid === true,
          token_present: apiChecks.tokenPresent === true,
          aws_mode: apiChecks.mode === 'aws',
          member: apiChecks.member === true,
          incidents_status: apiChecks.incidents,
          fixtures_status: apiChecks.fixtures,
          demo_login_status: apiChecks.demoLogin,
        }),
    );
    const localResidentCount = await page
      .getByLabel('Local demo resident')
      .count();
    const localControlCount = await page
      .getByText('Local scenario controls', { exact: true })
      .count();
    const integrationStatusCount = await page
      .getByText('Integration status', { exact: true })
      .count();
    console.log(
      'CHECK controls ' +
        JSON.stringify({
          local_resident: localResidentCount,
          local_controls: localControlCount,
          integration_status: integrationStatusCount,
        }),
    );
    verify(
      apiChecks.session === 200 &&
        apiChecks.mode === 'aws' &&
        apiChecks.member &&
        apiChecks.incidents === 200 &&
        [403, 404].includes(apiChecks.fixtures) &&
        [403, 404].includes(apiChecks.demoLogin),
    );
    pass(phase);
    phase = 'local controls absent and AWS integration status present';
    verify(
      localResidentCount === 0 &&
        localControlCount === 0 &&
        integrationStatusCount === 1,
    );
    pass(phase);

    if (sessionOutput) {
      phase = 'private authenticated API session export';
      await Promise.all(tokenCaptures);
      verify(authorizedPkce && tokenExchange && observedAccessToken);
      await writeSessionExport(sessionOutput, {
        api_origin: c.api,
        workspace_id: apiChecks.workspace_id,
        user_id: apiChecks.user_id,
        expires_at: apiChecks.expires_at,
        access_token: observedAccessToken,
      });
      observedAccessToken = undefined;
      pass(phase);
    }

    phase = 'Amazon Location style, tiles, rendered map, and attribution';
    await page.waitForFunction(
      () => {
        const map = document.querySelector('.map-canvas');
        // Empty workspaces are valid. Style readiness plus successful provider
        // tile requests and attribution below do not require any incident markers.
        return map?.getAttribute('data-map-ready') === 'true';
      },
      null,
      { timeout: 45000 },
    );
    await page.locator('.maplibregl-ctrl-attrib').waitFor({ state: 'visible' });
    verify(descriptorLoaded && tileLoaded);
    verify(
      (await page.locator('.maplibregl-ctrl-attrib').innerText()).includes(
        'Amazon Location',
      ),
    );
    verify(appErrors === 0);
    pass(phase);

    phase = 'page refresh retains the authenticated session';
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page
      .getByRole('heading', { name: 'Neighborhood', exact: true })
      .waitFor();
    await page.getByRole('button', { name: 'Sign out', exact: true }).waitFor();
    verify(appErrors === 0);
    pass(phase);

    phase = 'Cognito logout and signed-out refresh';
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await page.getByRole('button', { name: 'Sign in with Cognito' }).waitFor();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Sign in with Cognito' }).waitFor();
    verify(
      await page.evaluate(() => !sessionStorage.getItem('nf-cognito-access')),
    );
    verify(new URL(page.url()).origin === c.web && !new URL(page.url()).search);
    const loggedOut = await context.request.get(`${c.api}/api/session`);
    verify([401, 403].includes(loggedOut.status()));
    pass(phase);
    console.log(
      sessionOutput
        ? 'PASS AWS browser smoke: private session exported as requested; no reports or browser artifacts saved.'
        : 'PASS AWS browser smoke: no reports submitted; no credentials or browser artifacts saved.',
    );
    return 0;
  } catch {
    // Never include the caught exception: Playwright can embed passwords, OAuth
    // codes, map keys, and account-specific DOM in its standard diagnostics.
    console.error(`FAIL ${phase}. No sensitive diagnostics were recorded.`);
    return 1;
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    clearTimeout(timeout);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  process.exitCode = await runSmoke();
