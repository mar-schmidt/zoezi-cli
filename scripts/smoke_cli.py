import json
import subprocess
import sys

result = subprocess.run([sys.executable, "-m", "zoezi_cli", "--url", "https://example.zoezi.se", "--no-auto-login", "bookings", "list"], capture_output=True, text=True)
assert result.returncode == 2, result.returncode
assert result.stdout == ""
assert json.loads(result.stderr)["code"] == "login_required"
print("Installed CLI error contract verified without network access")
