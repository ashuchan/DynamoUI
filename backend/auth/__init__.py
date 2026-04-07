"""Authentication & tenant membership (Phase 1).

See ``docs/MULTI_TENANT_PLAN.md`` for the full phased rollout plan. This
package owns the ``tenants``, ``users``, ``tenant_users`` and
``oauth_identities`` tables and the REST routes under ``/api/v1/auth``.

Design invariants (must be preserved by later phases):

* Tenant segregation is enforced at the DAO layer — every query takes
  ``tenant_id`` as an explicit argument. Cross-tenant reads are a hard error.
* Passwords are hashed with stdlib ``hashlib.scrypt`` so we avoid adding a new
  runtime dependency.
* JWTs are built on ``python-jose`` (already in ``pyproject.toml``).
* The Google verifier is injected so tests never hit the live endpoint.
"""
