# Pull request

## Summary

<!-- One or two sentences describing what this PR changes and why. -->

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would change existing behavior)
- [ ] Documentation only

## Checklist

- [ ] I edited only `accelerator/`, `.github/`, `docs/`, or root-level files — **not** `generated/`.
- [ ] I ran `python -m unittest discover -s accelerator/tests` and all tests pass.
- [ ] I have not committed any secrets, connection strings, or `.env` files.
- [ ] Any new Azure resources use [Azure Verified Modules (AVM)](https://azure.github.io/Azure-Verified-Modules/).
- [ ] Any new code uses managed identity (no keys, no passwords, no connection strings).
- [ ] If this PR fixes a known issue, I moved the entry from `accelerator/KNOWN_ISSUES.md` to `accelerator/RESOLVED.md`.

## Related issues

<!-- e.g. Fixes #123 -->
