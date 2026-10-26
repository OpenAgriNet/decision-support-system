"""Fetch the Photon jar and a place index into the current directory.

Runs as the `photon-index` job in the compose files, on a Hardened Python
image. That image has no shell, curl or bzip2, so this does what the old shell
script did with the standard library. Needs PHOTON_VERSION and PHOTON_DB_URL.

The job runs as root so it can write a volume the old root-run image left
behind, then hands everything to the user Photon runs as (65532), because
Photon's own image is non-root.
"""

import bz2
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

PHOTON_UID = 65532
MARKER = Path(".index-ready")


def say(message: str) -> None:
    print(f"photon: {message}", flush=True)  # noqa: T201 - the job log


def fetch_jar(version: str) -> None:
    jar = Path(f"photon-{version}.jar")
    if jar.exists():
        say(f"{jar} is already here")
        return
    say(f"downloading {jar}")
    url = f"https://github.com/komoot/photon/releases/download/{version}/{jar}"
    with urllib.request.urlopen(url) as response, jar.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)


def fetch_index(url: str) -> None:
    # The marker holds the URL of the index that finished downloading. A cut-short
    # download leaves none, and a changed URL no longer matches.
    if MARKER.exists() and MARKER.read_text() == url:
        say("the place index is already here")
        return
    say("downloading the place index, this takes a while the first time")
    MARKER.unlink(missing_ok=True)
    shutil.rmtree("photon_data", ignore_errors=True)
    # BZ2File, not tarfile's own `r|bz2`: the archive is several bzip2 streams
    # joined (as `bzip2 -cd` reads them), which the latter stops short on.
    with (
        urllib.request.urlopen(url) as response,  # noqa: S310
        tarfile.open(fileobj=bz2.BZ2File(response), mode="r|") as archive,
    ):
        archive.extractall(".", filter="data")
    MARKER.write_text(url)
    say("the place index is ready")


def hand_to_photon() -> None:
    for root, directories, files in os.walk("."):
        for name in [*directories, *files]:
            path = os.path.join(root, name)
            os.chown(path, PHOTON_UID, PHOTON_UID, follow_symlinks=False)
    os.chown(".", PHOTON_UID, PHOTON_UID)


def main() -> None:
    fetch_jar(os.environ["PHOTON_VERSION"])
    fetch_index(os.environ["PHOTON_DB_URL"])
    hand_to_photon()


if __name__ == "__main__":
    sys.exit(main())
