"""Test configuration.

pytest imports this before any test module, which is the only reliable place
to set the environment the application reads at import time.
"""

import os

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CLOUDFRONT_DOMAIN", "cdn.test.invalid")
os.environ.setdefault("SKIP_DB_BOOTSTRAP", "1")
