// Fake `codex exec ... -o <file> -`: writes an answer to the -o file.
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  const args = process.argv.slice(2);
  if (process.env.FAKE_CODEX_MODE === "old" && args.includes("--ephemeral")) {
    process.stderr.write("error: unexpected argument '--ephemeral' found\n");
    process.exit(2);
  }
  const out = args[args.indexOf("--output-last-message") + 1];
  require("fs").writeFileSync(out, JSON.stringify({ args, cwd: process.cwd(), input }));
});
