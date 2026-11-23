export default [
  {
    "name": "a field of the wrong type reads as missing, and a file that does not parse as empty",
    "session": "s1",
    "files": {
      "daemon.json": [1, 2],
      "sessions/s1/subscriptions": "acme/app#1 fix\n#2 fix\n",
      "sessions/s1/state.json": "{oops",
      "cache/acme/app/pulls/1.json": {
        "repository": {
          "pullRequest": {
            "title": 5,
            "url": 7,
            "state": "merged",
            "isDraft": "true",
            "reviewDecision": "approved",
            "mergeable": "conflicting",
            "author": "ada",
            "headRefName": null,
            "additions": true,
            "deletions": "3",
            "changedFiles": 2.5,
            "body": 3,
            "labels": {"nodes": [{"name": 5}, null, {"name": "CLEAN"}]},
            "reviewThreads": {"nodes": [{"isResolved": false}, {"isResolved": "false"}, null, {"isResolved": true}]},
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "statusCheckRollup": {
                      "state": 1,
                      "contexts": {
                        "checkRunCountsByState": [
                          {"state": "FAILURE", "count": "1"}, {"state": "FAILURE", "count": true},
                          {"state": "SUCCESS", "count": 2}, null
                        ]
                      }
                    }
                  }
                }
              ]
            }
          }
        }
      }
    },
    "rows": [
      {
        "section": "pr",
        "label": "#1",
        "state": "findings",
        "notes": ["clean", "passing"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 1, "title": "", "url": "https://github.com/acme/app/pull/1", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 1, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 2.5, "checks": "passing",
          "is_cancelled": false, "labels": ["CLEAN"], "summary_url": null, "is_loading": false
        }
      }
    ]
  }
]
