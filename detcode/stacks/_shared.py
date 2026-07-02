"""Shared template fragments for the Python web-layer stacks (flask, fastapi).

Each generated project must be self-contained, so these fragments are spliced
into the stack templates at generation time — one source of truth here, no
cross-project imports there. The page and runner match webwrap's byte for
byte in behavior: commands typed in the browser run the package CLI
in-process with output captured.
"""

PAGE_AND_RUNNER = '''PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>__PKG__</title>
<style>
  body { background: #181818; color: #ccc; font-family: ui-monospace, Consolas, monospace;
         max-width: 860px; margin: 40px auto; padding: 0 20px; font-size: 14px; }
  h1 { color: #fff; font-weight: 400; font-size: 20px; }
  .row { display: flex; gap: 8px; margin: 16px 0; }
  .tp { color: #89d185; align-self: center; }
  input { flex: 1; background: #252526; color: #ccc; border: 1px solid #3c3c3c;
          border-radius: 3px; padding: 8px 10px; font: inherit; }
  pre { background: #1e1e1e; border: 1px solid #3c3c3c; border-radius: 3px;
        padding: 12px; white-space: pre-wrap; min-height: 60px; }
  pre.err { color: #f48771; }
  .dim { color: #8a8a8a; font-size: 12.5px; }
</style></head><body>
<h1>__PKG__</h1>
<p class="dim">Commands run the package CLI (deterministic). Try: <code>--help</code></p>
<div class="row"><span class="tp">__PKG__&gt;</span>
<input id="cmd" autofocus placeholder="--help"></div>
<pre id="out"></pre>
<script>
const input = document.getElementById("cmd"), out = document.getElementById("out");
input.addEventListener("keydown", async e => {
  if (e.key !== "Enter") return;
  const res = await fetch("/api/run", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command: input.value }) });
  const data = await res.json();
  out.textContent = data.output || "(no output)";
  out.className = data.ok ? "" : "err";
});
</script></body></html>
"""


def run_command(command):
    """Run one CLI invocation in-process, capturing all output."""
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return {"ok": False, "exit": 2, "output": f"could not parse command: {exc}"}
    buffer = io.StringIO()
    code = 0
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            code = cli_main(argv) or 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:
        buffer.write(f"error: {exc}")
        code = 1
    return {"ok": code == 0, "exit": code, "output": buffer.getvalue()}
'''
