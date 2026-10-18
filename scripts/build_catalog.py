"""Publish a PCM feed, retaining releases of the current package identity."""

from __future__ import annotations

import argparse
from copy import deepcopy
import html
import json
from pathlib import Path
import re
import time
import urllib.request

from project_manager import IDENTIFIER, NAME
from scripts.build_release import json_bytes, repository_name, sha256


def merge_packages(packages: list[dict]) -> dict:
    if not packages:
        raise ValueError("Cannot publish an empty catalog")
    versions = {}
    latest_package = None
    latest_version = (-1, -1, -1)
    for package in packages:
        if package["identifier"] != IDENTIFIER:
            raise ValueError("Unexpected package identifier")
        for version in package["versions"]:
            if not re.fullmatch(r"\d+\.\d+\.\d+", version["version"]):
                raise ValueError("Invalid version in catalog")
            existing = versions.get(version["version"])
            if existing and existing != version:
                raise ValueError("A published version must not be replaced with different metadata")
            versions[version["version"]] = deepcopy(version)
            number = tuple(map(int, version["version"].split('.')))
            if number >= latest_version:
                latest_package, latest_version = package, number
    if latest_package is None:
        raise ValueError('Cannot publish a catalog without versions')
    result = deepcopy(latest_package)
    result["versions"] = sorted(
        versions.values(), key=lambda item: tuple(map(int, item["version"].split("."))), reverse=True
    )
    return result


def released_packages(releases: list, repository: str) -> list[dict]:
    packages = []
    # gh api --paginate --slurp returns one array per API page.
    entries = [release for page in releases for release in page] if releases and isinstance(releases[0], list) else releases
    for release in entries:
        if release.get("draft"):
            continue
        tag = release.get("tag_name", "")
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            continue
        filename = f"kicad_project_manager-{tag[1:]}.pcm.json"
        assets = [asset for asset in release.get("assets", []) if asset["name"] == filename]
        if not assets:
            continue
        url = assets[0]["browser_download_url"]
        expected = f"https://github.com/{repository}/releases/download/{tag}/{filename}"
        if url != expected:
            raise ValueError("Unexpected release metadata URL")
        with urllib.request.urlopen(url, timeout=30) as response:
            package = json.load(response)
        if len(package["versions"]) != 1 or package["versions"][0]["version"] != tag[1:]:
            raise ValueError("Release tag and package version disagree")
        packages.append(package)
    return packages


def write_catalog(package: dict, destination: Path, base_url: str, timestamp: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    data = json_bytes({"packages": [package]})
    (destination / "packages.json").write_bytes(data)
    repository = {
        "$schema": "https://go.kicad.org/pcm/schemas/v1#/definitions/Repository",
        "name": NAME,
        "maintainer": package["author"],
        "packages": {
            "url": base_url.rstrip("/") + "/packages.json",
            "sha256": sha256(data), "update_timestamp": timestamp,
        },
    }
    (destination / "repository.json").write_bytes(json_bytes(repository))
    feed_url = html.escape(base_url.rstrip("/") + "/repository.json")
    version = html.escape(package["versions"][0]["version"])
    homepage = html.escape(package["resources"]["homepage"], quote=True)
    (destination / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>KiCad Project Manager</title><body style="font:18px system-ui;max-width:780px;margin:4em auto;padding:1em">'
        '<h1>KiCad Project Manager</h1>'
        '<p>A project companion for KiCad 9. Review template updates, edit KiBot metadata, and apply reusable rules and stackups.</p>'
        f'<p>Latest packaged version: <strong>{version}</strong> (testing).</p>'
        '<p>In KiCad’s Plugin and Content Manager, choose Manage and add this repository URL:</p>'
        f'<p><a href="{feed_url}">{feed_url}</a></p>'
        f'<p><a href="{homepage}">Documentation and source</a></p>'
        '</body></html>\n', encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--releases", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--repository", default=repository_name())
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, default=Path("site"))
    args = parser.parse_args()
    # Packages under a previous identity remain immutable release downloads;
    # KiCad requires reinstalling when switching to the new generic identity.
    packages = [package for package in released_packages(json.loads(args.releases.read_text()), args.repository)
                if package["identifier"] == IDENTIFIER]
    packages.append(json.loads(args.current.read_text()))
    package = merge_packages(packages)
    write_catalog(package, args.output, args.base_url, int(time.time()))
    print(f"Published catalog data for {len(package['versions'])} version(s)")


if __name__ == "__main__":
    main()
