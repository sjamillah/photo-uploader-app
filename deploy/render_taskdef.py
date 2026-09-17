#!/usr/bin/env python3
"""Render deploy/taskdef.json from its template.

CodeDeployToECS substitutes only IMAGE1_NAME, so every other field has to be
literal by the time the deploy action reads the file. Resolving them here keeps
account identifiers, and the database secret's generated ARN, out of the
repository. The secret suffix is the value that went stale and wedged a
deployment; nothing pins it any more.

Configuration is written in as plain environment variables rather than as
Parameter Store references. The tasks run in private subnets with no NAT
gateway and there is no ssm interface endpoint, so a task cannot dereference
one. Only the credentials stay as secrets, and secretsmanager does have an
endpoint.
"""

import json
import os
import string
import subprocess
import sys

TEMPLATE = "deploy/taskdef.template.json"
OUTPUT = "deploy/taskdef.json"

# Parameter name under /<project>/ -> token in the template.
PARAMETERS = {
    "db/host": "DB_HOST",
    "db/port": "DB_PORT",
    "db/name": "DB_NAME",
    "db/secret-arn": "DB_SECRET_ARN",
    "cdn/domain": "CDN_DOMAIN",
}


def aws(*args: str) -> str:
    result = subprocess.run(["aws", *args], capture_output=True, text=True)
    if result.returncode != 0:
        # Without this the build reports only "exit status 1" and the reason
        # AWS gave is invisible, because capture_output swallows stderr.
        sys.exit(f"aws {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def parameter(name: str) -> str:
    # get-parameter, not get-parameters: the plural form is a separate IAM
    # action and the render role is granted only ssm:GetParameter.
    return aws(
        "ssm",
        "get-parameter",
        "--name",
        name,
        "--query",
        "Parameter.Value",
        "--output",
        "text",
    )


def resolve() -> dict[str, str]:
    project = os.environ["PROJECT"]

    values = {token: parameter(f"/{project}/{suffix}") for suffix, token in PARAMETERS.items()}
    values["PROJECT"] = project
    values["REGION"] = os.environ.get("AWS_REGION") or os.environ["AWS_DEFAULT_REGION"]
    values["ACCOUNT"] = aws("sts", "get-caller-identity", "--query", "Account", "--output", "text")
    return values


def main() -> int:
    values = resolve()

    with open(TEMPLATE) as fh:
        template = string.Template(fh.read())

    # substitute, not safe_substitute: an unresolved token must fail the build
    # here rather than reach ECS as a literal ${...}.
    rendered = template.substitute(values)

    # Never hand CodeDeploy a file that is not valid JSON.
    json.loads(rendered)

    with open(OUTPUT, "w") as fh:
        fh.write(rendered)

    print(f"rendered {OUTPUT} for {values['PROJECT']} in {values['ACCOUNT']}/{values['REGION']}")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
