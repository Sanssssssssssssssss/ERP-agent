"""One host-owned local selector per run, surviving approval worker exits."""
import hmac
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, ProxyHandler, build_opener


def decide(endpoint, packet):
    request = Request(f"http://127.0.0.1:{endpoint['port']}/select",
        data=json.dumps(packet, ensure_ascii=False).encode(),
        headers={'Authorization': endpoint['token'], 'Content-Type': 'application/json'})
    # Local IPC must not inherit a user's HTTP proxy.
    with build_opener(ProxyHandler({})).open(request) as response:
        return json.load(response)


class SelectorService:
    def __init__(self, config_path, directory, run_id):
        config = json.loads(Path(config_path).read_text(encoding='utf8'))
        directory.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(32)
        env = {k: v for k, v in os.environ.items()
               if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PYTHONUTF8', 'CUDA_VISIBLE_DEVICES'}}
        env['ERP_SELECTOR_SERVICE_TOKEN'] = token
        self.stderr = (directory / 'service.stderr.log').open('ab')
        self.process = None
        started = time.perf_counter()
        try:
            self.process = subprocess.Popen([config['python'], '-P', '-u', '-X', 'utf8',
                str(Path(__file__).resolve()), str(config_path), run_id], env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            ready = json.loads(self.process.stdout.readline())
            if ready.get('event') != 'ready' or ready.get('run_id') != run_id:
                raise ValueError('Invalid selector startup')
            self.endpoint = {'port': ready['port'], 'token': token, 'run_id': run_id,
                             'config_sha256': hashlib.sha256(Path(config_path).read_bytes()).hexdigest()}
            ready['startup_ms'] = round((time.perf_counter() - started) * 1000, 2)
            (directory / 'service.json').write_text(json.dumps(ready), encoding='utf8')
        except Exception:
            self.close()
            raise

    def close(self):
        if self.process is not None:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.stderr.close()


def serve(select, run_id):
    token = os.environ.pop('ERP_SELECTOR_SERVICE_TOKEN')
    inference_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # No credentials or private states in HTTP logs.

        def do_POST(self):
            if self.path != '/select' or not hmac.compare_digest(self.headers.get('Authorization', ''), token):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4 * 1024 * 1024:
                    raise ValueError('Invalid IPC packet size')
                packet = json.loads(self.rfile.read(length))
                if packet.get('run_id') != run_id:
                    raise ValueError('Selector run mismatch')
                with inference_lock:
                    result = select(packet)
            except (ValueError, KeyError, TypeError, RuntimeError):
                self.send_error(400)
                return
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # Daemon handlers let host shutdown close IPC without waiting for an in-flight GPU call.
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(json.dumps({'event': 'ready', 'port': server.server_port, 'run_id': run_id,
                          'pid': os.getpid()}), flush=True)
        sys.stdin.read()  # Owning host exit closes the pipe, including crashes.
        server.shutdown()


if __name__ == '__main__':
    from contextlib import redirect_stdout
    import importlib.util
    spec = importlib.util.spec_from_file_location('laya_worker', Path(__file__).with_name('laya_worker.py'))
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    with redirect_stdout(sys.stderr):
        router = worker.CapabilityRouter(config['model'], device='cuda')
    serve(lambda packet: worker.select_packet(router, packet), sys.argv[2])
