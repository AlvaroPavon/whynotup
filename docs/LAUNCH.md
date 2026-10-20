# Publishing the 0.2 update

`AlvaroPavon/whynotup` already exists. This guide describes updating that repository and optionally preparing a `0.2.0` release; its commands are instructions, not evidence that a push, tag, release, or CI run has completed. Keep source-install instructions until a separate PyPI publication is completed.

## Repository and attribution

- Repository: [AlvaroPavon/whynotup](https://github.com/AlvaroPavon/whynotup), public, default branch `main`.
- Description: `Local Docker Compose diagnostics and improvement rules, without AI or API quotas.`
- Topics: `docker`, `docker-compose`, `python`, `cli`, `diagnostics`, `developer-tools`, `devops`.
- Current distribution: SPDX `zlib-acknowledgement`, with Álvaro Pavón (`AlvaroPavon`) identified in `LICENSE` and `NOTICE`.
- Required product-documentation acknowledgement: `Portions Copyright (c) 2026 Álvaro Pavón (AlvaroPavon)`.

Read [LICENSING.md](LICENSING.md) before describing the license change. Renamed forks are permitted, original authorship must be represented accurately, and altered source versions must be marked. Code previously published under MIT through commit `7944542` retains its earlier MIT grant; preserve `licenses/MIT-0.1.0.txt` in distributions. The current license is listed by SPDX, not marked OSI-approved.

## 1. Validate the update locally

Run from the repository root in an activated Python 3.10+ virtual environment:

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
whynotup --version
```

Run the following separately. The demo and broken fixture intentionally return `1`; the fixed static fixture returns `0`:

```bash
whynotup demo
whynotup doctor examples/broken -f compose.yaml
whynotup doctor examples/fixed -f compose.yaml
whynotup review examples/fixed -f compose.yaml --target production
```

With no relevant shell overrides, the demo has four findings, the broken fixture has three, and the fixed static fixture has none. Review adds warnings about configuration choices, so its output need not be empty. Shell variables take precedence over fixture dotenv values.

Where Docker Compose v2 is installed, validate the selected configuration without requiring a daemon:

```bash
whynotup doctor examples/fixed -f compose.yaml --validate
```

For real container state, log reads, and port checks, follow [DOCKER_TESTS.md](DOCKER_TESTS.md). `doctor --all` is an explicit request for native validation, runtime, bounded logs, and recommendations; it requires a reachable daemon and may read existing application logs. The tool does not start services or apply suggestions.

Review source, documentation, fixtures, workflows, and the license notices. Confirm that virtual environments, local credentials, and diagnostic reports are excluded:

```bash
git status --short
git diff --check
git diff --cached --check
```

Build and inspect the distributions:

```bash
python -m pip install 'setuptools>=77' wheel
python scripts/build_release.py
```

The script creates a wheel, source distribution, complete source ZIP, and `dist/SHA256SUMS`. It verifies the demo and source fixtures and checks that `LICENSE`, `NOTICE`, and the historical MIT notice are packaged. It uploads nothing. Inspect the installed package outside the checkout and ensure version `0.2.0`, the offline demo, and `review` work from the built wheel.

## 2. Push to the existing repository

Use an authenticated GitHub account with write access, including permission to update workflow files. Verify the account, remote, and branch before sending changes:

```bash
gh auth status
gh api user --jq .login
git remote -v
git branch --show-current
git log -1 --oneline
```

The intended `origin` is `https://github.com/AlvaroPavon/whynotup.git`. Do not create another repository or replace an unrelated remote. Once the reviewed update is committed on the intended branch:

```bash
git push origin main
```

Repository description/topics and profile pinning can be maintained separately from the code update. They do not establish that the update has passed CI.

## 3. Verify the exact pushed commit

```bash
gh run list --repo AlvaroPavon/whynotup --branch main
gh run watch --repo AlvaroPavon/whynotup --exit-status
```

Choose runs for the commit just pushed. Check both **CI** and **Docker integration**, including Ubuntu/Windows tests, package installation, the composite Action, native Compose validation, and real-container log/state scenarios. A successful older run does not verify new code. Fix failures and inspect the replacement commit's runs before claiming the update passes. Record the exact commit SHA and successful run URLs.

## 4. Optional GitHub release

No `v0.2.0` tag or GitHub release is assumed to exist. Review release notes against the validated code. Describe:

- Offline startup diagnosis and target-specific configuration recommendations.
- Optional daemon-independent native Compose validation.
- Optional read-only runtime checks and bounded log-pattern clues; no raw logs in reports.
- Deterministic operation without an AI/API service, key, account, or usage quota.
- Single-file scope, review-only suggestions, coverage status, and the attribution license transition.

After checking that `main` still identifies the validated commit, an optional draft can be prepared:

```bash
gh release create v0.2.0 --repo AlvaroPavon/whynotup --target main --title "WhyNotUp 0.2.0" --notes-file CHANGELOG.md --draft
gh release view v0.2.0 --repo AlvaroPavon/whynotup --web
```

If the tag already exists, inspect it rather than replacing it. Creating the draft can create a tag; inspect its target, notes, and any attachments. Trim historical changelog sections from the draft body before publishing it. A GitHub release does not publish to PyPI.

Remote GitHub Action examples may use the existing `main` branch while no release tag exists; recommend a reviewed full commit SHA for reproducible execution.

## 5. Share a reproducible demonstration

Use real demo output or a recording of the broken/fixed fixtures and `review`. Distinguish static findings, recommendations, native validation, and historical log clues. Link the exact example and state which optional checks ran.

Suggested announcement after the relevant code and checks are public:

> I built WhyNotUp to diagnose common Docker Compose mistakes and suggest configuration improvements using local rules. It includes an offline demo, production/development review policies, optional Compose validation, and bounded runtime/log checks. No AI API or usage subscription is needed. Try a minimal example and report a useful missing check or false positive.

Use release links only after the release exists. Track traffic, stars, reproducible reports, and contributions without promising rankings or follower growth.
