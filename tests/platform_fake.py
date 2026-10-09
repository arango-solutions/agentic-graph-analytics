"""A fake Arango platform for platform-login tests (NFR-24).

One real HTTP server on localhost plays both the integration sidecar
(``/_integration/authn/v1/identity`` and ``/createToken``) and the coordinator
(``/_api/version``, ``/_db/<db>/_api/database/current`` and
``/_db/<db>/_api/database/user``), so database handles under test are real
python-arango objects talking HTTP. Database permissions are per user:
``readers[db]`` is the set of users who may use ``db``.
"""

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

PLATFORM_ENV = (
    "ARANGO_DEPLOYMENT_ENDPOINT",
    "ARANGO_DEPLOYMENT_CA",
    "INTEGRATION_HTTP_ADDRESS_FULL",
    "INTEGRATION_HTTP_ADDRESS",
    "AGA_PLATFORM_AUTH",
    "AGA_SERVICE_USER",
    "AGA_SIDECAR_TOKEN_LIFETIME_S",
    "AGA_PLATFORM_CA_BUNDLE",
    "AGA_PLATFORM_VERIFY_TLS",
)


def make_jwt(**claims) -> str:
    def enc(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'HS256'})}.{enc(claims)}.sig"


FORWARDED = make_jwt(
    iss="arangodb",
    preferred_username="alice",
    exp=int(time.time()) + 3600,
    iat=int(time.time()),
)


class FakePlatform:
    """Sidecar + coordinator. Tokens it accepts: the forwarded login, and
    whatever it minted. ``expire_after`` makes minted tokens short-lived."""

    def __init__(self):
        self.users = {FORWARDED: "alice"}
        self.readers = {"aga_workspace": {"alice", "aga-service"}}
        self.minted = []
        self.fail_create = False
        self.expire_after = 3600
        self.seen_tokens = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _refuse(self, status):
                self._send(
                    status,
                    {
                        "error": True,
                        "code": status,
                        "errorNum": 11,
                        "errorMessage": "not authorized",
                    },
                )

            def _caller(self):
                auth = self.headers.get("Authorization", "")
                token = auth[7:] if auth.lower().startswith("bearer ") else None
                if token:
                    fake.seen_tokens.append(token)
                return fake.users.get(token)

            def _database(self):
                if self.path.startswith("/_db/"):
                    return unquote(self.path.split("/")[2])
                return "_system"

            def do_GET(self):
                user = self._caller()
                if self.path == "/_integration/authn/v1/identity":
                    self._send(200, {"user": user}) if user else self._send(401, {})
                    return
                if user is None:
                    self._refuse(401)
                    return
                database = self._database()
                if self.path.endswith("/_api/version"):
                    self._send(200, {"server": "arango", "version": "3.12"})
                elif self.path.endswith("/_api/database/user"):
                    names = sorted(
                        db for db, users in fake.readers.items() if user in users
                    )
                    self._send(200, {"error": False, "code": 200, "result": names})
                elif self.path.endswith("/_api/database/current"):
                    if user not in fake.readers.get(database, set()):
                        self._refuse(403)
                        return
                    self._send(
                        200,
                        {
                            "error": False,
                            "code": 200,
                            "result": {
                                "name": database,
                                "id": "1",
                                "path": "",
                                "isSystem": database == "_system",
                            },
                        },
                    )
                else:
                    self._send(404, {})

            def do_POST(self):
                if self.path != "/_integration/authn/v1/createToken":
                    self._send(404, {})
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.minted.append(body)
                if fake.fail_create:
                    self._send(500, {})
                    return
                token = make_jwt(
                    iss="arangodb",
                    preferred_username=body["user"],
                    exp=int(time.time()) + fake.expire_after,
                    n=len(fake.minted),
                )
                fake.users[token] = body["user"]
                self._send(200, {"token": token})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def user_of_last_request(self):
        return self.users.get(self.seen_tokens[-1]) if self.seen_tokens else None

    def close(self):
        self.server.shutdown()
        self.server.server_close()
