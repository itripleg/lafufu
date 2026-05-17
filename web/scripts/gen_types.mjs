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

/**
 * Recursively remove `title` from all property sub-schemas so that
 * json-schema-to-typescript does not emit `export type Foo = ...` aliases
 * for individual properties — we only want the top-level interface.
 *
 * The `inPropertySchema` flag is true when we are inside the value of a
 * `properties` entry (i.e., a property sub-schema), so we can safely strip
 * the JSON Schema `title` keyword without confusing it with a property *named*
 * "title".
 */
function stripPropertyTitles(obj, inPropertySchema = false) {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return obj;
  const result = {};
  for (const [k, v] of Object.entries(obj)) {
    if (k === "title" && inPropertySchema) {
      // Strip the title metadata from this property sub-schema only.
      continue;
    }
    if (k === "properties" && typeof v === "object" && v !== null) {
      // Process each property's sub-schema with inPropertySchema = true
      result[k] = Object.fromEntries(
        Object.entries(v).map(([prop, propSchema]) => [
          prop,
          stripPropertyTitles(propSchema, true),
        ])
      );
    } else if (typeof v === "object" && v !== null && !Array.isArray(v)) {
      result[k] = stripPropertyTitles(v, inPropertySchema);
    } else if (Array.isArray(v)) {
      result[k] = v.map((item) =>
        typeof item === "object" && item !== null
          ? stripPropertyTitles(item, inPropertySchema)
          : item
      );
    } else {
      result[k] = v;
    }
  }
  return result;
}

let out = "// AUTOGEN from pydantic — do not edit by hand.\n";
out += "// Regenerate via: npm run gen:types\n\n";

// Track emitted names to deduplicate interfaces that appear in multiple $defs
const emittedInterfaces = new Set();

const sorted = Object.entries(schema.definitions).sort(([a], [b]) => a.localeCompare(b));
for (const [name, def] of sorted) {
  // Strip titles from property sub-schemas to suppress inline type alias generation
  const cleanDef = stripPropertyTitles({ ...def, title: name });

  // eslint-disable-next-line no-await-in-loop
  const ts = await compile(cleanDef, name, {
    bannerComment: "",
    additionalProperties: false,
    style: { singleQuote: true, semi: true },
  });

  // Deduplicate: skip entire interface blocks we have already emitted
  const lines = ts.trim().split("\n");
  const outputLines = [];
  let skipDepth = 0;

  for (const line of lines) {
    const trimmed = line.trimEnd();

    if (skipDepth > 0) {
      if (trimmed.includes("{")) skipDepth++;
      if (trimmed.includes("}")) {
        skipDepth--;
        if (skipDepth === 0) continue; // skip the closing brace too
      }
      continue;
    }

    // Detect `export interface Foo {`
    const ifaceMatch = trimmed.match(/^export interface (\w+)\s*\{/);
    if (ifaceMatch) {
      const ifaceName = ifaceMatch[1];
      if (emittedInterfaces.has(ifaceName)) {
        skipDepth = 1;
        continue;
      }
      emittedInterfaces.add(ifaceName);
    }

    outputLines.push(trimmed);
  }

  const block = outputLines.join("\n").trim();
  if (block) out += block + "\n\n";
}

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, out);
console.log(`wrote ${OUT}: ${Object.keys(schema.definitions).length} types`);
