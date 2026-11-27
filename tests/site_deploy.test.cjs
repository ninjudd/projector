const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const timers = require('node:timers/promises');
const {spawn} = require('node:child_process');
const {once} = require('node:events');

const actionPath = path.resolve(__dirname, '../actions/site');
const action = fs.readFileSync(path.join(actionPath, 'action.yml'), 'utf8');
const script = action.match(/      script: \|\n((?:        .*\n?)+)/)?.[1];
const uploadStep = action.match(/- id: (\S+)\n\s+uses: actions\/upload-pages-artifact@/)?.[1];
const deploymentEnv = action.split('    - id: deploy\n')[1]?.match(/      env:\n((?:        [A-Z_]+: .*\n)+)/)?.[1] || '';
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function runner(t, responses) {
  const calls = [], outputs = {}, warnings = [];
  t.mock.method(timers, 'setTimeout', async () => {});
  const github = {request: async (route, data) => {
    calls.push({route, data});
    const response = responses.shift();
    if (response instanceof Error) throw response;
    assert.notEqual(response, undefined, `Unexpected request: ${route}`);
    return {data: response};
  }};
  const core = {getIDToken: async () => 'test-oidc-token',
    setOutput: (key, value) => {outputs[key] = value;}, warning: text => warnings.push(text)};
  const context = {repo: {owner: 'example', repo: 'site'}, sha: 'build-sha'};
  return {calls, outputs, warnings, run: async (artifactId = '123456') => {
    assert.ok(script, 'The action must deploy the uploader’s ID through its Pages API script');
    const expressions = {github: {action_path: actionPath}, steps: {[uploadStep]: {outputs: {artifact_id: artifactId}}}};
    const env = Object.fromEntries([...deploymentEnv.matchAll(/        ([A-Z_]+): (.*)/g)].map(([, key, value]) =>
      [key, value.replace(/\$\{\{ ([\w.]+) \}\}/g, (_, name) =>
        name.split('.').reduce((value, key) => value?.[key], expressions) ?? '')]));
    const process = {env};
    return new AsyncFunction('github', 'context', 'core', 'require', 'process', script)(github, context, core, require, process);
  }};
}

test('deploys the uploader ID without listing artifacts, and publishes the URL only after success', async t => {
  const fake = runner(t, [{id: 'deployment', page_url: 'https://example.test/site/'},
    {status: 'queued'}, {status: 'deployment_in_progress'}, {status: 'succeed'}]);
  await fake.run();
  assert.deepEqual(fake.calls[0], {route: 'POST /repos/{owner}/{repo}/pages/deployments', data: {
    owner: 'example', repo: 'site', artifact_id: 123456, pages_build_version: 'build-sha', oidc_token: 'test-oidc-token'}});
  assert.deepEqual(fake.calls.slice(1).map(call => call.route), Array(3).fill('GET /repos/{owner}/{repo}/pages/deployments/{deploymentId}'));
  assert.deepEqual(fake.outputs, {page_url: 'https://example.test/site/'});
});

test('fails before authenticating when the upload has no usable ID', async t => {
  for (const value of ['', 'missing', '0', '-1', '1.2', '9007199254740992']) {
    const fake = runner(t, []);
    await assert.rejects(fake.run(value), /upload.*artifact ID/i);
    assert.deepEqual(fake.calls, []);
  }
});

test('reports a terminal Pages failure and cancels the deployment', async t => {
  for (const status of ['deployment_failed', 'deployment_perms_error', 'deployment_content_failed', 'deployment_cancelled', 'deployment_lost']) {
    const fake = runner(t, [{id: 'failed', page_url: 'https://example.test/'}, {status}, {}]);
    await assert.rejects(fake.run(), new RegExp(status));
    assert.equal(fake.calls.at(-1).route, 'POST /repos/{owner}/{repo}/pages/deployments/{deploymentId}/cancel');
    assert.deepEqual(fake.outputs, {});
  }
});

test('retries a temporary status request failure without creating another deployment', async t => {
  const fake = runner(t, [{id: 'retry', page_url: 'https://example.test/'},
    Object.assign(new Error('temporarily unavailable'), {status: 503}), {status: 'succeed'}]);
  await fake.run();
  assert.equal(fake.calls.filter(call => call.route.startsWith('POST')).length, 1);
  assert.deepEqual(fake.outputs, {page_url: 'https://example.test/'});
});

test('fails and cancels when status requests keep failing', async t => {
  const unavailable = Object.assign(new Error('Pages unavailable'), {status: 503});
  const fake = runner(t, [{id: 'unavailable'}, ...Array(11).fill(unavailable), {}]);
  await assert.rejects(fake.run(), error => error === unavailable);
  assert.equal(fake.calls.at(-1).route, 'POST /repos/{owner}/{repo}/pages/deployments/{deploymentId}/cancel');
  assert.deepEqual(fake.outputs, {});
});

test('stops polling and cancels when Pages never finishes', async t => {
  const fake = runner(t, [{id: 'stuck'}, {status: 'queued'}, {}]);
  const times = [0, 0, 600001];
  t.mock.method(Date, 'now', () => times.shift() ?? 600001);
  await assert.rejects(fake.run(), /timed out/i);
  assert.equal(fake.calls.at(-1).data.deploymentId, 'stuck');
  assert.equal(fake.calls.at(-1).route, 'POST /repos/{owner}/{repo}/pages/deployments/{deploymentId}/cancel');
  assert.deepEqual(fake.outputs, {});
});

test('preserves a polling error when cancellation also fails', async t => {
  const failure = Object.assign(new Error('Pages access denied'), {status: 403});
  const fake = runner(t, [{id: 'denied'}, failure, new Error('cancel unavailable')]);
  await assert.rejects(fake.run(), error => error === failure);
  assert.deepEqual(fake.outputs, {});
});

test('does not poll or cancel a deployment that Pages refused to create', async t => {
  const fake = runner(t, [new Error('creation refused')]);
  await assert.rejects(fake.run(), /creation refused/);
  assert.equal(fake.calls.length, 1);
  assert.deepEqual(fake.outputs, {});
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  test(`${signal} cancels a pending Pages deployment before the process exits`, async t => {
    for (const phase of ['request', 'delay']) {
      await t.test(phase, async t => {
        const child = spawn(process.execPath, ['-e', `
          const deploy = require(process.argv[1]);
          process.channel.ref();
          const github = {request: async (route, data) => {
            if (route.endsWith('/cancel')) {
              process.send({event: 'cancelled', id: data.deploymentId});
              return {data: {}};
            }
            if (route.startsWith('POST')) return {data: {id: 'pending', page_url: 'https://example.test/'}};
            process.send({event: 'polling'});
            if (process.argv[2] === 'delay') return {data: {status: 'queued'}};
            return new Promise((resolve, reject) => data.request?.signal.addEventListener('abort', () => reject(new Error('aborted'))));
          }};
          deploy({github, context: {repo: {owner: 'example', repo: 'site'}, sha: 'test-sha'},
            core: {getIDToken: async () => 'test-token', warning: () => {},
              setOutput: () => process.send({event: 'published'})}, artifactId: '123'})
            .then(() => {process.disconnect();}, error => {
              process.send({event: 'error', message: error.message}, () => {process.disconnect();});
              process.exitCode = 1;
            });
        `, path.join(actionPath, 'deploy.cjs'), phase], {stdio: ['ignore', 'pipe', 'pipe', 'ipc']});
        t.after(() => {child.kill('SIGKILL');});
        const events = [];
        child.on('message', message => events.push(message));
        await once(child, 'message', {signal: AbortSignal.timeout(5000)});
        const exited = once(child, 'exit', {signal: AbortSignal.timeout(5000)});
        child.kill(signal);
        const [code, terminated] = await exited;
        assert.deepEqual({code, terminated}, {code: 1, terminated: null}, JSON.stringify(events));
        assert.deepEqual(events.filter(event => event.event === 'cancelled'), [{event: 'cancelled', id: 'pending'}]);
        assert.equal(events.some(event => event.event === 'published'), false);
        assert.match(events.find(event => event.event === 'error')?.message || '', new RegExp(signal));
      });
    }
  });
}
