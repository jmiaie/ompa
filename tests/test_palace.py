"""Tests for palace."""

import os
import tempfile


class TestPalace:
    """Test palace metadata layer."""

    def test_create_wing(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project", keywords=["analytics"])
            wings = p.list_wings()
            assert len(wings) == 1
            assert wings[0]["name"] == "Orion"

    def test_rooms_and_drawers(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project")
            p.create_room("Orion", "auth-migration")
            p.link_drawer("Orion", "auth-migration", "work/active/auth.md")
            drawers = p.get_drawers("Orion", "auth-migration")
            assert "work/active/auth.md" in drawers

    def test_halls(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project")
            p.create_room("Orion", "auth-migration")
            p.add_hall(
                "Orion", "auth-migration", "hall_facts", content="Team chose Clerk"
            )
            hall = p.get_hall("Orion", "auth-migration", "hall_facts")
            assert "Clerk" in hall

    def test_tunnels(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project")
            p.create_wing("Kai", type="person")
            p.create_room("Orion", "auth-migration")
            p.create_tunnel("Kai", "Orion", "auth-migration")
            tunnels = p.find_tunnels("Orion", "Kai")
            assert len(tunnels) == 1

    def test_stats(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project")
            p.create_room("Orion", "room1")
            p.link_drawer("Orion", "room1", "file.md")
            stats = p.stats()
            assert stats["wing_count"] == 1
            assert stats["room_count"] == 1

    def test_create_wing_idempotent_preserves_rooms(self):
        """Regression: create_wing on existing wing must NOT wipe rooms.

        Prior bug (pre-0.4.2): create_wing unconditionally overwrote
        ``rooms: {}``, destroying all rooms/drawers/halls under the wing.
        """
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project", keywords=["analytics"])
            p.create_room("Orion", "auth")
            p.link_drawer("Orion", "auth", "work/auth.md")
            p.add_hall("Orion", "auth", "hall_facts", "we chose Clerk")

            # Re-create same wing — must preserve everything.
            p.create_wing("Orion", type="project", keywords=["analytics"])

            assert "auth" in p.list_rooms("Orion")
            assert "work/auth.md" in p.get_drawers("Orion", "auth")
            assert "Clerk" in p.get_hall("Orion", "auth", "hall_facts")

    def test_create_room_idempotent_preserves_drawers_and_halls(self):
        """Regression: create_room on existing room must NOT wipe drawers/halls.

        Prior bug (pre-0.4.2): create_room unconditionally overwrote
        ``drawers: []`` and ``halls: {}``.
        """
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion")
            p.create_room("Orion", "auth")
            p.link_drawer("Orion", "auth", "work/auth.md")
            p.add_hall("Orion", "auth", "hall_facts", "we chose Clerk")

            # Re-create same room — must preserve drawers and halls.
            p.create_room("Orion", "auth")

            assert "work/auth.md" in p.get_drawers("Orion", "auth")
            assert "Clerk" in p.get_hall("Orion", "auth", "hall_facts")

    def test_create_wing_preserves_existing_type_and_keywords(self):
        """Re-creating an existing wing must not silently override metadata."""
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project", keywords=["analytics"])

            # Re-call with different type/keywords — must NOT clobber the
            # original metadata. Callers wanting to update should use a
            # dedicated update helper (none exists yet).
            p.create_wing("Orion", type="person", keywords=["different"])

            wing = p.get_wing("Orion")
            assert wing["type"] == "project"
            assert wing["keywords"] == ["analytics"]

    def test_batch_defers_disk_writes(self):
        """batch() must collapse N mutations into one JSON write."""
        from unittest.mock import patch

        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            # Patch the actual disk-write helper to count calls.
            with patch.object(p, "_save_now", wraps=p._save_now) as save_now:
                with p.batch():
                    for i in range(5):
                        p.create_wing(f"wing-{i}")
                        p.create_room(f"wing-{i}", "r")
                        p.link_drawer(f"wing-{i}", "r", f"f{i}.md")
                assert (
                    save_now.call_count == 1
                ), f"Expected 1 disk write during batch, got {save_now.call_count}"

            # Verify data actually persisted.
            p2 = Palace(os.path.join(tmpdir, ".palace"))
            assert len(p2.list_wings()) == 5

    def test_batch_flushes_on_exception(self):
        """If the batch block raises, queued changes must still flush so
        partial progress isn't silently lost."""
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            try:
                with p.batch():
                    p.create_wing("Orion")
                    raise RuntimeError("boom")
            except RuntimeError:
                pass

            # Fresh Palace must see Orion on disk.
            p2 = Palace(os.path.join(tmpdir, ".palace"))
            assert "Orion" in [w["name"] for w in p2.list_wings()]

    def test_batch_is_reentrant(self):
        """Nested batches only flush at the outermost exit."""
        from unittest.mock import patch

        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            with patch.object(p, "_save_now", wraps=p._save_now) as save_now:
                with p.batch():
                    p.create_wing("A")
                    with p.batch():
                        p.create_wing("B")
                    # Inner exit must NOT have flushed yet.
                    assert save_now.call_count == 0
                assert save_now.call_count == 1


