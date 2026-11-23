export default [
  {
    "name": "the state word is the first rule that holds, and every other one that holds follows as a note",
    "session": "s1",
    "files": {
      "sessions/s1/subscriptions": "acme/app#1 fix\nacme/app#2 fix\nacme/app#3 fix\nacme/app#4 fix\nacme/app#5 fix\nacme/app#6 fix\nacme/app#7 fix\nacme/app#8 fix\n",
      "cache/acme/app/pulls/8.json": {
        "repository": {
          "pullRequest": {
            "isDraft": true,
            "reviewDecision": "CHANGES_REQUESTED",
            "reviewThreads": {"nodes": [{"isResolved": false}, {"isResolved": true}]},
            "labels": {"nodes": [{"name": "reviewing", "color": "d77757"}, {"name": "clean", "color": "4eba65"}]},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]},
            "mergeable": "CONFLICTING"
          }
        }
      },
      "cache/acme/app/pulls/7.json": {
        "repository": {
          "pullRequest": {
            "isDraft": true,
            "reviewDecision": "CHANGES_REQUESTED",
            "reviewThreads": {"nodes": [{"isResolved": false}, {"isResolved": true}]},
            "labels": {"nodes": [{"name": "reviewing", "color": "d77757"}, {"name": "clean", "color": "4eba65"}]},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]}
          }
        }
      },
      "cache/acme/app/pulls/6.json": {
        "repository": {
          "pullRequest": {
            "isDraft": true,
            "reviewDecision": "CHANGES_REQUESTED",
            "reviewThreads": {"nodes": [{"isResolved": false}, {"isResolved": true}]},
            "labels": {"nodes": [{"name": "reviewing", "color": "d77757"}, {"name": "clean", "color": "4eba65"}]}
          }
        }
      },
      "cache/acme/app/pulls/5.json": {
        "repository": {
          "pullRequest": {
            "isDraft": true,
            "reviewThreads": {"nodes": [{"isResolved": false}, {"isResolved": true}]},
            "labels": {"nodes": [{"name": "reviewing", "color": "d77757"}, {"name": "clean", "color": "4eba65"}]}
          }
        }
      },
      "cache/acme/app/pulls/4.json": {
        "repository": {"pullRequest": {"isDraft": true, "labels": {"nodes": [{"name": "Reviewing"}, {"name": "clean"}]}}}
      },
      "cache/acme/app/pulls/3.json": {"repository": {"pullRequest": {"isDraft": true, "labels": {"nodes": [{"name": "clean"}]}}}},
      "cache/acme/app/pulls/2.json": {"repository": {"pullRequest": {"isDraft": true}}},
      "cache/acme/app/pulls/1.json": {"repository": {"pullRequest": {}}}
    },
    "rows": [
      {
        "section": "pr",
        "label": "#8",
        "state": "conflict",
        "notes": ["failing", "changes", "findings", "reviewing", "clean", "draft"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 8, "title": "", "url": "https://github.com/acme/app/pull/8", "state": "OPEN",
          "is_draft": true, "review_decision": "CHANGES_REQUESTED", "is_conflicting": true, "open_threads": 1,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "failing",
          "is_cancelled": false, "labels": ["reviewing", "clean"], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#7",
        "state": "failing",
        "notes": ["changes", "findings", "reviewing", "clean", "draft"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 7, "title": "", "url": "https://github.com/acme/app/pull/7", "state": "OPEN",
          "is_draft": true, "review_decision": "CHANGES_REQUESTED", "is_conflicting": false, "open_threads": 1,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "failing",
          "is_cancelled": false, "labels": ["reviewing", "clean"], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#6",
        "state": "changes",
        "notes": ["findings", "reviewing", "clean", "draft"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 6, "title": "", "url": "https://github.com/acme/app/pull/6", "state": "OPEN",
          "is_draft": true, "review_decision": "CHANGES_REQUESTED", "is_conflicting": false, "open_threads": 1,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": ["reviewing", "clean"], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#5",
        "state": "findings",
        "notes": ["reviewing", "clean", "draft"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 5, "title": "", "url": "https://github.com/acme/app/pull/5", "state": "OPEN",
          "is_draft": true, "review_decision": null, "is_conflicting": false, "open_threads": 1, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": ["reviewing", "clean"], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#4",
        "state": "reviewing",
        "notes": ["clean", "draft"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 4, "title": "", "url": "https://github.com/acme/app/pull/4", "state": "OPEN",
          "is_draft": true, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": ["Reviewing", "clean"], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#3",
        "state": "clean",
        "notes": ["draft"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 3, "title": "", "url": "https://github.com/acme/app/pull/3", "state": "OPEN",
          "is_draft": true, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": ["clean"], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#2",
        "state": "draft",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 2, "title": "", "url": "https://github.com/acme/app/pull/2", "state": "OPEN",
          "is_draft": true, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
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
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null, "is_cancelled": false,
          "labels": [], "summary_url": null, "is_loading": false
        }
      }
    ]
  }
]
