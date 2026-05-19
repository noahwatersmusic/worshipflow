import requests
from base64 import b64encode

PCO_BASE = "https://api.planningcenteronline.com"


class PCOClient:
    def __init__(self, app_id, secret):
        token = b64encode(f"{app_id}:{secret}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}"}

    def _get(self, path, params=None):
        r = requests.get(f"{PCO_BASE}{path}", headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def test_connection(self):
        """Returns the authenticated user's name, raises on bad credentials."""
        data = self._get("/people/v2/me")
        return data.get("data", {}).get("attributes", {}).get("name", "Connected")

    def get_service_types(self):
        data = self._get("/services/v2/service_types", params={"per_page": 100, "order": "name"})
        return data["data"]

    def get_plans(self, service_type_id, filter="future"):
        data = self._get(
            f"/services/v2/service_types/{service_type_id}/plans",
            params={"filter": filter, "per_page": 25, "order": "sort_date"},
        )
        return data["data"]

    def get_plan(self, service_type_id, plan_id):
        data = self._get(f"/services/v2/service_types/{service_type_id}/plans/{plan_id}")
        return data["data"]

    def get_plan_items(self, service_type_id, plan_id):
        """Returns the full response dict including 'data' and 'included'."""
        return self._get(
            f"/services/v2/service_types/{service_type_id}/plans/{plan_id}/items",
            params={"include": "song,arrangement", "per_page": 100},
        )

    def get_plan_team_members(self, service_type_id, plan_id):
        data = self._get(
            f"/services/v2/service_types/{service_type_id}/plans/{plan_id}/team_members",
            params={"per_page": 100},
        )
        return data["data"]


def parse_plan_songs(items_response):
    """
    Given the raw /items?include=song response, return a list of dicts:
      {sequence, title, artist, key, length_seconds}
    Only song-type items are returned, sorted by sequence.
    """
    included = {
        f"{obj['type']}_{obj['id']}": obj
        for obj in items_response.get("included", [])
    }
    songs = []
    for item in items_response.get("data", []):
        if item["attributes"].get("item_type") != "song":
            continue
        attrs = item["attributes"]
        song_rel = item.get("relationships", {}).get("song", {}).get("data")
        artist = ""
        if song_rel:
            inc = included.get(f"Song_{song_rel['id']}", {})
            artist = inc.get("attributes", {}).get("author", "")
        songs.append({
            "sequence": attrs.get("sequence", 0),
            "title": attrs.get("title", ""),
            "artist": artist,
            "key": attrs.get("key_name", ""),
            "length_seconds": attrs.get("length", 0) or 0,
        })
    songs.sort(key=lambda x: x["sequence"])
    return songs


_VOCALIST_KEYWORDS = {
    'vocal', 'voice', 'singer', 'singing', 'worship lead', 'worship leader',
    'lead vocal', 'background vocal', 'bgv', 'harmony',
}

def infer_person_role(position_name):
    """Guess vocalist/instrumentalist from a PCO team position name."""
    lower = position_name.lower()
    if any(kw in lower for kw in _VOCALIST_KEYWORDS):
        return 'vocalist'
    return 'instrumentalist'


def parse_plan_members(team_members):
    """
    Given the raw team_members list, return dicts for non-declined members:
      {name, role}
    """
    members = []
    for m in team_members:
        attrs = m["attributes"]
        if attrs.get("status") == "D":
            continue
        name = attrs.get("name", "").strip()
        if not name:
            continue
        members.append({
            "name": name,
            "role": attrs.get("team_position_name", "").strip(),
        })
    return members
