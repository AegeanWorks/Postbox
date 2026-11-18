"""Command-line interface for Postbox."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from urllib.error import URLError

from postbox.models import CapturedRequest, CorruptInboxError
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


def _non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="postbox",
        description="A local webhook inbox with replay. Zero dependencies.",
    )
    parser.add_argument("--file", default="inbox.jsonl", help="path to the JSONL inbox")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="listen for incoming requests")
    serve_parser.add_argument("--port", type=int, default=8787)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument(
        "--max-body", type=_positive_int, default=DEFAULT_MAX_BODY,
        help="reject bodies above N bytes",
    )
    serve_parser.add_argument(
        "--max", type=_positive_int, default=None, help="keep only the newest N requests"
    )

    list_parser = sub.add_parser("list", help="list captured request ids")
    list_parser.add_argument("--method", help="keep only this HTTP method")
    list_parser.add_argument("--path", help="keep only requests to this exact path")
    list_parser.add_argument(
        "--limit", type=_positive_int, default=None, help="show at most N results"
    )

    show_parser = sub.add_parser("show", help="show one captured request")
    show_parser.add_argument("id", help="full id or unique prefix")

    sub.add_parser("clear", help="empty the inbox")

    replay_parser = sub.add_parser("replay", help="send a captured request to a URL")
    replay_parser.add_argument("id", help="full id or unique prefix")
    replay_parser.add_argument("--url", required=True, help="target URL")
    replay_parser.add_argument(
        "--timeout", type=_non_negative_float, default=10.0, help="timeout in seconds"
    )

    return parser


def _find_or_error(store: InboxStore, prefix: str) -> CapturedRequest | None:
    try:
        request = store.find(prefix)
    except AmbiguousIdError as exc:
        print(f"Ambiguous id '{prefix}': {exc}")
        print("Why: a short prefix can match several captured requests.")
        print("Fix: use one of the listed full ids, or a longer unique prefix.")
        return None
    if request is None:
        print(f"No captured request starts with '{prefix}'.")
        print("Why: replay needs a request that exists in this inbox.")
        print("Fix: run 'postbox list' to see available ids, or capture a request first.")
    return request


def _replay(request: CapturedRequest, url: str, timeout: float) -> int:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be an absolute http:// or https:// address")
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        headers = {
            key: value
            for key, value in request.headers
            if key.lower() not in _HOP_BY_HOP
        }
        target = urllib.request.Request(
            url, data=request.body, headers=headers, method=request.method
        )
        with urllib.request.urlopen(target, timeout=timeout) as response:
            print(f"{request.method} {url} -> {response.status}")
            return 0
    except (URLError, OSError, ValueError) as exc:
        print(f"Replay failed: {exc}")
        print("Why: the target must be a reachable http(s) URL that accepts this method and body.")
        print("Fix: check --url, then retry with a longer --timeout if the target is slow.")
        return 1


def _dispatch(args: argparse.Namespace, store: InboxStore) -> int:
    if args.command == "serve":
        print(f"Postbox listening on http://{args.host}:{args.port} (inbox: {args.file})")
        serve(args.port, store, args.host, args.max_body)
        return 0

    if args.command == "list":
        requests = store.all()
        if args.method:
            requests = [r for r in requests if r.method.upper() == args.method.upper()]
        if args.path:
            requests = [r for r in requests if r.path == args.path]
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
        request = _find_or_error(store, args.id)
        if request is None:
            return 1
        if args.json:
            print(json.dumps(request.to_dict(), ensure_ascii=False))
        else:
            print(json.dumps(request.to_dict(), indent=2, ensure_ascii=False))
            print("\n--- body ---")
            print(request.text())
        return 0

    if args.command == "clear":
        count = store.count()
        store.clear()
        print(f"Cleared {count} captured request(s).")
        return 0

    if args.command == "replay":
        request = _find_or_error(store, args.id)
        if request is None:
            return 1
        return _replay(request, args.url, args.timeout)

    return 2


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return an exit code."""
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

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
