"""JSON CLI facade over the engine/model registry — what the UI (a ModelsWorker,
TODO #14b) talks to. Query commands (engines/list/available/resolve) touch ONLY
the registry (stdlib, instant). ``download`` / ``check-update`` lazily import the
heavy ``download_model`` paths.

Output: one JSON object per query command; ``download`` streams JSON-lines
(``{"event":"progress",...}`` then a terminal ``{"event":"done"|"error",...}``).

    backend\\python\\python.exe backend\\models_cli.py engines
    backend\\python\\python.exe backend\\models_cli.py list --engine vosk --language ru
    backend\\python\\python.exe backend\\models_cli.py available --engine whisper --model medium
    backend\\python\\python.exe backend\\models_cli.py download --engine vosk --model vosk-model-small-ru-0.22
    backend\\python\\python.exe backend\\models_cli.py check-update --engine faster-whisper --model medium
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # backend/
import engines_registry as reg


def _emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def cmd_engines(args) -> None:
    _emit({"engines": [
        {"id": eid, "label": e["label"], "implemented": reg.is_implemented(eid),
         "extra": e.get("extra", False), "default_model": e.get("default_model")}
        for eid, e in reg.ENGINES.items()
    ]})


def cmd_list(args) -> None:
    e = reg._engine(args.engine) or {}
    models = e.get("models") or {}
    out = []
    for mid in reg.list_models(args.engine, args.language):
        m = models.get(mid, {})
        out.append({
            "id": mid,
            "label": m.get("label", {"ru": mid, "en": mid}),
            "approx_mb": m.get("approx_mb"),
            "lang": m.get("lang"),
            "tier": m.get("tier"),
            "available": reg.is_available(args.engine, mid, args.language),
            "path": reg.resolve_model_path(args.engine, mid, args.language),
        })
    _emit({"engine": args.engine, "implemented": reg.is_implemented(args.engine),
           "default_model": reg.default_model(args.engine), "models": out})


def cmd_catalog(args) -> None:
    """Whole registry in one shot — engines, their models, availability — for
    the Settings UI to drive dropdowns/indicators without N subprocess calls."""
    engines = []
    for eid, e in reg.ENGINES.items():
        emodels = (reg._engine(eid) or {}).get("models") or {}
        models = []
        for mid in reg.list_models(eid):
            m = emodels.get(mid, {})
            models.append({
                "id": mid, "label": m.get("label", {"ru": mid, "en": mid}),
                "approx_mb": m.get("approx_mb"), "lang": m.get("lang"),
                "tier": m.get("tier"), "available": reg.is_available(eid, mid),
            })
        engines.append({"id": eid, "label": e["label"],
                        "implemented": reg.is_implemented(eid),
                        "extra": e.get("extra", False),
                        "default_model": e.get("default_model"), "models": models})
    _emit({"engines": engines})


def cmd_available(args) -> None:
    _emit({"engine": args.engine, "model": args.model,
           "available": reg.is_available(args.engine, args.model, args.language),
           "path": reg.resolve_model_path(args.engine, args.model, args.language)})


def cmd_resolve(args) -> None:
    _emit({"engine": args.engine, "model": args.model,
           "path": reg.resolve_model_path(args.engine, args.model, args.language),
           "intended": reg.intended_path(args.engine, args.model)})


def cmd_download(args) -> None:
    import download_model as dl

    def on_progress(pct, detail):
        _emit({"event": "progress", "percent": pct, "detail": detail})

    try:
        path = dl.download(args.engine, args.model, args.language,
                           on_progress=on_progress, force=args.force)
        _emit({"event": "done", "ok": True, "engine": args.engine,
               "model": args.model, "path": path})
    except Exception as exc:                                   # noqa: BLE001
        _emit({"event": "error", "ok": False, "error": str(exc)})
        sys.exit(1)


def cmd_check_update(args) -> None:
    import download_model as dl
    try:
        _emit({"ok": True, **dl.check_update(args.engine, args.model)})
    except Exception as exc:                                   # noqa: BLE001
        _emit({"ok": False, "error": str(exc)})
        sys.exit(1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Engine/model registry CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("engines").set_defaults(func=cmd_engines)
    sub.add_parser("catalog").set_defaults(func=cmd_catalog)

    sp = sub.add_parser("list"); sp.set_defaults(func=cmd_list)
    sp.add_argument("--engine", required=True); sp.add_argument("--language")

    for name, fn in (("available", cmd_available), ("resolve", cmd_resolve),
                     ("check-update", cmd_check_update)):
        sp = sub.add_parser(name); sp.set_defaults(func=fn)
        sp.add_argument("--engine", required=True)
        sp.add_argument("--model", required=True)
        sp.add_argument("--language")

    sp = sub.add_parser("download"); sp.set_defaults(func=cmd_download)
    sp.add_argument("--engine", required=True)
    sp.add_argument("--model", required=True)
    sp.add_argument("--language")
    sp.add_argument("--force", action="store_true")

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
