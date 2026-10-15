# First-release publishing guide

This guide prepares a personal repository at `AlvaroPavon/whynotup`. The commands below are publishing instructions, not a record of completed remote actions. Local tests do not establish that GitHub Actions passed. No PyPI release is available.

## Repository metadata

- Owner: `AlvaroPavon`
- Name: `whynotup`
- Visibility: public
- Description: `Find common Docker Compose startup problems with evidence, source locations, and reviewable fixes.`
- Topics: `docker`, `docker-compose`, `python`, `cli`, `diagnostics`, `developer-tools`, `devops`
- License: MIT
- First version: `0.1.0` / tag `v0.1.0`
- Default branch: `main`

## 1. Review and validate locally

Run from the repository root in an activated Python 3.10+ virtual environment:

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
whynotup --version
```

Run these separately because the first two commands intentionally return `1`:

```bash
whynotup demo
whynotup doctor examples/broken -f compose.yaml
whynotup doctor examples/fixed -f compose.yaml
```

Expected results with no relevant shell overrides: demo has four findings; the broken fixture has three; the fixed fixture has none and returns `0`. These are static checks. Environment variables in the invoking shell take precedence over fixture dotenv values.

Review `README.md`, `README.es.md`, `CHANGELOG.md`, `LICENSE`, `SECURITY.md`, examples, workflows, and any generated artifacts. Check that the staged content excludes virtual environments, local credentials, and diagnostic reports. Verify that no badge or release text claims a remote check has run before it actually has.

```bash
git status --short
git diff --cached --stat
git diff --cached --check
```

Create a reviewed local commit if one does not exist yet. Confirm the branch and latest commit:

```bash
git branch --show-current
git log -1 --oneline
```

Build and inspect the release files locally before publishing:

```bash
python -m pip install 'setuptools>=77' wheel
python scripts/build_release.py
```

The script creates a wheel, a source distribution, a complete source ZIP, and `dist/SHA256SUMS`. It verifies that package demo files and source examples are included. It uploads nothing. For a live Docker check, follow [DOCKER_TESTS.md](DOCKER_TESTS.md); only an opted-in run with a reachable local daemon exercises actual containers.

## 2. Create the personal repository and push

This section creates a public repository and sends the reviewed files to GitHub. Run it when ready to publish, using GitHub CLI authenticated as `AlvaroPavon`:

```bash
gh auth status
gh api user --jq .login
git remote -v
```

If authentication is missing or invalid, run `gh auth login --hostname github.com` and complete its interactive flow before continuing. Confirm that the account is correct. Check whether `AlvaroPavon/whynotup` already exists; if it does, inspect it rather than creating or replacing anything. The following command assumes the repository does not exist and no `origin` remote is configured:

```bash
git branch -M main
gh repo create AlvaroPavon/whynotup --public --source=. --remote=origin --description "Find common Docker Compose startup problems with evidence, source locations, and reviewable fixes."
git push -u origin main
gh repo edit AlvaroPavon/whynotup --add-topic docker,docker-compose,python,cli,diagnostics,developer-tools,devops
```

If `origin` already exists, verify that it points to the intended personal repository before pushing. Do not overwrite an unrelated remote. The issue template's security-policy link becomes usable after these files are published at that location.

In repository settings, enable private vulnerability reporting if available. Review the public files and rendering, then pin the repository on the maintainer's profile if desired.

## 3. Wait for remote checks

Inspect the first run on the pushed commit:

```bash
gh run list --repo AlvaroPavon/whynotup --branch main
gh run watch --repo AlvaroPavon/whynotup --exit-status
```

Select the run for the commit just pushed. Review every required job, including any real-Docker smoke test present in the workflow. If a job fails, fix the cause and wait for the new commit's run. Record the successful run URL before saying that CI passed. A local unit-test run and a remote runtime smoke test are different evidence.

## 4. Prepare the first GitHub release

Review `CHANGELOG.md` against the shipped code. These are the first-release facts to retain:

- Python CLI with `doctor` and an offline `demo`.
- Single-file Compose diagnostics for missing variables and environment files, missing bind sources, probable connection-host/port mistakes, and missing dependencies.
- Optional read-only Docker state and local TCP port diagnostics.
- Findings with source evidence, confidence, and review-only suggestions.
- Text, JSON schema `1.0`, and Markdown reports with best-effort redaction.
- Broken/fixed fixtures and English/Spanish README documentation.
- Explicit coverage limits and exit codes; no automatic configuration edits.

Include additional features only after they exist and have been validated. In particular, do not advertise a remote GitHub Action reference before the repository/ref exists or a PyPI installation before a package is actually published. A composite Action checked out locally can be tested with `uses: ./` independently of a marketplace release.

After the target commit's required remote checks pass, create a draft release:

```bash
gh release create v0.1.0 --repo AlvaroPavon/whynotup --target main --title "WhyNotUp 0.1.0" --notes-file CHANGELOG.md --draft
gh release view v0.1.0 --repo AlvaroPavon/whynotup --web
```

The command creates the tag from the current `main` target if it does not exist; check that `main` still points to the validated commit first. Review the draft title, notes, target, and any attachments. Remove historical or unreleased changelog sections from the draft body if they are present. Publish the reviewed draft when ready:

```bash
gh release edit v0.1.0 --repo AlvaroPavon/whynotup --draft=false
```

A GitHub release does not publish to PyPI. Keep source-install instructions until a separate package release is complete.

## 5. Share a reproducible demonstration

Use the real demo output or a recording of the broken/fixed examples. State that the fixtures are static training configurations and that the tool provides evidence and suggestions. Link to the exact example and explain which checks ran.

Suggested announcement text to adapt after publication:

> I built WhyNotUp to find common Docker Compose startup mistakes and explain them with source evidence. It includes an offline demo, broken/fixed examples, and optional read-only runtime checks. The first release is intentionally limited to one Compose file per run. Try it and share a minimal case where a diagnostic helps or gets something wrong.

Use the actual repository and release URLs after they exist. Measure repository traffic, stars, reproducible reports, and new contributors over time; do not promise rankings, followers, or adoption.
