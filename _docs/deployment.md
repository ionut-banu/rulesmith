# Deployment

Rulesmith runs on [Fly.io](https://fly.io) in two independent public
environments built from the same container image. This page is enough to
recreate both from scratch.

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

## Deploy (first deploy, manual)

The image is built **once**, for dev, and prod deploys that same image. Prod
never rebuilds. Each image gets a tag in the format
`YYYYMMDD-HHMMSS-<shortsha>` (the same format #22's pipeline uses), which is
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
(an authorization or "not found" error), the two apps are probably in
different organizations. Report it on the issue rather than rebuilding for
prod.

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

After the first deploy. Machines stop when idle, so the `curl` checks run
first (they also wake the machine before `fly ssh console`).

```bash
# Health over HTTPS: {"status":"ok"} and HTTP 200 (also proves the database is reachable)
curl -s -w '\n%{http_code}\n' https://rulesmith-dev.fly.dev/health
curl -s -w '\n%{http_code}\n' https://rulesmith-prod.fly.dev/health

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

# Environment and version: dev/<tag> and prod/<tag>, same image on both
fly ssh console --app rulesmith-dev -C "printenv RULESMITH_ENV RULESMITH_VERSION"
fly ssh console --app rulesmith-prod -C "printenv RULESMITH_ENV RULESMITH_VERSION"
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
`fly releases`, `curl`). A human does everything that creates or changes
something:

- Fly account, flyctl login, organizations and API tokens
- Neon account, both Neon projects and their connection strings
- `fly apps create`, `fly apps destroy`, `fly volumes create`,
  `fly secrets set` / `fly secrets import`, `fly deploy`, `fly apps restart`
- Any other `fly` command that changes state

## Known gaps

- No automated build, deploy or promotion yet (#22). Until then, deploy with
  the commands in *Deploy*.
- Telemetry from Fly is not shipped anywhere (see #23).
- The URLs are public and there is no login, so anyone who finds them can
  create datasets and upload files.
- No backup or restore procedure for the volume or Neon yet (Fly keeps daily
  volume snapshots by default; Neon has point-in-time restore).
- One machine per app until uploads move to object storage.
