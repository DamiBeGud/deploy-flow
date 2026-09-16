import hashlib
import io
import tarfile
from pathlib import Path, PurePosixPath

from app.repository.errors import RepositoryArchiveError, RepositoryLimitError


def extract_tarball(chunks, destination: Path, *, compressed_limit: int, extracted_limit: int, file_limit: int, path_limit: int) -> None:
    data = bytearray()
    for chunk in chunks:
        data.extend(chunk)
        if len(data) > compressed_limit:
            raise RepositoryLimitError()
    destination.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > file_limit:
                raise RepositoryLimitError()
            prefix = None
            for member in members:
                raw = PurePosixPath(member.name)
                if raw.is_absolute() or ".." in raw.parts or len(member.name) > path_limit:
                    raise RepositoryArchiveError()
                if prefix is None and raw.parts:
                    prefix = raw.parts[0]
                parts = raw.parts[1:] if prefix and raw.parts and raw.parts[0] == prefix else raw.parts
                if not parts:
                    continue
                relative = PurePosixPath(*parts)
                if str(relative) in seen or member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise RepositoryArchiveError()
                seen.add(str(relative))
                if member.isdir():
                    (destination / relative).mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise RepositoryArchiveError()
                total += member.size
                if total > extracted_limit:
                    raise RepositoryLimitError()
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise RepositoryArchiveError()
                with target.open("wb") as output:
                    output.write(source.read())
                target.chmod(0o444)
        for directory in destination.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o555)
    except (tarfile.TarError, OSError) as exc:
        raise RepositoryArchiveError() from exc


def excerpt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
