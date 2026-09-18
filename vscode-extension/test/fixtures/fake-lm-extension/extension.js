// Test-only extension: registers a fake chat model with VS Code's Language
// Model API, so Synapse's GitHub Copilot path (vscode.lm) can be exercised
// end to end without a Copilot account. It answers reasoning prompts with a
// fixed, graph-checkable JSON answer and records every prompt it receives.
const vscode = require("vscode");

const prompts = [];

function activate(context) {
  const provider = {
    provideLanguageModelChatInformation() {
      return [
        {
          id: "synapse-fake-model",
          name: "Fake Model",
          family: "fake",
          version: "1",
          maxInputTokens: 100000,
          maxOutputTokens: 4000,
          capabilities: {},
        },
      ];
    },
    async provideLanguageModelChatResponse(_model, messages, _options, progress) {
      const text = messages
        .map((m) => m.content.map((p) => (p instanceof vscode.LanguageModelTextPart ? p.value : "")).join(""))
        .join("\n");
      prompts.push(text);
      const answer = text.includes("VALIDATION REPORT")
        ? { answer: "`login()` checks the password using `hash_password()`.", claims: [] }
        : {
            answer: "`login()` looks up the user and calls `hash_password()`.",
            claims: [{ type: "calls", source: "login", target: "hash_password" }],
          };
      progress.report(new vscode.LanguageModelTextPart(JSON.stringify(answer)));
    },
    provideTokenCount(_model, text) {
      return Math.ceil(String(typeof text === "string" ? text : "").length / 4);
    },
  };
  context.subscriptions.push(vscode.lm.registerLanguageModelChatProvider("synapse-test", provider));
  return { prompts };
}

module.exports = { activate };
