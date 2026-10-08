'use strict';

// Reuse the branch-triggered build. "Latest" can temporarily describe a
// cancelled predecessor with the same commit, so inspect the build history.
module.exports = async function ensurePagesBuild(github, repo, commit, info,
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  const current = async () => {
    const {data} = await github.rest.repos.listPagesBuilds({...repo, per_page: 30});
    return data.filter(b => b.commit === commit).sort((a,b) =>
      Date.parse(b.created_at) - Date.parse(a.created_at) ||
      Number(b.url.split('/').pop()) - Number(a.url.split('/').pop()))[0];
  };
  let build = await current();
  if (!build || build.status === 'errored') {
    await github.rest.repos.requestPagesBuild(repo);
    info(`Requested Pages build for ${commit}`);
  } else {
    info(`Reusing Pages build ${build.url} (${build.status})`);
  }
  for (let attempt = 0; attempt < 30; attempt++) {
    build = await current();
    if (build?.status === 'built') return;
    // A replacement queued build may not be visible immediately. Bound the
    // wait instead of treating an earlier cancelled build as this run's result.
    await sleep(15000);
  }
  throw new Error(`Pages build did not finish for ${commit}: ${build?.status || 'missing'}; ${build?.error?.message || ''}`);
};
