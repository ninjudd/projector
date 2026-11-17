export default [
  {
    "name": "a head's checks come from the rollup's counts, less the review's own status and the failures a cancel caused or a newer run tests again",
    "session": "s1",
    "files": {
      "sessions/s1/subscriptions": "acme/app#1 fix\nacme/app#2 fix\nacme/app#3 fix\nacme/app#4 fix\nacme/app#5 fix\nacme/app#6 fix\nacme/app#7 fix\nacme/app#8 fix\nacme/app#9 fix\nacme/app#10 fix\n",
      "cache/acme/app/pulls/1.json": {"repository": {"pullRequest": {}}},
      "cache/acme/app/pulls/2.json": {
        "repository": {"pullRequest": {"commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]}}}
      },
      "cache/acme/app/pulls/3.json": {
        "repository": {"pullRequest": {"commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "EXPECTED"}}}]}}}
      },
      "cache/acme/app/pulls/4.json": {
        "repository": {
          "pullRequest": {
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "statusCheckRollup": {
                      "state": "PENDING",
                      "contexts": {
                        "checkRunCountsByState": [{"state": "IN_PROGRESS", "count": 1}, {"state": "SUCCESS", "count": 2}],
                        "nodes": []
                      }
                    }
                  }
                }
              ]
            }
          }
        }
      },
      "cache/acme/app/pulls/5.json": {
        "repository": {
          "pullRequest": {
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "status": {"context": {"state": "FAILURE"}},
                    "statusCheckRollup": {
                      "state": "FAILURE",
                      "contexts": {
                        "checkRunCountsByState": [{"state": "SUCCESS", "count": 2}],
                        "statusContextCountsByState": [{"state": "FAILURE", "count": 1}],
                        "nodes": []
                      }
                    }
                  }
                }
              ]
            }
          }
        }
      },
      "cache/acme/app/pulls/6.json": {
        "repository": {
          "pullRequest": {
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "statusCheckRollup": {
                      "state": "FAILURE",
                      "contexts": {
                        "checkRunCountsByState": [{"state": "FAILURE", "count": 1}, {"state": "SUCCESS", "count": 1}],
                        "nodes": [
                          {
                            "__typename": "CheckRun",
                            "name": "test",
                            "conclusion": "FAILURE",
                            "checkSuite": {
                              "conclusion": "FAILURE",
                              "workflowRun": {"databaseId": 60, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 1}}
                            },
                            "startedAt": "2026-11-16T10:01:00Z",
                            "completedAt": "2026-11-16T10:03:00Z"
                          },
                          {
                            "__typename": "CheckRun",
                            "name": "lint",
                            "conclusion": "SUCCESS",
                            "checkSuite": {
                              "conclusion": "FAILURE",
                              "workflowRun": {"databaseId": 60, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 1}}
                            },
                            "startedAt": "2026-11-16T10:01:00Z",
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
      "cache/acme/app/pulls/7.json": {
        "repository": {
          "pullRequest": {
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "statusCheckRollup": {
                      "state": "SUCCESS",
                      "contexts": {
                        "checkRunCountsByState": [{"state": "CANCELLED", "count": 1}],
                        "nodes": [
                          {
                            "__typename": "CheckRun",
                            "name": "test",
                            "conclusion": "CANCELLED",
                            "checkSuite": {
                              "conclusion": "CANCELLED",
                              "workflowRun": {"databaseId": 70, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 1}}
                            },
                            "startedAt": "2026-11-16T10:01:00Z",
                            "completedAt": "2026-11-16T10:05:00Z"
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
      "cache/acme/app/pulls/8.json": {
        "repository": {
          "pullRequest": {
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "statusCheckRollup": {
                      "state": "FAILURE",
                      "contexts": {
                        "checkRunCountsByState": [{"state": "CANCELLED", "count": 1}, {"state": "FAILURE", "count": 1}],
                        "nodes": [
                          {
                            "__typename": "CheckRun",
                            "name": "build",
                            "conclusion": "CANCELLED",
                            "checkSuite": {
                              "conclusion": "CANCELLED",
                              "workflowRun": {"databaseId": 80, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 1}}
                            },
                            "startedAt": "2026-11-16T10:01:00Z",
                            "completedAt": "2026-11-16T10:05:00Z"
                          },
                          {
                            "__typename": "CheckRun",
                            "name": "gate",
                            "conclusion": "FAILURE",
                            "checkSuite": {
                              "conclusion": "CANCELLED",
                              "workflowRun": {"databaseId": 80, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 1}}
                            },
                            "startedAt": "2026-11-16T10:06:00Z",
                            "completedAt": "2026-11-16T10:06:00Z"
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
      "cache/acme/app/pulls/9.json": {
        "repository": {
          "pullRequest": {
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "statusCheckRollup": {
                      "state": "FAILURE",
                      "contexts": {
                        "checkRunCountsByState": [{"state": "CANCELLED", "count": 1}, {"state": "FAILURE", "count": 1}],
                        "nodes": [
                          {
                            "__typename": "CheckRun",
                            "name": "build",
                            "conclusion": "CANCELLED",
                            "checkSuite": {
                              "conclusion": "CANCELLED",
                              "workflowRun": {"databaseId": 90, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 1}}
                            },
                            "startedAt": "2026-11-16T10:01:00Z",
                            "completedAt": "2026-11-16T10:05:00Z"
                          },
                          {
                            "__typename": "CheckRun",
                            "name": "test",
                            "conclusion": "FAILURE",
                            "checkSuite": {
                              "conclusion": "CANCELLED",
                              "workflowRun": {"databaseId": 90, "createdAt": "2026-11-16T10:00:00Z", "workflow": {"databaseId": 1}}
                            },
                            "startedAt": "2026-11-16T10:01:00Z",
                            "completedAt": "2026-11-16T10:03:00Z"
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
      "cache/acme/app/pulls/10.json": {
        "repository": {
          "pullRequest": {
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "statusCheckRollup": {
                      "state": "FAILURE",
                      "contexts": {
                        "checkRunCountsByState": [
                          {"state": "FAILURE", "count": 1}, {"state": "CANCELLED", "count": 1},
                          {"state": "SUCCESS", "count": 1}
                        ],
                        "nodes": [
                          {
                            "__typename": "CheckRun",
                            "name": "test",
                            "conclusion": "FAILURE",
                            "checkSuite": {
                              "conclusion": "CANCELLED",
                              "workflowRun": {
                                "databaseId": 100, "createdAt": "2026-11-16T10:00:00Z",
                                "workflow": {"databaseId": 1}
                              }
                            },
                            "startedAt": "2026-11-16T10:01:00Z",
                            "completedAt": "2026-11-16T10:03:00Z"
                          },
                          {
                            "__typename": "CheckRun",
                            "name": "build",
                            "conclusion": "CANCELLED",
                            "checkSuite": {
                              "conclusion": "CANCELLED",
                              "workflowRun": {
                                "databaseId": 100, "createdAt": "2026-11-16T10:00:00Z",
                                "workflow": {"databaseId": 1}
                              }
                            },
                            "startedAt": "2026-11-16T10:01:00Z",
                            "completedAt": "2026-11-16T10:05:00Z"
                          },
                          {
                            "__typename": "CheckRun",
                            "name": "test",
                            "conclusion": "SUCCESS",
                            "checkSuite": {
                              "conclusion": "SUCCESS",
                              "workflowRun": {
                                "databaseId": 101, "createdAt": "2026-11-16T10:10:00Z",
                                "workflow": {"databaseId": 1}
                              }
                            },
                            "startedAt": "2026-11-16T10:11:00Z",
                            "completedAt": "2026-11-16T10:14:00Z"
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
      }
    },
    "rows": [
      {
        "section": "pr",
        "label": "#10",
        "state": "ready",
        "notes": ["passing"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 10, "title": "", "url": "https://github.com/acme/app/pull/10",
          "state": "OPEN", "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0,
          "author": null, "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "passing",
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#9",
        "state": "failing",
        "notes": ["cancelled"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 9, "title": "", "url": "https://github.com/acme/app/pull/9", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "failing",
          "is_cancelled": true, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#8",
        "state": "ready",
        "notes": ["cancelled"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 8, "title": "", "url": "https://github.com/acme/app/pull/8", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "cancelled",
          "is_cancelled": true, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#7",
        "state": "ready",
        "notes": ["cancelled"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 7, "title": "", "url": "https://github.com/acme/app/pull/7", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "cancelled",
          "is_cancelled": true, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#6",
        "state": "failing",
        "notes": [],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 6, "title": "", "url": "https://github.com/acme/app/pull/6", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "failing",
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#5",
        "state": "ready",
        "notes": ["passing"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 5, "title": "", "url": "https://github.com/acme/app/pull/5", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "passing",
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
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
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "pending",
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
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
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "pending",
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#2",
        "state": "ready",
        "notes": ["passing"],
        "roles": ["fixing"],
        "pull": {
          "repo": "acme/app", "number": 2, "title": "", "url": "https://github.com/acme/app/pull/2", "state": "OPEN",
          "is_draft": false, "review_decision": null, "is_conflicting": false, "open_threads": 0, "author": null,
          "branch": "", "additions": 0, "deletions": 0, "changed_files": 0, "checks": "passing",
          "is_cancelled": false, "labels": [], "summary_url": null, "is_loading": false
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
