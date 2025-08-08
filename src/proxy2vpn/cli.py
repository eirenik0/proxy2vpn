"""Command line interface for proxy2vpn."""
from __future__ import annotations

from pathlib import Path

import typer

from . import compose_utils, docker_ops

app = typer.Typer(help="proxy2vpn command line interface")

@app.command()
def create(name: str, image: str, command: str = "sleep 60"):
    """Create a container with a given NAME and IMAGE."""
    docker_ops.create_container(name=name, image=image, command=command.split())
    typer.echo(f"Container '{name}' created from image '{image}'.")

@app.command()
def start(name: str):
    """Start a container by NAME."""
    docker_ops.start_container(name)
    typer.echo(f"Container '{name}' started.")

@app.command()
def stop(name: str):
    """Stop a container by NAME."""
    docker_ops.stop_container(name)
    typer.echo(f"Container '{name}' stopped.")

@app.command("list")
def list_cmd():
    """List containers."""
    containers = docker_ops.list_containers(all=True)
    for c in containers:
        typer.echo(f"{c.name}: {c.status}")

@app.command()
def set_image(compose_file: Path, service: str, image: str):
    """Set the IMAGE of a SERVICE in a COMPOSE_FILE."""
    compose_utils.set_service_image(compose_file, service, image)
    typer.echo(f"Service '{service}' image set to '{image}'.")

if __name__ == "__main__":
    app()
