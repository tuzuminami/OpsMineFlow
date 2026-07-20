import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const base = new URL("../src/", import.meta.url);
const en = JSON.parse(await readFile(new URL("locales/en.json", base), "utf8"));
const ja = JSON.parse(await readFile(new URL("locales/ja.json", base), "utf8"));
const i18nSource = await readFile(new URL("i18n.tsx", base), "utf8");
const appSource = await readFile(new URL("App.tsx", base), "utf8");
const apiSource = await readFile(new URL("api.ts", base), "utf8");

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
  assert.equal(ja["timeline.title"], "記録タイムライン");
  assert.match(ja["timeline.body"], /除外.*作業名修正.*分割.*結合/);
  assert.equal(ja["csvMapping.title"], "CSV列マッピング");
  assert.match(ja["csvMapping.body"], /列名が違う場合/);
  assert.match(ja["onboarding.body"], /サンプル確認.*実際の記録.*出力/);
  assert.match(ja["recording.scopeBody"], /前面アプリ名.*ウィンドウタイトル.*取得しない/);
  assert.match(ja["privacyEvidence.title"], /取得しないデータ/);
  assert.match(appSource, /title=\{t\("action\.refreshHelp"\)\}/);
  assert.match(appSource, /t\("confirm\.deleteData"\)/);
  assert.match(appSource, /<EmptyDataView onStart=\{onStart\}/);
  assert.match(appSource, /RECORDING_TEMPLATES_KEY/);
  assert.match(appSource, /CSV_MAPPING_KEY/);
  assert.match(appSource, /CsvMappingWizard/);
  assert.match(appSource, /RecordingTimeline/);
  assert.match(appSource, /actions\.splitEvent/);
  assert.match(appSource, /PrivacyEvidencePanel/);
});

test("the packaged WebUI uses the allowlisted Tauri proxy instead of a direct local API session", () => {
  assert.match(apiSource, /invoke<T>\("local_api_operation"/);
  assert.match(apiSource, /invoke<\{ deleted: boolean \}>\("delete_local_data", \{ payload: withProjectScope/);
  assert.match(apiSource, /import\.meta\.env\.DEV/);
  assert.match(apiSource, /isApprovedDevelopmentApiBase/);
  assert.match(apiSource, /url\.hostname === "127\.0\.0\.1"/);
  assert.match(apiSource, /url\.hostname === "localhost"/);
  assert.doesNotMatch(apiSource, /X-OpsMineFlow-Api-Session/);
  assert.doesNotMatch(apiSource, /runtime_secret|session_secret/i);
});

test("large event lists use bounded pages and offer a user-triggered next page", () => {
  assert.match(apiSource, /postJson<EventPage>\("events_page", \{ offset, limit \}, projectScope\)/);
  assert.match(apiSource, /if \(isTauri\(\)\) throw new Error\("Packaged exports must use the native save dialog/);
  assert.match(appSource, /async function loadMoreEvents\(\)/);
  assert.match(appSource, /loadEventPage\(data\.events\.length, 500, currentProjectScope\(\)\)/);
  assert.match(appSource, /t\("events\.loadMore"/);
});

test("recording polling stays lightweight and dashboard refreshes are single-flight", () => {
  assert.match(apiSource, /export async function getRecordingStatus\(projectScope: ProjectScope\)/);
  assert.match(appSource, /const refreshInFlight = useRef<Promise<void> \| null>\(null\)/);
  assert.match(appSource, /Promise\.all\(\[getNativeRuntimeStatus\(\), getRecordingStatus\(projectScope\)\]\)/);
  assert.match(appSource, /const projectsAfterClear = await loadProjects\(\)/);
  assert.match(appSource, /expectedRevision: refreshedProject\.revision/);
  assert.doesNotMatch(appSource, /setInterval\(\(\) => void refresh\(true\), 2000\)/);
});

test("quality repair exposes provenance and the timeline orders explicit-offset instants", () => {
  assert.match(appSource, /item\.case_correlation\.strategy/);
  assert.match(appSource, /item\.case_correlation\.evidence/);
  assert.match(appSource, /case_correlation_review/);
  assert.match(appSource, /function compareTimelineEvents/);
  assert.match(appSource, /Date\.parse\(left\.timestamp_start\)/);
  assert.match(appSource, /sort\(compareTimelineEvents\)/);
  assert.match(apiSource, /AutomationCandidatesResponse/);
});
