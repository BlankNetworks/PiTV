"""CLI for local operation, diagnostics and the phone control page."""
import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description="Universal TV Debloater for Raspberry Pi")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--state", type=Path, default=Path(os.environ.get("TVCLEANER_STATE", "~/.local/state/tvcleaner")).expanduser())
    parser.add_argument("--demo", action="store_true", help="Simulation only; no real network or TV operations")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve_parser = commands.add_parser("serve", help="Start local phone control page")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    commands.add_parser("token", help="Show local access token; do not share it")
    commands.add_parser("status")
    commands.add_parser("profiles")
    commands.add_parser("runs")
    scan_parser = commands.add_parser("scan")
    scan_parser.add_argument("cidr", nargs="?", default="", help="Omit to discover connected LANs and USB")
    for name in ("connect", "inspect", "report"):
        item = commands.add_parser(name)
        item.add_argument("endpoint")
    pair = commands.add_parser("pair")
    pair.add_argument("endpoint")
    plan = commands.add_parser("plan")
    plan.add_argument("endpoint")
    plan.add_argument("profile_id")
    apply = commands.add_parser("apply", help="Apply a stored reviewed plan")
    apply.add_argument("plan_id")
    apply.add_argument("--package", action="append", required=True, dest="packages")
    apply.add_argument("--enroll", action="store_true")
    restore = commands.add_parser("restore")
    restore.add_argument("run_id")
    restore.add_argument("endpoint")
    automatic = commands.add_parser("automatic")
    automatic.add_argument("mode", choices=("on", "off", "once", "unblock"))
    automatic.add_argument("--cidr", default="")
    automatic.add_argument("--network", choices=("manual", "auto", "usb"), default="auto")
    automatic.add_argument("--policy", choices=("enrolled", "recipes"), default="recipes")
    automatic.add_argument("--no-usb", action="store_true")
    automatic.add_argument("--endpoint", help="TV address for explicitly clearing a failed automatic attempt")
    recipes = commands.add_parser("recipes", help="Manage exact firmware recipes for unattended cleanup")
    recipes.add_argument("action", choices=("list", "create", "export", "import", "enable", "disable"))
    recipes.add_argument("id", nargs="?", help="Run ID for create; recipe ID for export/enable/disable")
    recipes.add_argument("--checks", type=Path, help="JSON with actual owner checks and notes")
    recipes.add_argument("--file", type=Path, help="Reviewed recipe JSON to import")
    args = parser.parse_args(argv)
    if args.demo:
        # Separate simulated records and keys from real device state even with an explicit --state.
        args.state = args.state / "demo"
    try:
        if args.command == "token":
            from .web import get_token
            from .storage import private_dir
            print(get_token(private_dir(args.state)))
            return 0
        from .service import CleanerService
        service = CleanerService(args.root, args.state, demo=args.demo)
        if args.command == "serve":
            from .web import serve
            serve(service, args.host, args.port)
            return 0
        if args.command == "status":
            result = service.status()
        elif args.command == "profiles":
            result = {"profiles": service.catalog.list_profiles()}
        elif args.command == "runs":
            result = service.list_runs()
        elif args.command == "scan":
            result = service.scan(args.cidr)
        elif args.command in ("connect", "inspect"):
            result = getattr(service, args.command)(args.endpoint)
        elif args.command == "report":
            result = service.support_report(args.endpoint)
        elif args.command == "pair":
            import getpass
            result = service.pair(args.endpoint, getpass.getpass("TV pairing code: "))
        elif args.command == "plan":
            result = service.plan(args.endpoint, args.profile_id)
        elif args.command == "apply":
            result = service.apply(args.plan_id, args.packages, args.enroll)
        elif args.command == "restore":
            result = service.restore(args.run_id, args.endpoint)
        elif args.command == "automatic":
            if args.mode == "once":
                result = service.automatic_pass()
            elif args.mode == "unblock":
                if not args.endpoint:
                    raise ValueError("Provide --endpoint for the TV whose failed attempt you reviewed.")
                result = service.clear_automatic_block(args.endpoint)
            else:
                network = "manual" if args.cidr else args.network
                result = service.set_automatic(args.mode == "on", args.cidr, network, not args.no_usb, args.policy)
        elif args.command == "recipes":
            if args.action == "list":
                result = service.list_recipes()
            elif args.action == "create":
                if not args.id or not args.checks:
                    raise ValueError("Provide a restored run ID and --checks containing your actual hardware checks.")
                checks = json.loads(args.checks.read_text(encoding="utf-8"))
                result = service.create_recipe(args.id, checks.get("checks"), checks.get("notes", ""))
            elif args.action == "import":
                if not args.file or args.file.stat().st_size > 262144:
                    raise ValueError("Provide a reviewed recipe JSON file smaller than 256 KiB.")
                result = service.import_recipe(json.loads(args.file.read_text(encoding="utf-8")))
            elif args.action == "export":
                result = service.export_recipe(args.id)
            else:
                result = service.set_recipe_enabled(args.id, args.action == "enable")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if isinstance(result, dict) and result.get("status") in ("needs_restore", "rolled_back", "interrupted"):
            return 1
        return 0
    except Exception as error:
        print(f"TV cleaner: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
