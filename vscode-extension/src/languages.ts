// File types the engine parses (mirror of CODE_EXTENSIONS in
// graphify/detect.py; a unit test keeps the two in sync).
export const CODE_EXTENSIONS: ReadonlySet<string> = new Set([
  ".F", ".F03", ".F08", ".F90", ".F95", ".astro", ".bash", ".c", ".cc", ".cjs", ".cls", ".cpp",
  ".cs", ".cshtml", ".csproj", ".cts", ".cu", ".cuh", ".cxx", ".dart", ".dfm", ".dm", ".dme",
  ".dmf", ".dmi", ".dmm", ".dpk", ".dpr", ".ejs", ".ets", ".ex", ".exs", ".f", ".f03", ".f08",
  ".f90", ".f95", ".fsproj", ".go", ".gradle", ".groovy", ".h", ".hcl", ".hpp", ".inc", ".java",
  ".jl", ".js", ".json", ".jsx", ".kt", ".kts", ".lfm", ".lpk", ".lpr", ".lua", ".luau", ".m",
  ".metal", ".mjs", ".mm", ".mts", ".pas", ".php", ".pp", ".ps1", ".psd1", ".psm1", ".py", ".r",
  ".rake", ".razor", ".rb", ".rs", ".scala", ".sh", ".sln", ".slnx", ".sql", ".sv", ".svelte",
  ".svh", ".swift", ".tf", ".tfvars", ".toc", ".trigger", ".ts", ".tsx", ".v", ".vbproj", ".vue",
  ".xaml", ".zig",
]);

/** Extensions that indicate a real source project, used to decide whether to
 * build automatically (config-only types like .json or .sln don't count). */
const PROJECT_EXTENSIONS = [
  "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "go", "rs", "java", "kt", "kts", "scala", "groovy",
  "c", "cc", "cpp", "cxx", "h", "hpp", "cs", "rb", "php", "swift", "m", "mm", "dart", "lua", "zig",
  "ex", "exs", "jl", "r", "vue", "svelte", "astro", "sh", "ps1", "sql", "f90", "pas", "v", "sv",
];

export const PROJECT_GLOB = `**/*.{${PROJECT_EXTENSIONS.join(",")}}`;

export const EXCLUDE_GLOB =
  "**/{node_modules,.git,.venv,venv,env,__pycache__,dist,build,out,target,graphify-out,.next,vendor}/**";

export function isCodeFile(fsPath: string): boolean {
  const dot = fsPath.lastIndexOf(".");
  return dot >= 0 && CODE_EXTENSIONS.has(fsPath.slice(dot));
}
