import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  httpsOrigin,
  probeAuthenticatedApi,
  sessionOutputPath,
  smokeConfig,
  writeSessionExport,
} from '../scripts/aws_browser_smoke.mjs';

const manifest = {
  frontend: {
    web_url: 'https://main.example.amplifyapp.com',
    api_url: 'https://example.execute-api.us-west-2.amazonaws.com',
    aws_region: 'us-west-2',
    cognito_domain: 'https://example.auth.us-west-2.amazoncognito.com',
    cognito_client_id: 'exampleclient123',
  },
};
const privateEnv = {
  NF_AWS_SMOKE_USERNAME: 'private-account@example.invalid',
  NF_AWS_SMOKE_PASSWORD: 'private-test-only-placeholder',
};

test('deployment manifest configures browser targets without storing account credentials', () => {
  const config = smokeConfig(privateEnv, manifest);
  assert.equal(config.api, manifest.frontend.api_url);
  assert.equal(config.web, manifest.frontend.web_url);
  assert.equal(config.mapHost, 'maps.geo.us-west-2.amazonaws.com');
  assert.equal(config.username, privateEnv.NF_AWS_SMOKE_USERNAME);
  assert.equal(
    JSON.stringify(manifest).includes(privateEnv.NF_AWS_SMOKE_PASSWORD),
    false,
  );
});

test('rejects local targets and credential or callback-bearing URLs without echoing them', () => {
  for (const value of [
    'http://localhost:5173',
    'https://localhost',
    'https://user:private-placeholder@example.com',
    'https://example.com/?code=private-placeholder',
    'https://example.com/#private-placeholder',
    'https://example.com/path',
  ]) {
    assert.throws(
      () => httpsOrigin(value, 'NF_AWS_WEB_URL'),
      (error) => {
        assert.equal(
          error.message,
          'Invalid NF_AWS_WEB_URL; supply a deployed HTTPS origin.',
        );
        return true;
      },
    );
  }
});

test('missing permanent account credentials fail closed with only the variable name', () => {
  assert.throws(
    () => smokeConfig({}, manifest),
    /^Error: Missing NF_AWS_SMOKE_USERNAME\.$/,
  );
  assert.throws(
    () =>
      smokeConfig(
        { NF_AWS_SMOKE_USERNAME: privateEnv.NF_AWS_SMOKE_USERNAME },
        manifest,
      ),
    /^Error: Missing NF_AWS_SMOKE_PASSWORD\.$/,
  );
});

test('default invocation skips before browser startup or configuration reads', () => {
  const result = spawnSync(
    process.execPath,
    ['scripts/aws_browser_smoke.mjs'],
    {
      cwd: new URL('..', import.meta.url),
      env: {
        PATH: process.env.PATH,
        NF_AWS_FRONTEND_MANIFEST: '/nonexistent/private-placeholder',
        ...privateEnv,
      },
      encoding: 'utf8',
    },
  );
  assert.equal(result.status, 0);
  assert.match(result.stdout, /^SKIP AWS browser smoke:/);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout.includes('private-placeholder'), false);
});

test('explicitly enabled invalid configuration fails without leaking paths or credentials', () => {
  const result = spawnSync(
    process.execPath,
    ['scripts/aws_browser_smoke.mjs'],
    {
      cwd: new URL('..', import.meta.url),
      env: {
        PATH: process.env.PATH,
        NF_RUN_AWS_BROWSER_SMOKE: '1',
        NF_AWS_FRONTEND_MANIFEST: '/nonexistent/private-placeholder',
        ...privateEnv,
      },
      encoding: 'utf8',
    },
  );
  assert.equal(result.status, 1);
  assert.equal(result.stdout, '');
  assert.equal(
    result.stderr,
    'FAIL configuration. No sensitive diagnostics were recorded.\n',
  );
});

test('session export requires an explicit path and cannot enter tracked repository areas', () => {
  const root = '/project/neighborhood-fixer';
  assert.equal(sessionOutputPath([], root), undefined);
  assert.equal(
    sessionOutputPath(
      ['--session-output', `${root}/.local/session.json`],
      root,
    ),
    `${root}/.local/session.json`,
  );
  assert.equal(
    sessionOutputPath(
      ['--session-output', '/private/caller-session.json'],
      root,
    ),
    '/private/caller-session.json',
  );
  assert.throws(() => sessionOutputPath(['--session-output'], root));
  assert.throws(() =>
    sessionOutputPath(['--session-output', `${root}/session.json`], root),
  );
  assert.throws(() =>
    sessionOutputPath(
      ['--session-output', `${root}/.local/../package.json`],
      root,
    ),
  );
});

test('verified session export is atomic, mode 0600, and only includes approved fields', async () => {
  const directory = await realpath(
    await mkdtemp(join(tmpdir(), 'nf-session-export-')),
  );
  try {
    const destination = join(directory, '.local', 'sessions', 'alex.json');
    const session = {
      api_origin: manifest.frontend.api_url,
      workspace_id: 'isolated-test-workspace',
      user_id: 'synthetic-test-user',
      access_token: 'synthetic.jwt.placeholder',
      expires_at: Date.UTC(2099, 0, 1),
      password: 'excluded-private-placeholder',
    };
    await writeSessionExport(destination, session, directory);
    assert.equal((await stat(destination)).mode & 0o777, 0o600);
    const stored = JSON.parse(await readFile(destination, 'utf8'));
    assert.deepEqual(Object.keys(stored), [
      'schema_version',
      'api_origin',
      'workspace_id',
      'user_id',
      'access_token',
      'expires_at',
    ]);
    assert.equal(stored.access_token, session.access_token);
    assert.equal(stored.expires_at, '2099-01-01T00:00:00.000Z');
    assert.equal(JSON.stringify(stored).includes(session.password), false);
    await writeSessionExport(
      destination,
      { ...session, access_token: 'replacement.jwt.placeholder' },
      directory,
    );
    assert.equal(
      JSON.parse(await readFile(destination, 'utf8')).access_token,
      'replacement.jwt.placeholder',
    );
    assert.deepEqual(await readdir(join(directory, '.local', 'sessions')), [
      'alex.json',
    ]);
    assert.equal((await stat(destination)).mode & 0o777, 0o600);
    await assert.rejects(
      writeSessionExport(destination, { ...session, expires_at: 0 }, directory),
    );
    assert.equal(
      JSON.parse(await readFile(destination, 'utf8')).access_token,
      'replacement.jwt.placeholder',
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('session export refuses a destination symlink without replacing its target', async () => {
  const directory = await realpath(
    await mkdtemp(join(tmpdir(), 'nf-session-link-')),
  );
  try {
    const outside = join(directory, 'preserved.json');
    const destination = join(directory, 'linked.json');
    await writeFile(outside, 'preserved');
    await symlink(outside, destination);
    await assert.rejects(
      writeSessionExport(destination, {
        api_origin: manifest.frontend.api_url,
        workspace_id: 'workspace',
        user_id: 'user',
        access_token: 'synthetic.jwt.placeholder',
        expires_at: Date.UTC(2099, 0, 1),
      }),
    );
    assert.equal(await readFile(outside, 'utf8'), 'preserved');
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('one rejected API fetch yields a fixed diagnostic while remaining probes complete', async () => {
  const savedFetch = globalThis.fetch;
  const savedStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    'sessionStorage',
  );
  const requested = [];
  try {
    Object.defineProperty(globalThis, 'sessionStorage', {
      configurable: true,
      value: {
        getItem: () =>
          JSON.stringify({
            accessToken: 'private-test-token',
            expiresAt: Date.UTC(2099, 0, 1),
          }),
      },
    });
    globalThis.fetch = async (url) => {
      const path = new URL(url).pathname;
      requested.push(path);
      if (path === '/api/demo/fixtures')
        throw new Error('private-url-and-token-placeholder');
      const status = path === '/api/demo/session' ? 403 : 200;
      return {
        status,
        ok: status === 200,
        json: async () => ({
          mode: 'aws',
          user: { id: 'private-user' },
          workspace_id: 'private-workspace',
        }),
      };
    };
    const result = await probeAuthenticatedApi(manifest.frontend.api_url);
    assert.deepEqual(requested, [
      '/api/session',
      '/api/incidents',
      '/api/demo/fixtures',
      '/api/demo/session',
    ]);
    assert.equal(result.tokenPresent, true);
    assert.equal(result.session, 200);
    assert.equal(result.fixtures, 'network_error');
    assert.equal(result.demoLogin, 403);
    assert.equal(
      JSON.stringify(result).includes('private-url-and-token-placeholder'),
      false,
    );
    assert.equal(JSON.stringify(result).includes('private-test-token'), false);
    globalThis.sessionStorage.getItem = () => 'invalid-json';
    const missing = await probeAuthenticatedApi(manifest.frontend.api_url);
    assert.equal(missing.tokenPresent, false);
  } finally {
    globalThis.fetch = savedFetch;
    if (savedStorage)
      Object.defineProperty(globalThis, 'sessionStorage', savedStorage);
    else delete globalThis.sessionStorage;
  }
});
