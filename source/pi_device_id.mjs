/**
 * Pi's OpenAI Sign in with ChatGPT expects a stable installation UUID.
 * Keep it in Pi's global settings.json, not in a second SpecWeave-only file.
 * No account tokens are read or written here.
 */
import {readFile, mkdir, open, rename, chmod, unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function validDeviceId(value) {
  return typeof value === 'string' && UUID_RE.test(value);
}

export async function readDeviceId(home) {
  const settingsPath = join(home, '.pi', 'agent', 'settings.json');
  try {
    const contents = JSON.parse(await readFile(settingsPath, 'utf8'));
    return validDeviceId(contents?.deviceId) ? contents.deviceId : null;
  } catch (error) {
    if (error?.code === 'ENOENT') return null;
    if (error instanceof SyntaxError) throw new Error('Pi global settings.json contains invalid JSON');
    throw error;
  }
}

export async function getOrCreateDeviceId(home) {
  const dir = join(home, '.pi', 'agent');
  const settingsPath = join(dir, 'settings.json');
  let settings = {};
  try {
    settings = JSON.parse(await readFile(settingsPath, 'utf8'));
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      if (error instanceof SyntaxError) throw new Error('Pi global settings.json contains invalid JSON');
      throw error;
    }
  }
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) {
    throw new Error('Pi global settings.json must contain a JSON object');
  }
  if (settings.deviceId !== undefined) {
    if (!validDeviceId(settings.deviceId)) throw new Error('Pi deviceId is invalid; refusing to replace existing identity');
    return settings.deviceId;
  }
  const deviceId = randomUUID();
  await mkdir(dir, {recursive: true, mode: 0o700});
  const temp = join(dir, `.settings-${randomUUID()}.tmp`);
  const fd = await open(temp, 'wx', 0o600);
  try {
    await fd.writeFile(JSON.stringify({...settings, deviceId}, null, 2) + '\n');
    await fd.sync();
  } finally {
    await fd.close();
  }
  try {
    await rename(temp, settingsPath);
    await chmod(settingsPath, 0o600);
  } catch (error) {
    await unlink(temp).catch(() => {});
    throw error;
  }
  return deviceId;
}
