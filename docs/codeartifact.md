# AWS CodeArtifact Publishing

This document covers the private PyPI setup and publishing flow for the
Workflow CLI and its shared models package:

| Package | PyPI Name | Description |
|---------|-----------|-------------|
| CLI | `agents-workflow-cli` | Standalone CLI tool for managing workflows |
| Models | `agents-workflow-models` | Shared Pydantic v2 schemas and enums |

Both are hosted on a private AWS CodeArtifact registry:

```
Domain:     agents-platform
Repository: agents-python-packages
Endpoint:   https://agents-platform-522946686627.d.codeartifact.us-east-1.amazonaws.com/pypi/agents-python-packages/
```

## Defaults

All CodeArtifact configuration lives in the **root `Makefile`** with sensible defaults:

| Variable                | Default                    |
|-------------------------|----------------------------|
| `CODEARTIFACT_DOMAIN`   | `agents-platform`          |
| `CODEARTIFACT_REPO`     | `agents-python-packages`   |
| `CODEARTIFACT_OWNER`    | `522946686627`             |
| `CODEARTIFACT_REGION`   | `us-east-1`               |
| `CODEARTIFACT_UPSTREAM` | `public:pypi`              |

Override per-call (`make codeartifact-setup CODEARTIFACT_DOMAIN=other`) or by
exporting environment variables.

## One-Time Setup

Create the CodeArtifact domain, repository, and upstream PyPI connection
(idempotent -- safe to run more than once):

```bash
# From the repo root
make codeartifact-setup
```

This runs:

1. `aws codeartifact create-domain` (skipped if it already exists)
2. `aws codeartifact create-repository` (skipped if it already exists)
3. `aws codeartifact associate-external-connection` with `public:pypi`

Verify the result:

```bash
make codeartifact-info
```

## Credentials

Get a short-lived auth token and the repository endpoint:

```bash
make codeartifact-login
```

This prints the token and endpoint URL which you can export for `uv` or `pip`.

## Developer Install (uv)

```bash
# Grab credentials
export CODEARTIFACT_AUTH_TOKEN=$(aws codeartifact get-authorization-token \
  --domain agents-platform \
  --domain-owner 522946686627 \
  --region us-east-1 \
  --query authorizationToken --output text)

export CODEARTIFACT_ENDPOINT=$(aws codeartifact get-repository-endpoint \
  --domain agents-platform \
  --domain-owner 522946686627 \
  --repository agents-python-packages \
  --format pypi \
  --region us-east-1 \
  --query repositoryEndpoint --output text)

# Install the CLI as a uv tool
uv tool install agents-workflow-cli \
  --index-url "https://aws:${CODEARTIFACT_AUTH_TOKEN}@${CODEARTIFACT_ENDPOINT#https://}simple/"

# Or add the shared models as a project dependency
uv add agents-workflow-models \
  --index-url "https://aws:${CODEARTIFACT_AUTH_TOKEN}@${CODEARTIFACT_ENDPOINT#https://}simple/"
```

## Publishing (Manual)

From the repo root:

```bash
# Publish the CLI
make codeartifact-publish-cli

# Publish the shared models
make codeartifact-publish-models
```

Each target runs `uv build` then `uv publish` with `UV_PUBLISH_URL`,
`UV_PUBLISH_USERNAME=aws`, and `UV_PUBLISH_PASSWORD` set automatically via
`aws codeartifact get-authorization-token`.

## CI/CD Publishing

Bitbucket Pipelines publishes on version tags (defined in
`bitbucket-pipelines.yml`):

| Tag pattern      | Package published          |
|------------------|----------------------------|
| `cli-vX.Y.Z`    | `agents-workflow-cli`      |
| `models-vX.Y.Z` | `agents-workflow-models`   |

The pipeline:

1. Installs `uv` and the AWS CLI
2. Validates semver + tag/version alignment via `scripts/release/validate_release.py`
3. Fetches CodeArtifact credentials
4. Runs `uv build && uv publish`

### Required Pipeline Variables

Set these in Bitbucket repository settings:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_DEFAULT_REGION` (defaults to `us-east-1`)

The CodeArtifact domain/repo/owner defaults are baked into the pipeline file.
Override them via pipeline variables if needed.

## Release Validation

The release validation script (`scripts/release/validate_release.py`) enforces:

- Semver format (`X.Y.Z`) in `pyproject.toml` and `__init__.py`
- Version sync between `pyproject.toml` and `__init__.py`
- Tag matches the package version (when `--tag` is provided)

```bash
# Validate locally (from workflow-cli/)
python scripts/release/validate_release.py --package cli
python scripts/release/validate_release.py --package models
python scripts/release/validate_release.py --package all

# Validate against a specific tag
python scripts/release/validate_release.py --package cli --tag "cli-v0.1.0"
```

Tests covering these checks live in `tests/test_release_validation.py`.

## How to Release a New Version

1. **Bump the version** in both places — they must match:
   - CLI: `pyproject.toml` (`[project].version`) and `src/cli/__init__.py` (`__version__`)
   - Models: `shared-models/pyproject.toml` and `shared-models/src/workflow_models/__init__.py`

2. **Validate** before pushing:
   ```bash
   uv run pytest tests/test_release_validation.py
   ```

3. **Commit and tag**:
   ```bash
   git commit -am "release: bump CLI to 0.2.0"
   git tag cli-v0.2.0
   git push origin feature/... --tags
   ```

4. **CI publishes automatically** when the tag matches `cli-v*` or `models-v*`.

   Or publish manually from the repo root:
   ```bash
   make codeartifact-publish-cli
   make codeartifact-publish-models
   ```

## Troubleshooting

### "Package already exists" error during publish

CodeArtifact rejects duplicate version uploads. Bump the version in
`pyproject.toml` and `__init__.py`, then publish again.

### "Could not find credentials" error

Ensure your AWS CLI is configured with credentials that have
`codeartifact:GetAuthorizationToken` and `codeartifact:GetRepositoryEndpoint`
permissions. Run `aws sts get-caller-identity` to verify.

### Auth token expired

CodeArtifact tokens expire after 12 hours. Run `make codeartifact-login` again.
