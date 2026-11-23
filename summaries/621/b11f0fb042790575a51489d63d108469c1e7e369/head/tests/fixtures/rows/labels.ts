export default [
  {
    "name": "one repository labels each row by its number",
    "session": "s1",
    "files": {"sessions/s1/subscriptions": "acme/app#3 fix\nacme/app#5 fix\n"},
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
        "label": "#3",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 3, "title": "", "url": "https://github.com/acme/app/pull/3", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      }
    ]
  },
  {
    "name": "repositories whose names differ label each row by name and number",
    "session": "s1",
    "files": {"sessions/s1/subscriptions": "acme/app#3 fix\nacme/web#5 fix\n"},
    "rows": [
      {
        "section": "pr",
        "label": "app#3",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 3, "title": "", "url": "https://github.com/acme/app/pull/3", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      },
      {
        "section": "pr",
        "label": "web#5",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/web", "number": 5, "title": "", "url": "https://github.com/acme/web/pull/5", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      }
    ]
  },
  {
    "name": "repositories that share a name label each row by owner, repository, and number",
    "session": "s1",
    "files": {"sessions/s1/subscriptions": "acme/app#3 fix\nother/app#4 fix\nacme/web#1 fix\n"},
    "rows": [
      {
        "section": "pr",
        "label": "acme/app#3",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 3, "title": "", "url": "https://github.com/acme/app/pull/3", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      },
      {
        "section": "pr",
        "label": "acme/web#1",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/web", "number": 1, "title": "", "url": "https://github.com/acme/web/pull/1", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": true
        }
      },
      {
        "section": "pr",
        "label": "other/app#4",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "other/app", "number": 4, "title": "", "url": "https://github.com/other/app/pull/4",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": true
        }
      }
    ]
  }
]
