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
| `build` | pushes to `main`, and only if `test` passed | build, smoke test, push to ECR |

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

Three repository secrets are required:

| Secret | Value |
|---|---|
| `AWS_ROLE_ARN` | `GitHubAppRoleArn`, an output of the `photo-app-main` stack |
| `AWS_REGION` | `eu-north-1` |
| `ECR_REPOSITORY` | `photo-app` |

There are no AWS access keys anywhere. The workflow authenticates with OIDC,
and the role trusts only this repository on `main`.

### Regenerating `deploy/taskdef.json`

The committed file carries the real role ARNs, bucket name, CloudFront domain
and secret ARN, because CodeDeploy reads a complete task definition from the
pipeline's source artifact and substitutes nothing but `<IMAGE1_NAME>`.

**Rebuilding the database stack invalidates it.** Secrets Manager mints a new
random ARN suffix on every create, so the committed file names a secret that no
longer exists. Tasks then fail to start with:

```
ResourceInitializationError: unable to pull secrets or registry auth:
... AccessDeniedException ... not authorized to perform: secretsmanager:GetSecretValue
```

That is *AccessDenied* rather than *NotFound* because the execution role's
policy is scoped to the real secret, so a stale ARN falls outside it. The
CodeDeploy deployment sits at step 1, "Deploying replacement task set", at 50%
until it times out.

Regenerate from what CloudFormation registered:

```bash
R=eu-north-1

# the task definition currently serving traffic, not the latest revision -
# the latest is the broken one the pipeline just registered from this file
TD=$(aws ecs describe-services --cluster photo-app-cluster --services photo-app-service --region $R \
  --query 'services[0].taskSets[?status==`PRIMARY`]|[0].taskDefinition' --output text)

aws ecs describe-task-definition --task-definition "$TD" --region $R --query taskDefinition --output json \
| python3 -c "
import json, sys
d = json.load(sys.stdin)
for k in ('taskDefinitionArn','revision','status','requiresAttributes','compatibilities','registeredAt','registeredBy','deregisteredAt'):
    d.pop(k, None)
d['containerDefinitions'][0]['image'] = '<IMAGE1_NAME>'
json.dump(d, open('deploy/taskdef.json','w'), indent=2)
print('secret:', d['containerDefinitions'][0]['secrets'][0]['valueFrom'])
"

git diff deploy/taskdef.json
```

Stripping those server-generated fields is not optional — leaving any of them in
makes `RegisterTaskDefinition` fail inside the pipeline, one field per attempt.

**Then start a pipeline run by hand.** `pipeline.yaml` sets
`DetectChanges: false` on the GitHub source, so only an image push starts a
deployment. Committing this file deploys nothing on its own:

```bash
aws codepipeline start-pipeline-execution --name photo-app-pipeline --region $R
```

Do not use the retry arrow on the failed stage — it replays the same source
revision, which is the file you just fixed.

If a deployment is still stuck when you start, stop it first so the two do not
overlap:

```bash
DEP=$(aws deploy list-deployments --application-name photo-app-app \
  --deployment-group-name photo-app-dg --region $R \
  --include-only-statuses InProgress --query 'deployments[0]' --output text)
aws deploy stop-deployment --deployment-id "$DEP" --auto-rollback-enabled --region $R
```

### Diagnosing a stuck deployment

```bash
aws ecs describe-tasks --cluster photo-app-cluster --region $R \
  --tasks $(aws ecs list-tasks --cluster photo-app-cluster --desired-status STOPPED --region $R --query 'taskArns[]' --output text) \
  --query 'tasks[].{stopped:stoppedReason,container:containers[0].reason}' --output json
```

| Message | Cause |
|---|---|
| `unable to pull secrets` | stale secret ARN — regenerate this file |
| `unable to pull image` | no route to ECR; check the `ecr.api` and `ecr.dkr` endpoints |
| `Task failed ELB health checks` | container is up but `/health` is not answering on 8080 |
| `Essential container in task exited` | the app is crashing; read `/ecs/photo-app` |

The cluster is `photo-app-cluster` and the service `photo-app-service`; the task
definition family and log group are both plain `photo-app`.

## A note on what is committed

An AWS account id appears in `deploy/taskdef.json` and nowhere else. It is not
a credential: it is in every ARN in the console, and the ARN of a secret is not
the secret. Reading the value needs `secretsmanager:GetSecretValue`, which only
`photo-app-task-execution` holds.

The alternative is a CodeBuild stage that renders the file inside the pipeline.
That removes the account id from this repository, makes the infra repo's
`templates/service.yaml` the only description of the task definition, and ends
the regeneration dance above — at the cost of a component the brief does not
ask for. The infrastructure repository needs neither: everything
account-specific there is read from Parameter Store by the templates.

## Known gaps

- Schema is created at container start, not by a migration tool.
  Alembic run as a one-off task before deployment is the real answer.
- The base image is pinned by tag, not digest, so two builds a week apart can
  differ. ECR scan-on-push covers the OS packages the base contributes;
  pinning the digest is the proper fix.
- Nothing scans the Python dependencies. ECR scan-on-push reads OS packages
  only, so a CVE in Pillow or Flask surfaces nowhere. Inspector enhanced
  scanning on the registry is the AWS-side answer; it bills per image and
  reports after the push instead of blocking it.
- `deploy/taskdef.json` and the infra repo's `templates/service.yaml` describe
  the same task definition in two repositories. Change cpu, memory, an
  environment variable or the port in one and the other silently disagrees: the
  stack keeps reporting the old values while deployments run the new ones.
  Change both, or move to the CodeBuild render described above.
- Dependency versions are pinned and updated by hand. Something like Dependabot
  earns its place once this outlives a single term.
- Rate limiting is absent. With no accounts, the only upload limits are the
  12 MB body cap and whatever the ALB and task count allow.
