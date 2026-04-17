"""
Palace — Wing/Room/Closet/Drawer metadata layer for OMPA.
Inspired by MemPalace. Manages the structured metadata that accelerates retrieval.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# orjson is a fast C JSON encoder. It dominates stdlib `json.dump` on
# palace-sized payloads (benchmarks showed json.dump at ~54% of
# auto_build_from_vault cost). Optional dep — fall back silently.
try:
    import orjson  # type: ignore[import-not-found]

    _HAS_ORJSON = True
except ImportError:  # pragma: no cover - optional dep
    _HAS_ORJSON = False

HALL_TYPES = [
    "hall_facts",  # decisions made, choices locked
    "hall_events",  # sessions, milestones, debugging
    "hall_discoveries",  # breakthroughs, insights
    "hall_preferences",  # habits, likes, opinions
    "hall_advice",  # recommendations
]


@dataclass
class Wing:
    name: str
    type: str  # "person" or "project"
    keywords: list[str] = field(default_factory=list)
    rooms: dict = field(default_factory=dict)


@dataclass
class Drawer:
    wing: str
    room: str
    path: str


@dataclass
class Tunnel:
    wing_a: str
    wing_b: str
    room: str
    hall_a: str
    hall_b: str


class Palace:
    """
    Manages the palace metadata layer (wings/rooms/closets/drawers/halls/tunnels).

    The palace is a structured overlay on top of the vault.
    It does NOT store the actual content — that lives in the vault.
    The palace stores metadata that makes retrieval faster.

    Storage: JSON file at .palace/palace.json
    """

    def __init__(self, palace_path: str | Path):
        self.palace_path = Path(palace_path)
        self.palace_path.mkdir(parents=True, exist_ok=True)
        self.data_file = self.palace_path / "palace.json"
        self._data = self._load()
        # Re-entrant batch depth. When > 0, `_save` becomes a no-op and the
        # final write happens when the outermost `batch()` block exits. This
        # lets `auto_build_from_vault` (and `Ompa.sync`) mutate hundreds of
        # wings/rooms/drawers without rewriting the JSON file each time.
        self._batch_depth = 0

    def _load(self) -> dict:
        """Load palace data from disk."""
        if self.data_file.exists():
            with open(self.data_file) as f:
                return json.load(f)
        return {"wings": {}, "tunnels": []}

    def _save(self) -> None:
        """Save palace data to disk unless a batch() block is active.

        Inside a batch, writes are deferred to the batch's exit. Outside a
        batch this writes immediately, preserving the original behavior.
        """
        if self._batch_depth > 0:
            return
        self._save_now()

    def _save_now(self) -> None:
        """Write the palace JSON unconditionally (used by batch exit)."""
        if _HAS_ORJSON:
            # orjson is ~10× faster than stdlib json for this shape of data.
            # Keep OPT_INDENT_2 so the file stays diff-friendly / readable.
            payload = orjson.dumps(self._data, option=orjson.OPT_INDENT_2)
            # Atomic-ish write to avoid leaving a truncated file if the
            # process dies mid-write.
            tmp = self.data_file.with_suffix(".json.tmp")
            tmp.write_bytes(payload)
            tmp.replace(self.data_file)
            return
        with open(self.data_file, "w") as f:
            json.dump(self._data, f, indent=2)

    @contextmanager
    def batch(self):
        """Defer all `_save` calls until the outermost batch exits.

        Nestable — inner blocks do not flush; only the outermost block
        writes to disk. If the block raises, the changes still flush so
        partial progress is not lost.
        """
        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._save_now()

    # Wing operations

    def create_wing(
        self,
        name: str,
        type: str = "project",
        keywords: list[str] | None = None,
    ) -> None:
        """Create a wing if missing; preserve existing rooms/metadata otherwise.

        Idempotent: re-calling with the same name will not overwrite existing
        rooms, drawers, halls, type, or keywords. Use update-style helpers to
        modify metadata on an existing wing.
        """
        if keywords is None:
            keywords = []
        wings = self._data.setdefault("wings", {})
        if name in wings:
            # Wing already exists — preserve everything (including type/keywords),
            # but ensure required keys are present for older-format palaces.
            wings[name].setdefault("name", name)
            wings[name].setdefault("type", type)
            wings[name].setdefault("keywords", keywords)
            wings[name].setdefault("rooms", {})
        else:
            wings[name] = {
                "name": name,
                "type": type,
                "keywords": keywords,
                "rooms": {},
            }
        self._save()

    def list_wings(self) -> list[dict]:
        """List all wings."""
        return [
            {"name": w["name"], "type": w["type"], "keywords": w.get("keywords", [])}
            for w in self._data.get("wings", {}).values()
        ]

    def get_wing(self, name: str) -> Optional[dict]:
        """Get a wing by name."""
        return self._data.get("wings", {}).get(name)

    # Room operations

    def create_room(self, wing: str, room_name: str) -> None:
        """Create a room if missing; preserve existing drawers/halls otherwise.

        Idempotent: re-calling with the same wing/room name will not overwrite
        existing drawers or halls.
        """
        if wing not in self._data.get("wings", {}):
            self.create_wing(wing)
        rooms = self._data["wings"][wing].setdefault("rooms", {})
        if room_name in rooms:
            # Room already exists — preserve drawers/halls, ensure shape.
            rooms[room_name].setdefault("name", room_name)
            rooms[room_name].setdefault("drawers", [])
            rooms[room_name].setdefault("halls", {})
        else:
            rooms[room_name] = {
                "name": room_name,
                "drawers": [],
                "halls": {},
            }
        self._save()

    def list_rooms(self, wing: str) -> list[str]:
        """List all rooms in a wing."""
        wing_data = self._data.get("wings", {}).get(wing)
        if not wing_data:
            return []
        return list(wing_data.get("rooms", {}).keys())

    def get_room(self, wing: str, room_name: str) -> Optional[dict]:
        """Get a room."""
        wing_data = self._data.get("wings", {}).get(wing)
        if not wing_data:
            return None
        return wing_data.get("rooms", {}).get(room_name)

    # Drawer operations

    def link_drawer(self, wing: str, room: str, file_path: str) -> None:
        """Link a vault file as a drawer in a room."""
        if wing not in self._data.get("wings", {}):
            self.create_room(wing, room)
        self._data["wings"][wing].setdefault("rooms", {}).setdefault(
            room, {"drawers": [], "halls": {}}
        )
        drawers = self._data["wings"][wing]["rooms"][room].setdefault("drawers", [])
        if file_path not in drawers:
            drawers.append(file_path)
        self._save()

    def get_drawers(self, wing: str, room: str) -> list[str]:
        """Get all drawers in a room."""
        room_data = self.get_room(wing, room)
        if not room_data:
            return []
        return room_data.get("drawers", [])

    # Hall operations

    def add_hall(self, wing: str, room: str, hall_type: str, content: str) -> None:
        """Add content to a hall within a room."""
        if hall_type not in HALL_TYPES:
            raise ValueError(
                f"Invalid hall type: {hall_type}. Must be one of {HALL_TYPES}"
            )
        if wing not in self._data.get("wings", {}):
            self.create_room(wing, room)
        self._data["wings"][wing]["rooms"][room].setdefault("halls", {})[
            hall_type
        ] = content
        self._save()

    def get_hall(self, wing: str, room: str, hall_type: str) -> Optional[str]:
        """Get hall content."""
        room_data = self.get_room(wing, room)
        if not room_data:
            return None
        return room_data.get("halls", {}).get(hall_type)

    # Tunnel operations

    def create_tunnel(
        self,
        wing_a: str,
        wing_b: str,
        room: str,
        hall_a: str = "hall_events",
        hall_b: str = "hall_facts",
    ) -> None:
        """Create a tunnel (cross-wing connection) via a shared room."""
        tunnels = self._data.setdefault("tunnels", [])
        tunnel_id = f"{wing_a}:{wing_b}:{room}"
        for t in tunnels:
            if t.get("id") == tunnel_id:
                return
        tunnels.append(
            {
                "id": tunnel_id,
                "wing_a": wing_a,
                "wing_b": wing_b,
                "room": room,
                "hall_a": hall_a,
                "hall_b": hall_b,
            }
        )
        self._save()

    def find_tunnels(self, wing_a: str, wing_b: str) -> list[dict]:
        """Find all tunnels between two wings."""
        tunnels = self._data.get("tunnels", [])
        return [
            t
            for t in tunnels
            if (t.get("wing_a") == wing_a and t.get("wing_b") == wing_b)
            or (t.get("wing_a") == wing_b and t.get("wing_b") == wing_a)
        ]

    def find_tunnels_by_room(self, room: str) -> list[dict]:
        """Find all tunnels that pass through a room."""
        return [t for t in self._data.get("tunnels", []) if t.get("room") == room]

    # Traversal

    def traverse(self, wing: str, room: str) -> dict:
        """Walk the palace from a room across all connected wings via tunnels."""
        result = {
            "wing": wing,
            "room": room,
            "room_data": self.get_room(wing, room),
            "tunnels": self.find_tunnels_by_room(room),
            "connected": [],
        }
        for tunnel in result["tunnels"]:
            other_wing = (
                tunnel["wing_b"] if tunnel["wing_a"] == wing else tunnel["wing_a"]
            )
            connected_room = self.get_room(other_wing, room)
            if connected_room:
                result["connected"].append(
                    {
                        "wing": other_wing,
                        "room": room,
                        "room_data": connected_room,
                        "hall": (
                            tunnel["hall_b"]
                            if tunnel["wing_a"] == wing
                            else tunnel["hall_a"]
                        ),
                    }
                )
        return result

    # Auto-build from vault

    def auto_build_from_vault(self, vault_path: Path) -> int:
        """Auto-detect wings and rooms from vault folder structure.

        All mutations are collapsed into a single JSON write via `batch()`,
        so even a thousand-note vault writes the palace file exactly once
        at the end rather than once per create/link operation.
        """
        count = 0
        vault_path = Path(vault_path)

        with self.batch():
            # brain/ notes → wing "brain" with rooms by filename
            brain = vault_path / "brain"
            if brain.exists():
                self.create_wing("brain", type="agent", keywords=["memory", "brain"])
                for note in brain.glob("*.md"):
                    room_name = note.stem.lower().replace(" ", "-")
                    self.create_room("brain", room_name)
                    self.link_drawer(
                        "brain", room_name, str(note.relative_to(vault_path))
                    )
                    count += 1

            # work/active/ → wings by subfolder or single wing "work"
            work_active = vault_path / "work" / "active"
            if work_active.exists():
                self.create_wing("work", type="projects", keywords=["work", "projects"])
                for note in work_active.glob("*.md"):
                    room_name = note.stem.lower().replace(" ", "-")
                    self.create_room("work", room_name)
                    self.link_drawer(
                        "work", room_name, str(note.relative_to(vault_path))
                    )
                    count += 1

            # org/people/ → wings per person
            org_people = vault_path / "org" / "people"
            if org_people.exists():
                for note in org_people.glob("*.md"):
                    person_name = note.stem
                    self.create_wing(
                        person_name, type="person", keywords=[person_name.lower()]
                    )
                    self.create_room(person_name, "context")
                    self.link_drawer(
                        person_name, "context", str(note.relative_to(vault_path))
                    )
                    count += 1

        return count

    # Stats

    def stats(self) -> dict:
        """Get palace statistics.

        Returns:
            dict with keys:
                wing_count (int): Number of top-level wings.
                room_count (int): Total rooms across all wings.
                drawer_count (int): Total file-reference drawers.
                tunnel_count (int): Cross-wing tunnel links.
        """
        wings = self._data.get("wings", {})
        total_rooms = sum(len(w.get("rooms", {})) for w in wings.values())
        total_drawers = sum(
            len(r.get("drawers", []))
            for w in wings.values()
            for r in w.get("rooms", {}).values()
        )
        return {
            "wing_count": len(wings),
            "room_count": total_rooms,
            "drawer_count": total_drawers,
            "tunnel_count": len(self._data.get("tunnels", [])),
        }
