"""Every shipped browser script must PARSE.

A stray comma after a class method is a syntax error that kills the whole file:
``api.js`` stopped defining ``api``, and the cabinet then rendered a permanent
"Загрузка встреч..." with an empty console - while 87 source-substring checks
stayed green, because the text they look for was all present. Substring checks
cannot see a broken file; a parser can.

Also verifies that every function the HTML calls through ``onclick`` actually
exists, since an undefined handler is a silently dead button.

    backend\\python\\python.exe server\\_selftest_web_js.py
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB = PROJECT_ROOT / "server" / "web"

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


scripts = sorted(WEB.glob("js/*.js"))
check("web_scripts_found", len(scripts) >= 3, f"{len(scripts)} files")

node = shutil.which("node")
if node:
    for script in scripts:
        # `node --check` parses without executing: no browser, no side effects.
        proc = subprocess.run([node, "--check", str(script)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        lines = [ln.strip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
        # node prints the offending file:line, then the source, then the error and
        # its own version banner; report the error itself, not the last line.
        err = next((ln for ln in lines if "Error" in ln), lines[0] if lines else "")
        where = next((ln for ln in lines if str(script.name) in ln), "")
        check(f"parses_{script.name}", proc.returncode == 0,
              f"{err} @ {where}"[:160] if proc.returncode else "")
else:
    check("node_available_for_syntax_check", False,
          "node not found - browser scripts were NOT parsed; install Node or "
          "run this check in CI")

# Behaviour, not text: run the real api.js in a sandbox and check the 401 policy.
# Signing in with wrong credentials returns 401, and the global "session expired"
# handler navigated to '/', so the error message showed for a fraction of a second
# and the login form reset itself with nothing displayed.
behaviour = Path(__file__).with_name("_selftest_web_behaviour.cjs")
if node and behaviour.exists():
    proc = subprocess.run([node, str(behaviour), str(WEB / "js" / "api.js")],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(PROJECT_ROOT))
    payload = (proc.stdout or "").strip().splitlines()
    try:
        import json
        cases = json.loads(payload[-1]) if payload else []
    except ValueError:
        cases = []
    check("behaviour_harness_ran", bool(cases),
          (proc.stderr or proc.stdout or "")[-160:] if not cases else f"{len(cases)} cases")
    for name, ok, detail in cases:
        check(name, ok, "" if ok else str(detail))
else:
    check("behaviour_harness_present", False, "missing _selftest_web_behaviour.cjs or node")

# Buttons wired through onclick="foo(...)" need foo to exist somewhere.
all_js = "\n".join(p.read_text(encoding="utf-8") for p in scripts)
defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", all_js))
defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", all_js))
handlers = set()
for page in sorted(WEB.glob("*.html")):
    handlers |= set(re.findall(r'onclick="(?:event\.stopPropagation\(\);\s*)?([A-Za-z_$][\w$]*)\(',
                               page.read_text(encoding="utf-8")))
handlers |= set(re.findall(r'onclick=\\?"(?:event\.stopPropagation\(\);\s*)?([A-Za-z_$][\w$]*)\(',
                           all_js))
missing = sorted(h for h in handlers if h not in defined)
check("every_onclick_handler_exists", not missing, str(missing[:5]))

# Element ids referenced by getElementById must exist in some page, or the
# listener silently never attaches (and can abort the rest of the init).
ids_in_html = set()
for page in sorted(WEB.glob("*.html")):
    ids_in_html |= set(re.findall(r'id="([^"]+)"', page.read_text(encoding="utf-8")))
ids_in_js = set(re.findall(r'id="([^"]+)"', all_js))     # ids the scripts render
wired = set(re.findall(r"getElementById\('([^']+)'\)\.addEventListener", all_js))
absent = sorted(i for i in wired if i not in ids_in_html and i not in ids_in_js)
check("listeners_target_existing_elements", not absent, str(absent[:5]))

print("\n".join(results))
ok_all = bool(results) and all(r.startswith("PASS") for r in results)
print("SUMMARY " + ("ALL_PASS" if ok_all else "HAS_FAILURES")
      + f" ({len(results)} checks)")
sys.exit(0 if ok_all else 1)
