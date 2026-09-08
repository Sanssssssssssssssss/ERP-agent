"""Local, explicitly scripted chat-completions fixture. No paid API or ERP writes.

Run with --mode read or --mode approval; reset by restarting this process.
The approval plan validates a proposed write but never calls an execution tool.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def plan(mode: str) -> list[tuple[str, dict]]:
    reads = [("get_current_time", {}), ("mcp_odoo_read_record", {
        "model": "res.partner", "record_id": 10, "fields": ["name", "ref", "comment"],
    })]
    if mode == "approval":
        return reads + [
            ("configure_odoo_tools", {"capabilities": ["actions"]}),
            ("mcp_odoo_validate_write", {
                "model": "res.partner", "operation": "write", "record_ids": [10],
                "values": {"comment": "Desktop offline approval check - do not execute"},
            }),
        ]
    return reads


def serve(port: int, mode: str, delay: float = 0) -> None:
    steps = plan(mode)
    lock = threading.Lock()
    request_count = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def do_POST(self) -> None:
            nonlocal request_count
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8_000_000:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(length))
            time.sleep(delay)
            with lock:
                index = request_count
                request_count += 1
            if index < len(steps):
                name, arguments = steps[index]
                advertised = {item["function"]["name"] for item in request.get("tools", [])}
                if name not in advertised:
                    self.send_error(422, "Scripted tool was not advertised")
                    return
                delta = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": f"offline-tool-{index + 1}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}
                finish = "tool_calls"
            else:
                if mode == "approval":
                    last_message = request.get("messages", [{}])[-1]
                    if "desktop host has completed human approval" not in str(last_message.get("content", "")):
                        self.send_error(422, "Missing durable host approval notification")
                        return
                delta = {"role": "assistant", "content": "离线脚本检查完成；本轮未执行 ERP 写入。"}
                finish = "stop"
            envelope = {"id": f"offline-response-{index + 1}", "object": "chat.completion.chunk",
                        "created": int(time.time()), "model": "local-scripted-acceptance"}
            chunks = [
                {**envelope, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**envelope, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                           "prompt_tokens_details": {"cached_tokens": 40},
                           "completion_tokens_details": {"reasoning_tokens": 5}}},
            ]
            body = "".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
            body = (body + "data: [DONE]\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            print(json.dumps({"fixture_request": index + 1, "mode": mode, "finish": finish}), flush=True)

    print(f"SCRIPTED FIXTURE ONLY: http://127.0.0.1:{port}/v1 ({mode})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--mode", choices=("read", "approval"), default="read")
    parser.add_argument("--delay", type=float, default=0, help="Response delay for cancellation acceptance, in seconds")
    args = parser.parse_args()
    if not 0 <= args.delay <= 60:
        parser.error("delay must be between 0 and 60 seconds")
    serve(args.port, args.mode, args.delay)
