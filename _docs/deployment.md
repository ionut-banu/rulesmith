# Deployment

Rulesmith runs on [Fly.io](https://fly.io) in two independent public
environments built from the same container image. This page is enough to
recreate both from scratch.

Every push to `main` that passes CI is built once and deployed to dev
automatically. Prod changes only when a human runs the `Promote prod` workflow
with a tag, which is also how prod is rolled back. See
[*Continuous deployment*](#continuous-deployment).

## Overview

| | Dev | Prod |
|---|---|---|
| Fly app | `rulesmith-dev` | `rulesmith-prod` |
| URL | https://rulesmith-dev.fly.dev | https://rulesmith-prod.fly.dev |
| Fly config | `deploy/fly.dev.toml` | `deploy/fly.prod.toml` |
| Region | `fra` (Frankfurt) | `fra` (Frankfurt) |
| Database | Neon project `rulesmith-dev` | Neon project `rulesmith-prod` |
| Upload volume | `rulesmith_data` (1 GB) at `/data` | `rulesmith_data` (1 GB) at `/data` |
| `RULESMITH_ENV` | `dev` | `prod` |

- **One machine and one volume per app.** Uploaded files are kept on a
  machine-local Fly volume (`RULESMITH_UPLOAD_DIR=/data/uploads`). A second
  machine would have its own empty volume and could not show the failing rows
  of a run whose file landed on the first one. Running more than one machine
  needs uploads in object storage first (not done).
- **The machine stops when idle** (`auto_stop_machines = "stop"`,
  `min_machines_running = 0`) and the next request starts it again. Expect a
  cold start of a few seconds after a quiet period, a bit more if Neon has
  also suspended its compute. This is accepted. The `/health` check has a 30 s
  grace period so a cold start does not fail it.
- **The VM has 512 MB of memory** (shared CPU) in both apps. 256 MB is not
  enough for pandas and pyarrow on a 20 MB upload.
- **Tables are created at startup**, so a healthy `/health` also proves the
  app reached its database. There are no migrations yet.
- **`/health` reports the running version**:
  `{"status": "ok", "version": "<RULESMITH_VERSION>"}`, or `"version": "dev"`
  when the variable is unset or empty. Each deploy sets `RULESMITH_VERSION`
  to the image tag.
- **Database connections are pre-pinged** (`pool_pre_ping=True` in
  `src/rulesmith/db.py`), so connections Neon closes when it suspends an idle
  compute are replaced instead of failing the next request.
- **The image starts as root only to prepare the volume.** Fly mounts volumes
  owned by root. `deploy/entrypoint.sh` creates `RULESMITH_UPLOAD_DIR`, hands
  it to the `app` user, then runs uvicorn as `app` (uid 10001) with `setpriv`.
  The app process never runs as root. (`docker exec` and `fly ssh console`
  open a root shell, as they would for any image without a `USER`.)

## Prerequisites (human)

- A Fly.io account and [flyctl](https://fly.io/docs/flyctl/install/), logged
  in (`fly auth login`; `fly auth whoami` prints your email). Both apps must be
  in the **same Fly organization**, because prod deploys the image that was
  pushed to dev's registry repository. `fly orgs list` shows the slugs; the
  default is `personal`.
- A [Neon](https://neon.tech) account with two projects, `rulesmith-dev` and
  `rulesmith-prod`, both in **AWS `eu-central-1` (Frankfurt)**, next to the
  Fly region. Postgres 16 matches the local Compose stack.
- `fly platform regions` lists `fra`. If it does not, stop: do not pick
  another region, because the databases are in Frankfurt.

## Connection string

Each app gets its own Neon connection string as the Fly secret
`RULESMITH_DATABASE_URL`.

1. In the Neon console, open the project, click **Connect**, and turn
   **Connection pooling off**. Use this **direct** string: its host has no
   `-pooler` in it.
2. Change the scheme from `postgresql://` to `postgresql+psycopg://` (the app
   uses the psycopg 3 driver).
3. Keep `sslmode=require` and every other query parameter Neon includes, such
   as `channel_binding=require`.

The result looks like this (placeholders in angle brackets):

```
postgresql+psycopg://<user>:<password>@<endpoint>.eu-central-1.aws.neon.tech/<database>?sslmode=require&channel_binding=require
```

In the shell, put it in **single quotes**, because it contains `?` and `&`.
Never commit it, paste it into an issue or a chat, or put it in a Fly config
file. Startup errors print it with the password hidden.

## One-time setup

Run from the repo root, in this order. Create both apps first, so a taken name
is found before anything else exists.

### 1. Create the apps

```bash
fly apps create rulesmith-dev --org <ORG_SLUG>
fly apps create rulesmith-prod --org <ORG_SLUG>
```

If a name is taken (`Name has already been taken`), pick a suffix and use
`rulesmith-<suffix>-dev` and `rulesmith-<suffix>-prod` for both apps, so the
pair stays consistent. Delete the app that was created under the old name
(`fly apps destroy rulesmith-dev`), then update every place that names the
apps: `app = ...` in both files in `deploy/`, the registry path
`registry.fly.io/rulesmith-<suffix>-dev:<tag>`, the URLs in `README.md`, and
this page.

### 2. Create one volume per app

`--yes` skips the prompt that warns about running a single volume.

```bash
fly volumes create rulesmith_data --app rulesmith-dev --region fra --size 1 --yes
fly volumes create rulesmith_data --app rulesmith-prod --region fra --size 1 --yes
```

### 3. Set the database secret

`--stage` stores the secret without deploying; the first deploy picks it up.

```bash
fly secrets set --app rulesmith-dev --stage RULESMITH_DATABASE_URL='<NEON_DEV_DIRECT_URL>'
fly secrets set --app rulesmith-prod --stage RULESMITH_DATABASE_URL='<NEON_PROD_DIRECT_URL>'
```

To keep the string out of shell history, read it without echo and pipe it to
`fly secrets import` (`printf` is a shell builtin, so the value does not show
in the process list either):

```bash
read -rs NEON_URL    # paste the dev string, press Enter
printf 'RULESMITH_DATABASE_URL=%s\n' "$NEON_URL" | fly secrets import --app rulesmith-dev --stage
read -rs NEON_URL    # paste the prod string, press Enter
printf 'RULESMITH_DATABASE_URL=%s\n' "$NEON_URL" | fly secrets import --app rulesmith-prod --stage
unset NEON_URL
```

## Continuous deployment

Two workflows run after `CI` (`.github/workflows/ci.yml`, which is unchanged
and uses no secrets):

```
PR ──> CI (unit, integration)
 │
 └─ merge ──> CI on main ──(passed, on a push)──> Deploy dev
                                                   ├─ check-head  is the commit still the head of main?
                                                   ├─ build       build once, push registry.fly.io/rulesmith-dev:<tag>
                                                   └─ deploy-dev  deploy <tag> to rulesmith-dev, check /health reports <tag>

a human runs Promote prod with <tag> ──> check the tag format
                                         ├─ check registry.fly.io/rulesmith-dev:<tag> exists
                                         ├─ record the version prod reports now ("previous")
                                         ├─ deploy <tag> to rulesmith-prod
                                         └─ check /health reports <tag>
```

| Workflow | File | Trigger | Environment (token) |
|---|---|---|---|
| `Deploy dev` | [`.github/workflows/deploy-dev.yml`](../.github/workflows/deploy-dev.yml) | `workflow_run` of `CI` on `main`; runs only when CI passed on a push, never for a PR | `dev` (`rulesmith-dev` deploy token) |
| `Promote prod` | [`.github/workflows/promote-prod.yml`](../.github/workflows/promote-prod.yml) | `workflow_dispatch` on `main`, required input `tag` | `production` (org token for `personal`, required reviewer) |

- **Tags** have the format `YYYYMMDD-HHMMSS-<sha7>`: the UTC build time, then
  the first 7 characters of the commit. The tag is the image label and the
  app's `RULESMITH_VERSION`.
- **The image is built once**, by the `build` job of `Deploy dev`, from the
  exact commit CI tested. `deploy-dev` and `Promote prod` only deploy
  `registry.fly.io/rulesmith-dev:<tag>`, so prod never rebuilds. Re-running a
  failed `deploy-dev` job ("Re-run failed jobs") deploys the same tag again
  without rebuilding.
- **Dev never goes backwards.** If `main` has moved on by the time a
  `Deploy dev` run starts, the run succeeds with the notice
  `skipped: main is now at <sha7>` and deploys nothing. The newer commit's own
  run deploys it. Runs of each workflow are serialized and never cancelled
  half way (`concurrency` with `cancel-in-progress: false`).
- **Rollback rolls back the image only.** `Promote prod` uses
  `deploy/fly.prod.toml` from `main` at the time of the run, so a rollback
  changes the image and `RULESMITH_VERSION`, not the Fly config.
- **Tokens.** Each job that talks to Fly runs in a GitHub environment, `dev`
  or `production`, and reads that environment's `FLY_API_TOKEN`. There is no
  repository-level token, so `CI` sees neither token, and the dev jobs cannot
  touch prod. Dev uses a deploy token scoped to `rulesmith-dev`. Prod needs
  an org token, because a token scoped to `rulesmith-prod` is refused an
  image owned by `rulesmith-dev` (see H3). Setup is H1 to H4 under
  *Needs a human*.

### The version check

Both workflows end with
`python3 scripts/wait_for_version.py --url https://rulesmith-<env>.fly.dev/health --expect <tag>`.
It requests `/health` every 5 s for up to 180 s (`--timeout`), retrying
through connection errors, non-200 responses, bodies that are not JSON and a
different version (the old release still answering, or a cold start). It
passes on the first HTTP 200 with `status` `ok` and `version` equal to the
tag, and otherwise fails with one line saying what it saw last. It uses only
the standard library, so you can run it by hand the same way.

One unauthenticated HTTPS request proves three things: the new release
answers, it reached its database (tables are created at startup), and it runs
the expected version. It needs no `fly ssh` and no token.

**A failed check does not roll back automatically.** The job fails and the
new release stays in place. For prod, the job summary names the version that
was running before. Roll back to it as described below.

### Finding tags

```bash
curl -s https://rulesmith-dev.fly.dev/health      # dev's current tag ("version")
curl -s https://rulesmith-prod.fly.dev/health     # prod's current tag
fly releases --app rulesmith-dev --image          # earlier dev tags (image column)
fly releases --app rulesmith-prod --image         # earlier prod tags
gh run list --workflow "Deploy dev"               # each run's summary shows its tag
```

Each `Deploy dev` run summary shows the tag, the image, the dev URL and
`To promote: run Promote prod with tag <TAG>`. Each `Promote prod` run
summary shows the previous version, the new tag and whether the check passed,
and, when the previous version is known,
`To roll back: run Promote prod with tag <previous>`.

### Promote or roll back prod

Promotion and rollback are the same action: a rollback promotes an older tag.

1. Pick the tag: dev's current tag to promote
   (`curl -s https://rulesmith-dev.fly.dev/health`), or the previous version
   from the last `Promote prod` summary to roll back.
2. Run `Promote prod` on `main` with that tag, in the Actions UI
   (**Actions** → **Promote prod** → **Run workflow**, branch `main`, field
   `tag`) or with `gh workflow run "Promote prod" --ref main -f tag=<TAG>`.
3. Approve the `production` deployment on the run page (if a reviewer is
   set), then check that the run is green and that
   `curl -s https://rulesmith-prod.fly.dev/health` shows the tag.

Promoting the tag prod already runs is allowed: it creates a new release and
the check passes.

**Images built before CD cannot be promoted with the workflow**, including
`20261106-201341-2452847` (the image from #21). They do not report a
version, so the deploy would succeed and the check would then fail, leaving
that release running. For those images, a human uses the manual commands in
*Deploy (manual fallback)*.

## Deploy (manual fallback)

Use these commands only when Actions is unavailable, or for an image that
`Promote prod` cannot check (built before CD). They were also used for the
first deploy.

The image is built **once**, for dev, and prod deploys that same image. Prod
never rebuilds. Each image gets a tag in the format
`YYYYMMDD-HHMMSS-<shortsha>` (the same format `Deploy dev` uses), which is
also the app's `RULESMITH_VERSION`. Deploy from a clean, committed checkout so
the tag's sha matches what was built.

```bash
TAG="$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short=7 HEAD)"
echo "$TAG"

# Dev: build from the repo root Dockerfile and push registry.fly.io/rulesmith-dev:$TAG
fly deploy . --config deploy/fly.dev.toml --dockerfile Dockerfile \
  --ha=false --image-label "$TAG" --env RULESMITH_VERSION="$TAG"

# Prod: deploy the image dev just pushed
fly deploy --config deploy/fly.prod.toml \
  --image "registry.fly.io/rulesmith-dev:$TAG" \
  --ha=false --env RULESMITH_VERSION="$TAG"
```

`--ha=false` keeps each app at one machine (see *Overview*). Run the prod
command in the same shell as the dev one, or set `TAG` to the dev tag first.

If the prod deploy cannot pull `registry.fly.io/rulesmith-dev:<tag>`
(`Not authorized to access this imageref` or "not found"), check that you
are logged in with an account or token that can read `rulesmith-dev`, and
that both apps are in the same organization. Report it on the issue rather
than rebuilding for prod.

## Must never be set on Fly

- `OTEL_EXPORTER_OTLP_ENDPOINT` or any other `OTEL_*` variable. Telemetry
  (`_docs/observability.md`) is opt-in and there is no hosted collector, so
  setting it only produces exporter warnings.
- `RULESMITH_FAIL_UPLOADS`, the test-only failure switch. The app refuses to
  start with it when `RULESMITH_ENV` is `prod`, but nothing stops it on dev,
  so the rule is: never on Fly.

Neither belongs in `deploy/*.toml` (`grep -E 'DATABASE_URL|OTEL_|FAIL_UPLOADS' deploy/*.toml`
prints nothing) or in secrets. Check the secrets:

```bash
fly secrets list --app rulesmith-dev    # only RULESMITH_DATABASE_URL
fly secrets list --app rulesmith-prod   # only RULESMITH_DATABASE_URL
```

## Verify

After a deploy. Machines stop when idle, so the `curl` checks run first
(they also wake the machine before `fly ssh console`).

```bash
# Health and version over HTTPS: {"status":"ok","version":"<tag>"} and HTTP 200
# (also proves the database is reachable)
curl -s -w '\n%{http_code}\n' https://rulesmith-dev.fly.dev/health
curl -s -w '\n%{http_code}\n' https://rulesmith-prod.fly.dev/health

# The same check the workflows run: waits until the app reports <TAG>
python3 scripts/wait_for_version.py --url https://rulesmith-prod.fly.dev/health --expect <TAG>

# Plain HTTP redirects to https://
curl -sI http://rulesmith-dev.fly.dev/health | grep -i -E '^(HTTP|location)'
curl -sI http://rulesmith-prod.fly.dev/health | grep -i -E '^(HTTP|location)'

# Isolation: a dataset created on dev is not on prod
NAME="isolation-check-$(date +%s)"
curl -s -o /dev/null -w '%{http_code}\n' -X POST -d "name=$NAME" https://rulesmith-dev.fly.dev/datasets
curl -s https://rulesmith-dev.fly.dev/datasets | grep -c "$NAME"    # 1 or more
curl -s https://rulesmith-prod.fly.dev/datasets | grep -c "$NAME"   # 0

# Separate databases: one secret per app, different digests
fly secrets list --app rulesmith-dev
fly secrets list --app rulesmith-prod

# One machine and one attached rulesmith_data volume per app, all in fra
fly machines list --app rulesmith-dev
fly volumes list --app rulesmith-dev
fly machines list --app rulesmith-prod
fly volumes list --app rulesmith-prod

# Environment: dev and prod (the version is on /health, above); same image on both
fly ssh console --app rulesmith-dev -C "printenv RULESMITH_ENV"
fly ssh console --app rulesmith-prod -C "printenv RULESMITH_ENV"
fly releases --app rulesmith-dev --image
fly releases --app rulesmith-prod --image

# No telemetry or failure-switch variables (both commands print nothing)
fly ssh console --app rulesmith-dev -C "printenv" | grep -E '^(OTEL_|RULESMITH_FAIL_UPLOADS)'
fly ssh console --app rulesmith-prod -C "printenv" | grep -E '^(OTEL_|RULESMITH_FAIL_UPLOADS)'
```

Persistence on dev (the file survives on the volume, the run in Neon):

1. Open https://rulesmith-dev.fly.dev/datasets, create a dataset, add the rule
   `age >= 30`, and upload `tests/fixtures/loader/valid.csv`. The run shows
   the rule failing for Bob's row.
2. `fly apps restart rulesmith-dev`
3. Reload the dataset page: the run is still in its history, and its
   failing-rows view still shows Bob's row.

## Needs a human

Agents prepare files and docs and run only read-only commands
(`fly config validate --strict`, `fly platform regions`, `fly ... list`,
`fly releases`, `fly tokens list`, `gh run list` / `gh run view`, `curl`). A
human does everything that creates or changes something:

- Fly account, flyctl login, organizations and API tokens
- Neon account, both Neon projects and their connection strings
- `fly apps create`, `fly apps destroy`, `fly volumes create`,
  `fly secrets set` / `fly secrets import`, `fly deploy`, `fly apps restart`
- Any other `fly` command that changes state
- GitHub environments and secrets, merges and pushes to `main`, and running
  or approving `Promote prod`

### CD setup (H1 to H4)

Run these from a shell where `fly auth whoami` and `gh auth status` work for
the repo owner (`ionut-banu`). Each token goes straight from `fly` into `gh`
through a pipe, so it never appears on screen or in shell history.

**H1. Create the two GitHub environments, each limited to `main`.**
`production` also gets the owner as required reviewer. This is optional but
recommended: it is free on a public repo, and approving your own run is
allowed while `prevent_self_review` is false. To skip the reviewer, drop the
`reviewers` line.

```bash
gh api -X PUT repos/ionut-banu/rulesmith/environments/dev --input - <<'EOF'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api -X POST repos/ionut-banu/rulesmith/environments/dev/deployment-branch-policies -f name=main -f type=branch

gh api -X PUT repos/ionut-banu/rulesmith/environments/production --input - <<'EOF'
{"reviewers": [{"type": "User", "id": 17333219}],
 "prevent_self_review": false,
 "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api -X POST repos/ionut-banu/rulesmith/environments/production/deployment-branch-policies -f name=main -f type=branch
```

**H2. Create a dev deploy token (`rulesmith-dev` only, one year) and store
it in the `dev` environment.**

```bash
fly tokens create deploy --app rulesmith-dev --name github-cd-dev --expiry 8760h \
  | tr -d '\n' | gh secret set FLY_API_TOKEN --env dev --repo ionut-banu/rulesmith
```

**H3. Create an org token for `personal` (one year) and store it in the
`production` environment.**

```bash
fly tokens create org --org personal --name github-cd-prod-org --expiry 8760h \
  | tr -d '\n' | gh secret set FLY_API_TOKEN --env production --repo ionut-banu/rulesmith
```

A deploy token scoped to `rulesmith-prod` is not enough. The first
`Promote prod` run with one passed *Check the image exists* and then failed
in `fly deploy` with `Not authorized to access this imageref`, because the
image belongs to `rulesmith-dev`. The org token can manage every app in
`personal`. It is held only by the `production` environment, which is
limited to `main` and gated by the required reviewer. Do not work around a
narrower token by rebuilding or copying the image for prod: promotion must
deploy the exact image dev ran.

**H4. Check the setup.**

```bash
gh api repos/ionut-banu/rulesmith/environments --jq '.environments[].name'   # dev, production
gh secret list --env dev --repo ionut-banu/rulesmith                         # FLY_API_TOKEN
gh secret list --env production --repo ionut-banu/rulesmith                  # FLY_API_TOKEN
gh secret list --repo ionut-banu/rulesmith                                   # empty: no repo-level token
fly tokens list --app rulesmith-dev                                          # github-cd-dev
fly tokens list --scope org --org personal                                   # github-cd-prod-org
```

**Token rotation.** Tokens expire after one year. When CD starts failing
with an authentication error, or to rotate early:

```bash
fly tokens list --app rulesmith-dev              # dev: find the old token's id
fly tokens list --scope org --org personal       # prod: find the old token's id
fly tokens revoke <id>
```

then run H2 (dev) or H3 (prod) again. To avoid a window in which CD fails,
you can run H2 or H3 first and revoke the old id afterwards.

## Known gaps

- The prod token is org-scoped, so it could deploy to any app in
  `personal`, not only `rulesmith-prod` (see H3).
- No automatic rollback. A failed version check leaves the new release
  running; roll back by hand with `Promote prod`.
- `Promote prod` checks that a tag is in the registry, not that it was
  deployed successfully to dev.
- Images built before CD, including `20261106-201341-2452847`, cannot be
  promoted with the workflow, because they do not report a version.
- No workflow to redeploy or roll back dev to a chosen tag. Dev only moves
  forward with `main`; use the manual commands for anything else.
- No retention or cleanup policy for old tags in
  `registry.fly.io/rulesmith-dev`, including whether Fly keeps an image no
  release uses, so an old tag could disappear before someone needs it for a
  rollback.
- No branch protection or required checks on `main`, so CD trusts that CI
  ran, not that it was required.
- No actionlint in CI, no post-deploy smoke test beyond `/health`, and no
  deploy failure notifications other than GitHub's own email.
- No database migrations: tables are created at startup.
- Telemetry from Fly is not shipped anywhere (see #23).
- The URLs are public and there is no login, so anyone who finds them can
  create datasets and upload files.
- No backup or restore procedure for the volume or Neon yet (Fly keeps daily
  volume snapshots by default; Neon has point-in-time restore).
- One machine per app until uploads move to object storage.
