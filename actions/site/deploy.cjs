const timers = require('node:timers/promises');

module.exports = async ({github, context, core, artifactId}) => {
  if (!/^\d+$/.test(artifactId || '') || !Number.isSafeInteger(Number(artifactId)) || Number(artifactId) <= 0) {
    throw new Error('The site upload did not return a valid artifact ID.');
  }
  const controller = new AbortController();
  let interrupted, parameters;
  const handlers = ['SIGINT', 'SIGTERM'].map(signal => [signal, () => {
    interrupted ||= new Error(`Pages deployment interrupted by ${signal}.`);
    controller.abort();
  }]);
  for (const [signal, handler] of handlers) process.on(signal, handler);
  try {
    const {data: deployment} = await github.request('POST /repos/{owner}/{repo}/pages/deployments', {
      ...context.repo,
      artifact_id: Number(artifactId),
      pages_build_version: context.sha,
      oidc_token: await core.getIDToken(),
    });
    const deploymentId = deployment.id || deployment.status_url?.split('/').pop() || context.sha;
    parameters = {...context.repo, deploymentId};
    const deadline = Date.now() + 600000;
    let errors = 0;
    while (Date.now() < deadline) {
      if (interrupted) throw interrupted;
      let status;
      try {
        const response = await github.request('GET /repos/{owner}/{repo}/pages/deployments/{deploymentId}', {
          ...parameters, request: {signal: controller.signal},
        });
        status = response.data.status;
        errors = 0;
      } catch (error) {
        if ((error.status === 429 || error.status >= 500) && ++errors <= 10) {
          core.warning(`Pages status request failed (${error.status}); retrying.`);
          await timers.setTimeout(5000, undefined, {signal: controller.signal});
          continue;
        }
        throw error;
      }
      if (interrupted) throw interrupted;
      if (status === 'succeed') {
        if (!deployment.page_url) throw new Error('Pages completed without returning the site URL.');
        core.setOutput('page_url', deployment.page_url);
        return;
      }
      if (!['queued', 'deployment_in_progress', 'unknown_status', 'not_found', 'deployment_attempt_error'].includes(status)) {
        throw new Error(`Pages deployment failed: ${status}.`);
      }
      await timers.setTimeout(5000, undefined, {signal: controller.signal});
    }
    throw new Error('Pages deployment timed out after 10 minutes.');
  } catch (error) {
    if (parameters) {
      try {
        await github.request('POST /repos/{owner}/{repo}/pages/deployments/{deploymentId}/cancel', parameters);
      } catch {
        core.warning('Could not cancel the Pages deployment.');
      }
    }
    throw interrupted || error;
  } finally {
    for (const [signal, handler] of handlers) process.removeListener(signal, handler);
  }
};
