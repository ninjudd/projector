export default [
  {
    "name": "a row carries the drawer's facts and the link to its summary",
    "session": "s1",
    "files": {
      "daemon.json": {"operator": "ada"},
      "sessions/s1/subscriptions": "acme/app#12 fix\nacme/app#11 review claude-opus-4 claude\n",
      "cache/acme/app/pulls/12.json": {
        "repository": {
          "pullRequest": {
            "title": "Add the console",
            "url": "https://github.com/acme/app/pull/12",
            "state": "OPEN",
            "author": {"login": "ada"},
            "headRefName": "console/rows",
            "additions": 120,
            "deletions": 8,
            "changedFiles": 4,
            "labels": {"nodes": [{"name": "enhancement", "color": "a2eeef"}]},
            "body": "Adds rows.\r\n\r\n<!-- projector-summary v=1 sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->\r\n📽️ **Projector summary** of aaaaaaa: https://projector.bot/acme/app/reviews/12 \r\n"
          }
        }
      },
      "cache/acme/app/pulls/11.json": {
        "repository": {
          "pullRequest": {
            "title": "Fix the cache",
            "author": {"login": "bob"},
            "headRefName": "fix-cache",
            "body": "<!-- projector-summary v=1 sha=aaaaaaa -->\n**Projector summary** of aaaaaaa: https://projector.bot/acme/app/reviews/11\n"
          }
        }
      }
    },
    "rows": [
      {
        "section": "pr",
        "label": "#12",
        "state": "ready",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 12, "title": "Add the console", "url": "https://github.com/acme/app/pull/12",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "ada", "branch": "console/rows", "additions": 120, "deletions": 8, "changed_files": 4,
          "checks": null, "is_cancelled": false, "labels": ["enhancement"],
          "summary_url": "https://projector.bot/acme/app/reviews/12", "is_loading": false
        }
      },
      {
        "section": "review",
        "label": "#11",
        "state": "ready",
        "notes": [],
        "roles": ["reviewing"],
        "pull": {
          "repo": "acme/app", "number": 11, "title": "Fix the cache", "url": "https://github.com/acme/app/pull/11",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": "bob", "branch": "fix-cache", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      }
    ]
  }
]
