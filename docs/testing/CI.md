---
audience: developer
type: how-to
covers: CI live tests — Forgejo service container, admin provisioning, test execution, cleanup
---

# CI Live Tests

The Forgejo CI pipeline (``.gitea/workflows/ci.yml``) includes an
optional ``live-test`` job that runs the live suite against a throwaway
Forgejo service container:

1. **Forgejo service**: Spawned as a Docker service container
   (``codeberg.org/forgejo/forgejo:16``) with SQLite backend,
   push-to-create enabled, and ``INSTALL_LOCK`` set.
2. **Admin provisioning**: A one-shot admin user and access token are
   created via ``gitea admin user create --access-token`` inside the
   service container.
3. **Test execution**: The CI image runs ``pytest tests/live/ -m live``
   with ``--network host``, ``GITEA_LIVE_RUN_ID=ci-<run-number>`` for
   namespace isolation, and a 300s timeout.
4. **Cleanup**: The ``OwnershipLedger`` and ``World.cleanup()`` delete
   run-owned repos, teams, orgs, and users in reverse dependency order
   within the worker fixture teardown.
5. **Artifacts**: Forgejo container logs are collected on both success
   and failure (``if: always()``).

The job uses ``continue-on-error: true`` — live test failures do not
block merges.  External service startup is inherently flaky and the
live suite primarily serves as a development-time regression catcher.

```bash
# CI target (same as what the live-test job runs):
make test-live
```
