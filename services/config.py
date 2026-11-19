"""Configuration defaults shared by application setup and services."""
import os

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
EVAL_API_ENABLED = os.getenv("ENABLE_EVAL_API", "false").lower() == "true"
