"""Print the payload claims of the GitHub identity token (not secret) to diagnose federation rule mismatches."""
import base64
import json
import os

t = open(os.environ["ANTHROPIC_IDENTITY_TOKEN_FILE"]).read().strip().split(".")[1]
c = json.loads(base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)))
print("OIDC claims:", {k: c.get(k) for k in ("iss", "sub", "aud", "ref", "repository", "event_name")})
