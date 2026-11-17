export default [
  {
    "name": "the drawer asks for one pull request's threads and each one's first comment",
    "pr": {"repo": "acme/app", "number": 12},
    "query": "query { r0: repository(owner: \"acme\", name: \"app\") { p12: pullRequest(number: 12) { reviewThreads(first: 100) { nodes { isResolved isOutdated path line originalLine\n  comments(first: 1) { nodes { url bodyText author { login } } } } } } } }"
  },
  {
    "name": "the unresolved threads gather under their files, in the order of each file's first thread",
    "pr": {"repo": "acme/app", "number": 12},
    "stdout": {
      "data": {
        "r0": {
          "p12": {
            "reviewThreads": {
              "nodes": [
                {
                  "isResolved": false,
                  "isOutdated": false,
                  "path": "src/a.py",
                  "line": 3,
                  "originalLine": 3,
                  "comments": {
                    "nodes": [
                      {
                        "url": "https://github.com/acme/app/pull/12#discussion_r1",
                        "bodyText": "  Rename this.\nIt reads as a verb.", "author": {"login": "bob"}
                      }
                    ]
                  }
                },
                {
                  "isResolved": true,
                  "isOutdated": false,
                  "path": "src/a.py",
                  "line": 5,
                  "originalLine": 5,
                  "comments": {
                    "nodes": [
                      {
                        "url": "https://github.com/acme/app/pull/12#discussion_r2", "bodyText": "Done",
                        "author": {"login": "bob"}
                      }
                    ]
                  }
                },
                {
                  "isResolved": false,
                  "isOutdated": true,
                  "path": "src/b.py",
                  "line": null,
                  "originalLine": 7,
                  "comments": {
                    "nodes": [
                      {
                        "url": "https://github.com/acme/app/pull/12#discussion_r3", "bodyText": "Outdated note",
                        "author": null
                      }
                    ]
                  }
                },
                {
                  "isResolved": false, "isOutdated": false, "path": "src/a.py", "line": null, "originalLine": null,
                  "comments": {"nodes": []}
                },
                {"isResolved": false, "comments": {"nodes": [{"bodyText": "No path"}]}}
              ]
            }
          }
        }
      }
    },
    "threads": [
      [
        "src/a.py",
        [
          {
            "path": "src/a.py", "line": 3, "is_outdated": false, "author": "bob", "excerpt": "Rename this.",
            "url": "https://github.com/acme/app/pull/12#discussion_r1"
          },
          {"path": "src/a.py", "line": null, "is_outdated": false, "author": null, "excerpt": "", "url": null}
        ]
      ],
      [
        "src/b.py",
        [
          {
            "path": "src/b.py", "line": 7, "is_outdated": true, "author": null, "excerpt": "Outdated note",
            "url": "https://github.com/acme/app/pull/12#discussion_r3"
          }
        ]
      ],
      ["", [{"path": "", "line": null, "is_outdated": false, "author": null, "excerpt": "No path", "url": null}]]
    ]
  },
  {
    "name": "output that is not JSON gives no threads",
    "pr": {"repo": "acme/app", "number": 12},
    "stdout": "Could not resolve to a Repository with the name 'acme/app'.",
    "threads": null
  },
  {
    "name": "errors without data give no threads",
    "pr": {"repo": "acme/app", "number": 12},
    "stdout": {"errors": [{"message": "Could not resolve to a PullRequest with the number of 12."}]},
    "threads": null
  },
  {
    "name": "data without the pull request gives no threads",
    "pr": {"repo": "acme/app", "number": 12},
    "stdout": {"data": {"r0": {"p13": {"reviewThreads": {"nodes": []}}}}},
    "threads": null
  },
  {
    "name": "a pull request without a threads field has none", "pr": {"repo": "acme/app", "number": 12},
    "stdout": {"data": {"r0": {"p12": {}}}}, "threads": []
  }
]
