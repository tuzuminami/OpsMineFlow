import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const base = new URL("../src/", import.meta.url);
const en = JSON.parse(await readFile(new URL("locales/en.json", base), "utf8"));
const ja = JSON.parse(await readFile(new URL("locales/ja.json", base), "utf8"));
const i18nSource = await readFile(new URL("i18n.tsx", base), "utf8");
const appSource = await readFile(new URL("App.tsx", base), "utf8");

test("English and Japanese dictionaries have matching non-empty keys", () => {
  assert.deepEqual(Object.keys(ja).sort(), Object.keys(en).sort());
  for (const [key, value] of Object.entries(ja)) {
    assert.equal(typeof value, "string", key);
    assert.notEqual(value.trim(), "", key);
  }
});

test("every translation key used by the WebUI exists", () => {
  const usedKeys = [...appSource.matchAll(/(?<![A-Za-z0-9_])t\("([^"]+)"/g)].map((match) => match[1]);
  assert.ok(usedKeys.length > 100);
  for (const key of usedKeys) assert.ok(key in en, `Missing translation key: ${key}`);
});

test("language selection defaults from the browser and persists locally", () => {
  assert.match(i18nSource, /startsWith\("ja"\)/);
  assert.match(i18nSource, /localStorage\.getItem\(STORAGE_KEY\)/);
  assert.match(i18nSource, /localStorage\.setItem\(STORAGE_KEY, language\)/);
  assert.match(i18nSource, /document\.documentElement\.lang = language/);
  assert.match(appSource, /setLanguage\("ja"\)/);
  assert.match(appSource, /setLanguage\("en"\)/);
});

test("beginner workflow labels are explicit in both languages", () => {
  assert.equal(ja["action.startCollecting"], "データ収集を始める");
  assert.match(ja["sample.body"], /再読込.*削除されない/);
  assert.match(en["collection.autoBody"], /No keystrokes/);
  assert.equal(ja["recording.start"], "記録を開始");
  assert.equal(ja["recording.stop"], "記録を停止");
  assert.equal(ja["recording.saveTemplate"], "保存");
  assert.match(ja["onboarding.body"], /サンプル確認.*実際の記録.*出力/);
  assert.match(ja["recording.scopeBody"], /前面アプリ名.*ウィンドウタイトル.*取得しない/);
  assert.match(ja["privacyEvidence.title"], /取得しないデータ/);
  assert.match(appSource, /title=\{t\("action\.refreshHelp"\)\}/);
  assert.match(appSource, /t\("confirm\.deleteData"\)/);
  assert.match(appSource, /<EmptyDataView onStart=\{onStart\}/);
  assert.match(appSource, /RECORDING_TEMPLATES_KEY/);
  assert.match(appSource, /PrivacyEvidencePanel/);
});
