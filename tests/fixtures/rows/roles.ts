export default [
  {
    "name": "each pull request lists once with its roles, less what a ! line takes out, then the adopted ones",
    "session": "s1",
    "files": {
      "sessions/s1/subscriptions": "acme/app#1 review claude-opus-4 claude\nacme/app#1 fix\nacme/app#2 fix\n!acme/app#2 fix\nacme/app#3 review gpt-5 codex\n!acme/app#3 review GPT-5[1m] codex\nAcme/App#4 fix auto\nacme/app review claude-opus-4 claude\n!acme/app#6 review claude-opus-4 claude\nthis line is not a subscription\nacme/web#9 fix extra\n!acme/app fix\n",
      "sessions/s1/state.json": {"adopted": {"acme/app": [5, 6, 1, 0, -2, true, 7.0], "acme/other": [8], "not a repository": [9]}, "fix": {}}
    },
    "rows": [
      {
        "section": "pr",
        "label": "#7",
        "state": "ready",
        "notes": [],
        "roles": ["reviewing"],
        "pull": {
          "repo": "acme/app", "number": 7, "title": "", "url": "https://github.com/acme/app/pull/7", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      },
      {
        "section": "pr",
        "label": "#5",
        "state": "ready",
        "notes": [],
        "roles": ["reviewing"],
        "pull": {
          "repo": "acme/app", "number": 5, "title": "", "url": "https://github.com/acme/app/pull/5", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      },
      {
        "section": "pr",
        "label": "#4",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 4, "title": "", "url": "https://github.com/acme/app/pull/4", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      },
      {
        "section": "pr",
        "label": "#1",
        "state": "ready",
        "notes": [],
        "roles": ["fixing", "reviewing"],
        "pull": {
          "repo": "acme/app", "number": 1, "title": "", "url": "https://github.com/acme/app/pull/1", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      }
    ]
  },
  {
    "name": "an adopted pull request stays while one model of its repository still reviews it",
    "session": "s1",
    "files": {
      "sessions/s1/subscriptions": "acme/app review claude-opus-4 claude\nacme/app review gpt-5 codex\n!acme/app#6 review claude-opus-4 claude\n!acme/app#7 review claude-opus-4 claude\n!acme/app#7 review gpt-5 codex\n",
      "sessions/s1/state.json": {"adopted": {"acme/app": [6, 7]}}
    },
    "rows": [
      {
        "section": "pr",
        "label": "#6",
        "state": "ready",
        "notes": [],
        "roles": ["reviewing"],
        "pull": {
          "repo": "acme/app", "number": 6, "title": "", "url": "https://github.com/acme/app/pull/6", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      }
    ]
  }
]
