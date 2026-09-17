#!/usr/bin/env python3
"""Render deploy/taskdef.json from its template.

CodeDeployToECS substitutes only IMAGE1_NAME, so every other field has to be
literal by the time the deploy action reads the file. Resolving them here keeps
account identifiers, and the database secret's generated ARN, out of the
repository. The secret suffix is the value that went stale and wedged a
deployment; nothing pins it any more.
"""

import json
import os
import string
import subprocess
import sys

TEMPLATE = "deploy/taskdef.template.json"
OUTPUT = "deploy/taskdef.json"


def aws(*args: str) -> str:
    result = subprocess.run(["aws", *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def resolve() -> dict[str, str]:
    project = os.environ["PROJECT"]
    return {
        "PROJECT": project,
        "REGION": os.environ.get("AWS_REGION") or os.environ["AWS_DEFAULT_REGION"],
        "ACCOUNT": aws("sts", "get-caller-identity", "--query", "Account", "--output", "text"),
        # The only lookup. Everything else is derived from the project name.
        "DB_SECRET_ARN": aws(
            "ssm",
            "get-parameter",
            "--name",
            f"/{project}/db/secret-arn",
            "--query",
            "Parameter.Value",
            "--output",
            "text",
        ),
    }


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
