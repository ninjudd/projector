export default [
  {
    "name": "a successful Vercel build links the branch preview that Vercel's newest comment names for a deployed project, and the build itself when no comment names one",
    "session": "s1",
    "files": {
      "daemon.json": {
        "operator": "ada"
      },
      "sessions/s1/subscriptions": "acme/app#1 fix\nacme/app#2 fix\nacme/app#3 fix\nacme/app#4 fix\n",
      "cache/acme/app/pulls/1.json": {
        "repository": {
          "pullRequest": {
            "title": "Prefer the branch",
            "author": {
              "login": "ada"
            },
            "headRefName": "branch-1",
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "deployments": {
                      "nodes": [
                        {
                          "creator": {
                            "login": "vercel"
                          },
                          "latestStatus": {
                            "state": "SUCCESS",
                            "environmentUrl": "https://app-b1d0001-acme.vercel.app"
                          }
                        }
                      ]
                    }
                  }
                }
              ]
            },
            "comments": {
              "nodes": [
                {
                  "author": {
                    "login": "vercel"
                  },
                  "body": "[vc]: #c2lnbmF0dXJl:eyJpc01vbm9yZXBvIjpmYWxzZSwidHlwZSI6ImdpdGh1YiIsInByb2plY3RzIjpbeyJuYW1lIjoiYXBwIiwicHJldmlld1VybCI6ImFwcC1naXQtc3RhbGUtYWNtZS52ZXJjZWwuYXBwIiwibmV4dENvbW1pdFN0YXR1cyI6IkRFUExPWUVEIn1dfQ\nThe latest updates on your projects."
                },
                {
                  "author": {
                    "login": "ada"
                  },
                  "body": "Looks right to me."
                },
                {
                  "author": {
                    "login": "vercel"
                  },
                  "body": "[vc]: #c2lnbmF0dXJl:eyJpc01vbm9yZXBvIjpmYWxzZSwidHlwZSI6ImdpdGh1YiIsInByb2plY3RzIjpbeyJuYW1lIjoiZG9jcyIsInByZXZpZXdVcmwiOiJkb2NzLWdpdC1sb25nLWJyYW5jaC1uYW1lLXRoLTFhMmIzYy1hY21lLnZlcmNlbC5hcHAiLCJuZXh0Q29tbWl0U3RhdHVzIjoiSUdOT1JFRCJ9LHsibmFtZSI6ImFwcCIsInByZXZpZXdVcmwiOiJhcHAtZ2l0LWxvbmctYnJhbmNoLW5hbWUtdGgtMWEyYjNjLWFjbWUudmVyY2VsLmFwcCIsIm5leHRDb21taXRTdGF0dXMiOiJERVBMT1lFRCJ9XX0\nThe latest updates on your projects."
                },
                {
                  "author": {
                    "login": "ada"
                  },
                  "body": "[vc]: #c2lnbmF0dXJl:eyJpc01vbm9yZXBvIjpmYWxzZSwidHlwZSI6ImdpdGh1YiIsInByb2plY3RzIjpbeyJuYW1lIjoiYXBwIiwicHJldmlld1VybCI6ImV2aWwuZXhhbXBsZS5jb20iLCJuZXh0Q29tbWl0U3RhdHVzIjoiREVQTE9ZRUQifV19\nThe latest updates on your projects."
                }
              ]
            }
          }
        }
      },
      "cache/acme/app/pulls/2.json": {
        "repository": {
          "pullRequest": {
            "title": "Wait for a build",
            "author": {
              "login": "ada"
            },
            "headRefName": "branch-2",
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "deployments": {
                      "nodes": [
                        {
                          "creator": {
                            "login": "vercel"
                          },
                          "latestStatus": {
                            "state": "FAILURE",
                            "environmentUrl": "https://app-b2d0001-acme.vercel.app"
                          }
                        }
                      ]
                    }
                  }
                }
              ]
            },
            "comments": {
              "nodes": [
                {
                  "author": {
                    "login": "vercel"
                  },
                  "body": "[vc]: #c2lnbmF0dXJl:eyJpc01vbm9yZXBvIjpmYWxzZSwidHlwZSI6ImdpdGh1YiIsInByb2plY3RzIjpbeyJuYW1lIjoiYXBwIiwicHJldmlld1VybCI6ImFwcC1naXQtd2FpdC1hY21lLnZlcmNlbC5hcHAiLCJuZXh0Q29tbWl0U3RhdHVzIjoiREVQTE9ZRUQifV19\nThe latest updates on your projects."
                }
              ]
            }
          }
        }
      },
      "cache/acme/app/pulls/3.json": {
        "repository": {
          "pullRequest": {
            "title": "Refuse a host that is not one",
            "author": {
              "login": "ada"
            },
            "headRefName": "branch-3",
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "deployments": {
                      "nodes": [
                        {
                          "creator": {
                            "login": "vercel"
                          },
                          "latestStatus": {
                            "state": "SUCCESS",
                            "environmentUrl": "https://app-b3d0001-acme.vercel.app"
                          }
                        }
                      ]
                    }
                  }
                }
              ]
            },
            "comments": {
              "nodes": [
                {
                  "author": {
                    "login": "vercel"
                  },
                  "body": "[vc]: #c2lnbmF0dXJl:eyJpc01vbm9yZXBvIjpmYWxzZSwidHlwZSI6ImdpdGh1YiIsInByb2plY3RzIjpbeyJuYW1lIjoiYXBwIiwicHJldmlld1VybCI6ImFwcC52ZXJjZWwuYXBwL3gpIFtjbGlja10oaHR0cHM6Ly9ldmlsLmV4YW1wbGUuY29tIiwibmV4dENvbW1pdFN0YXR1cyI6IkRFUExPWUVEIn1dfQ\nThe latest updates on your projects."
                },
                {
                  "author": {
                    "login": "vercel"
                  },
                  "body": "[vc]: #c2lnbmF0dXJl:eyJpc01vbm9yZXBvIjpmYWxzZSwidHlwZSI6ImdpdGh1YiIsInByb2plY3RzIjpbeyJuYW1lIjoiYXBwIiwicHJldmlld1VybCI6ImFwcC1naXQtcGF1c2VkLWFjbWUudmVyY2VsLmFwcCIsIm5leHRDb21taXRTdGF0dXMiOiJQRU5ESU5HIn1dfQ\nThe latest updates on your projects."
                }
              ]
            }
          }
        }
      },
      "cache/acme/app/pulls/4.json": {
        "repository": {
          "pullRequest": {
            "title": "Skip what is not Vercel's metadata",
            "author": {
              "login": "ada"
            },
            "headRefName": "branch-4",
            "commits": {
              "nodes": [
                {
                  "commit": {
                    "deployments": {
                      "nodes": [
                        {
                          "creator": {
                            "login": "vercel"
                          },
                          "latestStatus": {
                            "state": "SUCCESS",
                            "environmentUrl": "https://app-b4d0001-acme.vercel.app"
                          }
                        }
                      ]
                    }
                  }
                }
              ]
            },
            "comments": {
              "nodes": [
                {
                  "author": {
                    "login": "vercel"
                  },
                  "body": "[vc]: #c2lnbmF0dXJl:bm90IGpzb24="
                },
                {
                  "author": {
                    "login": "vercel"
                  },
                  "body": "Deployment failed with the following error."
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
        "label": "#4",
        "state": "ready",
        "notes": [],
        "roles": [
          "fixing"
        ],
        "pull": {
          "repo": "acme/app",
          "number": 4,
          "title": "Skip what is not Vercel's metadata",
          "url": "https://github.com/acme/app/pull/4",
          "state": "OPEN",
          "is_draft": false,
          "review_decision": null,
          "is_conflicting": false,
          "open_threads": 0,
          "author": "ada",
          "branch": "branch-4",
          "additions": 0,
          "deletions": 0,
          "changed_files": 0,
          "checks": null,
          "is_cancelled": false,
          "labels": [],
          "summary_url": null,
          "preview_url": "https://app-b4d0001-acme.vercel.app",
          "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#3",
        "state": "ready",
        "notes": [],
        "roles": [
          "fixing"
        ],
        "pull": {
          "repo": "acme/app",
          "number": 3,
          "title": "Refuse a host that is not one",
          "url": "https://github.com/acme/app/pull/3",
          "state": "OPEN",
          "is_draft": false,
          "review_decision": null,
          "is_conflicting": false,
          "open_threads": 0,
          "author": "ada",
          "branch": "branch-3",
          "additions": 0,
          "deletions": 0,
          "changed_files": 0,
          "checks": null,
          "is_cancelled": false,
          "labels": [],
          "summary_url": null,
          "preview_url": "https://app-b3d0001-acme.vercel.app",
          "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#2",
        "state": "ready",
        "notes": [],
        "roles": [
          "fixing"
        ],
        "pull": {
          "repo": "acme/app",
          "number": 2,
          "title": "Wait for a build",
          "url": "https://github.com/acme/app/pull/2",
          "state": "OPEN",
          "is_draft": false,
          "review_decision": null,
          "is_conflicting": false,
          "open_threads": 0,
          "author": "ada",
          "branch": "branch-2",
          "additions": 0,
          "deletions": 0,
          "changed_files": 0,
          "checks": null,
          "is_cancelled": false,
          "labels": [],
          "summary_url": null,
          "preview_url": null,
          "is_loading": false
        }
      },
      {
        "section": "pr",
        "label": "#1",
        "state": "ready",
        "notes": [],
        "roles": [
          "fixing"
        ],
        "pull": {
          "repo": "acme/app",
          "number": 1,
          "title": "Prefer the branch",
          "url": "https://github.com/acme/app/pull/1",
          "state": "OPEN",
          "is_draft": false,
          "review_decision": null,
          "is_conflicting": false,
          "open_threads": 0,
          "author": "ada",
          "branch": "branch-1",
          "additions": 0,
          "deletions": 0,
          "changed_files": 0,
          "checks": null,
          "is_cancelled": false,
          "labels": [],
          "summary_url": null,
          "preview_url": "https://app-git-long-branch-name-th-1a2b3c-acme.vercel.app",
          "is_loading": false
        }
      }
    ]
  }
]
