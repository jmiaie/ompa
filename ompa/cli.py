"""
CLI for OMPA.
Run with: ao <command> or ao-mcp <command>
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ompa import Ompa
from ompa.config import make_ompa

app = typer.Typer(help="OMPA — Universal AI agent memory layer")
console = Console()


@app.command()
def init(
    vault_path: Path = Path("."),
    shared_vault: Path | None = typer.Option(None, help="Shared vault path"),
    personal_vault: Path | None = typer.Option(None, help="Personal vault path"),
):
    """Initialize vault + palace structure."""
    from ompa import Vault

    if shared_vault and personal_vault:
        # Dual-vault init
        Vault(shared_vault)
        Vault(personal_vault)
        ao = make_ompa(
            shared_vault_path=shared_vault,
            personal_vault_path=personal_vault,
            enable_semantic=False,
        )
        shared_stats = ao.get_stats()
        console.print("[green]Dual-vault initialized![/green]")
        console.print(f"  Shared: {shared_vault.absolute()}")
        console.print(f"  Personal: {personal_vault.absolute()}")
        console.print(f"  Shared notes: {shared_stats['total_notes']}")
    else:
        vault = Vault(vault_path)
        stats = vault.get_stats()
        ao = Ompa(vault_path, enable_semantic=False)
        palace_count = ao.palace_build()
        console.print(f"[green]Initialized at {vault_path.absolute()}[/green]")
        console.print(f"  Notes: {stats['total_notes']}")
        console.print(f"  Brain notes: {stats['brain_notes']}")
        console.print(f"  Palace wings built: {palace_count}")


@app.command()
def status(
    vault_path: Path = Path("."),
):
    """Show vault + palace + KG overview."""
    ao = Ompa(vault_path, enable_semantic=False)

    vault_stats = ao.get_stats()
    palace_stats = ao.palace.stats()
    kg_stats = ao.kg.stats()

    console.print("[bold]Vault[/bold]")
    console.print(f"  Total notes: {vault_stats['total_notes']}")
    console.print(f"  Brain notes: {vault_stats['brain_notes']}")
    console.print(f"  Orphans: {vault_stats['orphans']}")
    console.print("[bold]Palace[/bold]")
    console.print(f"  Wings: {palace_stats['wing_count']}")
    console.print(f"  Rooms: {palace_stats['room_count']}")
    console.print(f"  Drawers: {palace_stats['drawer_count']}")
    console.print("[bold]Knowledge Graph[/bold]")
    console.print(f"  Entities: {kg_stats['entity_count']}")
    console.print(f"  Current facts: {kg_stats['current_facts']}")


@app.command()
def session_start(
    vault_path: Path = Path("."),
):
    """Run session start hook."""
    ao = Ompa(vault_path, enable_semantic=False)
    result = ao.session_start()
    console.print(result.output)


@app.command()
def classify(
    message: str,
    vault_path: Path = Path("."),
):
    """Classify a message."""
    ao = Ompa(vault_path, enable_semantic=False)
    c = ao.classify(message)
    console.print(f"[bold]Type:[/bold] {c.message_type.value.upper()}")
    console.print(f"[bold]Confidence:[/bold] {c.confidence:.0%}")
    console.print(f"[bold]Action:[/bold] {c.suggested_action}")
    if c.routing_hints:
        console.print("[bold]Hints:[/bold]")
        for hint in c.routing_hints:
            console.print(f"  - {hint}")


@app.command()
def search(
    query: str,
    vault_path: Path = Path("."),
    limit: int = 5,
    vault: str | None = typer.Option(None, help="Which vault: shared, personal, or both"),
    shared_vault: Path | None = typer.Option(None, help="Shared vault path"),
    personal_vault: Path | None = typer.Option(None, help="Personal vault path"),
):
    """Search the vault semantically."""
    ao = make_ompa(vault_path, shared_vault, personal_vault, enable_semantic=True)
    vaults = [vault] if vault and vault != "both" else None
    if vault == "both":
        vaults = ["shared", "personal"]
    results = ao.search(query, limit=limit, vaults=vaults)

    table = Table(title=f"Search: {query}")
    table.add_column("Score")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Excerpt")

    for r in results:
        excerpt = (
            r.content_excerpt[:80] + "..." if len(r.content_excerpt) > 80 else r.content_excerpt
        )
        table.add_row(f"{r.score:.2f}", r.match_type, r.path, excerpt)

    console.print(table)


@app.command()
def orphans(
    vault_path: Path = Path("."),
):
    """Find orphan notes (no wikilinks)."""
    ao = Ompa(vault_path, enable_semantic=False)
    orphan_notes = ao.find_orphans()
    if not orphan_notes:
        console.print("[green]No orphan notes found![/green]")
    else:
        console.print(f"[yellow]Found {len(orphan_notes)} orphan notes:[/yellow]")
        for note in orphan_notes:
            rel = (
                note.path.relative_to(vault_path)
                if note.path.is_relative_to(vault_path)
                else note.path
            )
            console.print(f"  - {rel}")


@app.command()
def wrap_up(
    vault_path: Path = Path("."),
):
    """Run wrap-up (stop) hook."""
    ao = Ompa(vault_path, enable_semantic=False)
    result = ao.stop()
    console.print(result.output)


@app.command()
def wings(
    vault_path: Path = Path("."),
):
    """List palace wings."""
    ao = Ompa(vault_path, enable_semantic=False)
    wing_list = ao.palace.list_wings()

    table = Table(title="Palace Wings")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Keywords")

    for w in wing_list:
        table.add_row(w["name"], w["type"], ", ".join(w.get("keywords", [])))

    console.print(table)


@app.command()
def rooms(
    wing: str,
    vault_path: Path = Path("."),
):
    """List rooms in a wing."""
    ao = Ompa(vault_path, enable_semantic=False)
    room_list = ao.palace.list_rooms(wing)
    if not room_list:
        console.print(f"[yellow]No rooms found in wing '{wing}'[/yellow]")
    else:
        console.print(f"[bold]Rooms in {wing}:[/bold]")
        for r in room_list:
            drawers = ao.palace.get_drawers(wing, r)
            console.print(f"  - {r} ({len(drawers)} drawers)")


@app.command()
def tunnel(
    wing_a: str,
    wing_b: str,
    vault_path: Path = Path("."),
):
    """Find tunnels between two wings."""
    ao = Ompa(vault_path, enable_semantic=False)
    tunnels = ao.palace.find_tunnels(wing_a, wing_b)
    if not tunnels:
        console.print(f"[yellow]No tunnels between {wing_a} and {wing_b}[/yellow]")
    else:
        console.print(f"[bold]Tunnels between {wing_a} and {wing_b}:[/bold]")
        for t in tunnels:
            console.print(f"  - {t['wing_a']}/{t['room']} <-> {t['wing_b']}/{t['room']}")


@app.command()
def kg_query(
    entity: str,
    as_of: str = None,
    vault_path: Path = Path("."),
):
    """Query the knowledge graph."""
    ao = Ompa(vault_path, enable_semantic=False)
    triples = ao.kg.query_entity(entity, as_of=as_of)

    if not triples:
        console.print(f"[yellow]No facts found for '{entity}'[/yellow]")
        return

    console.print(f"[bold]Facts about {entity}:[/bold]")
    if as_of:
        console.print(f"  (as of {as_of})")
    for t in triples:
        console.print(f"  {t.subject} --{t.predicate}--> {t.object}")


@app.command()
def kg_timeline(
    entity: str,
    vault_path: Path = Path("."),
):
    """Get entity timeline."""
    ao = Ompa(vault_path, enable_semantic=False)
    timeline = ao.kg.timeline(entity)

    if not timeline:
        console.print(f"[yellow]No timeline for '{entity}'[/yellow]")
        return

    console.print(f"[bold]Timeline: {entity}[/bold]")
    for event in timeline:
        date_str = event.get("date", "unknown")
        console.print(f"  [{date_str}] {event['label']}")


@app.command()
def kg_stats(
    vault_path: Path = Path("."),
):
    """Show KG statistics."""
    ao = Ompa(vault_path, enable_semantic=False)
    stats = ao.kg.stats()
    console.print("[bold]Knowledge Graph Stats[/bold]")
    for k, v in stats.items():
        console.print(f"  {k}: {v}")


@app.command()
def validate(
    vault_path: Path = Path("."),
):
    """Validate all notes in the vault."""
    ao = Ompa(vault_path, enable_semantic=False)
    from ompa import Vault

    vault = Vault(vault_path)
    notes = vault.list_notes()

    total = 0
    warnings_list = []
    for note in notes:
        result = ao.validate_write(str(note.path))
        if result["warnings"]:
            total += 1
            for w in result["warnings"]:
                rel = (
                    note.path.relative_to(vault_path)
                    if note.path.is_relative_to(vault_path)
                    else note.path
                )
                warnings_list.append(f"  {rel}: {w}")

    if warnings_list:
        console.print(f"[yellow]Found {total} notes with warnings:[/yellow]")
        for w in warnings_list:
            console.print(w)
    else:
        console.print("[green]All notes valid![/green]")


@app.command()
def rebuild_index(
    vault_path: Path = Path("."),
):
    """Rebuild the semantic search index."""
    ao = Ompa(vault_path, enable_semantic=True)
    count = ao.rebuild_index()
    console.print(f"[green]Rebuilt index: {count} files indexed[/green]")


@app.command()
def kg_populate(
    vault_path: Path = Path("."),
):
    """Populate KG from vault notes (wikilinks, tags, folders)."""
    ao = Ompa(vault_path, enable_semantic=False)
    count = ao.kg_populate()
    stats = ao.kg.stats()
    console.print(f"[green]KG populated: {count} triples added[/green]")
    console.print(f"  Entities: {stats['entity_count']}")
    console.print(f"  Total facts: {stats['triple_count']}")
    console.print(f"  Current facts: {stats['current_facts']}")


@app.command()
def sync(
    vault_path: Path = Path("."),
    shared_vault: Path | None = typer.Option(None, help="Shared vault path"),
    personal_vault: Path | None = typer.Option(None, help="Personal vault path"),
    backend: str | None = typer.Option(
        None,
        help="Sync backend: git | s3 | rsync. Omits remote push if not set.",
    ),
    remote: str | None = typer.Option(
        None, help="Remote target (git remote, S3 bucket, rsync host)"
    ),
    message: str = typer.Option("chore: vault sync", help="Commit/sync message"),
    push: bool = typer.Option(True, help="Push to remote after local sync"),
):
    """Full sync: rebuild KG + palace + index, then optionally push to remote."""
    ao = make_ompa(vault_path, shared_vault, personal_vault, enable_semantic=True)
    result = ao.sync()
    console.print("[green]Local sync complete![/green]")
    console.print(f"  KG triples: {result['kg_triples']}")
    console.print(f"  Palace wings: {result['palace_wings']}")
    console.print(f"  Indexed files: {result['indexed_files']}")
    if "personal_kg_triples" in result:
        console.print(f"  Personal KG triples: {result['personal_kg_triples']}")
        console.print(f"  Personal palace wings: {result['personal_palace_wings']}")

    if backend and push:
        _sync_remote(vault_path, backend, remote, message)


@app.command()
def write_note(
    content: str,
    vault: str | None = typer.Option(None, help="Target vault: shared or personal"),
    tags: str | None = typer.Option(None, help="Comma-separated tags"),
    file_path: str | None = typer.Option(None, help="Target file path"),
    shared_vault: Path | None = typer.Option(None, help="Shared vault path"),
    personal_vault: Path | None = typer.Option(None, help="Personal vault path"),
    vault_path: Path = Path("."),
):
    """Write content to the appropriate vault (auto-classifies in dual mode)."""
    ao = make_ompa(vault_path, shared_vault, personal_vault, enable_semantic=False)
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    result = ao.write(content, file_path=file_path, tags=tag_list, vault=vault)
    console.print(f"[green]Written to {result['vault']} vault[/green]")
    console.print(f"  Path: {result['path']}")
    console.print(f"  Classified as: {result['classified_as']}")


@app.command()
def export(
    note_path: str,
    shared_vault: Path = typer.Option(..., help="Shared vault path"),
    personal_vault: Path = typer.Option(..., help="Personal vault path"),
    sanitize: bool = typer.Option(True, help="Remove personal markers"),
    confirm: bool = typer.Option(False, help="Skip confirmation, export directly"),
):
    """Export a note from personal vault to shared vault."""
    ao = make_ompa(shared_vault_path=shared_vault, personal_vault_path=personal_vault)
    result = ao.export_to_shared(note_path, confirm=not confirm, sanitize=sanitize)
    if result.get("action") == "preview":
        console.print("[yellow]Preview (run with --confirm to export):[/yellow]")
        console.print(f"  From: {result['source']}")
        console.print(f"  To: {result['target']}")
        console.print(f"  Content: {result['preview'][:200]}...")
    elif result.get("success"):
        console.print("[green]Exported to shared vault[/green]")
        console.print(f"  {result['source']} -> {result['target']}")
    else:
        console.print(f"[red]Export failed: {result.get('error')}[/red]")


@app.command()
def import_note(
    note_path: str,
    shared_vault: Path = typer.Option(..., help="Shared vault path"),
    personal_vault: Path = typer.Option(..., help="Personal vault path"),
    link_back: bool = typer.Option(True, help="Maintain reference to original"),
):
    """Import a note from shared vault to personal vault."""
    ao = make_ompa(shared_vault_path=shared_vault, personal_vault_path=personal_vault)
    result = ao.import_to_personal(note_path, link_back=link_back)
    if result.get("success"):
        console.print("[green]Imported to personal vault[/green]")
        console.print(f"  {result['source']} -> {result['target']}")
    else:
        console.print(f"[red]Import failed: {result.get('error')}[/red]")


@app.command()
def migrate(
    shared_path: Path = typer.Option(..., help="New shared vault path"),
    personal_path: Path = typer.Option(..., help="New personal vault path"),
    vault_path: Path = Path("."),
    rules: str = typer.Option("auto", help="Classification: auto or all-shared"),
):
    """Migrate single vault to dual-vault architecture."""
    ao = Ompa(vault_path, enable_semantic=False)
    result = ao.migrate_to_dual_vault(shared_path, personal_path, rules)
    console.print("[green]Migration complete![/green]")
    console.print(f"  Shared notes: {result['shared_notes']}")
    console.print(f"  Personal notes: {result['personal_notes']}")
    console.print(f"  Config saved: {result['config_saved']}")


def _sync_remote(vault_path: Path, backend: str, remote: str | None, message: str) -> None:
    """Push vault to a remote backend and print the result."""
    from ompa.sync import GitSyncBackend, RsyncBackend, S3SyncBackend, SyncBackend

    b = backend.lower()
    syncer: SyncBackend | None = None
    try:
        if b == "git":
            parts = (remote or "origin/main").split("/", 1)
            git_remote, branch = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "main")
            syncer = GitSyncBackend(remote=git_remote, branch=branch)
        elif b == "s3":
            if not remote:
                console.print("[red]--remote required for S3 backend (bucket name)[/red]")
                return
            syncer = S3SyncBackend(bucket=remote)
        elif b == "rsync":
            if not remote:
                console.print("[red]--remote required for rsync backend (user@host:/path)[/red]")
                return
            syncer = RsyncBackend(remote=remote)
        else:
            console.print(f"[red]Unknown backend: {backend!r}. Choose git | s3 | rsync[/red]")
            return

        if syncer is None:
            return
        result = syncer.push(vault_path, message=message)
        if result.success:
            console.print(f"[green]Remote push ({b}): {result.message}[/green]")
        else:
            console.print(f"[red]Remote push ({b}) failed: {result.error}[/red]")
    except Exception as e:
        console.print(f"[red]Sync error: {e}[/red]")


@app.command()
def doctor(
    vault_path: Path = Path("."),
):
    """Check vault health — structure, KG, palace, semantic index, orphans."""
    from rich import box

    ao = Ompa(vault_path, enable_semantic=False)
    checks: list[tuple[str, str, str]] = []

    # Vault root
    if vault_path.exists():
        checks.append(("OK", "Vault root", str(vault_path.absolute())))
    else:
        checks.append(("ERROR", "Vault root", f"Not found: {vault_path.absolute()}"))

    # Folder structure
    for folder in ["brain", "work", "org", "perf"]:
        fp = vault_path / folder
        if fp.exists():
            note_count = len(list(fp.rglob("*.md")))
            checks.append(("OK", f"{folder}/", f"{note_count} notes"))
        else:
            checks.append(("WARN", f"{folder}/", "Missing — run `ao init` to create"))

    # Palace metadata
    palace_dir = vault_path / ".palace"
    if palace_dir.exists():
        ps = ao.palace.stats()
        checks.append(("OK", ".palace/", f"{ps['wing_count']} wings, {ps['room_count']} rooms"))
    else:
        checks.append(("WARN", ".palace/", "Not built — run `ao init`"))

    # Knowledge graph
    kg_db = vault_path / ".palace" / "knowledge_graph.sqlite3"
    if kg_db.exists():
        ks = ao.kg.stats()
        if ks["triple_count"] > 0:
            checks.append(
                (
                    "OK",
                    "Knowledge Graph",
                    f"{ks['entity_count']} entities, {ks['triple_count']} triples",
                )
            )
        else:
            checks.append(("WARN", "Knowledge Graph", "Empty — run `ao kg-populate` to fill"))
    else:
        checks.append(("WARN", "Knowledge Graph", "Not initialized — run `ao init`"))

    # Semantic index
    index_path = vault_path / ".palace" / "semantic_index"
    if index_path.exists() and any(index_path.iterdir()):
        checks.append(("OK", "Semantic Index", "Present"))
    else:
        checks.append(("INFO", "Semantic Index", "Not built — run `ao rebuild-index` (optional)"))

    # Orphans
    try:
        orphan_list = ao.find_orphans()
        if not orphan_list:
            checks.append(("OK", "Orphan notes", "None"))
        else:
            checks.append(("WARN", "Orphan notes", f"{len(orphan_list)} notes with no wikilinks"))
    except Exception:
        checks.append(("WARN", "Orphan notes", "Check failed"))

    # Total notes
    try:
        vs = ao.get_stats()
        checks.append(("OK", "Total notes", str(vs["total_notes"])))
    except Exception:
        checks.append(("WARN", "Total notes", "Could not read vault"))

    # Render
    styles = {"OK": "green", "WARN": "yellow", "ERROR": "red", "INFO": "blue"}
    table = Table(title="OMPA Health Check", box=box.ROUNDED)
    table.add_column("Status", width=8)
    table.add_column("Check", min_width=22)
    table.add_column("Detail")

    for status, check, detail in checks:
        s = styles.get(status, "white")
        table.add_row(f"[{s}]{status}[/{s}]", check, detail)

    console.print(table)

    errors = sum(1 for s, _, _ in checks if s == "ERROR")
    warns = sum(1 for s, _, _ in checks if s == "WARN")

    if errors:
        console.print(f"\n[red]✗ {errors} error(s), {warns} warning(s)[/red]")
    elif warns:
        console.print(f"\n[yellow]⚠ {warns} warning(s) — vault operational but incomplete[/yellow]")
    else:
        console.print("\n[green]✓ Vault is healthy[/green]")


@app.command()
def migrate_vault(
    vault_path: Path = Path("."),
    dry_run: bool = typer.Option(False, help="Show what would change without applying"),
    force: bool = typer.Option(False, help="Re-run all migrations from scratch"),
):
    """Apply pending schema migrations to the vault (.palace/ indexes, WAL mode)."""
    from ompa.migration import VaultMigrator

    migrator = VaultMigrator()
    report = migrator.check(vault_path)
    console.print(str(report))

    if report.is_current and not force:
        console.print("[green]✓ Vault is up to date — nothing to migrate[/green]")
        return

    if dry_run:
        console.print("\n[yellow]Dry run — no changes will be made[/yellow]")

    result = migrator.run(vault_path, dry_run=dry_run, force=force)
    console.print()
    console.print(str(result))

    if result.success:
        console.print(
            "[green]✓ Migration complete[/green]"
            if not dry_run
            else "[yellow]Dry run complete — re-run without --dry-run to apply[/yellow]"
        )
    else:
        console.print(f"[red]✗ {len(result.errors)} migration error(s)[/red]")


def main():
    app()


if __name__ == "__main__":
    main()
