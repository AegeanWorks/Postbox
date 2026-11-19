"""Command-line interface for Postbox."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from postbox import __version__
from postbox.lock import SingleWriterLock
from postbox.models import (
    REDACTED,
    SENSITIVE_HEADERS,
    CapturedRequest,
    CorruptInboxError,
)
from postbox.server import DEFAULT_MAX_BODY, serve
from postbox.store import (
    AmbiguousIdError,
    InboxStore,
    UnsupportedFormatError,
)

_HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding"}


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _iso_time(value: str) -> datetime:
    """Parse an ISO date or datetime into an aware UTC datetime."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use an ISO date or datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _in_time_window(
    request: CapturedRequest,
    after: datetime | None,
    before: datetime | None,
) -> bool:
    moment = datetime.fromisoformat(request.received_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    if after is not None and moment < after:
        return False
    if before is not None and moment > before:
        return False
    return True


def is_loopback(host: str) -> bool:
    """Return True for loopback names and addresses only."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def _port(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("must be in 0..65535")
    return number


def _add_global_flags(subparser: argparse.ArgumentParser) -> None:
    """Accept parent flags after the subcommand without overriding earlier values."""
    subparser.add_argument("--file", default=argparse.SUPPRESS, help="path to the JSONL inbox")
    subparser.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="emit machine-readable JSON",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="postbox",
        description="A local webhook inbox with replay. Zero dependencies.",
    )
    parser.add_argument(
        "--version", action="version", version=f"postbox {__version__}"
    )
    parser.add_argument("--file", default="inbox.jsonl", help="path to the JSONL inbox")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="listen for incoming requests")
    serve_parser.add_argument("--port", type=_port, default=8787)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument(
        "--allow-public", action="store_true", help="allow binding a non-loopback address"
    )
    serve_parser.add_argument(
        "--max-body", type=_positive_int, default=DEFAULT_MAX_BODY,
        help="reject bodies above N bytes",
    )
    serve_parser.add_argument(
        "--max", type=_positive_int, default=None, help="keep only the newest N requests"
    )
    _add_global_flags(serve_parser)

    list_parser = sub.add_parser("list", help="list captured request ids")
    list_parser.add_argument("--method", help="keep only this HTTP method")
    list_parser.add_argument("--path", help="keep only requests to this exact path")
    list_parser.add_argument(
        "--limit", type=_positive_int, default=None, help="show at most N results"
    )
    list_parser.add_argument("--after", type=_iso_time, default=None, help="ISO time lower bound")
    list_parser.add_argument("--before", type=_iso_time, default=None, help="ISO time upper bound")
    _add_global_flags(list_parser)

    show_parser = sub.add_parser("show", help="show one captured request")
    show_parser.add_argument("id", help="full id or unique prefix")
    _add_global_flags(show_parser)

    delete_parser = sub.add_parser("delete", help="remove one captured request")
    delete_parser.add_argument("id", help="full id or unique prefix")
    _add_global_flags(delete_parser)

    clear_parser = sub.add_parser("clear", help="empty the inbox")
    _add_global_flags(clear_parser)

    export_parser = sub.add_parser("export", help="write a portable JSON snapshot")
    export_parser.add_argument("--output", default=None, help="write to FILE instead of stdout")
    _add_global_flags(export_parser)

    import_parser = sub.add_parser("import", help="append a JSON snapshot to the inbox")
    import_parser.add_argument("snapshot", help="snapshot to import")
    _add_global_flags(import_parser)

    replay_parser = sub.add_parser("replay", help="send a captured request to a URL")
    replay_parser.add_argument("id", help="full id or unique prefix")
    replay_parser.add_argument("--url", required=True, help="target URL")
    replay_parser.add_argument(
        "--preserve-path", action="store_true",
        help="use the captured path and query with the target host",
    )
    replay_parser.add_argument(
        "--timeout", type=_non_negative_float, default=10.0, help="timeout in seconds"
    )
    _add_global_flags(replay_parser)

    return parser


def _lookup_error(
    prefix: str,
    context: str,
    json_mode: bool,
    ambiguous: AmbiguousIdError | None = None,
) -> None:
    """Report a failed lookup in the calling command's own language."""
    if json_mode:
        payload = {
            "error": "ambiguous_id" if ambiguous else "not_found",
            "prefix": prefix,
        }
        if ambiguous is not None:
            payload["matches"] = ambiguous.matches
        print(json.dumps(payload, ensure_ascii=False))
        return
    if ambiguous is not None:
        print(f"Ambiguous id '{prefix}': {ambiguous}")
        print("Why: a short prefix can match several captured requests.")
        print("Fix: use one of the listed full ids, or a longer unique prefix.")
        return
    print(f"No captured request starts with '{prefix}'.")
    print(f"Why: {context} needs a request that exists in this inbox.")
    print("Fix: run 'postbox list' to see available ids, or capture a request first.")


def _find_or_error(
    store: InboxStore, prefix: str, context: str = "replay", json_mode: bool = False
) -> CapturedRequest | None:
    try:
        request = store.find(prefix)
    except AmbiguousIdError as exc:
        _lookup_error(prefix, context, json_mode, exc)
        return None
    if request is None:
        _lookup_error(prefix, context, json_mode)
    return request


def _replay(
    request: CapturedRequest,
    url: str,
    timeout: float,
    preserve_path: bool = False,
    json_mode: bool = False,
) -> int:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be an absolute http:// or https:// address")
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        target_url = url
        if preserve_path:
            pairs = [
                (name, value)
                for name, value in urllib.parse.parse_qsl(
                    request.query, keep_blank_values=True
                )
                if value != REDACTED
            ]
            query = urllib.parse.urlencode(pairs)
            target_url = urllib.parse.urlunparse(
                (parsed.scheme, parsed.netloc, request.path or parsed.path, "", query, "")
            )
        headers = [
            (name, value)
            for name, value in request.headers
            if name.lower() not in _HOP_BY_HOP
            and name.lower() not in SENSITIVE_HEADERS
            and value != REDACTED
        ]
        target = urllib.parse.urlparse(target_url)
        selector = target.path or "/"
        if target.query:
            selector += "?" + target.query
        if target.scheme == "https":
            connection = http.client.HTTPSConnection(
                target.hostname, target.port or 443, timeout=timeout
            )
        else:
            connection = http.client.HTTPConnection(
                target.hostname, target.port or 80, timeout=timeout
            )
        connection.putrequest(request.method, selector, skip_accept_encoding=True)
        for name, value in headers:
            connection.putheader(name, value)
        if request.body:
            connection.putheader("Content-Length", str(len(request.body)))
        connection.endheaders(request.body if request.body else None)
        response = connection.getresponse()
        status = response.status
        response.read()
        connection.close()
        if json_mode:
            print(
                json.dumps(
                    {"method": request.method, "url": target_url, "status": status},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"{request.method} {target_url} -> {status}")
        return 0
    except (http.client.HTTPException, OSError, ValueError) as exc:
        if json_mode:
            print(json.dumps({"error": "replay_failed", "detail": str(exc)}, ensure_ascii=False))
        else:
            print(f"Replay failed: {exc}")
            print("Why: the target must be a reachable http(s) URL that accepts this method and body.")
            print("Fix: check --url, then retry with a longer --timeout if the target is slow.")
        return 1


def _dispatch(args: argparse.Namespace, store: InboxStore) -> int:
    if args.command == "serve":
        if not is_loopback(args.host) and not args.allow_public:
            print(f"Error: refusing to bind non-loopback address '{args.host}'.")
            print("Why: the inbox has no authentication and captures may contain sensitive data.")
            print("Fix: use --host 127.0.0.1, or pass --allow-public deliberately.")
            return 2
        if args.allow_public:
            print("WARNING: this inbox has no authentication; anyone who can reach it can read captures.")
        lock = SingleWriterLock(args.file)
        try:
            if not lock.acquire():
                print("Error: another serve process is already using this inbox file.")
                print("Why: two writers can interleave appends with an atomic trim and lose data.")
                print("Fix: stop the other server, or use a different --file.")
                return 1
            serve(args.port, store, args.host, args.max_body)
            return 0
        finally:
            lock.release()

    if args.command == "list":
        requests = store.all()
        if args.method is not None:
            requests = [r for r in requests if r.method.upper() == args.method.upper()]
        if args.path is not None:
            requests = [r for r in requests if r.path == args.path]
        if args.after is not None or args.before is not None:
            requests = [
                r for r in requests if _in_time_window(r, args.after, args.before)
            ]
        if args.limit is not None:
            requests = requests[: args.limit]
        if args.json:
            print(json.dumps([r.to_dict() for r in requests], ensure_ascii=False))
        else:
            for request in requests:
                print(f"{request.id}  {request.method:6}  {request.path}")
            if not requests:
                print("Inbox is empty.")
        return 0

    if args.command == "show":
        request = _find_or_error(store, args.id, "show", args.json)
        if request is None:
            return 1
        if args.json:
            print(json.dumps(request.to_dict(), ensure_ascii=False))
        else:
            print(json.dumps(request.to_dict(), indent=2, ensure_ascii=False))
            print("\n--- body ---")
            print(request.text())
        return 0

    if args.command == "delete":
        target = _find_or_error(store, args.id, "delete", args.json)
        if target is None:
            return 1
        removed = store.delete(target.id)
        if removed is None:
            _lookup_error(target.id, "delete", args.json)
            return 1
        if args.json:
            print(json.dumps({"deleted": removed.id}, ensure_ascii=False))
        else:
            print(f"Deleted {removed.id}.")
        return 0

    if args.command == "clear":
        count = store.count()
        store.clear()
        if args.json:
            print(json.dumps({"cleared": count}))
        else:
            print(f"Cleared {count} captured request(s).")
        return 0

    if args.command == "export":
        payload = {
            "format": "postbox-export",
            "version": 1,
            "requests": [request.to_dict() for request in store.all()],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if args.output:
            with Path(args.output).open("w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        else:
            print(text)
        return 0

    if args.command == "import":
        try:
            data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CorruptInboxError("snapshot is not valid JSON") from exc
        if not isinstance(data, dict) or data.get("format") != "postbox-export":
            raise UnsupportedFormatError("not a postbox-export snapshot")
        if data.get("version") != 1:
            raise UnsupportedFormatError(
                f"snapshot version {data.get('version')!r} is unsupported (this loader reads 1)"
            )
        records = data.get("requests")
        if not isinstance(records, list):
            raise CorruptInboxError("snapshot requests must be a list")
        requests = [CapturedRequest.from_dict(record) for record in records]
        seen_ids = {request.id for request in store.all()}
        fresh: list[CapturedRequest] = []
        for request in requests:
            if request.id in seen_ids:
                continue
            seen_ids.add(request.id)
            fresh.append(request)
        store.extend(fresh)
        skipped = len(requests) - len(fresh)
        if args.json:
            print(json.dumps({"imported": len(fresh), "skipped": skipped}))
        else:
            print(f"Imported {len(fresh)} request(s), skipped {skipped} duplicate(s).")
        return 0

    if args.command == "replay":
        request = _find_or_error(store, args.id, "replay", args.json)
        if request is None:
            return 1
        return _replay(request, args.url, args.timeout, args.preserve_path, args.json)

    return 2


_WRITE_COMMANDS = frozenset({"clear", "delete", "import"})


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return an exit code."""
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    lock = SingleWriterLock(args.file) if args.command in _WRITE_COMMANDS else None
    if lock is not None and not lock.acquire():
        print("Error: a serve process is using this inbox file.")
        print("Why: a CLI write can race a server append or atomic trim and lose data.")
        print("Fix: stop the server, or use a different --file.")
        return 1

    try:
        max_requests = args.max if args.command == "serve" else None
        store = InboxStore(args.file, max_requests=max_requests)
        return _dispatch(args, store)
    except UnsupportedFormatError as exc:
        print(f"Error: {exc}")
        print("Why: this loader refuses a format it cannot read, and leaves the file untouched.")
        print("Fix: point --file at a Postbox v1 inbox, or add a migration for that version.")
        return 1
    except CorruptInboxError as exc:
        print(f"Error: corrupt inbox -- {exc}")
        print("Why: a damaged record cannot be decoded without risking a wrong replay.")
        print("Fix: clear the inbox, or restore it from a backup, then retry.")
        return 1
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}")
        print("Why: the inbox file or one of its settings is invalid or inaccessible.")
        print("Fix: check --file, its permissions and the numeric options, then retry.")
        return 1
    finally:
        if lock is not None:
            lock.release()
