# claude-crypto

Module: `backend/crypto/`
Role: AES-256-GCM envelope encryption with per-record DEK wrapping. Phase 2 of the multi-tenant rollout. **Single point of import for `cryptography.hazmat` in the entire repo.**

## Files

- `config.py` — `CryptoSettings` (DYNAMO_CRYPTO_*). `master_key` is a `SecretStr` holding a base64-encoded 32-byte KEK.
- `envelope.py` — `encrypt()` / `decrypt()` + `EnvelopePayload` wrapper. `generate_master_key()` helper for operators.

## Envelope layout (canonical — do not change without a migration)

```json
{
  "v": 1,                  // key version (DYNAMO_CRYPTO_KEY_VERSION at encrypt time)
  "alg": "AES-256-GCM",
  "nonce_dek": "<b64>",    // 12 bytes — nonce used to wrap the DEK
  "wrapped_dek": "<b64>",  // AES-GCM(KEK, DEK)
  "nonce_data": "<b64>",   // 12 bytes — nonce used for the payload
  "ciphertext": "<b64>"    // AES-GCM(DEK, plaintext)
}
```

Stored as TEXT in the database. Per-record DEKs mean rotating the master key only needs a re-wrap of each `wrapped_dek`, never a re-encrypt of the (potentially large) `ciphertext`.

## Rules

- **Never import `cryptography.hazmat` outside this module.** Reuse `encrypt()` / `decrypt()` or extend this module — never re-import in consumers.
- **Never log / serialise plaintext.** Callers are responsible for keeping the decrypted return value single-use (pass it straight to the adapter, then drop it).
- **Master key missing → `CryptoNotConfiguredError`.** Route handlers surface this as 503 Service Unavailable, never as 500.
- **Tests** use `generate_master_key()` in a fixture and `pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")` so the suite skips cleanly in environments where the `cryptography` C extension fails to build.

## Rotation (future work)

- `DYNAMO_CRYPTO_KEY_VERSION` is in place but the rewrap CLI isn't. Plan:
  1. Add a `crypto_master_keys` table (id, version, wrapped_by_kms or encrypted_with_root).
  2. `decrypt()` looks up the key for the payload's `v` field.
  3. Rewrap CLI iterates `tenant_db_connections.encrypted_secret`, re-wraps the DEK only.
