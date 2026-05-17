// Generate web/src/shared/types.gen.ts from pydantic schemas.
// Run via: npm run gen:types  (defined in package.json in Task 28)
import { execSync } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { compile } from "json-schema-to-typescript";

const OUT = "src/shared/types.gen.ts";

// Capture the JSON Schema by running the Python exporter
const json = execSync("uv run python -m lafufu_shared.export_schemas", {
  cwd: "..",
  encoding: "utf-8",
});

const schema = JSON.parse(json);

// json-schema-to-typescript needs a top-level type; emit each definition independently
let out = "// AUTOGEN from pydantic — do not edit by hand.\n";
out += "// Regenerate via: npm run gen:types\n\n";

const sorted = Object.entries(schema.definitions).sort(([a], [b]) => a.localeCompare(b));
for (const [name, def] of sorted) {
  // eslint-disable-next-line no-await-in-loop
  const ts = await compile({ ...def, title: name }, name, {
    bannerComment: "",
    additionalProperties: false,
    style: { singleQuote: true, semi: true },
  });
  out += ts.trim() + "\n\n";
}

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, out);
console.log(`wrote ${OUT}: ${Object.keys(schema.definitions).length} types`);
