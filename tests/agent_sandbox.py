"""Offline skill evaluation adapter; never connects to a real Zoezi account."""
import json
import os
from pathlib import Path
from unittest.mock import patch

from zoezi_cli.cli import main
from zoezi_cli.client import Client
from test_client import FakeTransport, NOW, TENANT


root = Path(os.environ["ZOEZI_SANDBOX_STATE"])
root.mkdir(parents=True, exist_ok=True)
os.environ["ZOEZI_CONFIG_DIR"] = str(root)
transport = FakeTransport()
transport.rows[102].update(numBooked=10, numQueue=0)
if (root / "synthetic-bookings.json").exists():
    data = json.loads((root / "synthetic-bookings.json").read_text())
    transport.own = {int(k): v for k, v in data.items()}
client = Client(root=root, tenant=TENANT, transport=transport, now=lambda: NOW)
client.config["calendar_id"] = "synthetic-family"
try:
    with patch("zoezi_cli.cli.Client", return_value=client):
        main()
finally:
    (root / "synthetic-bookings.json").write_text(json.dumps(transport.own))
    with (root / "calls.jsonl").open("a") as log:
        for method, path, params, body in transport.calls:
            log.write(json.dumps({"method": method, "path": path, "params": params}) + "\n")
