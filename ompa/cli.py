"""
CLI for AgnosticObsidian.
Run with: ao <command> or ao-mcp <command>
"""

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))

from ompa import AgnosticObsidian

app = typer.Typer(help="AgnosticObsidian — Universal AI agent memory layer")
console = Console()


@app.command()
def init(
    vault_path: Path = Path("."),
):
    """Initialize vault + palace structure."""
    from ompa import Vault

    vault = Vault(vault_path)
    stats = vault.get_stats()
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
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
    ao = AgnosticObsidian(vault_path, enable_semantic=False)

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
    console.print(f"  Tunnels: {palace_stats['tunnel_count']}")

    console.print("[bold]Knowledge Graph[/bold]")
    console.print(f"  Entities: {kg_stats['entity_count']}")
    console.print(f"  Triples: {kg_stats['triple_count']}")
    console.print(f"  Current facts: {kg_stats['current_facts']}")


@app.command()
def session_start(
    vault_path: Path = Path("."),
):
    """Run session start hook."""
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
    result = ao.session_start()
    console.print(result.output)


@app.command()
def classify(
    message: str,
    vault_path: Path = Path("."),
):
    """Classify a message."""
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
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
):
    """Search the vault semantically."""
    ao = AgnosticObsidian(vault_path, enable_semantic=True)
    results = ao.search(query, limit=limit)

    table = Table(title=f"Search: {query}")
    table.add_column("Score")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Excerpt")

    for r in results:
        excerpt = (
            r.content_excerpt[:80] + "..."
            if len(r.content_excerpt) > 80
            else r.content_excerpt
        )
        table.add_row(f"{r.score:.2f}", r.match_type, r.path, excerpt)

    console.print(table)


@app.command()
def orphans(
    vault_path: Path = Path("."),
):
    """Find orphan notes (no wikilinks)."""
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
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
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
    result = ao.stop()
    console.print(result.output)


@app.command()
def wings(
    vault_path: Path = Path("."),
):
    """List palace wings."""
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
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
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
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
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
    tunnels = ao.palace.find_tunnels(wing_a, wing_b)
    if not tunnels:
        console.print(f"[yellow]No tunnels between {wing_a} and {wing_b}[/yellow]")
    else:
        console.print(f"[bold]Tunnels between {wing_a} and {wing_b}:[/bold]")
        for t in tunnels:
            console.print(
                f"  - {t['wing_a']}/{t['room']} <-> {t['wing_b']}/{t['room']}"
            )


@app.command()
def kg_query(
    entity: str,
    as_of: str = None,
    vault_path: Path = Path("."),
):
    """Query the knowledge graph."""
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
    triples = ao.kg.query_entity(entity, as_of=as_of)

    if not triples:
        console.print(f"[yellow]No facts found for '{entity}'[/yellow]")
        return

    console.print(f"[bold]Facts about {entity}:[/bold]")
    if as_of:
        console.print(f"  (as of {as_of})")
    for t in triples:
        validity = f"({t.valid_from}"
        validity += f" to {t.valid_to}" if t.valid_to else ""
        validity += ")"
        console.print(f"  {t.subject} --{t.predicate}--> {t.object} {validity}")


@app.command()
def kg_timeline(
    entity: str,
    vault_path: Path = Path("."),
):
    """Get entity timeline."""
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
    timeline = ao.kg.timeline(entity)

    if not timeline:
        console.print(f"[yellow]No timeline for '{entity}'[/yellow]")
        return

    console.print(f"[bold]Timeline: {entity}[/bold]")
    for event in timeline:
        date_str = event["date"] or "(no date)"
        console.print(f"  [{date_str}] {event['label']}")


@app.command()
def kg_stats(
    vault_path: Path = Path("."),
):
    """Show KG statistics."""
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
    stats = ao.kg.stats()
    console.print("[bold]Knowledge Graph Stats[/bold]")
    for k, v in stats.items():
        console.print(f"  {k}: {v}")


@app.command()
def validate(
    vault_path: Path = Path("."),
):
    """Validate all notes in the vault."""
    ao = AgnosticObsidian(vault_path, enable_semantic=False)
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
        console.print(f"[yellow]Found issues in {total} notes:[/yellow]")
        for w in warnings_list[:20]:
            console.print(w)
        if len(warnings_list) > 20:
            console.print(f"  ... and {len(warnings_list) - 20} more")
    else:
        console.print("[green]All notes valid![/green]")


@app.command()
def rebuild_index(
    vault_path: Path = Path("."),
):
    """Rebuild the semantic search index."""
    ao = AgnosticObsidian(vault_path, enable_semantic=True)
    count = ao.rebuild_index()
    console.print(f"[green]Indexed {count} files[/green]")


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
