import os

# The eventyay host settings require this to be set before Django is configured.
os.environ.setdefault("EVY_RUNNING_ENVIRONMENT", "testing")
