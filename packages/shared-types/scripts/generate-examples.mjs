// Extracts components.examples from the OpenAPI contract into a typed-agnostic module.
// Consumers assert the shape with generated types (`satisfies TodayResponse`), so no
// second model of the API exists. Generated file: do not edit.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';

const spec = parse(readFileSync(fileURLToPath(new URL('../../contracts/openapi.yaml', import.meta.url)), 'utf8'));
const examples = Object.fromEntries(
  Object.entries(spec.components?.examples ?? {}).map(([name, ex]) => [name, ex.value]),
);
const body = `// This file is generated from packages/contracts/openapi.yaml by scripts/generate-examples.mjs.\n// Do not edit by hand.\n\nexport const contractExamples = ${JSON.stringify(examples, null, 2)} as const;\n\nexport type ContractExampleName = keyof typeof contractExamples;\n`;
writeFileSync(fileURLToPath(new URL('../src/examples.generated.ts', import.meta.url)), body);
console.log(`examples: ${Object.keys(examples).join(', ')}`);
