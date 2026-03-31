# skills/

Place your `*.skill.yaml`, `*.patterns.yaml`, and `*.mutations.yaml` files here.

Run `dynamoui validate --skills-dir ./skills/` to validate all files before deployment.
Run `dynamoui compile-patterns --skills-dir ./skills/` to recompute `skill_hash` headers after any skill file change.
