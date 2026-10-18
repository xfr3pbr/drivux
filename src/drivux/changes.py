"""Google Drive change feed: what changed since a token, instead of listing the whole Drive again.

The sign-in is rclone's: its token is read from the rclone config, and when it is about to expire
rclone is asked to renew it (any command that talks to Google does).
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import rclone

API = "https://www.googleapis.com/drive/v3/"
CHANGE_FIELDS = (
    "nextPageToken,newStartPageToken,"
    "changes(fileId,removed,file(id,name,parents,mimeType,size,modifiedTime,trashed,ownedByMe))"
)
RENEW_MARGIN = timedelta(seconds=90)


class TokenExpired(RuntimeError):
    """Google no longer has the history since that token: a full scan is needed."""


def _stored_token(remote: str) -> dict:
    config = json.loads(rclone._run(["config", "dump"]))
    return json.loads(config[remote].get("token") or "{}")


def _fresh(token: dict) -> bool:
    try:
        expiry = datetime.fromisoformat(token["expiry"])
    except (KeyError, ValueError):
        return False
    return expiry - datetime.now(timezone.utc) > RENEW_MARGIN


class Feed:
    def __init__(self, remote: str):
        self.remote = remote
        self._access = ""

    def _access_token(self) -> str:
        token = _stored_token(self.remote)
        if not _fresh(token):
            rclone.about(self.remote)  # talking to Google makes rclone renew and save the token
            token = _stored_token(self.remote)
        self._access = token["access_token"]
        return self._access

    def _get(self, path: str, **params) -> dict:
        url = API + path + "?" + urllib.parse.urlencode(params)
        for attempt in (0, 1):
            request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._access or self._access_token()}"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self._access = ""
                    continue
                if exc.code in (400, 404, 410) and "changes" in path:
                    raise TokenExpired(str(exc)) from exc
                raise
        raise AssertionError("unreachable")

    def profile(self) -> dict:
        """Name, email and photo address of the signed-in Google account."""
        return self._get("about", fields="user(displayName,emailAddress,photoLink)").get("user", {})

    def root_id(self) -> str:
        return self._get("files/root", fields="id")["id"]

    def start_token(self) -> str:
        return self._get("changes/startPageToken")["startPageToken"]

    def since(self, token: str) -> tuple[list[dict], str]:
        """Every change after `token` and the token to ask with next time."""
        found: list[dict] = []
        while True:
            page = self._get(
                "changes", pageToken=token, pageSize=1000, fields=CHANGE_FIELDS,
                restrictToMyDrive="true", spaces="drive",
            )
            found += page.get("changes", [])
            if "nextPageToken" not in page:
                return found, page["newStartPageToken"]
            token = page["nextPageToken"]
