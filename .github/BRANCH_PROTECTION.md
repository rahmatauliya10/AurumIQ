# GitHub Branch Protection & Governance Specification

To ensure rigorous CI gate compliance before merging any future changes into `main`, configure branch protection for `main` on GitHub:

## Required Status Checks
- `Regression & Compliance Suite` (defined in `.github/workflows/ci.yml`)

## Enforcement Policy
- **Require pull request before merging**
- **Require status checks to pass before merging**
  - Status Check: `test` (`Regression & Compliance Suite`)
- **Require branches to be up to date before merging**
- **Do not allow bypassing the above settings** (Enforce for Administrators)
- **Do not allow force pushes (`--force`)**
- **Do not allow deletions of `main`**

## GitHub CLI Automation Script

Run the following command with GitHub CLI authenticated:

```bash
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  /repos/rahmatauliya10/AurumIQ/branches/main/protection \
  --input - <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Regression & Compliance Suite"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```
