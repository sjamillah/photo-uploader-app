#!/usr/bin/env bash
# Push the immutable tag, record its digest, then move the trigger tag.
#
# The order matters: moving :latest is what fires the EventBridge rule, so the
# digest has to be in Parameter Store before anything reacts to it.
#
#   scripts/publish-image.sh ci-candidate <registry> <repository> <sha>
set -euo pipefail

LOCAL="${1:?usage: publish-image.sh <local-image> <registry> <repository> <sha>}"
REGISTRY="${2:?missing registry}"
REPOSITORY="${3:?missing repository}"
SHA="${4:?missing commit sha}"
# Derived from the repository name, so the project appears in one place.
PARAM_PREFIX="${PARAM_PREFIX:-/$REPOSITORY/image}"

remote="$REGISTRY/$REPOSITORY"

docker tag "$LOCAL" "$remote:$SHA"
docker push "$remote:$SHA"

digest="$(aws ecr describe-images \
  --repository-name "$REPOSITORY" \
  --image-ids "imageTag=$SHA" \
  --query 'imageDetails[0].imageDigest' --output text)"

previous="$(aws ssm get-parameter --name "$PARAM_PREFIX/current" \
  --query Parameter.Value --output text 2>/dev/null || true)"

if [ -n "$previous" ]; then
  aws ssm put-parameter --name "$PARAM_PREFIX/previous" \
    --type String --overwrite --value "$previous" >/dev/null
fi

aws ssm put-parameter --name "$PARAM_PREFIX/current" \
  --type String --overwrite --value "$remote@$digest" >/dev/null

docker tag "$LOCAL" "$remote:latest"
docker push "$remote:latest"

echo "published $remote@$digest"
