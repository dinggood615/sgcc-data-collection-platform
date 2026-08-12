#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, os, sqlite3, subprocess, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST, PORT, BACKEND_PORT = "127.0.0.1", int(os.getenv("MODEL_DISPATCHER_PORT", "8083")), 18080
LLAMA_SERVER = os.getenv("LLAMA_SERVER", "/opt/llama.cpp/llama-server")
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/opt/local-llm/models"))
CACHE_PATH = os.getenv("MODEL_DISPATCHER_CACHE", "/opt/local-llm/dispatcher-cache.sqlite3")
IDLE_SECONDS = int(os.getenv("MODEL_IDLE_SECONDS", "120"))
MODELS = {"qwen3-0.6b": MODEL_DIR / "qwen3-0.6b-q8_0.gguf", "qwen3-1.7b": MODEL_DIR / "qwen3-1.7b-q4_k_m.gguf"}
lock, state_lock = threading.Lock(), threading.Lock()
backend, active_model, last_used = None, "", 0.0

def cache_db():
    db = sqlite3.connect(CACHE_PATH, timeout=10); db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS responses(key TEXT PRIMARY KEY, body BLOB NOT NULL, created_at INTEGER NOT NULL)")
    return db

def health():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{BACKEND_PORT}/health", timeout=1) as response: return response.status == 200
    except Exception: return False

def stop_backend():
    global backend, active_model
    if backend and backend.poll() is None:
        backend.terminate()
        try: backend.wait(timeout=15)
        except subprocess.TimeoutExpired: backend.kill(); backend.wait(timeout=5)
    backend, active_model = None, ""

def ensure_backend(model):
    global backend, active_model, last_used
    requested = model if model in MODELS else "qwen3-1.7b"
    if requested == active_model and backend and backend.poll() is None and health(): last_used = time.time(); return
    stop_backend(); path = MODELS[requested]
    if not path.is_file(): raise RuntimeError(f"model not installed: {requested}")
    backend = subprocess.Popen([LLAMA_SERVER, "-m", str(path), "--host", HOST, "--port", str(BACKEND_PORT), "--ctx-size", "2048", "--threads", "3", "--threads-batch", "3", "--parallel", "1", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--jinja", "--reasoning", "off", "--no-webui"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_model = requested
    for _ in range(120):
        if backend.poll() is not None: raise RuntimeError("llama backend exited during startup")
        if health(): last_used = time.time(); return
        time.sleep(1)
    stop_backend(); raise TimeoutError("llama backend startup timeout")

def idle_monitor():
    while True:
        time.sleep(15)
        if backend and time.time() - last_used > IDLE_SECONDS and lock.acquire(blocking=False):
            try:
                if time.time() - last_used > IDLE_SECONDS: stop_backend()
            finally: lock.release()

def dispatch(payload):
    global last_used
    request_data = json.loads(payload); model = str(request_data.get("model") or "qwen3-1.7b"); key = hashlib.sha256(payload).hexdigest()
    with cache_db() as db: row = db.execute("SELECT body FROM responses WHERE key=?", (key,)).fetchone()
    if row: return row[0]
    with lock:
        ensure_backend(model); forwarded = dict(request_data); forwarded["model"] = active_model
        request = urllib.request.Request(f"http://127.0.0.1:{BACKEND_PORT}/v1/chat/completions", data=json.dumps(forwarded, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=150) as response: body = response.read()
        last_used = time.time()
        with cache_db() as db:
            db.execute("INSERT OR REPLACE INTO responses(key,body,created_at) VALUES(?,?,?)", (key, body, int(time.time())))
            db.execute("DELETE FROM responses WHERE created_at<?", (int(time.time()) - 30 * 86400,))
        return body

class Handler(BaseHTTPRequestHandler):
    def send_body(self, status, body):
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health": self.send_body(200, json.dumps({"status":"ok","model":active_model or None,"busy":lock.locked()}).encode())
        else: self.send_body(404, b'{"error":"not found"}')
    def do_POST(self):
        if self.path != "/v1/chat/completions": self.send_body(404, b'{"error":"not found"}'); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2 * 1024 * 1024: raise ValueError("invalid request size")
            self.send_body(200, dispatch(self.rfile.read(length)))
        except Exception as exc: self.send_body(503, json.dumps({"error":{"message":type(exc).__name__}}).encode())
    def log_message(self, fmt, *args): print(f"{self.address_string()} {fmt % args}", flush=True)

threading.Thread(target=idle_monitor, daemon=True).start()
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
