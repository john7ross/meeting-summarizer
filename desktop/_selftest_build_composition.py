"""What the distribution archive is allowed to contain.

Runs the REAL packager's file collector (no archive is written), so the contract
is checked on every suite run instead of once at release time:

  * every shipped component, launcher and doc IS present,
  * dev scaffolding, caches, user data, databases, secrets and multi-gigabyte
    downloaded assets are ABSENT.

Both classes of mistake have actually shipped: the root README and LICENCE were
missing from the archive, and a 9.5 GB downloaded model directory was not
ignored at all.

    backend\\python\\python.exe desktop\\_selftest_build_composition.py
"""
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "desktop" / "packaging"))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import build as B                               # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


items = B.collect_common(B.ROOT)
arcs = [arc for _, arc in items]
total_mb = sum(fp.stat().st_size for fp, _ in items) / (1024 * 1024)
check("collector_returns_a_payload", len(arcs) > 50,
      f"{len(arcs)} files, {total_mb:.0f} MB")
check("no_duplicate_archive_names", len(arcs) == len(set(arcs)),
      str([a for a in arcs if arcs.count(a) > 1][:3]))
check("archive_paths_are_relative",
      all(not a.startswith("/") and ":" not in a for a in arcs),
      str([a for a in arcs if ":" in a][:3]))

# -- nothing private, heavy or dev-only may ship -----------------------------
FORBIDDEN = {
    "self-tests": r"_selftest",
    "live tests": r"_livetest",
    "test fakes": r"_fake_",
    "user config/history/tokens": r"(^|/)config/",
    "produced transcripts": r"(^|/)transcripts/",
    "uploads": r"(^|/)uploads/",
    "recordings": r"(^|/)recordings/",
    "cut segments": r"(^|/)segments/",
    "logs": r"(^|/)logs/",
    "virtualenvs": r"\.venv",
    "node_modules": r"node_modules",
    "bytecode caches": r"__pycache__|\.pyc$",
    "RAG stores": r"rag_data/|rag_knowledge_base/|rag_shared/|chroma",
    "server database": r"server\.db$",
    "JWT secret": r"\.jwt_secret",
    "GGUF models": r"\.gguf$",
    "downloaded local-AI assets": r"local_ai/(engine|models)/",
    "git metadata": r"(^|/)\.git/",
    "previous builds": r"(^|/)dist/",
    "knowledge-graph cache": r"graphify-out",
    "scratch files": r"_tmp_|\.tmp$|_extract_out",
    "rendered diagrams": r"desktop/out/",
}
for label, pattern in FORBIDDEN.items():
    hits = [a for a in arcs if re.search(pattern, a)]
    check(f"never_ships_{label.replace(' ', '_').replace('/', '_')}", not hits,
          f"{len(hits)} found: {hits[:2]}" if hits else "")

# -- everything the recipient needs must be there ----------------------------
REQUIRED = {
    "desktop entry point": r"^desktop/run\.py$",
    "server entry point": r"^server/run_server\.py$",
    "auto-installer": r"^desktop/packaging/installer\.py$",
    "packager": r"^desktop/packaging/build\.py$",
    "backend processor": r"^backend/processor\.py$",
    "backend ai client": r"^backend/ai_client\.py$",
    "url downloader": r"^backend/url_download\.py$",
    "models cli": r"^backend/models_cli\.py$",
    "local ai": r"^backend/local_ai\.py$",
    "local ai watchdog": r"^backend/local_ai_watchdog\.py$",
    "gpu handoff": r"^backend/gpu_handoff\.py$",
    "rag": r"^backend/rag\.py$",
    "embeddings": r"^backend/embeddings\.py$",
    "mcp server": r"^backend/mcp_server\.py$",
    "engine registry": r"^backend/engines_registry\.py$",
    "ffmpeg": r"^backend/FFmpeg/ffmpeg\.exe$",
    "web ui": r"^server/web/",
    "root readme (en)": r"^README\.md$",
    "root readme (ru)": r"^README\.ru\.md$",
    "licence": r"^LICENSE$",
    "version manifest": r"^package\.json$",
    # (the images the READMEs embed are asserted below, derived from the READMEs
    #  themselves rather than listed here twice)
    "application icon": r"^resources/icon\.png$",
    "web favicon": r"^server/web/favicon\.png$",
    "engine compat doc (en)": r"^WHISPER_ENGINES_COMPATIBILITY\.md$",
    "engine compat doc (ru)": r"^WHISPER_ENGINES_COMPATIBILITY\.ru\.md$",
    "mcp usage guide": r"^docs/MCP_USAGE\.md$",
    "google sheets script": r"^docs/google-sheets/code\.gs$",
}
for label, pattern in REQUIRED.items():
    hits = [a for a in arcs if re.search(pattern, a)]
    check(f"ships_{label.replace(' ', '_').replace('(', '').replace(')', '')}",
          bool(hits), hits[0] if hits else "MISSING")

# -- every image the shipped READMEs embed must be IN the archive -------------
# Derived from the READMEs, not listed here: a promo GIF was added to both root
# READMEs while an archive was being written, and nothing noticed that the
# packager collects images by an explicit list - the recipient's README would
# have rendered a broken picture. Now adding an image to a README and forgetting
# the packager fails the suite instead.
_readme_images = set()
for _readme in ("README.md", "README.ru.md"):
    _text = (PROJECT_ROOT / _readme).read_text(encoding="utf-8")
    _readme_images |= set(re.findall(r'<img\s+[^>]*src="([^":]+)"', _text))
    _readme_images |= set(re.findall(r"!\[[^\]]*\]\((?!https?:)([^)\s]+)\)", _text))
_missing_images = sorted(img for img in _readme_images if img not in arcs)
check("ships_every_image_the_readmes_embed", not _missing_images,
      f"{sorted(_readme_images)}" if not _missing_images
      else f"embedded but never packaged: {_missing_images}")

# Every engine adapter must travel, or that engine silently disappears.
import engines_registry as reg                  # noqa: E402

adapters = [a for a in arcs if re.match(r"^backend/processing/engines/.*_engine\.py$", a)]
selectable = [e for e in reg.ENGINES if e != "sherpa-extra"]
check("every_engine_adapter_ships", len(adapters) >= len(selectable),
      f"{len(adapters)} adapters / {len(selectable)} selectable engines")

# The desktop application code must be complete, not partially collected.
on_disk = {str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
           for p in (PROJECT_ROOT / "desktop" / "app").rglob("*.py")
           if "__pycache__" not in str(p)}
missing_app = sorted(on_disk - set(arcs))
check("all_desktop_app_modules_ship", not missing_app,
      f"{len(on_disk)} modules" if not missing_app else str(missing_app[:3]))

# -- ARCHITECTURE's module map must not drift ---------------------------------
# The map is written by hand from each module's docstring. Nothing checked it, so
# `server/runtime.py` was added and the map simply did not mention it. Compare
# against the SHIPPED file list: whatever a recipient gets must be documented.
_arch = (PROJECT_ROOT / "desktop" / "ARCHITECTURE.md").read_text(encoding="utf-8")
_arch_ru = (PROJECT_ROOT / "desktop" / "ARCHITECTURE.ru.md").read_text(encoding="utf-8")
_documented = set(re.findall(r"`([A-Za-z0-9_./\\-]+\.(?:py|ps1))`", _arch))
_documented = {m.replace("\\", "/") for m in _documented}
# Only our own top-level packages; vendored trees and generated launchers are not
# "modules this project owns".
_owned = {a for a in arcs
          if re.match(r"^(backend|server|desktop)/", a)
          and a.endswith((".py", ".ps1"))
          and not a.startswith("backend/python/")
          # package markers carry no docstring worth documenting
          and not a.endswith("__init__.py")}
_undocumented = sorted(_owned - _documented)
check("architecture_module_map_covers_everything_shipped",
      not _undocumented,
      f"{len(_owned)} modules shipped, missing from the map: {_undocumented[:5]}")
# The Russian map is a translation, not a subset: it must list the same files.
_documented_ru = {m.replace("\\", "/")
                  for m in re.findall(r"`([A-Za-z0-9_./\\-]+\.(?:py|ps1))`", _arch_ru)}
_ru_missing = sorted(_documented - _documented_ru)
check("architecture_ru_map_matches_the_english_one",
      not _ru_missing,
      f"only in the English map: {_ru_missing[:5]}")

# -- launchers the packager ships ---------------------------------------------
# NOTHING here may delete anything: launcher_items() returns files that live in
# the repository root. A cleanup loop here once removed `fp.parent` and took the
# whole project with it. See the guard checks at the end of this file.
for variant, expected in (("full", {"RUN.bat", "SERVER.bat"}),
                          ("min", {"RUN.bat", "SERVER.bat", "INSTALL.bat"})):
    names = {arc for _, arc in B.launcher_items(variant)}
    check(f"{variant}_variant_has_its_launchers", expected <= names, str(sorted(names)))

min_install = next((fp for fp, arc in B.launcher_items("min") if arc == "INSTALL.bat"), None)
installer_text = min_install.read_text(encoding="utf-8") if min_install else ""
check("min_installer_calls_the_interactive_installer",
      "packaging\\installer.py" in installer_text or "packaging/installer.py" in installer_text,
      installer_text.replace("\r\n", " ")[:70])
check("min_installer_is_not_a_blind_pip_install",
      "pip install -r" not in installer_text,
      "a bare requirements install would ignore the machine")

# ── nothing around launcher_items() may delete anything ──────────────────────
# launcher_items() returns files that live in the REPOSITORY ROOT. Cleanup code
# written when it still used a temp directory deleted `fp.parent` - and after the
# switch that was the project root. It wiped the working tree, .git and both
# release archives, twice, silently, because of `ignore_errors=True`.
_DELETE_CALL = re.compile(r"\brmtree\s*\(")
_packaging = PROJECT_ROOT / "desktop" / "packaging" / "build.py"
_callers = [_packaging] + sorted((PROJECT_ROOT / "desktop").glob("_selftest_*.py"))


def _executable_lines(path):
    """Source with comments dropped, so prose about the bug is not the bug."""
    return "\n".join(ln.split("#", 1)[0] for ln in
                     path.read_text(encoding="utf-8").splitlines())


_armed = [f.name for f in _callers
          if "launcher_items" in _executable_lines(f)
          and _DELETE_CALL.search(_executable_lines(f))]
check("nothing_deletes_around_launcher_items", not _armed,
      f"these call launcher_items AND rmtree: {_armed}")
check("the_packager_deletes_nothing_but_its_own_output",
      not _DELETE_CALL.search(_executable_lines(_packaging)),
      "building is read-only over the source tree")
# Every remaining rmtree in the suite must target a directory the test itself
# created. A delete aimed at anything else has no business in a test.
_wild = []
for _f in _callers:
    for _i, _ln in enumerate(_executable_lines(_f).splitlines(), 1):
        _m = _DELETE_CALL.search(_ln)
        if _m and not re.search(r"rmtree\s*\(\s*(tmp|_dir|tmpdir|out|root)\b", _ln):
            _wild.append(f"{_f.name}:{_i}")
check("deletes_only_target_a_temp_dir_the_test_made", not _wild,
      f"unrecognised delete target: {_wild}")

# ── our directory names must not be applied to somebody else's tree ──────────
# IGNORE_NAMES lists OUR layout: config, logs, uploads, dist. Third-party
# packages use those words too, and the filter was applied to vendored trees as
# well - so `opentelemetry/proto/collector/logs` never shipped and every full
# build died on `import chromadb`. The knowledge base was dead in the box.
for _name in sorted(B.IGNORE_NAMES - B.JUNK_NAMES):   # JUNK_NAMES is junk everywhere
    check(f"vendored_tree_keeps_a_dir_named_{_name.replace('.', '')}",
          not B._ignored(_name, vendored=True),
          "a third-party package may legitimately have this directory")
    check(f"our_own_tree_still_drops_{_name.replace('.', '')}",
          B._ignored(_name, vendored=False),
          "user data and build output must never ship")
for _junk in ("__pycache__", ".git"):
    check(f"{_junk.strip('._')}_is_junk_in_every_tree",
          B._ignored(_junk, vendored=True) and B._ignored(_junk, vendored=False))
# And the concrete casualty, asserted against the real collected file list.
_full_arcs = {arc for _fp, arc in B.collect_full_runtime(PROJECT_ROOT)[0]}
check("the_module_that_killed_chromadb_ships",
      any(a.endswith("opentelemetry/proto/collector/logs/v1/logs_service_pb2.py")
          for a in _full_arcs),
      "without it the full build cannot import chromadb at all")

# ── copyleft we redistribute must carry its licence INTO the archive ────────
# The archives ship an ffmpeg built with --enable-gpl --enable-version3 (GPLv3)
# and, in full, LGPL Qt. A copyleft binary distributed without its licence text
# is a real violation, and no functional test can see it: the product works
# perfectly either way. Assert on the COLLECTED FILE LIST, which is what the zip
# is written from.
_common_arcs = {arc for _fp, arc in B.collect_common(PROJECT_ROOT)}

check("notices_ship_in_both_variants",
      {"THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.ru.md"} <= _common_arcs,
      "a stranger must be able to see what is bundled and under what terms")
check("security_policy_ships",
      {"SECURITY.md", "SECURITY.ru.md"} <= _common_arcs)
check("licence_texts_ship",
      {"licenses/GPL-3.0.txt", "licenses/LGPL-3.0.txt",
       "licenses/Apache-2.0.txt"} <= _common_arcs,
      "a link to gnu.org is not compliance - the text travels in the archive")
check("gpl_text_sits_beside_the_ffmpeg_binary",
      "backend/FFmpeg/LICENSE-GPL-3.0.txt" in _common_arcs
      and "backend/FFmpeg/ffmpeg.exe" in _common_arcs,
      "recipients look next to the executable, not elsewhere in the zip")
check("ffmpeg_build_configuration_is_recorded",
      "backend/FFmpeg/BUILD-CONFIGURATION.txt" in _common_arcs,
      "the --enable-gpl flag is the EVIDENCE for which licence applies")

# Every relative link in a doc that SHIPS must resolve inside the archive.
# Adding CONTRIBUTING.md to the README doc map without adding it to the collected
# file list produced two dangling links in the built zip - invisible in the repo,
# where the file obviously exists.
def _dangling_links_in_shipped_docs():
    """Relative links in shipped .md files that do NOT resolve inside the archive.

    This file's check() takes a BOOLEAN, never a callable - passing the function
    itself makes the check always-truthy and it never runs. That is how this very
    check first "passed" while CONTRIBUTING.md was missing from the archive.
    """
    import re
    collected = B.collect_common(PROJECT_ROOT)
    shipped = {arc for _fp, arc in collected}
    src_of = {arc: fp for fp, arc in collected}
    dangling = []
    for arc in sorted(a for a in shipped if a.endswith(".md")):
        base = arc.rsplit("/", 1)[0] if "/" in arc else ""
        for m in re.finditer(r"\[[^\]]*\]\(([^)\s]+)\)",
                             src_of[arc].read_text(encoding="utf-8-sig")):
            t = m.group(1).split("#")[0]
            if not t or t.startswith(("http", "mailto:")):
                continue
            parts = []
            for seg in (f"{base}/{t}" if base else t).split("/"):
                if seg == "..":
                    if parts:
                        parts.pop()
                elif seg not in (".", ""):
                    parts.append(seg)
            target = "/".join(parts)
            # A link may point at a DIRECTORY (e.g. "licenses/"). A zip has no
            # directory entries, so match it by prefix instead of by exact name.
            if target in shipped:
                continue
            if any(s.startswith(target + "/") for s in shipped):
                continue
            dangling.append(f"{arc} -> {m.group(1)}")
    return dangling


_dangling = _dangling_links_in_shipped_docs()
_shipped_md = len([a for _f, a in B.collect_common(PROJECT_ROOT) if a.endswith(".md")])
check("shipped_docs_have_no_dangling_links", not _dangling,
      f"{_shipped_md} shipped docs" if not _dangling else str(_dangling[:4]))

# ── the release notes are pasted into the GitHub release body ────────────────
# A release body does NOT resolve relative links against the repository root -
# it resolves them against the release URL, so every one of them 404s. The
# language switcher did exactly that on the published v1.2.1 release. These two
# files are the only docs that get pasted somewhere else, so they must carry
# absolute links; everything shipped inside the archives stays relative and is
# policed by shipped_docs_have_no_dangling_links above.
_rel_link = re.compile(r"\]\((?!https?:|#)([^)]+)\)")
for _rn in ("RELEASE_NOTES.md", "RELEASE_NOTES.ru.md"):
    _p = PROJECT_ROOT / _rn
    _bad = _rel_link.findall(_p.read_text(encoding="utf-8")) if _p.is_file() else ["<missing>"]
    check(f"{_rn.lower().replace('.', '_')}_links_are_absolute", not _bad,
          f"relative links 404 inside a GitHub release body: {_bad[:4]}")

# ── one version, everywhere it is claimed ────────────────────────────────────
# package.json is the single source of truth and the code reads it: version.py,
# server/api/main.py and packaging/build.py all derive from it. The DOCS do not
# — the README badge, the two desktop READMEs and PROJECT_OVERVIEW carry the
# number as text, and nothing compared them with anything. A bump that updates
# the manifest and forgets a badge ships an archive named v1.3.0 whose README
# says 1.2.1, and the only way that is ever noticed is a user asking which one
# is true.
_manifest_version = json.loads(
    (PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
_VERSION_CLAIMS = (
    ("README.md", re.compile(r"version-([0-9]+\.[0-9]+\.[0-9]+)-blue")),
    ("README.ru.md", re.compile(r"version-([0-9]+\.[0-9]+\.[0-9]+)-blue")),
    ("desktop/README.md", re.compile(r"^Version ([0-9]+\.[0-9]+\.[0-9]+)\.", re.M)),
    ("desktop/README.ru.md", re.compile(r"^Версия ([0-9]+\.[0-9]+\.[0-9]+)\.", re.M)),
    ("PROJECT_OVERVIEW.md", re.compile(r"Версия ([0-9]+\.[0-9]+\.[0-9]+)\.")),
)
_version_mismatch = []
for _rel, _pattern in _VERSION_CLAIMS:
    _path = PROJECT_ROOT / _rel
    _found = _pattern.findall(_path.read_text(encoding="utf-8")) if _path.is_file() \
        else []
    if not _found:
        _version_mismatch.append(f"{_rel}: no version claim found")
    elif any(v != _manifest_version for v in _found):
        _version_mismatch.append(f"{_rel}: {_found} != {_manifest_version}")
check("every_documented_version_matches_package_json", not _version_mismatch,
      f"v{_manifest_version} in {len(_VERSION_CLAIMS)} docs"
      if not _version_mismatch else str(_version_mismatch))

# The release notes must actually describe the version being shipped: a build
# whose notes stop at the previous release tells a user nothing about what they
# just installed.
for _rn in ("RELEASE_NOTES.md", "RELEASE_NOTES.ru.md"):
    _text = (PROJECT_ROOT / _rn).read_text(encoding="utf-8")
    _covered = _manifest_version in _text
    check(f"{_rn.lower().replace('.', '_')}_covers_the_current_version", _covered,
          f"v{_manifest_version}" if _covered else f"no section for v{_manifest_version}")

_lic_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
check("licence_file_is_the_bare_mit_template",
      _lic_text.lstrip().startswith("MIT License")
      and len(_lic_text.splitlines()) <= 25
      and "font" not in _lic_text.lower() and "third-party" not in _lic_text.lower(),
      "extra prose in LICENSE makes GitHub report NOASSERTION instead of MIT")
check("licence_names_a_real_holder",
      "contributors" not in _lic_text.lower().split("copyright")[1].split("\n")[0],
      "a placeholder holder leaves the copyright legally undefined")


# ── our scaffolding globs must never touch a VENDORED tree ──────────────────
# "_fake_*" exists to keep desktop/_fake_processor.py out of a release. Applied
# to backend/python it also deleted torch/_subclasses/_fake_tensor_utils.py, so
# `import torch` raised ModuleNotFoundError in every full build ever shipped and
# the offline variant could not transcribe at all. Nothing in the repo showed
# it: the file is present on disk and only missing from the archive.
check("junk_globs_are_a_subset_of_all_globs",
      set(B.JUNK_GLOBS) <= set(B.IGNORE_GLOBS),
      "the vendored list must not invent patterns of its own")
check("project_globs_are_not_applied_to_vendored_trees",
      all(not B._ignored(n, vendored=True) for n in
          ("_fake_tensor_utils.py", "conftest.py", "_pip_wrapper.py",
           "_selftest_helper.py", "pytest.ini", "test_setup.py")),
      "third-party packages legitimately use these names for real code")
check("project_globs_still_apply_to_our_own_tree",
      all(B._ignored(n) for n in
          ("_fake_processor.py", "_selftest_ui.py", "_livetest_vosk.py",
           "conftest.py", "pytest.ini")),
      "our dev scaffolding must still be excluded")
check("real_junk_is_dropped_everywhere",
      all(B._ignored(n, vendored=True) and B._ignored(n) for n in
          ("x.pyc", "x.pyo", "build.log", "x.tmp")),
      "junk must go from vendored trees too")

# The concrete file that broke it, asserted by name against the real runtime.
_torch_victim = PROJECT_ROOT / "backend/python/Lib/site-packages/torch/_subclasses/_fake_tensor_utils.py"
if _torch_victim.exists():
    check("the_torch_module_that_broke_full_builds_now_ships",
          not B._ignored(_torch_victim.name, vendored=True),
          _torch_victim.name)
else:
    check("the_torch_module_that_broke_full_builds_now_ships", True,
          "torch not installed in this runtime - glob rule still asserted above")

print("\n".join(results))
ok_all = bool(results) and all(r.startswith("PASS") for r in results)
print("SUMMARY " + ("ALL_PASS" if ok_all else "HAS_FAILURES")
      + f" ({len(results)} checks)")
sys.exit(0 if ok_all else 1)
