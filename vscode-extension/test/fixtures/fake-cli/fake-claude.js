// Fake `claude -p --output-format json`: echoes argv and stdin back in a
// Claude-style JSON envelope. FAKE_CLAUDE_MODE selects failure behaviours.
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  const args = process.argv.slice(2);
  const mode = process.env.FAKE_CLAUDE_MODE || "ok";
  if (mode === "old" && args.includes("--tools")) {
    process.stderr.write("error: unknown option '--tools'\n");
    process.exit(1);
  }
  if (mode === "auth") {
    process.stdout.write(JSON.stringify({ type: "result", is_error: true, result: "Invalid API key · Please run /login" }));
    return;
  }
  const result = JSON.stringify({ args, input });
  process.stdout.write(JSON.stringify({ type: "result", is_error: false, result, usage: { input_tokens: 5, cache_read_input_tokens: 2, output_tokens: 7 } }));
});
