import { createWorker } from 'tesseract.js';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const [image, cache, language = 'hin'] = process.argv.slice(2);
if (!['hin', 'kan', 'urd', 'ara'].includes(language)) throw new Error('Unsupported OCR language');
let worker;
try {
  await mkdir(cache, { recursive: true });
  worker = await createWorker([language, 'eng'], 1, {
    cachePath: path.resolve(cache),
    errorHandler: e => { console.error(String(e)); process.exit(1); },
  });
  const result = await worker.recognize(image);
  process.stdout.write(JSON.stringify({ text: result.data.text,
    recognition_score: result.data.confidence, engine: `tesseract.js/${language}+eng` }));
} catch (error) {
  console.error(String(error));
  process.exitCode = 1;
} finally {
  if (worker) await worker.terminate();
}
