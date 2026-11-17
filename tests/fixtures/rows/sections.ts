export default [
  {
    "name": "your pull requests come first, then review requests by author, each by repository in the panel's collation and newest first",
    "session": "s1",
    "files": {
      "daemon.json": {"pid": 1, "operator": "ada"},
      "sessions/s1/subscriptions": "acme/app-b#1 fix\nacme/app_b#2 fix\nacme/app_b#3 fix\nacme/app1#4 fix\nacme/app_b#5 fix\nacme/app-b#6 fix\nacme/app_b#7 fix\nacme/app_b#8 fix\nacme/app_b#9 fix\nacme/app_b#10 fix\n",
      "cache/acme/app-b/pulls/1.json": {"repository": {"pullRequest": {"author": {"login": "ada"}}}},
      "cache/acme/app_b/pulls/2.json": {"repository": {"pullRequest": {"author": {"login": "ada"}}}},
      "cache/acme/app1/pulls/4.json": {"repository": {"pullRequest": {"author": {"login": "ada"}}}},
      "cache/acme/app_b/pulls/5.json": {"repository": {"pullRequest": {"author": {"login": "carol"}}}},
      "cache/acme/app-b/pulls/6.json": {"repository": {"pullRequest": {"author": {"login": "Bob"}}}},
      "cache/acme/app_b/pulls/7.json": {"repository": {"pullRequest": {"author": null}}},
      "cache/acme/app_b/pulls/8.json": {"repository": {"pullRequest": {"author": {"login": "bob"}}}},
      "cache/acme/app_b/pulls/9.json": {"repository": {"pullRequest": {"author": {"login": "ada"}}}},
      "cache/acme/app_b/pulls/10.json": {"repository": {"pullRequest": {"author": {"login": "Ada"}}}}
    },
    "rows": [
      {
        "section": "pr",
        "label": "app_b#9",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app_b", "number": 9, "title": "", "url": "https://github.com/acme/app_b/pull/9",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "ada", "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "app_b#3",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app_b", "number": 3, "title": "", "url": "https://github.com/acme/app_b/pull/3",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": true
        }
      },
      {
        "section": "pr",
        "label": "app_b#2",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app_b", "number": 2, "title": "", "url": "https://github.com/acme/app_b/pull/2",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "ada", "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "app-b#1",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app-b", "number": 1, "title": "", "url": "https://github.com/acme/app-b/pull/1",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "ada", "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "app1#4",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app1", "number": 4, "title": "", "url": "https://github.com/acme/app1/pull/4",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "ada", "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "review",
        "label": "app_b#7",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app_b", "number": 7, "title": "", "url": "https://github.com/acme/app_b/pull/7",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "review",
        "label": "app_b#10",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app_b", "number": 10, "title": "", "url": "https://github.com/acme/app_b/pull/10",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "Ada", "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "review",
        "label": "app_b#8",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app_b", "number": 8, "title": "", "url": "https://github.com/acme/app_b/pull/8",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "bob", "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "review",
        "label": "app-b#6",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app-b", "number": 6, "title": "", "url": "https://github.com/acme/app-b/pull/6",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "Bob", "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "review",
        "label": "app_b#5",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app_b", "number": 5, "title": "", "url": "https://github.com/acme/app_b/pull/5",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "carol", "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      }
    ]
  },
  {
    "name": "until the operator is known, every pull request lists with yours",
    "session": "s1",
    "files": {
      "daemon.json": {"pid": 1, "operator": ""},
      "sessions/s1/subscriptions": "acme/app#1 fix\nacme/app#2 fix\n",
      "cache/acme/app/pulls/1.json": {"repository": {"pullRequest": {"author": {"login": "bob"}}}},
      "cache/acme/app/pulls/2.json": {"repository": {"pullRequest": {"author": {"login": "ada"}}}}
    },
    "rows": [
      {
        "section": "pr",
        "label": "#2",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 2, "title": "", "url": "https://github.com/acme/app/pull/2", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": "ada",
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": false
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
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": "bob",
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": false
        }
      }
    ]
  }
]
