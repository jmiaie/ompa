"""
CLI for agent-memory.
"""
import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from .core import AgentMemory
from .classifier import MessageType

app = typer.Typer(help="Agent Memory - Universal agent memory layer")
console = Console()


@app.command()
def init(
    vault_path: Path = Path("."),
    force: bool = False,
):
    """Initialize agent memory in a vault."""
    from .vault import Vault
    
    vault = Vault(vault_path)
    stats = vault.get_stats()
    
    console.print(f"[green]Agent Memory initialized at {vault_path.absolute()}[/green]")
    console.print(f"  Notes: {stats['total_notes']}")
    console.print(f"  Brain notes: {stats['brain_notes']}")
    console.print(f"  Orphans: {stats['orphans']}")


@app.command()
def session_start(
    vault_path: Path = Path("."),
):
    """Run session start hook."""
    memory = AgentMemory(vault_path)
    result = memory.session_start()
    
    console.print(f"[bold]Session Start[/bold]")
    console.print(result.output)


@app.command()
def classify(
    message: str,
    vault_path: Path = Path("."),
):
    """Classify a message."""
    memory = AgentMemory(vault_path)
    classification = memory.classify(message)
    
    console.print(f"[bold]Classification:[/bold] {classification.message_type.value.upper()}")
    console.print(f"[bold]Confidence:[/bold] {classification.confidence:.0%}")
    console.print(f"[bold]Action:[/bold] {classification.suggested_action}")
    if classification.routing_hints:
        console.print("[bold]Hints:[/bold]")
        for hint in classification.routing_hints:
            console.print(f"  - {hint}")


@app.command()
def search(
    query: str,
    vault_path: Path = Path("."),
    limit: int = 5,
):
    """Search the vault."""
    memory = AgentMemory(vault_path)
    results = memory.search(query, limit)
    
    table = Table(title=f"Search: {query}")
    table.add_column("Path")
    table.add_column("Score")
    table.add_column("Type")
    table.add_column("Excerpt")
    
    for result in results:
        table.add_row(
            result.path,
            f"{result.score:.2f}",
            result.match_type,
            result.content_excerpt[:100] + "..." if len(result.content_excerpt) > 100 else result.content_excerpt,
        )
    
    console.print(table)


@app.command()
def stats(
    vault_path: Path = Path("."),
):
    """Show vault statistics."""
    memory = AgentMemory(vault_path)
    stats = memory.get_stats()
    
    console.print("[bold]Vault Statistics[/bold]")
    console.print(f"  Total notes: {stats['total_notes']}")
    console.print(f"  Brain notes: {stats['brain_notes']}")
    console.print(f"  Orphans: {stats['orphans']}")
    
    if stats['folder_counts']:
        console.print("  Notes by folder:")
        for folder, count in stats['folder_counts'].items():
            console.print(f"    {folder}: {count}")


@app.command()
def orphans(
    vault_path: Path = Path("."),
):
    """Find orphan notes (no links)."""
    memory = AgentMemory(vault_path)
    orphan_notes = memory.find_orphans()
    
    if not orphan_notes:
        console.print("[green]No orphan notes found![/green]")
    else:
        console.print(f"[yellow]Found {len(orphan_notes)} orphan notes:[/yellow]")
        for note in orphan_notes:
            console.print(f"  - {note.path.relative_to(vault_path)}")


@app.command()
def wrap_up(
    vault_path: Path = Path("."),
):
    """Run wrap-up (stop) hook."""
    memory = AgentMemory(vault_path)
    result = memory.stop()
    
    console.print(f"[bold]Wrap-Up[/bold]")
    console.print(result.output)


@app.command()
def rebuild_index(
    vault_path: Path = Path("."),
):
    """Rebuild the semantic search index."""
    memory = AgentMemory(vault_path)
    count = memory.rebuild_index()
    console.print(f"[green]Indexed {count} files[/green]")


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
