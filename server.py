#!/usr/bin/env python3
"""Minimal HTTP backend for Skill Control Console."""

import json, os, subprocess, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

AGENT_PLATFORM = Path("/media/yhr/2T/files/projects/test")
AGENT_SKILLS = Path.home() / ".agents" / "repos" / "agent-skills" / "skills"
UPSTREAM = AGENT_PLATFORM / "upstream"
DISABLED = UPSTREAM / ".disabled"
SCRIPTS = AGENT_PLATFORM / "scripts"
PORT = 8765

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.serve_file(self._www("index.html"), "text/html")
        if path == "/state.json":
            return self.serve_json(self.collect_state())
        if path == "/api/skill-md":
            return self.serve_skill_md(self.path)
        if path == "/favicon.ico":
            self.send_response(200); self.send_header("Content-Type","image/x-icon"); self.send_header("Content-Length","0"); self.end_headers()
        self.send_error(404)

    def do_POST(self):
        body = self._read_body()
        try: data = json.loads(body) if body else {}
        except: data = {}
        if self.path == "/api/enable": return self._skill_cmd("enable", data)
        if self.path == "/api/disable": return self._skill_cmd("disable", data)
        if self.path == "/api/sync": return self._run_sync()
        if self.path == "/api/save-skill": return self._save_skill(data)
        self.send_error(404)

    def serve_file(self, p, mime):
        if not p.exists(): return self.send_error(404)
        content = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def serve_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def serve_skill_md(self, raw_path):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(raw_path).query)
        name = qs.get("name", [""])[0]
        uid = qs.get("upstream", [""])[0]
        stype = qs.get("type", ["upstream"])[0]
        if stype == "self":
            f = AGENT_SKILLS / name / "SKILL.md"
        else:
            f = UPSTREAM / uid / name / "SKILL.md"
            if not f.exists(): f = DISABLED / uid / name / "SKILL.md"
        if f.exists(): return self._ok({"content": f.read_text("utf-8"), "path": str(f)})
        return self._err(f"Not found: {f}")

    def _skill_cmd(self, action, data):
        name = data.get("name", ""); uid = data.get("upstream", "")
        agent = data.get("agent", "")
        if not name or not uid: return self._err("Missing name/upstream")
        script = SCRIPTS / f"{action}_upstream_skill.sh"
        args = ["bash", str(script), "--upstream", uid, "--skill", name]
        if agent: args = ["bash", str(script), "--agent", agent, "--upstream", uid, "--skill", name]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=30)
            subprocess.run(["bash", str(SCRIPTS / "install_links.sh")], capture_output=True, timeout=30)
            return self._ok({"stdout": r.stdout, "stderr": r.stderr})
        except Exception as e:
            return self._err(str(e))

    def _run_sync(self):
        import yaml
        env = {**os.environ, "HOME": str(Path.home()), "GIT_HTTP_PROXY": "http://127.0.0.1:7890"}

        # First run sync_all_upstreams.py
        try:
            r = subprocess.run([sys.executable, str(SCRIPTS / "sync_all_upstreams.py")],
                               capture_output=True, text=True, timeout=180,
                               cwd=str(AGENT_PLATFORM), env=env)
        except subprocess.TimeoutExpired:
            return self._err("Sync overall timed out (>180s)")
        except Exception as e:
            return self._err(f"Sync failed to start: {e}")

        # Parse output to find per-repo status
        output = "=== Upstream Sync ===\n\n"
        lines = (r.stdout or "").split("\n")
        current_repo = ""
        has_error = False
        for line in lines:
            if "Synced upstream repo:" in line or "Upstream repo:" in line:
                current_repo = line.strip()
                has_error = False
            if current_repo:
                output += f"  {line}\n"
            else:
                output += f"  {line}\n"
            if "FAILED" in line or "Error" in line or "failed" in line or "timeout" in line.lower():
                has_error = True
                output += f"    ⚠️  ABOVE REPO ({current_repo or 'unknown'}) MAY HAVE FAILED\n"

        if r.returncode != 0:
            output += f"\n⚠️  Sync script exited with code {r.returncode}\n"
        if r.stderr:
            output += f"\n=== Stderr ===\n{r.stderr[:2000]}\n"

        # Run install_links.sh
        try:
            r2 = subprocess.run(["bash", str(SCRIPTS / "install_links.sh")],
                               capture_output=True, text=True, timeout=30,
                               cwd=str(AGENT_PLATFORM), env=env)
            output += "\n=== Install Links ===\n"
            output += (r2.stdout or "(no stdout)")[:2000]
        except Exception as e:
            output += f"\n⚠️  install_links.sh failed: {e}"

        return self._ok({"stdout": output})

    def _save_skill(self, data):
        name = data.get("name",""); uid = data.get("upstream","")
        content = data.get("content",""); stype = data.get("type","upstream")
        if not name or not content: return self._err("Missing name/content")
        if stype == "self":
            f = AGENT_SKILLS / name / "SKILL.md"
        else:
            f = UPSTREAM / uid / name / "SKILL.md"
            if not f.exists(): f = DISABLED / uid / name / "SKILL.md"
        if not f.exists(): return self._err(f"Not found: {uid}/{name}")
        f.write_text(content, "utf-8")
        return self._ok({"saved": str(f)})

    def collect_state(self):
        import yaml
        manifest = yaml.safe_load((AGENT_PLATFORM / "migration" / "upstream-manifest.yaml").read_text())
        dy = yaml.safe_load((AGENT_PLATFORM / "state" / "disabled-upstreams.yaml").read_text())
        am = yaml.safe_load((Path.home() / ".agents" / "repos" / "agent-skills" / "manifest.yaml").read_text())
        data = {"upstreams": [], "self_owned": [], "runtimes": ["agents", "claude", "hermes"]}
        d = dy.get("disabled", {})
        gdu = d.get("upstreams", []); gds = d.get("skills", {}); ad = d.get("agents", {})
        for u in manifest.get("upstreams", []):
            if u["id"] == "agent-skills":
                continue  # managed_install, skills shown under self-owned
            uid = u["id"]; pub = u.get("label", uid); skills = []
            for s in u.get("tracked_skills", []):
                sn = s["name"]; gd = uid in gdu or sn in gds.get(uid, [])
                pa = {}
                for a in data["runtimes"]:
                    ada = ad.get(a, {}); ec = ada.get("enabled", {})
                    if uid in ec.get("upstreams", []) or sn in ec.get("skills", {}).get(uid, []):
                        pa[a] = "whitelist"
                    elif uid in ada.get("upstreams", []) or sn in ada.get("skills", {}).get(uid, []):
                        pa[a] = "disabled"
                    else:
                        pa[a] = "disabled" if gd else "enabled"
                skills.append({"name": sn, "upstream": uid, "publisher": pub,
                               "globally_disabled": gd, "per_agent": pa})
            data["upstreams"].append({"id": uid, "publisher": pub, "repo": u.get("repo", ""),
                "tracked_count": len(skills), "globally_disabled": uid in gdu, "skills": skills})
        for s in am.get("skills", []):
            en = s.get("enabled", True)
            data["self_owned"].append({"name": s["name"], "publisher": "agent-skills (self)",
                "category": s.get("category", ""), "enabled": en,
                "globally_disabled": not en,
                "per_agent": {a: "enabled" if en else "disabled" for a in data["runtimes"]}})
        return data

    def _www(self, name): return Path(__file__).parent / name
    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""
    def _ok(self, data): return self.serve_json({"ok": True, **data})
    def _err(self, msg): self.send_response(400); return self.serve_json({"ok": False, "error": msg})

if __name__ == "__main__":
    print(f"Skill Console → http://localhost:{PORT}")
    HTTPServer(("", PORT), Handler).serve_forever()
