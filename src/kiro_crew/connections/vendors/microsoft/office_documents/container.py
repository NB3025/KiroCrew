"""Part-level OOXML container access with byte-preserving write-back.

An OOXML file is a zip of *parts*. The fidelity rule this engine keeps is: an
edit rewrites ONLY the parts it changed, and every other part is copied through
**byte-for-byte** — same bytes, same compression type, same order. That is what
lets a targeted edit (change one paragraph's text) leave the theme, styles,
media, custom XML and relationships of a real document exactly as the authoring
application wrote them, instead of the lossy "parse the whole thing and
re-serialize" round-trip a full-document library would do.

Reads go through :func:`read_part`, hardened by ``kiro_crew.zip_vet`` (declared
inventory bound) and a real decompressed-size cap, and parsed with
``defusedxml`` so a crafted part cannot mount an XXE. Writes go through
:func:`rewrite_parts`, which is atomic (temp file + ``os.replace``) so a failed
or interrupted write never truncates the destination in place.
"""

from __future__ import annotations

import os
import stat
import tempfile
import zipfile

try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ModuleNotFoundError:  # pragma: no cover - exercised via monkeypatch
    _xml_fromstring = None  # type: ignore[assignment]

from kiro_crew.security import is_sensitive_path
from kiro_crew.zip_vet import ZipInventoryRejected, vet_zip_inventory

from . import constants as C
from .errors import MalformedDocument, OfficeDocumentError

__all__ = [
    "read_part",
    "part_names",
    "parse_xml_part",
    "rewrite_parts",
]


def _guard_sensitive(path: str) -> None:
    if is_sensitive_path(path):
        raise OfficeDocumentError(
            f"refusing to read sensitive path: {path}", reason="sensitive_path"
        )


def _open_vetted(path: str) -> zipfile.ZipFile:
    """Open *path* as a zip after bounding its declared inventory.

    The vet runs BEFORE ``ZipFile`` is constructed because construction
    allocates from the declared central-directory size; see ``zip_vet``.
    """
    try:
        vet_zip_inventory(path, max_members=C.MAX_ARCHIVE_MEMBERS)
    except ZipInventoryRejected as exc:
        raise MalformedDocument(f"archive inventory rejected: {exc.reason}") from exc
    try:
        return zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise MalformedDocument(f"cannot open container: {exc}") from exc


def _read_member_bounded(zf: zipfile.ZipFile, name: str, max_size: int) -> bytes:
    """Read one member's decompressed bytes, refusing a member over *max_size*.

    A container's central directory can be tiny while a single member's deflate
    stream expands enormously (a "zip bomb"): the inventory vet bounds member
    COUNT and central-directory bytes, never decompressed part size, so the cap
    must be enforced at the point bytes are actually inflated. Reads one byte
    past the cap and rejects if that byte exists, so the whole member is never
    materialised when it is oversized.
    """
    with zf.open(name) as fh:
        data = fh.read(max_size + 1)
    if len(data) > max_size:
        raise MalformedDocument(f"part {name!r} exceeds {max_size} bytes decompressed")
    return data


def _infolist_no_duplicates(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Return the members, rejecting a container that names one part twice.

    A zip may physically hold two entries with the same name; a verbatim copy
    would emit both and a reader picks the last, so a duplicate name is a way to
    smuggle content past a check that inspected only the first. Refuse it rather
    than propagate the ambiguity.
    """
    infos = zf.infolist()
    seen: set[str] = set()
    for info in infos:
        if info.filename in seen:
            raise MalformedDocument(f"container names part {info.filename!r} more than once")
        seen.add(info.filename)
    return infos


def part_names(path: str) -> list[str]:
    """Return the container's member names in stored order."""
    _guard_sensitive(path)
    with _open_vetted(path) as zf:
        return zf.namelist()


def read_part(path: str, part: str, *, max_size: int | None = None) -> bytes:
    """Read one part's decompressed bytes, capped at *max_size*.

    Raises :class:`MalformedDocument` if the part is absent or its real
    decompressed size exceeds the cap, regardless of what the zip header
    declares (defends against a lying header / zip bomb).
    """
    _guard_sensitive(path)
    if max_size is None:
        max_size = C.MAX_PART_BYTES
    with _open_vetted(path) as zf:
        if part not in zf.namelist():
            raise MalformedDocument(f"container has no part {part!r}")
        return _read_member_bounded(zf, part, max_size)


def parse_xml_part(path: str, part: str):
    """Read and XML-parse one part with a hardened (XXE-safe) parser.

    Returns the parsed root ``Element``. Raises :class:`MalformedDocument` on
    an unparseable part or when the hardened parser is unavailable (a stale
    install), never falling back to the entity-resolving stdlib parser.
    """
    if _xml_fromstring is None:
        raise MalformedDocument(
            "defusedxml is not installed; refusing to parse OOXML with the "
            "entity-resolving stdlib parser (run: pip install -e .)"
        )
    data = read_part(path, part)
    try:
        return _xml_fromstring(data)
    except Exception as exc:  # defusedxml raises several distinct types
        raise MalformedDocument(f"part {part!r} is not well-formed XML: {exc}") from exc


def rewrite_parts(
    src_path: str,
    dst_path: str,
    replacements: dict[str, bytes],
    *,
    expect_source_signature: "tuple[int, int] | None" = None,
) -> None:
    """Write *dst_path* as *src_path* with *replacements* substituted per part.

    ``replacements`` maps a part name that MUST already exist in the source to
    its new bytes. Every part not named is copied through byte-for-byte: same
    compression type, same order, so the theme, styles, media and relationships
    of a real document survive a targeted edit exactly as the producer wrote
    them. A replacement naming a nonexistent part is a :class:`MalformedDocument`
    — the caller asked to change something that is not there.

    Each untouched member is copied through a decompressed-size cap
    (:data:`constants.MAX_PART_BYTES`): the byte-preserving copy inflates the
    member, so a high-ratio "zip bomb" member would otherwise exhaust memory on
    the copy path even though ``classify`` and the inventory vet passed. A
    container that names one part twice is refused (:func:`_infolist_no_duplicates`).

    Atomic: the whole archive is built in a temp file in the destination
    directory and swapped in with ``os.replace`` only once fully written, so an
    error mid-write never leaves a truncated destination. ``src_path`` and
    ``dst_path`` may be the same file; the swap makes in-place edit safe.
    """
    _guard_sensitive(src_path)
    _guard_sensitive(dst_path)

    dst_dir = os.path.dirname(os.path.abspath(dst_path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".ooxml-", suffix=".tmp", dir=dst_dir)
    os.close(fd)
    try:
        with _open_vetted(src_path) as zsrc:
            infos = _infolist_no_duplicates(zsrc)
            existing = {info.filename for info in infos}
            missing = [p for p in replacements if p not in existing]
            if missing:
                raise MalformedDocument(
                    f"cannot replace part(s) absent from source: " f"{', '.join(sorted(missing))}"
                )
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zdst:
                # Preserve original order for untouched parts; emit replacements
                # in their original slot so the central directory order is stable.
                for info in infos:
                    name = info.filename
                    if name in replacements:
                        data = replacements[name]
                    else:
                        data = _read_member_bounded(zsrc, name, C.MAX_PART_BYTES)
                    out = zipfile.ZipInfo(filename=name, date_time=info.date_time)
                    out.compress_type = info.compress_type
                    out.external_attr = info.external_attr
                    out.internal_attr = info.internal_attr
                    out.create_system = info.create_system
                    out.flag_bits = info.flag_bits
                    zdst.writestr(out, data)
        # The source zip is now CLOSED: on Windows an in-place edit (src == dst)
        # would raise a sharing violation if os.replace ran with the handle open.
        # F2 (concurrent-writer pin): if the caller captured the source's
        # signature at plan time (before it read the part it is editing),
        # revalidate it now — a writer who changed src between plan and publish
        # would otherwise be silently overwritten. Mismatch aborts the edit with
        # the destination untouched.
        if expect_source_signature is not None:
            current = source_signature(src_path)
            if current != expect_source_signature:
                raise MalformedDocument(
                    "source changed between read and write "
                    f"(expected {expect_source_signature}, saw {current}); "
                    "aborting to avoid overwriting a concurrent edit"
                )
        # Carry the file mode AND owner/group of the destination-being-replaced
        # (or, for a new destination, the source) onto the temp so the swap does
        # not downgrade a 0644 document to mkstemp's private 0600, nor silently
        # reassign a group-shared file's group.
        _carry_file_metadata(dst_path if os.path.exists(dst_path) else src_path, tmp)
        os.replace(tmp, dst_path)
    except BaseException:
        # Never leave the temp artifact behind on any failure path.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def source_signature(path: str) -> tuple[int, int] | None:
    """Return a cheap change-signature ``(size, mtime_ns)`` for *path*, or None.

    Pins a source file between the moment an editor reads the part it will
    change and the moment :func:`rewrite_parts` republishes, so a concurrent
    writer's change is detected rather than silently overwritten. ``None`` when
    the file cannot be stat'd (it is revalidated, not trusted).
    """
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _carry_file_metadata(model_path: str, target_path: str) -> None:
    """Copy *model_path*'s permission bits and owner/group onto *target_path*.

    ``mkstemp`` creates a private 0600 file owned by the running user; without
    this the atomic swap would tighten a normal 0644 document's permissions and,
    on a group-shared file, silently reassign its group. Best effort per field:
    ``chmod`` almost always succeeds; ``chown`` to a different owner needs
    privilege, so a failure there leaves the running user's ownership rather than
    failing the whole edit. POSIX ACLs beyond the mode bits have no portable
    stdlib API and are not carried — a documented limit, not a silent one.
    """
    try:
        st = os.stat(model_path)
    except OSError:
        return
    try:
        os.chmod(target_path, stat.S_IMODE(st.st_mode))
    except OSError:
        pass
    if hasattr(os, "chown"):
        try:
            os.chown(target_path, st.st_uid, st.st_gid)
        except OSError:
            pass
