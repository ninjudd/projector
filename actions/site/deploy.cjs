const timers = require('node:timers/promises');

module.exports = async ({github, context, core, artifactId}) => {
  if (!/^\d+$/.test(artifactId || '') || !Number.isSafeInteger(Number(artifactId)) || Number(artifactId) <= 0) {
    throw new Error('The site upload did not return a valid artifact ID.');
  }
  const {data: deployment} = await github.request('POST /repos/{owner}/{repo}/pages/deployments', {
    ...context.repo,
    artifact_id: Number(artifactId),
    pages_build_version: context.sha,
    oidc_token: await core.getIDToken(),
  });
  const deploymentId = deployment.id || deployment.status_url?.split('/').pop() || context.sha;
  const parameters = {...context.repo, deploymentId};
  const deadline = Date.now() + 600000;
  let errors = 0;
  try {
    while (Date.now() < deadline) {
      let status;
      try {
        const response = await github.request('GET /repos/{owner}/{repo}/pages/deployments/{deploymentId}', parameters);
        status = response.data.status;
        errors = 0;
      } catch (error) {
        if ((error.status === 429 || error.status >= 500) && ++errors <= 10) {
          core.warning(`Pages status request failed (${error.status}); retrying.`);
          await timers.setTimeout(5000);
          continue;
        }
        throw error;
      }
      if (status === 'succeed') {
        if (!deployment.page_url) throw new Error('Pages completed without returning the site URL.');
        core.setOutput('page_url', deployment.page_url);
        return;
      }
      if (!['queued', 'deployment_in_progress', 'unknown_status', 'not_found', 'deployment_attempt_error'].includes(status)) {
        throw new Error(`Pages deployment failed: ${status}.`);
      }
      await timers.setTimeout(5000);
    }
    throw new Error('Pages deployment timed out after 10 minutes.');
  } catch (error) {
    try {
      await github.request('POST /repos/{owner}/{repo}/pages/deployments/{deploymentId}/cancel', parameters);
    } catch {
      core.warning('Could not cancel the Pages deployment.');
    }
    throw error;
  }
};
