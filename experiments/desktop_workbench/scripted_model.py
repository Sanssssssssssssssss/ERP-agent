"""Local, explicitly scripted chat-completions fixture. No paid API or ERP writes.

Run with --mode read, approval, or conversation; reset by restarting this process.
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


def _latest_user_turn(messages: list[object]) -> tuple[str, list[object]]:
    user_index = next(
        (index for index in range(len(messages) - 1, -1, -1)
         if isinstance(messages[index], dict) and messages[index].get("role") == "user"),
        None,
    )
    if user_index is None:
        return "", []
    return str(messages[user_index].get("content", "")), messages[user_index + 1:]


def _conversation_response(request: dict) -> tuple[dict, str]:
    messages = request.get("messages", [])
    current_user, turn_messages = _latest_user_turn(messages)
    if "FIXTURE_PROPOSE" in current_user and not any(
        isinstance(message, dict) and message.get("role") == "tool" for message in turn_messages
    ):
        return ({"role": "assistant", "tool_calls": [{
            "index": 0, "id": "offline-propose-1", "type": "function",
            "function": {"name": "propose_business", "arguments": json.dumps({
                "type": "sale_invoice", "title": "Nimbus demo", "goal": "只读核对 Nimbus 销售与客户发票",
            }, ensure_ascii=False)},
        }]}, "tool_calls")
    if "FIXTURE_SLOW" in current_user:
        text = "\n".join(f"第 {index} 段：离线流式会话压力测试内容。" for index in range(1, 101))
    else:
        text = (
            "离线会话脚本已收到你的业务目标。\n\n"
            "1. 我会先确认客户、订单与发票的上下文。\n"
            "2. 只读核对会保留当前记录，不会直接执行 ERP 写入。\n"
            "3. 需要变更时，我会先展示可审阅的业务提案。\n\n"
            "当前结果：这是本地 scripted fixture，便于验证分段输出、取消和滚动行为。"
        )
    return {"role": "assistant", "content": text}, "stop"


def serve(port: int, mode: str, delay: float = 0, chunk_delay: float = 0) -> None:
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
            if mode == "conversation":
                current_user, turn_messages = _latest_user_turn(request.get("messages", []))
                if "FIXTURE_PROPOSE" in current_user and not any(
                    isinstance(message, dict) and message.get("role") == "tool"
                    for message in turn_messages
                ) and "propose_business" not in {
                    item.get("function", {}).get("name")
                    for item in request.get("tools", [])
                    if isinstance(item, dict)
                }:
                    self.send_error(422, "Scripted proposal tool was not advertised")
                    return
                delta, finish = _conversation_response(request)
            elif index < len(steps):
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
            text = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(text, str):
                deltas = [dict(delta, content=text[index:index + 24]) for index in range(0, len(text), 24)] or [delta]
            else:
                deltas = [delta]
            chunks = [
                {**envelope, "choices": [{"index": 0, "delta": item, "finish_reason": None}]}
                for item in deltas
            ]
            chunks.append({**envelope, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                           "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                                     "prompt_tokens_details": {"cached_tokens": 40},
                                     "completion_tokens_details": {"reasoning_tokens": 5}}})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
                if chunk_delay:
                    time.sleep(chunk_delay)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            print(json.dumps({"fixture_request": index + 1, "mode": mode, "finish": finish, "chunks": len(chunks)}), flush=True)

    print(f"SCRIPTED FIXTURE ONLY: http://127.0.0.1:{port}/v1 ({mode})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--mode", choices=("read", "approval", "conversation"), default="read")
    parser.add_argument("--delay", type=float, default=0, help="Response delay for cancellation acceptance, in seconds")
    parser.add_argument("--chunk-delay", type=float, default=0, help="Delay between flushed SSE chunks, 0 to 5 seconds")
    args = parser.parse_args()
    if not 0 <= args.delay <= 60:
        parser.error("delay must be between 0 and 60 seconds")
    if not 0 <= args.chunk_delay <= 5:
        parser.error("chunk-delay must be between 0 and 5 seconds")
    serve(args.port, args.mode, args.delay, args.chunk_delay)
