#!/usr/bin/env bash
# Regenerate deploy/taskdef.json from the task definition the service stack
# registered. The deleted fields are server-generated; leaving any of them in
# makes RegisterTaskDefinition fail inside the pipeline, one field per attempt.
set -euo pipefail

FAMILY="${1:?usage: update-taskdef.sh <task-definition-family>}"

aws ecs describe-task-definition --task-definition "$FAMILY" \
  --query taskDefinition --output json \
| jq 'del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
          .compatibilities, .registeredAt, .registeredBy)
      | .containerDefinitions[0].image = "<IMAGE1_NAME>"' \
> deploy/taskdef.json

jq -r '"family: \(.family)  image: \(.containerDefinitions[0].image)"' deploy/taskdef.json
