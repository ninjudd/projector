const [endpoint, cwd] = process.argv.slice(2);
const socket = new WebSocket(endpoint);
const pending = new Map();
let nextId = 0;
let completed;
const turn = new Promise((resolve, reject) => { completed = { resolve, reject }; });
const timer = setTimeout(() => completed.reject(new Error("initial turn timed out")), 60000);
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id !== undefined && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  }
  if (message.method === "turn/completed") {
    if (message.params.turn.error) completed.reject(new Error(JSON.stringify(message.params.turn.error)));
    else completed.resolve();
  }
};
const rpc = (method, params) => new Promise((resolve, reject) => {
  const id = ++nextId;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
});
try {
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  await rpc("initialize", { clientInfo: { name: "projector-smoke", version: "1" }, capabilities: { experimentalApi: true } });
  socket.send(JSON.stringify({ jsonrpc: "2.0", method: "initialized" }));
  const { data } = await rpc("hooks/list", { cwds: [cwd] });
  if (data.some(entry => entry.hooks.some(hook => hook.enabled
      && !["trusted", "managed"].includes(hook.trustStatus)))) {
    throw new Error("review the temporary test hooks first with --review-hooks");
  }
  const { thread } = await rpc("thread/start", { cwd, sandbox: "read-only", approvalPolicy: "never" });
  process.stdout.write(JSON.stringify({ thread: thread.id }) + "\n");
  await rpc("turn/start", { threadId: thread.id, input: [{ type: "text", text:
    "Transport smoke test. Use no tools or files. Reply only READY now. Later transport notices request only ACK; no PR actions.",
    text_elements: [] }] });
  await turn;
} finally {
  clearTimeout(timer);
  socket.close();
}
