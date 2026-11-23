export default [
  {
    "name": "a row notes its approval, passing checks, a cancelled newest run, and a draft another word takes",
    "session": "s1",
    "files": {
      "sessions/s1/subscriptions": "acme/app#1 fix\nacme/app#2 fix\n",
      "cache/acme/app/pulls/1.json": {
        "repository": {
          "pullRequest": {
            "reviewDecision": "APPROVED",
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "statusCheckRollup": {
                      "state": "SUCCESS",
                      "contexts": {
                        "checkRunCountsByState": [{"state": "SUCCESS", "count": 1}, {"state": "CANCELLED", "count": 1}],
                        "statusContextCountsByState": [],
                        "nodes": [
                          {
                            "__typename": "CheckRun",
                            "name": "test",
                            "conclusion": "SUCCESS",
                            "checkSuite": {
                              "conclusion": "SUCCESS",
                              "workflowRun": {"databaseId": 10, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 1}}
                            }
                          },
                          {
                            "__typename": "CheckRun",
                            "name": "deploy",
                            "conclusion": "CANCELLED",
                            "checkSuite": {
                              "conclusion": "CANCELLED",
                              "workflowRun": {"databaseId": 20, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 2}}
                            },
                            "completedAt": "2026-11-16T10:02:00Z"
                          }
                        ]
                      }
                    }
                  }
                }
              ]
            }
          }
        }
      },
      "cache/acme/app/pulls/2.json": {
        "repository": {"pullRequest": {"isDraft": true, "reviewDecision": "APPROVED", "labels": {"nodes": [{"name": "clean"}]}}}
      }
    },
    "rows": [
      {
        "section": "pr",
        "label": "#2",
        "state": "clean",
        "notes": ["draft", "approved"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 2, "title": "", "url": "https://github.com/acme/app/pull/2", "state": "OPEN",
          "is_draft": true, "review_decision": "APPROVED", "is_conflicting": false, "open_threads": 0,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": null,
          "is_cancelled": false, "labels": ["clean"], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#1",
        "state": "ready",
        "notes": ["approved", "passing", "cancelled"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 1, "title": "", "url": "https://github.com/acme/app/pull/1", "state": "OPEN",
          "is_draft": false, "review_decision": "APPROVED", "is_conflicting": false, "open_threads": 0,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "passing",
          "is_cancelled": true, "labels": [], "summary_url": null, "is_loading": false
        }
      }
    ]
  }
]
