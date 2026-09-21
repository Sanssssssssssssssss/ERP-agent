import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// The native custom-model loader defaults to 16,384. Remove the actual outgoing
// fields after Pi builds its request, rather than replacing one client cap with another.
export default function baselineReceipts(pi) {
  const directory = process.env.PI_BASELINE_REQUEST_DIR || "/logs/agent/requests";
  let number = 0;
  pi.on("before_provider_request", (event) => {
    const payload = event.payload;
    if (!payload || typeof payload !== "object" || !Array.isArray(payload.messages)) {
      throw new Error("Expected a chat-completions payload");
    }
    delete payload.max_tokens;
    delete payload.max_completion_tokens;
    number += 1;
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, `${String(number).padStart(4, "0")}.request.json`), JSON.stringify(payload));
    return payload;
  });
  pi.on("after_provider_response", (event) => {
    const requestIds = Object.fromEntries(Object.entries(event.headers).filter(([name]) =>
      ["x-request-id", "request-id", "x-generation-id"].includes(name.toLowerCase())));
    writeFileSync(join(directory, `${String(number).padStart(4, "0")}.response.json`),
      JSON.stringify({ status: event.status, request_ids: requestIds }));
  });
}
