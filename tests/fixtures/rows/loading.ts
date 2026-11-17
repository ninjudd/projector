export default [
  {
    "name": "a pull request the daemon has no result for is a loading row, and a closed one has no row",
    "session": "s1",
    "files": {
      "daemon.json": {"operator": "ada"},
      "sessions/s1/subscriptions": "acme/app#1 fix\nacme/app#2 fix\nacme/app#3 fix\nacme/app#4 fix\nacme/app#5 fix\nacme/app#6 fix\n",
      "cache/acme/app/pulls/2.json": {"repository": {"pullRequest": {"state": "MERGED"}}},
      "cache/acme/app/pulls/3.json": {"repository": {"pullRequest": {"state": "CLOSED"}}},
      "cache/acme/app/pulls/4.json": "{not json",
      "cache/acme/app/pulls/5.json": {"repository": null},
      "cache/acme/app/pulls/6.json": {"repository": {"pullRequest": {}}}
    },
    "rows": [
      {
        "section": "pr",
        "label": "#5",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
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
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 1, "title": "", "url": "https://github.com/acme/app/pull/1", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      },
      {
        "section": "review",
        "label": "#6",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 6, "title": "", "url": "https://github.com/acme/app/pull/6", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": false
        }
      }
    ]
  }
]
