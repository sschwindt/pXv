import { writeFileSync } from 'node:fs';
import { readSurvey } from './geopackage.mjs';

const [input, output, ...extra] = process.argv.slice(2);
if (!input || extra.length) {
  console.error('Usage: node web/export-survey.mjs survey.gpkg [survey.json]');
  process.exitCode = 1;
} else {
  try {
    const json = JSON.stringify(readSurvey(input)) + '\n';
    if (output) writeFileSync(output, json, { flag: 'wx' });
    else process.stdout.write(json);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
