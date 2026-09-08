# Darkroom

A shared wall for photographs. Upload an image, say something about it, and it
appears for everyone. No accounts.

Runs on ECS Fargate behind an ALB. Images live in a private S3 bucket served
through CloudFront; descriptions live in RDS PostgreSQL. The infrastructure is
in [photo-uploader-infra](https://github.com/sjamillah/photo-uploader-infra).

## Running it locally

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
bash scripts/install-deps.sh --dev

docker run -d --name darkroom-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=local -e POSTGRES_DB=photos postgres:16

cp .env.example .env        # fill in S3_BUCKET and DB_PASSWORD
set -a; . ./.env; set +a
python -m darkroom.app
```

`.env` is gitignored, and nothing is passed on the command line, so a
credential cannot end up in your shell history. Uploads need real S3
credentials; point `S3_BUCKET` at a scratch bucket to exercise the whole path.

```bash
pytest -q            # no AWS and no database needed
ruff check .
ruff format --check .
```

## Layout

| Path | What it holds |
|---|---|
| `.env.example` | Every variable the app reads, with its default |
| `darkroom/config.py` | Every setting, read from the environment once |
| `darkroom/app.py` | Routes, and nothing else |
| `darkroom/storage.py` | Object keys, public URLs, S3 reads and writes |
| `darkroom/db.py` | Connection pool, schema, keyset queries, cursors |
| `darkroom/images.py` | Validation, EXIF handling, the two renditions |
| `darkroom/templates/`, `darkroom/static/` | The interface. No framework, no build step |
| `tests/` | Unit tests. Each one pins a decision worth not losing |
| `deploy/` | `appspec.yaml` and `taskdef.json` for CodeDeploy |
| `scripts/` | What the workflows run, so they stay readable and you can run them by hand |

No module outside `config.py` reads `os.environ`, and the object key layout is
written once, in `storage.object_key`. Both are enforced by a test.

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/` | Server-rendered gallery, first 24 photos |
| `GET` | `/api/photos?q=&cursor=` | JSON feed for search and infinite scroll |
| `POST` | `/api/photos` | Multipart upload. Returns the photo and a manage token |
| `DELETE` | `/api/photos/<id>` | Requires `X-Manage-Token` |
| `GET` | `/health` | Liveness. Does not touch the database |
| `GET` | `/ready` | Readiness. Reports database reachability |

## Notes on the design

**`/health` never queries PostgreSQL.** A deep health check would fail every
target simultaneously during an RDS failover, so the ALB would drain the whole
service over a blip that Multi-AZ handles in about a minute. `/ready` reports
database state separately, for dashboards and not for the load balancer.

**Deleting without accounts.** An upload returns a random token; the browser
keeps it in `localStorage` and the server stores only its SHA-256. You can
delete what you posted and nobody else can. Clear your browser data and you
lose that ability, which is the honest cost of having no user table.

**Uploads are re-encoded, and originals are not kept.** Every image is decoded,
rotated according to its EXIF orientation, flattened, and written out as WebP
at two sizes. Re-encoding is also what strips EXIF, including the GPS tags
phones attach. That is right for a web gallery and wrong for an archive.

**The interface works without JavaScript.** The form posts, the server renders,
the search button navigates. Drag-and-drop, paste, upload progress, infinite
scroll and the lightbox are layered on top.

## Deployment

One workflow, `ci.yml`, with two jobs:

| Job | Runs on | Does |
|---|---|---|
| `test` | pull requests and pushes to `main` | ruff, then pytest |
| `build` | pushes to `main`, and only if `test` passed | build, smoke test, Trivy, push to ECR |

`needs: test` means nothing reaches ECR past a failing test, on a direct push
as much as through a pull request. Only the `build` job is granted
`id-token: write`; the workflow is read-only otherwise.

Each build step is one line calling a script in `scripts/`, so the workflow
stays readable and any step can be run by hand while debugging:

```bash
bash scripts/smoke-test.sh ci-candidate
```

The `build` job tags the image with the commit SHA, records its digest at
`/photo-app/image/current`, and only then moves `latest`. That last push is
what fires the EventBridge rule, so by the time anything reacts the digest is
already recorded.

Two GitHub settings are required:

| Secret | Value |
|---|---|
| `AWS_ROLE_ARN` | `GitHubAppRoleArn` from the bootstrap stack |
| `AWS_REGION` | `eu-west-1` |
| `ECR_REPOSITORY` | `photo-app` |

There are no AWS access keys anywhere. The workflow authenticates with OIDC,
and the role trusts only this repository on `main`.

### Regenerating `deploy/taskdef.json`

The committed file has `ACCOUNT_ID` and `XXXXXX` placeholders so the shape is
reviewable. Once the service stack exists, replace it with the real thing:

```bash
bash scripts/update-taskdef.sh photo-app
```

That strips the server-generated fields and restores the `<IMAGE1_NAME>`
placeholder. Leaving any of those fields in makes `RegisterTaskDefinition`
fail inside the pipeline, one field per attempt.

## A note on what is committed

Nothing in this repo carries an AWS account id except `deploy/taskdef.json`,
and that one is unavoidable: CodeDeploy reads a complete task definition from
the pipeline's source artifact, and a complete task definition names role and
secret ARNs. The committed copy has `ACCOUNT_ID` placeholders so the shape is
reviewable; the real one appears when you run `scripts/update-taskdef.sh`.

If that matters for your account, the options are to keep this repository
private, or to add a CodeBuild step that renders the file from Parameter Store
at deploy time. The infrastructure repository has no such file: everything
account-specific there is read from Parameter Store by the templates.

## Known gaps

- Schema is created at container start, not by a migration tool.
  Alembic run as a one-off task before deployment is the real answer.
- The base image is pinned by tag, not digest, so two builds a week apart can
  differ. Trivy fails the build on any fixable CVE the base carries, which is
  the practical backstop; pinning the digest is the proper fix.
- Dependency versions are pinned and updated by hand. Something like Dependabot
  earns its place once this outlives a single term.
- Rate limiting is absent. With no accounts, the only upload limits are the
  12 MB body cap and whatever the ALB and task count allow.
